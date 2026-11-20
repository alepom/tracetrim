"""End-to-end checks of the offline comparison and its held-out measurements."""
import json
from pathlib import Path

import pytest

from tracetrim.cli import main


def write(path, events):
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return str(path)


def call(tool, output):
    return {"kind": "call", "tool": tool, "arguments": {}, "output": output}


def run(capsys, paths, a="protect_ids=all", b="protect_ids=record"):
    main(["compare", *paths, "--a", a, "--b", b, "--json"])
    return json.loads(capsys.readouterr().out)


def test_held_out_usage_is_always_judged_with_1_1(tmp_path, capsys):
    paths = []
    for i in range(2):
        # 1.0 cannot detect reuse of the negative numbers. Both versions can learn
        # the long unused field, so the annotation pays for itself in both recipes.
        delta = -1234 - i
        paths.append(write(tmp_path / f"r{i}.jsonl", [
            call("metrics", {"change": delta, "padding": "long unused field " * 100}),
            call("tiny", {"quantity": 34567 + i}),
            call("expand", None), {"kind": "message", "text": f"The change was {delta}."}]))
    d = run(capsys, paths, a="learning=1.0", b="learning=1.1")
    assert d["format"] == "tracetrim/compare@1"
    assert d["tools"]["metrics"]["missed_fields"] == {"change": {"a": 2, "b": 0}}
    assert d["totals"]["delta"]["miss_calls"] == -2
    assert d["totals"]["calls"] == 4 and d["observed_expand"] == 2
    assert d["tools"]["tiny"]["a"]["full_cheaper"] == 2
    assert d["tools"]["tiny"]["missed_fields"] == {}
    assert d["full_cheaper_calls"] == [
        {"run": f"r{i}.jsonl", "call": 2, "tool": "tiny", "sides": ["a", "b"]} for i in range(2)]
    assert d["divergences"] == [{"run": "r0.jsonl", "call": 1, "tool": "metrics",
                                 "field": "change", "hidden_by": "a"}]
    assert d["warnings"] and d["inputs"][0]["calls_read"] == 3
    equal = run(capsys, paths, a="learning=1.0", b="learning=1.0")
    assert equal["divergences"] == []
    for row in [equal["totals"], *equal["tools"].values()]:
        assert row["a"] == row["b"] and not any(row["delta"].values())
    assert equal == run(capsys, paths, a="learning=1.0", b="learning=1.0")


def test_unseen_depends_on_training_and_preserves_original_call_number(tmp_path, capsys):
    paths = []
    for i in range(2):
        key = f"only_in_{i}"
        text = ("distinctive words for this field " * 50) + str(i)
        path = tmp_path / f"r{i}.jsonl"
        paths.append(write(path, [call("expand", "observed"), call("lookup", {key: text}),
                                  {"kind": "message", "text": text}]))
    d = run(capsys, paths, a="unseen=keep", b="unseen=drop")
    assert d["tools"]["lookup"]["missed_fields"] == {f"only_in_{i}": {"a": 0, "b": 1} for i in range(2)}
    assert d["divergences"][0]["call"] == 2
    assert "expand" not in d["tools"]


def test_compact_and_keep_positions_change_served_tokens(tmp_path, capsys):
    paths = [write(tmp_path / f"r{i}.jsonl", [
        call("items", {"data": [{"memo": "repeated unused words " * 60}, {"title": f"Result number {i}"}]}),
        {"kind": "message", "text": "done"}]) for i in range(2)]
    compact = run(capsys, paths, a="compact=false", b="compact=true")
    assert compact["totals"]["delta"]["tokens"] < 0
    positions = run(capsys, paths, a="keep_positions=true", b="keep_positions=false")
    assert positions["totals"]["delta"]["tokens"] < 0


@pytest.mark.parametrize("setting", ["p=q", "learning=2", "compact=1", "", "compact=true,compact=false"])
def test_reject_invalid_recipes(setting, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["compare", "r.jsonl", "--a", setting, "--b", "compact=true"])
    assert exc.value.code == 2
    assert "recipe setting" in capsys.readouterr().err


def test_checks_inputs_and_warns_about_exclusions(tmp_path, capsys):
    events = [call("tool", "plain text"), {"kind": "message", "text": "done"}]
    p = write(tmp_path / "one.jsonl", events)
    copy = write(tmp_path / "copy.jsonl", events)
    with pytest.raises(SystemExit, match="duplicate trace"):
        run(capsys, [p, copy])
    with pytest.raises(SystemExit, match="at least 2"):
        run(capsys, [p])
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{")
    with pytest.raises(SystemExit, match="bad.jsonl"):
        run(capsys, [p, str(bad)])
    q = write(tmp_path / "two.jsonl", [call("tool", "other plain text"), events[-1]])
    incomplete = write(tmp_path / "open.jsonl", [call("expand", "requested")])
    main(["compare", p, q, incomplete, "--a", "compact=true", "--b", "compact=true"])
    out = capsys.readouterr()
    assert "Read 3 files and 3 calls" in out.out and "Evaluated 2 runs" in out.out
    assert "Observed expand requests: 1" in out.out and "Leave-one-out" in out.out
    assert "small sample" in out.err and "missing final answer" in out.err


@pytest.mark.parametrize("case,calls,tokens_a,tokens_b", [
    ("C3", 61, 1100717, 874274), ("C4", 84, 73942, 64764), ("C5", 104, 216068, 152228)])
def test_public_phase_a(traces, capsys, case, calls, tokens_a, tokens_b):
    paths = sorted((traces / case).glob("A*.jsonl"))
    if len(paths) < 8:
        pytest.skip(f"complete public Phase A sample unavailable for {case}")
    published = json.loads((Path(__file__).resolve().parents[1] / "results/v1/identifiers.json").read_text())[case]
    d = run(capsys, [str(p) for p in paths])
    assert d["eligible_runs"] == 8 and not d["warnings"]
    assert d["totals"]["calls"] == calls and d["observed_expand"] == 0
    for side, tokens, arm in [("a", tokens_a, "all"), ("b", tokens_b, "labels")]:
        assert d["totals"][side] == {"tokens": tokens, "miss_calls": published["miss_" + arm], "full_cheaper": 0}
        assert tokens == sum(t[side]["tokens"] for t in d["tools"].values())


def test_literal_token_markers_in_tool_text(tmp_path, capsys):
    paths = [write(tmp_path / f"r{i}.jsonl", [
        call("lookup", {"id": f"value{i}<|endoftext|>", "padding": "unused words " * 80}),
        {"kind": "message", "text": "done"}]) for i in range(2)]
    d = run(capsys, paths)
    assert d["totals"]["calls"] == 2 and d["totals"]["a"]["tokens"] > 0
