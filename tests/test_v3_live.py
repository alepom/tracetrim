"""The paid loop of protocol v3 without spend: a scripted client in place of the provider,
the real immutable task worlds on the local simulated servers."""
import asyncio
import json
import re
from types import SimpleNamespace as NS

import pytest

from experiments.v3 import live

CAPS = (2.0, 3.0)              # arbitrary test caps, not the local ones
SLOT = "C4-05/1/P"


def _usage(i=1000, o=100):
    return NS(input_tokens=i, output_tokens=o, input_tokens_details=NS(cached_tokens=0))


def _call(name, args, cid):
    return NS(type="function_call", name=name, arguments=json.dumps(args), call_id=cid)


def _msg(text):
    return NS(type="message", content=[NS(text=text)])


class Client:
    """Three turns: search a customer, expand a hidden field if the result was trimmed, answer."""

    def __init__(self, fail=0):
        self.fail, self.created = fail, []
        self.responses = self

    async def create(self, **kw):
        if self.fail:
            self.fail -= 1
            raise ConnectionError("provider unreachable")
        self.created.append(kw)
        step = sum(1 for k in self.created if "previous_response_id" in k)
        if step == 0:
            out = [_call("stripe_search_customers", {"email": "ada.58757504@example.test"}, "f1")]
        elif step == 1:
            sent = kw["input"][0]["output"]
            m = re.search(r"call_id=(c[0-9a-f]{8})", sent)
            out = ([_call("expand", {"call_id": m.group(1), "fields": ["*"]}, "f2")] if m
                   else [_msg('{"ticket": []}')])
        else:
            out = [_msg('{"ticket": []}')]
        return NS(id=f"r{len(self.created)}", model="scripted-model", usage=_usage(), output=out)


def run(coro):
    return asyncio.run(coro)


def test_loop_writes_trace_meta_ledger_and_expand_works(tmp_path):
    where = live.Where(tmp_path)
    meta = run(live.run_slot(SLOT, where, Client(), "m", CAPS))
    trace = [json.loads(x) for x in where.trace(SLOT).read_text().splitlines()]
    calls = [e for e in trace if e["kind"] == "call"]
    assert calls[0]["tool"] == "stripe_search_customers" and calls[0]["exposure"] == "filtered"
    assert calls[0]["sent"] != calls[0]["output"] and "[trimmed]" in calls[0]["sent"]
    assert calls[1]["tool"] == "expand" and calls[1]["outcome"] == "ok" and json.loads(calls[1]["output"])
    assert trace[-1]["kind"] == "message"
    assert "expand" in trace[0]["text"]                       # base recovery instructions in P
    assert meta["expand_calls"] == 1 and meta["model_returned"] == ["scripted-model"]
    assert where.trace(SLOT).with_suffix(".meta.json").exists()
    assert [e["slot"] for e in live.lines(where)] == [SLOT]


def test_raw_arm_sends_originals_and_no_expand(tmp_path):
    where = live.Where(tmp_path)
    client = Client()
    run(live.run_slot("C4-05/1/R", where, client, "m", CAPS))
    assert "expand" not in {t["name"] for t in client.created[0]["tools"]}
    calls = [json.loads(x) for x in where.trace("C4-05/1/R").read_text().splitlines()]
    assert all("sent" not in e for e in calls if e["kind"] == "call")


def test_resume_skips_completed_slots(tmp_path):
    where = live.Where(tmp_path)
    first = run(live.campaign(where, Client(), "m", limit=2, limits=CAPS))
    assert len(first) == 2
    client = Client()
    second = run(live.campaign(where, client, "m", limit=1, limits=CAPS))
    from experiments.v3.coordinator import schedule
    assert [m["slot"] for m in first + second] == schedule()[:3]
    assert live.status(where)["done"] == 3


def test_caps_block_and_warn_asks(tmp_path):
    where = live.Where(tmp_path)
    with pytest.raises(live.Budget):
        live.may_start(where, (0.0, 0.0))                     # no local caps: refused
    where.root.mkdir(parents=True, exist_ok=True)
    live._append(where.ledger, {"slot": "x", "cost_usd": 2.5})
    with pytest.raises(live.Budget, match="warning"):
        run(live.run_slot(SLOT, where, Client(), "m", CAPS))
    where.reviewed.touch()
    assert live.may_start(where, CAPS) == 2.5
    live._append(where.ledger, {"slot": "x", "cost_usd": 0.5})
    with pytest.raises(live.Budget, match="stop"):
        run(live.run_slot(SLOT, where, Client(), "m", CAPS))
    assert not where.trace(SLOT).exists()


def test_one_retry_then_missing(tmp_path):
    where = live.Where(tmp_path)
    meta = run(live.run_slot(SLOT, where, Client(fail=1), "m", CAPS))
    assert meta["attempt"] == 2 and live.attempts(where, SLOT) == 2
    assert (where.attempts / "C4-05" / "1" / "P.1.json").exists()
    other = "C4-05/1/I"
    assert run(live.run_slot(other, where, Client(fail=2), "m", CAPS)) is None
    assert other in live.missing(where) and not where.trace(other).exists()
    assert live.done(where, other)
