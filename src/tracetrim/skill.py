"""The tracetrim skill: what an agent needs to know to work behind tracetrim.

Two levels, written once in `skills/tracetrim/*.md` and versioned with the code:

  base      always on: pass the final answer to `tracetrim_done` (done.md),
            recover trimmed fields with `expand` (expand.md)
  strategy  off unless asked for: how to organise tool calls (strategy.md).
            It teaches a method and prescribes no parameter value: limits,
            thresholds, ranges and budgets stay with the prompts and the
            tools' schemas.

The same text reaches the agent in three ways:

  MCP Skills      the proxy publishes it as an Agent Skill (`skill://tracetrim/SKILL.md`,
                  extension io.modelcontextprotocol/skills): hosts that load skills find it
  function calling `AutoTrim(...).instructions`, or `text()`, for the system prompt
  by hand         `tracetrim skill [--strategy]` prints it, for hosts that do not load skills

This module needs no SDK: the proxy serves what `entry()` and `skill_md()` return.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

NAME = "tracetrim"
# Version of the text. Raise it whenever a file in skills/tracetrim/ changes:
# hosts tell one edition from another by it (and by the digest).
VERSION = "1"
URI = f"skill://{NAME}/SKILL.md"
EXTENSION = "io.modelcontextprotocol/skills"
_DIR = Path(__file__).parent / "skills" / NAME


def _parts(strategy: bool, done: bool, expand: bool = True) -> list[str]:
    return (["done"] if done else []) + (["expand"] if expand else []) + (["strategy"] if strategy else [])


def text(strategy: bool = False, done: bool = True, expand: bool = True) -> str:
    """The instructions, as Markdown. `done=False` leaves out `tracetrim_done`:
    a function-calling loop passes the answer itself (`AutoTrim.done`).
    `expand=False` leaves out `expand` too: with `strategy=True` that is the
    strategy alone, for an agent whose tool results are not trimmed."""
    return "\n".join((_DIR / f"{p}.md").read_text(encoding="utf-8") for p in _parts(strategy, done, expand))


def frontmatter(strategy: bool = False, done: bool = True) -> dict:
    what = ("pass your final answer to tracetrim_done, " if done else "") + "recover trimmed fields with expand"
    when = ("the tools tracetrim_done or expand are available" if done else "the tool expand is available")
    return {
        "name": NAME,
        "description": (f"How to work with tools whose results tracetrim trims: {what}"
                        + (", and organise tool calls (independent calls together, grouped inputs, error "
                           "feedback, progressive search, when to stop)" if strategy else "")
                        + f". Use whenever {when}, or a tool result ends with a '[trimmed]' line."),
        "license": "MIT",
        "metadata": {"version": VERSION, "strategy": "on" if strategy else "off"},
    }


def pointer(done: bool = True) -> str:
    """One sentence for the server's MCP `instructions`: the only way a host
    that does not load skills learns that the skill exists."""
    tools = "`expand` and `tracetrim_done`" if done else "`expand`"
    return f"How to use {tools} is explained in the resource {URI}: read it before you first use them."


def _yaml(d: dict, indent: str = "") -> str:
    # Strings as JSON strings, which are valid YAML: no YAML library needed,
    # and the frontmatter served is exactly the one listed.
    return "".join(f"{indent}{k}:\n{_yaml(v, indent + '  ')}" if isinstance(v, dict)
                   else f"{indent}{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in d.items())


def skill_md(strategy: bool = False, done: bool = True) -> str:
    """The SKILL.md file: YAML frontmatter, then the instructions."""
    return f"---\n{_yaml(frontmatter(strategy, done))}---\n\n# tracetrim\n\n{text(strategy, done)}"


def entry(strategy: bool = False, done: bool = True) -> dict:
    """The skill as `skills/list` and `skills/get` return it: the frontmatter,
    and the manifest of its files with size and SHA-256 of the bytes served."""
    raw = skill_md(strategy, done).encode("utf-8")
    return {"uri": URI, "frontmatter": frontmatter(strategy, done),
            "resources": [{"uri": URI, "digest": "sha256:" + hashlib.sha256(raw).hexdigest(), "size": len(raw)}]}
