"""The offline evaluation of projections per mode of use (experiments/modes.py)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments import modes  # noqa: E402
from tracetrim.measure import tokenizer  # noqa: E402
from tracetrim.traces import Event, Trace  # noqa: E402
from tracetrim.usage import analyse  # noqa: E402

WORDS = "amber birch cedar delta ember fjord grove heron inlet jasper kelp lagoon".split()


def run(i: int, split: bool) -> Trace:
    """Two calls of one tool, `kind` a and b. With `split`, the answer quotes `alpha` of the first and `beta` of the
    second: two ways of using the tool. Without, it quotes both fields of both."""
    events, quoted = [Event("prompt", "Summarise.")], []
    for kind in ("a", "b"):
        # Words that occur nowhere else: a value quoted from one call must not look like a use of another.
        row = {"id": f"{kind}{i}", "alpha": " ".join(f"{w}{kind}alpha{i}" for w in WORDS[:6]),
               "beta": " ".join(f"{w}{kind}beta{i}" for w in WORDS[6:])}
        events.append(Event("call", json.dumps({"kind": kind, "query": f"q{i}{kind}", "record_id": f"r{i}"}), "search",
                            json.dumps({"rows": [row]})))
        quoted += [row["alpha"], row["beta"]] if not split else [row["alpha" if kind == "a" else "beta"]]
    return Trace(f"r{i}", events + [Event("message", " / ".join(quoted))])


def evaluate(split: bool) -> dict:
    return modes.evaluate_runs([analyse(run(i, split)) for i in range(6)], [], tokenizer())


def test_a_tool_used_in_two_ways_gains_from_a_projection_per_way():
    t = evaluate(split=True)["tools"]["search"]
    assert list(t["arguments"]) == ["kind"]                         # `query` has too many values, `record_id` is an identifier
    a = t["arguments"]["kind"]
    assert t["general"]["calls_losing_a_used_field"] == 0 and a["calls_losing_a_used_field"] == 0
    assert a["removed"] > t["general"]["removed"] and a["points_removed_more_than_general"] >= modes.BENEFIT_POINTS and a["benefit"]
    assert a["profiles_evaluated"] == 2 and a["calls_per_profile_min"] == 6 and a["new_cases_covered_by_the_general_projection"] == 0
    assert {v: p["fields_used_here_only"] for v, p in a["profiles"].items()} == {'"a"': ["rows[].alpha"], '"b"': ["rows[].beta"]}


def test_a_tool_used_in_one_way_gains_nothing_from_it():
    t = evaluate(split=False)["tools"]["search"]
    a = t["arguments"]["kind"]
    assert a["removed"] == t["general"]["removed"] and not a["benefit"] and a["fields_lost"] == []


def test_a_profile_with_too_few_calls_elsewhere_is_covered_by_the_general_projection():
    usages = [analyse(run(i, True)) for i in range(6)]
    rare = analyse(run(5, True))[:1]
    rare[0].arguments = json.dumps({"kind": "c", "query": "x"})
    a = modes.evaluate_runs(usages + [rare], [], tokenizer())["tools"]["search"]["arguments"]["kind"]
    assert a["profiles_evaluated"] == 3 and a["calls_per_profile_min"] == 1 and a["new_cases_covered_by_the_general_projection"] == 1


def test_identifiers_and_arguments_with_many_values_are_not_modes():
    calls = [("t", {"customer": f"cus_Ab12Cd34Ef{i % 2}", "email": f"u{i % 2}@example.com", "q": f"q{i}", "sort": ["a", "b"][i % 2],
                    "invoice_id": i % 2, "limit": 10}) for i in range(12)]
    c = modes.candidates(calls, modes.Counter({"t": 12}))
    assert list(c["t"]) == ["sort"] and dict(c["t"]["sort"]) == {'"a"': 6, '"b"': 6}
    assert modes.candidates(calls, modes.Counter({"t": 9})) == {}                      # too few calls evaluated
    # Calls without the argument are one more profile.
    c = modes.candidates(calls + [("t", {"q": "z"})] * 2, modes.Counter({"t": 14}))
    assert c["t"]["sort"][modes.ABSENT] == 2


def test_the_runs_evaluated_are_those_in_which_the_model_saw_whole_results():
    ok = ["A3", "E1-R-2", "E1-S-5", "E3-R-1", "E3-pilot-R-2", "E3-S-8"]
    no = ["E1-T-1", "E1-ST-3", "E2-base-s1-r1", "E2-promotion+sampling-s2-r5", "B-raw-1"]
    assert all(modes.WHOLE.match(n) for n in ok) and not any(modes.WHOLE.match(n) for n in no)
