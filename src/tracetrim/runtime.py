"""Apply a runtime config to tool outputs.

A config (`tracetrim/config@1`) is what `tracetrim report` writes from its
automatic actions: which tools get the projection, whether JSON is
re-serialised without escapes. It is plain JSON on purpose: a person can
read it, change it, and review it in a pull request like any other config.

Stateful features (`expand`, the cache of identical calls) need to live
where the calls happen, in a proxy or an SDK adapter: here they are only
read and exposed.
"""
from __future__ import annotations

import json
from pathlib import Path

from .projection import Projection
from .usage import parse_output


class Policy:
    def __init__(self, config: dict, projection: Projection | None):
        self.config = config
        self.projection = projection
        self.tools = config.get("tools", {})
        self.unescape = bool(config.get("unescape", True))
        self.cache_identical_calls = bool(config.get("cache_identical_calls", False))

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        path = Path(path)
        config = json.loads(path.read_text(encoding="utf-8"))
        if config.get("format") != "tracetrim/config@1":
            raise ValueError(f"{path.name}: not a tracetrim config")
        proj = config.get("projection")
        return cls(config, Projection.load(path.parent / proj) if proj else None)

    def expand_enabled(self, tool: str) -> bool:
        return bool(self.tools.get(tool, {}).get("expand"))

    def apply(self, tool: str, output: str) -> str:
        spec = self.tools.get(tool, {})
        if self.projection and spec.get("trim", "off") != "off":
            return self.projection.apply(tool, output)
        if self.unescape:
            payload = parse_output(output)
            if payload is not None:
                return json.dumps(payload, ensure_ascii=False)
        return output
