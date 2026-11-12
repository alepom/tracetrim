"""The saving split in two (0.4.1): what re-serialising saves, what removing fields saves.

A measure: with it on or off, the traces and the text the model receives are the same.
"""
import json
import sys
from pathlib import Path

import pytest

from tracetrim import engine as engine_module
from tracetrim.auto import AutoTrim, Learner
from tracetrim.contract import FILTERED, FULL, FULL_CHEAPER, SAMPLED, Sizes, ToolCall, ToolResult
from tracetrim.engine import Engine
from tracetrim.evidence import Adaptive
from tracetrim.measure import tokenizer
from tracetrim.runtime import Policy

FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(FIXTURES))
import billing  # noqa: E402

count = tokenizer()


def payload(i: int) -> dict:
    """An invoice like the fixture's, for any `i`: invented values."""
    return {"has_more": False, "data": [{
        "id": f"in_{2000 + i}", "account_id": f"acct_88{i}", "amount_due": 1300 + 137 * i, "created": 1795168800 + 86400 * i,
        "status": "open", "hosted_invoice_url": f"https://pay.example.test/i/in_{2000 + i}",
        "footer": "Thank you for your business with us", **billing.REMARKS}]}


def answer(i: int) -> str:
    return f"Invoice in_{2000 + i} is open: {(1300 + 137 * i) / 100:.2f} EUR due."


def plain(i: int) -> str:
    return json.dumps(payload(i))


def indented(i) -> str:
    """The output as many servers send it: indented, so that re-writing it saves something."""
    return json.dumps(payload(i), indent=2)


def call(i):
    return ToolCall(billing.TOOL, billing.arguments(i))


def by_definition(engine: Engine, tool: str, raw: str, text: str) -> Sizes:
    """R, W and S as the order of the measure defines them, each counted from scratch."""
    whole = engine.policy.apply(tool, raw, project=False) if engine.policy is not None else raw
    return Sizes(count(raw), count(whole), count(text))


def flow(workdir, monkeypatch, runs=6, **settings):
    """Six runs through AutoTrim, with errors, prose, small outputs and expand
    among the calls. Returns what the model received: the results in order, and the replies of expand."""
    ids = iter(range(10_000))
    monkeypatch.setattr(engine_module.secrets, "token_hex", lambda n: f"{next(ids):0{2 * n}x}")
    tt = AutoTrim(workdir, min_runs=3, adaptive=Adaptive(sample_rate=0.5, sample_max_share=1.0), **settings)
    received, replies = [], []
    for i in range(runs):
        received.append(tt.output(billing.TOOL, billing.arguments(i), indented(i)))
        received.append(tt.output(billing.TOOL, billing.arguments(i + 10), plain(i + 10)))
        received.append(tt.output("notes", {"n": i}, "Error: no note with that number."))
        received.append(tt.output("small", {"n": i}, json.dumps({"ok": True, "n": i}, indent=4)))
        if "[trimmed] call_id=" in received[-4] and i == 3:      # in one run only: the tool stays trimmed
            cid = received[-4].rsplit("call_id=", 1)[1].split(" ", 1)[0]
            replies.append(tt.expand({"call_id": cid, "fields": ["data[].footer"]}))
        tt.done(answer(i))
    return tt, received, replies


def test_the_two_parts_add_up_to_the_saving_on_every_call(tmp_path):
    engine = Engine(Learner(tmp_path, min_runs=3, adaptive=Adaptive(sample_rate=0.3, sample_max_share=1.0)))
    exposures = set()
    for i in range(12):
        for raw in (indented(i), plain(i + 20)):
            obs = engine.observe(call(i), ToolResult(raw))
            s = obs.sizes
            assert s == by_definition(engine, billing.TOOL, raw, obs.text)
            assert s.reserialisation + s.fields == s.raw - s.sent
            # Fields are removed only from a filtered result: every other one is at most re-written.
            assert (s.fields > 0) == (obs.exposure == FILTERED)
            exposures.add(obs.exposure)
        engine.close(answer(i))
    assert exposures == {FULL, FILTERED, SAMPLED}


def test_while_learning_only_reserialisation_saves_and_it_shows(tmp_path):
    engine = Engine(Learner(tmp_path, min_runs=3))
    obs = engine.observe(call(0), ToolResult(indented(0)))
    assert not engine.trimming and obs.exposure == FULL
    assert obs.sizes.fields == 0 and obs.sizes.reserialisation > 0
    assert obs.sizes.raw == count(indented(0)) and obs.sizes.sent == obs.sizes.whole == count(obs.text)


def test_what_is_passed_as_received_saves_nothing(tmp_path):
    engine = Engine(Learner(tmp_path, min_runs=3))
    error = engine.observe(call(0), ToolResult("Error: quota exceeded", verbatim=True)).sizes
    prose = engine.observe(call(1), ToolResult("three invoices were found")).sizes
    assert error.raw == error.whole == error.sent and prose.raw == prose.whole == prose.sent == count("three invoices were found")
    # No learner and nothing configured: the proxy passes everything through.
    assert Engine().observe(call(0), ToolResult(indented(0))).sizes == Sizes(*[count(indented(0))] * 3)


def test_a_result_cheaper_whole_has_no_saving_from_fields():
    # One short field would be removed: the line that says so costs more than the cut saves.
    from tracetrim.projection import Projection
    proj = Projection("prudent", "full", {"t": {"id"}}, {"t": {"id", "x"}}, "keep")
    engine = Engine(policy=Policy({"tools": {"t": {"trim": "prudent", "expand": True}}}, proj))
    raw = json.dumps({"id": 1, "x": 2}, indent=2)
    obs = engine.observe(ToolCall("t", {}), ToolResult(raw))
    assert obs.exposure == FULL_CHEAPER
    assert obs.sizes == by_definition(engine, "t", raw, obs.text) and obs.sizes.fields == 0 < obs.sizes.reserialisation


def test_the_same_flow_with_and_without_the_breakdown_gives_the_same_traces_and_text(tmp_path, monkeypatch):
    _, *received = flow(tmp_path / "on", monkeypatch)
    without, *received_off = flow(tmp_path / "off", monkeypatch, breakdown=False)
    assert received == received_off and any("[trimmed]" in r for r in received[0]) and received[1]

    def files(root, sub):
        return {p.name: p.read_bytes() for p in sorted((root / sub).iterdir())}
    for sub in ("traces", "evidence"):
        assert files(tmp_path / "on", sub) == files(tmp_path / "off", sub) and files(tmp_path / "on", sub)
    for name in ("state.json", "evidence.json", "report.projection.json"):
        assert (tmp_path / "on" / name).read_bytes() == (tmp_path / "off" / name).read_bytes()
    # What differs is the measure itself: its file, and the report's section.
    assert (tmp_path / "on" / "savings.json").exists() and not (tmp_path / "off" / "savings.json").exists()
    on, off = (json.loads((tmp_path / d / "report.json").read_text()) for d in ("on", "off"))
    assert "savings" not in off and "Where the saving comes from" not in without.report.read_text()
    for volatile in ("generated", "savings"):
        on.pop(volatile, None), off.pop(volatile, None)
    for d in (on, off):
        d["config"].pop("generated"), d["config"]["auto"].pop("updated"), d["auto"].pop("updated", None)
    assert on == off


def test_the_report_splits_the_saving_per_run_and_per_tool(tmp_path, monkeypatch):
    tt, received, replies = flow(tmp_path, monkeypatch)
    s = json.loads((tmp_path / "report.json").read_text())["savings"]
    assert s["unit"] == "tokens" and [r["run"] for r in s["runs"]] == [f"run-{n:04d}" for n in range(1, 7)]
    for row in [*s["runs"], *s["tools"].values(), s["total"]]:
        assert row["reserialisation"] + row["fields"] == row["raw"] - row["sent"]
        assert row["reserialisation"] == row["raw"] - row["whole"] and row["fields"] == row["whole"] - row["sent"]
    # While learning (three runs) nothing is removed, and re-writing already saves.
    assert all(r["fields"] == 0 and r["reserialisation"] > 0 for r in s["runs"][:3])
    # Then fields are removed too. In the last run sampling passed both results whole
    # (the sequence depends on the seed only): no saving from fields there, and it shows.
    assert [r["fields"] > 0 for r in s["runs"][3:]] == [True, True, False]
    assert all(r["reserialisation"] > 0 for r in s["runs"])
    # Tools that are only passed through, and the totals.
    assert s["tools"]["notes"]["raw"] == s["tools"]["notes"]["sent"] and s["tools"]["small"]["fields"] == 0
    assert s["total"]["calls"] == sum(r["calls"] for r in s["runs"]) == sum(t["calls"] for t in s["tools"].values()) == 24
    for key in ("raw", "whole", "sent"):
        assert s["total"][key] == sum(r[key] for r in s["runs"]) == sum(t[key] for t in s["tools"].values())
    # What the model received, counted again from the texts (expand replies apart).
    assert s["total"]["expand_calls"] == len(replies) == 1 and s["runs"][3]["expand_calls"] == 1
    assert s["total"]["expand_tokens"] == sum(count(r) for r in replies) > 0
    assert s["total"]["sent"] == sum(count(r) for r in received)
    page = tt.report.read_text()
    for text in ("Where the saving comes from", "Da dove viene il risparmio", "riserializzazione", "run-0006"):
        assert text in page


def test_counts_survive_a_restart_and_a_run_keeps_one_entry(tmp_path):
    for i in range(2):
        tt = AutoTrim(tmp_path, min_runs=3)
        tt.output(billing.TOOL, billing.arguments(i), indented(i))
        tt.done(answer(i))
    data = json.loads((tmp_path / "savings.json").read_text())
    assert data["format"] == "tracetrim/savings@1" and sorted(data["runs"]) == ["run-0001", "run-0002"]
    assert data["runs"]["run-0001"]["tools"][billing.TOOL]["calls"] == 1


def test_the_proxy_log_names_the_two_parts(capsys):
    from tracetrim.proxy import Live
    live = Live()
    live.per_run = True
    live.call("search", "", "", Sizes(1000, 730, 610))
    line = capsys.readouterr().err
    assert "this run: 1 calls, 1,000 -> 610 (-39%: -27% format, -12% fields)" in line
    assert "session: 1 calls, 1,000 -> 610 (-39%: -27% format, -12% fields)" in line
    assert live.new_run() == "1 calls, 1,000 -> 610 tokens (-39%: -27% format, -12% fields), 0 expand"
    # While learning: the saving is all format, and the line says so.
    live.call("search", "", "", Sizes(1000, 730, 730))
    assert "this run: 1 calls, 1,000 -> 730 (-27%: -27% format, 0% fields)" in capsys.readouterr().err
    # A call that comes without the count (the breakdown is off): totals only, as before 0.4.1.
    live.call("search", "x" * 40, "x" * 40)
    line = capsys.readouterr().err
    assert "format" not in line and "session: 3 calls" in line


@pytest.mark.parametrize("pause, en, it", [(300, "within the idle pause (300 seconds)", "pausa di inattività (300 secondi)"),
                                           (None, "within the idle pause. On a host", "pausa di inattività. Con un host")])
def test_the_report_explains_the_runs_followed_by_calls(pause, en, it):
    from tracetrim.report import _followed_note
    note = _followed_note({"answered_runs_followed_by_calls": 5, "idle_seconds": pause})
    for text in (en, it, "adds up two cases that tracetrim cannot tell apart", "closed the run too early",
                 "the next task began", "grows at every run", "somma due casi che tracetrim non sa distinguere",
                 "ha chiuso l&#x27;esecuzione troppo presto", "cresce a ogni esecuzione", ": 5."):
        assert text in note, text
    assert _followed_note({"answered_runs_followed_by_calls": 0, "idle_seconds": 300}) == ""
