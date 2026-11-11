"""Measures that change nothing: list elements reused (tracetrim.lists) and argument cardinality (tracetrim.arguments)."""
import json
import sys
from pathlib import Path

from tracetrim import Event, Trace, analyse
from tracetrim.arguments import cardinality
from tracetrim.auto import AutoTrim
from tracetrim.cli import main
from tracetrim.lists import element_usage, outer_lists, size_arguments, summarise

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from experiments.simulated import orders  # noqa: E402

RESULTS = {"total_count": 4, "items": [
    {"rank": 1, "title": "Thermal baths of the northern valley", "tags": ["wellness resort", "family offer"]},
    {"rank": 2, "title": "Weekend spa guide for couples", "tags": []},
    {"rank": 3, "title": "Mountain hotels with heated pools", "tags": ["winter season"]},
    {"rank": 4, "title": "Cheap flights and hostels", "tags": []}],
    "related": ["spa near me", "thermal water benefits"]}


def trace(answer, arguments=None, payload=RESULTS, turns=False):
    events = [Event("prompt", text="Find pages about spas."),
              Event("call", tool="search", text=json.dumps(arguments or {"query": "spa", "limit": 4}),
                    output=json.dumps(payload)),
              Event("message", text=answer)]
    if turns:
        for n, e in enumerate(events):
            e.turn = n - 1
    return Trace("t", events)


def test_the_positions_of_the_elements_reused_are_recorded_per_outer_list():
    [c] = element_usage(trace("Read 'Thermal baths of the northern valley' and 'Mountain hotels with heated pools'."))
    assert c["tool"] == "search" and c["size_arguments"] == {"limit": 4}
    assert c["lists"] == {"items[]": {"received": 4, "reused": [0, 2]}, "related[]": {"received": 2, "reused": []}}


def test_an_element_is_reused_through_any_of_its_values_nested_ones_included():
    [c] = element_usage(trace("One page is tagged winter season."))
    assert c["lists"]["items[]"]["reused"] == [2]                       # by a tag, a list inside the element
    assert "items[].tags[]" not in c["lists"]                          # inner lists are not measured on their own


def test_values_from_the_arguments_and_small_numbers_do_not_make_an_element_reused():
    [c] = element_usage(trace("The query was spa; ranks 1, 2 and 3 looked fine."))
    assert c["lists"]["items[]"]["reused"] == []
    [late] = element_usage(trace("Weekend spa guide for couples", turns=True))
    assert late["lists"]["items[]"]["reused"] == [1]


def test_a_root_list_and_no_list_at_all():
    [c] = element_usage(trace("Weekend spa guide for couples", payload=RESULTS["items"]))
    assert c["lists"] == {"[]": {"received": 4, "reused": [1]}}
    assert outer_lists({"a": {"b": [1, 2]}, "c": 3}) == {"a.b[]": [1, 2]} and outer_lists("text") == {}
    [none] = element_usage(trace("nothing", payload={"status": "ok"}))
    assert none["lists"] == {}


def test_size_arguments_are_found_by_name_at_any_depth():
    assert size_arguments({"limit": 10, "query": "spa", "page": {"per_page": 50, "cursor": "abc"},
                           "filters": [{"top": 3}], "depth": "deep", "count": True}) == {
        "limit": 10, "page.per_page": 50, "filters[].top": 3}       # by name only: a text or a flag is not a size


def test_the_summary_says_how_much_was_asked_received_and_reused_and_whether_from_the_top():
    per_trace = [element_usage(trace("Thermal baths of the northern valley; Weekend spa guide for couples")),
                 element_usage(trace("Mountain hotels with heated pools", {"query": "spa", "limit": 10})),
                 element_usage(trace("Nothing relevant."))]
    s = summarise(per_trace)["search"]
    assert s["calls"] == 3 and s["size_arguments"] == {"limit": {"calls": 3, "median": 4, "max": 10}}
    items = s["lists"]["items[]"]
    assert items["received"] == {"median": 4, "max": 4} and items["reused"] == {"median": 1, "max": 2}
    assert items["calls_with_reuse"] == 2 and items["calls_reused_from_top"] == 1       # [0, 1] yes, [2] no
    assert items["deepest_reused"] == {"median": 2.5, "max": 3}
    assert s["lists"]["related[]"]["deepest_reused"] == {"median": None, "max": None}


def test_measuring_elements_changes_neither_usage_nor_what_the_model_receives(tmp_path):
    t = trace("Thermal baths of the northern valley")
    before = analyse(t)
    element_usage(t)
    assert analyse(t) == before
    # In automatic mode rows are never cut: a filtered result has every element.
    tt = AutoTrim(tmp_path, min_runs=3)
    for k in (1, 2, 3, 4):
        raw = orders.output(f"cus_{k:03d}", 5)
        sent = tt.output("list_orders", {"customer": f"cus_{k:03d}", "limit": 5}, raw)
        tt.done(f"The newest order is {json.loads(raw)['data'][0]['id']}.")
    assert "[trimmed]" in sent and len(json.loads(sent.split("\n[trimmed] ")[0])["data"]) == 5
    report = json.loads((tmp_path / "report.json").read_text())
    data = report["lists"]["list_orders"]["lists"]["data[]"]
    assert report["lists"]["list_orders"]["size_arguments"]["limit"] == {"calls": 4, "median": 5.0, "max": 5}
    assert data["received"] == {"median": 5.0, "max": 5} and data["reused"] == {"median": 1.0, "max": 1}
    assert data["calls_reused_from_top"] == data["calls_with_reuse"] == 4
    assert "List elements reused" in (tmp_path / "report.html").read_text()


def test_argument_cardinality_counts_values_and_keeps_none():
    def call(tool, **arguments):
        return Event("call", tool=tool, text=json.dumps(arguments), output="{}")
    traces = [Trace("a", [call("search", query="spa", type="organic", limit=10),
                          call("search", query="thermal baths", type="organic", limit=10),
                          call("invoice", id="in_1")]),
              Trace("b", [call("search", query="hotel", type="news", filters=[{"field": "lang", "value": "it"}]),
                          call("invoice", id="in_2"), Event("call", tool="search", text="not json", output="{}")])]
    c = cardinality(traces)
    assert c["search"]["calls"] == 3                                   # the unparsable call is not counted
    assert c["search"]["arguments"] == {
        "query": {"calls": 3, "distinct": 3}, "type": {"calls": 3, "distinct": 2}, "limit": {"calls": 2, "distinct": 1},
        "filters": {"calls": 1, "distinct": 1}, "filters[].field": {"calls": 1, "distinct": 1},
        "filters[].value": {"calls": 1, "distinct": 1}}
    assert c["invoice"]["arguments"] == {"id": {"calls": 2, "distinct": 2}}
    assert "spa" not in json.dumps(c) and "in_1" not in json.dumps(c)


def test_the_offline_report_has_both_measures(tmp_path):
    for i, text in enumerate(["Thermal baths of the northern valley", "Weekend spa guide for couples"]):
        events = [{"kind": "call", "tool": "search", "arguments": {"query": "spa", "limit": 4},
                   "output": json.dumps(RESULTS)}, {"kind": "message", "text": text}]
        (tmp_path / f"r{i}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    main(["report", str(tmp_path / "r0.jsonl"), str(tmp_path / "r1.jsonl"), "-o", str(tmp_path / "report.html")])
    d = json.loads((tmp_path / "report.json").read_text())
    assert d["lists"]["search"]["lists"]["items[]"]["calls_with_reuse"] == 2
    assert d["arguments"]["search"]["arguments"]["query"] == {"calls": 2, "distinct": 1}
    assert "adaptive" not in d and "Arguments: distinct values observed" in (tmp_path / "report.html").read_text()
