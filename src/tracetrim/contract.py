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
SAMPLED = "sampled"      # the projection would have removed fields; this once, on purpose, it did not

# Kinds of evidence about a field the projection removes. They are kept apart:
# each says something different, and none is proof that a field is needed.
REQUESTED = "requested"        # the model asked for it by name (or for its group) with `expand`
RECOVERED = "recovered"        # an `expand` gave it back, however it was asked for ("*" included)
REUSED = "reused"              # after getting it back, the model wrote its value
SEEN_FULL = "seen_full"        # it reached the model in a sampled, unfiltered result
USED_IN_FULL = "used_in_full"  # ... and the model then wrote its value
KINDS = (REQUESTED, RECOVERED, REUSED, SEEN_FULL, USED_IN_FULL)


@dataclass(frozen=True)
class ToolCall:
    tool: str                  # the tool as tracetrim names it in traces, config and report
    arguments: dict
    turn: int | None = None    # the model turn that emitted the call, when the integration knows it
    # Where the call really went, when the integration knows: an MCP proxy in
    # front of several servers does, a function-calling loop leaves both empty.
    server: str | None = None
    upstream_tool: str | None = None


@dataclass(frozen=True)
class ToolResult:
    output: str                # the text the tool returned, untouched
    # True when the result must reach the model exactly as sent: an error, or
    # content that is not plain text. It is recorded, never rewritten.
    verbatim: bool = False


@dataclass(frozen=True)
class Observation:
    text: str                          # what the model receives
    exposure: str = FULL               # FULL, FILTERED or SAMPLED
    call_id: str | None = None         # the handle `expand` takes, when it can give something back
    # The paths the projection removes from this result: actually removed when
    # FILTERED, let through this once when SAMPLED.
    hidden: tuple[str, ...] = ()


@dataclass(frozen=True)
class Evidence:
    kind: str                  # one of KINDS
    tool: str                  # the tool whose result it is about
    call_id: str | None        # the observation it refers to (a sampled result has no handle)
    paths: tuple[str, ...]     # the fields concerned
