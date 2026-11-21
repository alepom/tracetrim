"""Automatic mode: the proxy observes, learns and applies, on its own.

  tracetrim proxy --upstream-url ... --auto workdir/ [--min-runs 5]

How a run is observed. The proxy sees every tool call but not the agent's
answer, and in the study behind this library learning without the answer
missed a used field in 49% of held-out calls (the three agents on real APIs),
against 4% with it. So the
proxy offers one more tool,
`tracetrim_done`, and the agent is told (one line in its instructions) to
pass its final answer to it. A run is closed by that call. A run that never
calls it is kept for telemetry only.

What is applied without asking. After every run the report is regenerated
(telemetry from the first run). Once `min_runs` complete runs exist, the
config is rebuilt and reloaded: only the report's automatic actions, which
in 0.2 are the prudent projection with identifying fields kept, transformed
values recognised, and `expand` on. Everything else stays in the report as a
manual action.

How it corrects itself. Every `expand` call is a sign that a cut was wrong.
What was asked for and what was given back is kept as evidence, per field
(tracetrim.evidence); with promotion on, a field with enough evidence is kept
from then on. A tool whose trimmed outputs are still expanded too often stops
being trimmed, and the report says so. With sampling on, now and then a result
goes to the model whole, to see whether it uses what is normally removed.

Everything lives in the work directory, so learning survives restarts:

  workdir/traces/run-0001.jsonl   one file per run (calls, then the answer)
  workdir/report.html (+ .json, .config.json, .projection.json)
  workdir/state.json              runs, expand counts, tools switched off
  workdir/evidence.json           evidence per field, promotions and their reasons
  workdir/evidence/run-0001.jsonl how results were exposed and what expand was asked, per run
  workdir/savings.json            per run and tool: tokens sent by the tools, re-written whole, received
  workdir/tools.json              the tool list as published (proxy), or as passed to AutoTrim.offered

Every file is replaced whole (written aside, then renamed): a process killed
while writing leaves the previous content, never half of a file.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import definitions, evidence, grounding, store, turns
from .contract import FULL, FULL_CHEAPER, KINDS, Observation, ToolCall, ToolResult
from .engine import EXPAND, Engine, expand_tool, requested_fields  # noqa: F401  (EXPAND: imported from here before 1.4)
from .evidence import Adaptive
from .runtime import Policy
from .traces import load as load_trace

DONE = {
    "name": "tracetrim_done",
    "description": ("REQUIRED last step: when your final answer is ready, call this once with the complete "
                    "answer, BEFORE you show it; then show the same answer. It lets tracetrim learn which data "
                    "you used, so that next time tool results are lighter. It does not change your answer."),
    "inputSchema": {"type": "object", "required": ["answer"],
                    "properties": {"answer": {"type": "string", "description":
                                              "Your final answer, verbatim: the answer only, not the conversation."}}},
}
DONE_REPLY = "Recorded."     # constant, and terminal: it never asks the model for anything else
# A tool is switched off when, over at least EXPAND_MIN_CALLS trimmed calls,
# the agent asked to expand at least EXPAND_MAX_RATE of them.
EXPAND_MIN_CALLS = 4
EXPAND_MAX_RATE = 0.5


def runs_needed(adaptive: Adaptive) -> int:
    """The distinct runs the expanded results of a tool must come from, before it is switched off."""
    return max(adaptive.switch_off_runs if adaptive.switch_off_runs > 1 else 0,
               adaptive.promote_requested_runs if adaptive.promote else 0)


def over_expanded(filtered: int, expanded: int, runs: int, needed: int = 0) -> bool:
    """The rule of the switch-off: enough filtered results, enough of them expanded, in enough runs."""
    return filtered >= EXPAND_MIN_CALLS and expanded / filtered >= EXPAND_MAX_RATE and runs >= needed


def answered(name: str, trace) -> bool:
    """A run closed with an answer: named so, and its trace ends with the answer. A trace cut short before it
    (written in place up to 0.3.5) is not, whatever its name: the state and the report count runs by this one rule
    (REVISIONE-14b-quinquies: the state counted an empty trace as complete)."""
    return "noanswer" not in name and bool(trace.events) and trace.events[-1].kind == "message"


@dataclass
class Run:
    """Mutable telemetry for one open run. Closed runs can receive late expansions."""
    events: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    index_of: dict[str, int] = field(default_factory=dict)
    sessions: set[str] = field(default_factory=set)
    sizes: dict[str, list[int]] = field(default_factory=dict)
    expand_sizes: list[int] = field(default_factory=lambda: [0, 0])
    last_number: int = 0
    answered_at: float | None = None
    # Closed with an answer, and the reply not given yet (the proxy, 1.4): the pause after the answer starts at the
    # reply, so no call counts as following it until then.
    reply_pending: bool = False


class Learner:
    def __init__(self, workdir: str | Path, min_runs: int = 5, idle_seconds: float | None = None,
                 adaptive: Adaptive | None = None, protect_ids: str = "all", learning: str = "1.1",
                 compact: bool = True, keep_positions: bool = True, durable_closes: bool = True,
                 relearn_at_start: bool = True):
        """`idle_seconds`: the pause after which the integration closes a run
        by itself (the proxy does; a function-calling loop closes its own
        runs, and passes None). Only used to count runs that an answer closed
        and that were followed by more calls within that pause.
        `adaptive`: promotion and sampling (tracetrim.evidence.Adaptive); all off by default.
        `protect_ids`: which identifying fields learning keeps whatever the usage, "all" (the default) or
        "record" (tracetrim.projection.PROTECT_IDS; since 1.1).
        `learning`: the rules of usage detection and of the report's measure, "1.1" (the default) or "1.0"
        (tracetrim.usage.LEARNING). `compact`, `keep_positions`: whether run time writes compact JSON and keeps the
        places of list elements left with no field; the measure follows them, and the config records them.
        `durable_closes`: a closing is written through a journal, all its files or none (1.4: in every mode; until
        1.3 only with the proxy's isolated sessions); False restores the writes of 1.3, file after file.
        `relearn_at_start`: a run left out of the report by a stopped process is relearned in the constructor; False
        leaves it to the caller (`needs_relearn`): the proxy with `--background-relearn` does it in a thread."""
        from .projection import protect_mode
        from .usage import learning_flags
        self.learning = learning
        self._flags = learning_flags(learning)
        self.compact = compact
        self.keep_positions = keep_positions
        self.protect_ids = protect_mode(protect_ids)
        if self.protect_ids == "none":
            raise ValueError('protect_ids: "all" or "record" in automatic mode')
        self.dir = Path(workdir)
        (self.dir / "traces").mkdir(parents=True, exist_ok=True)
        # Held for the life of the Learner: `tracetrim promotions` in another process then knows the directory is in
        # use, and does not relearn it from there (store.OPEN_LOCK).
        give_back = store.hold_open(self.dir)
        if give_back is not None:
            weakref.finalize(self, give_back)
        self.durable_closes = durable_closes
        self.pending_close = False
        # Relearnings owed: every run recorded, and every publication finished after an interruption, calls for one,
        # and `relearn_owed` stays true until a relearning that read them has ended without error. An integration
        # asks before confirming a closing, so that a retry after a failure, or after a publication that another
        # request finished, is never confirmed with the policy of before (REVISIONE-14b-quinquies).
        self._owed = 0
        self._paid = 0
        # The integration says when a tool call arrives (`arrived`; the proxy, since 1.4); otherwise a call counts
        # as arriving when it is recorded.
        self.arrivals_reported = False
        # Under the write lock: `tracetrim promotions` writes its temporary file under it, and the cleaning must not
        # remove it (REVISIONE-14b-quater).
        with store.write_lock(self.dir):
            recovered = store.recover_close(self.dir)
            store.clean(self.dir)
            store.clean(self.dir / "traces")
        # Whether a tool is trimmed is decided on held-out runs (leave-one-out,
        # as in `tracetrim measure`): with fewer than two there is nothing to
        # hold out, and nothing would ever be trimmed.
        self.min_runs = max(2, min_runs)
        self.adaptive = adaptive or Adaptive()
        # The report counts tokens: without a tokenizer automatic mode would stop at its first
        # relearning, in the middle of the agent's work. Say so now, at start-up.
        from . import measure
        measure.tokenizer()
        self.state_path = self.dir / "state.json"
        self.evidence_path = self.dir / "evidence.json"
        # The format names the file, as in every other file of the work directory (since 0.7);
        # a state written before has no key and loads the same.
        self.state = {"format": "tracetrim/state@1", "runs": 0, "complete_runs": 0, "trimmed": {}, "expanded": {}, "off": [],
                      "config_from_runs": 0,
                      # Where the boundary of a run may be wrong (see `refused` and `call`).
                      "refused_closings": 0, "answered_runs_followed_by_calls": 0,
                      # Since 0.4 work: `trimmed` and `expanded` count filtered results and those
                      # among them that were expanded (each once), since the tool's last promotion;
                      # `expand_runs`, the runs those requests came from; `sampling`, per tool.
                      "expand_runs": {}, "sampling": {},
                      # Results passed whole because the filtered one would not have been shorter.
                      "full_cheaper": {}}
        if self.state_path.exists():
            try:
                self.state.update(json.loads(self.state_path.read_text()))
            except json.JSONDecodeError:
                # Written in place by a version before atomic writes, and cut short.
                # The traces are intact: counters restart, learning does not.
                self.state_path.replace(self.state_path.with_suffix(".json.corrupt"))
        # Traces numbered after the runs the state knows: a closing written file after file (`durable_closes=False`,
        # or 1.3) stopped between its trace and its state. The state takes them in, once, and the report must cover
        # them; their counts and notes were only in memory.
        behind = self._realign()
        # One lock for everything the calls and the learning thread share:
        # the state, the open run and the evidence file.
        self.lock = threading.RLock()
        # One relearning at a time, whichever thread asks (1.4: `proxy --background-relearn` relearns in a thread
        # while the event loop can close runs); held for the whole relearning, never by a tool call.
        self._relearning = threading.Lock()
        # What the last relearning published, read from one consistent snapshot: for the proxy's log.
        self.published: dict | None = None
        # Counts the writes a relearning reads (traces, state, savings, evidence, tool list): a relearning reads them
        # outside the lock and keeps what it read only if no write came in between (`_inputs`).
        self._generation = 0
        self._run = ContextVar("tracetrim_run", default=Run(last_number=self.state["runs"]))
        # The filtered results counted in `trimmed` since the tool's counters last started again:
        # only an expansion of one of them counts against the tool (an older result can still be
        # expanded, but its filtering was wiped with the counters).
        self._counted: dict[str, set[str]] = {}
        # The relearnings that corrected a cut (a promotion, a resumed field), counted, and the last one per tool: a
        # result filtered by a policy learned before it belongs to the counts that correction started again, and does
        # not count against the tool (`observed`; Policy.corrections).
        self._corrections = 0
        self._restarted_at: dict[str, int] = {}
        # Counts a failed write of savings.json could not save, with the run they belong to: saved with the next ones.
        self._unsaved: list[tuple[str, dict, list[int]]] = []
        self._origins: dict[str, dict] = {}          # tool -> {"server", "tool"}, when the integration knows
        self.idle_seconds = idle_seconds
        self._count = None
        # The open run, counted (contract.Sizes): tool -> [calls, raw, whole, sent]; expand: [calls, tokens].
        self.savings_path = self.dir / "savings.json"
        self.closed_sessions = 0                     # confirmed session count at the last closing
        self.unsupported: list[str] = []             # the last run closed: values of its answer no output contains

        # A run on disk that no report covers yet: marked pending in the same write that records the run, replayed
        # from the journal of an interrupted closing, or taken in by `_realign`; or a change of the evidence that
        # `tracetrim promotions` left to the proxy (store.CHANGED).
        self.needs_relearn = bool(recovered or behind or self.state.get("report_pending")
                                  or (self.dir / store.CHANGED).exists())
        self._owed = int(self.needs_relearn)
        if self.needs_relearn and relearn_at_start:
            self.relearn()

    def _realign(self) -> bool:
        """Take into the state the traces numbered after its runs (see the constructor). True when there were."""
        later = {}
        for p in (self.dir / "traces").glob("run-*.jsonl"):
            m = re.match(r"run-(\d+)", p.name)
            if m and int(m[1]) > self.state["runs"]:
                # Its number is taken whatever it holds; it counts as complete only if it can be read (a trace cut
                # short is set aside by the relearning: REVISIONE-14b-quater).
                try:
                    later[int(m[1])] = answered(p.name, load_trace(p))
                except (ValueError, OSError):
                    later[int(m[1])] = False
        if not later:
            return False
        self.state["runs"] = max(later)
        self.state["complete_runs"] += sum(later.values())
        self.state["report_pending"] = True
        store.write_json(self.state_path, self.state)
        return True

    @property
    def run(self) -> Run:
        return self._run.get()

    @contextmanager
    def use_run(self, run: Run):
        token = self._run.set(run)
        try:
            yield
        finally:
            self._run.reset(token)

    @property
    def events(self):
        return self.run.events

    @property
    def notes(self):
        return self.run.notes

    def count(self, text: str) -> int:
        """Tokens (o200k_base) when tiktoken is installed, characters otherwise:
        only ever compared with counts in the same unit."""
        if self._count is None:
            from .measure import counter
            self._count, self.unit = counter()
        return self._count(text)

    # -- observation ------------------------------------------------------
    def call(self, tool: str, arguments: dict, output: str, turn: int | None = None,
             *, session_key: str | None = None) -> int:
        """Record a call of the open run; return its position in the run."""
        with self.lock:
            if not self.arrivals_reported:
                self.arrived(time.time())
            e = {"kind": "call", "tool": tool, "arguments": arguments, "output": output}
            if turn is not None:
                e["turn"] = turn             # calls of one turn do not use each other's outputs
            self.events.append(e)
            if session_key is not None:
                self.run.sessions.add(session_key)
            return len(self.events) - 1

    def arrived(self, at: float, run: Run | None = None) -> None:
        """A tool call of `run` (the open run) arrived at `at`. The first call after an answer: within the idle
        pause it may be the same task going on (the agent closed too early) or the next one; the two cannot be
        told apart, so they are counted, not merged.

        The proxy (since 1.4) says so on arrival, before any wait: the pause is the agent's, from the reply to
        `tracetrim_done` to the first call that arrived after it, whichever is recorded first (REVISIONE-14b-
        quinquies). A call that arrived before the reply (another client's, already on its way) follows no answer,
        and leaves the answer to the next one."""
        with self.lock:
            run = self.run if run is None else run
            if run.answered_at is None or run.reply_pending or at < run.answered_at:
                return
            if self.idle_seconds is not None and at - run.answered_at <= self.idle_seconds:
                self.state["answered_runs_followed_by_calls"] += 1
            run.answered_at = None

    def measured(self, tool: str, sizes) -> None:
        """The three counts of a call of the open run (contract.Sizes)."""
        with self.lock:
            t = self.run.sizes.setdefault(tool, [0, 0, 0, 0])
            for i, v in enumerate((1, sizes.raw, sizes.whole, sizes.sent)):
                t[i] += v

    def expand_measured(self, size: int) -> None:
        """What a reply of `expand` weighs: kept apart from the results' counts.
        A reply that arrives after a closing, before the next call, is about the
        run just closed: its tokens are added to that run at once. (Its evidence
        note is filed with the next closing, with no index: evidence counts runs
        and fields, not tokens.)"""
        with self.lock:
            self.run.expand_sizes[0] += 1
            self.run.expand_sizes[1] += size
            if not self.events and self.run.last_number:
                self.finish_pending()            # an interrupted publication first: its replay would undo this write
                self._save_sizes(f"run-{self.run.last_number:04d}")

    def _save_sizes(self, run: str) -> None:
        """Add the counts gathered since the last save to savings.json, under `run`,
        and start again. Counts a failed save could not write go with the next save, under their own run
        (REVISIONE-14b-quater: they went to the next run)."""
        current = (run, self.run.sizes, self.run.expand_sizes)
        self.run.sizes, self.run.expand_sizes = {}, [0, 0]
        pending = [*self._unsaved] + ([current] if current[1] or current[2][0] else [])
        # Counts with no run to belong to (an expand before any call) are dropped, as before.
        pending = [p for p in pending if p[0] != "run-0000"]
        if not pending:
            self._unsaved = []
            return
        try:
            data = {"format": "tracetrim/savings@1", "unit": self._unit(), "runs": {}}
            if self.savings_path.exists():
                try:
                    old = json.loads(self.savings_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    old = None
                # A measure: a file of another shape starts again rather than stopping a closing.
                if (isinstance(old, dict) and old.get("format") == data["format"] and isinstance(old.get("runs"), dict)
                        and all(isinstance(v, dict) for v in old["runs"].values())):
                    data.update(old)
            try:
                for name, sizes, expand in pending:
                    self._add_sizes(data, name, sizes, expand)
            except (KeyError, TypeError, ValueError, AttributeError):
                data["runs"] = {}                # an entry of another shape: start again
                for name, sizes, expand in pending:
                    self._add_sizes(data, name, sizes, expand)
            store.write_json(self.savings_path, data)
        except BaseException:
            self._unsaved = pending
            raise
        self._unsaved = []
        self._generation += 1

    def _add_sizes(self, data: dict, run: str, sizes: dict, expand: list[int]) -> None:
        entry = data["runs"].setdefault(run, {"tools": {}, "expand": {"calls": 0, "tokens": 0}})
        for tool, (n, raw, whole, sent) in sorted(sizes.items()):
            t = entry["tools"].setdefault(tool, {"calls": 0, "raw": 0, "whole": 0, "sent": 0})
            for k, v in (("calls", n), ("raw", raw), ("whole", whole), ("sent", sent)):
                t[k] += v
        entry["expand"]["calls"] += expand[0]
        entry["expand"]["tokens"] += expand[1]

    def record_tools(self, tools: list[dict]) -> bool:
        """The tool list as published to the model, for the report's section on
        definitions. Changes nothing the model receives. True when it changed."""
        with self.lock:
            self.finish_pending()
            changed = definitions.record(self.dir, tools)
            if changed:
                self._generation += 1
            return changed

    def trimmed(self, tool: str) -> None:
        with self.lock:
            self.state["trimmed"][tool] = self.state["trimmed"].get(tool, 0) + 1

    def expanded(self, tool: str) -> None:
        with self.lock:
            self.state["expanded"][tool] = self.state["expanded"].get(tool, 0) + 1

    def sample(self, tool: str, saved: int) -> bool:
        """Should this call, which filtering would shorten by `saved`, go to
        the model whole? Decided by (seed, tool, call number): the same
        sequence in every process, and it goes on where it was after a restart."""
        a = self.adaptive
        with self.lock:
            s = self.state["sampling"].setdefault(tool, {"eligible": 0, "sampled": 0, "removed": 0, "added": 0})
            s["eligible"] += 1
            digest = hashlib.sha256(f"{a.sample_seed}:{tool}:{s['eligible']}".encode()).hexdigest()
            drawn = int(digest[:8], 16) / 2 ** 32 < a.sample_rate
            # The cost limit: what sampling has added, this call included, stays
            # within a share of what filtering has removed.
            if drawn and s["added"] + saved <= a.sample_max_share * s["removed"]:
                s["sampled"] += 1
                s["added"] += saved
                return True
            s["removed"] += saved
            return False

    def observed(self, index: int, call: ToolCall, obs: Observation, policy: Policy | None = None) -> None:
        """How the result of call `index` reached the model; `policy`: the one it was filtered with."""
        with self.lock:
            if call.server or call.upstream_tool:
                self._origins[call.tool] = {"server": call.server, "tool": call.upstream_tool}
            if obs.exposure == FULL:
                return
            if obs.exposure == FULL_CHEAPER:
                self.state["full_cheaper"][call.tool] = self.state["full_cheaper"].get(call.tool, 0) + 1
            if obs.call_id:
                # Filtered by a policy learned before the tool's last correction (between the relearning that made it
                # and the installation of its policy): the counts of the tool started again at that correction, and
                # this result belongs to before it, whatever fields it hides (REVISIONE-14b-quinquies).
                if getattr(policy, "corrections", 0) >= self._restarted_at.get(call.tool, 0):
                    self.trimmed(call.tool)
                    self._counted.setdefault(call.tool, set()).add(obs.call_id)
                self.run.index_of[obs.call_id] = index
            self.notes.append({"kind": "observation", "index": index, "tool": call.tool, "exposure": obs.exposure,
                               "call_id": obs.call_id, "hidden": list(obs.hidden)})

    def expand(self, call_id: str, tool: str, *, requested: list[str], returned: list[str], recovered: list[str],
               targeted: list[str], broad: bool, first: bool) -> None:
        """An `expand` request on a filtered result. `requested`: the names as
        the model wrote them; `returned`: every path given back; `recovered`:
        those of them the projection had removed; `targeted`: those of them
        asked for by name or group; `broad`: the request asked for everything;
        `first`: the first request on this result that gave back a removed field."""
        with self.lock:
            self.finish_pending()                # an interrupted publication first: its replay would undo this write
            # Against the tool: once per result, however many times it is asked, only when a
            # removed field came back, and only for results counted since its counters started again.
            counts = first and call_id in self._counted.get(tool, set())
            if counts:
                self.expanded(tool)
            note = {"kind": "expand", "index": self.run.index_of.get(call_id), "after": len(self.events),
                    "tool": tool, "call_id": call_id, "requested": requested, "returned": returned,
                    "recovered": recovered, "targeted": targeted, "broad": broad,
                    "repeat": bool(recovered) and not first, "counts": counts}
            if not self.events and self.run.last_number:
                # No run is open: the request is about a result of the run just closed (the agent closed too
                # early). Its evidence goes there now, as `flush` does; until 1.0 it went to the next run, and
                # one task could count as two runs for the switch-off and the promotions.
                self._absorb(f"run-{self.run.last_number:04d}", [], [dict(note, index=None)], None)
                self._save()
            else:
                self.notes.append(note)

    def refused(self) -> None:
        """A closing that carried no answer and was refused: the run stays
        open. If the agent does not try again, only the idle pause or the end
        of the session closes it, and a task that starts before then lands in
        the same run."""
        with self.lock:
            self.finish_pending()
            self.state["refused_closings"] += 1
            self._save()

    def _next_run(self) -> int:
        # Also from the files: if the process died after writing a trace and
        # before saving the state, the next run must not overwrite it.
        on_disk = [int(m[1]) for p in (self.dir / "traces").glob("run-*.jsonl")
                   if (m := re.match(r"run-(\d+)", p.name))]
        return max([self.state["runs"], *on_disk]) + 1

    def done(self, answer: str | None) -> Path | None:
        """Close the run. Returns the trace written, or None if there was nothing
        to write: closing twice, or retrying a closing, changes nothing."""
        # An interrupted publication is finished and relearned first, without holding the lock: a relearning takes
        # its own lock and then this one (REVISIONE-14b-quater found the opposite order here).
        if self.finish_pending():
            self.relearn()
        with self.lock:
            if not self.events:
                return None
            if self.durable_closes:
                return self._durable_done(answer)
            return self._write_run(answer)

    def finish_pending(self) -> bool:
        """Finish any interrupted publication before the next workdir write. True when there was one: a relearning
        is then owed (`relearn_owed`), whoever finished it."""
        return self._publish_pending(owe=True)

    def _publish_pending(self, owe: bool) -> bool:
        """Publish the journal of this process. `owe=False`: by the writer that just wrote it, right after (its run,
        or its relearning, is accounted for already)."""
        if not self.pending_close:           # read without the lock: the proxy asks on its event loop at every call
            return False
        with self.lock, store.write_lock(self.dir):
            if not self.pending_close:
                return False
            store.recover_close(self.dir)
            self._generation += 1
            if owe:
                self._owed += 1
            self.pending_close = False
            return True

    @property
    def relearn_owed(self) -> bool:
        """A run recorded, or a publication finished, that no relearning ended without error has read yet: the
        report and the policy are behind. True also after a relearning that failed."""
        with self.lock:
            return self._owed != self._paid

    def _durable_done(self, answer: str | None) -> Path:
        # Under the write lock from the evidence read to the publication: `tracetrim promotions` in another process
        # never edits evidence.json between them (store.WRITE_LOCK).
        with store.write_lock(self.dir):
            original = self.run
            closed = copy.copy(original)
            closed.sessions = original.sessions.copy()
            # _write_run replaces the event, note, index and size containers. Its
            # aggregate state is updated in place, so prepare that on a private copy.
            previous = self.state
            self.state = copy.deepcopy(previous)
            self.state["report_pending"] = True
            # The savings not saved yet go with the journal too: a closing that does not reach it leaves them as they
            # were, whatever its preparation took or left (REVISIONE-14b-quinquies: run 2 lost, or counted twice).
            unsaved, owed = self._unsaved, self._owed
            try:
                with self.use_run(closed), store.collect_close() as files:
                    path, failed = self._stage_run(answer)
                store.write_json(self.dir / store.JOURNAL,
                                 {p.relative_to(self.dir).as_posix(): text for p, text in files.items()})
            except BaseException:
                self.state, self._unsaved, self._owed = previous, unsaved, owed
                raise
            # The journal is the commit point. Handles to the original Run must
            # now see the cleared, numbered run, including if publication fails.
            vars(original).update(vars(closed))
            self.pending_close = True
            self._publish_pending(owe=False)     # its run is owed already (`_stage_run`)
        self._check_grounding(path, bool(answer and answer.strip()))
        if failed is not None:
            # A secondary write failed once the trace was staged (the evidence, the savings): the run is on disk with
            # what did not fail, as in a closing written file after file since 1.0, and the failure is raised.
            raise failed
        return path

    def _check_grounding(self, path, complete):
        try:
            self.unsupported = grounding.unsupported(load_trace(path))[1] if complete else []
        except Exception:                # noqa: BLE001
            self.unsupported = []

    def _write_run(self, answer: str | None) -> Path:
        path, failed = self._stage_run(answer)
        if failed is not None:
            raise failed
        return path

    def _stage_run(self, answer: str | None) -> tuple[Path, Exception | None]:
        """Write the run (`_write_run`), or collect its files for the journal (`_durable_done`). Returns the trace and
        the failure of a secondary write, if one failed after the trace."""
        events, notes = self.events, self.notes
        number = self._next_run()
        complete = bool(answer and answer.strip())
        run = f"run-{number:04d}"
        path = self.dir / "traces" / f"{run}{'' if complete else '-noanswer'}.jsonl"
        lines = [json.dumps(e, ensure_ascii=False) for e in events]
        if complete:
            msg = {"kind": "message", "text": answer}
            turns = [e["turn"] for e in events if "turn" in e]
            if turns and len(turns) == len(events):
                msg["turn"] = max(turns) + 1
            lines.append(json.dumps(msg, ensure_ascii=False))
        store.write_atomic(path, "\n".join(lines) + "\n")
        # Only now is the run closed: had the write failed, its calls would
        # still be pending, and the closing could be tried again.
        self.run.events, self.run.notes, self.run.index_of = [], [], {}
        sessions = len(self.run.sessions)
        self.closed_sessions = sessions
        self.run.sessions.clear()
        if sessions > 1:
            totals = self.state.setdefault("mixed_sessions", {"runs": 0, "session_occurrences": 0})
            totals["runs"] += 1
            totals["session_occurrences"] += sessions
        self.run.last_number = number
        self.state["runs"] = number
        self._owed += 1
        # From here the run is on disk: its counts and the state are kept whatever fails next (until 1.0 a
        # failure in the evidence left complete_runs behind for good and moved its sizes to the next run).
        failed = None
        try:
            self._absorb(run, events, notes, answer if complete else None)
        except Exception as e:           # noqa: BLE001
            failed = e
        try:
            self._save_sizes(run)
        except Exception as e:           # noqa: BLE001
            failed = failed or e
        # What the proxy's log says at this closing (since 1.1): values of the answer no output contains.
        # A measure: it never stops a closing.
        if not self.durable_closes:
            self._check_grounding(path, complete)
        if complete:
            self.state["complete_runs"] += 1
            self.run.answered_at = time.time()
        # In the same write that records the run: no report covers it yet. The relearning that does clears it; a
        # process stopped before then relearns at its next start (1.4: in every mode, not only with isolated sessions).
        self.state["report_pending"] = True
        self._save()
        return path, failed

    def _absorb(self, run: str, events: list[dict], notes: list[dict], answer: str | None) -> None:
        """Turn the notes of a run into evidence, kept in evidence.json; the
        notes themselves are kept next to the trace, in evidence/."""
        if not notes:
            return
        found, broad = evidence.derive(events, notes, answer)
        with store.write_lock(self.dir):         # read, merged and written with no other process in between
            data = evidence.load(self.evidence_path)
            evidence.merge(data, run, found, broad, self._origins)
            evidence.save(self.evidence_path, data)
        self._generation += 1
        for n in notes:
            # A run counts for the switch-off only if an expand in it really gave a removed field
            # back (not a failed request, not a repeat) on a result that counts against the tool.
            # Notes written before this rule (no "counts" key) count as they did.
            if n["kind"] == "expand" and n.get("counts", bool(n.get("recovered")) and not n.get("repeat")):
                runs = self.state["expand_runs"].setdefault(n["tool"], [])
                if run not in runs:
                    runs.append(run)
        (self.dir / "evidence").mkdir(exist_ok=True)
        log = self.dir / "evidence" / f"{run}.jsonl"
        earlier = log.read_text(encoding="utf-8") if log.exists() else ""
        store.write_atomic(log, earlier + "".join(json.dumps(n, ensure_ascii=False) + "\n" for n in notes))

    def flush(self, relearn: bool = True) -> Path | None:
        """On shutdown: keep the calls of an unfinished run, for telemetry. `relearn=False`: the caller relearns
        (the proxy with `--background-relearn`, through its coordinator); the run is pending on disk until then."""
        path = self.done(None)
        if path is None:
            with self.lock:
                self.finish_pending()
                # Nothing to close, but `expand` requests made since the last closing
                # are only in memory (until 0.3.5 they were lost here). They are about
                # results of the last run: that is where their evidence goes.
                if self.notes:
                    notes, self.run.notes = [dict(n, index=None) for n in self.notes], []
                    self._absorb(f"run-{self.run.last_number:04d}", [], notes, None)
                self._save_sizes(f"run-{self.run.last_number:04d}")
                self._save()
        if relearn and (path is not None or self.relearn_owed):
            self.relearn()
        return path

    # -- learning ---------------------------------------------------------
    def relearn(self) -> Policy | None:
        """Regenerate the report; return a new policy when one should be applied."""
        with self._relearning:
            self.finish_pending()                # a closing whose publication was interrupted goes in first
            return self._relearn()

    # The state a relearning reads, copied with the runs on disk.
    _READ = ("runs", "trimmed", "expanded", "expand_runs", "sampling", "full_cheaper", "off", "refused_closings",
             "answered_runs_followed_by_calls", "mixed_sessions")

    def _inputs(self) -> dict:
        """What one relearning reads, all from one generation of the work directory: the runs on disk, the state,
        the savings, the evidence and the published tool list, with no write of this process between them.

        Read as a seqlock reader reads: the lock is taken only to note the generation (and copy the state, in
        memory) and then to check that it has not changed. If a write of this process came in between, everything is
        read again after a short pause, growing with the attempts: never under the lock, so a call or a closing on
        the event loop never waits for these reads. With `--background-relearn` no request of the proxy writes while
        a relearning runs (BackgroundLearning), so the first attempt is the only one; without it, another client's
        write can make it read again, for as long as writes keep coming in every attempt. (Writes of another process
        are checked at the commit, in both modes.)"""
        pause = 0.0
        while True:
            with self.lock:
                # An interrupted publication first: the state in memory already counts its run, whose trace is still
                # in the journal (the report would be marked as covering a run it did not read).
                self.finish_pending()
                generation, owed = self._generation, self._owed
                state = copy.deepcopy({k: self.state[k] for k in self._READ if k in self.state})
            # Before the evidence: a change `tracetrim promotions` marks after this read is not covered (`_apply`).
            marker = store.stamp(self.dir / store.CHANGED)
            paths = sorted((self.dir / "traces").glob("run-*.jsonl"))
            try:
                savings = json.loads(self.savings_path.read_text(encoding="utf-8")) \
                    if self.savings_path.exists() else None
            except (OSError, ValueError):
                savings = None                       # a measure: the report goes without the section
            data = evidence.load(self.evidence_path)
            tools = definitions.load(self.dir)
            with self.lock:
                if self._generation == generation:
                    return {"state": state, "paths": paths, "savings": savings, "evidence": data, "tools": tools,
                            "marker": marker, "owed": owed}
            pause = min(0.05, pause * 2 or 0.001)
            time.sleep(pause)

    def _relearn(self) -> Policy | None:
        from . import arguments, lists, measure
        from . import report as rp
        from .projection import FoldTraining, Projection
        from .traces import load
        from .usage import analyse

        inputs = self._inputs()
        state, paths = inputs["state"], inputs["paths"]
        runs_at_start = state["runs"]
        # A trace that cannot be read (written in place by 0.3.5 and cut short by a killed process) is set aside,
        # as state.json is: until 1.0 it stopped every later relearning.
        readable = {}
        for p in paths:
            try:
                readable[p] = load(p)
            except (ValueError, OSError):
                try:
                    p.replace(p.with_name(p.name + ".corrupt"))
                except OSError:
                    pass
        paths = [p for p in paths if p in readable]
        if not paths:
            self._commit(inputs, None)
            return None
        complete = [p for p in paths if answered(p.name, readable[p])]
        # Telemetry from every run; learning only from runs with an answer.
        learn_from = complete or paths
        traces = [readable[p] for p in learn_from]
        usages = [analyse(t, **self._flags) for t in traces]
        training = FoldTraining(usages, protect_ids=self.protect_ids)
        # The default keeps the report's value as before 1.1 (true); "record" is written as such.
        d = rp.compute([t.name for t in traces], usages,
                       protect_ids=True if self.protect_ids == "all" else self.protect_ids,
                       runtime=self.learning != "1.0", compact=self.compact, keep_positions=self.keep_positions,
                       training=training)
        d["turns"] = turns.summarise(traces)
        # Values of the answers that no tool output of their run contains (since 1.1): runs with an answer only,
        # which are the traces just loaded when there are any. A measure: the report goes without it if it fails.
        try:
            d["grounding"] = grounding.summarise(traces if complete else [], self.dir)
        except Exception:                    # noqa: BLE001
            pass
        published = inputs["tools"]
        if published is not None:
            # Tools called in complete runs only: a run with no answer says nothing of what a task needs.
            called = [{c.tool for c in t.calls} for t in traces] if complete else []
            d["definitions"] = definitions.summarise(published, called, self.count, self.min_runs)
            d["definitions"]["unit"] = self._unit()
        sugg = rp.suggestions(d)
        config, projection = rp.build_config(d, sugg, usages, "report.projection.json", training=training)
        # What the engine serves, so that the config read back elsewhere (`proxy --config`, `tracetrim apply`)
        # serves the same; keep_positions only when off, so that a config at the defaults is the file of 1.1.3.
        config["compact"] = self.compact
        if not self.keep_positions:
            config["keep_positions"] = False
        d["lists"] = lists.summarise([lists.element_usage(t, **self._flags) for t in traces])
        d["arguments"] = arguments.cardinality(traces)
        ready = len(complete) >= self.min_runs

        if inputs["savings"] is not None:
            try:
                d["savings"] = rp.savings(inputs["savings"])
            except Exception:                # noqa: BLE001 - a measure: the report goes without the section
                pass
        a = self.adaptive
        data = inputs["evidence"]
        # The targeted correction comes first: fields are promoted before a
        # whole tool is judged, and a tool that just got one starts counting again.
        last = f"run-{runs_at_start:04d}"
        new = evidence.decide(data, a, last) if a.promote and ready else []
        # A field that came back by itself (recovered with expand, written, and now kept by
        # ordinary learning) corrects the cut as a promotion does: the requests that asked
        # for it no longer count against the tool.
        # A field the operator reverted stays removed: its requests are not settled.
        reverted_now = evidence.promoted(data, "reverted")
        kept = {t: k - reverted_now.get(t, set()) for t, k in projection.keep.items()}
        resumed = evidence.resume(data, kept, last) if ready else []
        restarted = sorted({p["tool"] for p in new + resumed})
        for tool in restarted:
            for counter in ("trimmed", "expanded", "expand_runs"):
                state[counter].pop(tool, None)
        self._switch_off_overexpanded(state)
        off = list(state["off"])
        counters = {k: state[k] for k in ("trimmed", "expanded", "expand_runs", "sampling", "full_cheaper")}
        if "mixed_sessions" in state:
            d["mixed_sessions"] = state["mixed_sessions"]
        closings = (state["refused_closings"], state["answered_runs_followed_by_calls"])

        # Promotions and reverts change what the learned projection keeps.
        active, reverted = evidence.promoted(data), evidence.promoted(data, "reverted")
        before = Projection(projection.mode, projection.source, {t: set(k) for t, k in projection.keep.items()},
                            projection.seen, projection.unseen)
        # A reverted field that ordinary learning would keep (its value was seen
        # written): the operator's choice stands, and the report says so.
        conflicts = [{"tool": t, "field": f} for t in sorted(projection.keep)
                     for f in sorted(reverted.get(t, set()) & projection.keep[t])]
        for tool in projection.keep:
            changed = active.get(tool, set()) | reverted.get(tool, set())
            projection.keep[tool] = (projection.keep[tool] | active.get(tool, set())) - reverted.get(tool, set())
            projection.seen[tool] = projection.seen.get(tool, set()) | changed
        d["adaptive"] = self._adaptive_report(d, data, before, projection, usages, counters, measure.tokenizer())
        d["adaptive"]["conflicts"] = conflicts

        for tool in off:
            config["tools"].pop(tool, None)
        if not ready:
            config["tools"] = {}            # telemetry only until enough complete runs
        config["auto"] = {"complete_runs": len(complete), "min_runs": self.min_runs,
                          "applied": ready, "switched_off": off,
                          "refused_closings": closings[0], "answered_runs_followed_by_calls": closings[1],
                          # The pause that counter is about (None: the integration closes its own runs).
                          "idle_seconds": self.idle_seconds,
                          "promoted": {t: sorted(f) for t, f in sorted(active.items())},
                          # Only when not the default, so that a config of 1.0 and one of 1.1 at the
                          # defaults are the same file; `tracetrim promotions` reads it back.
                          **({"protect_ids": self.protect_ids} if self.protect_ids != "all" else {}),
                          **({"learning": self.learning} if self.learning != "1.1" else {}),
                          "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}

        projection.save(self.dir / "report.projection.json")
        store.write_json(self.dir / "report.config.json", config)
        d["suggestions"], d["config"], d["config_file"] = sugg, config, "report.config.json"
        d["auto"] = config["auto"]
        store.write_atomic(self.dir / "report.html", rp.render_html(d, sugg))
        store.write_json(self.dir / "report.json", d)
        self._commit(inputs, {"new": new, "resumed": resumed, "restarted": restarted, "off": off,
                              "complete_runs": len(complete), "ready": ready, "tools": sorted(config["tools"])})
        if not ready:
            return None
        policy = Policy(config, projection)
        policy.corrections = self._corrections   # after this relearning's own correction, if it made one (`_apply`)
        return policy

    def _commit(self, inputs: dict, decided: dict | None) -> None:
        """After the products of a relearning are written: its decisions go into the evidence and the state, and the
        report stops being pending if no run was closed since its inputs were read (otherwise it stays pending, on
        disk, until a relearning covers that run too, after a restart if need be).

        The decisions are added, not written over: the promotions and resumed fields this relearning found (by
        tool and field, as `evidence.decide` and `evidence.resume` do), on the evidence as it is now, so that what
        was written meanwhile is kept: a closing or a late `expand` of this process (the generation), or
        `tracetrim promotions` in another one (the file's stamp, checked under the write lock that every process
        takes to write it). The evidence is read outside both locks, as in `_inputs`, and read again if it changed;
        under the locks only the check and the writes (`_apply`).

        A limit, in both modes: the coordinator orders the proxy's own requests, but `tracetrim promotions` in another
        process can write the evidence while a relearning computes or commits. The commit then reads it again, for as
        long as such writes keep coming between its read and its check; each is a short write, and the pause between
        attempts grows to 50 ms (REVISIONE-14b-quinquies: four commands in a row, the fifth attempt went through)."""
        if decided is None or not (decided["new"] or decided["resumed"]):
            with self.lock:
                self._apply(inputs, decided, None)
            return
        pause = 0.0
        while True:
            with self.lock:
                generation = self._generation
            seen = store.stamp(self.evidence_path)
            data = evidence.load(self.evidence_path)
            with self.lock, store.write_lock(self.dir):
                # An interrupted publication is finished before the check, not after it: its replay changes the
                # evidence, which is then read again (REVISIONE-14b-quinquies: the copy read before the replay was
                # written over it, and a request of the journal was lost).
                self.finish_pending()
                if self._generation == generation and store.stamp(self.evidence_path) == seen:
                    self._apply(inputs, decided, data)
                    return
            pause = min(0.05, pause * 2 or 0.001)
            time.sleep(pause)

    def _apply(self, inputs: dict, decided: dict | None, data: dict | None) -> None:
        """`_commit`, under the lock. The evidence and the state go through a journal, both or neither: a process
        stopped between them found the promotion on its next start without the restart of the tool's counts, and
        switched the tool off (REVISIONE-14b-quater)."""
        runs_at_start, marker = inputs["state"]["runs"], inputs["marker"]
        with store.write_lock(self.dir):
            self.finish_pending()                # an interrupted closing first: its replay would undo these writes
            if decided is None:                  # no readable run: nothing published, nothing left to cover
                if self.state["runs"] == runs_at_start and self.state.pop("report_pending", None) is not None:
                    self._save()
                self._covered(marker)
                self._paid = max(self._paid, inputs["owed"])
                return
            previous = copy.deepcopy(self.state)
            try:
                with store.collect_close() as files:
                    if data is not None:
                        for key, entries in (("promotions", decided["new"]), ("resumed", decided["resumed"])):
                            known = {(e["tool"], e["field"]) for e in data[key]}
                            data[key] += [e for e in entries if (e["tool"], e["field"]) not in known]
                        evidence.save(self.evidence_path, data)
                    # A promotion or a resumed field corrects the cut of its tool, and the tool's counts start
                    # again. They start again for every result filtered with the old cut, that is every result
                    # filtered before the new policy is installed: those filtered after this relearning read its
                    # inputs too, and those filtered from now until the installation do not count (`observed`).
                    for tool in decided["restarted"]:
                        for counter in ("trimmed", "expanded", "expand_runs"):
                            self.state[counter].pop(tool, None)
                    for tool in decided["off"]:
                        if tool not in self.state["off"]:
                            self.state["off"].append(tool)
                    if decided["ready"]:
                        self.state["config_from_runs"] = decided["complete_runs"]
                    if self.state["runs"] == runs_at_start:
                        self.state.pop("report_pending", None)
                    self._save()
                store.write_json(self.dir / store.JOURNAL,
                                 {p.relative_to(self.dir).as_posix(): text for p, text in files.items()})
            except BaseException:
                self.state = previous
                raise
            if decided["restarted"]:
                self._corrections += 1
                for tool in decided["restarted"]:
                    self._counted.pop(tool, None)
                    self._restarted_at[tool] = self._corrections
            self.pending_close = True
            # The journal is the commit point: published now, or by the next write (which then owes a relearning,
            # since the policy of this one is not installed if its publication fails here).
            self._publish_pending(owe=False)
            self._covered(marker)
            self._paid = max(self._paid, inputs["owed"])
            self.published = {"runs": runs_at_start, "complete_runs": decided["complete_runs"],
                              "applied": decided["ready"], "tools": decided["tools"], "off": list(decided["off"])}

    def _covered(self, marker) -> None:
        """The change `tracetrim promotions` marked is in this relearning: the mark goes, unless it was written again
        since the inputs were read (under the write lock, which the command holds to write it)."""
        if marker is not None and store.stamp(self.dir / store.CHANGED) == marker:
            (self.dir / store.CHANGED).unlink(missing_ok=True)

    def _adaptive_report(self, d, data, before, after, usages, counters, count) -> dict:
        """What the report says about evidence, promotions and sampling."""
        effect = {}
        for tool in sorted(t for t in after.keep if after.keep[t] != before.keep[t]):
            outs = [c.output for calls in usages for c in calls if c.tool == tool and c.structured]
            effect[tool] = {"calls": len(outs), "raw": sum(count(o) for o in outs),
                            "without_promotions": sum(count(before.apply(tool, o, keep_empty=True,
                                                                         keep_positions=self.keep_positions))
                                                      for o in outs),
                            "with_promotions": sum(count(after.apply(tool, o, keep_empty=True,
                                                                     keep_positions=self.keep_positions))
                                                   for o in outs)}
        promotions = []
        for p in data["promotions"]:
            f = d["tools"].get(p["tool"], {}).get("fields", {}).get(p["field"])
            promotions.append(dict(p, field_tokens=f["tokens"] if f else None, field_calls=f["seen"] if f else None))
            if f and p["status"] == "active":
                f["status"] = "promoted"
        return {
            "settings": asdict(self.adaptive),
            "promotions": promotions,
            # Fields an expand gave back and ordinary learning then kept by itself.
            "resumed": data["resumed"],
            "effect": effect,
            "evidence": {tool: {"origin": t.get("origin"), "broad_requests": len(t.get("broad_requests", [])),
                                "fields": {path: {k: len(runs.get(k, [])) for k in KINDS}
                                           for path, runs in sorted(t["fields"].items())}}
                         for tool, t in sorted(data["tools"].items())},
            # Filtered results, and how many of them were expanded (each once), since the last promotion.
            "expand": {tool: {"filtered": n, "expanded": counters["expanded"].get(tool, 0),
                              "runs_with_expand": len(counters["expand_runs"].get(tool, []))}
                       for tool, n in sorted(counters["trimmed"].items())},
            "sampling": counters["sampling"],
            # Results passed whole because filtering them would not have saved anything.
            "full_cheaper": counters["full_cheaper"],
            # The unit of the run-time comparisons (sampling cost, the simple way for small outputs).
            "sampling_unit": self._unit(),
        }

    def _unit(self) -> str:
        self.count("")
        return self.unit

    def _switch_off_overexpanded(self, state: dict) -> None:
        """The last resort: a tool whose filtered results are expanded too often
        is no longer filtered. The counts start again whenever a cut of the
        tool is corrected, by a promotion or by ordinary learning keeping a
        recovered field: only requests the correction did not settle count. With promotion on, the requests must also come
        from as many runs as a promotion by request needs, so that the targeted
        correction gets its chance first. `Adaptive(switch_off_runs=N)` asks the
        same of every tool, promotion or not: results expanded in N distinct runs."""
        needed = runs_needed(self.adaptive)
        for tool, n in state["trimmed"].items():
            if tool not in state["off"] and over_expanded(
                    n, state["expanded"].get(tool, 0), len(state["expand_runs"].get(tool, [])), needed):
                state["off"].append(tool)

    def initial_policy(self) -> Policy | None:
        """The config learned in a previous session, if any."""
        p = self.dir / "report.config.json"
        if p.exists():
            try:
                config = json.loads(p.read_text())
            except ValueError:               # cut short (written in place up to 0.3.5): learnt again at the next closing
                return None
            if config.get("auto", {}).get("applied") and config.get("tools"):
                try:
                    return Policy.load(p)
                except (ValueError, OSError, KeyError):  # its projection unreadable: learnt again at the next closing
                    return None
        return None

    def _save(self) -> None:
        with self.lock:
            store.write_json(self.state_path, self.state)
            self._generation += 1


class AutoTrim:
    """Automatic mode for agents that run their own tools (function calling, no MCP).

        tt = AutoTrim("tracetrim-data/", min_runs=5)
        tools = my_tools + [tt.expand_tool]        # adapt the schema to your provider
        tt.offered(tools)                          # optional: the report weighs the tool definitions
        system_prompt += tt.instructions           # how to use expand (and, with strategy=True, tools in general)
        ...
        # in the loop, for every tool call the model makes:
        if name == "expand":
            result = tt.expand(arguments)
        else:
            result = tt.output(name, arguments, run_my_tool(name, arguments))
        ...
        # when the run is over:
        tt.done(final_answer)

    The same rules as the proxy's automatic mode (the same engine,
    tracetrim.engine), without `tracetrim_done`: the code that runs the loop
    already has the final answer.

    `strategy=True` adds to `instructions` the optional notes on how to
    organise tool calls (see tracetrim.skill). Off by default.

    `adaptive=Adaptive(promote=True, sample_rate=0.1)` turns on the promotion
    of fields on evidence and the sampling of unfiltered results
    (tracetrim.evidence). Both off by default.

    `protect_ids="record"` keeps, whatever the usage, ids and links only when they belong to the
    record itself, and names and titles at any depth; the default, "all", keeps every field named
    like an identifier, as up to 1.0 (tracetrim.projection.PROTECT_IDS). Since 1.1.

    `learning="1.0"` restores the learning rules of 1.0 (tracetrim.usage.LEARNING): negative numbers never
    found, a long text cancelled by one piece written before the call, the report measuring without compact
    JSON. Since 1.1.

    `skill_version="1"` gives `instructions` and `expand_tool` the text of 1.3, without the check before
    declaring information absent that version 2 adds (since 1.4).

    `breakdown=False` stops counting, call by call, what re-serialising saves
    and what removing fields saves (the report's section on where the saving
    comes from). The count changes nothing the model receives.
    """

    def __init__(self, workdir: str | Path, min_runs: int = 5, structural: str | None = None,
                 strategy: bool = False, adaptive: Adaptive | None = None, compact: bool = True,
                 breakdown: bool = True, protect_ids: str = "all", learning: str = "1.1",
                 keep_positions: bool = True, skill_version: str | None = None):
        self.engine = Engine(Learner(workdir, min_runs, adaptive=adaptive, protect_ids=protect_ids, learning=learning,
                                     compact=compact, keep_positions=keep_positions), structural=structural,
                             compact=compact, breakdown=breakdown, keep_positions=keep_positions)
        self.strategy = strategy
        from . import skill
        self.skill_version = skill_version or skill.VERSION
        tool = expand_tool(self.skill_version)
        self.expand_tool = {"name": tool["name"], "description": tool["description"],
                            "parameters": tool["inputSchema"]}

    @property
    def learner(self) -> Learner:
        return self.engine.learner

    @property
    def trimmer(self):
        return self.engine.trimmer

    @property
    def structural(self) -> str | None:
        return self.engine.structural

    @structural.setter
    def structural(self, value: str | None) -> None:
        # Settable, as it was up to 0.3.5: it applies from the policy in use on.
        self.engine.structural = value
        if self.engine.policy is not None:
            self.engine.policy.structural = value

    @property
    def trimming(self) -> bool:
        return self.engine.trimming

    @property
    def report(self) -> Path:
        return self.learner.dir / "report.html"

    @property
    def instructions(self) -> str:
        """Text for the system prompt: the tracetrim skill without the part on
        `tracetrim_done`, which a function-calling loop does not need."""
        from .skill import text
        return text(strategy=self.strategy, done=False, version=self.skill_version)

    def offered(self, tools: list[dict]) -> None:
        """Optional: the tools as you send them to the model, `expand` included, in
        your provider's format. The report then has a section on what their
        definitions weigh, and names the tools never called. Changes nothing the
        model receives; call it again when the list changes."""
        self.learner.record_tools([json.loads(json.dumps(t, default=str)) for t in tools])

    def output(self, tool: str, arguments: dict | str | None, raw: str, turn: int | None = None) -> str:
        """Record the call; return what the model should receive. `turn`: the
        model turn that emitted the call, if you run several calls per turn
        (pass it for every call, or for none)."""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": arguments}
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8", errors="replace")   # traces and counts are text
        return self.engine.observe(ToolCall(tool, arguments or {}, turn), ToolResult(raw)).text

    def expand(self, arguments: dict | str | None) -> str:
        """Answer an `expand` call: the hidden fields the model asked for."""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                return "Error: expand needs JSON arguments with call_id and fields."
        arguments = arguments if isinstance(arguments, dict) else {}
        return self.engine.expand(str(arguments.get("call_id", "")), requested_fields(arguments.get("fields")))

    def done(self, answer: str | None) -> None:
        """Close the run with the final answer: report updated, policy reloaded
        when ready. Calling it again before any new tool call does nothing."""
        self.engine.close(answer)
