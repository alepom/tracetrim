"""The proxy's trimming logic, without MCP: what a client receives."""
import json

from tracetrim import analyse, learn
from tracetrim.proxy import Trimmer
from tracetrim.runtime import Policy

from test_core import VOLUMES as SMALL
from test_core import trace

# Enough to remove that trimming pays for the line that says what was removed:
# on the four-field items of test_core the whole result is the shorter one,
# and since the simple way for small outputs it is what goes on.
VOLUMES = dict(SMALL, items=[dict(i, remark="checked against the monthly export of the keyword planner",
                                  source="imported from the advertising account of the agency")
                             for i in SMALL["items"]])


def policy(expand=True):
    p = learn([analyse(trace("t", "spa weekend 1600", VOLUMES))], "prudent")
    config = {"format": "tracetrim/config@1", "unescape": True,
              "tools": {"search_volume": {"trim": "prudent", "expand": expand}}}
    return Policy(config, p)


def test_trimmed_output_lists_hidden_groups_and_expand_returns_them():
    t = Trimmer(policy())
    out = t.output("search_volume", json.dumps(VOLUMES))
    body, line = out.rsplit("\n", 1)
    assert "location_code" not in body and line.startswith("[trimmed] call_id=c") and " hidden: " in line
    assert "items[].location_code" in line
    cid = line.split("call_id=")[1].split()[0]
    back = json.loads(t.expand(cid, ["items[].location_code"]))
    assert back == {"items": [{"location_code": 2380}, {"location_code": 2380}]}


def test_call_ids_are_not_guessable_and_the_store_is_bounded():
    from tracetrim.proxy import MAX_ORIGINALS
    t = Trimmer(policy())
    ids = [t.output("search_volume", json.dumps(VOLUMES)).rsplit("call_id=", 1)[1].split()[0]
           for _ in range(MAX_ORIGINALS + 5)]
    assert len(set(ids)) == len(ids) and "c1" not in ids
    assert len(t.originals) == MAX_ORIGINALS and t.expand(ids[0], ["x"]).startswith("Error")


def test_expand_without_a_match_says_what_is_hidden():
    t = Trimmer(policy())
    out = t.output("search_volume", json.dumps(VOLUMES))
    cid = out.rsplit("call_id=", 1)[1].split()[0]
    msg = t.expand(cid, ["no_such_field"])
    assert msg.startswith("Error: no field matches") and "items[].location_code" in msg
    assert "location_code" in t.expand(cid, ["*"])


def test_a_name_clash_after_prefixing_is_refused():
    import pytest
    from tracetrim.proxy import route_names
    with pytest.raises(SystemExit):
        route_names({"a": ["search"], "b": ["search"], "c": ["a__search"]})


def test_upstream_tools_named_like_ours_are_prefixed():
    from tracetrim.proxy import route_names
    routes = route_names({"a": ["expand", "search"], "b": ["tracetrim_done"]})
    assert routes == {"a__expand": ("a", "expand"), "search": ("a", "search"),
                      "b__tracetrim_done": ("b", "tracetrim_done")}


def test_a_result_that_trimming_would_not_shorten_goes_on_whole():
    from tracetrim.contract import FILTERED, FULL_CHEAPER
    t = Trimmer(policy())
    small = t.observe("search_volume", json.dumps(SMALL, indent=2))
    # Two short fields to remove, a line of some twenty tokens to say so: not worth it.
    assert small.exposure == FULL_CHEAPER and small.call_id is None and "[trimmed]" not in small.text
    assert json.loads(small.text) == SMALL and small.text == json.dumps(SMALL)        # whole, re-serialised
    assert small.hidden == ("items[].location_code",) and not t.originals             # nothing kept for expand
    large = t.observe("search_volume", json.dumps(VOLUMES))
    assert large.exposure == FILTERED and large.call_id and t.count(large.text) < t.count(json.dumps(VOLUMES))
    # Without the line there is no fixed cost: the same small result is trimmed.
    bare = Trimmer(policy(expand=False)).observe("search_volume", json.dumps(SMALL))
    assert bare.exposure == FILTERED and "location_code" not in bare.text


def test_empty_lists_survive_at_run_time():
    out = json.loads(policy().apply("search_volume", json.dumps({"items": [], "status_message": "Ok."})))
    assert out.get("items") == []


def test_no_config_means_pass_through_and_unknown_call_id_is_an_error():
    assert Trimmer(None).output("search_volume", json.dumps(VOLUMES)) == json.dumps(VOLUMES)
    assert Trimmer(policy()).expand("c9", ["x"]).startswith("Error")


def test_without_expand_the_output_is_trimmed_but_not_annotated():
    out = Trimmer(policy(expand=False)).output("search_volume", json.dumps(VOLUMES))
    assert "[trimmed]" not in out and "location_code" not in out
