"""A list element left with no field keeps its place (1.1.2).

Up to 1.1.1 the projection dropped an element whose fields were all removed: the positions after it shifted,
while `expand` reads positions on the original list, so `items[1].memo` could bring back another record's
memo. Now the element stays as an empty object, and the reply to `expand` matches the list the model saw.
"""
import json

import pytest

from tracetrim.auto import AutoTrim
from tracetrim.engine import Engine, Trimmer
from tracetrim.projection import Projection, project
from tracetrim.runtime import Policy


def memo(name):
    # Long, so that the trimmed result plus its line costs less than the whole one.
    return f"memo of {name} " * 40


def policy(keep, seen, tool="list", **config):
    projection = Projection("prudent", "full", {tool: set(keep)}, {tool: set(seen)})
    return Policy({"compact": True, "tools": {tool: {"trim": "prudent", "expand": True}}, **config}, projection)


def records(ids, missing):
    """One record per id; those at the positions in `missing` have no id (nothing the projection keeps)."""
    return [({} if n in missing else {"id": i}) | {"memo": memo(i)} for n, i in enumerate(ids)]


@pytest.mark.parametrize("missing", [{0}, {2}, {1, 2}, {4}, {0, 1, 3}])
def test_every_visible_record_expands_to_its_own_value(missing):
    ids = ["a", "b", "c", "d", "e"]
    t = Trimmer(policy({"items[].id"}, {"items[].id", "items[].memo"}))
    obs = t.observe("list", json.dumps({"items": records(ids, missing)}))
    seen = json.loads(obs.text.split("\n[trimmed]")[0])["items"]
    assert len(seen) == len(ids)
    for n, item in enumerate(seen):
        assert item == ({} if n in missing else {"id": ids[n]})
        reply = json.loads(t.expand(obs.call_id, [f"items[{n}].memo"]))
        assert [r for r in reply["items"] if r] == [{"memo": memo(ids[n])}]


def test_the_case_of_the_technical_review():
    # docs/REPORT_TECNICO_CLAUDE_IT.md, section 1: in 1.1.1 the model saw [{"id":"second"}] and
    # items[0].memo brought back the memo of "first".
    t = Trimmer(policy({"items[].id"}, {"items[].id", "items[].memo"}))
    raw = {"items": [{"memo": "first " * 100}, {"id": "second", "memo": "second " * 100}]}
    obs = t.observe("list", json.dumps(raw))
    assert obs.text.splitlines()[0] == '{"items":[{},{"id":"second"}]}'
    reply = json.loads(t.expand(obs.call_id, ["items[1].memo"]))
    assert [r["memo"].split()[0] for r in reply["items"] if r] == ["second"]


def test_nested_lists_keep_their_places_too():
    keep = {"orders[].id", "orders[].lines[].sku"}
    seen = keep | {"orders[].lines[].note"}
    orders = [{"id": "o1", "lines": [{"note": memo("o1-0")}, {"sku": "S2", "note": memo("o1-1")}]},
              {"id": "o2", "lines": [{"sku": "S3", "note": memo("o2-0")}, {"note": memo("o2-1")},
                                     {"sku": "S5", "note": memo("o2-2")}]}]
    t = Trimmer(policy(keep, seen, tool="orders"))
    obs = t.observe("orders", json.dumps({"orders": orders}))
    seen_orders = json.loads(obs.text.split("\n[trimmed]")[0])["orders"]
    assert seen_orders == [{"id": "o1", "lines": [{}, {"sku": "S2"}]},
                           {"id": "o2", "lines": [{"sku": "S3"}, {}, {"sku": "S5"}]}]
    reply = json.loads(t.expand(obs.call_id, ["orders[1].lines[2].note"]))
    assert reply["orders"][-1]["lines"][-1] == {"note": memo("o2-2")}


def test_a_list_with_nothing_left_still_goes_away():
    # No position can be read on a list the model does not see: nothing to keep.
    t = Trimmer(policy({"name"}, {"name", "items[].memo"}))
    obs = t.observe("list", json.dumps({"name": "x", "items": [{"memo": memo("a")}, {"memo": memo("b")}]}))
    assert obs.text.split("\n[trimmed]")[0] == '{"name":"x"}'


def test_the_automatic_path_keeps_the_places(tmp_path):
    tt = AutoTrim(tmp_path, min_runs=2)
    items = [{"title": "Alpha guide to spas", "body": memo("alpha")},
             {"body": memo("beta")},
             {"title": "Gamma thermal baths", "body": memo("gamma")}]
    for _ in range(2):
        tt.output("search", {"q": "spa"}, json.dumps({"items": items}))
        tt.done("Alpha guide to spas and Gamma thermal baths")
    out = tt.output("search", {"q": "spa"}, json.dumps({"items": items}))
    body, line = out.split("\n[trimmed] ")
    assert json.loads(body)["items"] == [{"title": "Alpha guide to spas"}, {}, {"title": "Gamma thermal baths"}]
    call_id = line.split()[0].removeprefix("call_id=")
    reply = json.loads(tt.expand({"call_id": call_id, "fields": ["items[2].body"]}))
    assert [r for r in reply["items"] if r] == [{"body": memo("gamma")}]


# -- the previous behaviour, for the rest of 1.x (STABILITY.md) ------------------------------------------------

def test_the_config_can_restore_the_previous_behaviour():
    t = Trimmer(policy({"items[].id"}, {"items[].id", "items[].memo"}, keep_positions=False))
    obs = t.observe("list", json.dumps({"items": records(["a", "b"], {0})}))
    assert obs.text.splitlines()[0] == '{"items":[{"id":"b"}]}'


def test_the_engine_option_overrides_the_config():
    engine = Engine(policy=policy({"items[].id"}, {"items[].id", "items[].memo"}), keep_positions=False)
    assert engine.policy.keep_positions is False
    assert Engine(policy=policy({"items[].id"}, {"items[].id"})).policy.keep_positions is True


def test_the_proxy_has_an_option_for_it(tmp_path, monkeypatch):
    from tracetrim import cli, proxy
    seen = []

    async def fake_serve(o):
        seen.append(o)
    monkeypatch.setattr(proxy, "serve", fake_serve)
    base = ["proxy", "--upstream-cmd", "python -m server", "--auto", str(tmp_path)]
    cli.main(base)
    cli.main(base + ["--no-keep-positions"])
    assert [o.keep_positions for o in seen] == [None, False]


def test_the_studies_measure_as_they_did():
    # Measures and experiments call the projection directly: they keep dropping emptied elements, so that
    # every number of the paper and of docs/measures-v1.md is computed as it was.
    payload = {"items": [{"memo": "x"}, {"id": "b", "memo": "y"}]}
    assert project(payload, {"items[].id"}) == {"items": [{"id": "b"}]}
    p = Projection("prudent", "full", {"t": {"items[].id"}}, {"t": {"items[].id", "items[].memo"}})
    assert json.loads(p.apply("t", json.dumps(payload), keep_empty=True, compact=True)) == {"items": [{"id": "b"}]}


# -- with TOON (1.1.3) -----------------------------------------------------------------------------------------
# `structural.clean()` dropped the empty and null elements of every list before the TOON encoding, the `{}` left
# by the projection included: the rows the model read shifted again, while `expand` reads the original.

def toon_rows(text):
    """The rows of the TOON list the model reads: what follows `- `, or `-` alone for an empty element."""
    return [line.strip() for line in text.split("\n[trimmed]")[0].splitlines()[1:]]


def toon_policy(keep, seen, tool="list", **config):
    return policy(keep, seen, tool, structural="toon", **config)


def test_with_toon_the_visible_row_expands_to_its_own_record():
    # docs/REPORT_TECNICO_CLAUDE_IT.md, section 1a: in 1.1.2 the model saw the single row `second`, and
    # items[0].memo brought back the memo of "first".
    t = Trimmer(toon_policy({"items[].id"}, {"items[].id", "items[].memo"}))
    raw = {"items": [{"memo": "first " * 100}, {"id": "second", "memo": "second " * 100}]}
    obs = t.observe("list", json.dumps(raw))
    assert toon_rows(obs.text) == ["-", "- id: second"]
    reply = json.loads(t.expand(obs.call_id, ["items[1].memo"]))
    assert [r["memo"].split()[0] for r in reply["items"] if r] == ["second"]


@pytest.mark.parametrize("missing", [{0}, {2}, {1, 2}, {4}])
def test_with_toon_every_visible_record_expands_to_its_own_value(missing):
    ids = ["a", "b", "c", "d", "e"]
    t = Trimmer(toon_policy({"items[].id"}, {"items[].id", "items[].memo"}))
    obs = t.observe("list", json.dumps({"items": records(ids, missing)}))
    rows = toon_rows(obs.text)
    assert rows == ["-" if n in missing else f"- id: {i}" for n, i in enumerate(ids)]
    for n in range(len(ids)):
        if n in missing:
            continue
        reply = json.loads(t.expand(obs.call_id, [f"items[{n}].memo"]))
        assert [r for r in reply["items"] if r] == [{"memo": memo(ids[n])}]


def test_with_toon_a_null_element_of_the_original_keeps_its_place():
    t = Trimmer(toon_policy({"items[].id"}, {"items[].id", "items[].memo"}))
    obs = t.observe("list", json.dumps({"items": [None, {"id": "b", "memo": memo("b")}, {"id": "c", "memo": memo("c")}]}))
    assert toon_rows(obs.text) == ["- null", "- id: b", "- id: c"]
    reply = json.loads(t.expand(obs.call_id, ["items[2].memo"]))
    assert [r for r in reply["items"] if r] == [{"memo": memo("c")}]


def test_with_toon_nested_lists_keep_their_places():
    keep = {"orders[].id", "orders[].lines[].sku"}
    t = Trimmer(toon_policy(keep, keep | {"orders[].lines[].note"}, tool="orders"))
    orders = [{"id": "o1", "lines": [{"note": memo("x")}, {"sku": "S2", "note": memo("s2")}]}]
    obs = t.observe("orders", json.dumps({"orders": orders}))
    assert obs.text.split("\n[trimmed]")[0] == "orders[1]:\n  - id: o1\n    lines[2]:\n      -\n      - sku: S2"
    reply = json.loads(t.expand(obs.call_id, ["orders[0].lines[1].note"]))
    assert reply["orders"][0]["lines"][-1] == {"note": memo("s2")}


def test_with_toon_the_automatic_path_keeps_the_places(tmp_path):
    tt = AutoTrim(tmp_path, min_runs=2, structural="toon")
    items = [{"body": memo("alpha")}, {"title": "Gamma thermal baths", "body": memo("gamma")}]
    for _ in range(2):
        tt.output("search", {"q": "spa"}, json.dumps({"items": items}))
        tt.done("Gamma thermal baths")
    out = tt.output("search", {"q": "spa"}, json.dumps({"items": items}))
    assert toon_rows(out) == ["-", "- title: Gamma thermal baths"]
    call_id = out.split("\n[trimmed] ")[1].split()[0].removeprefix("call_id=")
    reply = json.loads(tt.expand({"call_id": call_id, "fields": ["items[1].body"]}))
    assert [r for r in reply["items"] if r] == [{"body": memo("gamma")}]


def test_with_toon_the_previous_behaviour_comes_back_with_the_same_option():
    t = Trimmer(toon_policy({"items[].id"}, {"items[].id", "items[].memo"}, keep_positions=False))
    obs = t.observe("list", json.dumps({"items": records(["a", "b"], {0})}))
    assert obs.text.split("\n[trimmed]")[0] == "items[1]{id}:\n  b"


def test_the_studies_encode_toon_as_they_did():
    # experiments/replay.py calls structural() directly: it keeps dropping empty and null elements.
    from tracetrim.structural import clean, structural
    payload = {"items": [None, {}, {"id": "b"}], "tags": ["x", "", None]}
    assert clean(payload) == {"items": [{"id": "b"}], "tags": ["x"]}
    assert structural(json.dumps(payload)) == "items[1]{id}:\n  b\ntags[1]: x"
