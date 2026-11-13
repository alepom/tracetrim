"""The baselines of docs/baselines.md (experiments/baselines.py): each arm does
what the pre-registration says, the static filter never reads a trace, and the
list of metadata keys in the code is the one that was committed first."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments import baselines as B  # noqa: E402
from tracetrim.measure import removes  # noqa: E402
from tracetrim.projection import learn  # noqa: E402
from tracetrim.traces import load  # noqa: E402
from tracetrim.usage import analyse, leaves, parse_output  # noqa: E402

PAYLOAD = {
    "version": "0.1", "status_code": 20000, "status_message": "Ok.", "time": "0.12 sec.", "cost": 0.01,
    "object": "list", "has_more": False, "next_page": None, "request_id": "req_1",
    "data": [
        {"id": "in_1", "object": "invoice", "status": "paid", "amount_due": 2900, "paid": False,
         "discount": None, "description": "", "lines": [], "metadata": {}, "count": 0,
         "customer": {"id": "cus_1", "livemode": False, "_links": {"self": "/c/1"}}},
        {"id": "in_2", "object": "invoice", "status": "open", "amount_due": 4900, "paid": True,
         "discount": None, "description": " ", "lines": [{"note": None}], "metadata": {"k": "v"}, "count": 3,
         "customer": {"id": "cus_2", "livemode": False}},
    ],
}


def text(payload):
    return json.dumps(payload, indent=2)


def test_minified_removes_nothing():
    out = B.minified(text(PAYLOAD))
    assert json.loads(out) == PAYLOAD
    assert ", " not in out and '": ' not in out and "\n" not in out   # no separator spaces, no indentation
    assert len(out) < len(text(PAYLOAD))
    assert B.minified("an error, not JSON") == "an error, not JSON"


def test_static_removes_metadata_keys_and_empties_and_keeps_content():
    out = json.loads(B.static(text(PAYLOAD)))
    assert set(out) == {"data"}                         # `data` is the payload: never a rule
    a, b = out["data"]
    assert a == {"id": "in_1", "status": "paid", "amount_due": 2900, "paid": False, "count": 0,
                 "customer": {"id": "cus_1"}}           # `_links`, `livemode` gone; customer kept
    assert b["description"] == " " and b["count"] == 3 and b["metadata"] == {"k": "v"}
    assert "lines" not in b                             # emptied by the filter, then removed
    assert "object" not in a and "discount" not in a and "lines" not in a
    # Compact, and nothing left to remove: the filter is idempotent.
    assert B.static(B.static(text(PAYLOAD))) == B.static(text(PAYLOAD))
    assert ", " not in B.static(text(PAYLOAD))


def test_static_keys_are_case_insensitive_at_any_depth():
    out = json.loads(B.static(text({"Version": 1, "a": {"b": [{"ETag": "x", "keep": 1}]}})))
    assert out == {"a": {"b": [{"keep": 1}]}}


def test_static_empty_result_keeps_the_kind_and_leaves_prose_alone():
    assert B.static(text([None, "", {}, []])) == "[]"
    assert B.static(text({"version": 2, "x": None})) == "{}"
    assert B.static("Error: rate limited") == "Error: rate limited"
    assert B.static("") == ""


def test_static_rules_can_be_switched_off_for_the_diagnostic():
    p = text({"object": "x", "n": None, "k": 1})
    assert json.loads(B.static(p, empties=False)) == {"n": None, "k": 1}
    assert json.loads(B.static(p, keys=False)) == {"object": "x", "k": 1}


def test_the_static_filter_never_reads_a_trace(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("the static filter read a trace or its usage")
    for mod, name in ((B, "load"), (B, "analyse"), (B, "learn")):
        monkeypatch.setattr(mod, name, forbidden)
    import tracetrim.traces
    import tracetrim.usage
    monkeypatch.setattr(tracetrim.traces, "load", forbidden)
    monkeypatch.setattr(tracetrim.usage, "analyse", forbidden)
    B.static(text(PAYLOAD))
    B.static_payload(PAYLOAD)
    # Its only inputs are the payload and the two diagnostic switches: no tool, no trace.
    import inspect
    assert list(inspect.signature(B.static).parameters) == ["output", "keys", "empties"]
    assert list(inspect.signature(B.static_payload).parameters) == ["node", "keys", "empties"]


def test_the_key_list_is_the_one_pre_registered():
    doc = (ROOT / "docs" / "baselines.md").read_text(encoding="utf-8")
    section = doc.split("**(b) Generic metadata keys.**", 1)[1].split("**(a) Empty values.**", 1)[0]
    rows = [r for r in section.splitlines() if r.startswith("| ") and not r.startswith("| Group")]
    names = {n for r in rows for n in re.findall(r"`([^`]+)`", r.split("|")[2])}
    assert names == set(B.STATIC_KEYS)


def write_run(path, outputs, answer):
    """A run: two calls of one tool, then the answer quoting some values."""
    lines = [{"kind": "prompt", "text": "system"}, {"kind": "prompt", "text": "Find the invoices."}]
    for i, o in enumerate(outputs):
        lines.append({"kind": "call", "tool": "list_invoices", "arguments": {"page": i}, "output": text(o)})
    lines.append({"kind": "message", "text": answer})
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    return load(path)


def invoices(seed):
    return {"object": "list", "has_more": False, "data": [
        {"id": f"in_{seed}{j}", "object": "invoice", "total": 1000 + 111 * seed + j, "memo": f"Quarterly plan {seed}{j}",
         "footer": f"Thank you for your business number {seed}{j}", "paid": True, "discount": None} for j in range(2)]}


def test_learned_and_combined_arms_and_misses(tmp_path):
    runs = [write_run(tmp_path / f"A{s}.jsonl", [invoices(s)], f"Totale {1000 + 111 * s}; Quarterly plan {s}0")
            for s in range(1, 4)]
    study1 = [analyse(t, normalize=False, comma_grouping=False) for t in runs]
    proj = learn([analyse(t) for t in runs[1:]])
    c = study1[0][0]
    learned = B.leaf_paths(proj.apply(c.tool, c.output, keep_empty=True, compact=True))
    # The footer was never used: learned removes it; static does not.
    assert "data[].footer" not in learned and "data[].footer" in B.leaf_paths(B.static(c.output))
    # Unjudgeable fields (booleans, nulls) survive the prudent projection, and the static filter
    # removes the null on top of it.
    assert {"data[].paid", "data[].discount"} <= learned
    combined = B.leaf_paths(B.static(proj.apply(c.tool, c.output, keep_empty=True, compact=True)))
    assert "data[].discount" not in combined and "data[].paid" in combined
    # Misses: the generic form agrees with Study 1's for the learned arm; lossless arms never miss.
    assert c.used >= {"data[].total", "data[].memo"}
    out = proj.apply(c.tool, c.output, keep_empty=True, compact=True)
    assert bool(B.removed_used(c, out, c.used)) == any(removes(proj, c.tool, f) for f in c.used)
    assert not B.removed_used(c, B.minified(c.output), c.used)
    assert not B.removed_used(c, c.output, c.used)


def test_a_static_rule_on_a_used_field_is_a_miss(tmp_path):
    t = write_run(tmp_path / "A1.jsonl", [{"data": [{"id": "x1", "object": "subscription", "cost": 4321}]}],
                  "The subscription costs 4321.")
    c = analyse(t, normalize=False, comma_grouping=False)[0]
    assert B.removed_used(c, B.static(c.output), c.used) == {"data[].object", "data[].cost"}


def test_released_case_reads_as_study_1(traces, monkeypatch):
    """On a released case the inputs must match Study 1's results exactly."""
    monkeypatch.setattr(B, "STUDY_TRACES", traces)
    from tracetrim.measure import tokenizer
    r = B.measure("C5", tokenizer())
    a = json.loads((ROOT / "results" / "phase_a.json").read_text())["cases"]["C5"]
    assert r["input_check"]["calls"] == a["calls"] and r["tokens"]["raw"] == a["loo_full"]["raw"]
    assert r["misses"]["minified"]["calls"] == 0
    assert r["tokens"]["raw"] > r["tokens"]["minified"] > r["tokens"]["static"]
