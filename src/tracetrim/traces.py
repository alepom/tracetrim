"""Execution traces in a framework-neutral form.

A trace is the ordered record of what an agent was given and what it wrote:

  prompt    text the agent received (system, developer, user)
  call      a tool call: the arguments the agent wrote, and the raw output
  message   text the agent wrote outside tool calls, final answer included

The distinction between `call` and `message` matters: an MCP proxy sees only
calls, a proxy in front of the model sees both. Learning what the agent uses
depends heavily on which of the two you can observe.

Two loaders: the native JSONL format, and OpenAI Playground Python exports.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Event:
    kind: str                  # "prompt" | "call" | "message"
    text: str = ""             # prompt/message text; for calls, the arguments
    tool: str = ""             # calls only
    output: str | None = None  # calls only; None when the output is not exposed
    # Model turn that produced the event (the prompt: -1). Optional: when every
    # event has one, "after" means a later turn, so that calls emitted together
    # do not count as using each other's outputs.
    turn: int | None = None


@dataclass
class Trace:
    name: str
    events: list[Event] = field(default_factory=list)

    @property
    def calls(self) -> list[Event]:
        return [e for e in self.events if e.kind == "call" and e.output is not None]


def load(path: str | Path) -> Trace:
    p = Path(path)
    if p.suffix == ".jsonl":
        return load_jsonl(p)
    if p.suffix == ".py":
        return load_openai_playground(p)
    raise ValueError(f"unknown trace format: {p.name} (expected .jsonl or .py)")


def load_jsonl(path: Path) -> Trace:
    """One event per line: {"kind": ..., "text"|"tool"|"arguments"|"output": ...}.

    `arguments` may be an object or the JSON text the model wrote.
    """
    trace = Trace(path.name)
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        d = json.loads(line)
        kind = d.get("kind")
        if kind == "call":
            args = d.get("arguments", "")
            trace.events.append(Event(
                "call",
                text=args if isinstance(args, str) else json.dumps(args, ensure_ascii=False),
                tool=d.get("tool") or "?",
                output=d.get("output"),
                turn=d.get("turn"),
            ))
        elif kind in ("prompt", "message"):
            trace.events.append(Event(kind, text=d.get("text") or "", turn=d.get("turn")))
        else:
            raise ValueError(f"{path.name}:{n}: unknown event kind {kind!r}")
    return trace


class _BareNull(ast.NodeTransformer):
    """Playground exports leave a few JSON `null`s bare in Python source.

    They are fixed on the syntax tree, never on the text: the tool outputs are
    JSON *strings* full of `null`/`true`/`false`, and a textual replacement
    corrupts them. That mistake once turned a -29.7% result into -1.7%.
    """

    def visit_Name(self, node):
        if node.id == "null":
            return ast.copy_location(ast.Constant(value=None), node)
        return node


def load_openai_playground(path: Path) -> Trace:
    """The `input=[...]` of a `responses.create()` call exported as Python."""
    tree = _BareNull().visit(ast.parse(path.read_text(encoding="utf-8")))
    items = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "input":
                    items = ast.literal_eval(kw.value)
    if items is None:
        raise ValueError(f"{path.name}: no `input=` argument found")

    trace = Trace(path.name)
    for v in items:
        if not isinstance(v, dict):
            continue
        role = v.get("role")
        if role in ("user", "developer", "system", "assistant"):
            content = v.get("content")
            if isinstance(content, list):
                text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            else:
                text = str(content or "")
            trace.events.append(Event("message" if role == "assistant" else "prompt", text=text))
        elif v.get("type") == "web_search_call":
            # Native search: the query is written by the agent, the results
            # are never exposed by the API. A call with no visible output.
            trace.events.append(Event(
                "call", text=json.dumps(v.get("action") or {}, ensure_ascii=False),
                tool="web_search", output=None))
        elif v.get("type") == "mcp_call":
            raw = v.get("arguments") or ""
            try:
                text = json.dumps(json.loads(raw), ensure_ascii=False)
            except json.JSONDecodeError:
                text = raw
            trace.events.append(Event(
                "call", text=text, tool=v.get("name") or "?", output=v.get("output") or ""))
    return trace
