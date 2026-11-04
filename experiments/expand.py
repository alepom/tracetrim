"""Phase C: the live loop with a projection applied and an `expand` tool.

The trimmed output keeps its JSON shape; one plain line after it tells the
agent which fields were hidden and how to get them back. Without that line
the agent could not know what to ask for: `expand` is only as good as the
agent's knowledge of what is missing.
"""
from __future__ import annotations

import json

from tracetrim.projection import Projection, project
from tracetrim.usage import leaves, parse_output

MAX_GROUPS = 25
GROUP_DEPTH = 2     # data[].items.data[].current_period_end -> data[].items
EXPAND_SCHEMA = {
    "type": "function", "name": "expand", "strict": False,
    "description": ("Some tool results are trimmed to save context: the fields removed are listed in the line "
                    "'[trimmed] call_id=... hidden: ...' after the result. Call expand with that call_id and the "
                    "field paths you need to get their values from the original result."),
    "parameters": {"type": "object", "required": ["call_id", "fields"], "properties": {
        "call_id": {"type": "string", "description": "The call_id given in the [trimmed] line, e.g. 'c7'."},
        "fields": {"type": "array", "items": {"type": "string"},
                   "description": "Field paths or groups as listed, e.g. 'data[].items' returns every hidden "
                                  "field under it."}}},
}


class Trimmed:
    """Wraps a toolset: same tools, projected outputs, plus `expand`."""

    def __init__(self, inner, projection: Projection):
        self.inner = inner
        self.projection = projection
        self.originals: dict[str, object] = {}
        self.n = 0
        self.expand_calls = 0
        self.expanded_fields = 0

    @property
    def schemas(self):
        return self.inner.schemas + [EXPAND_SCHEMA]

    @property
    def rejections(self):
        return self.inner.rejections

    @property
    def quota_stop(self):
        return getattr(self.inner, "quota_stop", False)

    def _hidden(self, tool, payload) -> list[str]:
        keep = self.projection.keep.get(tool, set())
        drop = self.projection.seen.get(tool, set()) - keep
        return sorted({p for p, _ in leaves(payload) if p in drop})

    async def call(self, name: str, args: dict) -> str:
        if name == "expand":
            self.expand_calls += 1
            payload = self.originals.get(str(args.get("call_id", "")))
            if payload is None:
                return "Error: unknown call_id."
            wanted = [str(f) for f in args.get("fields") or []]
            # A group returns every field under it.
            fields = {p for p, _ in leaves(payload)
                      if any(p == w or p.startswith(w + ".") or p.startswith(w + "[]") for w in wanted)}
            self.expanded_fields += len(fields)
            got = project(payload, fields)
            return json.dumps(got if isinstance(got, (dict, list)) else {}, ensure_ascii=False)
        out = await self.inner.call(name, args)
        trimmed = self.projection.apply(name, out)
        payload = parse_output(out)
        if not isinstance(payload, (dict, list)) or name not in self.projection.keep:
            return trimmed
        hidden = self._hidden(name, payload)
        if not hidden:
            return trimmed
        self.n += 1
        cid = f"c{self.n}"
        self.originals[cid] = payload
        # Grouped, not truncated: an alphabetical cut can hide exactly the
        # field the agent needs (the pilot of Phase B showed it).
        groups: dict[str, int] = {}
        for h in hidden:
            g = ".".join(h.split(".")[:GROUP_DEPTH])
            groups[g] = groups.get(g, 0) + 1
        # Largest groups first: a whole hidden sub-object matters more than one scalar.
        ordered = sorted(groups.items(), key=lambda x: (-x[1], x[0]))
        items = [g if n == 1 and g in hidden else f"{g} ({n} field{'' if n == 1 else 's'})" for g, n in ordered]
        listed = ", ".join(items[:MAX_GROUPS]) + (f" (+{len(items) - MAX_GROUPS} more groups)" if len(items) > MAX_GROUPS else "")
        return f"{trimmed}\n[trimmed] call_id={cid} hidden: {listed}"
