"""docs/measures-v04.md states results; these tests keep them equal to results/v04/*.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = " ".join((ROOT / "docs" / "measures-v04.md").read_text().split())
E1 = json.loads((ROOT / "results" / "v04" / "e1.json").read_text())
E2 = json.loads((ROOT / "results" / "v04" / "e2.json").read_text())


def test_the_outcomes_of_the_hypotheses_are_those_computed():
    h1, h2 = E1["e1_hypotheses"], E2["e2_hypotheses"]
    assert h1["H1"]["holds"] is False and sum(v["holds"] for v in h1["H1"]["per_comparison"].values()) == 4
    assert h1["H1"]["per_comparison"]["C1 ST vs T"]["gates"] == [4, 5]
    assert "4 comparisons of 5 hold; in C1, strategy with trimming passed the gates in 4 runs of 5 against 5 of 5" in DOC
    assert h1["H2"]["holds"] is False and (h1["H2"]["turns_median_S"], h1["H2"]["turns_min_R"]) == (4, 3)
    assert "median 4 turns with it; minimum 3 without" in DOC
    assert {c: v["holds"] for c, v in h1["H4"]["per_case"].items()} == {"C1": True, "C4": False}
    assert (h2["H5"]["holds"], h2["H5"]["recoveries_written"], h2["H5"]["kept_in_every_later_run"]) == (True, 16, 16)
    assert "16 recoveries of 16, in 10 sequences" in DOC and len(E2["e2"]) == 10
    assert h2["H6"]["testable"] is False
    assert h2["H6"]["sequences_where_the_field_was_reworded"] == {"promotion_on": 2, "promotion_off": 0}
    assert h2["H7"]["holds"] is False
    assert h2["H7"]["runs_without_expand_while_a_field_was_removed"] == {"sampling_arms": 1, "other_arms": 1}
    assert h2["H7"]["requirement_met"] == {"sampling_arms": 1.0, "other_arms": 1.0}


def test_the_figures_of_e1_are_those_of_the_results_file():
    t = E1["e1"]
    assert sum(a["runs"] for arms in t.values() for a in arms.values()) == 50
    shares = [round(t[c]["R"]["share_of_turns_with_several_calls"] * 100) for c in ("C1", "C2", "C4")]
    assert f"{shares[0]}%, {shares[1]}% and {shares[2]}% of the turns" in DOC
    for case, saving, cost in (("C1", 44, 30), ("C4", 44, 24)):
        a, r = t[case]["T"], t[case]["R"]
        assert round((1 - a["tokens_outputs_sent"]["median"] / a["tokens_outputs_original"]["median"]) * 100) == saving
        assert round((1 - a["cost_usd"]["median"] / r["cost_usd"]["median"]) * 100) == cost
        assert a["gates_passed"] == 5
    assert "down 44% on C1 and on C4, cost of a run down 30% and 24%" in DOC
    did_not = {(c, arm): a["did_not_do_the_task"] for c, arms in t.items() for arm, a in arms.items()}
    assert sum(did_not.values()) == 1 and did_not[("C1", "ST")] == 1
    checks = [v for r in E1["per_run"] for k, v in r["coverage"]["checks"].items()
              if k in ("every limit is 10", "every SERP depth is 10", "at most 5 keywords per Trends call",
                       "every SERP call asks Italy, Italian, depth 10")]
    assert checks and all(checks)                                       # the prescribed parameter values, in every run


def test_the_figures_of_e2_are_those_of_the_results_file():
    runs = E2["per_run"]
    assert len(runs) == 50 and all(r["e2"]["requirement"]["met"] for r in runs)
    kept = [max(n for n in s["kept_from_run"].values()) for s in E2["e2"]]
    assert kept.count(2) == 8 and kept.count(3) == 2 and "from the second run in 8 sequences of 10, from the third in 2" in DOC
    assert {(s["case"], s["arm"]) for s in E2["e2"] if s["case"] == "C1"} == {("C1", "base"), ("C1", "sampling")}
    off = [(s["case"], s["arm"], s["switched_off"]) for s in E2["e2"] if s["switched_off"]]
    assert off == [("C1", "base", ["kw_data_google_trends_explore"])]
    quiet = E2["e2_hypotheses"]["H7"]["runs"]
    assert len(quiet) == 2 and all(r["calls_per_tool"].get("stripe_retrieve_invoice") for r in quiet)
    base = next(s for s in E2["e2"] if (s["case"], s["arm"]) == ("C1", "base"))["saving_per_run"]
    samp = next(s for s in E2["e2"] if (s["case"], s["arm"]) == ("C1", "sampling"))["saving_per_run"]
    assert round(base[0] * 100) == 33 and (round(min(base[1:]) * 100), round(max(base[1:]) * 100)) == (26, 38)
    assert (round(min(samp) * 100), round(max(samp) * 100)) == (35, 40)


def test_the_spend_and_the_calls_are_those_of_the_runs():
    cost = lambda rows: sum(r["cost_usd"] for r in rows)       # noqa: E731
    c4 = [r for r in E2["per_run"] if r["case"] == "C4"]
    c1 = [r for r in E2["per_run"] if r["case"] == "C1"]
    # The table has what the ledger says, attempts cut short included: within a few cents of the completed runs.
    for stated, rows in ((12.46, E1["per_run"]), (2.63, c4), (3.99, c1)):
        assert 0 <= stated - cost(rows) < 0.05
    assert "| **Total** | **100** | **19.08** |" in DOC and abs(19.08 - 12.46 - 2.63 - 3.99) < 0.005
    assert E2["dataforseo"]["calls"] == 813 and "813 calls" in DOC
