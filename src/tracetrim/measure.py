"""How much a learned projection trims, and how often it is wrong.

Leave-one-out: for each trace, the projection is learned from the OTHER
traces and applied to this one, which is the situation of a proxy meeting a
new run. Two numbers per trace:

  trimmed  tokens of tool output after projection, counted once
           (not multiplied by turns: the real bill depends on when the
           output enters the conversation and on caching)
  misses   calls where the aggressive projection hides a field this trace
           then uses. With an `expand` tool they cost one extra turn each;
           without it they are lost information.

Misses are always counted against FULL usage, whatever the learning source:
they are what the agent would have looked for and not found.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .projection import FoldTraining, learn as learn  # keep the historical module-level import
from .usage import CallUsage


def tokenizer(*, ordinary_text=False):
    """Use ordinary BPE for tool text that may literally quote a special-token marker."""
    try:
        import tiktoken
    except ImportError:
        raise SystemExit("measuring needs a real tokenizer: pip install 'tracetrim[measure]'")
    enc = tiktoken.get_encoding("o200k_base")
    encode = enc.encode_ordinary if ordinary_text else enc.encode
    return lambda s: len(encode(s or ""))


def counter():
    """(count, unit) for decisions taken at run time: tokens (o200k_base) when
    tiktoken is installed, characters otherwise. Only ever used to compare
    two texts counted the same way; a report that shows such counts names the unit."""
    try:
        import tiktoken
    except ImportError:
        return (lambda s: len(s or "")), "characters"
    enc = tiktoken.get_encoding("o200k_base")
    return (lambda s: len(enc.encode(s or ""))), "tokens"


@dataclass
class Row:
    name: str
    raw: int = 0
    prudent: int = 0
    aggressive: int = 0
    trimmed_calls: int = 0
    misses: int = 0
    misses_text: int = 0
    misses_prudent: int = 0
    per_tool: dict = field(default_factory=lambda: defaultdict(lambda: [0, 0, 0, 0, 0, 0]))
    # The used fields the aggressive projection removed, by tool and path: the misses by name, collected while the
    # projection learned without this run is at hand (since 1.1.5 the report reads them here, not learning again).
    missed_fields: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))


def removes(p, tool: str, path: str) -> bool:
    """Does projection `p` actually remove this field? A miss is a used field
    that is removed; with unseen="keep", a field never seen is not."""
    if path in p.keep[tool]:
        return False
    return p.unseen == "drop" or path in p.seen[tool]


def folds(usages):
    """Yield the held-out run and its training runs, in input order."""
    for i, calls in enumerate(usages):
        yield calls, usages[:i] + usages[i + 1:]


def leave_one_out(names: list[str], usages: list[list[CallUsage]], source="full",
                  unseen="keep", protect_ids: bool | str = True, compact: bool = False,
                  keep_empty: bool = False, keep_positions: bool = False, *, training=None) -> list[Row]:
    """`compact`, `keep_empty`, `keep_positions`: apply the projections as run time does (since 1.1, from the
    report of automatic mode and of `tracetrim report`; positions since 1.1.2); the studies measured without
    them, and so does the default."""
    count = tokenizer()
    rows = []
    if training is None:
        training = FoldTraining(usages, source, unseen, protect_ids)
    for i, (name, calls) in enumerate(zip(names, usages)):
        prudent = training.project("prudent", exclude=i)
        aggressive = training.project("aggressive", exclude=i)
        r = Row(name)
        for c in calls:
            raw = count(c.output)
            pr = count(prudent.apply(c.tool, c.output, keep_empty=keep_empty, compact=compact,
                                     keep_positions=keep_positions))
            ag = count(aggressive.apply(c.tool, c.output, keep_empty=keep_empty, compact=compact,
                                        keep_positions=keep_positions))
            trimmed = c.structured and c.tool in aggressive.keep
            missed = {f for f in c.used if removes(aggressive, c.tool, f)} if trimmed else set()
            for f in missed:
                r.missed_fields[c.tool][f] += 1
            r.raw += raw
            r.prudent += pr
            r.aggressive += ag
            r.trimmed_calls += trimmed
            r.misses += bool(missed)
            r.misses_text += bool(missed & c.used_as_text)
            r.misses_prudent += trimmed and any(removes(prudent, c.tool, f) for f in c.used)
            t = r.per_tool[c.tool]
            for k, v in enumerate((1, raw, pr, ag, int(trimmed), int(bool(missed)))):
                t[k] += v
        rows.append(r)
    return rows
