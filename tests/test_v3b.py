"""Protocol v3b without spend: key, objective scorer, hidden-field construction, selection and schedule."""
import json

from experiments.v3b import facts, run
from experiments.v3b.tools import task_data
from experiments.v3b.worlds import CANDIDATES, HIDDEN_TEMPLATES


def perfect(key):
    tickets = []
    for k in key["ticket"]:
        parts = []
        for f in k["facts"][1:]:
            if f["kind"] == "id":
                parts.append(f["value"])
            elif f["kind"] == "amount":
                e, c = divmod(f["value"], 100)
                parts.append(f"{e:,}".replace(",", ".") + f",{c:02d} €")
            else:
                parts.append(f"{f['value']} {'licenze' if f['unit'] == 'licenz' else 'TB'}")
        tickets.append({"id": k["id"], "customer_id": k["customer_id"], "diagnosi": "; ".join(parts)})
    return {"ticket": tickets}


def test_every_task_hides_a_fact_and_perfect_answer_scores_one():
    for i in range(1, CANDIDATES + 1):
        key = task_data(f"C4b-{i:02}")["key"]
        assert sum(t["template"] in HIDDEN_TEMPLATES for t in key["ticket"]) >= 3
        s = facts.score_answer(perfect(key), key)
        assert s["share"] == 1.0 and s["hidden_share"] == 1.0


def test_scorer_is_strict():
    key = task_data("C4b-01")["key"]
    good = perfect(key)
    assert facts.score_answer(None, key)["share"] == 0.0
    wrong = json.loads(json.dumps(good))
    wrong["ticket"][0]["customer_id"] = "cus_00000000000000"
    wrong["ticket"][0]["diagnosi"] = ""
    s = facts.score_answer(wrong, key)
    assert s["per_ticket"]["T1"]["correct"] == 0 and s["share"] < 1.0
    assert facts.amount_pattern(12345).search("123,45 EUR") and facts.amount_pattern(12345).search("€123.45")
    assert not facts.amount_pattern(12345).search("1123,45") and not facts.amount_pattern(12345).search("123,456")
    assert facts.found({"kind": "count", "value": 3, "unit": "licenz"}, {"x": "tre licenze"})
    assert not facts.found({"kind": "count", "value": 3, "unit": "licenz"}, {"x": "13 licenze"})


def test_selection_rule():
    shares = {f"C4b-{i:02}": s for i, s in enumerate([1.0, 0.95, 0.9, 0.4, 0.39, 0.65, 0.66, 0.64, 0.5, 0.8,
                                                      0.7, 0.6, 0.55, 0.75, 0.45, 0.85, 0.2, 0.0], 1)}
    eligible, kept = run.rule(shares)
    assert "C4b-03" in eligible and "C4b-04" in eligible and "C4b-05" not in eligible
    assert len(eligible) == 13 and len(kept) == 12
    assert "C4b-03" in kept and "C4b-04" not in kept              # a tie at 0.25 from 0.65 goes by task number


def test_schedule_blocks_and_balance():
    tasks = [f"C4b-{i:02}" for i in range(1, 11)]
    s = run.schedule(tasks, 3)
    assert len(s) == 60 and len(set(s)) == 60 and s == run.schedule(tasks, 3)
    pairs = [s[i:i + 2] for i in range(0, 60, 2)]
    assert all(a.rsplit("/", 1)[0] == b.rsplit("/", 1)[0] for a, b in pairs)
    assert sum(a.endswith("/R") for a, _ in pairs) == 15
