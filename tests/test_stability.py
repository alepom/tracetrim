"""What STABILITY.md promises from 1.0 on, held by tests.

A failing test here means a change that breaks a promise: either it is a
mistake, or it waits for 2.0 and a note in STABILITY.md. `fixtures/workdir_060`
is a work directory exactly as 0.6.0 left it (written by AutoTrim on the
billing fixture: three runs to learn, a trimmed run with one `expand`).
"""
import inspect
import json
import shutil
import sys
from pathlib import Path

import pytest

import tracetrim
from tracetrim.auto import DONE, DONE_REPLY, AutoTrim
from tracetrim.engine import EXPAND, MARK
from tracetrim.evidence import Adaptive

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(FIXTURES))
import billing  # noqa: E402

FORMATS = {"tracetrim/config@1", "tracetrim/evidence@1", "tracetrim/projection@1", "tracetrim/report@1",
           "tracetrim/savings@1", "tracetrim/state@1", "tracetrim/tools@1"}


@pytest.fixture
def workdir_060(tmp_path):
    work = tmp_path / "work"
    shutil.copytree(FIXTURES / "workdir_060", work, ignore=shutil.ignore_patterns("expected_trimmed.json"))
    return work


def _flow(work) -> AutoTrim:
    """Every file the automatic mode writes: offered tools, learning, a trimmed run with an expand."""
    tt = AutoTrim(work, min_runs=3)
    tt.offered([{"type": "function", "name": billing.TOOL, "parameters": {"type": "object"}}])
    for i in range(3):
        tt.output(billing.TOOL, billing.arguments(i), billing.output(i))
        tt.done(billing.answer(i))
    sent = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    tt.expand({"call_id": sent.rsplit("call_id=", 1)[1].split()[0], "fields": ["data[].footer"]})
    tt.done(billing.answer(3))
    return tt


# -- files -------------------------------------------------------------------

def test_a_060_work_directory_is_picked_up_and_trims_as_before(workdir_060):
    expected = json.loads((FIXTURES / "workdir_060" / "expected_trimmed.json").read_text())
    tt = AutoTrim(workdir_060, min_runs=3)
    assert tt.trimming and tt.learner.state["complete_runs"] == 4
    sent = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    body, line = sent.rsplit(MARK, 1)
    assert json.loads(body) == expected["body"] and line.split("hidden: ")[1] == expected["hidden"]


def test_a_060_work_directory_keeps_learning_and_reporting(workdir_060):
    tt = AutoTrim(workdir_060, min_runs=3)
    tt.output(billing.TOOL, billing.arguments(0), billing.output(0))
    tt.done(billing.answer(0))
    report = json.loads((workdir_060 / "report.json").read_text())
    assert report["auto"]["complete_runs"] == 5 and report["savings"]["runs"]


def test_every_json_file_of_a_work_directory_names_its_format(tmp_path):
    _flow(tmp_path)
    found = {}
    for p in sorted(tmp_path.glob("*.json")):
        found[p.name] = json.loads(p.read_text()).get("format")
    assert found and all(f in FORMATS for f in found.values()), found


def test_the_line_files_keep_their_fields(tmp_path):
    _flow(tmp_path)
    for line in (tmp_path / "traces" / "run-0004.jsonl").read_text().splitlines():
        e = json.loads(line)
        assert e["kind"] in ("call", "message")
        assert set(e) >= ({"tool", "arguments", "output"} if e["kind"] == "call" else {"text"})


def test_report_json_keeps_every_key_0_6_wrote(tmp_path):
    _flow(tmp_path)
    old = json.loads((FIXTURES / "workdir_060" / "report.json").read_text())
    new = json.loads((tmp_path / "report.json").read_text())
    assert set(new) >= set(old), sorted(set(old) - set(new))
    assert set(new["auto"]) >= set(old["auto"])


# -- what the model sees over MCP ------------------------------------------------

def test_the_tools_tracetrim_adds_keep_their_names_and_arguments():
    assert EXPAND["name"] == "expand" and DONE["name"] == "tracetrim_done"
    assert EXPAND["inputSchema"]["required"] == ["call_id", "fields"]
    assert EXPAND["inputSchema"]["properties"]["fields"]["items"] == {"type": "string"}
    assert DONE["inputSchema"]["required"] == ["answer"]
    assert MARK == "\n[trimmed] " and DONE_REPLY == "Recorded."


def test_the_trimmed_line_keeps_its_shape(tmp_path):
    tt = _flow(tmp_path)
    sent = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    line = sent.rsplit(MARK, 1)[1]
    assert line.startswith("call_id=c") and " hidden: " in line


# -- Python ------------------------------------------------------------------------

def test_the_python_api_keeps_its_names():
    assert set(tracetrim.__all__) >= {"Event", "Projection", "Trace", "analyse", "learn", "load", "project"}
    params = inspect.signature(AutoTrim.__init__).parameters
    assert list(params)[1:3] == ["workdir", "min_runs"]
    assert set(params) >= {"structural", "strategy", "adaptive", "compact", "breakdown"}
    for name in ("output", "expand", "done", "offered", "instructions", "report", "expand_tool", "trimming"):
        assert hasattr(AutoTrim, name) or name == "expand_tool", name
    assert set(inspect.signature(Adaptive).parameters) >= {
        "promote", "promote_requested_runs", "promote_reused_runs", "sample_rate", "sample_max_share",
        "sample_seed", "switch_off_runs"}


# -- command line --------------------------------------------------------------------

COMMANDS = {
    "learn": [], "apply": [], "measure": [], "report": ["--tools"], "promotions": ["--revert"], "skill": ["--strategy"],
    "init": ["--host", "--file", "--scope", "--workdir", "--name", "--exclude", "--http", "--write", "--force", "--undo", "--no-check"],
    "proxy": ["--servers", "--upstream-cmd", "--upstream-url", "--header", "--config", "--record", "--http", "--token",
              "--allowed-host", "--quiet", "--auto", "--min-runs", "--structural", "--skill-strategy", "--no-skill",
              "--no-compact", "--no-breakdown", "--log-client", "--promote", "--promote-runs", "--sample-rate",
              "--sample-max-share", "--sample-seed", "--switch-off-runs"],
}


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_command_keeps_its_options(command, capsys):
    from tracetrim.cli import main
    with pytest.raises(SystemExit) as e:
        main([command, "--help"])
    assert e.value.code == 0
    text = capsys.readouterr().out
    missing = [o for o in COMMANDS[command] if o not in text]
    assert not missing, missing
