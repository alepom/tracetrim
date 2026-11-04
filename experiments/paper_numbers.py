#!/usr/bin/env python3
"""Every number and table of the paper, generated from results/*.json.

  .venv/bin/python -m experiments.paper_numbers      # writes paper/generated/*.tex

The paper never types a number: it uses the macros written here (numbers.tex)
and inputs the tables. Rerun after any analysis; check_numbers.py then
verifies that no decimal in the prose is missing from these files.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from tracetrim import about

from .run import ROOT

RES = ROOT / "results"
GEN = ROOT / "paper" / "generated"
ROMAN = {"C1": "i", "C2": "ii", "C3": "iii", "C4": "iv", "C5": "v"}
CASE_NAME = {"C1": "Editorial plan", "C2": "Content gaps", "C3": "Literature scan",
             "C4": "Billing support$^\\dagger$", "C5": "CI triage$^\\dagger$"}


def pct(x, signed=True):
    v = round(100 * x)
    return (f"$-${abs(v)}\\%" if v < 0 else (f"+{v}\\%" if signed and v > 0 else f"{v}\\%"))


def apct(x, signed=None):
    """Prose form: the verb carries the direction ("removed 63%", "fell by 56%")."""
    return f"{abs(round(100 * x))}\\%"


def num(n):
    return f"{int(round(n)):,}".replace(",", "{,}")


def load(name):
    p = RES / name
    return json.loads(p.read_text()) if p.exists() else None


def main():
    GEN.mkdir(parents=True, exist_ok=True)
    macros = {}
    m = lambda k, v: macros.__setitem__(k, v)

    # ---- meta -------------------------------------------------------------
    (GEN / "meta.tex").write_text(
        f"\\newcommand{{\\PaperAuthors}}{{{about.AUTHOR}\\\\\\small {about.AUTHOR_ROLE}, ORCID {about.ORCID}}}\n"
        f"\\newcommand{{\\PaperVersionLine}}{{Preprint, version {about.VERSION} of {about.NAME}}}\n"
        f"\\newcommand{{\\PaperStatus}}{{Preprint, not peer reviewed.}}\n", encoding="utf-8")

    # ---- Phase A (Study 1) ------------------------------------------------
    a = load("phase_a.json")
    rows = []
    if a:
        for c, r in a["cases"].items():
            R = ROMAN[c]
            full, calls, strict, rq1 = r["loo_full"], r["loo_calls"], r["loo_strict"], r["rq1"]
            m(f"PhaseARuns{R}", str(len(r["runs"])))
            m(f"PhaseACalls{R}", num(r["calls"]))
            m(f"PhaseARaw{R}", num(full["raw"]))
            m(f"PhaseAUsedShare{R}", apct(rq1["share"], False))
            m(f"PhaseAPrudentSaving{R}", apct(-full["prudent_saving"]))
            m(f"PhaseAAggressiveSaving{R}", apct(-full["aggressive_saving"]))
            m(f"PhaseAMisses{R}", f"{full['misses']}/{full['trimmed_calls']}")
            m(f"PhaseAMissesCalls{R}", f"{calls['misses']}/{calls['trimmed_calls']}")
            g = r["gates"]
            fo, ft = sum(x["facts_ok"] for x in g), sum(x["facts"] for x in g)
            m(f"PhaseAFacts{R}", apct(fo / ft, False) if ft else "--")
            rows.append(f"{CASE_NAME[c]} & {len(r['runs'])} & {num(r['calls'])} & {num(full['raw'])} & "
                        f"{pct(rq1['share'], False)} & {pct(-full['prudent_saving'])} & {pct(-full['aggressive_saving'])} & "
                        f"{full['misses']}/{full['trimmed_calls']} & {calls['misses']}/{calls['trimmed_calls']} \\\\")
        t = a.get("rq5")
        if t:
            m("TransferMisses", f"{t['transfer']['misses']}/{t['transfer']['calls']}")
            m("WithinMisses", f"{t['within']['misses']}/{t['within']['calls']}")
        (GEN / "table_phase_a.tex").write_text(
            "\\begin{tabular}{lrrrrrrrr}\n\\toprule\n"
            "Case & Runs & Calls & Tool-output tokens & Used & Prudent & Aggressive & Misses & Misses (calls only) \\\\\n"
            "\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    # ---- Phase B and Study 2 (replays) -----------------------------------
    for name, label in (("phase_b.json", "PhaseB"), ("study2.json", "StudyTwo")):
        b = load(name)
        if not b:
            continue
        rows = []
        for c, r in b["cases"].items():
            R = ROMAN[c]
            raw_tok = r["arms"]["raw"]["tool_tokens"]
            for arm, x in r["arms"].items():
                key = arm.replace("-", "").replace("v02", "Vtwo").capitalize()
                m(f"{label}{key}Tokens{R}", apct(x["tool_tokens"] / raw_tok - 1))
                m(f"{label}{key}Gates{R}", f"{x['gates_passed']}/{len(x['runs'])}")
                meds = ", ".join(f"{k.replace('_', ' ')} {v:g}" for k, v in x["medians"].items())
                rows.append(f"{CASE_NAME[c] if arm == 'raw' else ''} & {arm} & {pct(x['tool_tokens'] / raw_tok - 1) if arm != 'raw' else num(raw_tok)} & "
                            f"{x['gates_passed']}/{len(x['runs'])} & {'' if arm == 'raw' else ('holds' if x['holds'] else 'does not hold')} \\\\")
            if r.get("headroom"):
                h = r["headroom"]
                m(f"{label}HeadroomTokens{R}", apct(h["headroom"] / h["tool_tokens"] - 1))
                rows.append(f" & Headroom (tokens only) & {pct(h['headroom'] / h['tool_tokens'] - 1)} & -- & -- \\\\")
            rows.append("\\addlinespace")
        (GEN / f"table_{name.removesuffix('.json')}.tex").write_text(
            "\\begin{tabular}{llrrl}\n\\toprule\nCase & Arm & Tool tokens & Gates passed & Band rule \\\\\n\\midrule\n"
            + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    s2 = load("study2.json")
    if s2 and "C4" in s2["cases"]:
        for arm, key in (("raw", "Raw"), ("v02", "Vtwo"), ("v02-structural", "Vtwostructural")):
            th = s2["cases"]["C4"]["arms"][arm]["thermometers"]
            m(f"StudyTwoStrictAllRight{key}", f"{sum(t.get('correct_diagnoses_strict') == 5 for t in th)}/{len(th)}")
            m(f"StudyTwoFrozenAllRight{key}", f"{sum(t.get('correct_diagnoses') == 5 for t in th)}/{len(th)}")
        m("StudyTwoRunsPerArm", str(len(s2["cases"]["C4"]["arms"]["raw"]["runs"])))

    # ---- Phase C ----------------------------------------------------------
    cdat = load("phase_c.json")
    if cdat:
        rows = []
        for c, arms in cdat["cases"].items():
            R = ROMAN[c]
            for arm, x in arms.items():
                med = lambda k: statistics.median(x[k])
                key = arm.capitalize()
                m(f"PhaseC{key}Input{R}", num(med("billed_input")))
                m(f"PhaseC{key}Uncached{R}", num(med("uncached_input")))
                m(f"PhaseC{key}Expand{R}", f"{med('expand_calls'):g}")
                m(f"PhaseC{key}Gates{R}", f"{x['gates_passed']}/{len(x['runs'])}")
                rows.append(f"{CASE_NAME[c] if arm == 'raw' else ''} & {arm} & {num(med('billed_input'))} & "
                            f"{num(med('uncached_input'))} & {med('turns'):g} & {med('expand_calls'):g} & "
                            f"{x['gates_passed']}/{len(x['runs'])} \\\\")
            if "raw" in arms and "prudent" in arms:
                rr, pp = arms["raw"], arms["prudent"]
                for k, label in (("billed_input", "Input"), ("uncached_input", "Uncached"), ("cost_usd", "Cost")):
                    m(f"PhaseCChange{label}{R}", apct(statistics.median(pp[k]) / statistics.median(rr[k]) - 1))
        (GEN / "table_phase_c.tex").write_text(
            "\\begin{tabular}{llrrrrr}\n\\toprule\nCase & Arm & Input tokens & Uncached & Turns & Expand calls & Gates \\\\\n"
            "\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    (GEN / "numbers.tex").write_text(
        "% Generated by experiments/paper_numbers.py. Do not edit.\n" +
        "".join(f"\\newcommand{{\\{k}}}{{{v}}}\n" for k, v in sorted(macros.items())), encoding="utf-8")
    print(f"paper/generated: {len(macros)} macros, tables: "
          + ", ".join(sorted(p.name for p in GEN.glob("table_*.tex"))))


if __name__ == "__main__":
    main()
