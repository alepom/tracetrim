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
import re
from dataclasses import replace
from pathlib import Path

from .projection import Projection, dumps
from .usage import parse_output

# Characters of hidden free text shown on the `[trimmed]` line (tracetrim.engine.previews). On by default since 1.5:
# in protocol v3e, 40 characters raised the hidden facts reported from 91.3% to 99.5% at the same cost on ordinary
# tasks. 0 turns them off, as in 1.4.
TEXT_PREVIEW = 40


def check_preview(value) -> int:
    """A number of characters for the text previews, the same check wherever it is set (config, Engine, AutoTrim)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"text_preview is a number of characters, 0 (off) or more, not {value!r}")
    return value


def check_keep_fields(value) -> dict[str, list[str]]:
    """`{tool: [field, ...]}`, the fields a person wants never hidden (1.5), the same check wherever it is set."""
    if value is None:
        return {}
    if not isinstance(value, dict) or any(
            not isinstance(tool, str) or not tool or not isinstance(fields, (list, tuple))
            or not all(isinstance(f, str) and f for f in fields) for tool, fields in value.items()):
        raise ValueError(f"keep_fields is {{tool: [field, ...]}} with non-empty names, not {value!r}")
    # A field written with an index (data[0].memo) means the same field in every element, as in `expand`.
    return {tool: list(dict.fromkeys(re.sub(r"\[\d+\]", "[]", f) for f in fields)) for tool, fields in value.items()}


def kept_by_hand(projection: Projection, fields: dict[str, list[str]]) -> tuple[dict[str, set[str]], list[str]]:
    """The paths of `projection` that the fields kept by hand cover, per tool trimmed (the field itself, with `[]`
    for a list of values, and every field under it that learning saw), and what matches nothing, as messages."""
    covered: dict[str, set[str]] = {}
    unmatched = []
    for tool, wanted in fields.items():
        if tool not in projection.keep:
            trimmed = ", ".join(sorted(projection.keep)) or "none yet"
            unmatched.append(f"keep_fields: {tool!r} is not a tool this policy trims (it trims: {trimmed})")
            continue
        seen = projection.seen.get(tool, set())
        paths = covered.setdefault(tool, set())
        for f in wanted:
            under = {s for s in seen if s == f or s.startswith(f + ".") or s.startswith(f + "[")}
            if not under and projection.unseen == "keep":
                unmatched.append(f"keep_fields: {f!r} of {tool!r} matches no field seen while learning")
            paths.update(under | {f, f + "[]"})
    return covered, unmatched


class Policy:
    def __init__(self, config: dict, projection: Projection | None):
        self.config = config
        # The projection as learned or loaded; `projection` is the one applied, with the fields kept by hand.
        self.learned_projection = projection
        self.projection = projection
        self.keep_fields: dict[str, list[str]] = {}
        self.keep_unmatched: list[str] = []      # entries of keep_fields that match nothing here, as messages
        self.keep(check_keep_fields(config.get("keep_fields")))
        self.tools = config.get("tools", {})
        self.unescape = bool(config.get("unescape", True))
        # Compact JSON, no spaces after separators: lossless. Off when the key is
        # missing, so that a config written before 0.4 serves what it served.
        self.compact = bool(config.get("compact", False))
        # A list element left with no field keeps its place, so that a position
        # the model reads is the one `expand` reads. On when the key is missing:
        # a fix (1.1.2); `"keep_positions": false` restores the earlier behaviour.
        self.keep_positions = bool(config.get("keep_positions", True))
        # 1.5: the `[trimmed]` line also shows the start of the hidden free-text fields, so that a description that
        # holds the answer does not look like an empty one. TEXT_PREVIEW when the key is missing or null, as in a
        # config written before 1.5; `"text_preview": 0` restores the line of 1.4.
        preview = config.get("text_preview")
        self.text_preview = TEXT_PREVIEW if preview is None else check_preview(preview)
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

    def keep_only(self, fields: dict[str, list[str]]) -> None:
        """Exactly these fields kept by hand, whatever the config said: automatic mode, where the option decides (the
        learned config records the option of the run that wrote it, for `proxy --config` and `tracetrim apply`)."""
        self.keep_fields = {}
        self.keep(fields)

    def keep(self, fields: dict[str, list[str]]) -> None:
        """Fields never hidden, whatever learning found (1.5, `--keep-field`): for a person who knows that a field
        matters to the process even when the agent rarely uses it, an invoice memo for instance. A field is written
        as on the `[trimmed]` line (`data[].memo`); a group (`data[].metadata`) keeps every field under it that
        learning saw. Added to what the projection keeps, never removed from it; the learned projection, and its
        file, stay as they are. A tool this policy does not trim, or a field that matches nothing seen, is listed in
        `keep_unmatched` (the engine warns): a tool name that differs from the one the proxy uses (`billing__t`)
        would otherwise be ignored in silence."""
        for tool, wanted in fields.items():
            have = self.keep_fields.setdefault(tool, [])
            have.extend(f for f in wanted if f not in have)
        p = self.learned_projection
        if p is None or not self.keep_fields:
            self.projection, self.keep_unmatched = p, []
            return
        covered, self.keep_unmatched = kept_by_hand(p, self.keep_fields)
        keep = {tool: set(paths) | covered.get(tool, set()) for tool, paths in p.keep.items()}
        self.projection = replace(p, keep=keep)

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
