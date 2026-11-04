"""Which fields of a tool output did the agent use?

A field is *used* if one of its values shows up in something the agent wrote
AFTER receiving it, and not before. "Not before" excludes echoes: `keyword`
in a search-volume response is the keyword the agent had just asked for,
not information the tool gave it.

Echoes are still kept, for a different reason: they are the record's key.
Without `keyword`, the volumes survive and nobody knows which keyword they
belong to. The plan stays plausible and the numbers are wrong.

What this can see is a LOWER BOUND of real use. Reasoning is usually
encrypted or summarised away: a field read only to rank or discard shows up
as "never used". Fields whose values cannot be attributed at all (small
numbers, booleans, short labels) are tracked as not *judgeable*, so that a
prudent projection can keep them.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .traces import Trace

N_MAX = 4
_WORD = re.compile(r"\w+(?:[.,]\w+)*")


def words(text: str) -> list[str]:
    # "1.600" and "www.example.com" stay one token: that is how a number or a
    # domain reappears in the agent's output.
    return _WORD.findall(text.lower())


def ngrams(ws: list[str]) -> set[tuple[str, ...]]:
    return {tuple(ws[j:j + n]) for n in range(1, N_MAX + 1) for j in range(len(ws) - n + 1)}


def leaves(node, path=""):
    """(path, value) pairs. List indices become `[]`: the field is the same
    in the first and in the tenth result."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from leaves(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for v in node:
            yield from leaves(v, path + "[]")
    else:
        yield path, node


def keys_of(value) -> list[tuple[str, ...]]:
    """The n-grams under which a value can reappear, or none when it is too
    generic to attribute: `10`, `true`, `LOW` show up everywhere."""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, int):
        if abs(value) < 100:
            return []
        return [(str(value),), (f"{value:,}".replace(",", "."),)]
    if isinstance(value, float):
        s = repr(value)
        if len(s.replace(".", "").lstrip("-0")) < 3:
            return []
        return [(s,), (s.replace(".", ","),)]
    if not isinstance(value, str):
        return []
    ws = words(value)
    if not ws or (len(ws) == 1 and len(ws[0]) < 4):
        return []
    if len(ws) <= N_MAX:
        return [tuple(ws)]
    # Prose gets quoted in pieces, never whole: one 4-gram is enough.
    return [tuple(ws[i:i + N_MAX]) for i in range(len(ws) - N_MAX + 1)]


def parse_output(output: str | None):
    """The output as JSON, or None when it is an error, prose or hidden."""
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


@dataclass
class CallUsage:
    tool: str
    output: str
    payload: object                                  # parsed JSON, or None
    arguments: str = ""                              # as the agent wrote them
    used: set[str] = field(default_factory=set)        # seen in any later text
    used_by_calls: set[str] = field(default_factory=set)  # seen in later call arguments only
    used_as_text: set[str] = field(default_factory=set)   # confirmed by a string, not a number
    judgeable: set[str] = field(default_factory=set)
    echo: set[str] = field(default_factory=set)

    @property
    def structured(self) -> bool:
        return isinstance(self.payload, (dict, list))


def analyse(trace: Trace) -> list[CallUsage]:
    texts = [e.text for e in trace.events]

    # First event where each n-gram is written. Two versions: a proxy in front
    # of the model sees everything, an MCP proxy sees tool calls only.
    first, first_in_calls = {}, {}
    for i, e in enumerate(trace.events):
        for g in ngrams(words(e.text)):
            first.setdefault(g, i)
            if e.kind != "message":
                first_in_calls.setdefault(g, i)

    result = []
    for i, e in enumerate(trace.events):
        if e.kind != "call" or e.output is None:
            continue
        u = CallUsage(e.tool, e.output, parse_output(e.output), e.text)
        result.append(u)
        if not u.structured:
            continue
        own = ngrams(words(texts[i]))
        for path, value in leaves(u.payload):
            k = keys_of(value)
            if k:
                u.judgeable.add(path)
            if any(x in own for x in k):
                u.echo.add(path)
            seen = [first_in_calls[x] for x in k if x in first_in_calls]
            if seen and min(seen) > i:
                u.used_by_calls.add(path)
            seen = [first[x] for x in k if x in first]
            if seen and min(seen) > i:
                u.used.add(path)
                # Bucketed numbers (search volumes: 1,600, 2,400...) can match
                # by coincidence. A string cannot.
                if isinstance(value, str):
                    u.used_as_text.add(path)
    return result
