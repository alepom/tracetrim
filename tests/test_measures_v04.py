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
        assert round(-a["cost_vs_R"] * 100) == cost and r["cost_vs_R"] == 0
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


def test_the_runs_and_the_calls_are_those_of_the_results_and_no_amount_is_published():
    assert len(E1["per_run"]) == 50 and len(E2["per_run"]) == 50
    assert E2["dataforseo"]["calls"] == 813 and "813 calls" in DOC
    # The author's decision (2026-11-12): no amount spent anywhere in what is published.
    import json
    import re
    for name in ("e1", "e2", "e3"):
        text = (ROOT / "results" / "v04" / f"{name}.json").read_text()
        assert "usd" not in text.lower(), name
    assert "usd" not in json.dumps(json.loads((ROOT / "results" / "phase_c.json").read_text())).lower()
    for path in [ROOT / "docs" / "measures-v04.md", ROOT / "CHANGELOG.md", ROOT / "protocol.md",
                 ROOT / "protocol-v04.md", ROOT / "protocol-v04-e3.md", ROOT / "CLAUDE.md"]:
        text = path.read_text()
        # A list price per million tokens is public; an amount in US dollars of our own is not.
        amounts = re.findall(r"[0-9][0-9.,]*\s*USD(?!\s*(?:/\s*1M|per 1M|/day))", text)
        assert not amounts, (path.name, amounts)


def _n(x):
    return f"{x:g}"


def cost(change, arm):
    if arm == "R":
        return "—"
    pct = round(change * 100)
    return f"{'+' if pct > 0 else '−'}{abs(pct)}%"


def test_the_table_of_e3_is_that_of_the_results_file():
    rng = lambda m, pct=False: (f"{round(m['median'] * 100)}% [{round(m['min'] * 100)}–{round(m['max'] * 100)}]" if pct  # noqa: E731
                                else f"{_n(m['median'])} [{_n(m['min'])}–{_n(m['max'])}]")
    assert (E3["runs"], E3["pilot_runs"], E3["provider_calls"], E3["models"]) == (38, 4, 0, ["gemini-3.8-flash"])
    for case, arms in E3["e3"].items():
        for arm, name in (("R", "without"), ("S", "strategy")):
            a = arms[arm]
            line = (f"| {case} | {name} | {a['runs']} | {rng(a['turns'])} | {rng(a['calls'])} | "
                    f"{rng(a['several_calls_share_per_run'], True)} | {round(a['billed_input']['median']):,} | "
                    f"{cost(a['cost_vs_R'], arm)} |")
            assert line in DOC, line
    assert "E3: 42 runs, pilot" in " ".join(DOC.split()) and E3["runs"] + E3["pilot_runs"] == 42


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
    assert round(-t["C5"]["S"]["cost_vs_R"] * 100) == 15
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


# -- the offline recalculations made after E3 -------------------------------------

MODES = json.loads((ROOT / "results" / "v04" / "modes.json").read_text())
OFF = json.loads((ROOT / "results" / "v04" / "switch_off.json").read_text())
ROWS = json.loads((ROOT / "results" / "v04" / "rows.json").read_text())
ROWS_DOC = " ".join((ROOT / "docs" / "rows.md").read_text().split())
ADAPTIVE = " ".join((ROOT / "docs" / "adaptive.md").read_text().split())


def test_what_is_said_of_projections_per_mode_is_what_the_file_says():
    cands = [(t, a) for c in MODES["cases"].values() for t in c["tools"].values() for a in t["arguments"].values()]
    tools = sum(len(c["tools"]) for c in MODES["cases"].values())
    assert (len(cands), tools, MODES["candidates"], MODES["with_a_benefit"]) == (18, 11, 18, 0)
    assert f"{len(cands)} candidate arguments on {tools} tools" in DOC and "**None shows a benefit.**" in DOC
    assert sum(c["runs_evaluated"] for c in MODES["cases"].values()) == 102 and "On the 102 runs" in DOC
    points = [a["points_removed_more_than_general"] for _, a in cands]
    assert max(points) == 3.2 and sum(p < 1 for p in points) == 16 and "is 3.2 points; 16 of 18 add less than one" in DOC
    assert MODES["settings"]["benefit_points"] == 5 and "at least 5 more points" in DOC
    assert sum(a["calls_losing_a_used_field"] > t["general"]["calls_losing_a_used_field"] for t, a in cands) == 5
    assert sum(a["calls_per_profile_min"] < MODES["settings"]["min_profile_calls"] for _, a in cands) == 8
    assert sum(a["new_cases_covered_by_the_general_projection"] for _, a in cands) == 35
    assert "8 candidates have a profile with fewer than three calls, and 35 held-out calls" in DOC
    trends = MODES["cases"]["C1"]["tools"]["kw_data_google_trends_explore"]["arguments"]
    assert sorted(trends) == ["time_range", "type"]
    for a in trends.values():                              # no profile reuses a field the others do not
        assert not any(p["fields_used_here_only"] for p in a["profiles"].values()) and abs(a["points_removed_more_than_general"]) <= 0.1


def test_what_is_said_of_the_switch_off_is_what_the_file_says():
    assert (OFF["sequences"], OFF["switch_offs"]) == (10, {"as_run": 1, "2": 0, "3": 0})
    off = [(s["case"], s["sequence"], s["switched_off"]["as_run"]) for s in OFF["per_sequence"] if s["switched_off"]["as_run"]]
    assert off == [("C1", "base-s1", {"kw_data_google_trends_explore": "run-0009"})]                # the first run of the sequence
    first = next(s for s in OFF["per_sequence"] if s["sequence"] == "base-s1" and s["case"] == "C1")["counts_where_a_result_was_expanded"][0]
    assert (first["filtered"], first["expanded"], first["runs_with_expand"]) == (4, 2, 1)
    for doc in (DOC, ADAPTIVE):
        assert "ten sequences of E2" in doc
    assert "four filtered results of a tool, two of them expanded" in ADAPTIVE and "with 2 or 3 runs asked for, none would have been at that point" in ADAPTIVE


def test_what_is_said_of_rows_is_what_the_file_says():
    r = ROWS["requests"]
    assert (r["expand_requests"], r["gave_nothing_back"], r["by_reply"]) == (38, 10, {"unknown call_id": 8, "no field matches": 2})
    assert (r["unknown_call_id_asking_for_rows"], r["with_an_index_on_a_valid_call_id"], r["of_which_answered_now"]) == (7, 2, 2)
    assert "**The two requests with an index are now answered**" in ROWS_DOC
    assert "the agents called `expand` 38 times. Ten requests gave nothing back" in ROWS_DOC
    assert "| unknown `call_id` | 8 | 7 asked for rows" in ROWS_DOC and "| no field matches | 2 |" in ROWS_DOC
    assert sum(c["runs"] for c in ROWS["elements"].values()) == 112 and "in the 112 runs" in ROWS_DOC
    a, b = ROWS["search_data_api"], ROWS["other_apis"]
    pct = lambda d, k: f"{d[k]} ({round(d[k] / d['calls_with_reuse'] * 100)}%)"                      # noqa: E731
    for label, key in (("Lists with a reused element", None), ("Calls with a reused element", "calls_with_reuse")):
        assert f"| {label} | {a[key or 'lists']} | {b[key or 'lists']} |" in ROWS_DOC
    for label, key in (("… where the reused elements are exactly the first ones", "calls_reused_from_the_top"),
                       ("… where the last element received is reused", "calls_reusing_the_last_element"),
                       ("… that lose a reused element if the list is cut to 5", "calls_losing_a_reused_element_if_cut_to_5"),
                       ("… that lose a reused element if the list is cut to 3", "calls_losing_a_reused_element_if_cut_to_3")):
        assert f"| {label} | {pct(a, key)} | {pct(b, key)} |" in ROWS_DOC
    assert "first ones in 25% of the calls, and a cut to five rows would have removed a reused one in 92%" in DOC
    c1 = ROWS["elements"]["C1"]["lists"]
    share = lambda tool: round(c1[f"{tool} items[]"]["share_of_elements_reused"] * 100)               # noqa: E731
    keyword = [share(t) for t in ("dataforseo_labs_google_keyword_ideas", "dataforseo_labs_google_keyword_suggestions",
                                  "dataforseo_labs_google_ranked_keywords", "kw_data_google_ads_search_volume")]
    assert (min(keyword), max(keyword)) == (58, 89)
    assert (round(ROWS["elements"]["C2"]["lists"]["serp_organic_live_advanced items[]"]["share_of_elements_reused"] * 100),
            share("serp_organic_live_advanced")) == (65, 72)
    video = c1["serp_youtube_organic_live_advanced items[]"]
    assert (video["received_median"], round(video["share_of_elements_reused"] * 100)) == (20, 16)
    thin = [c1[f"{t} items[]"] for t in ("dataforseo_labs_bulk_keyword_difficulty", "dataforseo_labs_search_intent")]
    assert [(m["received_median"], m["calls_with_reuse"], m["calls"]) for m in thin] == [(20, 2, 22), (20, 2, 20)]


def test_the_hint_figures_in_the_docs_are_those_of_e3():
    t = E3["e3"]
    several = {c: round(t[c]["R"]["share_of_turns_with_several_calls"] * 100) for c in ("C5", "C4")}
    earlier = {c: round(IND["cases"][c]["R"]["share_that_could_have_been_made_earlier"] * 100) for c in ("C5", "C4")}
    assert f"({several['C5']}% of the turns with several calls, {earlier['C5']}% of the calls could have been earlier)" in ADAPTIVE
    assert f"already grouped them ({several['C4']}%, {earlier['C4']}%)" in ADAPTIVE
    from tracetrim import turns
    assert (turns.MAX_SEVERAL, turns.MIN_EARLIER, turns.MIN_RUNS) == (0.2, 0.25, 2)
    assert "at most 20% of the turns with calls make several calls, and at least 25% of the calls could have been made earlier, over at least 2 runs" in ADAPTIVE


def test_no_versioned_result_carries_the_spend_of_a_run():
    # Rule of the project: what the tests cost is never published, in any file.
    root = Path(__file__).resolve().parent.parent
    found = []

    def walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                if "usd" in str(k).lower():
                    found.append(f"{where}:{k}")
                walk(v, where)
        elif isinstance(node, list):
            for v in node:
                walk(v, where)

    files = sorted((root / "results").rglob("*.json"))
    assert files                                      # the check reads something
    for f in files:
        walk(json.loads(f.read_text(encoding="utf-8")), f.relative_to(root))
    assert not found, found
