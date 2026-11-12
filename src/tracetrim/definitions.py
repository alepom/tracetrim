"""What the tool definitions weigh, and which tools the agent never called.

The model receives the definition of every listed tool on every turn. This
module measures that weight and, when a large part of it belongs to tools the
agent never called, writes a suggestion for a person. It never changes the
list: tracetrim hides no tool (docs/definitions.md says why).

  record(workdir, tools)    the tool list as published: the proxy's `tools/list`,
                            or the list a function-calling loop passes to
                            `AutoTrim.offered`; written to workdir/tools.json
                            only when it changes
  summarise(tools, called)  the report's section

Tokens are counted on the JSON of each definition as published, with the
tokenizer of the rest of the report. The provider bills its own rendering of
the definitions, and the weight per run is this figure times the model's turns,
which neither the proxy nor tracetrim sees: the report says so instead of
estimating it.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import store

FILE = "tools.json"
FORMAT = "tracetrim/tools@1"
# tracetrim's own tools: counted in the weight, never proposed for removal.
OWN = ("expand", "tracetrim_done")

# The suggestion appears when all three hold. Changing one changes when it
# appears, never the numbers it quotes.
NEVER_CALLED_MIN_SHARE = 0.5      # tools never called weigh at least half of the definitions
NEVER_CALLED_MIN_TOKENS = 5_000   # ... and at least this many tokens per turn
# (and at least `min_runs` complete runs: the caller's threshold, the same as for trimming)
LIST_MAX = 20                     # tools named in the suggestion, heaviest first


def name_of(tool: dict) -> str:
    """The name of a tool in MCP form (`name`) or in a provider's function form
    (`name`, or `function.name` as in Chat Completions)."""
    return str(tool.get("name") or (tool.get("function") or {}).get("name") or "")


def record(workdir: str | Path, tools: list[dict]) -> bool:
    """Write the tool list as published, if it changed. True when written."""
    path = Path(workdir) / FILE
    data = {"format": FORMAT, "tools": tools}
    if path.exists():
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("tools") == tools:
                return False
        except (OSError, ValueError, AttributeError):
            pass                                  # unreadable: written again
    store.write_json(path, data)
    return True


def load(workdir: str | Path) -> list[dict] | None:
    """The tool list last recorded, or None (never recorded, or unreadable)."""
    return read(Path(workdir) / FILE)


def read(path: str | Path) -> list[dict] | None:
    """A tool list from a file: ours, a saved `tools/list` result ({"tools": [...]}),
    or a plain JSON list of tools. None when missing or unreadable."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        tools = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(tools, dict):                   # ours, or a saved `tools/list` result
        tools = tools.get("tools")
    return tools if isinstance(tools, list) and all(isinstance(t, dict) for t in tools) else None


def summarise(tools: list[dict], called: list[set[str]], count, min_runs: int) -> dict:
    """The report's section. `called`: per complete run, the names of the tools it called."""
    weight = {}
    for t in tools:
        weight[name_of(t)] = weight.get(name_of(t), 0) + count(json.dumps(t, ensure_ascii=False))
    total = count(json.dumps(tools, ensure_ascii=False))
    offered = [n for n in weight if n not in OWN]
    used = set().union(*called) if called else set()
    never = sorted(({"name": n, "tokens": weight[n]} for n in offered if n not in used),
                   key=lambda x: (-x["tokens"], x["name"]))
    never_tokens = sum(x["tokens"] for x in never)
    share = never_tokens / total if total else 0.0
    return {
        "tokens_per_turn": total,
        "own_tokens": sum(weight[n] for n in weight if n in OWN),
        "own_tools": sorted(n for n in weight if n in OWN),
        "offered": len(offered),
        "called": sorted(n for n in offered if n in used),
        "never_called": never,
        "never_called_tokens": never_tokens,
        "never_called_share": share,
        "runs": len(called),
        "per_run": None,      # not computable here: the turns are the model's, not seen by tracetrim
        "suggest": (len(called) >= min_runs and share >= NEVER_CALLED_MIN_SHARE
                    and never_tokens >= NEVER_CALLED_MIN_TOKENS),
        "thresholds": {"min_runs": min_runs, "min_share": NEVER_CALLED_MIN_SHARE,
                       "min_tokens": NEVER_CALLED_MIN_TOKENS},
    }
