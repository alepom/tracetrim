#!/usr/bin/env python3
"""The check of tracetrim.grounding (since 1.1) on the runs already recorded: does it see what the studies found?

  PYTHONPATH=src .venv/bin/python -m experiments.grounding
  TRACETRIM_TRACES=/path/to/traces ...      (the E1 runs, when they are in another checkout)

The library's own function, `grounding.unsupported`, on:

  Study 1 (Phase B replays: raw, prudent, aggressive, structural arms of tracetrim 0.1, without the safeguards),
  Study 2 (Phase D replays: raw and v0.2, with the safeguards),
  E1 of protocol-v04.md (live runs: R raw, S strategy, T trimming, ST both), where the traces are available.

What to expect, from the paper: in Study 1, on the literature scan (C3), the agent made up identifiers once the
projection removed them; with the safeguards of 0.2 it did not. A useful check flags the first and stays quiet
on the arms without trimming. Exploratory; written after the results; no model is called.
Numbers: results/v1/grounding.json.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from tracetrim.grounding import unsupported
from tracetrim.traces import load

from .paths import ROOT, STUDY_TRACES, TRACES

OUT = ROOT / "results" / "v1" / "grounding.json"
CASES = ("C1", "C2", "C3", "C4", "C5")


def arm_of(stem: str) -> str:
    """B-aggressive-1 -> B-aggressive; E1-ST-3 -> E1-ST."""
    return stem.rsplit("-", 1)[0]


def main():
    out = defaultdict(dict)
    for case in CASES:
        found = sorted((TRACES / case).glob("[BD]-*.jsonl")) + sorted((STUDY_TRACES / case).glob("E1-*.jsonl"))
        for p in found:
            if "pilot" in p.name:
                continue
            n, missing = unsupported(load(p))
            arm = out[case].setdefault(arm_of(p.stem), {"runs": 0, "values": 0, "unsupported": [], "runs_flagged": 0})
            arm["runs"] += 1
            arm["values"] += n
            arm["unsupported"] += missing
            arm["runs_flagged"] += bool(missing)
    if not any(out.values()):
        raise SystemExit("no trace read: check traces/ (and TRACETRIM_TRACES for E1)")
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # As the report does (grounding.summarise): a value also unsupported in an arm where nothing is filtered
    # (raw, or the strategy alone) is not held against trimming. What remains is what the suggestion would name.
    for case, arms in out.items():
        base = {v.lower() for arm, a in arms.items() if unfiltered(arm) for v in a["unsupported"]}
        for arm, a in arms.items():
            a["suspect"] = [] if unfiltered(arm) else [v for v in a["unsupported"] if v.lower() not in base]
    # C1 and C2 answers are written from DataForSEO responses, which are not redistributed: counts only.
    public = {case: {arm: (a if case not in ("C1", "C2") else
                           {k: (len(v) if isinstance(v, list) else v) for k, v in a.items()})
                     for arm, a in arms.items()} for case, arms in out.items()}
    OUT.write_text(json.dumps(public, indent=2, ensure_ascii=False) + "\n")

    print(f"{'caso':<5}{'braccio':<20}{'esecuzioni':>11}{'valori':>8}{'non trovati':>13}{'sospetti':>10}   esempi")
    for case, arms in out.items():
        for arm, a in arms.items():
            ex = ", ".join(f"{v[:24]}×{k}" for v, k in Counter(a["unsupported"]).most_common(3))
            print(f"{case:<5}{arm:<20}{a['runs']:>11}{a['values']:>8}{len(a['unsupported']):>13}{len(a['suspect']):>10}   {ex}")
    print("\nsospetti: non trovati in un braccio filtrato e mai non trovati in un braccio senza filtro (raw, E1-R, E1-S)")


def unfiltered(arm: str) -> bool:
    return arm.endswith("-raw") or arm in ("E1-R", "E1-S")


if __name__ == "__main__":
    main()
