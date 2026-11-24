#!/usr/bin/env python3
"""The numbers and tables of Study 4, Parts D and E (version 4 of the paper), from results/v3d and results/v3e.

  .venv/bin/python -m experiments.paper_numbers      # calls macros() below, writes paper/generated/*.tex

Part D (protocol-v3d.md) runs the hard tasks of Part B with another agent model, `gemini-3.8-flash`, in the arms R,
P1 and P2 of Parts B and C. Part E (protocol-v3e.md) tests the text previews of tracetrim 1.5 with the agent model of
Parts A-C. Neither has a judge: every measure is computed by scripts from the frozen keys. Every figure is a macro
defined here (prefixes `SfourD`, `SfourE`); costs reach the paper only as ratios.
"""
from __future__ import annotations

import json
from pathlib import Path

from tracetrim.engine import TEXT_LONG, TEXT_SHARE

from .paper_numbers_v3 import ci, ratio, sgn, word
from .paths import ROOT

V3D = ROOT / "results" / "v3d" / "analysis.json"
V3E = ROOT / "results" / "v3e" / "analysis.json"
V3E_REPLAY = ROOT / "results" / "v3e" / "replay.json"
V3E_HINTS = ROOT / "results" / "v3e" / "hints_offline.json"
V3E_ROUTES = ROOT / "results" / "v3e" / "routes.json"
PREVIEW = 40          # the characters of arm P2T (experiments/v3e/run.py), the default of 1.5 (runtime.TEXT_PREVIEW)


def share(x: float) -> str:
    """A share with one decimal, rounded half up as a reader would (0.9125 -> 91.3%, not the 91.2% of binary
    rounding); 1 is written 100%."""
    from decimal import ROUND_HALF_UP, Decimal
    return "100\\%" if x == 1 else f"{Decimal(repr(100 * x)).quantize(Decimal('0.1'), ROUND_HALF_UP)}\\%"


def one(x: float) -> str:
    return f"{x:.1f}"


def macros(m, GEN: Path) -> None:
    _part_d(m, GEN)
    _part_e(m, GEN)


def _part_d(m, GEN: Path) -> None:
    if not V3D.exists():
        return
    d = json.loads(V3D.read_text(encoding="utf-8"))
    arms = ("R", "P1", "P2")
    m("SfourDModel", d["model"])
    m("SfourDRuns", str(d["runs"]))
    m("SfourDMissing", word(sum(d["missing"].values())))
    m("SfourDTasks", word(len(d["tasks"])))
    p = d["process"]
    m("SfourDRunsPerArm", str(p["P2"]["runs"]))
    for a, tag in zip(arms, ("R", "Pone", "Ptwo")):
        m(f"SfourDHidden{tag}", share(d["hidden_fact_share_by_arm"][a]))
        m(f"SfourDFacts{tag}", share(d["fact_share_by_arm"][a]))
        m(f"SfourDGates{tag}", f"{d['gates_passed_by_arm'][a]}/{p[a]['runs']}")
        m(f"SfourDCalls{tag}", one(p[a]["calls"]))
        m(f"SfourDTurns{tag}", one(p[a]["turns"]))
        m(f"SfourDExpand{tag}", str(p[a]["expand_calls"]))
        m(f"SfourDExpandRuns{tag}", str(p[a]["runs_with_expand"]))
        m(f"SfourDCost{tag}", ratio(p[a]["cost_per_run_over_R"]))
    for key, name in (("primary_hidden_fact_share_P2_minus_P1", "PtwoMinusPone"), ("P1_minus_R", "PoneMinusR"),
                      ("P2_minus_R", "PtwoMinusR")):
        m(f"SfourD{name}", sgn(d[key]["point"], 3))
        m(f"SfourD{name}CI", ci(d[key]["ci95"], 3))
        m(f"SfourD{name}Verdict", d[key]["verdict"])
    rows = [("Hidden facts reported", "hidden_fact_share_by_arm", share),
            ("All key facts reported", "fact_share_by_arm", share)]
    lines = [f"{label} & " + " & ".join(f(d[k][a]) for a in arms) + " \\\\" for label, k, f in rows]
    lines += [f"Gates passed & " + " & ".join(f"{d['gates_passed_by_arm'][a]}/{p[a]['runs']}" for a in arms) + " \\\\",
              "Tool calls per run & " + " & ".join(one(p[a]["calls"]) for a in arms) + " \\\\",
              "Turns per run & " + " & ".join(one(p[a]["turns"]) for a in arms) + " \\\\",
              "\\texttt{expand} calls (runs with one) & " + " & ".join(
                  f"{p[a]['expand_calls']} ({p[a]['runs_with_expand']})" for a in arms) + " \\\\",
              "Cost per run relative to R & " + " & ".join(ratio(p[a]["cost_per_run_over_R"]) for a in arms) + " \\\\"]
    (GEN / "table_s4d.tex").write_text(
        "\\begin{tabular}{lrrr}\n\\toprule\nOutcome & R & P1 (text v1) & P2 (text v2) \\\\\n\\midrule\n"
        + "\n".join(lines) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")


def _part_e(m, GEN: Path) -> None:
    if not V3E.exists():
        return
    e = json.loads(V3E.read_text(encoding="utf-8"))
    pe, fe = e["primary_hidden_fact_share_P2T_minus_P2"], e["secondary_fact_share_P2T_minus_P2"]
    m("SfourEPreview", str(PREVIEW))
    m("SfourERuns", str(e["runs"]))
    m("SfourEMissing", word(sum(e["missing"].values())))
    m("SfourEHardTasks", word(len(e["tasks"]["hard"])))
    m("SfourEOrdinaryTasks", word(len(e["tasks"]["ordinary"])))
    m("SfourEHiddenPtwo", share(e["hidden_fact_share_by_arm"]["P2"]))
    m("SfourEHiddenPtwoT", share(e["hidden_fact_share_by_arm"]["P2T"]))
    m("SfourEPoint", sgn(pe["point"], 3))
    m("SfourECI", ci(pe["ci95"], 3))
    m("SfourEVerdict", pe["verdict"])
    m("SfourEFactsPoint", sgn(fe["point"], 3))
    m("SfourEFactsCI", ci(fe["ci95"], 3))
    per = pe["per_task"].values()
    m("SfourEBetterTasks", word(sum(v > 0 for v in per)))
    worse = sum(v < 0 for v in per)
    m("SfourEWorseTasks", "none" if worse == 0 else f"{word(worse)} task{'s' if worse > 1 else ''}")
    hf = e["hidden_fields"]
    names = {"balance: data[].description": "Origin", "memo: data[].description": "Memo",
             "partial_refund: data[].refunds.data[].description": "Refund",
             "credit_note: data[].post_payment_credit_notes_amount": "CreditNote"}
    for key, name in names.items():
        for arm, tag in (("P2", "Ptwo"), ("P2T", "PtwoT")):
            m(f"SfourE{name}{tag}", f"{hf[key][arm]['reported']}/{hf[key][arm]['facts']}")
        m(f"SfourE{name}OnPreview", f"{hf[key]['P2T']['on_preview']}/{hf[key]['P2T']['facts']}")
    m("SfourERefundReachedPtwo", str(hf['partial_refund: data[].refunds.data[].description']['P2']['reached']))
    m("SfourEBalanceAlways", f"{hf['balance: data[].balance']['P2']['reported']}/{hf['balance: data[].balance']['P2']['facts']}")
    m("SfourEReplacementAlways",
      f"{hf['replacement: data[].from_invoice.invoice']['P2']['reported']}/{hf['replacement: data[].from_invoice.invoice']['P2']['facts']}")
    m("SfourERefundReachedPtwoT", f"{hf['partial_refund: data[].refunds.data[].description']['P2T']['reached']}")
    m("SfourEFromPreviewOnly", word(sum(v["P2T"]["reported_from_preview_only"] for v in hf.values())))
    if V3E_ROUTES.exists():
        r = json.loads(V3E_ROUTES.read_text(encoding="utf-8"))["hidden_fields"]
        for key, name in (("balance: data[].description", "Origin"),
                          ("partial_refund: data[].refunds.data[].description", "Refund")):
            for arm, tag in (("P2", "Ptwo"), ("P2T", "PtwoT")):
                via = r[key][arm]["via"]
                m(f"SfourE{name}Expand{tag}", str(via.get("expand", 0)))
                m(f"SfourE{name}Detail{tag}", str(via.get("detail", 0)))
            m(f"SfourE{name}Facts", str(r[key]["P2"]["facts"]))
    hp, op = e["hard_process"], e["ordinary_process"]
    m("SfourERunsPerArm", str(hp["P2"]["runs"]))
    for arm, tag in (("P2", "Ptwo"), ("P2T", "PtwoT")):
        m(f"SfourEExpand{tag}", str(hp[arm]["expand_calls"]))
        m(f"SfourEExpandRuns{tag}", str(hp[arm]["runs_with_expand"]))
        m(f"SfourECalls{tag}", one(hp[arm]["calls"]))
        m(f"SfourETurns{tag}", one(hp[arm]["turns"]))
    m("SfourEHardCost", ratio(e["hard_cost_P2T_over_P2"]))
    m("SfourEOrdinaryCost", ratio(e["ordinary_cost_P2T_over_P2"]))
    g = e["ordinary_gates"]
    m("SfourEOrdinaryGates", f"{g['P2']}/{op['P2']['runs']} and {g['P2T']}/{op['P2T']['runs']}")
    m("SfourEUnsupported", word(sum(e["ordinary_unsupported"].values())))
    m("SfourEDefault", "on" if e["decision"]["recommend_default_on"] else "off")
    if V3E_HINTS.exists():
        h = json.loads(V3E_HINTS.read_text(encoding="utf-8"))
        m("SfourEHintTraces", str(h["traces"]))
        m("SfourEHintMissed", str(h["missed_hidden_facts"]))
        m("SfourEHintFlagged", str(h["flagged"][f"preview-{PREVIEW}"]))
        m("SfourEHintShare", f"{100 * h['hint_share_of_sent'][f'preview-{PREVIEW}']:.1f}\\%")
    if V3E_REPLAY.exists():
        r = json.loads(V3E_REPLAY.read_text(encoding="utf-8"))
        for arm, tag in (("P2", "Ptwo"), ("P2T", "PtwoT")):
            m(f"SfourEReplay{tag}", f"{r['arms'][arm]['identical']}/{r['arms'][arm]['results']}")
        s = r["share_of_saving_used_by_previews"]
        m("SfourEReplayPreviews", str(s["results"]))
        m("SfourEShareMedian", f"{s['median']:.2f}")
        m("SfourEShareMax", f"{s['max']:.2f}")
        assert r["text_share_bound"] == TEXT_SHARE
    m("SfourEShareBound", f"{TEXT_SHARE:.2f}")
    m("SfourETextLong", str(TEXT_LONG))
    rows = [f"Hidden facts reported (primary) & {share(e['hidden_fact_share_by_arm']['P2'])} & "
            f"{share(e['hidden_fact_share_by_arm']['P2T'])} & \\textbf{{{sgn(pe['point'], 3)}}} & "
            f"\\textbf{{{ci(pe['ci95'], 3)}}} \\\\"]
    for key, label in (("balance: data[].description", "amount of the last credit"),
                       ("memo: data[].description", "TB in the invoice memo"),
                       ("partial_refund: data[].refunds.data[].description", "licences in a partial refund"),
                       ("credit_note: data[].post_payment_credit_notes_amount", "credit notes")):
        rows.append(f"\\quad {label} & {hf[key]['P2']['reported']}/{hf[key]['P2']['facts']} & "
                    f"{hf[key]['P2T']['reported']}/{hf[key]['P2T']['facts']} & & \\\\")
    rows += [f"All key facts reported & {share(e['fact_share_by_arm']['P2'])} & {share(e['fact_share_by_arm']['P2T'])} & "
             f"{sgn(fe['point'], 3)} & {ci(fe['ci95'], 3)} \\\\",
             f"Cost per run, hard tasks (P2T/P2) & & & {ratio(e['hard_cost_P2T_over_P2'])} & \\\\",
             f"Cost per run, ordinary tasks (P2T/P2) & & & {ratio(e['ordinary_cost_P2T_over_P2'])} & \\\\",
             f"Gates passed, ordinary tasks & {g['P2']}/{op['P2']['runs']} & {g['P2T']}/{op['P2T']['runs']} & & \\\\"]
    (GEN / "table_s4e.tex").write_text(
        "\\begin{tabular}{lrrrr}\n\\toprule\nOutcome & P2 & P2T (previews) & P2T $-$ P2 & 95\\% interval \\\\\n"
        "\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")
