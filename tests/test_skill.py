"""The tracetrim skill: its text, its two levels, and the ways it reaches an agent without MCP."""
import hashlib
import json
import re
from pathlib import Path

import pytest

from tracetrim import skill
from tracetrim.auto import AutoTrim
from tracetrim.cli import main

TEXTS = Path(skill.__file__).parent / "skills" / "tracetrim"
# SHA-256 of each text at skill.VERSION. A change here without a new VERSION
# would give hosts two different skills under one version.
PINNED = {"1": {
    "done.md": "960512dc2eabb5c2771caa75d121548e80026b2c00f251e7d595803e7fc27541",
    "expand.md": "f103feef47e62b199a8972a66bfe290679124a633a93847053f124e331b7b20c",
    "strategy.md": "b7f7914377743b0c76bca4c373062598c6d9502a1d31c085923044e8addfe496"}}
LEVELS = [{"strategy": s, "done": d} for s in (False, True) for d in (True, False)]


def test_the_texts_match_the_version_they_are_published_under():
    now = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(TEXTS.glob("*.md"))}
    assert now == PINNED[skill.VERSION], "skill text changed: raise skill.VERSION and pin the new digests"


def test_base_is_always_on_and_strategy_only_when_asked():
    base, full = skill.text(), skill.text(strategy=True)
    assert "`tracetrim_done`" in base and "`expand`" in base and "Organising tool calls" not in base
    assert full.startswith(base) and "Organising tool calls" in full
    for heading in ("Run independent calls together", "Group inputs when a tool takes a list",
                    "Use the feedback of errors", "Search progressively", "Know when to stop"):
        assert f"### {heading}" in full
    assert skill.frontmatter()["metadata"]["strategy"] == "off"
    assert skill.frontmatter(strategy=True)["metadata"]["strategy"] == "on"


def test_the_answer_is_asked_whole_once_and_is_not_the_conversation():
    done = (TEXTS / "done.md").read_text()
    assert "exactly once" in done and "word for word" in done and "not the conversation" in done
    assert "do not call it again" in done


def test_the_strategy_prescribes_no_parameter_value_and_nothing_out_of_scope():
    strategy = (TEXTS / "strategy.md").read_text()
    assert not re.search(r"\d", strategy), "a number in the strategy is a prescribed value"
    assert "never from these notes" in strategy
    # Functions this release does not have: discovery mode, references, declared output fields.
    assert not re.search(r"\b(search/execute|execute|reference|output fields)\b", strategy, re.I)


@pytest.mark.parametrize("level", LEVELS)
def test_skill_md_is_a_valid_agent_skill(level):
    yaml = pytest.importorskip("yaml")
    md = skill.skill_md(**level)
    assert md.startswith("---\n")
    front, body = md[4:].split("\n---\n", 1)
    parsed = yaml.safe_load(front)
    # The listing must carry the frontmatter of the file, field by field.
    assert parsed == skill.frontmatter(**level)
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", parsed["name"]) and len(parsed["name"]) <= 64
    assert 0 < len(parsed["description"]) <= 1024
    assert all(isinstance(v, str) for v in parsed["metadata"].values())
    assert body.strip().endswith(skill.text(**level).strip()) and len(md.splitlines()) < 500


@pytest.mark.parametrize("level", LEVELS)
def test_the_manifest_describes_the_bytes_served(level):
    e = skill.entry(**level)
    raw = skill.skill_md(**level).encode("utf-8")
    assert e["uri"] == "skill://tracetrim/SKILL.md" and e["uri"].split("/")[-2] == e["frontmatter"]["name"]
    assert e["resources"] == [{"uri": e["uri"], "size": len(raw),
                               "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}]


def test_function_calling_gets_the_same_text_without_tracetrim_done(tmp_path):
    tt = AutoTrim(tmp_path)
    assert tt.strategy is False and tt.instructions == skill.text(done=False)
    assert "tracetrim_done" not in tt.instructions and "`expand`" in tt.instructions
    assert tt.expand_tool["name"] == "expand"
    with_strategy = AutoTrim(tmp_path, strategy=True).instructions
    assert with_strategy == skill.text(strategy=True, done=False) and "Know when to stop" in with_strategy


def test_the_cli_prints_the_text_or_writes_a_skill_folder(tmp_path, capsys):
    main(["skill"])
    assert capsys.readouterr().out == skill.text()
    main(["skill", "--strategy", "--no-done"])
    assert capsys.readouterr().out == skill.text(strategy=True, done=False)
    main(["skill", "--strategy", "--write", str(tmp_path)])
    written = tmp_path / "tracetrim" / "SKILL.md"
    assert capsys.readouterr().out.strip() == str(written)
    assert written.read_text() == skill.skill_md(strategy=True)


def test_the_tool_descriptions_are_enough_without_the_skill():
    from tracetrim.auto import DONE, DONE_REPLY
    from tracetrim.engine import EXPAND
    assert "once" in DONE["description"] and "complete" in DONE["description"]
    assert "not the conversation" in json.dumps(DONE["inputSchema"])
    assert "call_id" in EXPAND["description"] and "[trimmed]" in EXPAND["description"]
    assert DONE_REPLY == "Recorded."
