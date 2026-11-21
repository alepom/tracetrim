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
    "strategy.md": "b7f7914377743b0c76bca4c373062598c6d9502a1d31c085923044e8addfe496"},
    # 2 (1.4): expand.md adds the check before declaring information absent; done.md and strategy.md unchanged.
    "2": {
    "done.md": "960512dc2eabb5c2771caa75d121548e80026b2c00f251e7d595803e7fc27541",
    "expand.md": "b80a8ff64d4df82048b5d31d7b5a313df2af09c6e2e6f42121f69eeb698f342a",
    "strategy.md": "b7f7914377743b0c76bca4c373062598c6d9502a1d31c085923044e8addfe496"}}
LEVELS = [{"strategy": s, "done": d} for s in (False, True) for d in (True, False)]


def test_the_texts_match_the_version_they_are_published_under():
    now = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(TEXTS.glob("*.md"))}
    assert now == PINNED[skill.VERSION], "skill text changed: raise skill.VERSION and pin the new digests"


@pytest.mark.parametrize("version", ["1", "2"])
def test_every_version_serves_exactly_its_pinned_texts(version):
    for part, digest in PINNED[version].items():
        served = skill._file(part.removesuffix(".md"), version).read_bytes()
        assert hashlib.sha256(served).hexdigest() == digest, (version, part)


def test_version_1_is_the_text_of_1_3_and_version_2_adds_the_absence_check():
    from tracetrim.engine import expand_tool
    old, new = skill.text(version="1"), skill.text()
    assert "missing, unknown or not in the data" in new and "missing" not in old
    assert new.startswith(old.rstrip("\n").rsplit("\n", 1)[0][:200])
    assert "missing or unknown" in expand_tool()["description"]
    assert "missing" not in expand_tool("1")["description"]
    assert skill.frontmatter(version="1")["metadata"]["version"] == "1"
    with pytest.raises(ValueError):
        skill.text(version="3")


def test_the_proxy_and_the_cli_choose_the_version(tmp_path, capsys):
    from tracetrim import proxy
    assert proxy.Options().skill_version == skill.VERSION == "2"
    main(["skill", "--skill-version", "1"])
    assert capsys.readouterr().out == skill.text(version="1")
    tt = AutoTrim(tmp_path, skill_version="1")
    assert tt.instructions == skill.text(done=False, version="1") and "missing" not in tt.expand_tool["description"]
    assert "missing" in AutoTrim(tmp_path / "b").expand_tool["description"]


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


def test_the_strategy_can_be_had_alone_for_an_agent_whose_results_are_not_trimmed():
    alone = skill.text(strategy=True, done=False, expand=False)
    assert alone == (TEXTS / "strategy.md").read_text() and "expand" not in alone and "tracetrim" not in alone
    assert skill.text(done=False, expand=False) == ""


def test_the_cli_prints_the_text_or_writes_a_skill_folder(tmp_path, capsys):
    main(["skill"])
    assert capsys.readouterr().out == skill.text()
    main(["skill", "--strategy", "--no-done"])
    assert capsys.readouterr().out == skill.text(strategy=True, done=False)
    main(["skill", "--strategy", "--write", str(tmp_path)])
    written = tmp_path / "tracetrim" / "SKILL.md"
    assert capsys.readouterr().out.strip() == str(written)
    assert written.read_text() == skill.skill_md(strategy=True)


def test_the_token_counts_in_the_documentation_are_the_measured_ones():
    tiktoken = pytest.importorskip("tiktoken")
    count = lambda t: len(tiktoken.get_encoding("o200k_base").encode(t))   # noqa: E731
    base, full = count(skill.text()), count(skill.text(strategy=True))
    doc = (Path(__file__).resolve().parent.parent / "docs" / "skill.md").read_text()
    assert f"| {base} |" in doc and f"| +{full - base} ({full} in all) |" in doc
    assert f"({count(skill.skill_md())} and {count(skill.skill_md(strategy=True))})" in doc
    assert f"points to the skill ({count(skill.pointer())} tokens)" in doc


def test_the_tool_descriptions_are_enough_without_the_skill():
    from tracetrim.auto import DONE, DONE_REPLY
    from tracetrim.engine import EXPAND
    assert "once" in DONE["description"] and "complete" in DONE["description"]
    assert "not the conversation" in json.dumps(DONE["inputSchema"])
    assert "call_id" in EXPAND["description"] and "[trimmed]" in EXPAND["description"]
    assert DONE_REPLY == "Recorded."


@pytest.mark.parametrize("version", ["1", "2"])
def test_the_proxy_lists_expand_and_publishes_the_skill_of_its_version(tmp_path, version):
    import asyncio
    import sys
    from mcp import Client
    from tracetrim import proxy
    from tracetrim.engine import expand_tool
    upstream = f"{sys.executable} {Path(__file__).resolve().parent / 'fixtures' / 'meta_server.py'}"

    async def go():
        server, _ = proxy._server(proxy.Options(upstream_cmd=upstream, auto_dir=str(tmp_path), quiet=True,
                                                skill_version=version))
        async with Client(server) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert tools["expand"].description == expand_tool(version)["description"]
            read = await client.read_resource(skill.URI)
            assert read.contents[0].text == skill.skill_md(done=True, version=version)
    asyncio.run(go())


def test_every_skill_file_is_installed_with_the_package():
    """Each text a version serves is matched by the package data of pyproject.toml (1.4: v1/ is a subfolder)."""
    import fnmatch
    import tomllib
    root = Path(skill.__file__).resolve().parent
    patterns = tomllib.loads((root.parent.parent / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["tracetrim"]
    for path in (root / "skills").rglob("*.md"):
        rel = path.relative_to(root).as_posix()
        assert any(fnmatch.fnmatch(rel, p) and rel.count("/") == p.count("/") for p in patterns), rel
    for version in skill.VERSIONS:
        for part in ("done", "expand", "strategy"):
            assert skill._file(part, version).exists()
