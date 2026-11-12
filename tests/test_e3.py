"""E3 of protocol-v04-e3.md, without spending: the Gemini adapter, the stand-in that answers from
recorded outputs, the schedule, the stop rules, the pilot's rule and the dry run.

No model and no provider is reached by any of these tests. The dry run needs
the original study's traces and this checkout's own E1 runs, which are not in
git: without them it is skipped.
"""
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments import e3, recorded, v04  # noqa: E402
from experiments.tools import BLOCKED  # noqa: E402
from experiments.v04 import Run, Where  # noqa: E402

types = pytest.importorskip("google.genai.types")


# -- the adapter: several calls in a turn, results returned together ---------------------

class FakeModels:
    """Stands for the SDK's client: returns scripted responses and keeps what it was sent."""

    def __init__(self, responses):
        self.responses, self.sent = list(responses), []

    async def generate_content(self, *, model, contents, config):
        self.sent.append({"model": model, "contents": list(contents), "config": config})
        return self.responses.pop(0)


def fake_client(responses):
    models = FakeModels(responses)
    return SimpleNamespace(aio=SimpleNamespace(models=models)), models


def response(parts, prompt=100, cached=0, answer=20, thoughts=0):
    content = types.Content(role="model", parts=parts)
    return SimpleNamespace(candidates=[SimpleNamespace(content=content, finish_reason="STOP")],
                           usage_metadata=SimpleNamespace(prompt_token_count=prompt, cached_content_token_count=cached,
                                                          candidates_token_count=answer, thoughts_token_count=thoughts))


def call_part(name, args, id_, signature=None):
    return types.Part(function_call=types.FunctionCall(name=name, args=args, id=id_), thought_signature=signature)


TOOLS = [{"type": "function", "name": "lookup", "description": "Look a customer up.", "strict": False,
          "parameters": {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]}}]


def test_several_calls_of_one_response_are_one_turn_in_the_models_order():
    from experiments.gemini import Gemini
    first = response([types.Part(text="planning", thought=True),
                      call_part("lookup", {"email": "a@example.com"}, "id-a", b"sig-1"),
                      call_part("lookup", {"email": "b@example.com"}, "id-b")], prompt=500, cached=120, answer=40, thoughts=60)
    client, models = fake_client([first, response([types.Part(text='{"done": true}')], prompt=900, cached=500, answer=30)])
    g = Gemini(client)

    async def go():
        r1 = await g.responses.create(model="gemini-3.8-flash", instructions="SYSTEM", tools=TOOLS,
                                      input=[{"role": "user", "content": "two customers"}])
        outputs = [{"type": "function_call_output", "call_id": c.call_id, "output": json.dumps({"n": i})}
                   for i, c in enumerate(r1.output)]
        r2 = await g.responses.create(model="gemini-3.8-flash", instructions="SYSTEM", tools=TOOLS,
                                      previous_response_id=r1.id, input=outputs)
        return r1, r2

    r1, r2 = asyncio.run(go())
    # Two calls, in the order the model gave them; its thinking is not a message.
    assert [(o.type, o.name, json.loads(o.arguments)) for o in r1.output] == [
        ("function_call", "lookup", {"email": "a@example.com"}), ("function_call", "lookup", {"email": "b@example.com"})]
    assert len({o.call_id for o in r1.output}) == 2
    # Billed: input with its cached part, output with the thinking tokens.
    assert (r1.usage.input_tokens, r1.usage.input_tokens_details.cached_tokens, r1.usage.output_tokens) == (500, 120, 100)
    assert [o.type for o in r2.output] == ["message"] and r2.output[0].content[0].text == '{"done": true}'

    one, two = models.sent
    assert one["config"].system_instruction == "SYSTEM" and one["config"].automatic_function_calling.disable is True
    [declaration] = one["config"].tools[0].function_declarations
    assert declaration.name == "lookup" and declaration.parameters_json_schema == TOOLS[0]["parameters"]
    assert one["config"].tool_config is None                       # the calling mode is the model's default
    assert [c.role for c in one["contents"]] == ["user"] and one["contents"][0].parts[0].text == "two customers"
    # Second request: the user turn, the model's turn AS IT CAME, and ONE content with both results.
    assert [c.role for c in two["contents"]] == ["user", "model", "user"]
    assert two["contents"][1] is first.candidates[0].content
    assert two["contents"][1].parts[1].thought_signature == b"sig-1"
    results = two["contents"][2].parts
    assert [(p.function_response.name, p.function_response.id, p.function_response.response) for p in results] == [
        ("lookup", "id-a", {"n": 0}), ("lookup", "id-b", {"n": 1})]


def test_a_tool_text_becomes_the_object_a_function_response_carries():
    from experiments.gemini import function_response_payload as payload
    assert payload('{"data": [1, 2]}') == {"data": [1, 2]}
    assert payload("[1, 2]") == {"result": [1, 2]} and payload("Error: API Error.") == {"output": "Error: API Error."}
    assert payload(BLOCKED) == {"output": BLOCKED}


def test_text_beside_calls_is_a_message_and_an_empty_response_ends_the_run():
    from experiments.gemini import Gemini
    both = response([types.Part(text="I will check."), call_part("lookup", {"email": "a@example.com"}, None)])
    empty = SimpleNamespace(candidates=[], usage_metadata=None)
    client, models = fake_client([both, empty])
    g = Gemini(client)

    async def go():
        r1 = await g.responses.create(model="m", instructions="S", tools=TOOLS, input=[{"role": "user", "content": "x"}])
        r2 = await g.responses.create(model="m", instructions="S", tools=TOOLS, previous_response_id=r1.id,
                                      input=[{"type": "function_call_output", "call_id": r1.output[1].call_id, "output": "{}"}])
        return r1, r2

    r1, r2 = asyncio.run(go())
    assert [o.type for o in r1.output] == ["message", "function_call"]
    assert r2.output == [] and (r2.usage.input_tokens, r2.usage.output_tokens) == (0, 0)
    assert models.sent[1]["contents"][2].parts[0].function_response.id is None      # no id from the model: none sent back


def test_a_request_that_fails_is_made_again_by_the_adapter_and_every_attempt_is_reported():
    from google.genai import errors
    from experiments.gemini import Gemini

    class Flaky(FakeModels):
        def __init__(self, failures, responses):
            super().__init__(responses)
            self.failures = list(failures)

        async def generate_content(self, **kw):
            if self.failures:
                code = self.failures.pop(0)
                raise errors.APIError(code, {"error": {"code": code, "message": "x", "status": "UNAVAILABLE"}})
            return await super().generate_content(**kw)

    models = Flaky([503, 429], [response([types.Part(text="{}")], prompt=50, answer=5)])
    g = Gemini(SimpleNamespace(aio=SimpleNamespace(models=models)), pause=0)
    r = asyncio.run(g.responses.create(model="m", instructions="S", tools=TOOLS, input=[{"role": "user", "content": "x"}]))
    assert r.retries == [{"attempt": 1, "code": 503, "status": "UNAVAILABLE"}, {"attempt": 2, "code": 429, "status": "UNAVAILABLE"}]
    assert (r.usage.input_tokens, r.usage.output_tokens) == (50, 5) and len(models.sent) == 1      # only the one that answered
    # An error that is not transient is not repeated; and after the last attempt the error is raised.
    for failures in ([403], [503, 503, 503, 503]):
        g = Gemini(SimpleNamespace(aio=SimpleNamespace(models=Flaky(failures, []))), pause=0)
        with pytest.raises(errors.APIError):
            asyncio.run(g.responses.create(model="m", instructions="S", tools=TOOLS, input=[{"role": "user", "content": "x"}]))


def test_the_repeats_are_written_in_the_trace_the_meta_file_and_the_ledger(tmp_path):
    class Repeats(v04.Stub):
        async def create(self, **kw):
            r = await super().create(**kw)
            r.retries = [{"attempt": 1, "code": 503, "status": "UNAVAILABLE"}] if self.n == 2 else []
            return r

    script = {"turns": [[{"tool": "order_status", "arguments": {"order": "o1"}}]], "answer": "{}"}

    class Tools:
        schemas, rejections, retries, tool_failures = [], 0, 0, []
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def call(self, name, args): return "{}"

    where = Where(tmp_path)
    (tmp_path / "traces" / "C4").mkdir(parents=True)
    for r in [r for r in e3.schedule() if r.pilot]:
        (tmp_path / "traces" / r.case).mkdir(parents=True, exist_ok=True)
        (tmp_path / "traces" / r.case / f"{r.id}.meta.json").write_text(json.dumps({"usage_per_turn": [{"calls": 1}]}))
    e3.pilot_decision(where).parent.mkdir(parents=True)
    e3.pilot_decision(where).write_text("{}")
    real = v04.base_toolset
    v04.base_toolset = lambda run, dry: Tools()
    try:
        m = asyncio.run(v04.execute(Run("E3", "C4", "R", 1), where, client=Repeats(script), model="stub", dry=True))
    finally:
        v04.base_toolset = real
    assert m["model_retries"] == [{"turn": 1, "attempt": 1, "code": 503, "status": "UNAVAILABLE"}]
    assert m["usage_per_turn"][1]["model_retries"] == [{"attempt": 1, "code": 503, "status": "UNAVAILABLE"}]
    assert "model_retries" not in m["usage_per_turn"][0]
    events = [json.loads(line) for line in (tmp_path / "traces" / "C4" / "E3-R-1.jsonl").open()]
    assert [e.get("model_retries") for e in events if e["kind"] in ("call", "message")] == [None, 1]
    [line] = v04.ledger(where, e3.ledger_path(where))
    assert line["model_retries"] == 1


def test_the_adapter_is_not_in_the_library():
    import subprocess
    code = ("import sys, tracetrim, tracetrim.engine, tracetrim.auto, tracetrim.proxy, tracetrim.skill; "
            "sys.exit(any(m.startswith('google') for m in sys.modules))")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


# -- outputs already recorded, in place of the provider -------------------------------------

def call(tool, output, source="A1", **args):
    return {"tool": tool, "key": recorded.normalise(args), "tokens": recorded.tokens(args), "output": output, "source": source}


POOL = [call("serp", "OUT-agents", keyword="AI agents marketing", depth=10, location_name="Italy"),
        call("serp", "OUT-mcp", "A2", keyword="model context protocol", depth=10, location_name="Italy"),
        call("serp", "OUT-mcp-later", "A3", keyword="model context protocol", depth=10, location_name="Italy"),
        call("volume", "OUT-volume", keywords=["ai agents", "mcp"])]


def answer(r, name, **args):
    out = asyncio.run(r.call(name, args))
    return out, r.last_match, r.last_source


def test_the_same_arguments_get_the_recorded_output_whatever_their_spelling():
    r = recorded.Recorded("C2", POOL, tool_schemas=[])
    assert answer(r, "serp", keyword="AI agents marketing", depth=10, location_name="Italy") == ("OUT-agents", "exact", "A1")
    # Order of the keys, capitals and spaces do not make a different call.
    assert answer(r, "serp", location_name="italy", depth=10, keyword="  ai AGENTS   marketing ")[:2] == ("OUT-agents", "exact")
    # Two recordings of the same call: the first of the pool.
    assert answer(r, "serp", keyword="model context protocol", depth=10, location_name="Italy") == ("OUT-mcp", "exact", "A2")
    assert r.matches == {"exact": 3, "nearest": 0, "none": 0}


def test_other_arguments_get_the_nearest_output_of_the_same_tool_and_say_so():
    r = recorded.Recorded("C2", POOL, tool_schemas=[])
    assert answer(r, "serp", keyword="AI agents for marketing teams", depth=10, location_name="Italy") == ("OUT-agents", "nearest", "A1")
    assert answer(r, "serp", keyword="context protocol servers", depth=10, location_name="Italy")[:2] == ("OUT-mcp", "nearest")
    # Never an output of another tool, however similar the words.
    assert answer(r, "volume", keywords=["model context protocol"]) == ("OUT-volume", "nearest", "A1")
    # Nothing in common: still the same tool, the first of the pool.
    assert answer(r, "serp", query="xyz") == ("OUT-agents", "nearest", "A1")
    # The same question twice gets the same answer.
    assert answer(r, "serp", keyword="context protocol servers", depth=10, location_name="Italy")[0] == "OUT-mcp"
    assert r.matches == {"exact": 0, "nearest": 5, "none": 0}


def test_a_tool_never_recorded_is_an_error_and_the_domain_gate_comes_first():
    r = recorded.Recorded("C2", POOL, tool_schemas=[])
    out, how, _ = answer(r, "on_page_content_parsing", url="https://www.alessiopomaro.it/")
    assert out.startswith("Error: no recorded output") and how == "none"
    assert answer(r, "on_page_content_parsing", url="https://example.com/") == (BLOCKED, None, None)
    assert answer(r, "serp", keyword="site:example.com agents") == (BLOCKED, None, None)
    assert r.rejections == 2 and r.matches == {"exact": 0, "nearest": 0, "none": 1}


def test_the_stand_in_has_no_way_to_the_provider():
    source = (ROOT / "experiments" / "recorded.py").read_text()
    assert "DataForSEO(" not in source and "ClientSession" not in source and "http" not in source.lower().replace("https://example", "")
    assert not hasattr(recorded.Recorded("C2", POOL, tool_schemas=[]), "session")
    # In E3 the two cases of the provider are answered by the stand-in, in a paid run as in a dry one.
    assert Run("E3", "C1", "R", 1).dataforseo is False and Run("E1", "C1", "R", 1).dataforseo is True


# -- schedule, budget, pilot -----------------------------------------------------------------

def test_the_schedule_is_the_pilot_then_rounds_of_both_arms():
    runs = e3.schedule()
    assert [(r.case, r.id) for r in runs[:4]] == [("C4", "E3-pilot-R-1"), ("C4", "E3-pilot-R-2"),
                                                  ("C5", "E3-pilot-R-1"), ("C5", "E3-pilot-R-2")]
    measured = [r for r in runs if not r.pilot]
    assert Counter((r.case, r.arm) for r in measured) == {(c, a): n for c, n in e3.RUNS.items() for a in ("R", "S")}
    assert e3.RUNS == {"C4": 8, "C5": 6, "C2": 3, "C1": 2} and len(runs) == 42
    # The simulated cases to their end, then the cases with recorded outputs: a cap that
    # stops the runs takes them from the cases that say less.
    assert [(r.case, r.arm, r.n) for r in measured[:8]] == [(c, a, n) for n in (1, 2) for c in ("C4", "C5") for a in ("R", "S")]
    assert [r.case for r in measured] == (["C4", "C4", "C5", "C5"] * 6 + ["C4"] * 4 + ["C2", "C2", "C1", "C1"] * 2 + ["C2"] * 2)
    assert [(r.case, r.arm, r.n) for r in measured[-2:]] == [("C2", "R", 3), ("C2", "S", 3)]
    assert all(r.experiment == "E3" and not r.dataforseo for r in runs) and len({(r.case, r.id) for r in runs}) == 42


def test_e3_has_its_own_ledger_prices_and_limits(tmp_path):
    where = Where(tmp_path)
    acct = v04.book(where, Run("E3", "C4", "R", 1))
    assert acct["ledger"] == tmp_path / "out" / "ledger-e3.jsonl" != v04.book(where, Run("E1", "C4", "R", 1))["ledger"]
    assert (acct["cap"], e3.WARN, acct["model"], acct["prices"]) == (35.0, 30.0, "gemini-3.8-flash", "experiments/prices-e3.json")
    assert e3.cost({"input": 1_000_000, "cached": 0, "output": 0}) == 0.75
    assert e3.cost({"input": 1_000_000, "cached": 1_000_000, "output": 1_000_000}) == pytest.approx(0.075 + 3.75)
    # The prices of the original study are not touched.
    assert json.loads((ROOT / "experiments" / "prices.json").read_text())["model"] == "gpt-5.6-terra"


def pilot_metas(where, per_turn):
    for r in [r for r in e3.schedule() if r.pilot]:
        d = where.traces / r.case
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{r.id}.meta.json").write_text(json.dumps({"usage_per_turn": [{"calls": n} for n in per_turn]}))


def spend(where, usd):
    e3.ledger_path(where).parent.mkdir(parents=True, exist_ok=True)
    e3.ledger_path(where).write_text(json.dumps({"case": "C4", "run": "x", "cost_usd": usd}) + "\n")


def test_the_cap_and_the_warning_are_those_of_e3(tmp_path):
    where, run = Where(tmp_path), Run("E3", "C4", "R", 1, pilot=True)
    assert e3.may_start(where, run) is None
    spend(where, 30.0)
    assert e3.may_start(where, run) == "WARNING: 30.00 USD spent on E3, warning at 30, cap 35"
    spend(where, 35.0)
    with pytest.raises(SystemExit, match="stop rule: 35.00 USD already spent on E3, cap 35"):
        e3.may_start(where, run)
    # The spend of the other experiments is another ledger.
    (tmp_path / "out" / "ledger.jsonl").write_text(json.dumps({"case": "C1", "run": "y", "cost_usd": 4.9}) + "\n")
    spend(where, 1.0)
    assert e3.may_start(where, run) is None


def test_the_measured_runs_wait_for_the_pilot_and_then_for_the_authors_decision_whatever_it_showed(tmp_path):
    where, first = Where(tmp_path), Run("E3", "C4", "R", 1)
    with pytest.raises(SystemExit, match="the pilot comes first"):
        e3.may_start(where, first)
    # A model that makes its calls one at a time: there is margin, and the runs stop all the same.
    pilot_metas(where, [1, 1, 1, 2, 1, 0])
    p = e3.pilot(where)
    assert (p["complete"], p["little_margin"], p["share_of_turns_with_several_calls"]) == (True, False, 0.2)
    with pytest.raises(SystemExit, match=r"E3 pilot: done. Several calls in 20% of the turns that make a call. Report the pilot"):
        e3.may_start(where, first)
    # A model that already issues calls together in most turns: the stop says so.
    pilot_metas(where, [5, 5, 1, 0])
    assert e3.pilot(where)["little_margin"] is True
    with pytest.raises(SystemExit, match="Several calls in 67% of the turns that make a call .above 50%: little for the strategy"):
        e3.may_start(where, first)
    assert e3.may_start(where, Run("E3", "C5", "R", 2, pilot=True)) is None      # the pilot itself is never held
    e3.pilot_decision(where).parent.mkdir(parents=True)
    e3.pilot_decision(where).write_text('{"go_on": true}\n')
    assert e3.may_start(where, first) is None
    # Exactly half is not "most".
    pilot_metas(where, [2, 1, 0])
    assert e3.pilot(where)["little_margin"] is False


def test_the_estimate_in_the_protocol_is_the_one_the_script_computes():
    from conftest import TRACES
    if TRACES is None:
        pytest.skip("the estimate for C5 reads the original study's runs")
    e3.STUDY_TRACES = TRACES
    r = e3.estimate()
    protocol = " ".join((ROOT / "protocol-v04-e3.md").read_text().split())
    for case in ("C4", "C5", "C2", "C1"):
        n = sum(x["runs"] for x in r["lines"] if x["case"] == case)
        assert f"| {case} | {n} |" in protocol, case
    assert f"| **Total** | **{r['runs']}** |" in protocol
    assert r["runs"] == 42 and r["usd_as_measured"] < r["usd_one_call_per_turn"]
    assert "8 on C4, 6 on C5, 3 on C2, 2 on C1: 38 runs" in protocol and "not published" in protocol
    prices = r["prices"]
    assert (prices["input_per_mtok"], prices["cached_input_per_mtok"], prices["output_per_mtok"]) == (0.75, 0.075, 3.75)
    assert "0.75 USD per 1M input tokens, 0.075 cached, 3.75 output" in protocol and "`gemini-3.8-flash`" in protocol


# -- the dry run -----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dry(tmp_path_factory):
    from conftest import TRACES
    if TRACES is None or not (ROOT / "traces" / "C1" / "E1-R-1.tools.json").exists():
        pytest.skip("needs the original study's traces and this checkout's E1 runs (not in git)")
    import experiments.paths
    import experiments.recorded
    import experiments.v04_preconditions
    for module in (experiments.paths, experiments.recorded, experiments.v04_preconditions, v04, e3):
        module.STUDY_TRACES = TRACES
    before = sorted(p.name for p in (ROOT / "traces").rglob("*")), (ROOT / "out" / "ledger-e3.jsonl").exists()
    root = tmp_path_factory.mktemp("e3dry")
    metas = asyncio.run(e3.dry_run(root))
    return {"root": root, "where": Where(root), "metas": metas, "before": before}


def of(dry, case, arm, pilot=False):
    return next(m for m in dry["metas"] if (m["case"], m["arm"], m["pilot"]) == (case, arm, pilot))


def test_the_dry_run_covers_the_pilot_and_every_arm_of_every_case_and_spends_nothing(dry):
    assert Counter((m["case"], m["arm"], m["pilot"]) for m in dry["metas"]) == {
        **{(c, "R", True): 2 for c in ("C4", "C5")}, **{(c, a, False): 1 for c in ("C4", "C5", "C2", "C1") for a in ("R", "S")}}
    assert all(m["dry_run"] and m["model"] == "stub" and m["experiment"] == "E3" and not m["trimming"] for m in dry["metas"])
    # Its own ledger, in its own directory; this checkout's files are as they were.
    assert len(v04.ledger(dry["where"], e3.ledger_path(dry["where"]))) == 12 and not dry["where"].ledger.exists()
    assert (sorted(p.name for p in (ROOT / "traces").rglob("*")), (ROOT / "out" / "ledger-e3.jsonl").exists()) == dry["before"]


def test_the_provider_is_never_called_and_recorded_outputs_are_marked(dry):
    assert all(m["dataforseo_calls"] == {} for m in dry["metas"])
    for case in ("C1", "C2"):
        m = of(dry, case, "R")
        # The stub's calls are those of a recorded run: exact, but for the one it changed on purpose.
        assert m["replay"]["nearest"] >= 1 and m["replay"]["none"] == 0 and m["replay"]["exact"] > m["replay"]["nearest"]
        assert m["domain_rejections"] == 1 and m["outcomes"]["gate_refused"] == 1          # the gate is the real one
        calls = [json.loads(line) for line in (dry["root"] / "traces" / case / f"{m['run']}.jsonl").open()
                 if '"kind": "call"' in line]
        marked = [c for c in calls if c["outcome"] == "ok"]
        assert all(c["replay"] in ("exact", "nearest") and c["replay_source"] for c in marked)
        assert Counter(c["replay"] for c in marked) == {k: v for k, v in m["replay"].items() if v}
        assert all("replay" not in c for c in calls if c["outcome"] == "gate_refused")
        # The tools are offered with the provider's own descriptions, recorded in E1.
        offered = json.loads((dry["root"] / "traces" / case / f"{m['run']}.tools.json").read_text())
        assert all(len(t["description"]) > 40 and t["parameters"].get("properties") for t in offered)
    assert of(dry, "C4", "R")["replay"] is None and of(dry, "C5", "S")["replay"] is None      # simulated servers, live


def test_the_strategy_text_is_in_the_s_arm_only_and_several_calls_share_a_turn(dry):
    for case in ("C4", "C5", "C2", "C1"):
        for arm in ("R", "S"):
            m = of(dry, case, arm)
            system = json.loads((dry["root"] / "traces" / case / f"{m['run']}.jsonl").read_text().splitlines()[0])["text"]
            assert ("### Know when to stop" in system) is (arm == "S") and "`expand`" not in system
            assert m["skill"]["strategy"] is (arm == "S") and m["skill"]["expand"] is False
            assert "expand" not in m["tool_schemas"] and set(m["exposure"]) == {"full"}
            assert max(t["calls"] for t in m["usage_per_turn"]) > 1                   # the stub issues calls together
    assert all(not of(dry, c, "R", pilot=True)["skill"]["strategy"] for c in ("C4", "C5"))


def test_the_stop_after_the_pilot_fired_in_the_dry_run(dry):
    decision = json.loads(e3.pilot_decision(dry["where"]).read_text())
    assert decision["dry_run"] is True and decision["pilot"]["complete"] is True and decision["pilot"]["little_margin"] is True
    assert decision["pilot"]["share_of_turns_with_several_calls"] > 0.5 and decision["pilot"]["runs_done"] == 4


def test_the_tables_of_e3_are_computed_from_the_runs(dry):
    from experiments.analyze_v04 import analyse_e3, tables_e3
    res = analyse_e3(dry["where"])
    assert (res["runs"], res["pilot_runs"], res["provider_calls"], res["dry_run"]) == (8, 4, 0, True)
    assert set(res["e3"]) == {"C1", "C2", "C4", "C5"} and all(set(a) == {"R", "S"} for a in res["e3"].values())
    # Where the outputs are recorded ones, the content of the answer is not evaluated.
    assert res["e3"]["C1"]["R"]["gates_passed"] is None and res["e3"]["C2"]["S"]["coverage"] is None
    assert res["e3"]["C4"]["R"]["gates_passed"] == 1 and res["e3"]["C5"]["S"]["coverage"]["median"] == 1.0
    h = res["e3_hypotheses"]
    assert set(h["H8"]["per_case"]) == set(h["H9"]["per_case"]) == {"C1", "C2", "C4", "C5"}
    assert set(h["H10"]["per_case"]) == {"C4", "C5"}
    assert h["H8"]["holds"] is False and h["H9"]["holds"] is False        # the stub does the same in both arms
    assert h["H11"] == {"checks": 4, "passed": 4, "failed": [], "holds": True}
    assert set(res["replay"]) == {"C1", "C2"} and "DRY RUN" in tables_e3(res)


def test_a_call_can_be_made_as_soon_as_what_it_names_has_been_returned(tmp_path):
    """The post-hoc analysis: the fewest turns are the longest chain of results a call depends on."""
    from experiments import e3_independence as ind
    assert ind.references({"run_id": 18204065799, "branch": "main", "per_page": 50}) == ["18204065799"]
    assert ind.references({"customer": "cus_Qx81kLmN2pZr", "limit": 10, "email": "a@example.com"}) == ["cus_Qx81kLmN2pZr"]
    call = lambda turn, tool, args, out: {"kind": "call", "tool": tool, "arguments": args, "output": out, "turn": turn, "outcome": "ok"}  # noqa: E731
    events = [{"kind": "prompt", "text": "Report the failures on main."},
              call(0, "list_runs", {"branch": "main"}, '{"runs": [{"id": 111111}, {"id": 222222}]}'),
              call(1, "list_jobs", {"run_id": 111111}, '{"jobs": [{"id": 333333}]}'),
              call(2, "logs", {"job_id": 333333}, "boom"),
              call(3, "list_jobs", {"run_id": 222222}, '{"jobs": [{"id": 444444}]}'),      # could have gone with turn 1
              call(4, "logs", {"job_id": 444444}, "bang")]                                   # and this with turn 2
    trace = tmp_path / "E3-R-1.jsonl"
    trace.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    r = ind.run(trace)
    assert (r["calls"], r["turns"], r["fewest_turns"]) == (5, 6, 4)
    assert (r["calls_that_depend_on_an_earlier_result"], r["calls_that_could_have_been_made_earlier"]) == (4, 2)
