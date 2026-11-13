#!/usr/bin/env python3
"""The numbers and tables of Study 3 (version 2 of the paper), from results/v04 and results/v06.

  .venv/bin/python -m experiments.paper_numbers      # calls macros() below, writes paper/generated/*.tex

Study 3 reports the measures made after the paper on tracetrim 0.4: E1 and E2 of
protocol-v04.md, E3 of protocol-v04-e3.md, and three offline recalculations that no
protocol covers. As for Studies 1 and 2, the text never types a number: every figure is
a macro defined here (prefix `Sthree`), computed from the JSON files, and the tables are
written as paper/generated/table_s3_*.tex.

No amount of money is read or written: results/v04 carries costs only as changes relative
to the reference arm (`cost_vs_R`), and that is the only form in which they reach the paper.
"""
from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

from .paper_numbers import ROMAN, apct, num, pct
from .paths import ROOT

V04 = ROOT / "results" / "v04"
V06 = ROOT / "results" / "v06"
LOWER = {"C1": "editorial plan", "C2": "content gaps", "C4": "billing support", "C5": "CI triage"}
CASE = {"C1": "Editorial plan", "C2": "Content gaps", "C4": "Billing support$^\\dagger$",
        "C5": "CI triage$^\\dagger$"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


TEN = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def word(n: int) -> str:
    """Counts in prose are written as words up to ten, as in the protocols' reports."""
    return TEN[n] if 0 <= n <= 10 else str(n)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def pct1(x) -> str:
    """A small share, with one decimal: definitions weigh a few percent, and 0% would hide them."""
    return f"{100 * x:.1f}\\%"


def rng(x, fmt="{:g}") -> str:
    """median [min--max], as in the appendix of Studies 1 and 2."""
    return f"{fmt.format(x['median'])} [{fmt.format(x['min'])}--{fmt.format(x['max'])}]"


def rng_pct(x) -> str:
    return f"{round(100 * x['median'])}\\% [{round(100 * x['min'])}--{round(100 * x['max'])}]"


def frozen(protocol: Path) -> str:
    """The date a protocol was frozen, from its own first lines: the paper copies no date by hand."""
    y, mth, d = re.search(r"Frozen on (\d{4})-(\d{2})-(\d{2})", protocol.read_text(encoding="utf-8")).groups()
    return f"{int(d)} {MONTHS[int(mth) - 1]} {y}"


def deviations(protocol: Path) -> int:
    """Deviations are numbered paragraphs, `**N — date, ...`, after the heading of their section."""
    text = protocol.read_text(encoding="utf-8").split("## Deviations", 1)[1]
    return len(re.findall(r"^\*\*\d+ \u2014 ", text, flags=re.M))


def inside(value, spread) -> bool:
    return spread["min"] <= value <= spread["max"]


def macros(m, GEN: Path) -> None:
    """Defines every macro of Study 3 through m(name, value) and writes its tables into GEN."""
    seen = set()
    define = m

    def m(name, value):                                                      # noqa: F811
        # LaTeX macro names are letters only; the prefix keeps Study 3 apart from Studies 1 and 2.
        assert name.startswith("Sthree") and name.isalpha() and name not in seen, name
        seen.add(name)
        define(name, value)

    e1, e2, e3 = (_load(V04 / f"{n}.json") for n in ("e1", "e2", "e3"))
    ind, modes, off, rows = (_load(V04 / f"{n}.json") for n in ("e3_independence", "modes", "switch_off", "rows"))
    dfn = _load(V06 / "definitions.json")

    # ---- design -------------------------------------------------------------
    p04, p04e3 = ROOT / "protocol-v04.md", ROOT / "protocol-v04-e3.md"
    m("SthreeFrozenVfour", frozen(p04))
    m("SthreeFrozenEthree", frozen(p04e3))
    m("SthreeDeviationsVfour", word(deviations(p04)))
    m("SthreeDeviationsEthree", word(deviations(p04e3)))
    (model_a,) = {r["model"] for r in e1["per_run"] + e2["per_run"]}       # one model in E1 and E2, by design
    (model_b,) = e3["models"]
    m("SthreeModelA", model_a)
    m("SthreeModelB", model_b)
    m("SthreeRunsEOneTwo", str(len(e1["per_run"]) + len(e2["per_run"])))
    m("SthreeRunsEOne", str(len(e1["per_run"])))
    m("SthreeRunsETwo", str(len(e2["per_run"])))
    m("SthreeRunsETwoSim", str(sum(r["case"] == "C4" for r in e2["per_run"])))
    m("SthreeRunsETwoReal", str(sum(r["case"] == "C1" for r in e2["per_run"])))
    m("SthreeRunsEThree", str(e3["runs"]))
    m("SthreeRunsEThreePilot", word(e3["pilot_runs"]))
    m("SthreeProviderCallsEThree", word(e3["provider_calls"]))
    runs_e1 = {a["runs"] for arms in e1["e1"].values() for a in arms.values()}
    (per_arm,) = runs_e1
    m("SthreeRunsPerArmEOne", word(per_arm))

    # ---- E1: the strategy, and trimming as a side arm ---------------------------
    t = e1["e1"]
    for c, arms in t.items():
        R = ROMAN[c]
        m(f"SthreeEOneSeveralR{R}", apct(arms["R"]["share_of_turns_with_several_calls"], False))
        m(f"SthreeEOneSeveralS{R}", apct(arms["S"]["share_of_turns_with_several_calls"], False))
    # The strategy alone (S) against the arm without it (R): is S's median inside R's range?
    measures = ("turns", "calls", "billed_input", "several_calls_share_per_run")
    checks = [inside(t[c]["S"][k]["median"], t[c]["R"][k]) for c in t for k in measures]
    m("SthreeEOneSinsideR", str(sum(checks)))
    m("SthreeEOneSinsideRof", str(len(checks)))
    m("SthreeEOneCases", word(len(t)))
    m("SthreeEOneMeasures", word(len(measures)))
    # Combined with trimming (ST against T), billed input left T's range in two cases, in opposite directions.
    st_vs_t = {c: (t[c]["ST"]["billed_input"]["median"], t[c]["T"]["billed_input"]) for c in t if "ST" in t[c]}
    above = [c for c, (v, s) in st_vs_t.items() if v > s["max"]]
    below = [c for c, (v, s) in st_vs_t.items() if v < s["min"]]
    assert above == ["C4"] and below == ["C1"], (above, below)                   # what the text says
    for c, (v, s) in st_vs_t.items():
        m(f"SthreeEOneBilledST{ROMAN[c]}", num(v))
        m(f"SthreeEOneBilledTmin{ROMAN[c]}", num(s["min"]))
        m(f"SthreeEOneBilledTmax{ROMAN[c]}", num(s["max"]))
    h = e1["e1_hypotheses"]
    comp = h["H1"]["per_comparison"]
    m("SthreeHOneHolds", word(sum(v["holds"] for v in comp.values())))
    m("SthreeHOneOf", word(len(comp)))
    m("SthreeHOneFails", word(sum(not v["holds"] for v in comp.values())))
    g = comp["C1 ST vs T"]["gates"]
    m("SthreeHOneGatesST", f"{g[0]}/{per_arm}")
    m("SthreeHOneGatesT", f"{g[1]}/{per_arm}")
    m("SthreeHTwoS", f"{h['H2']['turns_median_S']:g}")
    m("SthreeHTwoR", f"{h['H2']['turns_min_R']:g}")
    strategy_runs = [r for r in e1["per_run"] if r["arm"] in ("S", "ST")]
    other_runs = [r for r in e1["per_run"] if r["arm"] in ("R", "T")]
    did_not = lambda rs: sum(r["no_tool_call"] or r["answer_is_not_the_json_asked"] for r in rs)  # noqa: E731
    assert did_not(strategy_runs) == sum(a["did_not_do_the_task"] for arms in t.values() for a in arms.values())
    assert [r["arm"] for r in strategy_runs if r["no_tool_call"] or r["answer_is_not_the_json_asked"]] == ["ST"]
    m("SthreeEOneAsked", word(did_not(strategy_runs)))
    m("SthreeEOneAskedOther", word(did_not(other_runs)))
    m("SthreeEOneStrategyRuns", str(len(strategy_runs)))
    prescribed = ("every limit is 10", "every SERP depth is 10", "at most 5 keywords per Trends call",
                  "every SERP call asks Italy, Italian, depth 10")
    rs_checked = [r for r in e1["per_run"] if any(k in r["coverage"]["checks"] for k in prescribed)]
    kept = all(v for r in e1["per_run"] for k, v in r["coverage"]["checks"].items() if k in prescribed)
    m("SthreeEOnePrescribedRuns", str(len(rs_checked)))
    m("SthreeEOnePrescribedKept", "respected" if kept else "not always respected")
    for c in ("C1", "C4"):
        R, a = ROMAN[c], t[c]["T"]
        m(f"SthreeEOneTrimTokens{R}", apct(a["tokens_outputs_sent"]["median"] / a["tokens_outputs_original"]["median"] - 1))
        m(f"SthreeEOneTrimCost{R}", apct(a["cost_vs_R"]))
        m(f"SthreeEOneTrimGates{R}", f"{a['gates_passed']}/{a['runs']}")
    rows_e1 = []
    for c, arms in t.items():
        for arm, a in arms.items():
            trim = (pct(a["tokens_outputs_sent"]["median"] / a["tokens_outputs_original"]["median"] - 1)
                    if arm in ("T", "ST") else "--")
            rows_e1.append(f"{CASE[c] if arm == 'R' else ''} & {arm} & {a['runs']} & {a['gates_passed']}/{a['runs']} & "
                           f"{rng(a['turns'])} & {rng(a['calls'])} & {rng_pct(a['several_calls_share_per_run'])} & "
                           f"{num(a['billed_input']['median'])} & {trim} & "
                           f"{pct(a['cost_vs_R']) if arm != 'R' else '--'} \\\\")
        rows_e1.append("\\addlinespace")
    (GEN / "table_s3_e1.tex").write_text(
        "\\begin{tabular}{llrrlllrrr}\n\\toprule\n"
        "Case & Arm & Runs & Gates & Turns & Tool calls & Turns with several calls & Billed input & "
        "Tool-output tokens & Cost of a run \\\\\n"
        "\\midrule\n" + "\n".join(rows_e1[:-1]) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    # ---- E2: adaptive learning --------------------------------------------------
    seqs, h2 = e2["e2"], e2["e2_hypotheses"]
    m("SthreeHFiveKept", str(h2["H5"]["kept_in_every_later_run"]))
    m("SthreeHFiveRecoveries", str(h2["H5"]["recoveries_written"]))
    m("SthreeETwoSequences", word(len(seqs)))
    m("SthreeETwoSequencesSim", word(sum(s["case"] == "C4" for s in seqs)))
    m("SthreeETwoSequencesReal", word(sum(s["case"] == "C1" for s in seqs)))
    per = lambda case: {sum((x["case"], x["arm"]) == (case, s["arm"]) for x in seqs) for s in seqs if s["case"] == case}  # noqa: E731
    (per_sim,), (per_real,) = per("C4"), per("C1")
    m("SthreeETwoSeqPerArmSim", word(per_sim))
    m("SthreeETwoSeqPerArmReal", word(per_real))
    (length,) = {len(s["saving_per_run"]) for s in seqs}
    m("SthreeETwoSequenceLength", word(length))
    kept_from = [max(s["kept_from_run"].values()) for s in seqs]
    assert set(kept_from) <= {2, 3}, kept_from                       # the text names the second and third run
    m("SthreeETwoFromSecond", str(kept_from.count(2)))
    m("SthreeETwoFromThird", word(kept_from.count(3)))
    met = [r["e2"]["requirement"]["met"] for r in e2["per_run"]]
    m("SthreeETwoRequirementMet", str(sum(met)))
    m("SthreeETwoRuns", str(len(met)))
    rw = h2["H6"]["sequences_where_the_field_was_reworded"]
    assert rw["promotion_on"] == 2 and rw["promotion_off"] == 0        # the text: "both with promotion on"
    m("SthreeHSixReworded", word(rw["promotion_on"] + rw["promotion_off"]))
    m("SthreeHSixRewordedOff", word(rw["promotion_off"]))
    # Sampling, on the simulated case: saving of the last run of each sequence (the protocol's
    # per-sequence measure), median over the sequences with sampling and over those without.
    last = lambda sampling: statistics.median(s["saving_per_run"][-1] for s in seqs  # noqa: E731
                                              if s["case"] == "C4" and ("sampling" in s["arm"]) == sampling)
    m("SthreeETwoSavingSampling", apct(-last(True)))
    m("SthreeETwoSavingNoSampling", apct(-last(False)))
    quiet = h2["H7"]["runs"]
    via = {t_ for r in quiet for t_, n in r["calls_per_tool"].items() if t_ == "stripe_retrieve_invoice" and n}
    assert via == {"stripe_retrieve_invoice"} and all(r["met"] for r in quiet)
    m("SthreeETwoOtherTool", word(len(quiet)))
    n7 = h2["H7"]["runs_without_expand_while_a_field_was_removed"]
    m("SthreeHSevenPerGroup", word(n7["sampling_arms"]) if n7["sampling_arms"] == n7["other_arms"] else "--")
    # The one switch-off, on the real case, and its recalculation with evidence from more runs.
    (s_off,) = [s for s in seqs if s["switched_off"]]
    s_samp = next(s for s in seqs if s["case"] == s_off["case"] and s["arm"] == "sampling")
    assert not s_samp["switched_off"] and s_off["case"] == "C1" and s_off["arm"] == "base"
    # The text: on the real case the model used expand in both sequences (no other tool returns the fields).
    assert all(sum(s["expand_calls"]) for s in seqs if s["case"] == "C1")
    sv = s_off["saving_per_run"]
    m("SthreeSwitchSavingFirst", apct(-sv[0], False))
    m("SthreeSwitchSavingLaterMin", apct(-min(sv[1:]), False))
    m("SthreeSwitchSavingLaterMax", apct(-max(sv[1:]), False))
    m("SthreeSwitchSamplingMin", apct(-min(s_samp["saving_per_run"]), False))
    m("SthreeSwitchSamplingMax", apct(-max(s_samp["saving_per_run"]), False))
    so = off["switch_offs"]
    rules = sorted(int(k) for k in so if k != "as_run")              # distinct runs of evidence asked for
    assert rules == [2, 3], rules
    m("SthreeSwitchAsRun", word(so["as_run"]))
    m("SthreeSwitchRuleA", word(rules[0]))
    m("SthreeSwitchRuleB", word(rules[1]))
    m("SthreeSwitchWithRules", word(sum(so[str(k)] for k in rules)))
    first = next(s for s in off["per_sequence"] if s["switched_off"]["as_run"])["counts_where_a_result_was_expanded"][0]
    m("SthreeSwitchFiltered", word(first["filtered"]))
    m("SthreeSwitchExpanded", word(first["expanded"]))
    m("SthreeSwitchRunsWithExpand", word(first["runs_with_expand"]))
    from tracetrim.evidence import Adaptive
    m("SthreeSwitchDefaultNow", word(Adaptive().switch_off_runs))
    rq = rows["requests"]
    m("SthreeExpandRequests", str(rq["expand_requests"]))
    m("SthreeExpandNothing", str(rq["gave_nothing_back"]))
    m("SthreeExpandUnknownId", str(rq["by_reply"]["unknown call_id"]))
    m("SthreeExpandRows", str(rq["unknown_call_id_asking_for_rows"]))
    m("SthreeExpandIndex", word(rq["with_an_index_on_a_valid_call_id"]))

    # ---- E3: the strategy on a second model ------------------------------------
    t3, h3 = e3["e3"], e3["e3_hypotheses"]
    order = ("C4", "C5", "C2", "C1")
    rows_e3 = []
    for c in order:
        for arm, name in (("R", "without"), ("S", "strategy")):
            a = t3[c][arm]
            label = CASE[c] + ("$^\\ddagger$" if c in ("C1", "C2") else "") if arm == "R" else ""
            rows_e3.append(f"{label} & {name} & "
                           f"{a['runs']} & {rng(a['turns'])} & {rng(a['calls'])} & "
                           f"{rng_pct(a['several_calls_share_per_run'])} & {num(round(a['billed_input']['median']))} & "
                           f"{pct(a['cost_vs_R']) if arm == 'S' else '--'} \\\\")
        rows_e3.append("\\addlinespace")
    (GEN / "table_s3_e3.tex").write_text(
        "\\begin{tabular}{llrlllrr}\n\\toprule\n"
        "Case & Arm & Runs & Turns & Tool calls & Turns with several calls & Billed input & Cost of a run \\\\\n"
        "\\midrule\n" + "\n".join(rows_e3[:-1]) + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")
    m("SthreeEThreeRunsPerArmMin", word(min(t3[c][a]["runs"] for c in t3 for a in "RS")))
    m("SthreeEThreeRunsPerArmMax", word(max(t3[c][a]["runs"] for c in t3 for a in "RS")))
    for c in order:
        R = ROMAN[c]
        for arm in "RS":
            a = t3[c][arm]
            m(f"SthreeEThreeTurns{arm}{R}", f"{a['turns']['median']:g}")
            m(f"SthreeEThreeTurns{arm}min{R}", f"{a['turns']['min']:g}")
            m(f"SthreeEThreeTurns{arm}max{R}", f"{a['turns']['max']:g}")
            m(f"SthreeEThreeRuns{arm}{R}", word(a["runs"]))
    # C5: the runs without the strategy that issued one call per turn, and the one that did not.
    c5 = {a: sorted(r["turns"] for r in e3["per_run"] if (r["case"], r["arm"]) == ("C5", a) and not r.get("pilot"))
          for a in "RS"}
    long_r = [x for x in c5["R"] if x > c5["S"][-1]]
    # "one call per turn": every turn of those runs but the last (the answer) made exactly one call.
    long_runs = [r for r in e3["per_run"] if (r["case"], r["arm"]) == ("C5", "R") and not r.get("pilot")
                 and r["turns"] in long_r]
    assert all(r["turns"] == r["calls"] + 1 for r in long_runs), [(r["turns"], r["calls"]) for r in long_runs]
    assert len(c5["R"]) - len(long_r) == 1                                  # the text: "the remaining one"
    m("SthreeEThreeLongRunsv", word(len(long_r)))
    m("SthreeEThreeLongMinv", str(min(long_r)))
    m("SthreeEThreeLongMaxv", str(max(long_r)))
    m("SthreeEThreeRunsCv", word(len(c5["R"])))
    m("SthreeEThreeInputChangev", apct(t3["C5"]["S"]["billed_input"]["median"] / t3["C5"]["R"]["billed_input"]["median"] - 1))
    m("SthreeEThreeCostv", apct(t3["C5"]["S"]["cost_vs_R"]))
    m("SthreeEThreeCostiv", apct(t3["C4"]["S"]["cost_vs_R"]))
    assert [c for c, v in h3["H8"]["per_case"].items() if v["holds"]] == ["C1", "C2"]   # "the two with recorded outputs"
    assert all(v["share_max_R"] >= v["share_median_S"] for v in h3["H9"]["per_case"].values())
    m("SthreeHEightHolds", word(h3["H8"]["cases_where_it_holds"]))
    m("SthreeHEightOf", word(len(h3["H8"]["per_case"])))
    m("SthreeHNineHolds", word(h3["H9"]["cases_where_it_holds"]))
    for c, v in h3["H10"]["per_case"].items():
        m(f"SthreeHTenGates{ROMAN[c]}", f"{v['gates'][0]}/{t3[c]['S']['runs']}")
    m("SthreeHElevenChecks", str(h3["H11"]["checks"]))
    m("SthreeHElevenPassed", str(h3["H11"]["passed"]))
    m("SthreeHElevenRuns", word(t3["C1"]["S"]["runs"] + t3["C2"]["S"]["runs"]))
    for c in ("C1", "C2"):
        sh = e3["replay"][c]["per_run_exact_share"]
        m(f"SthreeExactMin{ROMAN[c]}", apct(min(sh), False))
        m(f"SthreeExactMax{ROMAN[c]}", apct(max(sh), False))
    # Post hoc, not in the protocol: the calls that could have been made in an earlier turn.
    for c in ("C4", "C5"):
        R = ROMAN[c]
        for arm in "RS":
            a = ind["cases"][c][arm]
            assert a["turns_median"] == t3[c][arm]["turns"]["median"]
            m(f"SthreeEarlier{arm}{R}", apct(a["share_that_could_have_been_made_earlier"], False))
        (fewest,) = {r["fewest_turns"] for a in "RS" for r in ind["cases"][c][a]["per_run"]} if c == "C5" else \
            {ind["cases"][c][a]["fewest_turns_median"] for a in "RS"}            # C5: the same in every run
        m(f"SthreeFewest{R}", word(int(fewest)))
    # The text: on billing support the strategy's runs took the fewest turns the task needs.
    assert ind["cases"]["C4"]["S"]["fewest_turns_median"] == t3["C4"]["S"]["turns"]["median"] == t3["C4"]["S"]["turns"]["max"]
    c5i = ind["cases"]["C5"]                    # both arms pooled, by their calls
    m("SthreeDependingv", apct(sum(c5i[a]["share_depending_on_an_earlier_result"] * c5i[a]["calls"] for a in "RS")
                               / sum(c5i[a]["calls"] for a in "RS"), False))

    # ---- hypotheses H1--H11, as frozen ------------------------------------------
    say = lambda v: "holds" if v else "failed"                                  # noqa: E731
    hyp = [
        ("H1", "E1", "With the strategy, gates and coverage are no worse",
         say(h["H1"]["holds"]), f"{sum(v['holds'] for v in comp.values())} of {len(comp)} comparisons hold; "
         f"editorial plan: ST passed the gates in {g[0]}/{per_arm} runs, T in {g[1]}/{per_arm}"),
        ("H2", "E1", "Billing support: median turns with the strategy below the minimum without",
         say(h["H2"]["holds"]), f"median {h['H2']['turns_median_S']:g} against minimum {h['H2']['turns_min_R']:g}"),
        ("H3", "E1", "Removed before freezing (it concerned arms that are not run)", "--", "--"),
        ("H4", "E1", "The strategy does not undo the saving of trimming (billed input)",
         say(h["H4"]["holds"]), "; ".join(f"{LOWER[c]} {'holds' if v['holds'] else 'fails'}"
                                          for c, v in h["H4"]["per_case"].items())),
        ("H5", "E2", "A field recovered with \\texttt{expand} and written is kept from the next run",
         say(h2["H5"]["holds"]), f"{h2['H5']['kept_in_every_later_run']} of {h2['H5']['recoveries_written']} "
         f"recoveries, {len(seqs)} sequences"),
        ("H6", "E2", "A reworded field needs promotion to come back",
         "not testable" if not h2["H6"]["testable"] else say(h2["H6"]["holds"]),
         f"reworded in {rw['promotion_on'] + rw['promotion_off']} sequences, "
         + ("all with promotion on" if not rw["promotion_off"] else f"{rw['promotion_off']} with promotion off")),
        ("H7", "E2", "Sampling reaches a field the agent does not ask for",
         say(h2["H7"]["holds"]), f"runs with a field removed and no \\texttt{{expand}}: {n7['sampling_arms']} with "
         f"sampling, {n7['other_arms']} without; requirement met in "
         + ("both" if h2["H7"]["requirement_met"] == {"sampling_arms": 1.0, "other_arms": 1.0} else "not all")),
        ("H8", "E3", "Median turns with the strategy below the minimum without",
         say(h3["H8"]["holds"]), f"holds in {h3['H8']['cases_where_it_holds']} of {len(h3['H8']['per_case'])} cases"),
        ("H9", "E3", "Median share of turns with several calls above the maximum without",
         say(h3["H9"]["holds"]), f"holds in {h3['H9']['cases_where_it_holds']} of {len(h3['H9']['per_case'])} cases"),
        ("H10", "E3", "Gates and coverage are no worse with the strategy",
         say(h3["H10"]["holds"]), "; ".join(f"gates, {LOWER[c]}: {v['gates'][0]}/{t3[c]['S']['runs']} "
                                           f"with, {v['gates'][1]}/{t3[c]['R']['runs']} without"
                                           for c, v in h3["H10"]["per_case"].items())),
        ("H11", "E3", "The strategy does not override the values the prompts prescribe",
         say(h3["H11"]["holds"]), f"{h3['H11']['passed']} of {h3['H11']['checks']} checks"),
    ]
    m("SthreeHypotheses", word(sum(x[3] != "--" for x in hyp)))
    (GEN / "table_s3_hypotheses.tex").write_text(
        "\\begin{tabular}{llp{6.2cm}lp{5.2cm}}\n\\toprule\n& Exp. & Hypothesis, as frozen & Outcome & Detail \\\\\n\\midrule\n"
        + "\n".join(f"{a} & {b} & {c} & {d} & {e} \\\\" for a, b, c, d, e in hyp)
        + "\n\\bottomrule\n\\end{tabular}\n", encoding="utf-8")

    # ---- offline, not in a protocol: modes of use, rows, tool definitions -------
    cands = [(tl, a) for c in modes["cases"].values() for tl in c["tools"].values() for a in tl["arguments"].values()]
    assert len(cands) == modes["candidates"]
    m("SthreeModesCandidates", str(len(cands)))
    m("SthreeModesTools", str(sum(len(c["tools"]) for c in modes["cases"].values())))
    m("SthreeModesRuns", str(sum(c["runs_evaluated"] for c in modes["cases"].values())))
    m("SthreeModesBenefit", word(modes["with_a_benefit"]))
    m("SthreeModesThreshold", word(modes["settings"]["benefit_points"]))
    pts = [a["points_removed_more_than_general"] for _, a in cands]
    small = 1                                                                # points: "less than one point"
    m("SthreeModesSmall", word(small))
    m("SthreeModesMaxPoints", f"{max(pts):g}")
    m("SthreeModesUnderOne", str(sum(p < small for p in pts)))
    m("SthreeModesLoseMore", word(sum(a["calls_losing_a_used_field"] > tl["general"]["calls_losing_a_used_field"]
                                      for tl, a in cands)))
    m("SthreeModesThin", str(sum(a["calls_per_profile_min"] < modes["settings"]["min_profile_calls"] for _, a in cands)))
    m("SthreeModesMinProfile", word(modes["settings"]["min_profile_calls"]))
    m("SthreeModesFallback", str(sum(a["new_cases_covered_by_the_general_projection"] for _, a in cands)))

    a_, b_ = rows["search_data_api"], rows["other_apis"]
    m("SthreeRowsRuns", str(sum(c["runs"] for c in rows["elements"].values())))
    m("SthreeRowsTopSearch", apct(a_["calls_reused_from_the_top"] / a_["calls_with_reuse"], False))
    m("SthreeRowsTopOther", apct(b_["calls_reused_from_the_top"] / b_["calls_with_reuse"], False))
    m("SthreeRowsCut", word(5))                                              # the key below: ..._if_cut_to_5
    m("SthreeRowsCutFiveSearch", apct(a_["calls_losing_a_reused_element_if_cut_to_5"] / a_["calls_with_reuse"], False))
    m("SthreeRowsCutFiveOther", apct(b_["calls_losing_a_reused_element_if_cut_to_5"] / b_["calls_with_reuse"], False))
    m("SthreeRowsLastSearch", apct(a_["calls_reusing_the_last_element"] / a_["calls_with_reuse"], False))
    m("SthreeRowsCalls", str(a_["calls_with_reuse"]))

    m("SthreeDefRuns", str(dfn["read"]["runs"]))
    ref = [gr for gr in dfn["groups"] if gr["arm"] == "R"]
    med = lambda k: [gr[k]["median"] for gr in ref]                                 # noqa: E731
    m("SthreeDefCostMin", pct1(min(med("definitions_share_of_cost"))))
    m("SthreeDefCostMax", pct1(max(med("definitions_share_of_cost"))))
    m("SthreeDefInputMin", pct1(min(med("definitions_share_of_input"))))
    m("SthreeDefInputMax", pct1(max(med("definitions_share_of_input"))))
    m("SthreeDefUncachedMax", pct1(max(med("definitions_share_of_cost_if_never_cached"))))
    sc = dfn["scenario_all_server_tools"]
    d_ = sc["definitions"]
    m("SthreeDefServerTools", str(sc["server_tools"]))
    m("SthreeDefCaseTools", str(len(sc["tools_used"]["in_any_run"]) + len(sc["tools_used"]["offered_never_called"])))
    m("SthreeDefUsedTools", str(len(sc["tools_used"]["in_any_run"])))
    m("SthreeDefHiddenTools", str(d_["hidden_tools"]))
    m("SthreeDefScenarioRuns", word(sc["runs"]))
    m("SthreeDefAllTokens", num(d_["all_server_tools"]))
    m("SthreeDefCaseTokens", num(d_["case_tools_12"]))
    m("SthreeDefDeferredTokens", num(d_["deferred_visible_plus_meta"]))
    m("SthreeDefSavedShare", apct(d_["saved_per_turn_share"], False))
    cached = {k: v for k, v in sc["cache"].items() if k != "none"}               # every assumption with a cache
    gain = [1 - v["D_cost_vs_R79"]["median"] for v in cached.values()]
    m("SthreeDefHideGainMin", apct(min(gain), False))
    m("SthreeDefHideGainMax", apct(max(gain), False))
    extra = [v["extra_turns_that_cancel_the_gain"]["median"] for v in cached.values()]
    m("SthreeDefExtraTurnsMin", f"{min(extra):.1f}")
    m("SthreeDefExtraTurnsMax", f"{max(extra):.1f}")
    first_turn = sc["cache"]["recorded"]
    m("SthreeDefFirstTurnAll", num(first_turn["R79_first_turn_input"]["median"]))
    m("SthreeDefFirstTurnHidden", num(first_turn["D_first_turn_input"]["median"]))
