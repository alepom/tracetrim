"""Paths, prices and helpers shared by the analyses, with no dependency on the
agent SDKs: regenerating the paper's numbers and figures needs only the
library and the results (the agent loop, run.py, needs openai; tools.py, mcp).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "experiments" / "prompts"
# Traces live in traces/ where they were recorded; from the Zenodo dataset they
# are in dataset/traces/ (C3, C4 and C5 only: C1 and C2 are not redistributable).
TRACES = next((p for p in (ROOT / "traces", ROOT / "dataset" / "traces") if any(p.glob("C*/A*.jsonl"))),
              ROOT / "traces")
LEDGER = ROOT / "out" / "ledger.jsonl"
PRICES = json.loads((ROOT / "experiments" / "prices.json").read_text())

# Replay arms: Phase B (tracetrim 0.1) and Study 2 (safeguards S1 + S2).
ARMS = ("raw", "prudent", "aggressive", "structural")
ARMS_D = ("raw", "v02", "v02-structural")
PREFIX = {"B": ARMS, "D": ARMS_D}


def cost(usage: dict) -> float:
    cached = usage.get("cached", 0)
    return ((usage["input"] - cached) * PRICES["input_per_mtok"]
            + cached * PRICES["cached_input_per_mtok"]
            + usage["output"] * PRICES["output_per_mtok"]) / 1e6


def spent() -> float:
    if not LEDGER.exists():
        return 0.0
    return sum(json.loads(l)["cost_usd"] for l in LEDGER.read_text().splitlines() if l.strip())


def keep_missing(res: dict, out_path, order) -> dict:
    """Cases whose traces are not here (C1 and C2 outside the machine that
    recorded them) keep their entry from the existing results file instead of
    disappearing from it: re-running an analysis on the released data must
    never shrink the results."""
    old = json.loads(Path(out_path).read_text()) if Path(out_path).exists() else {"cases": {}}
    merged = {}
    for case in order:
        if case in res["cases"]:
            merged[case] = res["cases"][case]
        elif case in old.get("cases", {}):
            merged[case] = old["cases"][case]
            print(f"  {case}: traces not available here, kept from {Path(out_path).name}")
    res["cases"] = merged
    for key, value in old.items():
        res.setdefault(key, value)       # e.g. the transfer analysis, which needs C1 and C2
    return res
