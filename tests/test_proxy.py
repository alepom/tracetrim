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
    assert "location_code" not in body and line.startswith("[trimmed] call_id=c1 hidden: ")
    assert "items[].location_code" in line
    back = json.loads(t.expand("c1", ["items[].location_code"]))
    assert back == {"items": [{"location_code": 2380}, {"location_code": 2380}]}


def test_no_config_means_pass_through_and_unknown_call_id_is_an_error():
    assert Trimmer(None).output("search_volume", json.dumps(VOLUMES)) == json.dumps(VOLUMES)
    assert Trimmer(policy()).expand("c9", ["x"]).startswith("Error")


def test_without_expand_the_output_is_trimmed_but_not_annotated():
    out = Trimmer(policy(expand=False)).output("search_volume", json.dumps(VOLUMES))
    assert "[trimmed]" not in out and "location_code" not in out
