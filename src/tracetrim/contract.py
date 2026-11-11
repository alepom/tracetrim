"""The four things tracetrim's core reasons about, in a form no SDK owns.

  ToolCall      what the agent asked for
  ToolResult    what the tool sent back: the data tracetrim RECEIVES
  Observation   what goes on to the model: the data tracetrim EXPOSES
  Evidence      a sign that the model needed a field

The MCP proxy and `AutoTrim` (function calling) translate their own events
into these and hand them to `tracetrim.engine.Engine`; nothing here, or in
the engine, imports an MCP or model-provider SDK. The two sides of a call
are kept apart on purpose: learning reads the result, the model reads the
observation, and a field can be in one and not in the other.
"""
from __future__ import annotations

from dataclasses import dataclass

# How a result reached the model.
FULL = "full"            # nothing removed (it may have been re-serialised, which loses nothing)
FILTERED = "filtered"    # fields removed by the projection

# Kinds of evidence.
RECOVERED = "recovered"  # fields the model got back with `expand`


@dataclass(frozen=True)
class ToolCall:
    tool: str                  # the tool as tracetrim names it in traces, config and report
    arguments: dict
    turn: int | None = None    # the model turn that emitted the call, when the integration knows it


@dataclass(frozen=True)
class ToolResult:
    output: str                # the text the tool returned, untouched
    # True when the result must reach the model exactly as sent: an error, or
    # content that is not plain text. It is recorded, never rewritten.
    verbatim: bool = False


@dataclass(frozen=True)
class Observation:
    text: str                          # what the model receives
    exposure: str = FULL               # FULL or FILTERED
    call_id: str | None = None         # the handle `expand` takes, when it can give something back
    hidden: tuple[str, ...] = ()       # the paths removed from this result


@dataclass(frozen=True)
class Evidence:
    kind: str                  # RECOVERED
    tool: str                  # the tool whose result it is about
    call_id: str               # the observation it refers to
    paths: tuple[str, ...]     # the fields concerned
