"""Structural compression keeps every value and writes field names once."""
import json

from tracetrim.structural import clean, structural, to_toon


def test_clean_drops_only_empty_values():
    assert clean({"a": None, "b": "", "c": [], "d": {}, "e": 0, "f": False, "g": [None, 1]}) == \
        {"e": 0, "f": False, "g": [1]}


def test_uniform_arrays_become_a_table_with_names_once():
    t = to_toon({"items": [{"keyword": "spa", "volume": 1600}, {"keyword": "terme, libere", "volume": 2400}]})
    assert t.splitlines() == ['items[2]{keyword,volume}:', '  spa,1600', '  "terme, libere",2400']


def test_ambiguous_strings_are_quoted():
    t = to_toon({"code": "007", "flag": "true", "note": "a: b", "ok": "plain text"})
    assert 'code: "007"' in t and 'flag: "true"' in t and 'note: "a: b"' in t and "ok: plain text" in t


def test_every_value_survives_and_tokens_drop():
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")
    payload = {"status": "ok", "items": [{"id": f"ch_{i}", "amount": 100 * i, "currency": "eur"} for i in range(20)],
               "meta": {"page": 1, "tags": ["a", "b"]}}
    raw = json.dumps(payload, indent=2)
    out = structural(raw)
    for i in range(20):
        assert f"ch_{i},{100 * i},eur" in out
    assert len(enc.encode(out)) < len(enc.encode(raw)) * 0.6


def test_prose_passes_through():
    assert structural("Error: quota exceeded") == "Error: quota exceeded"
