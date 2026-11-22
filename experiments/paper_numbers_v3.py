#!/usr/bin/env python3
"""The numbers and tables of Study 4 (version 3 of the paper), from results/v3 and results/v3b.

  .venv/bin/python -m experiments.paper_numbers      # calls macros() below, writes paper/generated/*.tex

Study 4 measures answer quality on held-out tasks, in two parts: 4A (protocol-v3.md), new
ordinary billing and CI tasks; 4B (protocol-v3b.md, version 2), billing tasks built so that the
facts the answer needs sit in fields the prudent projection removes from list calls. Every figure
in the text is a macro defined here (prefix `Sfour`), computed from results/v3/quality.json and
results/v3b/quality.json (written by experiments/quality_export.py). Costs reach the paper only as
ratios to the raw arm R.
"""
from __future__ import annotations

import json
from pathlib import Path

from .paths import ROOT

V3 = ROOT / "results" / "v3" / "quality.json"
V3B = ROOT / "results" / "v3b" / "quality.json"
TEN = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")
DIM = {"correctness": "Correctness", "completeness": "Completeness", "action": "Action"}


def word(n: int) -> str:
    return TEN[n] if 0 <= n < len(TEN) else str(n)


def sgn(x: float, nd: int = 2) -> str:
    """A signed difference, with a typographic minus; zero has no sign."""
    v = round(x, nd)
    if v == 0:
        return f"{0:.{nd}f}"
    return (f"$-${abs(v):.{nd}f}" if v < 0 else f"+{v:.{nd}f}")


def ci(c, nd: int = 2) -> str:
    return f"[{sgn(c[0], nd)}; {sgn(c[1], nd)}]"


def share(x: float, nd: int = 1) -> str:
    return "100\\%" if x == 1 else f"{100 * x:.{nd}f}\\%"


def ratio(x: float) -> str:
    return f"{x:.2f}"


def macros(m, GEN: Path) -> None:
    if not V3.exists() or not V3B.exists():
        return
    a = json.loads(V3.read_text(encoding="utf-8"))
    b = json.loads(V3B.read_text(encoding="utf-8"))

    # ---- common -------------------------------------------------------------
    (model,) = a["agent_models_returned"]
    assert b["agent_models_returned"] == [model]
    m("SfourModel", model)
    m("SfourJudge", a["judge"]["model"])
    m("SfourJudgeThinking", a["judge"]["thinking_level"])

    # ---- 4A: protocol v3 ----------------------------------------------------
    h, i = a["H1_P_minus_R"], a["secondary_I_minus_R"]
    m("SfourARuns", str(a["design"]["runs"]))
    m("SfourATasks", str(sum(h["tasks"].values())))
    m("SfourATasksPerCase", word(a["design"]["tasks_per_case"]))
    m("SfourAReps", word(a["design"]["repetitions"]))
    m("SfourAMissing", word(sum(a["missing"].values())))
    m("SfourAPoint", sgn(h["point"]["combined"]))
    m("SfourACI", ci(h["ci95"]["combined"]))
    m("SfourAVerdict", h["verdict_0.5"])
    m("SfourAVerdictQuarter", h["verdict_0.25"])
    for c, roman in (("C4", "iv"), ("C5", "v")):
        m(f"SfourAPoint{roman}", sgn(h["point"]["by_case"][c]))
        m(f"SfourACI{roman}", ci(h["ci95"]["by_case"][c]))
        m(f"SfourIPoint{roman}", sgn(i["point"]["by_case"][c]))
        m(f"SfourICI{roman}", ci(i["ci95"]["by_case"][c]))
        p = a["process"][c]
        m(f"SfourACostP{roman}", ratio(p["P"]["cost_relative_to_R"]))
        m(f"SfourACostI{roman}", ratio(p["I"]["cost_relative_to_R"]))
        m(f"SfourATokensP{roman}", share(1 - p["P"]["outputs_delivered_tokens"] / p["R"]["outputs_delivered_tokens"], 0))
        ex = a["exposure_replay"][c]
        m(f"SfourAReplayP{roman}", share(1 - ex["P"]["tokens"] / ex["R"]["tokens"], 0))
        m(f"SfourAExpandRuns{roman}", str(p["P"]["runs_with_expand"] + p["I"]["runs_with_expand"]))
        m(f"SfourAComposite{roman}", f"{a['dimensions'][c]['R']['composite']:.2f}")
    m("SfourIPoint", sgn(i["point"]["combined"]))
    m("SfourICI", ci(i["ci95"]["combined"]))
    m("SfourIVerdict", i["verdict_0.5"])
    r = a["ratings_both_passes"]
    total = sum(r.values())
    m("SfourARatings", str(total))
    m("SfourARatingsFour", str(r["4"]))
    m("SfourACeiling", share(r["4"] / total))
    allfour = [c for c in ("C4", "C5") if all(a["dimensions"][c][arm]["composite"] == 4 for arm in "RPI")]
    m("SfourACeilingCase", "CI triage" if allfour == ["C5"] else ", ".join(allfour) or "none")
    ag = a["agreement"]
    m("SfourAExactMin", share(min(v["exact"] for v in ag.values())))
    m("SfourAExactMax", share(max(v["exact"] for v in ag.values())))
    m("SfourAKappaMin", f"{min(v['quadratic_kappa'] for v in ag.values()):.2f}")
    m("SfourAKappaMax", f"{max(v['quadratic_kappa'] for v in ag.values()):.2f}")
    m("SfourAAdjudicated", word(a["adjudicated"]))
    m("SfourACues", word(a["answers_with_cues"]))
    gates = sum(v["pass"] for c in a["gates"]["by_arm"].values() for v in c.values())
    answers = sum(v["answers"] for c in a["gates"]["by_arm"].values() for v in c.values())
    m("SfourAGates", f"{gates}/{answers}")
    m("SfourAUnsupported", word(sum(v["unsupported"] for c in a["grounding"]["by_arm"].values() for v in c.values())))
    m("SfourAFullCheaper", word(sum(a["full_cheaper_calls"][c]["P"] for c in ("C4", "C5"))))

    rows = []
    for c, name in (("C4", "Billing"), ("C5", "CI triage")):
        rows.append(f"{name} & {sgn(h['point']['by_case'][c])} & {ci(h['ci95']['by_case'][c])} & "
                    f"{sgn(i['point']['by_case'][c])} & {ci(i['ci95']['by_case'][c])} \\\\")
    rows.append(f"\\textbf{{Both, cases equal}} & \\textbf{{{sgn(h['point']['combined'])}}} & "
                f"\\textbf{{{ci(h['ci95']['combined'])}}} & {sgn(i['point']['combined'])} & {ci(i['ci95']['combined'])} \\\\")
    (GEN / "table_s4a.tex").write_text(
        "\\begin{tabular}{lrrrr}\n\\toprule\n & \\multicolumn{2}{c}{P $-$ R (primary)} & "
        "\\multicolumn{2}{c}{I $-$ R (secondary)} \\\\\n\\cmidrule(lr){2-3}\\cmidrule(lr){4-5}\n"
        "Case & Estimate & 95\\% interval & Estimate & 95\\% interval \\\\\n\\midrule\n" + "\n".join(rows)
        + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    # ---- 4B: protocol v3b, version 2 ----------------------------------------
    cal = b["calibration"]
    v1 = list(cal["R_fact_share_version_1_scorer"].values())
    v2 = list(cal["R_fact_share_version_2_scorer"].values())
    m("SfourBCandidates", str(cal["candidates"]))
    m("SfourBCalMin", f"{min(v1):.2f}")
    m("SfourBCalMax", f"{max(v1):.2f}")
    m("SfourBCalEligible", word(cal["eligible_version_1"]))
    m("SfourBCalPerfect", word(sum(s == 1 for s in v2)))
    m("SfourBCalHidden", f"{sum(1 for s in cal['R_hidden_share'].values() if s == 1)}")
    cc = b["construction_check"]
    m("SfourBVerified", f"{cc['verified']} of {cc['hidden_facts']}")
    d = b["design"]
    m("SfourBTasks", word(len(d["tasks"])))
    m("SfourBReps", word(d["repetitions"]))
    m("SfourBRuns", str(d["runs"]))
    m("SfourBHiddenFacts", str(sum(d["hidden_facts_per_task"].values())))
    m("SfourBHiddenMin", word(min(d["hidden_facts_per_task"].values())))
    m("SfourBHiddenMax", word(max(d["hidden_facts_per_task"].values())))
    m("SfourBMissing", word(sum(b["missing"].values())))
    p, f, j = (b["primary_hidden_fact_share_P_minus_R"], b["secondary_fact_share_P_minus_R"],
               b["secondary_composite_P_minus_R"])
    m("SfourBPoint", sgn(p["point"], 3))
    m("SfourBCI", ci(p["ci95"], 3))
    m("SfourBVerdict", p["verdict_0.1"])
    m("SfourBMarginGap", f"{p['ci95'][1] - (-0.10):.3f}")
    m("SfourBFactsPoint", sgn(f["point"], 3))
    m("SfourBFactsCI", ci(f["ci95"], 3))
    m("SfourBCompositePoint", sgn(j["point"]))
    m("SfourBCompositeCI", ci(j["ci95"]))
    m("SfourBCompositeVerdict", j["verdict_0.5"])
    lv = b["levels"]
    m("SfourBHiddenR", share(lv["hidden_fact_share"]["R"]))
    m("SfourBHiddenP", share(lv["hidden_fact_share"]["P"], 2))
    m("SfourBFactsR", share(lv["fact_share"]["R"]))
    m("SfourBFactsP", share(lv["fact_share"]["P"]))
    m("SfourBCompositeR", f"{lv['composite']['R']:.2f}")
    m("SfourBCompositeP", f"{lv['composite']['P']:.2f}")
    m("SfourBTasksAbove", word(sum(v > 0 for v in p["per_task"].values())))
    m("SfourBTasksZero", word(sum(v == 0 for v in p["per_task"].values())))
    pr = b["process"]
    m("SfourBCost", ratio(pr["P"]["cost_relative_to_R"]))
    m("SfourBTokens", share(1 - pr["P"]["outputs_delivered_tokens"] / pr["R"]["outputs_delivered_tokens"], 0))
    m("SfourBCallsR", f"{pr['R']['calls']:.1f}")
    m("SfourBCallsP", f"{pr['P']['calls']:.1f}")
    m("SfourBTurnsR", f"{pr['R']['turns']:.2f}")
    m("SfourBTurnsP", f"{pr['P']['turns']:.2f}")
    m("SfourBExpand", str(pr["P"]["expand_calls"]))
    m("SfourBExpandRuns", str(pr["P"]["runs_with_expand"]))
    m("SfourBRunsPerArm", str(pr["P"]["runs"]))
    rr = b["ratings_both_passes"]
    m("SfourBRatings", str(sum(rr.values())))
    m("SfourBRatingsFour", share(rr["4"] / sum(rr.values())))
    ag = b["agreement"]
    m("SfourBExactMin", share(min(v["exact"] for v in ag.values())))
    m("SfourBExactMax", share(max(v["exact"] for v in ag.values())))
    m("SfourBKappaMin", f"{min(v['quadratic_kappa'] for v in ag.values()):.2f}")
    m("SfourBAdjudicated", word(b["adjudicated"]))
    m("SfourBCues", word(b["answers_with_cues"]))
    g = b["gates"]["by_arm"]
    m("SfourBGates", f"{g['R']}/{pr['R']['runs']} and {g['P']}/{pr['P']['runs']}")
    gr = b["grounding"]
    m("SfourBUnsupportedR", str(gr["R"]["unsupported"]))
    m("SfourBUnsupportedP", str(gr["P"]["unsupported"]))
    hf = b["hidden_fields"]
    desc = hf["balance: data[].description"]
    memo = hf["memo: data[].description"]
    refund = hf["partial_refund: data[].refunds.data[].description"]
    m("SfourBOriginP", f"{desc['P'][0]}/{desc['P'][1]}")
    m("SfourBOriginReached", word(desc["P_reached"]))
    m("SfourBMemoP", f"{memo['P'][0]}/{memo['P'][1]}")
    m("SfourBMemoMissed", word(memo["P"][1] - memo["P"][0]))
    m("SfourBRefundP", f"{refund['P'][0]}/{refund['P'][1]}")
    m("SfourBRefundReached", word(refund["P_reached"]))

    label = {"balance: data[].balance": ("Current credit balance", "customer \\texttt{balance}"),
             "balance: data[].description": ("Amount of the last credit", "customer \\texttt{description}"),
             "credit_note: data[].post_payment_credit_notes_amount": ("Credit note after payment",
                                                                      "invoice \\texttt{post\\_payment\\_credit\\allowbreak{}\\_notes\\_amount}"),
             "memo: data[].description": ("TB in the invoice memo", "invoice \\texttt{description}"),
             "partial_refund: data[].refunds.data[].description": ("Licences refunded", "refund \\texttt{description}"),
             "replacement: data[].from_invoice.invoice": ("Invoice replaced", "invoice \\texttt{from\\_invoice}")}
    rows = []
    for key, (what, field) in label.items():
        v = hf[key]
        via = "" if v["P_via"] is None else ", ".join(f"{k} {n}" for k, n in sorted(v["P_via"].items()))
        reached = "n/a$^{\\ddagger}$" if v["P_reached"] is None else f"{v['P_reached']}/{v['P'][1]}" + (f" ({via})" if via else "")
        rows.append(f"{what} & {field} & {v['R'][0]}/{v['R'][1]} & {v['P'][0]}/{v['P'][1]} & {reached} \\\\")
    (GEN / "table_s4b_hidden.tex").write_text(
        "\\begin{tabular}{>{\\raggedright\\arraybackslash}p{0.2\\textwidth}>{\\raggedright\\arraybackslash}p{0.27\\textwidth}"
        "rr>{\\raggedright\\arraybackslash}p{0.25\\textwidth}}\n\\toprule\nHidden fact & Field removed from the list call & R & P & "
        "Value reached the model in P \\\\\n\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n",
        encoding="utf-8")

    rows = [f"Hidden facts reported (primary) & {share(lv['hidden_fact_share']['R'])} & "
            f"{share(lv['hidden_fact_share']['P'], 2)} & \\textbf{{{sgn(p['point'], 3)}}} & \\textbf{{{ci(p['ci95'], 3)}}} \\\\",
            f"All key facts reported & {share(lv['fact_share']['R'])} & {share(lv['fact_share']['P'])} & "
            f"{sgn(f['point'], 3)} & {ci(f['ci95'], 3)} \\\\",
            f"Judged composite (0--4) & {lv['composite']['R']:.2f} & {lv['composite']['P']:.2f} & "
            f"{sgn(j['point'])} & {ci(j['ci95'])} \\\\"]
    rows += [f"\\quad {DIM[k]} & {lv['dimensions']['R'][k]:.2f} & {lv['dimensions']['P'][k]:.2f} & & \\\\"
             for k in ("correctness", "completeness", "action")]
    (GEN / "table_s4b.tex").write_text(
        "\\begin{tabular}{lrrrr}\n\\toprule\nOutcome & R & P & P $-$ R & 95\\% interval \\\\\n\\midrule\n"
        + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    # ---- 4B, a second blind reader (post hoc): results/v3b/second_reader.json -----------
    sr_path = ROOT / "results" / "v3b" / "second_reader.json"
    if sr_path.exists():
        sr = json.loads(sr_path.read_text(encoding="utf-8"))
        ag = sr["agreement_with_judge"]
        m("SfourBSecondAnswers", str(sr["sample"]["answers"]))
        m("SfourBSecondPerArm", word(sr["sample"]["per_arm"]))
        m("SfourBSecondExactMin", share(min(v["exact"] for v in ag.values()), 0))
        m("SfourBSecondExactMax", share(max(v["exact"] for v in ag.values()), 0))
        m("SfourBSecondWithinOne", share(min(v["within_one"] for v in ag.values()), 0))
        m("SfourBSecondKappaMin", f"{min(v['quadratic_kappa'] for v in ag.values()):.2f}")
        m("SfourBSecondKappaMax", f"{max(v['quadratic_kappa'] for v in ag.values()):.2f}")
        diff = sr["composite_P_minus_R_on_sample"]
        m("SfourBSecondReaderDiff", sgn(diff["reader"]))
        m("SfourBSecondJudgeDiff", sgn(diff["judge"]))

    # ---- 4C: protocol v3c, the corrected skill text ----------------------------------------
    c_path = ROOT / "results" / "v3c" / "quality.json"
    if not c_path.exists():
        return
    c = json.loads(c_path.read_text(encoding="utf-8"))
    assert c["agent_models_returned"] == [model]
    pc, fc, jc = (c["primary_hidden_fact_share_P2_minus_P1"], c["secondary_fact_share_P2_minus_P1"],
                  c["secondary_composite_P2_minus_P1"])
    m("SfourCRuns", str(c["runs"]))
    m("SfourCMissing", word(sum(c["missing"].values())))
    m("SfourCHardTasks", word(len(c["tasks"]["hard"])))
    m("SfourCOrdinaryTasks", word(len(c["tasks"]["ordinary"])))
    m("SfourCPoint", sgn(pc["point"], 3))
    m("SfourCCI", ci(pc["ci95"], 3))
    m("SfourCVerdict", pc["verdict"])
    m("SfourCHiddenPone", share(c["hidden_fact_share_by_arm"]["P1"]))
    m("SfourCHiddenPtwo", share(c["hidden_fact_share_by_arm"]["P2"]))
    m("SfourCFactsPoint", sgn(fc["point"], 3))
    m("SfourCFactsCI", ci(fc["ci95"], 3))
    m("SfourCCompositePoint", sgn(jc["point"]))
    m("SfourCCompositeCI", ci(jc["ci95"]))
    m("SfourCCompositePone", f"{jc['by_arm']['P1']:.2f}")
    m("SfourCCompositePtwo", f"{jc['by_arm']['P2']:.2f}")
    m("SfourCExactMin", share(min(v["exact"] for v in jc["agreement"].values())))
    m("SfourCExactMax", share(max(v["exact"] for v in jc["agreement"].values())))
    m("SfourCAdjudicated", word(jc["adjudicated"]))
    m("SfourCCues", word(c["answers_with_cues"]))
    m("SfourCPoneMinusR", sgn(c["P1_minus_R_historical"]["point"], 3))
    m("SfourCPoneMinusRCI", ci(c["P1_minus_R_historical"]["ci95"], 3))
    m("SfourCPtwoMinusR", sgn(c["P2_minus_R_historical"]["point"], 3))
    m("SfourCPtwoMinusRCI", ci(c["P2_minus_R_historical"]["ci95"], 3))
    m("SfourCHardCost", ratio(c["hard_cost_P2_over_P1"]))
    m("SfourCOrdinaryCost", ratio(c["ordinary_cost_P2_over_P1"]))
    g = c["ordinary_gates"]
    m("SfourCOrdinaryGates", f"{g['P1']}/{c['ordinary_answers']['P1']} and {g['P2']}/{c['ordinary_answers']['P2']}")
    hp = c["hard_process"]
    m("SfourCExpandPone", str(hp["P1"]["expand_calls"]))
    m("SfourCExpandPtwo", str(hp["P2"]["expand_calls"]))
    m("SfourCExpandRunsPone", str(hp["P1"]["runs_with_expand"]))
    m("SfourCExpandRunsPtwo", str(hp["P2"]["runs_with_expand"]))
    m("SfourCRunsPerArm", str(hp["P2"]["runs"]))
    m("SfourCDefault", c["decision"]["default_version"])
    hf = c["hidden_fields"]
    for key, name in (("balance: data[].description", "Origin"), ("memo: data[].description", "Memo"),
                      ("credit_note: data[].post_payment_credit_notes_amount", "CreditNote")):
        for arm, tag in (("P1", "Pone"), ("P2", "Ptwo")):
            m(f"SfourC{name}{tag}", f"{hf[key][arm]['reported']}/{hf[key][arm]['facts']}")
    m("SfourCOriginExpandPtwo", str(hf["balance: data[].description"]["P2"]["via"].get("expand", 0)))

    credit, memo = hf["balance: data[].description"], hf["memo: data[].description"]
    # The two hidden facts behind the gain, under the primary row they belong to (REVISIONE-14a, M9), each over its own
    # number of facts (F6).
    rows = [f"Hidden facts reported (primary) & {share(c['hidden_fact_share_by_arm']['P1'])} & "
            f"{share(c['hidden_fact_share_by_arm']['P2'])} & \\textbf{{{sgn(pc['point'], 3)}}} & "
            f"\\textbf{{{ci(pc['ci95'], 3)}}} \\\\",
            f"\\quad amount of the last credit & {credit['P1']['reported']}/{credit['P1']['facts']} & "
            f"{credit['P2']['reported']}/{credit['P2']['facts']} & & \\\\",
            f"\\quad TB in the invoice memo & {memo['P1']['reported']}/{memo['P1']['facts']} & "
            f"{memo['P2']['reported']}/{memo['P2']['facts']} & & \\\\",
            f"All key facts reported & {share(c['fact_share_by_arm']['P1'])} & {share(c['fact_share_by_arm']['P2'])} & "
            f"{sgn(fc['point'], 3)} & {ci(fc['ci95'], 3)} \\\\",
            f"Judged composite (0--4) & {jc['by_arm']['P1']:.2f} & {jc['by_arm']['P2']:.2f} & {sgn(jc['point'])} & "
            f"{ci(jc['ci95'])} \\\\",
            f"Cost per run, hard tasks (P2/P1) & & & {ratio(c['hard_cost_P2_over_P1'])} & \\\\",
            f"Cost per run, ordinary tasks (P2/P1) & & & {ratio(c['ordinary_cost_P2_over_P1'])} & \\\\"]
    (GEN / "table_s4c.tex").write_text(
        "\\begin{tabular}{lrrrr}\n\\toprule\nOutcome & P1 (text v1) & P2 (text v2) & P2 $-$ P1 & 95\\% interval \\\\\n"
        "\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")
