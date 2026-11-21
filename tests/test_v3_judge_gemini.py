"""The Gemini judge of protocol v3 without spend: invented cards, a scripted client."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from experiments.v3 import judge_gemini as jg

LIMITS = (2.0, 3.0)                    # arbitrary test caps, not the local ones
PRICES = {"model": "fake", "verified": True, "input_per_mtok": 1.0, "cached_input_per_mtok": 0.1,
          "output_per_mtok": 2.0, "thinking_level": "HIGH"}


@pytest.fixture
def where(tmp_path, monkeypatch):
    terms = tmp_path / "terms.txt"
    terms.write_text("zzz-never-in-the-cards\n")
    monkeypatch.setattr(jg, "NEVER_PUBLISH", terms)
    monkeypatch.setattr(jg, "config", lambda req, labels, p: {"labels": labels})
    w = jg.Where(tmp_path / "v3")
    for n in (1, 2):
        d = w.cards(n)
        d.mkdir(parents=True)
        for t in range(2):
            for i in range(6):
                cid = f"{n}{t}{i:02d}" + "0" * 28
                (d / f"{cid}.json").write_text(json.dumps({"id": cid, "task": f"task {t}", "rubric": "R",
                                                           "answer": f"answer {t}-{i}", "evidence": [t]}))
    return w


def score_of(answer):                   # a deterministic score readable back from the card
    return int(answer.split("-")[1]) % 5


class Client:
    def __init__(self, bad=0):
        self.bad, self.calls = bad, []
        self.aio = NS(models=self)

    async def generate_content(self, model, contents, config):
        self.calls.append(contents)
        if self.bad:
            self.bad -= 1
            text = '{"A": "not scores"}'
        else:
            out = {}
            for label in config["labels"]:
                answer = contents.split(f"## Answer {label}\n\n")[1].split("\n")[0]
                s = score_of(answer)
                out[label] = {"correctness": s, "completeness": s, "action": 4 - s, "rationale": "ok"}
            text = json.dumps(out)
        usage = NS(prompt_token_count=1000, cached_content_token_count=0, candidates_token_count=100,
                   thoughts_token_count=50)
        return NS(candidates=[NS(content=NS(parts=[NS(text=text, thought=False)]), finish_reason="STOP")],
                  usage_metadata=usage, model_version="fake-1")


def run(coro):
    return asyncio.run(coro)


def test_grouping_and_order(where):
    a = jg.plan(where.cards(1), jg.SEEDS[1])
    assert len(a) == 2 and all(list(g["labels"]) == list("ABCDEF") for g in a)
    assert sorted(c for g in a for c in g["labels"].values()) == sorted(p.stem for p in where.cards(1).glob("*.json"))
    assert a == jg.plan(where.cards(1), jg.SEEDS[1])
    assert [g["labels"] for g in a] != [g["labels"] for g in jg.plan(where.cards(1), 7)]
    req = jg.request(a[0])
    assert req["user"].count("# Original reference tool returns") == 1 and req["user"].count("## Answer ") == 6
    assert "each on its own" in req["user"] and "ON ITS OWN" in req["system"]


def test_labels_back_to_card_ids(where):
    run(jg.run(where, 1, Client(), PRICES, limits=LIMITS))
    path = jg.sheet(where, 1, jg.plan(where.cards(1), jg.SEEDS[1]))
    sheet = json.loads(path.read_text())
    cards = {p.stem: json.loads(p.read_text()) for p in where.cards(1).glob("*.json")}
    assert set(sheet) == set(cards)
    for cid, row in sheet.items():
        s = score_of(cards[cid]["answer"])
        assert row == {"correctness": s, "completeness": s, "action": 4 - s}


def test_passes_share_nothing(where):
    c = Client()
    run(jg.run(where, 1, c, PRICES, limits=LIMITS))
    run(jg.run(where, 2, c, PRICES, limits=LIMITS))
    assert not any("answer" in x and x.count("## Answer ") != 6 for x in c.calls)
    assert all("correctness\": " not in x for x in c.calls[2:])     # no scores of pass 1 in pass 2


def test_resume(where):
    c = Client()
    run(jg.run(where, 1, c, PRICES, limit=1, limits=LIMITS))
    assert jg.sheet(where, 1, jg.plan(where.cards(1), jg.SEEDS[1])) is None
    run(jg.run(where, 1, c, PRICES, limits=LIMITS))
    assert len(c.calls) == 2 and jg.status(where, 1)["scored"] == 2


def test_caps(where):
    with pytest.raises(jg.Budget):
        jg.may_start(where, (0.0, 0.0))
    where.root.mkdir(parents=True, exist_ok=True)
    jg._append(where.campaign.ledger, {"slot": "x", "cost_usd": 2.5})      # the campaign counts too
    with pytest.raises(jg.Budget, match="warning"):
        run(jg.run(where, 1, Client(), PRICES, limits=LIMITS))
    where.reviewed.touch()
    jg._append(where.ledger, {"pass": 1, "group": "g", "cost_usd": 0.6})
    with pytest.raises(jg.Budget, match="stop"):
        run(jg.run(where, 1, Client(), PRICES, limits=LIMITS))


def test_malformed_once_then_reported(where):
    c = Client(bad=1)
    run(jg.run(where, 1, c, PRICES, limit=1, limits=LIMITS))
    assert jg.status(where, 1)["scored"] == 1 and len(c.calls) == 2
    c = Client(bad=2)
    run(jg.run(where, 1, c, PRICES, limits=LIMITS))
    st = jg.status(where, 1)
    assert st["scored"] == 1 and len(st["failed"]) == 1 and len(c.calls) == 2
    assert jg.sheet(where, 1, jg.plan(where.cards(1), jg.SEEDS[1])) is None
    run(jg.run(where, 1, Client(), PRICES, limits=LIMITS))                 # a reported group is not retried
    assert jg.status(where, 1)["scored"] == 1


def test_unverified_price_refused(tmp_path, monkeypatch):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"model": "m", "verified": False}))
    monkeypatch.setattr(jg, "PRICES", p)
    with pytest.raises(SystemExit):
        jg.prices()
