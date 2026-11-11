"""The harness of protocol-v04.md, without spending: schedule, stop rules, arms, dry run, coverage, tables.

The dry run replays a recorded Phase A run of each case with a stub in place
of the model, so it needs the original study's traces (skipped without them).
"""
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments import coverage, v04  # noqa: E402
from experiments.budget import V04  # noqa: E402
from experiments.v04 import Run, Where  # noqa: E402
from tracetrim import skill  # noqa: E402


# -- the schedule ---------------------------------------------------------------

def test_the_schedule_is_the_design_of_the_protocol():
    runs = v04.schedule()
    assert len(runs) == 130 and len({(r.case, r.id) for r in runs}) == 130
    per = Counter((r.experiment, r.case, r.arm) for r in runs)
    assert {k: n for k, n in per.items() if k[0] == "E1"} == {
        ("E1", c, a): 5 for c, arms in V04["e1"]["arms"].items() for a in arms}
    assert set(V04["e1"]["arms"]["C2"]) == {"R", "S"}                   # nothing is trimmed on C2: no trimmed arm
    assert {k: n for k, n in per.items() if k[0] == "E2"} == {("E2", c, a): 10 for c in ("C1", "C4") for a in v04.E2_ARMS}
    # Along a sequence the runs come in order: learning accumulates.
    for key in {(r.case, r.arm, r.sequence) for r in runs if r.experiment == "E2"}:
        assert [r.n for r in runs if (r.case, r.arm, r.sequence) == key and r.experiment == "E2"] == [1, 2, 3, 4, 5]
    assert [r.experiment for r in runs] == ["E1"] * 50 + ["E2"] * 80


def test_the_first_ten_dataforseo_runs_cover_both_real_cases():
    first = [r for r in v04.schedule() if r.dataforseo][:V04["dataforseo_checkpoint_runs"]]
    assert Counter(r.case for r in first) == {"C1": 8, "C2": 2}
    assert [(r.case, r.arm, r.n) for r in first] == (
        [("C1", a, 1) for a in ("R", "S", "T", "ST")] + [("C2", "R", 1), ("C2", "S", 1)]
        + [("C1", a, 2) for a in ("R", "S", "T", "ST")])


def test_the_e2_arms_are_the_settings_of_the_protocol():
    assert v04.E2_ARMS["base"] == {} and v04.E2_ARMS["promotion"] == {"promote": True}
    assert v04.E2_ARMS["sampling"] == {"sample_rate": 0.25, "sample_max_share": 0.5}
    assert v04.E2_ARMS["promotion+sampling"] == {"promote": True, "sample_rate": 0.25, "sample_max_share": 0.5}
    assert (v04.CAP, v04.WARN, v04.MIN_RUNS) == (50.0, 40.0, 5)


# -- which text reaches the system prompt -------------------------------------------

@pytest.mark.parametrize("arm, strategy, expand", [("R", False, False), ("S", True, False), ("T", False, True),
                                                   ("ST", True, True)])
def test_the_skill_text_is_added_only_in_the_arms_that_have_it(arm, strategy, expand):
    system, added = v04.instructions(Run("E1", "C4", arm, 1))
    assert system == (ROOT / "experiments" / "prompts" / "c4-system.md").read_text()
    assert ("### Know when to stop" in added) is strategy and ("`expand`" in added) is expand
    assert "tracetrim_done" not in added                               # the loop passes the answer itself
    assert added == skill.text(strategy=strategy, done=False, expand=expand) and (added == "") is (arm == "R")


def test_e2_arms_get_the_expand_text_the_variant_prompt_and_no_strategy():
    run = Run("E2", "C4", "promotion", 1, 1)
    assert v04.instructions(run)[1] == skill.text(done=False)
    assert v04.user_prompt(run) == (ROOT / "experiments" / "prompts" / "c4v-user.md").read_text()
    assert v04.user_prompt(Run("E1", "C4", "ST", 1)) == (ROOT / "experiments" / "prompts" / "c4-user.md").read_text()


# -- stop rules -------------------------------------------------------------------------

def spend(where: Where, usd: float, dataforseo_runs: int = 0):
    where.ledger.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"case": "C4", "run": "x", "cost_usd": usd, "dataforseo_calls": 0}]
    lines += [{"case": "C1", "run": f"d{i}", "cost_usd": 0.0, "dataforseo_calls": 20} for i in range(dataforseo_runs)]
    where.ledger.write_text("".join(json.dumps(line) + "\n" for line in lines))


def test_the_warning_and_the_cap_are_those_of_the_protocol(tmp_path):
    where, run = Where(tmp_path), Run("E1", "C4", "R", 1)
    assert v04.may_start(where, run) is None                           # no ledger yet: nothing spent
    spend(where, 39.99)
    assert v04.may_start(where, run) is None
    spend(where, 40.0)
    assert v04.may_start(where, run) == "WARNING: 40.00 USD spent, warning at 40, cap 50"
    spend(where, 50.0)
    with pytest.raises(SystemExit, match="stop rule: 50.00 USD already spent, cap 50"):
        v04.may_start(where, run)
    assert not (ROOT / "out" / "ledger.jsonl").exists() or v04.HERE.ledger != where.ledger


def test_after_ten_dataforseo_runs_those_runs_stop_until_the_decision_is_recorded(tmp_path):
    where = Where(tmp_path)
    spend(where, 3.0, dataforseo_runs=9)
    assert v04.may_start(where, Run("E1", "C2", "R", 2)) is None
    spend(where, 3.0, dataforseo_runs=10)
    for run in (Run("E1", "C2", "R", 2), Run("E2", "C1", "base", 1, 1)):
        with pytest.raises(SystemExit, match="DataForSEO stop rule: 10 runs"):
            v04.may_start(where, run)
    assert v04.may_start(where, Run("E1", "C4", "R", 2)) is None       # the simulated case is not held back
    where.checkpoint.parent.mkdir(parents=True)
    where.checkpoint.write_text('{"decided_by": "the author", "go_on": true}\n')
    assert v04.may_start(where, Run("E1", "C2", "R", 2)) is None


# -- coverage and the requirement of the variants ------------------------------------------

def write_run(path: Path, calls, answer: str) -> Path:
    events = [{"kind": "prompt", "text": "go", "turn": -1}]
    events += [{"kind": "call", "tool": t, "arguments": json.dumps(a), "output": json.dumps(o), "turn": 0} for t, a, o in calls]
    path.write_text("".join(json.dumps(e) + "\n" for e in events + [{"kind": "message", "text": answer, "turn": 1}]))
    return path


def ticket(i, reply="Abbiamo verificato il pagamento.", evidence=("ch_AAAAAAAAAAAAAA",)):
    return {"id": f"T{i}", "customer_id": "cus_AAAAAAAAAAAAAA", "diagnosi": "addebito doppio",
            "evidenze": [{"oggetto": e, "fatto": "x"} for e in evidence], "risposta_al_cliente": reply, "azioni_interne": []}


def test_c4_coverage_counts_what_the_prompt_asks(tmp_path):
    good = write_run(tmp_path / "good.jsonl", [], json.dumps({"ticket": [ticket(i) for i in range(1, 6)]}))
    r = coverage.coverage(good, "C4")
    assert (r["passed"], r["of"], r["share"]) == (6, 6, 1.0)
    bad = {"ticket": [ticket(1, reply="Vedi ch_AAAAAAAAAAAAAA."), ticket(2, evidence=()), ticket(3), ticket(4)]}
    r = coverage.coverage(write_run(tmp_path / "bad.jsonl", [], json.dumps(bad)), "C4")
    assert r["passed"] == 3 and not r["checks"]["five tickets"] and not r["checks"]["evidence per ticket"]
    assert not r["checks"]["no technical identifier in the replies"]
    assert coverage.coverage(write_run(tmp_path / "none.jsonl", [], "not json"), "C4")["passed"] == 0


def test_c2_coverage_checks_the_arguments_the_prompt_prescribes(tmp_path):
    theme = {"tema": "t", "query_analizzate": ["a", "b"], "risultati_rilevanti": [], "pagine_del_sito": [{"url": "u"}],
             "gap": [{"sottotema": "s", "evidenze": ["https://x.test/"]}], "proposte": [{"tipo": "nuovo"}]}
    serp = lambda depth: ("serp_organic_live_advanced",      # noqa: E731
                          {"keyword": "k", "location_name": "Italy", "language_code": "it", "depth": depth}, {"items": []})
    ok = coverage.coverage(write_run(tmp_path / "ok.jsonl", [serp(10)], json.dumps({"temi": [theme] * 3})), "C2")
    assert ok["share"] == 1.0
    # A strategy that "saved calls" by asking for more results at once would show here.
    wide = coverage.coverage(write_run(tmp_path / "wide.jsonl", [serp(10), serp(100)], json.dumps({"temi": [theme] * 3})), "C2")
    assert wide["passed"] == 5 and not wide["checks"]["every SERP call asks Italy, Italian, depth 10"]
    one = dict(theme, query_analizzate=["a"])
    assert not coverage.coverage(write_run(tmp_path / "one.jsonl", [serp(10)], json.dumps({"temi": [one] * 3})),
                                 "C2")["checks"]["2 or 3 queries analysed per theme"]


def test_c1_coverage_checks_plan_sizes_and_the_prescribed_limits(tmp_path):
    plan = [{"potenziale titolo": f"t{i}"} for i in range(12)]
    answer = json.dumps({"piano_editoriale_brevissimo_termine": plan, "piano_editoriale_medio_periodo": plan[:9]})
    calls = [("dataforseo_labs_google_keyword_ideas", {"limit": 10}, {}),
             ("dataforseo_labs_google_keyword_suggestions", {"limit": 50}, {}),
             ("kw_data_google_trends_explore", {"keywords": list("abcdef")}, {})]
    r = coverage.coverage(write_run(tmp_path / "c1.jsonl", calls, answer), "C1")["checks"]
    assert r == {"short-term plan has 10 to 15 items": True, "medium-term plan has 10 to 15 items": False,
                 "at most 50 tool calls": True, "every limit is 10": False, "every SERP depth is 10": True,
                 "at most 5 keywords per Trends call": False}


def test_the_requirement_of_the_c4_variant_needs_the_link_and_the_way_of_collection(tmp_path):
    link = "https://invoice.billing.example/i/in_AAAAAAAAAAAAAA"
    calls = [("stripe_list_invoices", {"customer": "cus_AAAAAAAAAAAAAA"},
              {"data": [{"id": "in_AAAAAAAAAAAAAA", "hosted_invoice_url": link, "collection_method": "send_invoice"}]})]
    about = lambda reply: ticket(2, reply=reply, evidence=("in_AAAAAAAAAAAAAA",))     # noqa: E731

    def met(*tickets):
        path = write_run(tmp_path / f"r{len(list(tmp_path.iterdir()))}.jsonl", calls, json.dumps({"ticket": list(tickets)}))
        return coverage.requirement(path, "C4")

    assert met(ticket(1), about(f"La fattura le è stata inviata per il pagamento: {link}")) == {"met": True, "items": 1, "items_met": 1}
    assert met(about("La fattura le è stata inviata per il pagamento."))["met"] is False              # no link
    assert met(about(f"Ecco la fattura: {link}"))["met"] is False                                    # not how it is collected
    assert met(about("Inviata: https://invoice.billing.example/i/in_INVENTED000000"))["met"] is False   # a link not in the outputs
    assert met(ticket(1)) == {"met": False, "items": 0, "items_met": 0}                              # no invoice ticket at all


def test_the_requirement_of_the_c1_variant_needs_a_channel_from_that_runs_outputs(tmp_path):
    calls = [("serp_youtube_organic_live_advanced", {"keyword": "k"},
              {"items": [{"channel_name": "Canale Esempio Uno", "views_count": 12345}]})]
    assert coverage.requirement(write_run(tmp_path / "a.jsonl", calls, "Canale: Canale Esempio Uno, 12.345 visualizzazioni"), "C1")["met"]
    assert not coverage.requirement(write_run(tmp_path / "b.jsonl", calls, "Canale: un canale qualsiasi"), "C1")["met"]


# -- the dry run: every arm, a stub in place of the model ---------------------------------------

@pytest.fixture(scope="module")
def dry(tmp_path_factory, request):
    traces = request.getfixturevalue("study_traces")
    before = sorted(p.name for p in traces.rglob("*"))
    root = tmp_path_factory.mktemp("dry")
    metas = asyncio.run(v04.dry_run(root))
    return {"root": root, "metas": metas, "where": Where(root), "study_before": before, "study": traces}


@pytest.fixture(scope="module")
def study_traces():
    import os
    from conftest import TRACES
    if TRACES is None:
        pytest.skip("recorded traces not available: set $TRACETRIM_TRACES")
    import experiments.budget
    import experiments.paths
    import experiments.v04_preconditions
    for module in (experiments.budget, experiments.paths, experiments.v04_preconditions, v04):
        module.STUDY_TRACES = TRACES
    os.environ.setdefault("TRACETRIM_TRACES", str(TRACES))
    return TRACES


def by(dry, experiment, case, arm, n=1):
    return next(m for m in dry["metas"] if (m["experiment"], m["case"], m["arm"], m["n"]) == (experiment, case, arm, n))


def trace_of(dry, m) -> list[dict]:
    return [json.loads(line) for line in (dry["root"] / "traces" / m["case"] / f"{m['run']}.jsonl").open()]


def test_the_dry_run_exercises_every_arm_and_spends_nothing(dry):
    arms = Counter((m["experiment"], m["case"], m["arm"]) for m in dry["metas"])
    assert {k for k in arms if k[0] == "E1"} == {("E1", c, a) for c, arms_ in V04["e1"]["arms"].items() for a in arms_}
    assert {k: n for k, n in arms.items() if k[0] == "E2"} == {("E2", c, a): 2 for c in ("C1", "C4") for a in v04.E2_ARMS}
    assert all(m["dry_run"] and m["model"] == "stub" and not m["flags"] for m in dry["metas"])
    # Everything was written under the dry run's own directory.
    assert sorted(p.name for p in dry["study"].rglob("*")) == dry["study_before"]
    assert len(v04.ledger(dry["where"])) == len(dry["metas"]) == 26
    assert all(line["dry_run"] for line in v04.ledger(dry["where"]))
    assert v04.HERE.ledger == ROOT / "out" / "ledger.jsonl" and v04.HERE.ledger != dry["where"].ledger


def test_traces_meta_files_and_ledger_agree(dry):
    for m in dry["metas"]:
        events = trace_of(dry, m)
        assert [e["kind"] for e in events[:2]] == ["prompt", "prompt"] and events[-1]["kind"] == "message"
        calls = [e for e in events if e["kind"] == "call"]
        assert len([c for c in calls if c["tool"] != "expand"]) == m["calls"] == sum(m["exposure"].values())
        assert len([c for c in calls if c["tool"] == "expand"]) == m["expand_calls"]
        assert sum(t["calls"] for t in m["usage_per_turn"]) == len(calls) and len(m["usage_per_turn"]) == m["turns"]
        line = next(x for x in v04.ledger(dry["where"]) if (x["case"], x["run"]) == (m["case"], m["run"]))
        assert line["cost_usd"] == m["cost_usd"] > 0 and line["usage"] == m["usage"]
        assert line["dataforseo_calls"] == sum(m["dataforseo_calls"].values())


@pytest.mark.parametrize("arm, strategy, expand", [("R", False, False), ("S", True, False), ("T", False, True),
                                                   ("ST", True, True)])
def test_in_the_dry_run_the_system_prompt_carries_the_text_of_its_arm_only(dry, arm, strategy, expand):
    m = by(dry, "E1", "C4", arm)
    system = trace_of(dry, m)[0]["text"]
    original = (ROOT / "experiments" / "prompts" / "c4-system.md").read_text()
    assert system.startswith(original) and (system == original) is (arm == "R")
    assert ("### Know when to stop" in system) is strategy and ("## Hidden fields: `expand`" in system) is expand
    assert m["skill"] == {"version": skill.VERSION, "strategy": strategy, "expand": expand,
                          "tokens": m["tokens"]["instructions_added"]}
    assert (m["skill"]["tokens"] == 0) is (arm == "R") and m["trimming"] is expand
    # The trimmed arms offer `expand`; the others have exactly the case's tools.
    assert (m["tokens"]["tool_definitions"] > by(dry, "E1", "C4", "R")["tokens"]["tool_definitions"]) is expand


def test_trimmed_arms_filter_and_the_trace_keeps_both_sides_of_a_call(dry):
    raw, trimmed = by(dry, "E1", "C4", "R"), by(dry, "E1", "C4", "T")
    assert raw["exposure"] == {"full": raw["calls"]} and raw["tools_trimmed"] == []
    assert trimmed["exposure"].get("filtered", 0) > 0 and len(trimmed["tools_trimmed"]) == 4
    assert sum(trimmed["tokens"]["outputs_sent"].values()) < trimmed["tokens"]["outputs_original"] == raw["tokens"]["outputs_original"]
    filtered = [e for e in trace_of(dry, trimmed) if e.get("exposure") == "filtered"]
    assert filtered and all("[trimmed] call_id=" in e["sent"] and "[trimmed]" not in e["output"] for e in filtered)
    assert all("sent" not in e for e in trace_of(dry, raw))            # nothing rewritten: one side only
    assert by(dry, "E1", "C2", "S")["trimming"] is False and ("E1", "C2", "T") not in {
        (m["experiment"], m["case"], m["arm"]) for m in dry["metas"]}


def test_dataforseo_calls_are_counted_and_the_domain_gate_is_on(dry):
    for case in ("C1", "C2"):
        m = by(dry, "E1", case, "R")
        # The stub asked once for a page outside the author's domain: refused, counted, never sent.
        assert m["domain_rejections"] == 1 and sum(m["dataforseo_calls"].values()) == m["calls"] - 1
        refused = [e for e in trace_of(dry, m) if e["kind"] == "call" and e["output"].startswith("Error: blocked by the study's domain rule")]
        assert len(refused) == 1 and "example.com" in refused[0]["arguments"]
        assert "on_page_content_parsing" not in m["dataforseo_calls"] or case == "C2"
    assert by(dry, "E1", "C4", "R")["dataforseo_calls"] == {} and by(dry, "E1", "C4", "R")["domain_rejections"] == 0


def test_the_dataforseo_stop_rule_fired_during_the_dry_run(dry):
    stop = json.loads(dry["where"].checkpoint.read_text())
    assert stop["dry_run"] is True and stop["runs_that_called_dataforseo"] == 10
    order = [(m["case"], m["run"]) for m in dry["metas"]]
    assert order.index(tuple(stop["stopped_before"].split())) == 10 + sum(m["case"] == "C4" for m in dry["metas"])


@pytest.mark.parametrize("case", ["C1", "C4"])
def test_e2_sequences_learn_from_one_run_to_the_next(dry, case):
    first, second = by(dry, "E2", case, "promotion", 1), by(dry, "E2", case, "promotion", 2)
    fields = list(first["adaptive"]["kept_before"])
    assert len(fields) == 2 and not any(first["adaptive"]["kept_before"].values())      # removed at the start: the premise
    assert all(first["adaptive"]["reached"][f] == ["expand"] for f in fields) and first["expand_calls"] >= 1
    assert all(first["adaptive"]["kept_after"].values())                               # the stub wrote the values
    assert second["adaptive"]["kept_before"] == first["adaptive"]["kept_after"] and second["expand_calls"] == 0
    assert all("filtered" in second["adaptive"]["reached"][f] for f in fields)
    assert {p["field"] for p in second["adaptive"]["promotions"]} == set(fields)
    assert by(dry, "E2", case, "base", 2)["adaptive"]["promotions"] == []               # promotion off: none recorded
    assert first["adaptive"]["settings"]["promote"] is True and first["adaptive"]["settings"]["sample_rate"] == 0.0
    sampling = by(dry, "E2", case, "sampling", 2)["adaptive"]
    assert (sampling["settings"]["sample_rate"], sampling["settings"]["sample_max_share"]) == (0.25, 0.5)
    assert sum(by(dry, "E2", case, "sampling", n)["exposure"].get("sampled", 0) for n in (1, 2)) >= 1
    assert (dry["where"].workdir(Run("E2", case, "promotion", 1, 1)) / "evidence.json").exists()


def test_a_run_that_crosses_the_cap_stops_and_is_flagged(dry, tmp_path):
    where = Where(tmp_path)
    spend(where, 49.99)
    run = Run("E1", "C4", "R", 1)
    m = asyncio.run(v04.execute(run, where, client=v04.stub_for(run), model="stub", dry=True))
    # Stopped at the turn that crossed the cap, before the run was over.
    assert m["flags"] == ["stopped_by_budget"] and m["turns"] < by(dry, "E1", "C4", "R")["turns"]
    assert v04.spent(where) >= 50 and len(v04.ledger(where)) == 2
    # And nothing starts after it: the cap is checked before anything else.
    with pytest.raises(SystemExit, match="stop rule: 50.0. USD already spent, cap 50"):
        asyncio.run(v04.execute(Run("E1", "C4", "S", 1), where, client=v04.stub_for(run), model="stub", dry=True))


def test_a_run_id_is_never_reused(dry, tmp_path):
    where, run = Where(tmp_path), Run("E1", "C4", "R", 1)
    asyncio.run(v04.execute(run, where, client=v04.stub_for(run), model="stub", dry=True))
    with pytest.raises(SystemExit, match="run ids are never reused"):
        asyncio.run(v04.execute(run, where, client=v04.stub_for(run), model="stub", dry=True))
    assert len(v04.ledger(where)) == 1


def test_an_aborted_run_is_in_the_ledger_all_the_same(dry, tmp_path):
    class Dies(v04.Stub):
        async def create(self, **kw):
            if self.n == 1:
                raise RuntimeError("the connection dropped")
            return await super().create(**kw)

    where, run = Where(tmp_path), Run("E1", "C4", "S", 1)
    with pytest.raises(RuntimeError):
        asyncio.run(v04.execute(run, where, client=Dies(v04.recorded("C4")), model="stub", dry=True))
    [line] = v04.ledger(where)
    assert line["aborted"] is True and line["cost_usd"] > 0 and line["run"] == "E1-S-1"


def test_the_tables_are_computed_from_the_files_of_the_runs(dry):
    from experiments.analyze_v04 import analyse, tables
    res = analyse(dry["where"])
    assert res["dry_run"] and res["runs"] == 26 and res["dataforseo"]["runs"] == 14
    assert res["dataforseo"]["calls"] == sum(sum(m["dataforseo_calls"].values()) for m in dry["metas"])
    assert set(res["e1"]) == {"C1", "C2", "C4"} and set(res["e1"]["C2"]) == {"R", "S"}
    c4 = res["e1"]["C4"]
    assert c4["R"]["gates_passed"] == 1 and c4["R"]["coverage"]["median"] == 1.0          # the replayed run was a good one
    assert c4["T"]["tokens_outputs_sent"]["median"] < c4["R"]["tokens_outputs_sent"]["median"]
    assert c4["S"]["tokens_instructions_added"]["median"] == 549 and c4["R"]["tokens_instructions_added"]["median"] == 0
    sequences = {(s["case"], s["arm"]): s for s in res["e2"]}
    assert len(sequences) == 8 and all(s["runs"] == 2 for s in res["e2"])
    assert sequences[("C4", "promotion")]["kept_from_run"] == {"data[].hosted_invoice_url": 2, "data[].collection_method": 2}
    assert sequences[("C1", "promotion")]["expand_calls"] == [2, 0]
    page = tables(res)
    assert "DRY RUN" in page and "kept from run" in page
    # Counts, shares and field paths only: no value of a tool output.
    saved = json.dumps(res)
    for m in dry["metas"]:
        if m["case"] == "C1":
            for e in trace_of(dry, m):
                if e["kind"] == "call" and e["tool"] == "serp_youtube_organic_live_advanced":
                    for item in (json.loads(e["output"]).get("items") or [])[:3]:
                        assert str(item.get("channel_name")) not in saved
