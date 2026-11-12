#!/usr/bin/env python3
"""Would the tools switched off in E2 have been switched off with evidence from more runs?

  python -m experiments.switch_off_recalc         # results/v04/switch_off.json

Offline, from the work directories of the E2 sequences (out/v04/e2): the
notes tracetrim kept next to each run say which results were filtered and
which of them were expanded. The script replays the counters run by run, with
the resets a promotion or a field kept by ordinary learning causes, and applies
the rule of the switch-off (`tracetrim.auto.over_expanded`) with the number
of distinct runs it asks for:

  as run    what each arm ran with: no requirement, or 2 runs where promotion was on
  2, 3      `Adaptive(switch_off_runs=N)`

What it can and cannot say. Up to the first run in which two rules decide
differently, the runs are the same under both, and the counts are exact.
After it they are not: the model then saw the tool whole, and what it would
have asked of a trimmed result is not in the traces. So a tool "not switched
off" by a stricter rule means not at that point, with what was recorded.
The replay of the rule as run must give back what the work directory says:
that is checked, and the script stops if it does not.
"""
from __future__ import annotations

import json
from pathlib import Path

from tracetrim.auto import over_expanded

from .paths import ROOT
from .v04 import E2_ARMS

OUT = ROOT / "results" / "v04" / "switch_off.json"
RULES = (2, 3)


def sequence(workdir: Path, promote: bool) -> dict:
    """One sequence: per tool, the run after which each rule switches it off."""
    ev = json.loads((workdir / "evidence.json").read_text())
    resets: dict[str, set] = {}
    for p in ev.get("resumed", []):
        resets.setdefault(p["after"], set()).add(p["tool"])
    for p in ev.get("promotions", []):
        resets.setdefault(p["decided_after"], set()).add(p["tool"])
    rules = {"as_run": 2 if promote else 0, **{str(n): max(n, 2 if promote else 0) for n in RULES}}
    off: dict[str, dict] = {r: {} for r in rules}
    filtered: dict[str, int] = {}
    expanded: dict[str, int] = {}
    runs: dict[str, set] = {}
    per_run = []
    for log in sorted((workdir / "evidence").glob("run-*.jsonl")):
        run = log.stem
        notes = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
        for n in notes:
            if n["kind"] == "observation" and n.get("call_id"):
                filtered[n["tool"]] = filtered.get(n["tool"], 0) + 1
            elif n["kind"] == "expand":
                runs.setdefault(n["tool"], set()).add(run)
                if n["recovered"] and not n["repeat"]:
                    expanded[n["tool"]] = expanded.get(n["tool"], 0) + 1
        for tool in resets.get(run, ()):                # a cut was corrected: the count starts again
            for counter in (filtered, expanded, runs):
                counter.pop(tool, None)
        for tool, n in filtered.items():
            row = {"run": run, "tool": tool, "filtered": n, "expanded": expanded.get(tool, 0),
                   "runs_with_expand": len(runs.get(tool, ()))}
            if row["expanded"]:
                per_run.append(row)
            for rule, needed in rules.items():
                if tool not in off[rule] and over_expanded(n, row["expanded"], row["runs_with_expand"], needed):
                    off[rule][tool] = run
    recorded = json.loads((workdir / "state.json").read_text())["off"]
    if sorted(off["as_run"]) != sorted(recorded):
        raise SystemExit(f"{workdir}: the replay switches off {sorted(off['as_run'])}, the work directory says {sorted(recorded)}")
    return {"switched_off": off, "counts_where_a_result_was_expanded": per_run}


def analyse(root: Path) -> dict:
    out, totals = [], {}
    for workdir in sorted(p for p in (root / "out" / "v04" / "e2").glob("*/*") if (p / "evidence.json").exists()):
        arm = workdir.name.rsplit("-s", 1)[0]
        s = sequence(workdir, bool(E2_ARMS[arm].get("promote")))
        out.append({"case": workdir.parent.name, "sequence": workdir.name, **s})
        for rule, tools in s["switched_off"].items():
            totals[rule] = totals.get(rule, 0) + len(tools)
    return {"what": "offline recalculation of the switch-off of a tool on the E2 sequences, by the number of distinct runs asked",
            "limit": "exact up to the first run in which two rules decide differently; not after it",
            "sequences": len(out), "switch_offs": totals, "per_sequence": out}


if __name__ == "__main__":
    res = analyse(ROOT)
    OUT.write_text(json.dumps(res, indent=2) + "\n")
    print(f"{res['sequences']} sequences · tools switched off, by distinct runs asked: {res['switch_offs']}")
    for s in res["per_sequence"]:
        if any(s["switched_off"].values()):
            print(f"  {s['case']} {s['sequence']}: {s['switched_off']}")
            for row in s["counts_where_a_result_was_expanded"]:
                print(f"    {row}")
    print(OUT)
