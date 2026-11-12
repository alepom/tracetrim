"""Adaptive learning: evidence from expand, promotion of fields, sampling without filter.

The tool is the simulated order list of the demo (experiments/simulated/orders.py):
after three runs tracetrim removes `carrier`, `refund_note` and the remarks.
"""
import asyncio
import json
import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments import adaptive_demo as demo  # noqa: E402
from experiments.simulated import orders  # noqa: E402
from tracetrim import evidence, store  # noqa: E402
from tracetrim.auto import AutoTrim, Learner  # noqa: E402
from tracetrim.cli import main  # noqa: E402
from tracetrim.contract import FILTERED, SAMPLED, ToolCall, ToolResult  # noqa: E402
from tracetrim.engine import MARK, Engine  # noqa: E402
from tracetrim.evidence import Adaptive  # noqa: E402

TOOL = "list_orders"
CARRIER, NOTE = "data[].carrier", "data[].refund_note"


def args(k):
    return {"customer": f"cus_{k:03d}", "limit": 3}


def newest(k):
    return orders.list_orders(**args(k))["data"][0]


def answer(k, extra=""):
    return demo._answer(newest(k), f"cus_{k:03d}") + extra


def learned(tmp_path, **adaptive) -> AutoTrim:
    """Three runs in which the agent reports id, status, total and date."""
    tt = AutoTrim(tmp_path, min_runs=3, adaptive=Adaptive(**adaptive))
    for k in (1, 2, 3):
        tt.output(TOOL, args(k), orders.output(**args(k)))
        tt.done(answer(k))
    assert tt.trimming and {CARRIER, NOTE} <= dropped(tmp_path)
    return tt


def run(tt, k, fields=(), extra="", times=1):
    """One more run: a call, perhaps expand requests, the answer. Returns what the model received."""
    sent = tt.output(TOOL, args(k), orders.output(**args(k)))
    missing = [f for f in fields if f == "*" or f.rsplit(".", 1)[-1] not in sent.split(MARK)[0]]
    if missing and MARK in sent:                                       # an agent asks for what it does not see
        for _ in range(times):
            tt.expand({"call_id": sent.rsplit("call_id=", 1)[1].split()[0], "fields": list(fields)})
    tt.done(answer(k, extra))
    return sent


def ev(tmp_path) -> dict:
    return evidence.load(tmp_path / "evidence.json")


def runs_of(tmp_path, field) -> dict:
    return ev(tmp_path)["tools"][TOOL]["fields"].get(field, {})


def dropped(tmp_path) -> set:
    return set(json.loads((tmp_path / "report.projection.json").read_text())["dropped"].get(TOOL, []))


def kept(tmp_path) -> set:
    return set(json.loads((tmp_path / "report.projection.json").read_text())["tools"].get(TOOL, []))


def state(tmp_path) -> dict:
    return json.loads((tmp_path / "state.json").read_text())


# -- evidence: four kinds, kept apart --------------------------------------------

def test_a_request_by_name_is_requested_and_recovered_but_not_reused_without_the_value(tmp_path):
    tt = learned(tmp_path)
    run(tt, 4, [NOTE], " Refunds: to be approved by a person.")       # used, never quoted
    assert runs_of(tmp_path, NOTE) == {"requested": ["run-0004"], "recovered": ["run-0004"]}
    assert CARRIER not in ev(tmp_path)["tools"][TOOL]["fields"]


def test_a_value_written_after_the_recovery_is_reused(tmp_path):
    tt = learned(tmp_path)
    run(tt, 4, [CARRIER], f" Carrier: {newest(4)['carrier']}.")
    assert runs_of(tmp_path, CARRIER) == {"requested": ["run-0004"], "recovered": ["run-0004"], "reused": ["run-0004"]}


def test_a_value_the_agent_already_had_before_the_recovery_is_not_reused(tmp_path):
    tt = learned(tmp_path)
    carrier = newest(4)["carrier"]
    sent = tt.output(TOOL, args(4), orders.output(**args(4)))
    tt.output("note", {"text": f"the customer says it was {carrier}"}, "{}")      # written before asking
    tt.expand({"call_id": sent.rsplit("call_id=", 1)[1].split()[0], "fields": [CARRIER]})
    tt.done(answer(4, f" Carrier: {carrier}."))
    assert "reused" not in runs_of(tmp_path, CARRIER) and "recovered" in runs_of(tmp_path, CARRIER)


def test_asking_for_everything_is_not_asking_for_each_field(tmp_path):
    tt = learned(tmp_path)
    run(tt, 4, ["*"])
    run(tt, 5, ["data[]"])                                             # a prefix over every group: as broad as "*"
    fields = ev(tmp_path)["tools"][TOOL]["fields"]
    assert {CARRIER, NOTE, "data[].remark_0"} <= set(fields)
    assert all(set(kinds) == {"recovered"} for kinds in fields.values())
    assert ev(tmp_path)["tools"][TOOL]["broad_requests"] == ["run-0004", "run-0005"]
    # Even with promotion on, nothing is promoted on that ground.
    assert evidence.decide(ev(tmp_path), Adaptive(promote=True, promote_requested_runs=1), "run-0005") == []


def test_everything_plus_a_name_counts_the_name_only(tmp_path):
    tt = learned(tmp_path)
    run(tt, 4, ["*", NOTE])
    fields = ev(tmp_path)["tools"][TOOL]["fields"]
    assert set(fields[NOTE]) == {"requested", "recovered"} and set(fields[CARRIER]) == {"recovered"}


def test_the_notes_record_tool_original_requested_and_returned_paths(tmp_path):
    tt = learned(tmp_path)
    sent = run(tt, 4, ["data[].carrier", "data[].id", "no_such"])
    notes = [json.loads(line) for line in (tmp_path / "evidence" / "run-0004.jsonl").open()]
    seen, asked = notes
    assert seen["kind"] == "observation" and seen["exposure"] == "filtered" and CARRIER in seen["hidden"]
    assert asked["kind"] == "expand" and asked["tool"] == TOOL and asked["call_id"] == seen["call_id"]
    assert asked["requested"] == ["data[].carrier", "data[].id", "no_such"]
    assert asked["returned"] == ["data[].carrier", "data[].id"]        # `id` was never hidden: returned, not recovered
    assert asked["recovered"] == asked["targeted"] == [CARRIER] and asked["index"] == seen["index"] == 0
    # The original result is the call at that position of the run's trace.
    trace = [json.loads(line) for line in (tmp_path / "traces" / "run-0004.jsonl").open()]
    assert trace[asked["index"]]["output"] == orders.output(**args(4)) and "carrier" not in sent.split(MARK)[0]


def test_repeats_and_retries_are_one_piece_of_evidence_and_one_expanded_result(tmp_path):
    tt = learned(tmp_path)
    run(tt, 4, [NOTE], times=5)
    assert runs_of(tmp_path, NOTE) == {"requested": ["run-0004"], "recovered": ["run-0004"]}
    assert state(tmp_path)["expanded"] == {TOOL: 1} and state(tmp_path)["trimmed"] == {TOOL: 1}
    notes = [json.loads(line) for line in (tmp_path / "evidence" / "run-0004.jsonl").open() if '"expand"' in line]
    assert [n["repeat"] for n in notes] == [False, True, True, True, True]


def test_a_run_without_answer_still_shows_what_was_asked(tmp_path):
    tt = learned(tmp_path)
    sent = tt.output(TOOL, args(4), orders.output(**args(4)))
    tt.expand({"call_id": sent.rsplit("call_id=", 1)[1].split()[0], "fields": [CARRIER]})
    tt.done(None)
    assert runs_of(tmp_path, CARRIER) == {"requested": ["run-0004"], "recovered": ["run-0004"]}


# -- promotion ---------------------------------------------------------------------

def test_promotion_is_off_by_default_and_evidence_is_recorded_all_the_same(tmp_path):
    tt = learned(tmp_path)
    for k in (4, 5):
        run(tt, k, [NOTE])
    assert len(runs_of(tmp_path, NOTE)["requested"]) == 2 and ev(tmp_path)["promotions"] == []
    assert NOTE in dropped(tmp_path)
    report = json.loads((tmp_path / "report.json").read_text())["adaptive"]
    assert report["settings"]["promote"] is False and report["evidence"][TOOL]["fields"][NOTE]["requested"] == 2


def test_a_field_asked_for_by_name_in_two_runs_is_promoted_and_then_kept(tmp_path):
    tt = learned(tmp_path, promote=True)
    assert MARK in run(tt, 4, [NOTE]) and NOTE in dropped(tmp_path)    # one run is not enough
    run(tt, 5, [NOTE])
    [p] = ev(tmp_path)["promotions"]
    assert (p["tool"], p["field"], p["reason"], p["runs"], p["status"]) == (
        TOOL, NOTE, "requested", ["run-0004", "run-0005"], "active")
    assert p["threshold"] == 2 and p["decided_after"] == "run-0005" and p["evidence"]["requested"] == 2
    assert NOTE in kept(tmp_path)
    sent = run(tt, 6)
    assert json.loads(sent.split(MARK)[0])["data"][0]["refund_note"] == newest(6)["refund_note"]
    assert CARRIER in dropped(tmp_path) and "carrier" not in sent.split(MARK)[0]      # the rest is still removed


def test_two_requests_in_one_run_do_not_promote(tmp_path):
    tt = learned(tmp_path, promote=True)
    sent = [tt.output(TOOL, args(k), orders.output(**args(k))) for k in (4, 5)]
    for s in sent:
        tt.expand({"call_id": s.rsplit("call_id=", 1)[1].split()[0], "fields": [NOTE]})
    tt.done(answer(4))
    assert runs_of(tmp_path, NOTE)["requested"] == ["run-0004"] and ev(tmp_path)["promotions"] == []


def test_a_field_reused_after_recovery_is_promoted_after_one_run(tmp_path):
    tt = learned(tmp_path, promote=True)
    run(tt, 4, [CARRIER], f" Carrier: {newest(4)['carrier']}.")
    [p] = ev(tmp_path)["promotions"]
    assert (p["field"], p["reason"], p["runs"], p["threshold"]) == (CARRIER, "reused", ["run-0004"], 1)
    assert "carrier" in run(tt, 5).split(MARK)[0]


def test_the_thresholds_are_configurable(tmp_path):
    tt = learned(tmp_path, promote=True, promote_requested_runs=3)
    for k in (4, 5):
        run(tt, k, [NOTE])
    assert ev(tmp_path)["promotions"] == []
    run(tt, 6, [NOTE])
    assert [p["field"] for p in ev(tmp_path)["promotions"]] == [NOTE]
    with pytest.raises(ValueError):
        Adaptive(promote_requested_runs=0)


def test_without_promotion_the_whole_tool_is_switched_off_and_with_it_only_the_field_comes_back(tmp_path):
    # The agent needs `refund_note` in every run and never writes its value.
    plain, targeted = learned(tmp_path / "a"), learned(tmp_path / "b", promote=True)
    for k in range(4, 9):
        run(plain, k, [NOTE])
        run(targeted, k, [NOTE])
    assert state(tmp_path / "a")["off"] == [TOOL] and not plain.trimming
    assert state(tmp_path / "b")["off"] == [] and targeted.trimming
    assert NOTE in kept(tmp_path / "b") and CARRIER in dropped(tmp_path / "b")
    # The counters of the tool started again at the promotion: later results are filtered and not expanded.
    assert state(tmp_path / "b")["trimmed"] == {TOOL: 3} and state(tmp_path / "b")["expanded"] == {}


def twice(tt, k, fields=(), extra=""):
    """A run in which the tool is called twice, as the agents of the study do."""
    sent = [tt.output(TOOL, args(k), orders.output(**args(k))), tt.output(TOOL, args(k + 100), orders.output(**args(k + 100)))]
    for s in sent:
        missing = [f for f in fields if f.rsplit(".", 1)[-1] not in s.split(MARK)[0]]
        if missing and MARK in s:
            tt.expand({"call_id": s.rsplit("call_id=", 1)[1].split()[0], "fields": missing})
    tt.done(answer(k, extra))
    return sent


def test_a_field_that_came_back_by_itself_does_not_get_the_tool_switched_off(tmp_path):
    # Found in the dry run of protocol-v04.md, before any paid run: a tool called twice
    # a run, its field asked back and written in run 1 and kept from run 2. After run 2:
    # four filtered results, two expanded, and the tool was switched off for nothing.
    tt = learned(tmp_path)
    assert all(MARK in s for s in twice(tt, 4, [CARRIER], f" Carrier: {newest(4)['carrier']}."))
    assert CARRIER in kept(tmp_path) and ev(tmp_path)["promotions"] == []           # ordinary learning, no promotion
    assert ev(tmp_path)["resumed"] == [{"tool": TOOL, "field": CARRIER, "after": "run-0004",
                                         "recovered_in": ["run-0004"]}]
    assert state(tmp_path)["trimmed"] == {} and state(tmp_path)["expanded"] == {}   # the count started again
    for k in (5, 6, 7):
        sent = twice(tt, k, [CARRIER], f" Carrier: {newest(k)['carrier']}.")
        assert all(MARK in s and "carrier" in s.split(MARK)[0] for s in sent)       # still trimmed, with the field
    assert state(tmp_path)["off"] == [] and tt.trimming and state(tmp_path)["trimmed"] == {TOOL: 6}
    report = json.loads((tmp_path / "report.json").read_text())["adaptive"]
    assert [r["field"] for r in report["resumed"]] == [CARRIER]
    assert "kept by ordinary learning" in (tmp_path / "report.html").read_text()


def test_a_tool_that_goes_on_asking_for_other_fields_is_still_switched_off(tmp_path):
    tt = learned(tmp_path)
    twice(tt, 4, [CARRIER], f" Carrier: {newest(4)['carrier']}.")                   # settled: the count restarts
    assert state(tmp_path)["off"] == []
    for k in (5, 6):
        twice(tt, k, [NOTE])                                                        # another field, never written
    assert state(tmp_path)["off"] == [TOOL] and not tt.trimming
    assert [r["field"] for r in ev(tmp_path)["resumed"]] == [CARRIER]               # recorded once


def four_calls(tt, k, expand=2):
    """A run with four calls of the tool, the first `expand` of them asked back whole, nothing of it written."""
    for i in range(4):
        sent = tt.output(TOOL, args(k + 100 * i), orders.output(**args(k + 100 * i)))
        if i < expand and MARK in sent:
            tt.expand({"call_id": sent.rsplit("call_id=", 1)[1].split()[0], "fields": [NOTE]})
    tt.done(answer(k))


def test_a_tool_is_switched_off_on_two_runs_by_default_and_on_one_when_asked(tmp_path):
    # What happened in E2 on the real case: four filtered results in the first run, two of them
    # expanded, and the tool stopped being trimmed on the evidence of that run alone. Since 02c the
    # default asks for two runs; switch_off_runs=1 restores the behaviour up to 0.3.5.
    assert Adaptive().switch_off_runs == 2
    as_before, careful = learned(tmp_path / "a", switch_off_runs=1), learned(tmp_path / "b")
    four_calls(as_before, 4)
    four_calls(careful, 4)
    assert state(tmp_path / "a")["off"] == [TOOL] and not as_before.trimming
    assert state(tmp_path / "b")["off"] == [] and careful.trimming
    assert state(tmp_path / "b")["expanded"] == {TOOL: 2} and state(tmp_path / "b")["expand_runs"] == {TOOL: ["run-0004"]}
    # A run without requests changes nothing; a second run with requests is the second piece of evidence.
    four_calls(careful, 5, expand=0)
    assert state(tmp_path / "b")["off"] == []
    four_calls(careful, 6, expand=4)
    assert state(tmp_path / "b")["off"] == [TOOL] and not careful.trimming
    with pytest.raises(ValueError):
        Adaptive(switch_off_runs=0)


def test_the_runs_asked_for_are_the_most_any_setting_asks():
    from tracetrim.auto import over_expanded, runs_needed
    assert runs_needed(Adaptive()) == 2 and runs_needed(Adaptive(switch_off_runs=1)) == 0
    assert runs_needed(Adaptive(promote=True)) == 2 and runs_needed(Adaptive(promote=True, switch_off_runs=1)) == 2
    assert runs_needed(Adaptive(switch_off_runs=3)) == 3 and runs_needed(Adaptive(promote=True, switch_off_runs=3)) == 3
    assert runs_needed(Adaptive(promote=True, promote_requested_runs=4, switch_off_runs=2)) == 4
    assert over_expanded(4, 2, 1) and not over_expanded(3, 3, 1) and not over_expanded(4, 1, 1) and not over_expanded(4, 2, 1, 2)


def test_an_evidence_file_without_the_newer_key_loads(tmp_path):
    (tmp_path / "evidence.json").write_text(json.dumps({"format": evidence.FORMAT, "tools": {}, "promotions": []}))
    assert evidence.load(tmp_path / "evidence.json")["resumed"] == []


def test_a_switched_off_tool_is_exposed_whole_and_counted_nowhere(tmp_path):
    tt = learned(tmp_path)
    for k in range(4, 9):
        run(tt, k, [NOTE])
    assert state(tmp_path)["off"] == [TOOL]
    before = state(tmp_path)
    tt = AutoTrim(tmp_path, min_runs=3, adaptive=Adaptive(sample_rate=1.0, sample_max_share=10))
    obs = tt.engine.observe(ToolCall(TOOL, args(9)), ToolResult(orders.output(**args(9))))
    assert (obs.exposure, obs.call_id, obs.hidden) == ("full", None, ()) and json.loads(obs.text) == orders.list_orders(**args(9))
    tt.done(answer(9))
    after = state(tmp_path)
    assert all(after[k] == before[k] for k in ("trimmed", "expanded", "sampling", "full_cheaper"))


def test_a_request_that_gives_back_nothing_removed_does_not_count_against_the_tool(tmp_path):
    tt = learned(tmp_path)
    for k in range(4, 9):
        sent = tt.output(TOOL, args(k), orders.output(**args(k)))
        cid = sent.rsplit("call_id=", 1)[1].split()[0]
        assert tt.expand({"call_id": cid, "fields": ["no_such_field"]}).startswith("Error: no field matches")
        assert "status" in tt.expand({"call_id": cid, "fields": ["data[].status"]})      # it was never removed
        tt.done(answer(k))
    assert state(tmp_path)["expanded"] == {} and state(tmp_path)["off"] == [] and tt.trimming


def test_when_requests_stay_frequent_after_promotion_the_tool_is_still_switched_off(tmp_path):
    tt = learned(tmp_path, promote=True)
    for k in range(4, 6):
        run(tt, k, [NOTE])
    assert [p["field"] for p in ev(tmp_path)["promotions"]] == [NOTE]
    for k in range(6, 12):
        run(tt, k, ["*"])                                              # everything, every time: no field to promote
    assert state(tmp_path)["off"] == [TOOL] and not tt.trimming


def test_the_report_shows_promoted_fields_their_observations_and_the_effect_on_tokens(tmp_path):
    tt = learned(tmp_path, promote=True)
    for k in (4, 5):
        run(tt, k, [NOTE])
    report = json.loads((tmp_path / "report.json").read_text())
    a = report["adaptive"]
    [p] = a["promotions"]
    assert p["runs"] == ["run-0004", "run-0005"] and p["field_tokens"] > 0 and p["field_calls"] == 5
    e = a["effect"][TOOL]
    assert e["calls"] == 5 and e["without_promotions"] < e["with_promotions"] < e["raw"]
    assert report["tools"][TOOL]["fields"][NOTE]["status"] == "promoted"
    assert report["auto"]["promoted"] == {TOOL: [NOTE]}
    page = (tmp_path / "report.html").read_text()
    assert "Promoted fields" in page and "requested by name" in page and "run-0004, run-0005" in page


def test_a_promotion_can_be_taken_back_and_is_not_made_again(tmp_path, capsys):
    tt = learned(tmp_path, promote=True)
    for k in (4, 5):
        run(tt, k, [NOTE])
    main(["promotions", str(tmp_path)])
    assert capsys.readouterr().out.startswith(f"active    {TOOL}  {NOTE}  (requested in 2 runs: run-0004, run-0005)")
    main(["promotions", str(tmp_path), "--revert", TOOL, NOTE])
    assert "reverted" in capsys.readouterr().out
    assert NOTE in dropped(tmp_path)                                   # the config before the promotion
    again = AutoTrim(tmp_path, min_runs=3, adaptive=Adaptive(promote=True))
    for k in (6, 7, 8):
        assert "refund_note" not in run(again, k, [NOTE]).split(MARK)[0]
    assert [p["status"] for p in ev(tmp_path)["promotions"]] == ["reverted"]
    main(["promotions", str(tmp_path), "--restore", TOOL, NOTE])
    assert NOTE in kept(tmp_path) and "active" in capsys.readouterr().out
    main(["promotions", str(tmp_path), "--revert"])                    # everything
    assert NOTE in dropped(tmp_path)


# -- sampling without filter -----------------------------------------------------------

def sampled_runs(tmp_path, n=40, **adaptive) -> list[bool]:
    tt = learned(tmp_path, **adaptive)
    return [MARK not in run(tt, k) for k in range(4, 4 + n)]


def test_sampling_is_off_by_default(tmp_path):
    assert not any(sampled_runs(tmp_path, n=10)) and state(tmp_path)["sampling"] == {}


def test_a_sampled_result_is_the_whole_output_and_the_tool_ran_once(tmp_path):
    engine = Engine(Learner(tmp_path, min_runs=3, adaptive=Adaptive(sample_rate=1.0, sample_max_share=10)))
    for k in (1, 2, 3):
        engine.observe(ToolCall(TOOL, args(k)), ToolResult(orders.output(**args(k))))
        engine.close(answer(k))
    first = engine.observe(ToolCall(TOOL, args(4)), ToolResult(orders.output(**args(4))))
    second = engine.observe(ToolCall(TOOL, args(5)), ToolResult(orders.output(**args(5))))
    assert first.exposure == FILTERED                                   # nothing removed yet to pay for a sample
    assert second.exposure == SAMPLED and second.call_id is None and MARK not in second.text
    assert json.loads(second.text) == orders.list_orders(**args(5))     # every field, re-serialised
    assert CARRIER in second.hidden and engine.trimmer.originals.keys() == {first.call_id}
    # The engine was handed each raw output once and never asks for one: one event per call.
    assert [e["arguments"] for e in engine.learner.events] == [args(4), args(5)]


def test_through_the_proxy_a_sampled_call_reaches_the_upstream_tool_once(tmp_path):
    log = tmp_path / "calls.log"
    ad = Adaptive(sample_rate=1.0, sample_max_share=10)

    async def go():
        async with demo.Mcp(tmp_path / "work", ad, call_log=log) as s:
            out = []
            for k in range(1, 7):
                out.append((await s.call(args(k)))[1])
                await s.done(answer(k))
            return out

    sent = asyncio.run(go())
    assert [MARK in s for s in sent] == [False, False, False, True, False, False]     # 5 and 6 sampled
    assert json.loads(sent[4]) == orders.list_orders(**args(5))
    assert log.read_text().split() == [f"cus_{k:03d}" for k in range(1, 7)]           # six calls, six executions
    assert state(tmp_path / "work")["sampling"][TOOL]["sampled"] == 2


def test_sampling_is_reproducible_with_a_seed_and_goes_on_after_a_restart(tmp_path):
    kw = {"sample_rate": 0.3, "sample_max_share": 10, "sample_seed": 11}
    one = sampled_runs(tmp_path / "a", **kw)
    assert one == sampled_runs(tmp_path / "b", **kw) and 0 < sum(one) < len(one)
    assert one != sampled_runs(tmp_path / "c", **(kw | {"sample_seed": 12}))
    # The same forty runs over four processes.
    tt = learned(tmp_path / "d", **kw)
    parts = []
    for start in range(4, 44, 10):
        parts += [MARK not in run(tt, k) for k in range(start, start + 10)]
        tt = AutoTrim(tmp_path / "d", min_runs=3, adaptive=Adaptive(**kw))
    assert parts == one


@pytest.mark.parametrize("share", [0.0, 0.2, 0.5])
def test_the_cost_of_sampling_stays_within_its_share_of_what_filtering_removes(tmp_path, share):
    sampled = sampled_runs(tmp_path, sample_rate=1.0, sample_max_share=share)
    s = state(tmp_path)["sampling"][TOOL]
    assert s["eligible"] == 40 and s["sampled"] == sum(sampled) and s["added"] <= share * s["removed"]
    assert (sum(sampled) == 0) is (share == 0.0)
    if share:
        assert sum(sampled) >= 40 * share / (1 + share) - 1             # and it does use the share it is given


def test_sampled_results_are_counted_apart_from_filtered_ones(tmp_path):
    sampled = sampled_runs(tmp_path, n=20, sample_rate=0.5, sample_max_share=10, sample_seed=3)
    st = state(tmp_path)
    assert st["trimmed"] == {TOOL: sampled.count(False)} and st["sampling"][TOOL]["sampled"] == sampled.count(True)
    a = json.loads((tmp_path / "report.json").read_text())["adaptive"]
    assert a["expand"][TOOL]["filtered"] == sampled.count(False) and a["sampling"][TOOL]["eligible"] == 20
    fields = a["evidence"][TOOL]["fields"]
    # Seen whole and not seen reused: recorded as that, and nothing is concluded from it.
    assert fields[CARRIER]["seen_full"] == sampled.count(True) and fields[CARRIER]["used_in_full"] == 0
    assert CARRIER in dropped(tmp_path) and ev(tmp_path)["promotions"] == []
    assert "Filtered and sampled results" in (tmp_path / "report.html").read_text()


def test_a_field_used_when_seen_whole_is_kept_from_then_on(tmp_path):
    # The agent never calls expand: it uses the carrier only when the result shows it.
    def lazy(tt, k):
        sent = tt.output(TOOL, args(k), orders.output(**args(k)))
        carrier = json.loads(sent.split(MARK)[0])["data"][0].get("carrier")
        tt.done(answer(k, f" Carrier: {carrier}." if carrier else " Carrier: not available."))
        return MARK in sent, carrier is not None

    tt = learned(tmp_path, promote=True, sample_rate=1.0, sample_max_share=10)
    # Filtered without it, then sampled with it.
    assert [lazy(tt, k) for k in (4, 5)] == [(True, False), (False, True)]
    # From then on a filtered result carries it (sampling switched off, to see one).
    assert lazy(AutoTrim(tmp_path, min_runs=3), 6) == (True, True)
    [p] = ev(tmp_path)["promotions"]
    assert (p["field"], p["reason"], p["runs"]) == (CARRIER, "used_in_full", ["run-0005"])


def test_new_fields_still_pass_and_a_changed_schema_does_not_break_promotions(tmp_path):
    tt = learned(tmp_path, promote=True, sample_rate=0.5, sample_max_share=10)
    for k in (4, 5):
        run(tt, k, [NOTE])                                             # `refund_note` promoted
    payload = orders.list_orders(**args(9))
    for o in payload["data"]:
        o["eta"] = "arrives within two working days"                   # a field the server adds
        o["refund_policy"] = o.pop("refund_note")                      # ... and one it renames
    sent = AutoTrim(tmp_path, min_runs=3).output(TOOL, args(9), json.dumps(payload))
    first = json.loads(sent.split(MARK)[0])["data"][0]
    assert first["eta"] and first["refund_policy"] and "carrier" not in first
    assert NOTE in kept(tmp_path)                                      # the promotion stays, with nothing to apply to


# -- the simple way for small outputs ------------------------------------------------------

def status_runs(session_factory, work, n=6, **adaptive):
    """The same small-result workflow through an adapter: what the model received, and the state."""
    async def go():
        async with session_factory(work, Adaptive(**adaptive)) as s:
            sent = []
            for k in range(1, n + 1):
                raw, got = await s.call({"order": f"ord_{k:04d}"}, tool="order_status")
                sent.append(got)
                o = json.loads(raw)
                await s.done(f"Order {o['id']} is {o['status']}.")
            return sent
    return asyncio.run(go()), state(work)


def test_a_small_result_that_filtering_would_not_shorten_is_passed_whole(tmp_path):
    sent, st = status_runs(demo.FunctionCalling, tmp_path, sample_rate=1.0, sample_max_share=10)
    projection = json.loads((tmp_path / "report.projection.json").read_text())
    assert projection["dropped"]["order_status"] == ["lane"]           # learned: `lane` is removed ...
    for k, text in enumerate(sent[3:], 4):                             # ... yet every result goes on whole
        assert MARK not in text and json.loads(text) == orders.order_status(f"ord_{k:04d}")
        assert text == json.dumps(orders.order_status(f"ord_{k:04d}"))                 # re-serialised, no line
    # Counted as its own kind: not filtered, not sampled, and no sampling quota used.
    assert st["full_cheaper"] == {"order_status": 3} and st["trimmed"] == {} and st["sampling"] == {}
    notes = [json.loads(line) for p in sorted((tmp_path / "evidence").glob("*.jsonl")) for line in p.open()]
    assert [(n["exposure"], n["call_id"], n["hidden"]) for n in notes] == [("full_cheaper", None, ["lane"])] * 3
    assert evidence.load(tmp_path / "evidence.json")["tools"] == {}                    # no evidence drawn from it
    report = json.loads((tmp_path / "report.json").read_text())["adaptive"]
    assert report["full_cheaper"] == {"order_status": 3} and report["sampling_unit"] == "tokens"
    page = (tmp_path / "report.html").read_text()
    assert "filtering would not have been shorter" in page and "run-time comparisons are in" in page


def test_a_large_result_is_filtered_as_before_and_the_two_are_counted_apart(tmp_path):
    tt = learned(tmp_path)
    assert MARK in run(tt, 4) and state(tmp_path)["full_cheaper"] == {} and state(tmp_path)["trimmed"] == {TOOL: 1}


def test_the_simple_way_is_the_same_through_the_proxy_and_autotrim(tmp_path):
    fc, fc_state = status_runs(demo.FunctionCalling, tmp_path / "fc")
    mcp, mcp_state = status_runs(demo.Mcp, tmp_path / "mcp")
    assert fc == mcp and all(MARK not in t for t in fc)
    for key in ("runs", "complete_runs", "trimmed", "expanded", "sampling", "full_cheaper", "off"):
        assert fc_state[key] == mcp_state[key]
    assert fc_state["full_cheaper"] == {"order_status": 3}


def test_without_a_tokenizer_the_comparison_is_on_characters_and_says_so(tmp_path, monkeypatch):
    import tracetrim.measure as measure
    monkeypatch.setattr(measure, "counter", lambda: ((lambda s: len(s or "")), "characters"))
    tt = learned(tmp_path)
    assert tt.trimmer.count("abcd") == 4 and tt.trimmer.unit == "characters"
    assert MARK in run(tt, 4)                                          # the decision is still taken
    assert json.loads((tmp_path / "report.json").read_text())["adaptive"]["sampling_unit"] == "characters"


def test_a_reverted_field_that_learning_would_keep_stays_removed_and_is_reported(tmp_path, capsys):
    tt = learned(tmp_path, promote=True)
    run(tt, 4, [CARRIER], f" Carrier: {newest(4)['carrier']}.")        # written: ordinary learning keeps it too
    assert CARRIER in kept(tmp_path)
    main(["promotions", str(tmp_path), "--revert", TOOL, CARRIER])
    capsys.readouterr()
    assert CARRIER in dropped(tmp_path)                                # the operator's choice stands
    report = json.loads((tmp_path / "report.json").read_text())["adaptive"]
    assert report["conflicts"] == [{"tool": TOOL, "field": CARRIER}]
    assert "To review: reverted fields that ordinary learning would keep" in (tmp_path / "report.html").read_text()


# -- persistence -----------------------------------------------------------------------

def test_an_interrupted_write_leaves_the_previous_file_and_the_run_open(tmp_path, monkeypatch):
    tt = learned(tmp_path)
    before = (tmp_path / "state.json").read_text()
    tt.output(TOOL, args(4), orders.output(**args(4)))
    real = os.replace

    def dies(src, dst):
        raise OSError("killed while writing")
    monkeypatch.setattr(store.os, "replace", dies)
    with pytest.raises(OSError):
        tt.done(answer(4))
    monkeypatch.setattr(store.os, "replace", real)
    assert (tmp_path / "state.json").read_text() == before             # whole, and the old one
    assert not list(tmp_path.rglob(".*.tmp")) and not (tmp_path / "traces" / "run-0004.jsonl").exists()
    assert len(tt.learner.events) == 1                                 # the run is still open: try again
    tt.done(answer(4))
    assert state(tmp_path)["runs"] == 4 and (tmp_path / "traces" / "run-0004.jsonl").exists()


def test_leftovers_and_a_truncated_state_do_not_stop_a_restart(tmp_path):
    learned(tmp_path)
    (tmp_path / ".state.json.999.tmp").write_text('{"runs": ')         # a writer killed mid-way
    (tmp_path / "state.json").write_text('{"runs": 3, "compl')         # written in place by an older version
    lr = Learner(tmp_path, min_runs=3)
    assert not list(tmp_path.glob(".*.tmp")) and (tmp_path / "state.json.corrupt").exists()
    assert lr.initial_policy() is not None                             # what was learned is in the other files
    lr.call(TOOL, args(4), orders.output(**args(4)))
    assert lr.done(answer(4)).name == "run-0004.jsonl"                 # numbered from the traces


def test_calls_and_learning_can_touch_the_state_at_the_same_time(tmp_path):
    tt = learned(tmp_path, promote=True, sample_rate=0.5, sample_max_share=10)
    errors, stop = [], threading.Event()

    def calls():
        try:
            while not stop.is_set():
                sent = tt.output(TOOL, args(7), orders.output(**args(7)))
                if MARK in sent:
                    tt.expand({"call_id": sent.rsplit("call_id=", 1)[1].split()[0], "fields": [NOTE]})
        except Exception as e:                                          # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=calls)
    t.start()
    try:
        for _ in range(8):
            tt.learner.relearn()
    finally:
        stop.set()
        t.join()
    assert errors == []
    json.loads((tmp_path / "state.json").read_text())
    tt.done(answer(7))
    assert state(tmp_path)["runs"] == 4


def test_a_035_work_directory_works_with_the_new_functions_on(tmp_path):
    import shutil
    work = tmp_path / "work"
    shutil.copytree(ROOT / "tests" / "fixtures" / "workdir_035", work)
    (work / "traces").mkdir()
    for p in work.glob("run-*.jsonl"):
        p.rename(work / "traces" / p.name)
    sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
    import billing
    tt = AutoTrim(work, min_runs=3, adaptive=Adaptive(promote=True, sample_rate=0.1))
    assert tt.trimming and tt.learner.state["expand_runs"] == {} and not (work / "evidence.json").exists()
    sent = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    tt.expand({"call_id": sent.rsplit("call_id=", 1)[1].split()[0], "fields": ["data[].footer"]})
    tt.done(billing.answer(3) + " Thank you for your business with us.")
    assert [p["field"] for p in evidence.load(work / "evidence.json")["promotions"]] == ["data[].footer"]


# -- the same decisions through both adapters --------------------------------------------

@pytest.mark.parametrize("variant, arm", [("invisible", "promotion+sampling"), ("visible", "baseline"),
                                          ("lazy", "sampling")])
def test_the_proxy_and_autotrim_decide_the_same(variant, arm):
    count = demo.stable_count()
    fc, mcp = (asyncio.run(demo.scenario(adapter, variant, arm, count)) for adapter in ("function", "mcp"))
    origins = [{t: e.pop("origin") for t, e in r["decisions"]["evidence"].items()} for r in (fc, mcp)]
    # Same calls, same answers, same configuration: same exposure of every result, same
    # counters, same evidence, same projection.
    assert fc["decisions"] == mcp["decisions"]
    # The field the agent wrote came back by itself in both, and restarted the tool's count in both.
    assert bool(fc["decisions"]["resumed"]) is (variant == "visible")
    assert fc["promotions"] == mcp["promotions"] and fc["kept_again_from_shift_run"] == mcp["kept_again_from_shift_run"]
    # Only the proxy knows where a call really went.
    if origins[1]:
        assert origins[0] == {TOOL: None} and origins[1] == {TOOL: {"server": "upstream", "tool": TOOL}}
