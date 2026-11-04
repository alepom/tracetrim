#!/usr/bin/env python3
"""Every number and table of the paper, generated from results/*.json.

  .venv/bin/python -m experiments.paper_numbers      # writes paper/generated/*.tex

The paper never types a number: it uses the macros written here (numbers.tex)
and inputs the tables. Rerun after any analysis. The charts come from
paper_figures.py, on the same files.
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
    """Prose form: the verb carries the direction ("removed 63%", "fell by 56%").

    For a change (signed=None) only a decrease can be printed this way: an
    increase would read as a reduction, so it stops the generation instead.
    Shares and rates pass signed=False."""
    if signed is None and x > 0:
        raise ValueError(f"an increase ({x:+.3f}) cannot be printed as a reduction")
    return f"{abs(round(100 * x))}\\%"


def plain(tex_path, macros) -> str:
    """The abstract as plain text, numbers filled in: for the Zenodo description."""
    import re
    t = tex_path.read_text(encoding="utf-8")
    for k in sorted(macros, key=len, reverse=True):
        v = macros[k].replace("\\%", "%").replace("$-$", "-").replace("{,}", ",")
        t = t.replace("\\" + k + "{}", v).replace("\\" + k, v)
    t = re.sub(r"\\(?:texttt|emph|textbf)\{([^}]*)\}", r"\1", t)
    t = t.replace("\\%", "%").replace("~", " ")
    assert "\\" not in t, f"unexpanded LaTeX left in the abstract: {t[t.index(chr(92)):][:40]}"
    return " ".join(t.split())


WORDS = {0: "no", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def word(n: int) -> str:
    """Small counts in prose are written as words."""
    return WORDS.get(n, str(n))


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
        f"\\newcommand{{\\PaperVersionLine}}{{Preprint, {about.NAME} {about.VERSION} (studies run with versions 0.1 and 0.2)}}\n"
        f"\\newcommand{{\\PaperStatus}}{{Preprint, not peer reviewed."
        + (f" DOI \\href{{https://doi.org/{about.PAPER_DOI}}}{{{about.PAPER_DOI}}}." if about.PAPER_DOI else "") + "}\n"
        f"\\newcommand{{\\SoftwareDOI}}{{\\href{{https://doi.org/{about.SOFTWARE_DOI}}}{{{about.SOFTWARE_DOI}}}}}\n"
        f"\\newcommand{{\\RepositoryURL}}{{\\url{{{about.REPOSITORY}}}}}\n", encoding="utf-8")

    nf = load("number_formats.json")
    if nf:
        m("CommaNumbersMatched", str(sum(x["matched"] for x in nf.values())))
        m("CommaNumbersWritten", str(sum(x["written"] for x in nf.values())))
    h = load("headroom_baseline.json")
    if h:
        m("HeadroomVersion", h["headroom_version"])     # the version that produced the numbers

    # ---- Phase A (Study 1) ------------------------------------------------
    a = load("phase_a.json")
    ser = load("reserialize.json")
    rows = []
    if a:
        # Real-API and simulated cases are never pooled together (protocol, A1).
        pooled = {g: {"full": [0, 0], "calls": [0, 0]} for g in ("Real", "Sim")}
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
            sens = r.get("sensitivity_no_failures")
            if sens:
                m(f"PhaseASensExcluded{R}", word(len(sens["excluded"])))
                m(f"PhaseASensSaving{R}", apct(-sens["loo_full"]["prudent_saving"]))
                m(f"PhaseASensMisses{R}", f"{sens['loo_full']['misses']}/{sens['loo_full']['trimmed_calls']}")
            group = "Sim" if c in ("C4", "C5") else "Real"
            for k, x in (("full", full), ("calls", calls)):
                pooled[group][k][0] += x["misses"]
                pooled[group][k][1] += x["trimmed_calls"]
            # H3, as frozen: calls-only misses more than three times the full-trace misses.
            ratio = calls["misses"] / full["misses"] if full["misses"] else float("inf")
            m(f"HThreeRatio{R}", f"{ratio:.1f}")
            m(f"HThreeHolds{R}", "holds" if ratio > 3 else "fails")
            rs = ser["cases"][c]["phase_a"]["saving"] if ser else None
            m(f"ReserialA{R}", apct(-rs) if rs is not None else "--")
            if ser:
                m(f"ReserialReplay{R}", apct(-ser["cases"][c]["replay"]["saving"]))
            g = r["gates"]
            fo, ft = sum(x["facts_ok"] for x in g), sum(x["facts"] for x in g)
            m(f"PhaseAFacts{R}", apct(fo / ft, False) if ft else "--")
            rows.append(f"{CASE_NAME[c]} & {len(r['runs'])} & {num(r['calls'])} & {num(full['raw'])} & "
                        f"{pct(rq1['share'], False)} & {pct(-rs) if rs is not None else '--'} & "
                        f"{pct(-full['prudent_saving'])} & {pct(-full['aggressive_saving'])} & "
                        f"{full['misses']}/{full['trimmed_calls']} & {calls['misses']}/{calls['trimmed_calls']} \\\\")
        for g, kinds in pooled.items():
            for k, (mi, tr) in kinds.items():
                m(f"MissesPooled{g}{k.capitalize()}", f"{mi}/{tr}")
                m(f"MissesPooledShare{g}{k.capitalize()}", apct(mi / tr, False))
        t = a.get("rq5")
        if t:
            m("TransferMisses", f"{t['transfer']['misses']}/{t['transfer']['calls']}")
            m("WithinMisses", f"{t['within']['misses']}/{t['within']['calls']}")
        (GEN / "table_phase_a.tex").write_text(
            "\\begin{tabular}{lrrrrrrrrr}\n\\toprule\n"
            "Case & Runs & Calls & Tool-output tokens & Used & Re-serialised & Prudent & Aggressive & Misses & Misses (calls only) \\\\\n"
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
                            f"{x['gates_passed']}/{len(x['runs'])} & {'' if arm == 'raw' else ('holds' if x['holds'] else 'does not hold') + ('$^{\\ast}$' if x.get('outside_band_posthoc') else '')} \\\\")
            if r.get("headroom"):
                h = r["headroom"]
                m(f"{label}HeadroomTokens{R}", apct(h["headroom"] / h["tool_tokens"] - 1))
                rows.append(f" & Headroom (tokens only) & {pct(h['headroom'] / h['tool_tokens'] - 1)} & -- & -- \\\\")
            rows.append("\\addlinespace")
        (GEN / f"table_{name.removesuffix('.json')}.tex").write_text(
            "\\begin{tabular}{llrrl}\n\\toprule\nCase & Arm & Tool tokens & Gates passed & Band rule \\\\\n\\midrule\n"
            + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    s2 = load("study2.json")
    if s2 and "C1" in s2["cases"]:
        c1 = s2["cases"]["C1"]
        lo, hi = c1["band"]["median_volume"]
        m("StudyTwoRawBandVolumei", f"{lo:g}--{hi:g}")
        m("StudyTwoVtwoMedianVolumei", f"{c1['arms']['v02']['medians']['median_volume']:g}")
        m("StudyTwoVtwoHoldsi", "holds" if c1["arms"]["v02"]["holds"] else "does not hold")
    pb = load("phase_b.json")
    if pb and "C1" in pb["cases"]:
        meds = {a: x["medians"]["median_volume"] for a, x in pb["cases"]["C1"]["arms"].items()}
        m("PhaseBAggressiveMedianVolumei", f"{meds['aggressive']:g}")
        m("PhaseBStructuralMedianVolumei", f"{meds['structural']:g}")
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
                case = c
                for k, label in (("billed_input", "Input"), ("uncached_input", "Uncached"), ("cost_usd", "Cost")):
                    m(f"PhaseCChange{label}{R}", apct(statistics.median(pp[k]) / statistics.median(rr[k]) - 1))
                if case == "C4":
                    # Tickets diagnosed correctly (frozen thermometer), worst run of each arm.
                    for arm_name, x in (("Raw", rr), ("Prudent", pp)):
                        m(f"PhaseCMinRight{arm_name}{R}", str(min(t["correct_diagnoses"] for t in x["thermometers"])))
                # Sensitivity (deviation 6): without the runs in which a tool call failed.
                ok = lambda x, k: [v for v, f in zip(x[k], x.get("tool_failures") or [0] * len(x[k])) if not f]
                dropped = sum(bool(f) for x in (rr, pp) for f in x.get("tool_failures") or [])
                m(f"PhaseCFailedRuns{R}", word(dropped))
                m(f"PhaseCChangeInputNoFail{R}",
                  apct(statistics.median(ok(pp, "billed_input")) / statistics.median(ok(rr, "billed_input")) - 1))
        rc = load("phase_c_recheck.json")
        if rc:
            pr = [rc["runs"][f"C-prudent-{i}"] for i in (1, 2, 3)]
            words = {1: "one", 2: "two", 3: "three"}
            m("RecheckPairs", str(pr[0]["pairs"]))
            m("RecheckGateChecked", ", ".join(str(r["own"]) for r in pr))
            m("RecheckVerified", ", ".join(str(r["own"] + r["other"]) for r in pr))
            m("RecheckUnknown", ", ".join(str(r["unknown"]) for r in pr))
            m("RecheckUnknownTotal", str(sum(r["unknown"] for r in pr)))
            in_text = sum(r["unverified_in_own_text"] for r in pr)
            m("RecheckUnknownInText", "all" if in_text == sum(r["unknown"] for r in pr) else str(in_text))
            m("RecheckUnknownInExpand", str(sum(r["unverified_volume_in_expand"] for r in pr)))
            n_mis = sum(r["mismatch"] for r in pr)
            m("RecheckMismatch", words.get(n_mis, str(n_mis)) if n_mis else "no")
        pr_ = json.loads((ROOT / "experiments" / "prices.json").read_text())
        m("PriceInput", f"{pr_['input_per_mtok']:.2f}")
        m("PriceCached", f"{pr_['cached_input_per_mtok']:.2f}")
        m("PriceOutput", f"{pr_['output_per_mtok']:.2f}")
        (GEN / "table_phase_c.tex").write_text(
            "\\begin{tabular}{llrrrrr}\n\\toprule\nCase & Arm & Input tokens & Uncached & Turns & Expand calls & Gates \\\\\n"
            "\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    (GEN / "abstract.txt").write_text(plain(ROOT / "paper" / "sections" / "abstract.tex", macros) + "\n",
                                      encoding="utf-8")
    (GEN / "numbers.tex").write_text(
        "% Generated by experiments/paper_numbers.py. Do not edit.\n" +
        "".join(f"\\newcommand{{\\{k}}}{{{v}}}\n" for k, v in sorted(macros.items())), encoding="utf-8")
    print(f"paper/generated: {len(macros)} macros, tables: "
          + ", ".join(sorted(p.name for p in GEN.glob("table_*.tex"))))


if __name__ == "__main__":
    main()
