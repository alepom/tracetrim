"""docs/measures-v04.md states results; these tests keep them equal to results/v04/*.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = " ".join((ROOT / "docs" / "measures-v04.md").read_text().split())
E1 = json.loads((ROOT / "results" / "v04" / "e1.json").read_text())
E2 = json.loads((ROOT / "results" / "v04" / "e2.json").read_text())
E3 = json.loads((ROOT / "results" / "v04" / "e3.json").read_text())
IND = json.loads((ROOT / "results" / "v04" / "e3_independence.json").read_text())
SKILL = " ".join((ROOT / "docs" / "skill.md").read_text().split())


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


def _n(x):
    return f"{x:g}"


def test_the_table_of_e3_is_that_of_the_results_file():
    rng = lambda m, pct=False: (f"{round(m['median'] * 100)}% [{round(m['min'] * 100)}–{round(m['max'] * 100)}]" if pct  # noqa: E731
                                else f"{_n(m['median'])} [{_n(m['min'])}–{_n(m['max'])}]")
    assert (E3["runs"], E3["pilot_runs"], E3["provider_calls"], E3["models"]) == (38, 4, 0, ["gemini-3.8-flash"])
    for case, arms in E3["e3"].items():
        for arm, name in (("R", "without"), ("S", "strategy")):
            a = arms[arm]
            line = (f"| {case} | {name} | {a['runs']} | {rng(a['turns'])} | {rng(a['calls'])} | "
                    f"{rng(a['several_calls_share_per_run'], True)} | {round(a['billed_input']['median']):,} | "
                    f"{a['cost_usd']['median']:.3f} |")
            assert line in DOC, line
    assert f"42 runs, pilot included, for {E3['cost_usd']:.2f} USD" in DOC


def test_the_outcomes_of_e3_are_those_computed():
    h = E3["e3_hypotheses"]
    assert (h["H8"]["holds"], h["H8"]["cases_where_it_holds"]) == (False, 2)
    assert [c for c, v in h["H8"]["per_case"].items() if v["holds"]] == ["C1", "C2"]
    assert (h["H9"]["holds"], h["H9"]["cases_where_it_holds"]) == (False, 0)
    assert all(v["share_max_R"] >= v["share_median_S"] for v in h["H9"]["per_case"].values())
    assert h["H10"]["holds"] and {c: v["gates"] for c, v in h["H10"]["per_case"].items()} == {"C4": [8, 8], "C5": [6, 6]}
    assert (h["H11"]["holds"], h["H11"]["checks"], h["H11"]["passed"]) == (True, 9, 9)
    t = E3["e3"]
    assert all(t[c][a]["gates_passed"] == t[c][a]["runs"] and t[c][a]["coverage"]["min"] == 1 for c in ("C4", "C5") for a in "RS")
    assert not E3["did_not_do_the_task"] and all(t[c][a]["flagged"] == 0 for c in t for a in "RS")
    # What the text says of C5 and C4, in both pages.
    c5 = {a: sorted(r["turns"] for r in E3["per_run"] if (r["case"], r["arm"]) == ("C5", a) and not r.get("pilot")) for a in "RS"}
    assert c5["R"][0] == 5 and (c5["R"][1], c5["R"][-1]) == (14, 19) and (c5["S"][0], c5["S"][-1]) == (5, 8)
    assert round((1 - t["C5"]["S"]["cost_usd"]["median"] / t["C5"]["R"]["cost_usd"]["median"]) * 100) == 15
    assert t["C5"]["S"]["billed_input"]["median"] < t["C5"]["R"]["billed_input"]["median"] / 2
    for doc in (DOC, SKILL):
        assert "down 15%" in doc and "more than halved" in doc
    assert "Median 17 turns against 6" in SKILL and "13 to 18 calls in as many turns" in SKILL
    calls = sorted(r["calls"] for r in E3["per_run"] if (r["case"], r["arm"]) == ("C5", "R") and not r.get("pilot") and r["turns"] > 5)
    assert (calls[0], calls[-1]) == (13, 18)
    c4 = {a: (t["C4"][a]["turns"]["min"], t["C4"][a]["turns"]["max"]) for a in "RS"}
    assert c4 == {"R": (3, 8), "S": (3, 3)}
    share = {c: sorted(E3["replay"][c]["per_run_exact_share"]) for c in ("C1", "C2")}
    assert [round(share[c][i] * 100) for c in ("C1", "C2") for i in (0, -1)] == [11, 40, 5, 8]
    assert "exactly in 11% to 40% of the calls of a run on C1 and in 5% to 8% on C2" in DOC
    assert all(E3["replay"][c]["none"] == 0 for c in ("C1", "C2"))


def test_the_post_hoc_table_of_e3_is_that_of_its_file():
    for case, arms in IND["cases"].items():
        for arm, name in (("R", "without"), ("S", "strategy")):
            a = arms[arm]
            assert (f"| {case} | {name} | {_n(a['turns_median'])} | {_n(a['fewest_turns_median'])} | "
                    f"{round(a['share_that_could_have_been_made_earlier'] * 100)}% |") in DOC
            assert a["turns_median"] == E3["e3"][case][arm]["turns"]["median"]
    c5 = IND["cases"]["C5"]
    assert all(r["fewest_turns"] == 4 for a in "RS" for r in c5[a]["per_run"])
    assert all(round(c5[a]["share_depending_on_an_earlier_result"], 1) == 0.7 for a in "RS")


def test_e1_beside_e3_is_what_the_two_files_say():
    med = lambda res, key, arm: [_n(res[key][c][arm]["turns"]["median"]) for c in ("C4", "C2", "C1")]  # noqa: E731
    r1, r3, s1, s3 = med(E1, "e1", "R"), med(E3, "e3", "R"), med(E1, "e1", "S"), med(E3, "e3", "S")
    assert f"a median of {r1[0]} turns on C4, {r1[1]} on C2 and {r1[2]} on C1; `gemini-3.8-flash` {r3[0]}, {r3[1]} and {r3[2]}" in DOC
    assert f"With it, {s1[0]}, {s1[1]} and {s1[2]} against {s3[0]}, {s3[1]} and {s3[2]}" in DOC
