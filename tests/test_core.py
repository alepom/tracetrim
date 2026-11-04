"""Core behaviour on small synthetic traces.

Each test pins down one decision of the method; most of them come from a
mistake made on real traces before the rule existed.
"""
import json

from tracetrim import Event, Trace, analyse, learn
from tracetrim.measure import leave_one_out

VOLUMES = {
    "status_message": "Ok.",
    "cost": 0.075,
    "items": [
        {"keyword": "spa weekend", "search_volume": 1600, "cpc": 0.5, "location_code": 2380},
        {"keyword": "thermal baths", "search_volume": 2400, "cpc": 0.4, "location_code": 2380},
    ],
}


def trace(name, final_text, payload=VOLUMES):
    return Trace(name, [
        Event("prompt", text="Plan articles about wellness."),
        Event("call", tool="search_volume",
              text=json.dumps({"keywords": ["spa weekend", "thermal baths"]}),
              output=json.dumps(payload)),
        Event("message", text=final_text),
    ])


def test_value_written_after_the_call_is_used():
    [u] = analyse(trace("t", "Write about spa weekend, 1600 searches a month."))
    assert "items[].search_volume" in u.used
    assert "items[].location_code" not in u.used


def test_echo_is_not_use_but_is_kept_as_record_key():
    # The keyword comes from the arguments: it is not information the tool
    # gave. Dropping it, though, leaves volumes nobody can attribute.
    [u] = analyse(trace("t", "Write about spa weekend, 1600 searches a month."))
    assert "items[].keyword" not in u.used
    assert "items[].keyword" in u.echo
    p = learn([[u]], mode="aggressive")
    trimmed = json.loads(p.apply("search_volume", u.output))
    assert trimmed["items"][0] == {"keyword": "spa weekend", "search_volume": 1600}


def test_values_seen_before_the_call_are_not_use():
    t = trace("t", "Volume 1600.")
    t.events[0].text += " Consider volumes around 1600."
    [u] = analyse(t)
    assert "items[].search_volume" not in u.used


def test_generic_values_are_not_judgeable_and_prudent_keeps_them():
    # cpc 0.5 and 0.4: too generic to attribute. Invisible is not unused.
    [u] = analyse(trace("t", "spa weekend 1600"))
    assert "items[].cpc" not in u.judgeable
    assert "items[].cpc" in learn([[u]], mode="prudent").keep["search_volume"]
    assert "items[].cpc" not in learn([[u]], mode="aggressive").keep["search_volume"]


def test_mcp_proxy_source_ignores_the_final_answer():
    [u] = analyse(trace("t", "spa weekend 1600"))
    assert "items[].search_volume" in u.used
    assert "items[].search_volume" not in u.used_by_calls


def test_apply_leaves_prose_and_unknown_tools_alone_but_unescapes_json():
    p = learn([analyse(trace("t", "spa weekend 1600"))])
    assert p.apply("search_volume", "Error: quota exceeded") == "Error: quota exceeded"
    assert p.apply("other_tool", '{"city": "Forl\\u00ec"}') == '{"city": "Forlì"}'


def test_fields_never_seen_pass_through_unless_strict():
    # An API that adds a field must not have it hidden silently.
    p = learn([analyse(trace("t", "spa weekend 1600"))], mode="aggressive")
    new = dict(VOLUMES, items=[dict(VOLUMES["items"][0], seasonality="high")])
    assert json.loads(p.apply("search_volume", json.dumps(new)))["items"][0]["seasonality"] == "high"
    strict = learn([analyse(trace("t", "spa weekend 1600"))], mode="aggressive", unseen="drop")
    assert "seasonality" not in json.loads(strict.apply("search_volume", json.dumps(new)))["items"][0]


def test_saved_projection_lists_what_it_drops_and_loads_back(tmp_path):
    from tracetrim import Projection
    p = learn([analyse(trace("t", "spa weekend 1600"))], mode="aggressive")
    p.save(tmp_path / "p.json")
    d = json.loads((tmp_path / "p.json").read_text())
    assert "items[].cpc" in d["dropped"]["search_volume"]
    q = Projection.load(tmp_path / "p.json")
    assert q.apply("search_volume", json.dumps(VOLUMES)) == p.apply("search_volume", json.dumps(VOLUMES))


def test_leave_one_out_counts_a_miss_when_the_held_out_trace_uses_a_new_field():
    a = analyse(trace("a", "spa weekend 1600"))
    b = analyse(trace("b", "spa weekend 1600, location 2380"))
    rows = leave_one_out(["a", "b"], [a, b])
    assert rows[0].misses == 0           # a uses only what b also uses
    assert rows[1].misses == 1           # b uses location_code, never seen in a
    assert rows[1].aggressive < rows[1].raw


def test_comma_grouped_numbers_match_unless_reproducing_the_studies():
    from tracetrim.usage import keys_of
    assert ("1,600",) in keys_of(1600) and ("1.600",) in keys_of(1600)
    assert ("1,600",) not in keys_of(1600, comma_grouping=False)
