"""After `--write`, `tracetrim init` starts the entry it wrote, once, as the host
will, and asks for its tools: an entry that cannot start is found at once, and a
slow first start (Windows, right after installing) happens here, not in the host."""
import json
import os
import sys
from pathlib import Path

import pytest

from tracetrim import init
from tracetrim.cli import main

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.real_check


def _project(tmp_path, monkeypatch, env=None):
    proj = tmp_path / "proj"
    proj.mkdir()
    server = {"command": sys.executable, "args": ["-m", "experiments.simulated.server_orders"],
              "env": {"PYTHONPATH": str(ROOT), **(env or {})}}
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"orders": server}}, indent=2) + "\n")
    monkeypatch.chdir(proj)
    # The entry runs this checkout's tracetrim, whatever the interpreter has installed.
    monkeypatch.setattr(init, "executable", lambda: [sys.executable, "-m", "tracetrim.cli"])
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    return proj


def _init(capsys, *argv):
    failure = None
    try:
        main(["init", "--host", "claude-code", "--workdir", "work", *argv])
    except SystemExit as e:
        failure = str(e.code)
    out = capsys.readouterr()
    return failure, out.out + out.err


def test_after_writing_the_entry_is_started_once_and_lists_the_tools(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    failure, text = _init(capsys, "--write")
    assert failure is None, text
    line = next(l for l in text.splitlines() if l.startswith("Check"))
    assert "the entry starts and answers" in line and "2 tools of your servers, plus expand, tracetrim_done" in line


def test_a_dry_run_and_no_check_start_nothing(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    started = []
    monkeypatch.setattr(init, "check", lambda p, timeout=None: started.append(p) or (True, "Check x"))
    _init(capsys)
    _init(capsys, "--write", "--no-check")
    assert started == []


def test_an_entry_that_does_not_answer_is_reported_with_the_way_back(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, env={"ORDERS_NOTE": "${ORDERS_NOTE}"})
    monkeypatch.delenv("ORDERS_NOTE", raising=False)
    monkeypatch.setattr(init, "executable", lambda: [sys.executable, "-c", "import time; time.sleep(30)"])
    monkeypatch.setattr(init, "CHECK_TIMEOUT", 3.0)
    failure, text = _init(capsys, "--write")
    assert failure and "did not start here" in failure and "tracetrim init --undo" in failure
    line = next(l for l in text.splitlines() if l.startswith("Check"))
    assert "FAILED" in line and "did not answer within 3 seconds" in line
    assert "not set here, so not passed: ORDERS_NOTE" in line
    assert list(Path(".").glob(".mcp.json.tracetrim-backup-*"))      # written all the same: the way back exists


def test_the_entry_env_is_resolved_as_the_host_would(tmp_path, monkeypatch):
    p = init.Plan.__new__(init.Plan)
    p.entry = {"env": {"A": "${A}", "B": "${B:-dflt}", "C": "${env:C}", "D": "${D}"}}
    monkeypatch.setenv("A", "a-value")
    monkeypatch.setenv("C", "c-value")
    monkeypatch.delenv("B", raising=False)
    monkeypatch.delenv("D", raising=False)
    env, missing = init._resolved_env(p)
    assert env == {"A": "a-value", "B": "dflt", "C": "c-value"} and missing == ["D"]
