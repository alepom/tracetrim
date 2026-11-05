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

from .paths import ROOT

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
    t = t.replace("\\%", "%").replace("~", " ").replace("--", "\u2013")
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
                            f"{x['gates_passed']}/{len(x['runs'])} & {'' if arm == 'raw' else ('meets' if x['holds'] else 'fails') + ('$^{\\ast}$' if x.get('outside_band_posthoc') else '')} & "
                            f"{'' if arm == 'raw' else ('meets' if x.get('holds_protocol_list') else 'fails')} \\\\")
            if r.get("headroom"):
                h = r["headroom"]
                m(f"{label}HeadroomTokens{R}", apct(h["headroom"] / h["tool_tokens"] - 1))
                rows.append(f" & Headroom (tokens only) & {pct(h['headroom'] / h['tool_tokens'] - 1)} & -- & -- & -- \\\\")
            rows.append("\\addlinespace")
        (GEN / f"table_{name.removesuffix('.json')}.tex").write_text(
            "\\begin{tabular}{llrrll}\n\\toprule\nCase & Arm & Tool tokens & Gates passed & H2 (implemented) & H2 (protocol, computable) \\\\\n\\midrule\n"
            + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    s2 = load("study2.json")
    if s2 and "C1" in s2["cases"]:
        c1 = s2["cases"]["C1"]
        lo, hi = c1["band"]["median_volume"]
        m("StudyTwoRawBandVolumei", f"{lo:g}--{hi:g}")
        if "mean_volume" in c1["band"]:
            lo_m, hi_m = c1["band"]["mean_volume"]
            m("StudyTwoRawBandMeanVolumei", f"{num(lo_m)}--{num(hi_m)}")
            m("StudyTwoVtwoMeanVolumei", num(c1["arms"]["v02"]["medians"]["mean_volume"]))
        for arm, key in (("v02", "Vtwo"), ("v02-structural", "Vtwostructural")):
            m(f"StudyTwo{key}MedianVolumei", f"{c1['arms'][arm]['medians']['median_volume']:g}")
        m("StudyTwoVtwoHoldsi", "holds" if c1["arms"]["v02"]["holds"] else "does not hold")
    pb = load("phase_b.json")
    if pb and "C1" in pb["cases"]:
        meds = {a: x["medians"]["median_volume"] for a, x in pb["cases"]["C1"]["arms"].items()}
        m("PhaseBPrudentMedianVolumei", f"{meds['prudent']:g}")
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
            total = sum(r["pairs"] for r in pr)
            m("RecheckPairs", str(pr[0]["pairs"]))
            m("RecheckPairsTotal", str(total))
            m("RecheckGateChecked", ", ".join(str(r["own"]) for r in pr))
            m("RecheckConfirmed", str(sum(r["own"] + r["other"] for r in pr)))
            mis = [x for r in pr for x in r["mismatches"]]
            m("RecheckMismatch", word(len(mis)))
            m("RecheckUnknownTotal", word(sum(r["unknown"] for r in pr)))
            if len(mis) == 1:
                m("RecheckMismatchWritten", f"{mis[0]['written']}")
                m("RecheckMismatchOther", ", ".join(str(v) for v in mis[0]["in_other_runs"]))
        pr_ = json.loads((ROOT / "experiments" / "prices.json").read_text())
        m("PriceInput", f"{pr_['input_per_mtok']:.2f}")
        m("PriceCached", f"{pr_['cached_input_per_mtok']:.2f}")
        m("PriceOutput", f"{pr_['output_per_mtok']:.2f}")
        (GEN / "table_phase_c.tex").write_text(
            "\\begin{tabular}{llrrrrr}\n\\toprule\nCase & Arm & Input tokens & Uncached & Turns & Expand calls & Gates \\\\\n"
            "\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    # ---- Supplementary material (appendix) -----------------------------------
    def rng(vals):
        vals = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not vals:
            return "--"
        return f"{statistics.median(vals):g} [{min(vals):g}--{max(vals):g}]"

    sup = []
    for name, label in (("phase_b.json", "B"), ("study2.json", "2"), ("phase_c.json", "C")):
        d = load(name)
        if not d:
            continue
        for c, r in d["cases"].items():
            arms = r["arms"] if "arms" in r else r
            for arm, x in arms.items():
                th = [t for t in x.get("thermometers") or [] if t]
                keys = [k for k in (th[0] if th else {}) if isinstance(th[0][k], (int, float)) and not isinstance(th[0][k], bool)]
                cells = "; ".join(f"{k.replace('_', ' ')} {rng([t.get(k) for t in th])}" for k in keys)
                sup.append(f"{label} & {CASE_NAME[c]} & {arm.replace('-', ' ')} & {len(th)} & {cells} \\\\")
    (GEN / "table_thermometers.tex").write_text(
        "\\begin{longtable}{lllrp{8.0cm}}\n\\toprule\nStudy & Case & Arm & Runs & Thermometers: median [min--max] \\\\\n\\midrule\n"
        "\\endhead\n" + "\n".join(sup) + "\n\\bottomrule\n\\end{longtable}\n", encoding="utf-8")

    ser, b, s2 = load("reserialize.json"), load("phase_b.json"), load("study2.json")
    if ser and b and s2:
        rows = []
        for c in ("C1", "C2", "C3", "C4", "C5"):
            R, sr = ROMAN[c], ser["cases"][c]
            raw = b["cases"][c]["arms"]["raw"]["tool_tokens"]
            v02 = s2["cases"][c]["arms"]["v02"]["tool_tokens"]
            m(f"ReserialMinA{R}", apct(-sr["phase_a"]["minified_saving"]))
            m(f"ReserialMinReplay{R}", apct(-sr["replay"]["minified_saving"]))
            m(f"VtwoNet{R}", apct(v02 / sr["replay"]["reserialized"] - 1))
            # Against JSON already minified (conservative: tracetrim's own output keeps the
            # default separators). Negative only if the projection beats the minifier.
            vs_min = v02 / sr["replay"]["minified"] - 1
            m(f"VtwoVsMin{R}", apct(vs_min) if vs_min <= 0 else "--")
            m(f"VtwoAboveMin{R}", "yes" if vs_min > 0 else "no")
            rows.append(f"{CASE_NAME[c]} & {pct(-sr['replay']['saving'])} & {pct(-sr['replay']['minified_saving'])} & "
                        f"{pct(b['cases'][c]['arms']['prudent']['tool_tokens'] / raw - 1)} & {pct(v02 / raw - 1)} & "
                        f"{pct(v02 / sr['replay']['reserialized'] - 1)} & {pct(v02 / sr['replay']['minified'] - 1)} & "
                        f"{pct(b['cases'][c]['headroom']['headroom'] / raw - 1)} \\\\")
        (GEN / "table_savings_detail.tex").write_text(
            "\\begin{tabular}{lrrrrrrr}\n\\toprule\nCase & Re-serialised & Minified & 0.1 prudent & 0.2 & 0.2 vs re-serialised & 0.2 vs minified & Headroom \\\\\n"
            "\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    ga = load("gates_audit.json")
    if ga:
        cs = ga["cases"]
        c1 = [r for g in cs["C1"].values() for r in g]
        c2 = [r for g in cs["C2"].values() for r in g]
        c3 = [r for g in cs["C3"].values() for r in g]
        m("AuditCOneSkipped", word(sum(r["skipped_by_gate"] for r in c1)))
        m("AuditCOnePairs", num(sum(r["pairs"] for r in c1)))
        m("AuditCTwoDiffers", str(sum(r["title_differs"] for r in c2)))
        m("AuditCTwoFound", num(sum(r["url_found"] for r in c2)))
        m("AuditCThreeDoi", word(sum(r["doi_differs"] for r in c3)))
        for arm in ("prudent", "aggressive", "structural"):
            rows3 = cs["C3"].get(f"B-{arm}", [])
            m(f"PhaseBOriginalGates{arm.capitalize()}iii", f"{sum(r['gate_original'] for r in rows3)}/{len(rows3)}")
            m(f"PhaseBNowGates{arm.capitalize()}iii", f"{sum(r['gate_now'] for r in rows3)}/{len(rows3)}")

    au = load("c4_audit.json")
    if au:
        rows = []
        for g, rr in au.items():
            key = "".join(w.capitalize() for w in g.replace("-", " ").replace("v02", "vtwo").split())
            m(f"AuditCFour{key}", f"{sum(r['audit'] for r in rr)}/{5 * len(rr)}")
            m(f"FrozenCFour{key}", f"{sum(r['frozen'] for r in rr)}/{5 * len(rr)}")
            rows.append(f"{g.replace('-', ' ')} & {len(rr)} & {sum(r['frozen'] for r in rr)}/{5 * len(rr)} & "
                        f"{sum(r['audit'] for r in rr)}/{5 * len(rr)} \\\\")
        (GEN / "table_c4_audit.tex").write_text(
            "\\begin{tabular}{lrrr}\n\\toprule\nRuns & n & Frozen test & Audit \\\\\n\\midrule\n" + "\n".join(rows)
            + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    ts = load("turns_sensitivity.json")
    if ts:
        m("TurnsCallsChanged", str(sum(v["calls_changed"] for v in ts.values())))
        m("TurnsCalls", str(sum(v["calls"] for v in ts.values())))
        m("TurnsFiguresUnchanged", "unchanged" if all(v["turn_aware"] == v["published"] for v in ts.values()) else "changed")

    (GEN / "abstract.txt").write_text(plain(ROOT / "paper" / "sections" / "abstract.tex", macros) + "\n",
                                      encoding="utf-8")
    (GEN / "numbers.tex").write_text(
        "% Generated by experiments/paper_numbers.py. Do not edit.\n" +
        "".join(f"\\newcommand{{\\{k}}}{{{v}}}\n" for k, v in sorted(macros.items())), encoding="utf-8")
    print(f"paper/generated: {len(macros)} macros, tables: "
          + ", ".join(sorted(p.name for p in GEN.glob("table_*.tex"))))


if __name__ == "__main__":
    main()
