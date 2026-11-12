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


def test_every_suggestion_has_an_action_and_manual_ones_carry_evidence():
    d = _data()
    sugg = suggestions(d)
    assert all(s["action"]["kind"] in ("auto", "manual") for s in sugg)
    err = next(s for s in sugg if s["rule"] == "errors")["action"]
    assert err["type"] == "fix_tool_description" and err["examples"] == ["Error: quota exceeded"]


def test_config_from_auto_actions_applies_through_policy(tmp_path):
    from tracetrim.report import build_config
    from tracetrim.runtime import Policy
    d = _data()
    sugg = suggestions(d)
    # Force a trim action, as a large tool would get one.
    sugg.append({"rule": "trim", "level": "saving", "tool": "search_volume",
                 "action": {"kind": "auto", "type": "trim", "tool": "search_volume", "mode": "prudent"}})
    traces = [trace(f"t{i}", "spa weekend 1600") for i in range(3)]
    config, proj = build_config(d, sugg, [analyse(t) for t in traces], "p.json")
    proj.save(tmp_path / "p.json")
    (tmp_path / "c.json").write_text(json.dumps(config))
    pol = Policy.load(tmp_path / "c.json")
    out = json.loads(pol.apply("search_volume", json.dumps(VOLUMES)))
    assert "location_code" not in out["items"][0] and out["items"][0]["keyword"] == "spa weekend"
    assert config["compact"] is True                      # generated configs write compact JSON (0.4)
    assert pol.apply("other", '{"a": "\\u00e0"}') == '{"a":"à"}'


def test_report_names_project_and_author_and_no_missing_identifier():
    d = _data()
    a = render_html(d, suggestions(d))
    assert "Alessio Pomaro" in a and "0009-0008-6975-996X" in a and "tracetrim" in a
    # Every identifier appears if and only if it exists: a report must never
    # link to a repository or a DOI that is not there.
    from tracetrim import about
    for key in ("REPOSITORY", "PAPER_DOI", "SOFTWARE_DOI", "LICENSE"):
        value = getattr(about, key)
        assert (key.lower() in d["generator"]) == (value is not None)
        if value is not None:
            assert value in a
    assert "None" not in a


def test_with_one_run_the_headline_does_not_claim_held_out_savings():
    import json as _json
    from tracetrim import report as rp
    from tracetrim.traces import Trace, Event
    from tracetrim.usage import analyse
    t = Trace("one", [Event("prompt", text="go"),
                      Event("call", text="{}", tool="t", output=_json.dumps({"a": "x" * 20}, indent=2)),
                      Event("message", text="done")])
    d = rp.compute(["one"], [analyse(t)])
    page = rp.render_html(d, rp.suggestions(d))
    assert "at least two runs" in page and "on runs it never saw" not in page
    assert "Only 1 trace:" in page
