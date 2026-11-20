"""Run provenance survives a failed close and stays out of the model's data."""
import json

import pytest

from tracetrim import store
from tracetrim.auto import Learner
from tracetrim.clientlog import ClientLog, SessionKeys


def test_only_a_reused_connection_counts_without_a_transport_id(tmp_path):
    class Connection:
        pass

    keys = SessionKeys()
    log = ClientLog(str(tmp_path / "client.jsonl"), keys)
    try:
        for connection in [Connection(), Connection()]:
            log.record(connection, "tools/call", {"name": "example"})
            assert keys.for_call(connection) is None
            assert keys.for_call(connection) == log.session(connection)
        assert keys.for_call(None) is None
        connection.session_id = "private-transport-id"
        key = keys.for_call(connection)
        log.record(connection, "tools/call", {"name": "example"})
        assert key == log.session(connection) and "private-transport-id" not in log.session(connection)
        assert "private-transport-id" not in (tmp_path / "client.jsonl").read_text()
    finally:
        log.close()


def test_mixed_session_totals_survive_retry_and_restart(tmp_path, monkeypatch):
    learner = Learner(tmp_path)
    for key in ("same-private-key", "other-private-key", "same-private-key"):
        learner.call("example", {}, "value", session_key=key)

    def fail_write(*args):
        raise OSError("test trace write failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(store, "write_atomic", fail_write)
        with pytest.raises(OSError):
            learner.done("value")
    assert "mixed_sessions" not in learner.state
    learner.done("value")
    assert learner.closed_sessions == 2
    assert learner.done("value") is None
    learner.call("example", {}, "value", session_key="single-private-key")
    learner.done("value")
    learner = Learner(tmp_path)
    for key in ("same-private-key", "other-private-key", "third-private-key"):
        learner.call("example", {}, "value", session_key=key)
    learner.flush()
    for file in ("state.json", "report.json"):
        assert json.loads((tmp_path / file).read_text())["mixed_sessions"] == {"runs": 2, "session_occurrences": 5}
    assert learner.closed_sessions == 3
    assert all("private-key" not in p.read_text() for p in tmp_path.rglob("*") if p.is_file())
