"""Automatic mode: each rule of tracetrim.auto, on a small synthetic tool."""
import json

from tracetrim.auto import EXPAND_MIN_CALLS, Learner

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
    lr = Learner(tmp_path, min_runs=3)
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
