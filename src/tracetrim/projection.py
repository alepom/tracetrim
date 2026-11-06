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


def identifying(path: str) -> bool:
    return bool(IDENTIFYING.search(path.rsplit(".", 1)[-1].removesuffix("[]")))


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

    def apply(self, tool: str, output: str, keep_empty: bool = False) -> str:
        """The trimmed output.

        `keep_empty`: keep lists and objects that were already empty in the
        output, so that "no results" does not look like "hidden". Used at run
        time (proxy, AutoTrim) since 0.3.4; the studies measured without it.

        Anything that parses as JSON is at least re-serialised without ASCII
        escapes (`\\u00e0` -> `à`): lossless, and it saves tokens on its own.
        Fields are dropped only for structured payloads of known tools.
        """
        payload = parse_output(output)
        if payload is None:
            return output                    # error message, prose, empty
        if not isinstance(payload, (dict, list)) or tool not in self.keep:
            return json.dumps(payload, ensure_ascii=False)
        drop = (self.seen.get(tool, set()) - self.keep[tool]) if self.unseen == "keep" else None
        p = project(payload, self.keep[tool], drop=drop, keep_empty=keep_empty)
        return json.dumps({} if p is _EMPTY else p, ensure_ascii=False)


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
          protect_ids: bool = True) -> Projection:
    """`usages`: one list of CallUsage per trace (see usage.analyse)."""
    if mode not in MODES or source not in SOURCES or unseen not in UNSEEN:
        raise ValueError(f"mode in {MODES}, source in {SOURCES}, unseen in {UNSEEN}")
    used, judgeable, seen = defaultdict(set), defaultdict(set), defaultdict(set)
    for calls in usages:
        for c in calls:
            if not c.structured:
                continue
            used[c.tool] |= (c.used if source == "full" else c.used_by_calls) | c.echo
            judgeable[c.tool] |= c.judgeable
            seen[c.tool] |= {p for p, _ in leaves(c.payload)}
    keep = {t: used[t] | (seen[t] - judgeable[t]) if mode == "prudent" else used[t] for t in seen}
    if protect_ids:
        keep = {t: k | {p for p in seen[t] if identifying(p)} for t, k in keep.items()}
    return Projection(mode, source, keep, {t: set(v) for t, v in seen.items()}, unseen)
