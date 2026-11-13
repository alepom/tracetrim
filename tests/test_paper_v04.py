"""Study 3 of the paper and docs/measures-v04.md state the same results: these tests keep them from drifting.

The paper's figures are macros computed from results/v04 and results/v06 by
experiments/paper_numbers_v04.py; the page states them in words. Each test reads a
macro and finds the same figure, in its sentence, in the page.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments import paper_numbers_v04  # noqa: E402

DOC = " ".join((ROOT / "docs" / "measures-v04.md").read_text(encoding="utf-8").split())
DEFS = " ".join((ROOT / "docs" / "definitions.md").read_text(encoding="utf-8").split())
BASE = " ".join((ROOT / "docs" / "baselines.md").read_text(encoding="utf-8").split())


@pytest.fixture(scope="module")
def M(tmp_path_factory):
    macros = {}
    paper_numbers_v04.macros(macros.__setitem__, tmp_path_factory.mktemp("generated"))
    # As the page writes them: 44\% is 44%, 1{,}023{,}210 is 1,023,210.
    return {k: v.replace("\\%", "%").replace("{,}", ",") for k, v in macros.items()}


def test_every_macro_is_a_valid_latex_name_and_the_tables_are_written(tmp_path):
    macros = {}
    paper_numbers_v04.macros(macros.__setitem__, tmp_path)
    assert macros and all(re.fullmatch(r"(Sthree|Base|AuditEOne|AuditETwo)[A-Za-z]+", k) for k in macros)
    assert {p.name for p in tmp_path.glob("table_s3_*.tex")} == {
        "table_s3_e1.tex", "table_s3_e1_tokens.tex", "table_s3_e3.tex", "table_s3_hypotheses.tex",
        "table_s3_audit_eone.tex", "table_s3_baselines.tex"}


def test_study3_macros_do_not_collide_with_studies_1_and_2(tmp_path):
    # Studies 1 and 2 have audits of their own (`AuditC...`): a name defined twice would be silently overwritten.
    from experiments import paper_numbers
    names = []
    paper_numbers_v04.macros(lambda k, v: names.append(k), tmp_path)
    src = (ROOT / "experiments" / "paper_numbers.py").read_text(encoding="utf-8")
    assert not [n for n in names if f'"{n}"' in src or f"'{n}'" in src]
    assert len(names) == len(set(names)) and hasattr(paper_numbers, "main")


def test_adaptive_learning(M):
    assert (M["SthreeHFiveKept"], M["SthreeHFiveRecoveries"]) == ("16", "16")
    assert f"{M['SthreeHFiveKept']} recoveries of {M['SthreeHFiveRecoveries']}, in 10 sequences" in DOC
    assert M["SthreeETwoSequences"] == "ten"
    assert (f"from the second run in {M['SthreeETwoFromSecond']} sequences of 10, from the third in 2" in DOC
            and M["SthreeETwoFromThird"] == "two")
    assert f"met in all {M['SthreeETwoRequirementMet']} runs" in DOC
    assert (f"a median of {M['SthreeETwoSavingSampling']} of tool-output tokens removed with sampling against "
            f"{M['SthreeETwoSavingNoSampling']} without it") in DOC
    assert (f"went from {M['SthreeSwitchSavingFirst']} to between {M['SthreeSwitchSavingLaterMin']} and "
            f"{M['SthreeSwitchSavingLaterMax']} in the later runs") in DOC
    assert f"it stayed at {M['SthreeSwitchSamplingMin'][:-1]}–{M['SthreeSwitchSamplingMax']}" in DOC
    assert f"`expand` requests that gave nothing back**: {M['SthreeExpandNothing']} of {M['SthreeExpandRequests']}" in DOC


def test_trimming_and_the_strategy_on_the_first_model(M):
    assert M["SthreeEOneTrimTokensiv"] == M["SthreeEOneTrimTokensi"] == "44%"
    # Review 12.1: per run, before recovery and with the expand replies; the cost from billed usage.
    assert (M["SthreeEOneTrimBeforei"], M["SthreeEOneTrimBeforeiv"]) == ("44%", "44%")
    assert (M["SthreeEOneTrimNeti"], M["SthreeEOneTrimNetiv"]) == ("34%", "44%")
    assert M["SthreeEOneTrimBeforei"] == M["SthreeEOneTrimBeforeiv"]
    assert (f"tool-output tokens before recovery down {M['SthreeEOneTrimBeforei']} on C1 and on C4, cost of a run "
            f"down {M['SthreeEOneTrimCosti']} and {M['SthreeEOneTrimCostiv']}") in DOC
    assert (f"down {M['SthreeEOneTrimNeti']} on C1, where every run of T called `expand` (median "
            f"{d(M['SthreeEOneTrimExpandi'])} calls), and {M['SthreeEOneTrimNetiv']} on C4, where no run did") in DOC
    assert "The cost of a run is the LLM API cost per run, from billed usage" in DOC
    assert f"medians of the {d(M['SthreeEOneTrimRatioRuns'])} runs that called a tool" in DOC
    assert (M["SthreeEOneTrimCosti"], M["SthreeEOneTrimCostiv"]) == ("30%", "24%")
    assert (f"{M['SthreeEOneSeveralRi']}, {M['SthreeEOneSeveralRii']} and {M['SthreeEOneSeveralRiv']} "
            "of the turns that made a call made several") in DOC
    assert f"gates passed in {M['SthreeEOneTrimGatesi'].replace('/', ' runs of ')} in the trimmed arm" in DOC


def d(value: str) -> str:
    """A count the paper writes as a word ("four"), as the page writes it ("4")."""
    return str(paper_numbers_v04.TEN.index(value)) if value in paper_numbers_v04.TEN else value


def test_the_strategy_on_the_second_model(M):
    assert (M["SthreeEThreeTurnsRv"], M["SthreeEThreeTurnsSv"]) == ("17", "6")
    assert f"The median went from {M['SthreeEThreeTurnsRv']} turns to {M['SthreeEThreeTurnsSv']}" in DOC
    assert f"the cost of a run went down {M['SthreeEThreeCostv']}" in DOC
    assert f"holds on {d(M['SthreeHEightHolds'])} cases of {d(M['SthreeHEightOf'])} (C1, C2)" in DOC
    assert f"holds on {d(M['SthreeHNineHolds'])} cases of {d(M['SthreeHEightOf'])}:" in DOC
    assert (f"{M['SthreeHElevenPassed']} checks of {M['SthreeHElevenChecks']}, in the "
            f"{d(M['SthreeHElevenRuns'])} strategy runs") in DOC
    assert (f"exactly in {M['SthreeExactMini']} to {M['SthreeExactMaxi']} of the calls of a run on C1 and in "
            f"{M['SthreeExactMinii']} to {M['SthreeExactMaxii']} on C2") in DOC
    for c, R in (("C4", "iv"), ("C5", "v")):       # the post-hoc table: turns, fewest turns, calls made late
        for arm, name in (("R", "without"), ("S", "strategy")):
            assert (f"| {c} | {name} | {M['SthreeEThreeTurns' + arm + R]} | {d(M['SthreeFewest' + R])} | "
                    f"{M['SthreeEarlier' + arm + R]} |") in DOC


def test_the_offline_analyses(M):
    assert (M["SthreeModesCandidates"], M["SthreeModesTools"]) == ("18", "11")
    assert f"{M['SthreeModesCandidates']} candidate arguments on {M['SthreeModesTools']} tools" in DOC
    assert f"is {M['SthreeModesMaxPoints']} points; {M['SthreeModesUnderOne']} of 18 add less than one" in DOC
    assert M["SthreeModesBenefit"] == "no" and "**None shows a benefit.**" in DOC
    assert (f"the first ones in {M['SthreeRowsTopSearch']} of the calls, and a cut to five rows would have removed a "
            f"reused one in {M['SthreeRowsCutFiveSearch']}") in DOC
    assert M["SthreeRowsCutFiveSearch"] == "92%"


def test_the_tool_definitions(M):
    assert f"the {M['SthreeDefRuns']} recorded runs of the v0.4 measures" in DEFS
    assert f"lists **{M['SthreeDefServerTools']} tools**" in DEFS
    assert f"| R79: the 79 tools of the server | {M['SthreeDefAllTokens']} |" in DEFS
    assert f"| D: 11 listed + meta-tools, 68 hidden | {M['SthreeDefDeferredTokens']} |" in DEFS
    assert f"({M['SthreeDefSavedShare']})" in DEFS
    assert (f"save between about {M['SthreeDefHideGainMin']} and {M['SthreeDefHideGainMax']} of a run's cost"
            in DEFS)
    assert f"the first turn goes from {M['SthreeDefFirstTurnAll']} to {M['SthreeDefFirstTurnHidden']} tokens" in DEFS


def test_the_post_hoc_audits(M):
    assert (M["AuditEOneAudit"], M["AuditEOneFacts"], M["AuditEOneKeyword"], M["AuditEOneByHand"]) == \
        ("84", "100", "92", "87")
    assert (f"The audit passed {M['AuditEOneAudit']} of {M['AuditEOneFacts']} diagnoses, the keyword test "
            f"{M['AuditEOneKeyword']}; {d(M['AuditEOneRuleCaused'])} of the audit's failures are the rule's") in DOC
    assert f"so {M['AuditEOneByHand']} of {M['AuditEOneFacts']} are right read by hand" in DOC
    assert (f"missing in {M['AuditEOneMissing']} runs of {M['AuditEOneRuns']}, in every arm") in DOC
    for arm in ("R", "S", "T", "ST"):
        assert (f"| {arm} | 5 | 5/5 | {M['AuditEOneKeyword' + arm]} | {M['AuditEOneAudit' + arm]} | "
                f"{d(M['AuditEOneMissing' + arm])}/5 |") in DOC
    assert (M["AuditEOneMissingR"], M["AuditEOneMissingS"], M["AuditEOneMissingT"], M["AuditEOneMissingST"]) == \
        ("one", "three", "five", "two")
    assert f"The keyword test accepted {d(M['AuditEOneKeywordMissed'])} of the 11" in DOC
    assert (f"{M['AuditETwoCorrecti']} of {M['AuditETwoPairsi']} pairs right by rule; every pair right in "
            f"{d(M['AuditETwoRunsAlli'])} runs of {d(M['AuditETwoRunsi'])}") in DOC
    assert (f"{M['AuditETwoCorrectiv']} of {M['AuditETwoTicketsiv']} tickets by rule; the "
            f"{d(M['AuditETwoAmbiguousiv'])} failures are the rule's") in DOC
    assert f"by hand {M['AuditETwoByHandiv']} of {M['AuditETwoTicketsiv']} are right" in DOC


def test_the_baselines(M):
    # docs/baselines.md writes one decimal (−51.3%); the paper rounds to whole points.
    for case, R in (("C1", "i"), ("C2", "ii"), ("C3", "iii"), ("C4", "iv"), ("C5", "v")):
        n = M[f"BaseStructured{R}"]
        static, learned = d(M[f"BaseMissStatic{R}"]), d(M[f"BaseMissLearned{R}"])
        assert re.search(rf"\| {case}†? \| {n} \| {static} \([0-9.]+%\)\*? \| {learned} \(", BASE), case
    for case, R in (("C1", "i"), ("C3", "iii"), ("C4", "iv"), ("C5", "v"), ("C2", "ii")):
        row = re.search(rf"\| {case}†? \| −[0-9.]+% \| −[0-9.]+% \| −[0-9.]+% \| −([0-9.]+)% \|", BASE)
        assert row and f"{round(float(row.group(1)))}%" == M[f"BaseLearnedStatic{R}"], case
    assert (M["BaseLearnedStaticBigMin"], M["BaseLearnedStaticBigMax"]) == ("40%", "56%")
    assert "removes 40–56% of what is left after the static filter in C1, C3 and C5, and 24% in C4" in BASE
    assert M["BaseLearnedStaticiv"] == "24%" and M["BaseLearnedStaticii"] == "4%"
    assert f"Minification is a large share of every figure** ({M['BaseMinRawMin'][:-1]}–{M['BaseMinRawMax']} of raw)" in BASE
    assert (f"the value `\"customer\"` ({M['BaseObjectCustomer']} calls of `stripe_search_customers`) or "
            f"`\"charge\"` ({d(M['BaseObjectCharge'])})") in BASE
    assert "adds 3.5 points in C3" in BASE and M["BaseBothAddsiii"] == "3.5"
