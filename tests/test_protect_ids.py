"""Which identifying fields learning keeps: `protect_ids` "all" (the default), "record", "none" (since 1.1)."""
import json
import sys
from pathlib import Path

import pytest

from tracetrim.auto import AutoTrim, Learner
from tracetrim.cli import main
from tracetrim.projection import learn, protected, record_level
from tracetrim.traces import Event, Trace
from tracetrim.usage import analyse

FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(FIXTURES))
import billing  # noqa: E402


@pytest.mark.parametrize("path, record", [
    ("id", True),
    ("results[].id", True),
    ("results[].doi", True),
    ("items[].url", True),
    ("[].html_url", True),
    ("results[].authorships[].author.id", False),        # a nested entity, two list levels
    ("results[].authorships[].institutions[].id", False),
    ("results[].concepts[].id", False),
    ("data[].refunds.url", False),                       # an object between the record and the field
    ("[].user.html_url", False),
    ("[].labels[].node_id", False),
    ("jobs[].steps[].number", False),
    ("results[].authorships[].author.display_name", True),  # names and titles: any depth
    ("results[].authorships[].institutions[].display_name", True),
    ("items[].links[].title", True),
    ("[].labels[].name", True),
    ("data.id", True),                                    # one record wrapped in an object (review of 1.1)
    ("result.url", True),
    ("data.customer.id", False),                          # an entity inside the wrapped record
])
def test_record_level(path, record):
    assert record_level(path) is record


def test_protected_by_mode():
    assert protected("results[].concepts[].id", "all")
    assert not protected("results[].concepts[].id", "record")
    assert protected("results[].id", "record")
    assert not protected("results[].id", "none")
    assert not protected("results[].abstract", "all")    # not named like an identifier
    assert protected("results[].id", True) and not protected("results[].id", False)
    with pytest.raises(ValueError):
        protected("results[].id", "some")


# -- learning ------------------------------------------------------------------------

def _work(i):
    return {"id": f"https://example.test/W{9000 + i}", "title": f"Paper number {i} on tool outputs",
            "authorships": [{"author": {"id": f"https://example.test/A{500 + i}", "display_name": f"Author {i}"}}],
            "concepts": [{"id": f"https://example.test/C{70 + i}", "display_name": "Machine learning"}],
            "abstract": f"An abstract long enough to be judged, about topic {i} and nothing else at all."}


def _usages(n=4, cite_author_id=False):
    out = []
    for i in range(n):
        t = Trace(f"r{i}.jsonl")
        t.events.append(Event("call", text="{}", tool="search", output=json.dumps({"results": [_work(i)]})))
        answer = f"Paper number {i} on tool outputs (W{9000 + i}), by Author {i}."
        if cite_author_id:
            answer += f" Author id: https://example.test/A{500 + i}"
        t.events.append(Event("message", text=answer))
        out.append(analyse(t))
    return out


def test_record_drops_nested_ids_that_are_never_used_and_keeps_the_rest():
    usages = _usages()
    a, r = learn(usages), learn(usages, protect_ids="record")
    assert {"results[].authorships[].author.id", "results[].concepts[].id"} <= a.keep["search"]
    assert "results[].authorships[].author.id" not in r.keep["search"]
    assert "results[].concepts[].id" not in r.keep["search"]
    assert {"results[].id", "results[].title", "results[].authorships[].author.display_name",
            "results[].concepts[].display_name"} <= r.keep["search"]
    assert "results[].abstract" not in r.keep["search"]          # as in every mode: never used, judgeable


def test_a_nested_id_the_agent_used_is_kept_in_record_mode():
    r = learn(_usages(cite_author_id=True), protect_ids="record")
    assert "results[].authorships[].author.id" in r.keep["search"]


def test_true_and_false_are_all_and_none():
    usages = _usages()
    assert learn(usages, protect_ids=True).keep == learn(usages, protect_ids="all").keep == learn(usages).keep
    assert learn(usages, protect_ids=False).keep == learn(usages, protect_ids="none").keep


def test_record_removes_more_tokens_on_the_same_output():
    usages = _usages()
    raw = json.dumps({"results": [_work(9)]})
    a, r = learn(usages).apply("search", raw), learn(usages, protect_ids="record").apply("search", raw)
    assert len(r) < len(a) and "A509" in a and "A509" not in r and "W9009" in r


# -- automatic mode -------------------------------------------------------------------

def _flow(work, **kw):
    tt = AutoTrim(work, min_runs=3, **kw)
    for i in range(3):
        tt.output(billing.TOOL, billing.arguments(i), billing.output(i))
        tt.done(billing.answer(i))
    return tt


def test_the_default_writes_the_same_files_as_1_0(tmp_path):
    _flow(tmp_path)
    config = json.loads((tmp_path / "report.config.json").read_text())
    report = json.loads((tmp_path / "report.json").read_text())
    assert "protect_ids" not in config["auto"] and report["protect_ids"] is True


def test_record_is_recorded_in_the_config_and_used(tmp_path):
    tt = _flow(tmp_path, protect_ids="record")
    config = json.loads((tmp_path / "report.config.json").read_text())
    report = json.loads((tmp_path / "report.json").read_text())
    assert config["auto"]["protect_ids"] == "record" and report["protect_ids"] == "record"
    # `account_id` is the record's own: kept in both modes; the billing fixture has no nested id.
    sent = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    assert "acct_773" in sent


def test_automatic_mode_refuses_none(tmp_path):
    with pytest.raises(ValueError):
        Learner(tmp_path, protect_ids="none")


def test_promotions_relearn_with_the_mode_of_the_work_directory(tmp_path, capsys):
    _flow(tmp_path, protect_ids="record")
    # A promotion to take back makes `tracetrim promotions` relearn with a Learner of its own.
    ev = json.loads((tmp_path / "evidence.json").read_text()) if (tmp_path / "evidence.json").exists() else \
        {"format": "tracetrim/evidence@1", "tools": {}, "promotions": [], "resumed": []}
    ev["promotions"].append({"tool": billing.TOOL, "field": "data[].footer", "reason": "reused", "threshold": 1,
                             "runs": ["run-0001"], "evidence": {}, "decided_after": "run-0001",
                             "status": "active", "at": "2026-11-14T00:00:00+00:00"})
    (tmp_path / "evidence.json").write_text(json.dumps(ev))
    before = (tmp_path / "report.config.json").stat().st_mtime_ns
    main(["promotions", str(tmp_path), "--revert"])
    assert "<- changed" in capsys.readouterr().out
    assert (tmp_path / "report.config.json").stat().st_mtime_ns != before
    assert json.loads((tmp_path / "report.config.json").read_text())["auto"]["protect_ids"] == "record"


# -- command line -----------------------------------------------------------------------

def _traces(tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"r{i}.jsonl"
        lines = [{"kind": "call", "tool": "search", "arguments": {}, "output": json.dumps({"results": [_work(i)]})},
                 {"kind": "message", "text": f"Paper number {i} on tool outputs (W{9000 + i}), by Author {i}."}]
        p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        paths.append(str(p))
    return paths


def test_learn_with_record(tmp_path):
    out = tmp_path / "p.json"
    main(["learn", *_traces(tmp_path), "-o", str(out), "--protect-ids", "record"])
    kept = json.loads(out.read_text())["tools"]["search"]
    assert "results[].id" in kept and "results[].concepts[].id" not in kept


def test_measure_with_record_removes_more(tmp_path, capsys):
    paths = _traces(tmp_path)
    main(["measure", *paths])
    default = capsys.readouterr().out
    main(["measure", *paths, "--protect-ids", "record"])
    assert capsys.readouterr().out != default


def test_v01_and_protect_ids_together_are_refused(tmp_path):
    with pytest.raises(SystemExit):
        main(["measure", *_traces(tmp_path), "--v01", "--protect-ids", "record"])


def test_proxy_refuses_protect_ids_without_auto():
    with pytest.raises(SystemExit) as e:
        main(["proxy", "--upstream-cmd", "true", "--protect-ids", "record"])
    assert "--auto" in str(e.value)


def test_v01_keeps_the_value_of_1_0_in_report_json(tmp_path):
    out = tmp_path / "r.html"
    main(["report", *_traces(tmp_path), "-o", str(out), "--v01"])
    assert json.loads(out.with_suffix(".json").read_text())["protect_ids"] is False
