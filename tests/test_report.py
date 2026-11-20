"""The report: same traces, same numbers, same suggestions, in both languages."""
import json

import pytest

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


def test_the_misses_by_name_come_from_the_leave_one_out_measure(traces):
    # Since 1.1.5 compute() reads the missed fields from the leave-one-out measure instead of learning every
    # aggressive projection a second time: the counts must be the ones that second learning gave.
    from collections import defaultdict
    from tracetrim import analyse
    from tracetrim import report as rp
    from tracetrim.measure import removes
    from tracetrim.projection import learn
    from tracetrim.traces import load
    from tracetrim.usage import learning_flags
    for case in ("C4", "C5"):
        loaded = [load(traces / case / f"A{i}.jsonl") for i in range(1, 7)]
        usages = [analyse(t, **learning_flags("1.1")) for t in loaded]
        d = rp.compute([t.name for t in loaded], usages, runtime=True)
        expected = defaultdict(lambda: defaultdict(int))
        for i, calls in enumerate(usages):
            agg = learn(usages[:i] + usages[i + 1:], "aggressive")
            for c in calls:
                if c.structured and c.tool in agg.keep:
                    for f in c.used:
                        if removes(agg, c.tool, f):
                            expected[c.tool][f] += 1
        got = {(t, f): v["missed"] for t, tool in d["tools"].items() for f, v in tool["fields"].items() if v["missed"]}
        assert got == {(t, f): n for t, fs in expected.items() for f, n in fs.items()}
        assert got, case


def _check_fold_projections(usages, source, unseen, protect_ids):
    from tracetrim.projection import FoldTraining, learn

    training = FoldTraining(usages, source, unseen, protect_ids)
    for exclude in [None, *range(len(usages))]:
        others = usages if exclude is None else usages[:exclude] + usages[exclude + 1:]
        for mode in ("prudent", "aggressive"):
            assert training.project(mode, exclude) == learn(others, mode, source, unseen, protect_ids)


def test_fold_support_counts_distinct_runs_and_empty_tools():
    from itertools import product
    from tracetrim.usage import CallUsage

    def observed(tool, payload, **fields):
        return CallUsage(tool, json.dumps(payload), payload, **fields)

    rare = observed("tool", {"once": 1234, "owner": {"id": "abc"}, "label": "xyz"},
                    used={"label"}, used_by_calls={"once"}, echo={"owner.id"}, judgeable={"once", "label"})
    shared = observed("tool", {"once": 7, "other": True})
    empty = observed("empty", {})
    runs = [[rare, rare, empty], [shared, observed("only_empty_list", [])], [shared, empty], [],
            [observed("text_only", "plain text")]]
    for source, unseen, protect in product(("full", "calls"), ("keep", "drop"), (False, True, "record")):
        _check_fold_projections(runs, source, unseen, protect)
    _check_fold_projections([], "full", "keep", True)
    _check_fold_projections([runs[0]], "calls", "drop", False)


@pytest.mark.parametrize("case", ("C3", "C4", "C5"))
def test_every_public_phase_a_fold_matches_full_refitting(traces, case):
    from tracetrim.traces import load
    from tracetrim.usage import learning_flags

    paths = sorted((traces / case).glob("A*.jsonl"))
    if len(paths) != 8:
        pytest.skip(f"eight public Phase A runs required for {case}")
    usages = [analyse(load(path), **learning_flags("1.1")) for path in paths]
    _check_fold_projections(usages, "full", "keep", True)
    _check_fold_projections(usages, "calls", "drop", "record")
