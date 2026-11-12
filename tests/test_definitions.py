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



def test_the_numbers_in_docs_definitions_are_those_the_script_computes():
    """docs/definitions.md is typed prose: its numbers are kept equal to results/v06/definitions.json."""
    if not d.RESULTS.exists():
        pytest.skip("results/v06/definitions.json not generated here")
    res = json.loads(d.RESULTS.read_text())
    text = (d.ROOT / "docs" / "definitions.md").read_text()
    sc, defs = res["scenario_all_server_tools"], res["scenario_all_server_tools"]["definitions"]

    def rng(x, fmt):
        return fmt(x["median"]) if x["min"] == x["max"] else f"{fmt(x['median'])} [{fmt(x['min'])}–{fmt(x['max'])}]"

    pct = lambda v: f"{100 * v:.1f}"                                             # noqa: E731
    for g in res["groups"]:
        if g["arm"] != "R":
            continue
        share = lambda k, f=pct: (f(g[k]["median"]) + "%" + ("" if g[k]["min"] == g[k]["max"]   # noqa: E731
                                  else f" [{f(g[k]['min'])}–{f(g[k]['max'])}]"))
        row = (f"| {g['case']} | {g['model']} | {g['runs']} | {g['tools_offered']['median']:.0f} "
               f"| {g['definitions_per_turn']['median']:,.0f} | {rng(g['turns'], lambda v: f'{v:g}')} "
               f"| {rng(g['definitions_per_run'], lambda v: f'{v:,.0f}')} | {share('definitions_share_of_input')} "
               f"| {share('definitions_cached_share', lambda v: f'{100 * v:.0f}')} | {share('definitions_share_of_cost')} "
               f"| {share('definitions_share_of_cost_if_never_cached')} |")
        assert row in text, row
    label = {"cold": "cold start: first turn not cached", "warm": "warm start: first turn cached too",
             "none": "no cache at all", "cold_as_billed": f"cold start, definitions as billed (×{sc['billed_vs_counted']:.2f})",
             "warm_as_billed": f"warm start, definitions as billed (×{sc['billed_vs_counted']:.2f})"}
    two = lambda v: f"{v:.2f}"                                                    # noqa: E731
    for k, name in label.items():
        c = sc["cache"][k]
        row = (f"| {name} | {rng(c['R79_cost_vs_recorded'], two)} | {rng(c['D_cost_vs_R79'], two)} "
               f"| {rng(c['D_input_vs_R79'], two)} | {rng(c['extra_turns_that_cancel_the_gain'], lambda v: f'{v:.1f}')} |")
        assert row in text, row
    c = sc["cache"]["cold"]
    for stated in (f"| R79: the {sc['server_tools']} tools of the server | {defs['all_server_tools']:,} |",
                   f"| the 12 tools of the case, as recorded | {defs['case_tools_12']:,} |",
                   f"| the 11 tools called in at least one run | {defs['tools_used_in_any_run']:,} |",
                   f"| the two meta-tools | {defs['meta_tools_draft']:,} |",
                   f"| D: 11 listed + meta-tools, {defs['hidden_tools']} hidden | {defs['deferred_visible_plus_meta']:,} |",
                   f"| {defs['saved_per_turn']:,} ({100 * defs['saved_per_turn_share']:.0f}%) |",
                   f"at most **{sc['billed_vs_counted']:.2f}** of our count",
                   f"from {c['R79_first_turn_input']['median']:,.0f} to {c['D_first_turn_input']['median']:,.0f} tokens",
                   f"from {c['R79_turn_max']['median']:,.0f} to {c['D_turn_max']['median']:,.0f}",
                   f"{sc['hidden_tool_tokens']['median']:,.0f} tokens at the median "
                   f"({sc['hidden_tool_tokens']['min']:,} to {sc['hidden_tool_tokens']['max']:,})",
                   f"runs of the v0.4 measures (E1, E2, E3; `docs/traces-v04.md`): C1 {res['read']['by_case']['C1']},",
                   f"The two meta-tools weigh {defs['meta_tools_draft']:,} tokens"):
        assert stated in text, stated
    assert f"the {res['read']['runs']} recorded runs" in text
    assert "usd" not in text.lower()
