"""Fields kept by hand (1.5, `--keep-field`, `keep_fields`): never hidden, whatever learning finds.

Asked after the stress test of Study 4: an invoice memo the agent rarely uses can still be what a reader needs, and an
agent that does not see it may say it is not there. A person who knows the process lists such fields; learning goes
on as before, and its files are not changed, so dropping the option restores what was learned."""
import json

import pytest

from tracetrim.auto import AutoTrim
from tracetrim.engine import MARK, TEXT_LABEL, Engine, Trimmer
from tracetrim.projection import Projection
from tracetrim.runtime import Policy

SEEN = {"data[].id", "data[].memo", "data[].amount", "data[].meta.a", "data[].meta.b", "data[].note"}
MEMO = "Supplemento per 7 TB migrati oltre la soglia"


def output(n=3) -> str:
    return json.dumps({"data": [{"id": f"in_{i}", "memo": MEMO, "amount": 5 + i, "meta": {"a": 1, "b": 2},
                                 "note": "nota interna lunga " * 10} for i in range(n)]})


def policy(keep_fields=None) -> Policy:
    config = {"format": "tracetrim/config@1", "tools": {"t": {"trim": "auto", "expand": True}}}
    if keep_fields is not None:
        config["keep_fields"] = keep_fields
    return Policy(config, Projection("prudent", "full", {"t": {"data[].id"}}, {"t": set(SEEN)}))


def hidden(text: str) -> str:
    return text.split(MARK, 1)[1].split(" hidden: ", 1)[1].split(TEXT_LABEL)[0]


def test_a_field_and_a_group_kept_by_hand_are_never_hidden():
    p = policy({"t": ["data[].memo", "data[].meta"]})
    obs = Trimmer(p).observe("t", output())
    body = json.loads(obs.text.split(MARK)[0])["data"][0]
    assert body == {"id": "in_0", "memo": MEMO, "meta": {"a": 1, "b": 2}}
    assert hidden(obs.text) == "data[].amount, data[].note"
    assert "Supplemento" not in obs.text.split(MARK, 1)[1]           # visible, so not previewed either
    assert p.learned_projection.keep == {"t": {"data[].id"}}           # the learned projection is not changed


def test_without_the_option_nothing_changes():
    plain = policy()
    obs = Trimmer(plain).observe("t", output())
    assert "data[].memo" in hidden(obs.text) and plain.projection is plain.learned_projection
    assert Trimmer(policy({})).observe("t", output()).text.split(MARK)[0] == obs.text.split(MARK)[0]


def test_a_tool_the_projection_leaves_whole_is_left_alone():
    p = policy({"other": ["data[].memo"]})
    assert p.projection.keep == {"t": {"data[].id"}}


def test_the_engine_adds_its_fields_to_the_configs():
    e = Engine(policy=policy({"t": ["data[].memo"]}), breakdown=False, keep_fields={"t": ["data[].amount"]})
    assert e.policy.keep_fields == {"t": ["data[].memo", "data[].amount"]}
    assert "data[].amount" not in hidden(e.trimmer.observe("t", output()).text)


def test_automatic_mode_keeps_the_field_and_leaves_the_learned_files_alone(tmp_path):
    tt = AutoTrim(tmp_path, min_runs=2, keep_fields={"t": ["data[].memo"]})
    for i in range(2):                                  # learning: the answers use the ids only
        tt.output("t", {"q": i}, output())
        tt.done(f"Fatture in_0, in_1 e in_2 trovate, run {i}")
    assert tt.trimming
    sent = tt.output("t", {"q": 9}, output())
    assert MARK in sent and MEMO in sent.split(MARK)[0] and "data[].memo" not in hidden(sent)
    learned = json.loads((tmp_path / "report.projection.json").read_text())
    assert "data[].memo" in learned["dropped"]["t"]     # learning still says it was not used
    plain = AutoTrim(tmp_path, min_runs=2)              # the same work directory, without the option
    assert "data[].memo" in hidden(plain.output("t", {"q": 10}, output()))


@pytest.mark.parametrize("bad", [["data[].memo"], {"t": "data[].memo"}, {"t": [""]}, {"": ["x"]}, {"t": [3]}])
def test_every_entry_point_checks_the_fields(tmp_path, bad):
    with pytest.raises(ValueError):
        policy(bad)
    with pytest.raises(ValueError):
        Engine(keep_fields=bad)
    with pytest.raises(ValueError):
        AutoTrim(tmp_path, min_runs=2, keep_fields=bad)


def test_the_proxy_option(monkeypatch):
    from tracetrim import cli, proxy
    with pytest.raises(SystemExit, match="needs --auto WORKDIR or --config"):
        cli.main(["proxy", "--upstream-cmd", "python -m nothing", "--keep-field", "t", "data[].memo"])
    got = {}

    async def serve(o):
        got["o"] = o
    monkeypatch.setattr(proxy, "serve", serve)
    cli.main(["proxy", "--upstream-cmd", "python -m nothing", "--auto", "w", "--keep-field", "t", "data[].memo",
              "--keep-field", "t", "data[].meta", "--keep-field", "u", "x"])
    assert got["o"].keep_fields == {"t": ["data[].memo", "data[].meta"], "u": ["x"]}
