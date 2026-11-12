#!/usr/bin/env python3
"""The runs of protocol-v04.md: E1 (the skill's strategy) and E2 (adaptive learning).

  python -m experiments.run --v04 schedule           # every run, in order, and which are done
  python -m experiments.run --v04 next               # the next run not done yet (PAID)
  python -m experiments.run --v04 run E1-ST-3 C1     # one run by id and case (PAID)
  python -m experiments.run --v04 dry-run DIR        # every arm with a stub in place of the model: no spend

Same loop as the original study (experiments/run.py: OpenAI Responses API,
function calling, stateful), with tracetrim in it through its function-calling
adapter. What is new here:

  arms        E1: R, S, T, ST (strategy text x trimming, fixed configuration)
              E2: base, promotion, sampling, promotion+sampling (automatic mode,
              in sequences that share a work directory)
  stop rules  the model spend (cap and warning of the protocol, on this
              checkout's ledger) and the DataForSEO checkpoint: after the first
              runs that call DataForSEO, those runs stop until the author has
              read the spend and recorded the decision
  traces      written under this checkout, never under the original study's.
              A call event holds the tool's ORIGINAL output (`output`) and, when
              it differs, what the model received (`sent`), with how it was
              exposed: gates and usage read the original, costs the other.
              It also says how the call ended (`outcome`), and next to every
              trace are the schemas of the tools the model was offered
              (`<run>.tools.json`). `calls()` reads a trace back: see
              docs/traces-v04.md for what these runs are kept for.

The dry run replays the calls of a recorded Phase A run through a stub that
stands for the model. For the DataForSEO cases the stub also stands for the
provider (recorded outputs, no network); the domain gate is the real one.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from tracetrim import skill
from tracetrim.contract import FULL, ToolCall, ToolResult
from tracetrim.engine import MARK, Engine
from tracetrim.evidence import Adaptive
from tracetrim.measure import tokenizer
from tracetrim.usage import leaves, parse_output

from .budget import V04
from .paths import PRICES, PROMPTS, ROOT, STUDY_TRACES, cost
from .tools import BLOCKED, CASE_TOOLS, DataForSEO, domain_gate, to_args, toolset
from .v04_preconditions import NEEDED, phase_a

CAP, WARN = V04["cap_usd"], V04["warn_usd"]
DATAFORSEO = ("C1", "C2")                      # cases whose tools are billed by the provider
GUARD_CALLS, GUARD_TURNS = 80, 40              # a safety guard, as in the original study: a run that hits it is flagged
E1_ARMS = {"R": (False, False), "S": (True, False), "T": (False, True), "ST": (True, True)}      # strategy, trimming
E2 = V04["e2"]
E2_ARMS = {"base": {}, "promotion": {"promote": True},
           "sampling": {"sample_rate": E2["sample_rate"], "sample_max_share": E2["sample_max_share"]},
           "promotion+sampling": {"promote": True, "sample_rate": E2["sample_rate"],
                                  "sample_max_share": E2["sample_max_share"]}}
MIN_RUNS = 5
# E2: the fields the variant of each case needs, per tool (the simulated case runs on the copy of its world).
NEEDS = {"C1": NEEDED["C1"], "C4": NEEDED["C4v"]}


@dataclass(frozen=True)
class Where:
    """Where a campaign writes. The real one is this checkout; a dry run gets a directory of its own."""
    root: Path

    @property
    def traces(self) -> Path:
        return self.root / "traces"

    @property
    def ledger(self) -> Path:
        return self.root / "out" / "ledger.jsonl"

    @property
    def checkpoint(self) -> Path:
        return self.root / "out" / "v04" / "dataforseo_checkpoint.json"

    @property
    def e1_reviewed(self) -> Path:          # written by hand when the report on E1 has been reviewed
        return self.root / "out" / "v04" / "e1_reviewed.json"

    @property
    def e2_c1_approved(self) -> Path:       # written by hand when E2 on the real case is decided, after E2 on C4
        return self.root / "out" / "v04" / "e2_c1_approved.json"

    @property
    def warning_reviewed(self) -> Path:     # written by hand when the report at the warning has been reviewed
        return self.root / "out" / "v04" / "warning_reviewed.json"

    def workdir(self, run: "Run") -> Path:
        return self.root / "out" / "v04" / "e2" / run.case / f"{run.arm}-s{run.sequence}"


HERE = Where(ROOT)


@dataclass(frozen=True)
class Run:
    experiment: str            # "E1" | "E2"
    case: str
    arm: str
    n: int                     # E1, E3: the run of the arm; E2: the run within its sequence
    sequence: int = 0          # E2 only
    pilot: bool = False        # E3 only: a pilot run, used in no result

    @property
    def id(self) -> str:
        if self.experiment == "E2":
            return f"E2-{self.arm}-s{self.sequence}-r{self.n}"
        return f"{self.experiment}-{'pilot-' if self.pilot else ''}{self.arm}-{self.n}"

    @property
    def dataforseo(self) -> bool:
        # E3 answers the DataForSEO cases from recorded outputs: it never calls the provider.
        return self.case in DATAFORSEO and self.experiment != "E3"


def e2_sequences(where: "Where | None" = None) -> dict[str, int]:
    """Sequences per arm in E2. On the real case, C1, one instead of the two of the
    frozen design (deviation 4: its DataForSEO cost), unless the decision recorded
    after E2 on C4 says otherwise (`sequences` in e2_c1_approved.json)."""
    n = dict(E2["sequences_per_arm"]) | {"C1": 1}
    where = where or HERE
    if where.e2_c1_approved.exists():
        n["C1"] = int(json.loads(where.e2_c1_approved.read_text()).get("sequences", 1))
    return n


def e2_arms(case: str, where: "Where | None" = None) -> list[str]:
    """The arms of E2 that are run on a case. All four, except on the real case, C1,
    where the decision recorded after E2 on C4 can name fewer (`arms` in
    e2_c1_approved.json: deviation 5)."""
    where = where or HERE
    if case == "C1" and where.e2_c1_approved.exists():
        named = json.loads(where.e2_c1_approved.read_text()).get("arms")
        if named:
            unknown = [a for a in named if a not in E2_ARMS]
            if unknown:
                raise SystemExit(f"{where.e2_c1_approved}: unknown arms {unknown}")
            return [a for a in E2["arms"] if a in named]
    return list(E2["arms"])


def schedule(where: "Where | None" = None) -> list[Run]:
    """Every run of the protocol, in the order it is made.

    E1 in rounds: one run of every arm of C1, then of C2, then of C4, and
    again. E2 after it, the simulated case first: within a case, the first
    sequence of every arm, run by run across the arms, then the second. The
    arms advance together, so that if the budget stops the runs early it takes
    about as many from each arm."""
    e1 = V04["e1"]
    runs = [Run("E1", c, arm, n) for n in range(1, e1["runs_per_arm"] + 1) for c in e1["cases"] for arm in e1["arms"][c]]
    sequences = e2_sequences(where)
    for c in ("C4", "C1"):                  # the simulated case first: E2 on C1 is decided on its outcome
        runs += [Run("E2", c, arm, n, s) for s in range(1, sequences[c] + 1)
                 for n in range(1, E2["runs_per_sequence"] + 1) for arm in e2_arms(c, where)]
    return runs


# -- what a trace records about a call ---------------------------------------------

# How a call ended. Told apart so that errors can be studied later without running anything again.
OK = "ok"                          # the tool answered
TOOL_ERROR = "tool_error"          # the tool answered and marked the result as an error (MCP `isError`)
GATE_REFUSED = "gate_refused"      # the study's domain gate refused the call: it never left this machine
CLIENT_ERROR = "client_error"      # no answer after the retries: the model was told the tool did not respond
EXPAND_ERROR = "expand_error"      # `expand` could not give anything back (unknown call_id, no field matches)


def schema_records(schemas: list[dict]) -> list[dict]:
    """The tools as offered to the model: name, description, argument schema, and
    a hash of the three, so that runs offered the same tool can be recognised."""
    out = []
    for s in schemas:
        rec = {"name": s["name"], "description": s.get("description", ""), "parameters": s.get("parameters", {})}
        rec["sha256"] = hashlib.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        out.append(rec)
    return out


def calls(trace_path: str | Path) -> list[dict]:
    """Every call of a v0.4 run, read back from its trace: what an offline
    evaluation needs, with nothing to run again.

    `{"tool", "arguments", "original", "sent", "exposure", "turn", "outcome"}`:
    `original` is what the tool returned, `sent` what the model received (the
    same text unless tracetrim rewrote it). For `expand`, `original` and `sent`
    are its reply and `exposure` is "expand"."""
    out = []
    for line in Path(trace_path).read_text(encoding="utf-8").splitlines():
        e = json.loads(line) if line.strip() else {}
        if e.get("kind") != "call":
            continue
        out.append({"tool": e["tool"], "arguments": to_args(e["arguments"]) if isinstance(e["arguments"], str) else e["arguments"],
                    "original": e["output"], "sent": e.get("sent", e["output"]),
                    "exposure": e.get("exposure", "expand" if e["tool"] == "expand" else FULL),
                    "turn": e["turn"], "outcome": e["outcome"]})
    return out


# -- stop rules ----------------------------------------------------------------

def ledger(where: Where, path: Path | None = None) -> list[dict]:
    path = path or where.ledger
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def spent(where: Where, path: Path | None = None) -> float:
    return sum(e["cost_usd"] for e in ledger(where, path))


def book(where: Where, run: "Run") -> dict:
    """The ledger, the limits and the prices a run is accounted with. E3 has its own:
    another model, another provider, another budget (experiments/e3.py)."""
    if run.experiment == "E3":
        from . import e3
        return e3.book(where)
    return {"ledger": where.ledger, "cap": CAP, "cost": cost, "model": PRICES["model"], "prices": "experiments/prices.json",
            "may_start": may_start}


def dataforseo_runs(where: Where) -> int:
    return sum(1 for e in ledger(where) if e.get("dataforseo_calls"))


def may_start(where: Where, run: Run) -> str | None:
    """Refuse (SystemExit) a run the stop rules forbid; return a warning to print, if any."""
    total = spent(where)
    if total >= CAP:
        raise SystemExit(f"stop rule: {total:.2f} USD already spent, cap {CAP:.0f}")
    limit = V04["dataforseo_checkpoint_runs"]
    if run.dataforseo and dataforseo_runs(where) >= limit and not where.checkpoint.exists():
        raise SystemExit(f"DataForSEO stop rule: {dataforseo_runs(where)} runs have called DataForSEO (checkpoint at "
                         f"{limit}). Report the calls, let the author read the spend, and record the decision in "
                         f"{where.checkpoint} before going on.")
    # Two more stops, decided with the author before the first paid run: E2 does not start
    # until the report on E1 has been reviewed, and nothing starts past the warning until the
    # report on the spend has been.
    if run.experiment == "E2" and run.n == 1 and run.sequence == 1 and not where.e1_reviewed.exists() \
            and not any(e.get("experiment") == "E2" for e in ledger(where)):
        raise SystemExit(f"E1 is over: report it, and record its review in {where.e1_reviewed} before E2 starts.")
    if run.experiment == "E2" and run.case == "C1" and not where.e2_c1_approved.exists():
        raise SystemExit(f"E2 on C1 waits for the decision taken after E2 on C4: report E2 on C4, and have the "
                         f"decision recorded in {where.e2_c1_approved}.")
    if run.experiment == "E2" and run.arm not in e2_arms(run.case, where):
        raise SystemExit(f"E2 on {run.case} is run with the arms {e2_arms(run.case, where)} only: "
                         f"{run.arm} is not among those decided in {where.e2_c1_approved}.")
    if total >= WARN and not where.warning_reviewed.exists():
        raise SystemExit(f"warning: {total:.2f} USD spent (warning at {WARN:.0f}, cap {CAP:.0f}). Report the spend "
                         f"and the runs left, and record the review in {where.warning_reviewed} before going on.")
    return f"WARNING: {total:.2f} USD spent, warning at {WARN:.0f}, cap {CAP:.0f}" if total >= WARN else None


# -- tools ---------------------------------------------------------------------

class Counted:
    """A toolset, with the calls that reached the provider counted per tool."""

    def __init__(self, inner):
        self.inner = inner
        self.reached: dict[str, int] = {}

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def call(self, name: str, args: dict) -> str:
        before = self.inner.rejections
        out = await self.inner.call(name, args)
        if self.inner.rejections == before:          # a call the domain gate refused never left this machine
            self.reached[name] = self.reached.get(name, 0) + 1
        return out


class Replay:
    """Dry run only: stands for the DataForSEO server, with the outputs of a
    recorded run. No network and no spend. The domain gate is the real one."""

    def __init__(self, case: str, recorded: list[dict]):
        self.schemas = [{"type": "function", "name": n, "description": "(dry run)", "strict": False,
                         "parameters": {"type": "object", "properties": {}}} for n in CASE_TOOLS[case]]
        self.recorded = list(recorded)
        self.rejections, self.retries, self.tool_failures = 0, 0, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def call(self, name: str, args: dict) -> str:
        if not domain_gate(name, args):
            self.rejections += 1
            return BLOCKED
        for i, c in enumerate(self.recorded):
            if c["tool"] == name:
                return self.recorded.pop(i)["output"]
        return "Error: nothing recorded for this call (dry run)."


class Trimmed:
    """A toolset with tracetrim in front of it: the same tools, their results
    as the engine exposes them, plus `expand`. `engine` is a fixed one (E1) or
    the one of an `AutoTrim` (E2)."""

    def __init__(self, inner, engine: Engine):
        from tracetrim.engine import EXPAND
        self.inner, self.engine = inner, engine
        self.schemas = inner.schemas + [{"type": "function", "strict": False, "name": EXPAND["name"],
                                         "description": EXPAND["description"], "parameters": EXPAND["inputSchema"]}]
        self.turn = 0
        self.last = None                              # the Observation of the last tool call
        self.original = ""                            # ... and the output the tool returned for it

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def call(self, name: str, args: dict) -> str:
        if name == "expand":
            self.last = None
            return self.engine.expand(str(args.get("call_id", "")), [str(f) for f in args.get("fields") or []])
        out = self.original = await self.inner.call(name, args)
        # An error, or the gate's refusal, goes on as it is.
        self.last = self.engine.observe(ToolCall(name, args, self.turn), ToolResult(out, verbatim=out.startswith("Error")))
        return self.last.text


def fixed_policy(case: str):
    """E1, arms T and ST: the configuration tracetrim builds from the case's
    Phase A traces, fixed for every run. Nothing is learned between runs."""
    from tracetrim import analyse
    from tracetrim import report as rp
    from tracetrim.runtime import Policy
    traces = phase_a(case)
    if len(traces) < 2:
        raise SystemExit(f"{case}: the Phase A traces are needed ($TRACETRIM_TRACES), found {len(traces)}")
    usages = [analyse(t) for t in traces]
    d = rp.compute([t.name for t in traces], usages)
    config, projection = rp.build_config(d, rp.suggestions(d), usages, "report.projection.json")
    if not config["tools"]:
        raise SystemExit(f"{case}: the configuration trims no tool, so the protocol gives it no trimmed arm")
    return Policy(config, projection)


def seed(case: str, workdir: Path, adaptive: Adaptive):
    """E2: a work directory that starts from the projection learned on the case's
    Phase A traces (for C4, read as recorded on the copy of its world)."""
    from tracetrim.auto import AutoTrim, Learner
    if workdir.exists():
        raise SystemExit(f"{workdir} exists: a sequence starts from a fresh work directory")
    traces = phase_a("C4v" if case == "C4" else case)
    (workdir / "traces").mkdir(parents=True)
    for n, t in enumerate(traces, 1):
        lines = []
        for e in t.events:
            d = ({"kind": "call", "tool": e.tool, "arguments": e.text, "output": e.output} if e.kind == "call"
                 else {"kind": e.kind, "text": e.text})
            if e.turn is not None:
                d["turn"] = e.turn
            lines.append(json.dumps(d, ensure_ascii=False))
        (workdir / "traces" / f"run-{n:04d}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    learner = Learner(workdir, MIN_RUNS)
    learner.state["runs"] = learner.state["complete_runs"] = len(traces)
    if learner.relearn() is None:
        raise SystemExit(f"{case}: no configuration is applied after seeding")
    dropped = json.loads((workdir / "report.projection.json").read_text())["dropped"]
    for tool, fields in NEEDS[case].items():
        missing = [f for f in fields if f not in dropped.get(tool, [])]
        if missing:           # the precondition of the experiment: the variant must need fields that are removed
            raise SystemExit(f"{case}: the projection does not remove {missing} of {tool}")
    return AutoTrim(workdir, MIN_RUNS, adaptive=adaptive)


def projection_keeps(workdir: Path, case: str) -> dict:
    kept = json.loads((workdir / "report.projection.json").read_text())["tools"]
    return {f: f in kept.get(tool, []) for tool, fields in NEEDS[case].items() for f in fields}


# -- one run -------------------------------------------------------------------

def instructions(run: Run) -> tuple[str, str]:
    """(the case's system prompt, the skill text this arm adds to it)."""
    system = (PROMPTS / f"{run.case.lower()}-system.md").read_text(encoding="utf-8")
    if run.experiment in ("E1", "E3"):
        strategy, trimming = E1_ARMS[run.arm]
        added = skill.text(strategy=strategy, done=False, expand=trimming)
    else:
        added = skill.text(strategy=False, done=False)          # every E2 arm trims: the `expand` part, no strategy
    return system, added


def user_prompt(run: Run) -> str:
    name = f"{run.case.lower()}{'v' if run.experiment == 'E2' else ''}-user.md"
    return (PROMPTS / name).read_text(encoding="utf-8")


def text_of(item) -> str:
    return "".join(getattr(c, "text", "") or "" for c in (getattr(item, "content", None) or []))


def base_toolset(run: Run, dry: bool):
    if run.experiment == "E3" and run.case in DATAFORSEO:
        from .recorded import Recorded          # recorded outputs, in a paid run as in a dry one: never the provider
        return Recorded(run.case)
    if dry and run.dataforseo:
        return Replay(run.case, recorded(run.case)["calls"])
    if run.experiment == "E2" and run.case == "C4":
        return DataForSEO(CASE_TOOLS["C4"], "experiments.simulated.v04.server_c4")
    return toolset(run.case)


async def execute(run: Run, where: Where = HERE, client=None, model: str | None = None, dry: bool = False) -> dict:
    """One run: the loop, the trace, the meta file, the ledger line."""
    acct = book(where, run)
    cost = acct["cost"]                      # noqa: F811  (the prices of this run's experiment)
    warning = acct["may_start"](where, run)
    if warning:
        print(warning, flush=True)
    before = spent(where, acct["ledger"])
    model = model or acct["model"]
    count = tokenizer()
    system, added = instructions(run)
    system_full = system + ("\n\n" + added if added else "")
    user = user_prompt(run)
    out_dir = where.traces / run.case
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path, meta_path = out_dir / f"{run.id}.jsonl", out_dir / f"{run.id}.meta.json"
    if trace_path.exists():
        raise SystemExit(f"{trace_path} exists: run ids are never reused")

    tt, engine, kept_before = None, None, None
    if run.experiment == "E2":
        workdir = where.workdir(run)
        adaptive = Adaptive(sample_seed=run.sequence, **E2_ARMS[run.arm])
        if run.n == 1:
            tt = seed(run.case, workdir, adaptive)
        else:
            from tracetrim.auto import AutoTrim
            if not (workdir / "state.json").exists():
                raise SystemExit(f"{workdir}: run {run.n} of a sequence needs the work directory of the runs before it")
            tt = AutoTrim(workdir, MIN_RUNS, adaptive=adaptive)
        engine, kept_before = tt.engine, projection_keeps(workdir, run.case)
    elif E1_ARMS[run.arm][1]:
        engine = Engine(policy=fixed_policy(run.case))

    if client is None and run.experiment == "E3":
        from .gemini import Gemini
        client = Gemini()
    elif client is None:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(timeout=900.0, max_retries=0)
    events = [{"kind": "prompt", "text": system_full, "turn": -1}, {"kind": "prompt", "text": user, "turn": -1}]
    turns, flags, total = [], [], {"input": 0, "cached": 0, "output": 0}
    tokens = {"tool_definitions": 0, "instructions_added": count(added), "outputs_original": 0,
              "outputs_sent": {}, "expand_replies": 0, "final_answer": 0}
    exposure: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    model_retries: list[dict] = []
    schemas: list[dict] = []
    reached: dict[str, set] = {}          # needed field -> the ways it reached the model
    n_calls = n_expand = 0
    t0 = time.time()
    try:
        async with base_toolset(run, dry) as base:
            counted = Counted(base)
            tools = Trimmed(counted, engine) if engine is not None else counted
            tokens["tool_definitions"] = count(json.dumps(tools.schemas, ensure_ascii=False))
            schemas = schema_records(tools.schemas)
            kwargs = {"model": model, "instructions": system_full, "tools": tools.schemas,
                      "input": [{"role": "user", "content": user}]}
            while True:
                r = await client.responses.create(**kwargs)
                u = r.usage
                turn = {"input": u.input_tokens, "output": u.output_tokens,
                        "cached": getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0}
                for k in total:
                    total[k] += turn[k]
                # Requests of this turn that failed and were made again by the adapter (E3). A failed
                # request returns no usage: its attempts are written down, with nothing to add to the tokens.
                repeated = list(getattr(r, "retries", None) or [])
                if repeated:
                    turn["model_retries"] = repeated
                    model_retries.extend({"turn": len(turns), **x} for x in repeated)
                pending = []
                for item in r.output:
                    if item.type == "message":
                        events.append({"kind": "message", "text": text_of(item), "turn": len(turns),
                                       **({"model_retries": len(repeated)} if repeated else {})})
                    elif item.type == "function_call":
                        pending.append(item)
                outputs = []
                if engine is not None:
                    tools.turn = len(turns)
                for fc in pending:
                    args = to_args(fc.arguments)
                    failed_before = len(getattr(base, "tool_failures", []))
                    sent = await tools.call(fc.name, args)
                    e = {"kind": "call", "tool": fc.name, "arguments": fc.arguments, "output": sent, "turn": len(turns)}
                    if repeated:
                        e["model_retries"] = len(repeated)
                    if fc.name == "expand":
                        n_expand += 1
                        tokens["expand_replies"] += count(sent)
                        how = "expand"
                        e["outcome"] = EXPAND_ERROR if sent.startswith("Error") else OK
                    else:
                        n_calls += 1
                        obs = getattr(tools, "last", None)
                        how = obs.exposure if obs is not None else FULL
                        original = tools.original if engine is not None else sent
                        e["outcome"] = (GATE_REFUSED if original == BLOCKED
                                        else CLIENT_ERROR if len(getattr(base, "tool_failures", [])) > failed_before
                                        else TOOL_ERROR if getattr(base, "last_is_error", False) else OK)
                        outcomes[e["outcome"]] = outcomes.get(e["outcome"], 0) + 1
                        if getattr(base, "last_match", None):          # answered from recorded outputs (E3)
                            e["replay"], e["replay_source"] = base.last_match, base.last_source
                        tokens["outputs_original"] += count(original)
                        tokens["outputs_sent"][how] = tokens["outputs_sent"].get(how, 0) + count(sent)
                        exposure[how] = exposure.get(how, 0) + 1
                        if original != sent:
                            e.update(output=original, sent=sent)
                        e["exposure"] = how
                    for f in NEEDS.get(run.case, {}).get(fc.name, []) if run.experiment == "E2" else []:
                        if _carries(sent, f):
                            reached.setdefault(f, set()).add(how)
                    if run.experiment == "E2" and fc.name == "expand":
                        for fields in NEEDS[run.case].values():
                            for f in fields:
                                if _carries(sent, f):
                                    reached.setdefault(f, set()).add("expand")
                    events.append(e)
                    outputs.append({"type": "function_call_output", "call_id": fc.call_id, "output": sent})
                turn["calls"] = len(pending)
                turns.append(turn)
                run_cost = cost(total)
                print(f"  turn {len(turns):>2}: {len(pending):>2} calls · input {turn['input']:>7,} "
                      f"· output {turn['output']:>6,} · run {run_cost:.3f} USD", flush=True)
                if not pending:
                    break
                if before + run_cost >= acct["cap"]:
                    flags.append("stopped_by_budget")
                    break
                if n_calls >= GUARD_CALLS or len(turns) >= GUARD_TURNS:
                    flags.append("safety_guard")
                    break
                kwargs = {"model": model, "instructions": system_full, "tools": tools.schemas,
                          "previous_response_id": r.id, "input": outputs}
            dataforseo_calls = dict(counted.reached) if run.dataforseo else {}
            replay = dict(base.matches) if hasattr(base, "matches") else None
            rejections, failures, retries = base.rejections, getattr(base, "tool_failures", []), getattr(base, "retries", 0)
    except BaseException:
        # The tokens of an aborted run are spent all the same: the ledger must see them.
        _ledger(acct["ledger"], {"experiment": run.experiment, "case": run.case, "run": run.id, "arm": run.arm,
                                 "model": model, "aborted": True, "cost_usd": round(cost(total), 4), "usage": total,
                                 "dry_run": dry})
        raise

    messages = [e["text"] for e in events if e["kind"] == "message"]
    answer = messages[-1] if messages else ""
    tokens["final_answer"] = count(answer)
    trace_path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    # The tools as the model was offered them, `expand` included: recorded, never used by a measure.
    out_dir.joinpath(f"{run.id}.tools.json").write_text(json.dumps(schemas, indent=2, ensure_ascii=False) + "\n",
                                                        encoding="utf-8")
    adaptive_meta = None
    if tt is not None:
        # The run is closed in the sequence's work directory: this is where learning happens.
        tt.done(answer if "stopped_by_budget" not in flags and "safety_guard" not in flags else None)
        st = tt.learner.state
        report = json.loads((where.workdir(run) / "report.json").read_text())["adaptive"]
        adaptive_meta = {
            "settings": report["settings"], "kept_before": kept_before,
            "kept_after": projection_keeps(where.workdir(run), run.case),
            "reached": {f: sorted(reached.get(f, [])) for fields in NEEDS[run.case].values() for f in fields},
            "evidence": {tool: {f: report["evidence"].get(tool, {}).get("fields", {}).get(f)
                                for f in fields} for tool, fields in NEEDS[run.case].items()},
            "promotions": [{k: p[k] for k in ("tool", "field", "reason", "runs", "status")} for p in report["promotions"]],
            "switched_off": list(st["off"]), "filtered": dict(st["trimmed"]), "expanded": dict(st["expanded"]),
            "sampling": st["sampling"], "full_cheaper": st["full_cheaper"],
        }
    meta = {
        "protocol": "protocol-v04.md", "experiment": run.experiment, "case": run.case, "run": run.id, "arm": run.arm,
        "sequence": run.sequence or None, "n": run.n, "model": model, "dry_run": dry,
        "started": datetime.fromtimestamp(t0, timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.time() - t0, 1),
        "skill": {"version": skill.VERSION, "strategy": "### Know when to stop" in added, "expand": "`expand`" in added,
                  "tokens": count(added)},
        "trimming": engine is not None, "tools_trimmed": sorted(engine.policy.tools) if engine is not None else [],
        "turns": len(turns), "calls": n_calls, "expand_calls": n_expand, "usage": total, "usage_per_turn": turns,
        "tokens": tokens, "exposure": exposure, "outcomes": outcomes, "dataforseo_calls": dataforseo_calls,
        "tool_schemas": {s["name"]: s["sha256"] for s in schemas},
        # E3 on the DataForSEO cases: how the calls were answered from recorded outputs.
        "replay": replay, "pilot": run.pilot,
        "model_retries": model_retries,
        "domain_rejections": rejections, "tool_failures": failures, "retries": retries,
        "flags": flags + (["tool_failure"] if failures else []),
        "adaptive": adaptive_meta, "cost_usd": round(cost(total), 4), "prices": acct["prices"],
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _ledger(acct["ledger"], {k: meta[k] for k in ("experiment", "case", "run", "arm", "model", "started", "cost_usd",
                                                    "dry_run")}
            | {"usage": total, "dataforseo_calls": sum(dataforseo_calls.values()), "model_retries": len(model_retries)})
    return meta


def _carries(text: str, field: str) -> bool:
    """Does this result (or `expand` reply) carry a value for the field?"""
    payload = parse_output(text.split(MARK)[0])
    return isinstance(payload, (dict, list)) and any(p == field and v is not None for p, v in leaves(payload))


def _ledger(path: Path, line: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


# -- the stub that stands for the model in a dry run ------------------------------

def recorded(case: str) -> dict:
    """The first Phase A run of the case, as a script: its calls grouped by turn, and its answer."""
    path = STUDY_TRACES / case / "A1.jsonl"
    if not path.exists():
        raise SystemExit(f"a dry run replays {path}: set $TRACETRIM_TRACES")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    per_turn = [t["calls"] for t in json.loads(path.with_suffix(".meta.json").read_text())["usage_per_turn"]]
    calls = [e for e in events if e["kind"] == "call"]
    turns, i = [], 0
    for n in per_turn:
        if n:
            turns.append(calls[i:i + n])
            i += n
    return {"calls": calls, "turns": turns, "answer": [e["text"] for e in events if e["kind"] == "message"][-1]}


class Stub:
    """Stands for the model: it emits the calls of a recorded run, turn by turn,
    then the recorded answer. Two additions make it exercise what the real runs
    will: once, a call the domain gate must refuse; and, when `needs` is given
    (E2), an `expand` for a needed field that a result does not carry, whose
    values it then writes in the answer."""

    def __init__(self, script: dict, needs: dict | None = None, gate_probe: bool = False, perturb: bool = False):
        self.turns, self.answer = [list(t) for t in script["turns"]], script["answer"]
        if perturb and self.turns and self.turns[0]:
            # Once, a call whose arguments were never recorded: the stand-in for the
            # provider must answer it with the nearest recorded output, and say so.
            c = dict(self.turns[0][0])
            args = c["arguments"] if isinstance(c["arguments"], dict) else to_args(c["arguments"])
            key = next((k for k, v in args.items() if isinstance(v, str)), None)
            c["arguments"] = json.dumps({**args, key: args[key] + " novita"} if key else {**args, "note": "novita"},
                                        ensure_ascii=False)
            self.turns[0][0] = c
        self.needs = needs or {}
        self.gate_probe = gate_probe
        self.count = tokenizer()
        self.seen_input = 0
        self.tool_of: dict[str, str] = {}
        self.values: dict[str, str] = {}
        self.n = 0
        self.requests: list[dict] = []
        self.responses = self

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        self.n += 1
        new = self.count(json.dumps(kwargs["input"], ensure_ascii=False))
        if self.n == 1:
            new += self.count(kwargs["instructions"]) + self.count(json.dumps(kwargs["tools"], ensure_ascii=False))
        cached, self.seen_input = self.seen_input, self.seen_input + new
        expands = []
        for item in kwargs["input"]:
            if item.get("type") != "function_call_output":
                continue
            tool, text = self.tool_of.get(item["call_id"]), item["output"]
            fields = self.needs.get(tool, [])
            for f in fields:
                self._note(text, f)
            missing = [f for f in fields if f not in self.values]
            if missing and MARK in text:
                expands.append((text.rsplit("call_id=", 1)[1].split()[0], missing))
            if tool == "expand":
                for fields in self.needs.values():
                    for f in fields:
                        self._note(text, f)
        out = []
        if expands:
            for cid, fields in expands:
                out.append(self._call("expand", json.dumps({"call_id": cid, "fields": fields})))
        elif self.turns:
            for c in self.turns.pop(0):
                args = c["arguments"]
                out.append(self._call(c["tool"], args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)))
            if self.gate_probe:
                self.gate_probe = False
                out.append(self._call("on_page_content_parsing", json.dumps({"url": "https://example.com/"})))
        else:
            answer = self.answer + "".join(f"\n{f.rsplit('.', 1)[-1]}: {v}" for f, v in sorted(self.values.items()))
            out.append(SimpleNamespace(type="message", content=[SimpleNamespace(text=answer)]))
        written = sum(self.count(getattr(o, "arguments", "") or text_of(o)) for o in out)
        return SimpleNamespace(id=f"stub-{self.n}", output=out, usage=SimpleNamespace(
            input_tokens=self.seen_input, output_tokens=written,
            input_tokens_details=SimpleNamespace(cached_tokens=cached)))

    def _call(self, name: str, arguments: str):
        cid = f"call_{len(self.tool_of) + 1}"
        self.tool_of[cid] = name
        return SimpleNamespace(type="function_call", name=name, arguments=arguments, call_id=cid)

    def _note(self, text: str, field: str) -> None:
        payload = parse_output(text.split(MARK)[0])
        if field not in self.values and isinstance(payload, (dict, list)):
            for p, v in leaves(payload):
                if p == field and v is not None:
                    self.values[field] = str(v)
                    return


def stub_for(run: Run) -> Stub:
    return Stub(recorded(run.case), NEEDS[run.case] if run.experiment == "E2" else None,
                gate_probe=run.case in DATAFORSEO, perturb=run.experiment == "E3" and run.case in DATAFORSEO)


async def dry_run(directory: str | Path, e2_runs: int = 2) -> list[dict]:
    """One run of every arm of E1, and the first `e2_runs` runs of the first
    sequence of every arm of E2, with the stub. Nothing is spent."""
    where = Where(Path(directory))
    if where.root.exists() and any(where.root.iterdir()):
        raise SystemExit(f"{where.root} is not empty: a dry run starts from an empty directory")
    metas = []
    for run in schedule(where):
        wanted = run.n == 1 if run.experiment == "E1" else (run.sequence == 1 and run.n <= e2_runs)
        if not wanted:
            continue
        print(f"{run.case} {run.id} (dry run)", flush=True)
        for _ in range(3):
            try:
                metas.append(await execute(run, where, client=stub_for(run), model="stub", dry=True))
                break
            except SystemExit as stop:
                # A stop fired, as it must. In a dry run the "decision" is recorded here,
                # so that the remaining arms can be exercised too.
                gate = (where.checkpoint if "DataForSEO stop rule" in str(stop)
                        else where.e1_reviewed if "E1 is over" in str(stop)
                        else where.e2_c1_approved if "E2 on C1 waits" in str(stop) else None)
                if gate is None or gate.exists():
                    raise
                print(f"  stopped: {str(stop)[:76]}...", flush=True)
                gate.parent.mkdir(parents=True, exist_ok=True)
                gate.write_text(json.dumps({"dry_run": True, "stopped_before": f"{run.case} {run.id}",
                                            "runs_that_called_dataforseo": dataforseo_runs(where)}) + "\n")
    return metas


# -- command line -----------------------------------------------------------------

def done(where: Where, run: Run) -> bool:
    return (where.traces / run.case / f"{run.id}.meta.json").exists()


def main(argv=None):
    p = argparse.ArgumentParser(prog="experiments.run --v04", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("schedule", help="every run in order, and which are done")
    sub.add_parser("next", help="make the next run not done yet (paid)")
    s = sub.add_parser("run", help="make one run (paid)")
    s.add_argument("run_id")
    s.add_argument("case")
    s = sub.add_parser("dry-run", help="every arm with a stub in place of the model (no spend)")
    s.add_argument("directory")
    a = p.parse_args(argv)
    runs = schedule()
    if a.cmd == "schedule":
        seen = 0
        for n, r in enumerate(runs, 1):
            seen += r.dataforseo
            mark = "done" if done(HERE, r) else ""
            dfs = f"DataForSEO run {seen}" if r.dataforseo else ""
            print(f"{n:>4}  {r.case}  {r.id:<32}{dfs:<20}{mark}")
        print(f"\n{len(runs)} runs · spent {spent(HERE):.2f} USD of {CAP:.0f} (warning at {WARN:.0f}) · "
              f"{dataforseo_runs(HERE)} runs have called DataForSEO (checkpoint at {V04['dataforseo_checkpoint_runs']})")
    elif a.cmd == "dry-run":
        metas = asyncio.run(dry_run(a.directory))
        print(f"\n{len(metas)} dry runs in {a.directory}: nothing spent")
    else:
        run = (next((r for r in runs if not done(HERE, r)), None) if a.cmd == "next"
               else next((r for r in runs if r.id == a.run_id and r.case == a.case), None))
        if run is None:
            raise SystemExit("no such run" if a.cmd == "run" else "every run of the protocol is done")
        m = asyncio.run(execute(run))
        print(f"\n  {m['case']} {m['run']}: {m['turns']} turns, {m['calls']} calls, {m['cost_usd']:.3f} USD, flags {m['flags']}")


if __name__ == "__main__":
    main()
