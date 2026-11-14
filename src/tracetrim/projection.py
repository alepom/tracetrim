"""Learn a projection from traces, save it, apply it to tool outputs.

Two modes, because the usage signal does not see everything:

  aggressive  keep the fields seen in use, plus the echoes (record keys)
  prudent     also keep the fields usage detection cannot judge
              (small numbers, booleans, labels the agent translates)

Two sources, because what you can observe depends on where you sit:

  full        everything the agent wrote, final answer included
  calls       later tool-call arguments only: what an MCP proxy can see

On the traces this was developed on, `calls` sees about a fifth of the
fields the agent visibly reuses. Learn offline from full traces; apply
anywhere, an MCP proxy included.

Fields never seen while learning pass through by default (`unseen="keep"`):
when an API adds a field, a projection must not hide it silently. `drop`
reproduces the strict behaviour of the original measurements.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .usage import CallUsage, leaves, parse_output

# Fields that identify a record, to a program or to a person. The pilot study
# showed that removing them is not just a loss: without the id, an agent
# invented ids; without issue titles, it could not match an issue to a failure.
# Kept whenever `protect_ids` is on, whatever the usage says.
IDENTIFYING = re.compile(r"^(id|uuid|guid|doi|url|html_url|uri|href|slug|number|title|name|display_name)$|_id$")


def dumps(payload, compact: bool = False) -> str:
    """JSON as tracetrim writes it: UTF-8, no indentation. `compact` also drops
    the spaces after `,` and `:`; lossless, and on DataForSEO outputs (o200k_base)
    worth about as much again as removing the indentation."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":") if compact else None)


def identifying(path: str) -> bool:
    # Every list level off the name: `ids[][]` is `ids` (until 1.0 only one level came off).
    name = path.rsplit(".", 1)[-1]
    while name.endswith("[]"):
        name = name[:-2]
    return bool(IDENTIFYING.search(name))


# Which identifying fields `protect_ids` keeps (since 1.1):
#   all     every field named like one, at any depth (the rule since 0.2, and the default)
#   record  names and titles at any depth; ids and links only when they belong to the record itself:
#           at most one list level, and no object between the list and the field (`results[].id`,
#           `items[].url`, `id`; with no list, also one wrapping object: `data.id`), not
#           `results[].authorships[].author.id`, `data[].refunds.url` or `data.customer.id`
#   none    no protection (the studies' 0.1 arm, `--v01`)
# Fields the agent was seen to use are kept in every case. On the study's traces, `record` removed 10-13
# more points of tool-output tokens on two cases, with no extra miss (`docs/measures-v1.md`); whether an
# agent makes up the nested identifiers it no longer sees was not measured live, so it is off by default.
PROTECT_IDS = ("all", "record", "none")
LABELS = ("title", "name", "display_name")


def record_level(path: str) -> bool:
    """An identifying field of the record itself, or a name or title at any depth.

    In a list, the record is each element: `items[].id`, not `items[].owner.id`. With no list, the record is the
    output itself or the one object that wraps it (`{"data": {"id": ...}}`, as many APIs return one record):
    `id` and `data.id`, not `data.customer.id`."""
    name = path.rsplit(".", 1)[-1]
    while name.endswith("[]"):
        name = name[:-2]
    if name in LABELS:
        return True
    if path.count("[]") > 1:
        return False
    if "[]" not in path:
        return path.count(".") <= 1
    return "." not in path.rsplit("[]", 1)[-1].lstrip(".")


def protect_mode(protect_ids) -> str:
    """`protect_ids` as one of PROTECT_IDS: True and False, as before 1.1, are "all" and "none"."""
    if protect_ids is True:
        return "all"
    if protect_ids is False or protect_ids is None:
        return "none"
    if protect_ids not in PROTECT_IDS:
        raise ValueError(f"protect_ids in {PROTECT_IDS} (or True/False)")
    return protect_ids


def protected(path: str, protect_ids="all") -> bool:
    mode = protect_mode(protect_ids)
    return mode != "none" and identifying(path) and (mode == "all" or record_level(path))


MODES = ("prudent", "aggressive")
SOURCES = ("full", "calls")
UNSEEN = ("keep", "drop")
_EMPTY = object()


@dataclass
class Projection:
    mode: str
    source: str
    keep: dict[str, set[str]] = field(default_factory=dict)
    seen: dict[str, set[str]] = field(default_factory=dict)
    unseen: str = "keep"

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({
            "format": "tracetrim/projection@1",
            "mode": self.mode,
            "source": self.source,
            "unseen": self.unseen,
            "tools": {t: sorted(p) for t, p in sorted(self.keep.items())},
            # What was seen and NOT kept: the fields this projection removes.
            "dropped": {t: sorted(self.seen.get(t, set()) - p) for t, p in sorted(self.keep.items())},
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Projection":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        keep = {t: set(p) for t, p in d["tools"].items()}
        seen = {t: keep[t] | set(d.get("dropped", {}).get(t, [])) for t in keep}
        return cls(d["mode"], d["source"], keep, seen, d.get("unseen", "keep"))

    def apply(self, tool: str, output: str, keep_empty: bool = False, compact: bool = False) -> str:
        """The trimmed output.

        `keep_empty`: keep lists and objects that were already empty in the
        output, so that "no results" does not look like "hidden". Used at run
        time (proxy, AutoTrim) since 0.3.4; the studies measured without it.
        `compact`: no spaces after separators (see `dumps`); the studies
        measured without it.

        Anything that parses as JSON is at least re-serialised without ASCII
        escapes (`\\u00e0` -> `à`): lossless, and it saves tokens on its own.
        Fields are dropped only for structured payloads of known tools.
        """
        payload = parse_output(output)
        if payload is None:
            return output                    # error message, prose, empty
        if not isinstance(payload, (dict, list)) or tool not in self.keep:
            return dumps(payload, compact)
        drop = (self.seen.get(tool, set()) - self.keep[tool]) if self.unseen == "keep" else None
        p = project(payload, self.keep[tool], drop=drop, keep_empty=keep_empty)
        # Everything removed: an empty container of the same kind (a list stays a list).
        return dumps(([] if isinstance(payload, list) else {}) if p is _EMPTY else p, compact)


def project(node, keep: set[str], path="", drop: set[str] | None = None, keep_empty: bool = False):
    """Keep the leaves in `keep`. With `drop` given, keep every leaf NOT in
    `drop` instead: fields never seen while learning survive. `keep_empty`:
    containers that were empty to begin with are kept as they are."""
    if keep_empty and isinstance(node, (dict, list)) and not node:
        return node
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            r = project(v, keep, f"{path}.{k}" if path else k, drop, keep_empty)
            if r is not _EMPTY:
                out[k] = r
        return out or _EMPTY
    if isinstance(node, list):
        out = [r for v in node if (r := project(v, keep, path + "[]", drop, keep_empty)) is not _EMPTY]
        return out or _EMPTY
    if drop is not None:
        return _EMPTY if path in drop else node
    return node if path in keep else _EMPTY


def learn(usages: list[list[CallUsage]], mode="prudent", source="full", unseen="keep",
          protect_ids: bool | str = True) -> Projection:
    """`usages`: one list of CallUsage per trace (see usage.analyse).
    `protect_ids`: True or "all" (the default), "record", False or "none" (see PROTECT_IDS)."""
    if mode not in MODES or source not in SOURCES or unseen not in UNSEEN:
        raise ValueError(f"mode in {MODES}, source in {SOURCES}, unseen in {UNSEEN}")
    protect = protect_mode(protect_ids)
    used, judgeable, seen = defaultdict(set), defaultdict(set), defaultdict(set)
    for calls in usages:
        for c in calls:
            if not c.structured:
                continue
            used[c.tool] |= (c.used if source == "full" else c.used_by_calls) | c.echo
            judgeable[c.tool] |= c.judgeable
            seen[c.tool] |= {p for p, _ in leaves(c.payload)}
    keep = {t: used[t] | (seen[t] - judgeable[t]) if mode == "prudent" else used[t] for t in seen}
    if protect != "none":
        keep = {t: k | {p for p in seen[t] if protected(p, protect)} for t, k in keep.items()}
    return Projection(mode, source, keep, {t: set(v) for t, v in seen.items()}, unseen)
