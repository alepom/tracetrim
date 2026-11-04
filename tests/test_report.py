"""The report: same traces, same numbers, same suggestions, in both languages."""
import json

from tracetrim import analyse
from tracetrim.report import compute, render_html, suggestions

from test_core import VOLUMES, trace


def _data():
    traces = [trace(f"t{i}", "spa weekend 1600, thermal baths 2400") for i in range(3)]
    traces[2].events.insert(2, traces[2].events[1].__class__(
        "call", tool="search_volume", text=traces[2].events[1].text, output="Error: quota exceeded"))
    return compute([t.name for t in traces], [analyse(t) for t in traces])


def test_fields_get_a_decision_and_tokens():
    f = _data()["tools"]["search_volume"]["fields"]
    assert f["items[].search_volume"]["status"] == "used"
    assert f["items[].keyword"]["status"] == "key"
    assert f["items[].cpc"]["status"] == "unjudged"
    assert f["status_message"]["status"] == "unjudged"
    assert all(v["tokens"] > 0 for v in f.values())


def test_rules_fire_with_their_numbers_in_both_languages():
    d = _data()
    s = {x["rule"]: x for x in suggestions(d)}
    assert "few-traces" in s and "3 traces" in s["few-traces"]["en"] and "3 tracce" in s["few-traces"]["it"]
    assert "errors" in s and "1 of 4" in s["errors"]["en"] and "1 chiamata su 4" in s["errors"]["it"]
    assert "duplicates" in s


def test_html_is_bilingual_and_deterministic_apart_from_the_date():
    d = _data()
    a = render_html(d, suggestions(d))
    b = render_html(d, suggestions(d))
    assert a == b
    assert 'lang="en"' in a and 'lang="it"' in a
    json.dumps(d)            # the JSON twin must serialise
