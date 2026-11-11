"""Tokens (tiktoken o200k_base) of the tracetrim skill and of the two tool definitions.

  python -m experiments.skill_tokens          # a table
  python -m experiments.skill_tokens --json

What each row is: the text as it enters a system prompt (`text`), the file a
host loads (`SKILL.md`: frontmatter + text), the `skills/list` entry, and the
tool definitions as JSON. How many of these tokens a model is billed for
depends on the host: these are counts of the representation, not a bill.
"""
import json
import sys

from tracetrim import skill
from tracetrim.auto import DONE
from tracetrim.engine import EXPAND
from tracetrim.measure import tokenizer


def rows() -> dict:
    count = tokenizer()
    base, full = skill.text(), skill.text(strategy=True)
    return {
        "skill_version": skill.VERSION,
        "text_base": count(base),
        "text_base_strategy": count(full),
        "text_strategy_added": count(full) - count(base),
        "skill_md_base": count(skill.skill_md()),
        "skill_md_base_strategy": count(skill.skill_md(strategy=True)),
        "function_calling_base": count(skill.text(done=False)),
        "function_calling_base_strategy": count(skill.text(strategy=True, done=False)),
        "skills_list_entry_base": count(json.dumps(skill.entry())),
        "tool_definition_tracetrim_done": count(json.dumps(DONE)),
        "tool_definition_expand": count(json.dumps(EXPAND)),
    }


if __name__ == "__main__":
    r = rows()
    if "--json" in sys.argv:
        print(json.dumps(r, indent=2))
    else:
        for k, v in r.items():
            print(f"{k:<34}{v:>6}")
