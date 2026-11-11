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

Every file is replaced whole (written aside, then renamed): a process killed
while writing leaves the previous content, never half of a file.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import evidence, store
from .contract import FULL, FULL_CHEAPER, KINDS, Observation, ToolCall, ToolResult
from .engine import EXPAND, Engine
from .evidence import Adaptive
from .runtime import Policy

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


class Learner:
    def __init__(self, workdir: str | Path, min_runs: int = 5, idle_seconds: float | None = None,
                 adaptive: Adaptive | None = None):
        """`idle_seconds`: the pause after which the integration closes a run
        by itself (the proxy does; a function-calling loop closes its own
        runs, and passes None). Only used to count runs that an answer closed
        and that were followed by more calls within that pause.
        `adaptive`: promotion and sampling (tracetrim.evidence.Adaptive); all off by default."""
        self.dir = Path(workdir)
        (self.dir / "traces").mkdir(parents=True, exist_ok=True)
        store.clean(self.dir)
        store.clean(self.dir / "traces")
        # Whether a tool is trimmed is decided on held-out runs (leave-one-out,
        # as in `tracetrim measure`): with fewer than two there is nothing to
        # hold out, and nothing would ever be trimmed.
        self.min_runs = max(2, min_runs)
        self.adaptive = adaptive or Adaptive()
        self.state_path = self.dir / "state.json"
        self.evidence_path = self.dir / "evidence.json"
        self.state = {"runs": 0, "complete_runs": 0, "trimmed": {}, "expanded": {}, "off": [],
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
        # One lock for everything the calls and the learning thread share:
        # the state, the open run and the evidence file.
        self.lock = threading.RLock()
        self.events: list[dict] = []
        self.notes: list[dict] = []                  # how results were exposed, and expand requests: evidence to be
        self._index_of: dict[str, int] = {}          # call_id -> position of its call in the open run
        self._origins: dict[str, dict] = {}          # tool -> {"server", "tool"}, when the integration knows
        self.idle_seconds = idle_seconds
        self._answered_at: float | None = None       # when an answer last closed a run
        self._count = None

    def count(self, text: str) -> int:
        """Tokens (o200k_base) when tiktoken is installed, characters otherwise:
        only ever compared with counts in the same unit."""
        if self._count is None:
            from .measure import counter
            self._count, self.unit = counter()
        return self._count(text)

    # -- observation ------------------------------------------------------
    def call(self, tool: str, arguments: dict, output: str, turn: int | None = None) -> int:
        """Record a call of the open run; return its position in the run."""
        with self.lock:
            if not self.events and self._answered_at is not None:
                # The first call after an answer. Within the idle pause it may be
                # the same task going on (the agent closed too early) or the next
                # one: the two cannot be told apart, so they are counted, not merged.
                if self.idle_seconds is not None and time.time() - self._answered_at <= self.idle_seconds:
                    self.state["answered_runs_followed_by_calls"] += 1
                self._answered_at = None
            e = {"kind": "call", "tool": tool, "arguments": arguments, "output": output}
            if turn is not None:
                e["turn"] = turn             # calls of one turn do not use each other's outputs
            self.events.append(e)
            return len(self.events) - 1

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

    def observed(self, index: int, call: ToolCall, obs: Observation) -> None:
        """How the result of call `index` reached the model."""
        with self.lock:
            if call.server or call.upstream_tool:
                self._origins[call.tool] = {"server": call.server, "tool": call.upstream_tool}
            if obs.exposure == FULL:
                return
            if obs.exposure == FULL_CHEAPER:
                self.state["full_cheaper"][call.tool] = self.state["full_cheaper"].get(call.tool, 0) + 1
            if obs.call_id:
                self.trimmed(call.tool)
                self._index_of[obs.call_id] = index
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
            if first:
                # One per result, however many times it is asked: what the
                # switch-off of a whole tool counts.
                self.expanded(tool)
            self.notes.append({"kind": "expand", "index": self._index_of.get(call_id), "after": len(self.events),
                               "tool": tool, "call_id": call_id, "requested": requested, "returned": returned,
                               "recovered": recovered, "targeted": targeted, "broad": broad, "repeat": bool(recovered) and not first})

    def refused(self) -> None:
        """A closing that carried no answer and was refused: the run stays
        open. If the agent does not try again, only the idle pause or the end
        of the session closes it, and a task that starts before then lands in
        the same run."""
        with self.lock:
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
        with self.lock:
            if not self.events:
                return None
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
            self.events, self.notes, self._index_of = [], [], {}
            self.state["runs"] = number
            self._absorb(run, events, notes, answer if complete else None)
            if complete:
                self.state["complete_runs"] += 1
                self._answered_at = time.time()
            self._save()
            return path

    def _absorb(self, run: str, events: list[dict], notes: list[dict], answer: str | None) -> None:
        """Turn the notes of a run into evidence, kept in evidence.json; the
        notes themselves are kept next to the trace, in evidence/."""
        if not notes:
            return
        found, broad = evidence.derive(events, notes, answer)
        data = evidence.load(self.evidence_path)
        evidence.merge(data, run, found, broad, self._origins)
        evidence.save(self.evidence_path, data)
        for n in notes:
            if n["kind"] == "expand":
                runs = self.state["expand_runs"].setdefault(n["tool"], [])
                if run not in runs:
                    runs.append(run)
        (self.dir / "evidence").mkdir(exist_ok=True)
        log = self.dir / "evidence" / f"{run}.jsonl"
        earlier = log.read_text(encoding="utf-8") if log.exists() else ""
        store.write_atomic(log, earlier + "".join(json.dumps(n, ensure_ascii=False) + "\n" for n in notes))

    def flush(self) -> Path | None:
        """On shutdown: keep the calls of an unfinished run, for telemetry."""
        path = self.done(None)
        if path is not None:
            self.relearn()
            return path
        with self.lock:
            # Nothing to close, but `expand` requests made since the last closing
            # are only in memory (until 0.3.5 they were lost here). They are about
            # results of the last run: that is where their evidence goes.
            if self.notes:
                notes, self.notes = [dict(n, index=None) for n in self.notes], []
                self._absorb(f"run-{self.state['runs']:04d}", [], notes, None)
            self._save()
        return None

    # -- learning ---------------------------------------------------------
    def relearn(self) -> Policy | None:
        """Regenerate the report; return a new policy when one should be applied."""
        from . import arguments, lists, measure
        from . import report as rp
        from .projection import Projection
        from .traces import load
        from .usage import analyse

        paths = sorted((self.dir / "traces").glob("run-*.jsonl"))
        if not paths:
            return None
        complete = [p for p in paths if "noanswer" not in p.name]
        # Telemetry from every run; learning only from runs with an answer.
        learn_from = complete or paths
        traces = [load(p) for p in learn_from]
        usages = [analyse(t) for t in traces]
        d = rp.compute([t.name for t in traces], usages)
        sugg = rp.suggestions(d)
        config, projection = rp.build_config(d, sugg, usages, "report.projection.json")
        d["lists"] = lists.summarise([lists.element_usage(t) for t in traces])
        d["arguments"] = arguments.cardinality(traces)
        ready = len(complete) >= self.min_runs

        with self.lock:
            a = self.adaptive
            data = evidence.load(self.evidence_path)
            # The targeted correction comes first: fields are promoted before a
            # whole tool is judged, and a tool that just got one starts counting again.
            last = f"run-{self.state['runs']:04d}"
            new = evidence.decide(data, a, last) if a.promote and ready else []
            # A field that came back by itself (recovered with expand, written, and now kept by
            # ordinary learning) corrects the cut as a promotion does: the requests that asked
            # for it no longer count against the tool.
            resumed = evidence.resume(data, projection.keep, last) if ready else []
            if new or resumed:
                evidence.save(self.evidence_path, data)
                for tool in {p["tool"] for p in new + resumed}:
                    for counter in ("trimmed", "expanded", "expand_runs"):
                        self.state[counter].pop(tool, None)
            self._switch_off_overexpanded()
            off = list(self.state["off"])
            counters = {k: json.loads(json.dumps(self.state[k]))
                        for k in ("trimmed", "expanded", "expand_runs", "sampling", "full_cheaper")}
            closings = (self.state["refused_closings"], self.state["answered_runs_followed_by_calls"])

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
                          "promoted": {t: sorted(f) for t, f in sorted(active.items())},
                          "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}

        projection.save(self.dir / "report.projection.json")
        store.write_json(self.dir / "report.config.json", config)
        d["suggestions"], d["config"], d["config_file"] = sugg, config, "report.config.json"
        d["auto"] = config["auto"]
        store.write_atomic(self.dir / "report.html", rp.render_html(d, sugg))
        store.write_json(self.dir / "report.json", d)
        with self.lock:
            if ready:
                self.state["config_from_runs"] = len(complete)
            self._save()
        return Policy(config, projection) if ready else None

    def _adaptive_report(self, d, data, before, after, usages, counters, count) -> dict:
        """What the report says about evidence, promotions and sampling."""
        effect = {}
        for tool in sorted(t for t in after.keep if after.keep[t] != before.keep[t]):
            outs = [c.output for calls in usages for c in calls if c.tool == tool and c.structured]
            effect[tool] = {"calls": len(outs), "raw": sum(count(o) for o in outs),
                            "without_promotions": sum(count(before.apply(tool, o, keep_empty=True)) for o in outs),
                            "with_promotions": sum(count(after.apply(tool, o, keep_empty=True)) for o in outs)}
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

    def _switch_off_overexpanded(self) -> None:
        """The last resort: a tool whose filtered results are expanded too often
        is no longer filtered. The counts start again whenever a cut of the
        tool is corrected, by a promotion or by ordinary learning keeping a
        recovered field: only requests the correction did not settle count. With promotion on, the requests must also come
        from as many runs as a promotion by request needs, so that the targeted
        correction gets its chance first."""
        runs_needed = self.adaptive.promote_requested_runs if self.adaptive.promote else 0
        for tool, n in self.state["trimmed"].items():
            e = self.state["expanded"].get(tool, 0)
            if (n >= EXPAND_MIN_CALLS and e / n >= EXPAND_MAX_RATE and tool not in self.state["off"]
                    and len(self.state["expand_runs"].get(tool, [])) >= runs_needed):
                self.state["off"].append(tool)

    def initial_policy(self) -> Policy | None:
        """The config learned in a previous session, if any."""
        p = self.dir / "report.config.json"
        if p.exists():
            config = json.loads(p.read_text())
            if config.get("auto", {}).get("applied") and config.get("tools"):
                return Policy.load(p)
        return None

    def _save(self) -> None:
        with self.lock:
            store.write_json(self.state_path, self.state)


class AutoTrim:
    """Automatic mode for agents that run their own tools (function calling, no MCP).

        tt = AutoTrim("tracetrim-data/", min_runs=5)
        tools = my_tools + [tt.expand_tool]        # adapt the schema to your provider
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
    """

    def __init__(self, workdir: str | Path, min_runs: int = 5, structural: str | None = None,
                 strategy: bool = False, adaptive: Adaptive | None = None):
        self.engine = Engine(Learner(workdir, min_runs, adaptive=adaptive), structural=structural)
        self.strategy = strategy
        self.expand_tool = {"name": EXPAND["name"], "description": EXPAND["description"],
                            "parameters": EXPAND["inputSchema"]}

    @property
    def learner(self) -> Learner:
        return self.engine.learner

    @property
    def trimmer(self):
        return self.engine.trimmer

    @property
    def structural(self) -> str | None:
        return self.engine.structural

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
        return text(strategy=self.strategy, done=False)

    def output(self, tool: str, arguments: dict | str | None, raw: str, turn: int | None = None) -> str:
        """Record the call; return what the model should receive. `turn`: the
        model turn that emitted the call, if you run several calls per turn
        (pass it for every call, or for none)."""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": arguments}
        return self.engine.observe(ToolCall(tool, arguments or {}, turn), ToolResult(raw)).text

    def expand(self, arguments: dict | str | None) -> str:
        """Answer an `expand` call: the hidden fields the model asked for."""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                return "Error: expand needs JSON arguments with call_id and fields."
        arguments = arguments if isinstance(arguments, dict) else {}
        return self.engine.expand(str(arguments.get("call_id", "")), [str(f) for f in arguments.get("fields") or []])

    def done(self, answer: str | None) -> None:
        """Close the run with the final answer: report updated, policy reloaded
        when ready. Calling it again before any new tool call does nothing."""
        self.engine.close(answer)
