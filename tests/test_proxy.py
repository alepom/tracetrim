"""The proxy's trimming logic, without MCP: what a client receives."""
import json

from tracetrim import analyse, learn
from tracetrim.proxy import Trimmer
from tracetrim.runtime import Policy

from test_core import VOLUMES, trace


def policy(expand=True):
    p = learn([analyse(trace("t", "spa weekend 1600"))], "prudent")
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


def test_upstream_tools_named_like_ours_are_prefixed():
    from tracetrim.proxy import route_names
    routes = route_names({"a": ["expand", "search"], "b": ["tracetrim_done"]})
    assert routes == {"a__expand": ("a", "expand"), "search": ("a", "search"),
                      "b__tracetrim_done": ("b", "tracetrim_done")}


def test_empty_lists_survive_at_run_time():
    out = json.loads(policy().apply("search_volume", json.dumps({"items": [], "status_message": "Ok."})))
    assert out.get("items") == []


def test_no_config_means_pass_through_and_unknown_call_id_is_an_error():
    assert Trimmer(None).output("search_volume", json.dumps(VOLUMES)) == json.dumps(VOLUMES)
    assert Trimmer(policy()).expand("c9", ["x"]).startswith("Error")


def test_without_expand_the_output_is_trimmed_but_not_annotated():
    out = Trimmer(policy(expand=False)).output("search_volume", json.dumps(VOLUMES))
    assert "[trimmed]" not in out and "location_code" not in out
