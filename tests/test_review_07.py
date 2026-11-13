"""Defects found by the review before 1.0 (code added after 0.4.0), each reproduced first."""
import json
import sys
from pathlib import Path

from tracetrim.auto import AutoTrim

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
import billing  # noqa: E402

BIG = "A long description of what this tool does, its arguments and its limits. " * 120


def _runs(tt, names, n=5):
    for i in range(n):
        for name in names:
            tt.output(name, {"q": i}, json.dumps({"value": i, "name": name}))
        tt.done(f"answer {i}")


def _definitions(work):
    return json.loads((Path(work) / "report.json").read_text()).get("definitions")


def test_gemini_groups_of_functions_count_as_the_functions_they_hold(tmp_path):
    tt = AutoTrim(tmp_path, min_runs=3)
    names = ["alpha", "beta", "gamma"]
    tt.offered([{"function_declarations": [{"name": n, "description": BIG, "parameters": {"type": "object"}}
                                           for n in names]}])
    _runs(tt, names)
    d = _definitions(tmp_path)
    assert d["offered"] == 3 and d["called"] == names and d["never_called"] == [] and not d["suggest"]


def test_built_in_tools_weigh_but_are_never_named_as_unused(tmp_path):
    tt = AutoTrim(tmp_path, min_runs=3)
    tt.offered([{"type": "function", "name": "alpha", "parameters": {"type": "object"}},
                {"type": "web_search"},
                {"type": "web_search_20250305", "name": "web_search", "max_uses": 3, "note": BIG}])
    _runs(tt, ["alpha"])
    d = _definitions(tmp_path)
    assert d["offered"] == 1 and d["never_called"] == [] and d["other_tokens"] > 0 and not d["suggest"]


def test_report_with_tools_counts_only_runs_that_end_with_an_answer(tmp_path, capsys):
    from tracetrim.cli import main
    traces = tmp_path / "traces"
    traces.mkdir()
    for i in range(5):                       # calls, but no final answer
        (traces / f"run-{i:04d}.jsonl").write_text(json.dumps(
            {"kind": "call", "tool": "alpha", "arguments": {}, "output": "{\"v\": 1}"}) + "\n")
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps([{"name": n, "description": BIG, "inputSchema": {"type": "object"}}
                                 for n in ("alpha", "beta", "gamma", "delta", "epsilon")]))
    main(["report", *map(str, sorted(traces.glob("*.jsonl"))), "--tools", str(tools), "-o", str(tmp_path / "r.html")])
    d = json.loads((tmp_path / "r.json").read_text())["definitions"]
    assert d["runs"] == 0 and not d["suggest"]


def test_a_savings_file_of_another_shape_never_stops_a_closing(tmp_path):
    for bad in ("[1]", '"x"', '{"runs": null}', '{"runs": []}',
                json.dumps({"format": "tracetrim/savings@1", "runs": {"run-0001": {"tools": {"t": {}}}}})):
        work = tmp_path / str(abs(hash(bad)))
        work.mkdir()
        (work / "savings.json").write_text(bad)
        tt = AutoTrim(work, min_runs=3)
        tt.output(billing.TOOL, billing.arguments(0), billing.output(0))
        tt.done(billing.answer(0))
        assert json.loads((work / "state.json").read_text())["complete_runs"] == 1
        assert (work / "report.json").exists()
        saved = json.loads((work / "savings.json").read_text())
        assert saved["format"] == "tracetrim/savings@1" and "run-0001" in saved["runs"]


def test_an_expand_before_any_run_leaves_no_run_0000(tmp_path):
    tt = AutoTrim(tmp_path, min_runs=3)
    tt.expand({"call_id": "cbogus", "fields": ["x"]})
    tt.learner.flush()
    saved = tmp_path / "savings.json"
    assert not saved.exists() or "run-0000" not in json.loads(saved.read_text())["runs"]


def test_a_tool_that_returns_bytes_is_read_as_text_with_or_without_the_breakdown(tmp_path):
    on = AutoTrim(tmp_path / "on", min_runs=3).output("t", {}, b'{"a": 1}')
    off = AutoTrim(tmp_path / "off", min_runs=3, breakdown=False).output("t", {}, b'{"a": 1}')
    assert on == off == '{"a":1}'


def test_the_dataset_never_publishes_an_amount_nor_what_the_agent_typed():
    from experiments.export_data import public_meta
    meta = {"run": "E2-base-s1-r1", "cost_usd": 0.42, "usage": {"input": 1}, "tool_failures": [{"arguments": {"q": "x"}}],
            "domain_rejections": ["example.test"], "replay": {"exact": 1}}
    full, derived = public_meta(dict(meta), derived=False), public_meta(dict(meta), derived=True)
    assert "cost_usd" not in full and "tool_failures" in full and full["usage"] == {"input": 1}
    assert set(derived) == {"run", "usage"}
