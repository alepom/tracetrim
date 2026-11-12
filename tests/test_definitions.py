"""experiments/definitions.py: the offline measure of what tool definitions weigh (0.6, phase 06a)."""
import json

import pytest

from experiments import definitions as d

PRICE = {"input_per_mtok": 2.0, "cached_input_per_mtok": 0.2, "output_per_mtok": 12.0}


def test_a_tool_is_counted_as_the_harness_sends_it():
    mcp = {"name": "t", "description": "does t", "inputSchema": {"$schema": "x", "type": "object", "properties": {}}}
    rec = {"name": "t", "description": "does t", "parameters": {"type": "object", "properties": {}}, "sha256": "ab"}
    sent = {"type": "function", "name": "t", "description": "does t",
            "parameters": {"type": "object", "properties": {}}, "strict": False}
    assert d.as_sent(mcp) == d.as_sent(rec) == sent
    assert d.count_definitions([mcp], len) == len(json.dumps([sent]))


def test_definitions_are_cached_only_when_the_cache_covers_the_static_head():
    turns = [{"input": 10_000, "cached": 0}, {"input": 30_000, "cached": 9_990},
             {"input": 40_000, "cached": 3_000}, {"input": 50_000, "cached": 40_000}]
    assert d.turn_cache(turns) == ["not_cached", "cached", "unclear", "cached"]
    # an unclear turn is costed as not cached
    assert d.weights(d.turn_cache(turns), PRICE) == pytest.approx(1 + 0.1 + 1 + 0.1)


def test_cost_units_are_uncached_input_tokens():
    assert d.cost_units({"input": 1000, "cached": 400, "output": 10}, PRICE) == pytest.approx(600 + 40 + 60)


def _run(states, first_input=10_000, definitions=1_000, called=("a",)):
    inputs = [first_input + 5_000 * i for i in range(len(states))]
    usage = {"input": sum(inputs), "cached": 0, "output": 100}
    return {"case": "C1", "experiment": "E1", "arm": "R", "tools_called": list(called), "turns": len(states),
            "definitions_per_turn": definitions, "input": usage["input"], "output": usage["output"],
            "first_turn_input": first_input,
            "billed_vs_counted": 0.8, "_states": states, "_units": d.cost_units(usage, PRICE),
            "_weight": d.weights(states, PRICE), "_price": PRICE, "_turn_inputs": inputs}


def test_scenario_replaces_only_the_weight_of_the_definitions(monkeypatch):
    server = [{"name": n, "description": "x" * 40, "inputSchema": {"type": "object", "properties": {}}}
              for n in ("a", "b", "c", "d")]
    monkeypatch.setitem(d.CASE_TOOLS, "C1", ["a", "b"])
    d_case = d.count_definitions(server[:2], len)
    runs = [_run(["not_cached", "cached", "cached"], definitions=d_case),
            _run(["cached", "cached"], definitions=d_case, called=("a", "b"))]
    sc = d.scenario(runs, server, len)
    defs = sc["definitions"]
    assert sc["counterfactual"] is True and sc["server_tools"] == 4 and defs["hidden_tools"] == 2
    assert sc["tools_used"] == {"in_any_run": ["a", "b"], "in_every_run": ["a"], "offered_never_called": []}
    assert defs["saved_per_turn"] == defs["all_server_tools"] - defs["deferred_visible_plus_meta"]
    none, cold, warm = (sc["cache"][k] for k in ("none", "cold", "warm"))
    # more definitions cost more; with a cache they cost less than without; a cold start costs more than a warm one
    assert none["R79_cost_vs_recorded"]["min"] > cold["R79_cost_vs_recorded"]["min"] > 1
    assert cold["R79_cost_vs_recorded"]["median"] > warm["R79_cost_vs_recorded"]["median"] > 1
    # input tokens do not depend on the cache
    assert none["R79_input_vs_recorded"] == cold["R79_input_vs_recorded"] == warm["R79_input_vs_recorded"]
    r = runs[0]
    assert none["R79_turn_max"]["max"] == max(r["_turn_inputs"]) + defs["all_server_tools"] - d_case


def test_scenario_refuses_definitions_that_changed_since_the_runs(monkeypatch):
    server = [{"name": "a", "description": "x", "inputSchema": {"type": "object"}}]
    monkeypatch.setitem(d.CASE_TOOLS, "C1", ["a"])
    with pytest.raises(SystemExit, match="changed since the runs"):
        d.scenario([_run(["cached"], definitions=1)], server, len)


def test_no_amount_in_the_results_file():
    """What the tests cost is never published: the results hold tokens, shares and ratios only."""
    if not d.RESULTS.exists():
        pytest.skip("results/v06/definitions.json not generated here")
    text = d.RESULTS.read_text()
    assert "usd" not in text.lower() and "per_mtok" not in text
    res = json.loads(text)
    assert res["scenario_all_server_tools"]["counterfactual"] is True
    assert all(not k.startswith("_") for r in res["runs"] for k in r)


def test_the_numbers_in_the_draft_protocol_are_those_the_scripts_compute():
    """protocol-v06.md states what was known offline: kept equal to results/v06/definitions.json."""
    from experiments.budget import V06
    if not d.RESULTS.exists():
        pytest.skip("results/v06/definitions.json not generated here")
    res = json.loads(d.RESULTS.read_text())
    sc, text = res["scenario_all_server_tools"], " ".join((d.ROOT / "protocol-v06.md").read_text().split())
    g = next(x for x in res["groups"] if (x["case"], x["experiment"], x["arm"]) == ("C1", "E1", "R"))
    defs, cache = sc["definitions"], sc["cache"]
    med = lambda mode, key: f"{cache[mode][key]['median']:.2f}"                       # noqa: E731
    for stated in (
        f"**{sc['server_tools']} tools: {defs['all_server_tools']:,} tokens**",
        f"the 12 of C1 weigh {defs['case_tools_12']:,}",
        f"at least one recorded run {defs['tools_used_in_any_run']:,}",
        f"definitions are {100 * g['definitions_share_of_input']['median']:.1f}% of the billed input tokens",
        f"{100 * g['definitions_share_of_cost']['median']:.1f}% of the run's cost at the median",
        f"first turn, {sc['billed_vs_counted']:.2f} of it",
        f"cost {med('cold', 'R79_cost_vs_recorded')} times the recorded at a cold start",
        f"{med('warm', 'R79_cost_vs_recorded')} at a warm one",
        f"{med('cold', 'D_cost_vs_R79')} and {med('warm', 'D_cost_vs_R79')} of that at best",
        f"({med('cold_as_billed', 'D_cost_vs_R79')} and {med('warm_as_billed', 'D_cost_vs_R79')} with the definitions as billed)",
        f"input tokens {med('cold', 'D_input_vs_R79')}",
        f"that is {len(sc['tools_used']['in_any_run'])} tools, {defs['hidden_tools']} hidden",
    ):
        assert stated in text, stated
    runs = (sum(V06["pilot"].values()) + len(V06["e4a"]["arms"]) * V06["e4a"]["runs_per_arm"]
            + sum(V06["e4b"]["runs"].values()))
    assert f"| **Total** | **{runs}** |" in text
    # No amount, estimate or cap in the protocol: they stay local.
    assert "usd" not in text.lower() and "$" not in text.replace("$TRACETRIM_V04_TRACES", "")
