"""Apply a runtime config to tool outputs.

A config (`tracetrim/config@1`) is what `tracetrim report` writes from its
automatic actions: which tools get the projection, whether JSON is
re-serialised without escapes. It is plain JSON on purpose: a person can
read it, change it, and review it in a pull request like any other config.

Stateful features (`expand`) need to live where the calls happen, in a
proxy or an SDK adapter: here they are only read and exposed.
"""
from __future__ import annotations

import json
from pathlib import Path

from .projection import Projection, dumps
from .usage import parse_output


class Policy:
    def __init__(self, config: dict, projection: Projection | None):
        self.config = config
        self.projection = projection
        self.tools = config.get("tools", {})
        self.unescape = bool(config.get("unescape", True))
        # Compact JSON, no spaces after separators: lossless. Off when the key is
        # missing, so that a config written before 0.4 serves what it served.
        self.compact = bool(config.get("compact", False))
        # A list element left with no field keeps its place, so that a position
        # the model reads is the one `expand` reads. On when the key is missing:
        # a fix (1.1.2); `"keep_positions": false` restores the earlier behaviour.
        self.keep_positions = bool(config.get("keep_positions", True))
        # 1.5, off by default (0): the `[trimmed]` line also shows the first N characters of the hidden free-text
        # fields, so that a description that holds the answer does not look like an empty one (tracetrim.engine).
        preview = config.get("text_preview", 0) or 0
        if isinstance(preview, bool) or not isinstance(preview, int) or preview < 0:
            raise ValueError(f"text_preview is a number of characters, 0 (off) or more, not {preview!r}")
        self.text_preview = preview
        # Optional, after the projection: "toon" (lossless re-encoding) or
        # "headroom" (experimental). The study found that a denser format can
        # change how the model reads a payload: validate before relying on it.
        self.structural = config.get("structural")
        if self.structural not in (None, "toon", "headroom"):
            raise ValueError(f"structural must be null, 'toon' or 'headroom', not {self.structural!r}")
        # Automatic mode (1.4): how many corrections of its Learner this policy was learned after. A result it
        # filtered counts against a tool only if the policy came after the tool's last correction
        # (tracetrim.auto.Learner.observed). 0 for a policy read from a file.
        self.corrections = 0

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

    def apply(self, tool: str, output: str, project: bool = True) -> str:
        """`project=False`: everything but the projection (re-serialisation,
        structural encoding): the whole output, as it is shown when a call is
        sampled without filter."""
        return self._structural(tool, self._project(tool, output, project))

    def _project(self, tool: str, output: str, project: bool = True) -> str:
        spec = self.tools.get(tool, {})
        if project and self.projection and spec.get("trim", "off") != "off":
            return self.projection.apply(tool, output, keep_empty=True, compact=self.compact,
                                         keep_positions=self.keep_positions)
        if self.unescape or self.compact:
            payload = parse_output(output)
            if payload is not None:
                return dumps(payload, self.compact)
        return output

    def _structural(self, tool: str, output: str) -> str:
        if self.structural == "toon":
            from .structural import structural
            return structural(output, keep_positions=self.keep_positions)
        if self.structural == "headroom":
            from .structural import headroom
            return headroom(output, tool)
        return output
