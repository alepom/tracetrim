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
from datetime import datetime, timezone

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


MONTHS = {
    "it": ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
           "settembre", "ottobre", "novembre", "dicembre"),
    "en": ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"),
}
_MONEY = re.compile(r"(amount|price|total|subtotal|balance|cost|fee|tax)", re.I)
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _date_keys(d) -> list[tuple[str, ...]]:
    forms = [d.strftime("%Y-%m-%d"), d.strftime("%d/%m/%Y"), f"{d.day}/{d.month}/{d.year}"]
    keys = [tuple(words(f)) for f in forms]
    for names in MONTHS.values():
        m = names[d.month - 1]
        keys += [(str(d.day), m), (m, str(d.day)), (str(d.day), m, str(d.year))]
    return keys


def transformed_keys(value, path: str = "") -> list[tuple[str, ...]]:
    """Forms an agent writes a value in after transforming it.

    The pilot study lost information exactly where the agent did not copy a
    value but rewrote it: a Unix timestamp as "20 novembre", an amount in
    cents as "29,00", an OpenAlex URL as its short id. These normalisers are
    deliberately few and generic; each one is a hypothesis of the
    confirmation study, not a guarantee.
    """
    if isinstance(value, bool) or value is None:
        return []
    keys: list[tuple[str, ...]] = []
    if isinstance(value, int):
        if 946_684_800 <= value <= 4_102_444_800:          # 2000-01-01 .. 2100-01-01
            keys += _date_keys(datetime.fromtimestamp(value, timezone.utc))
        name = path.rsplit(".", 1)[-1]
        if _MONEY.search(name) and abs(value) >= 100:      # minor units: 2900 -> 29,00
            major = f"{value / 100:.2f}"
            grouped = f"{value / 100:,.2f}"
            for f in (major, major.replace(".", ","), grouped,
                      grouped.replace(",", "_").replace(".", ",").replace("_", ".")):
                keys.append(tuple(words(f)))
    elif isinstance(value, str):
        m = _ISO_DATE.match(value)
        if m:
            try:
                keys += _date_keys(datetime(int(m[1]), int(m[2]), int(m[3])))
            except ValueError:
                pass
        if value.startswith(("http://", "https://")):
            last = value.rstrip("/").rsplit("/", 1)[-1]
            if len(last) >= 4 and re.fullmatch(r"[\w.\-]+", last):
                keys.append(tuple(words(last)))
    return [k for k in keys if k]


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


def analyse(trace: Trace, normalize: bool = False) -> list[CallUsage]:
    """`normalize`: also look for transformed forms of each value (dates,
    amounts in minor units, short ids from URLs). Off by default in 0.1."""
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
            if normalize:
                k = k + transformed_keys(value, path)
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
