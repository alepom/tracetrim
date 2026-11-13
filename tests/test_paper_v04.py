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


@pytest.fixture(scope="module")
def M(tmp_path_factory):
    macros = {}
    paper_numbers_v04.macros(macros.__setitem__, tmp_path_factory.mktemp("generated"))
    # As the page writes them: 44\% is 44%, 1{,}023{,}210 is 1,023,210.
    return {k: v.replace("\\%", "%").replace("{,}", ",") for k, v in macros.items()}


def test_every_macro_is_a_valid_latex_name_and_the_tables_are_written(tmp_path):
    macros = {}
    paper_numbers_v04.macros(macros.__setitem__, tmp_path)
    assert macros and all(re.fullmatch(r"Sthree[A-Za-z]+", k) for k in macros)
    assert {p.name for p in tmp_path.glob("table_s3_*.tex")} == {"table_s3_e1.tex", "table_s3_e3.tex",
                                                                 "table_s3_hypotheses.tex"}


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
    assert (f"tool-output tokens down {M['SthreeEOneTrimTokensi']} on C1 and on C4, cost of a run down "
            f"{M['SthreeEOneTrimCosti']} and {M['SthreeEOneTrimCostiv']}") in DOC
    assert M["SthreeEOneTrimTokensiv"] == M["SthreeEOneTrimTokensi"] == "44%"
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
