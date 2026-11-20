"""Automatic mode: each rule of tracetrim.auto, on a small synthetic tool."""
import json

from tracetrim.auto import EXPAND_MIN_CALLS, Learner
from tracetrim.evidence import Adaptive

# A tool whose items carry many fields the agent never uses.
ITEM_NOISE = {f"note_{i}": f"internal remark number {i} about the record" for i in range(12)}
PAYLOAD = {"items": [dict(keyword=k, search_volume=v, **ITEM_NOISE)
                     for k, v in (("spa weekend", 1600), ("thermal baths", 2400))]}


def run(learner, answer="spa weekend has 1600 searches, thermal baths 2400"):
    learner.call("search_volume", {"keywords": ["spa weekend", "thermal baths"]}, json.dumps(PAYLOAD))
    return learner.done(answer)


def test_before_min_runs_the_report_exists_but_nothing_is_trimmed(tmp_path):
    lr = Learner(tmp_path, min_runs=3)
    run(lr)
    assert lr.relearn() is None
    config = json.loads((tmp_path / "report.config.json").read_text())
    assert config["tools"] == {} and config["auto"]["applied"] is False
    assert (tmp_path / "report.html").exists()


def test_runs_without_answer_feed_telemetry_not_learning(tmp_path):
    lr = Learner(tmp_path, min_runs=2)
    assert run(lr, answer=None).name.endswith("-noanswer.jsonl")
    run(lr, answer=None)
    assert lr.state["complete_runs"] == 0 and lr.relearn() is None


def test_after_min_runs_the_safe_config_is_applied(tmp_path):
    lr = Learner(tmp_path, min_runs=3)
    for _ in range(3):
        run(lr)
    policy = lr.relearn()
    assert policy is not None and "search_volume" in policy.tools
    assert policy.tools["search_volume"]["expand"] is True
    out = json.loads(policy.apply("search_volume", json.dumps(PAYLOAD)))
    assert out["items"][0] == {"keyword": "spa weekend", "search_volume": 1600}


def test_a_tool_expanded_too_often_is_switched_off(tmp_path):
    # One run's requests: the rule up to 0.3.5, kept with switch_off_runs=1 (the default asks for two runs).
    lr = Learner(tmp_path, min_runs=3, adaptive=Adaptive(switch_off_runs=1))
    for _ in range(3):
        run(lr)
    for _ in range(EXPAND_MIN_CALLS):
        lr.trimmed("search_volume")
        lr.expanded("search_volume")
    policy = lr.relearn()
    assert "search_volume" in lr.state["off"]
    assert policy is not None and "search_volume" not in policy.tools


def test_an_unfinished_run_is_kept_on_shutdown(tmp_path):
    lr = Learner(tmp_path, min_runs=3)
    lr.call("search_volume", {"keywords": ["spa weekend"]}, json.dumps(PAYLOAD))
    path = lr.flush()
    assert path is not None and path.name.endswith("-noanswer.jsonl")
    assert (tmp_path / "report.html").exists() and lr.flush() is None


def test_learning_survives_a_restart(tmp_path):
    lr = Learner(tmp_path, min_runs=3)
    for _ in range(3):
        run(lr)
    lr.relearn()
    again = Learner(tmp_path, min_runs=3)
    assert again.state["complete_runs"] == 3
    assert again.initial_policy() is not None


def test_closing_products_match_refitting_after_history_changes(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from unittest.mock import patch
    from tracetrim import auto, projection, report

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, tzinfo=timezone.utc)

    class Refit:
        def __init__(self, usages, source="full", unseen="keep", protect_ids=True):
            self.usages = usages
            self.options = dict(source=source, unseen=unseen, protect_ids=protect_ids)

        def project(self, mode, exclude=None):
            runs = self.usages
            if exclude is not None:
                runs = runs[:exclude] + runs[exclude + 1:]
            return projection.learn(runs, mode, **self.options)

    monkeypatch.setattr(auto, "datetime", FixedClock)
    monkeypatch.setattr(report, "datetime", FixedClock)
    work = tmp_path / "work"
    learner = Learner(work, min_runs=2)
    paths = [run(learner), run(learner)]

    def compare_products():
        files = [work / name for name in ("report.json", "report.config.json", "report.projection.json")]
        learner.relearn()
        actual = [p.read_bytes() for p in files]
        with patch.object(projection, "FoldTraining", Refit):
            learner.relearn()
        assert actual == [p.read_bytes() for p in files]

    compare_products()
    run(learner, "Internal remark number 3 about the record.")
    compare_products()
    lines = [json.loads(line) for line in paths[0].read_text().splitlines()]
    lines[-1]["text"] = "The record has a new final answer."
    paths[0].write_text("".join(json.dumps(e) + "\n" for e in lines))
    compare_products()
    paths[1].unlink()
    compare_products()
    for learning, protect in [("1.1", "all"), ("1.0", "all"), ("1.0", "record")]:
        learner = Learner(work, min_runs=2, learning=learning, protect_ids=protect)
        compare_products()


def test_relearn_reads_each_structured_payload_once_to_fit_all_projections(tmp_path, monkeypatch):
    from tracetrim import projection

    learner = Learner(tmp_path, min_runs=2)
    for _ in range(4):
        run(learner)
    original = projection.leaves
    scanned = []

    def observed(payload):
        scanned.append(payload)
        return original(payload)

    monkeypatch.setattr(projection, "leaves", observed)
    assert learner.relearn() is not None
    assert scanned == [PAYLOAD] * 4
