"""A stand-in for the DataForSEO server that answers with outputs already recorded. No provider call, ever.

Experiment E3 (protocol-v04-e3.md) measures how a model organises its tool
calls on the two cases built on DataForSEO without paying the provider again:
the tools are offered with the schemas recorded in the E1 runs, and each call
is answered with an output recorded in an earlier run of the same case
(Phase A of the original study and E1), kept locally.

  exact     an earlier call of the same tool with the same arguments, once
            normalised: that call's output
  nearest   no such call: the output of the recorded call of the SAME tool
            whose arguments are the most similar. The content is then not
            what the provider would return for these arguments: the call is
            marked, and nothing about content is evaluated on these cases
  none      the tool was never recorded: an error text

The rule for "nearest" is deterministic. Arguments are flattened into
tokens (`path=word` for every word of every value); similarity is the
Jaccard index of the two token sets; ties go to the first call of the pool,
which is ordered by source file and position.

The study's domain gate is applied first, as in the real wrapper.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from tracetrim.usage import leaves

from .paths import ROOT, STUDY_TRACES
from .tools import BLOCKED, CASE_TOOLS, domain_gate, to_args

EXACT, NEAREST, NONE = "exact", "nearest", "none"
_WORD = re.compile(r"\w+")


def normalise(arguments: dict) -> str:
    """Arguments compared as data: keys sorted, strings trimmed and lower-cased."""
    def norm(x):
        if isinstance(x, str):
            return " ".join(x.lower().split())
        if isinstance(x, list):
            return [norm(v) for v in x]
        if isinstance(x, dict):
            return {str(k): norm(v) for k, v in sorted(x.items())}
        return x
    return json.dumps(norm(arguments or {}), sort_keys=True, ensure_ascii=False)


def tokens(arguments: dict) -> frozenset:
    out = set()
    for path, value in leaves(arguments or {}):
        words = _WORD.findall(str(value).lower()) or [""]
        out |= {f"{path}={w}" for w in words}
    return frozenset(out)


def pool(case: str, traces: Path | None = None) -> list[dict]:
    """Every recorded call of the case that got an answer: Phase A of the original
    study, then the E1 runs of this checkout. Errors and gate refusals are left out."""
    calls = []
    sources = sorted((STUDY_TRACES / case).glob("A*.jsonl")) + sorted(((traces or ROOT / "traces") / case).glob("E1-*.jsonl"))
    for path in sources:
        for line in path.read_text(encoding="utf-8").splitlines():
            e = json.loads(line) if line.strip() else {}
            if e.get("kind") != "call" or e.get("tool") not in CASE_TOOLS[case]:
                continue
            out = e.get("output") or ""
            if not out or out.startswith("Error") or e.get("outcome", "ok") != "ok":
                continue
            args = e["arguments"] if isinstance(e["arguments"], dict) else to_args(e["arguments"])
            calls.append({"tool": e["tool"], "key": normalise(args), "tokens": tokens(args), "output": out,
                          "source": f"{path.stem}"})
    return calls


def schemas(case: str, traces: Path | None = None) -> list[dict]:
    """The tools as the provider's server describes them, from the snapshot taken in an E1 run."""
    snapshot = (traces or ROOT / "traces") / case / "E1-R-1.tools.json"
    if not snapshot.exists():
        raise SystemExit(f"{snapshot} is needed: the tool schemas recorded in E1")
    return [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["parameters"],
             "strict": False} for t in json.loads(snapshot.read_text()) if t["name"] in CASE_TOOLS[case]]


class Recorded:
    """A toolset with the interface of the study's wrapper, answering from the pool."""

    def __init__(self, case: str, calls: list[dict] | None = None, tool_schemas: list[dict] | None = None):
        self.case = case
        self.pool = pool(case) if calls is None else calls
        self.schemas = schemas(case) if tool_schemas is None else tool_schemas
        self.by_key = {}
        for c in self.pool:
            self.by_key.setdefault((c["tool"], c["key"]), c)
        self.rejections, self.retries, self.tool_failures = 0, 0, []
        self.last_match: str | None = None        # how the last call was answered
        self.last_source: str | None = None
        self.matches = {EXACT: 0, NEAREST: 0, NONE: 0}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def lookup(self, name: str, args: dict) -> tuple[str, dict | None]:
        hit = self.by_key.get((name, normalise(args)))
        if hit is not None:
            return EXACT, hit
        mine, best, score = tokens(args), None, -1.0
        for c in self.pool:
            if c["tool"] != name:
                continue
            union = len(mine | c["tokens"])
            s = len(mine & c["tokens"]) / union if union else 1.0
            if s > score:                                   # strictly: the first of the pool wins a tie
                best, score = c, s
        return (NEAREST, best) if best is not None else (NONE, None)

    async def call(self, name: str, args: dict) -> str:
        self.last_match = self.last_source = None
        if not domain_gate(name, args):
            self.rejections += 1
            return BLOCKED
        how, hit = self.lookup(name, args)
        self.last_match, self.last_source = how, hit["source"] if hit else None
        self.matches[how] += 1
        return hit["output"] if hit else f"Error: no recorded output for the tool {name}."
