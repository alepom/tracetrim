"""One engine for every integration: observe a call, answer `expand`, close a run.

The MCP proxy and `AutoTrim` are thin adapters over this module: they turn
their own events into the contract of `tracetrim.contract` and call the
engine, so that the same calls, final answers and configuration lead to the
same decisions whichever way the agent reaches its tools.

Concurrency: one engine holds ONE run at a time. Calls that reach it between
two closings belong to the same run, whoever sent them: an HTTP proxy shared
by several agents working at once mixes their calls into one run. Give each
agent its own proxy (or its own `AutoTrim`) and work directory.
"""
from __future__ import annotations

import html
import json
import re
import secrets
from dataclasses import replace
from pathlib import Path

from .contract import FILTERED, FULL, FULL_CHEAPER, SAMPLED, Observation, Sizes, ToolCall, ToolResult
from .evidence import is_broad
from .projection import dumps
from .runtime import Policy, check_preview
from .usage import leaves, parse_output

_EXPAND_V1 = ("Some tool results are trimmed to save context: the fields removed are listed in the line "
              "'[trimmed] call_id=... hidden: ...' after the result. Call expand with that call_id and "
              "the fields or groups you need, to get their values from the original result.")
# Since 1.4 (skill version 2, protocol-v3c.md): the check before declaring information absent.
_EXPAND_V2 = _EXPAND_V1 + (" Before you state that information is missing or unknown, check whether a hidden field "
                           "could hold it (a description, note, memo or reason) and expand it first.")
EXPAND = {
    "name": "expand",
    "description": _EXPAND_V2,
    "inputSchema": {"type": "object", "required": ["call_id", "fields"], "properties": {
        "call_id": {"type": "string"},
        "fields": {"type": "array", "items": {"type": "string"}}}},
}


def expand_tool(version: str | None = None) -> dict:
    """The `expand` tool as listed with skill version `version` (default: the current one): "1" is 1.3's text."""
    from . import skill
    version = version or skill.VERSION
    if version not in skill.VERSIONS:
        raise ValueError(f"skill version {version!r}: one of {', '.join(skill.VERSIONS)}")
    return dict(EXPAND, description=_EXPAND_V1 if version == "1" else _EXPAND_V2)
MAX_GROUPS = 25
MAX_ORIGINALS = 256          # originals kept for expand; the oldest go first
MARK = "\n[trimmed] "        # starts the line that follows a filtered result
RECENT_IDS = 3               # valid call_ids named in the reply to an unknown one
# Text previews (1.5, on by default: runtime.TEXT_PREVIEW). A hidden field is free text when one of its values is a string with a space
# and at least TEXT_MIN characters that is not a URL: a description, a memo, a note. Ids, codes, dates, emails and
# names of one or two short words are not. Per field, the first TEXT_VALUES distinct texts; at most TEXT_FIELDS
# fields per result, so that a result with many hidden texts cannot make the line longer than the cut saves.
TEXT_MIN = 12
TEXT_VALUES = 3
TEXT_FIELDS = 5
# Separates the previews from the list of hidden groups, on the same line, and says what they are: the start of a
# text, cut at a word (with "…" when there is more), never a value to quote whole. The rest comes with expand.
TEXT_LABEL = " · text, first characters: "
# A preview shows readable text: markup (HTML tags and entities, markdown syntax, link and image targets) is removed
# and whitespace collapsed before the cut, wherever it appears, so a scraped page shows its words and not
# «<!DOCTYPE html>» or «## Menu\n\n». A text longer than TEXT_LONG readable characters is a document (a page, a
# transcript): its first words rarely say what it is about (a cookie banner, a menu, a greeting), so the preview gives
# its size instead. On the real traces of the studies, descriptions, snippets and notes stay under it (95th
# percentile 356 characters); extracted pages and transcripts go over it (median about 2,000).
TEXT_LONG = 500
# The previews may spend at most this share of what the cut saves on a result (in tokens): they never decide whether
# a result goes on whole, and a small result with many hidden texts keeps at least a quarter of its saving. In
# protocol v3e they used at most 0.55 of it (median 0.30: experiments/v3e/replay.py), so the bound leaves the tested
# previews as they were.
TEXT_SHARE = 0.75
# Every pattern below runs in linear time on any input (no class that can scan past the next opening bracket), and
# HTML is recognised only by a closing tag, a comment, a doctype or a void tag: «importo<soglia e data>scadenza» is text.
_HTML_SIGN = re.compile(r"</[A-Za-z]|<!--|<!doctype|<br\b|<[A-Za-z][^<>]*/>", re.I)
_HTML_BLOCKS = (("<!--", "-->"), ("<script", "</script"), ("<style", "</style"))
_HTML_TAG = re.compile(r"<[!/?]?[A-Za-z][^<>]*>")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")   # keeps the length
_MD_IMAGE = re.compile(r"!\[[^\[\]]*\]\([^()]*\)")
_MD_LINK = re.compile(r"\[([^\[\]]*)\]\([^()]*\)")
# Heading marks, and bullets unless a number or a currency follows, after any spaces: «- 30,00 EUR storno» and
# «-  €30» keep their sign. Numbered lists and quotes keep their marks («100. Totale», «> 5 licenze»).
_MD_LINE = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|[-*+](?![ \t]*[-+]?[\d.,€$£])[ \t]+)", re.M)
# Emphasis only in pairs that open and close around words («**SPA**», «*Sauna*»), each at most 200 characters, so a
# lone mark stays («r**2», «2**10», «5 * 3») and the cost stays linear. Underscores are left alone (snake_case,
# __init__). Backticks of inline code go.
_MD_STRONG = re.compile(r"(?<![\w*])\*\*(?=[^\s*])([^*\n]{1,200}?)(?<=[^\s*])\*\*(?![\w*])")
_MD_EM = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]{1,200}?)(?<=[^\s*])\*(?![\w*])")
_MD_CODE = re.compile(r"`+")
# Entities only when written whole, with their semicolon: «&amp;», «&#8364;»; «&copy2026» and «a=1&not=2» stay.
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
_SPACE = re.compile(r"\s+")
_BEFORE_MARK = re.compile(r" ([,.;:!?)])(?=\s|$)")         # «urgente</b>, vedi» reads «urgente, vedi»; «.5» stays
# A field name on the line is written as it is, but a control character, a line separator or a lone surrogate would
# let a key open a line of its own (a forged «[trimmed]») or make the text impossible to encode: written escaped.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029\ud800-\udfff]")
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_INDEX = re.compile(r"\[(\d+)\]")
_NOTHING = object()


def requested_fields(value) -> list[str]:
    """The `fields` of an `expand` call as a list of names: a model may send one
    name as a plain string, which must not be read as a list of characters."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(f) for f in value]
    return []


def generic(path: str) -> str:
    """`data[0].x` -> `data[].x`: the path of a field, whatever the element."""
    return _INDEX.sub("[]", path)


def _matches(wanted: str) -> re.Pattern:
    """A requested path as a pattern on the positions of a result: `[]` is any
    element, `[2]` the third one; a path also stands for everything under it."""
    return re.compile(re.escape(wanted).replace(r"\[\]", r"\[\d+\]") + r"(?:$|\.|\[)")


def _elements(wanted: str) -> list[re.Pattern]:
    """The list elements a requested path addresses: `data[].lines[1].sku`
    addresses every element of `data` and the second of each `lines`."""
    out, i = [], 0
    while (m := re.compile(r"\[(\d*)\]").search(wanted, i)) is not None:
        prefix = wanted[:m.end()]
        out.append(re.compile(re.escape(prefix).replace(r"\[\]", r"\[\d+\]") + "$"))
        i = m.end()
    return out


def _pick(node, wanted: list[re.Pattern], found: set, at: str = "", addressed: list[re.Pattern] = ()):
    """What a result holds at the requested positions, in the original order.

    An element of a list that a request addresses keeps its place even when it
    lacks the field (an empty object, or null for a value), so that a value is
    never read as another record's; elements no request addresses are left out.
    A list in which nothing requested was found is left out whole."""
    if isinstance(node, dict):
        out = {k: r for k, v in node.items()
               if (r := _pick(v, wanted, found, f"{at}.{k}" if at else k, addressed)) is not _NOTHING}
        return out or _NOTHING
    if isinstance(node, list):
        picked = [(i, v, _pick(v, wanted, found, f"{at}[{i}]", addressed)) for i, v in enumerate(node)]
        if all(r is _NOTHING for _, _, r in picked):
            return _NOTHING
        return [({} if isinstance(v, dict) else None) if r is _NOTHING else r for i, v, r in picked
                if r is not _NOTHING or any(a.match(f"{at}[{i}]") for a in addressed)]
    if any(w.match(at) for w in wanted):
        found.add(generic(at))
        return node
    return _NOTHING


def is_text(value) -> bool:
    """A free-text value, for the previews: see TEXT_MIN."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    return len(v) >= TEXT_MIN and " " in v and not v.startswith(("http://", "https://"))


def cut(text: str, chars: int) -> str:
    """The start of a text for a preview: at most `chars` characters, cut after the last whole word that fits, so
    that a number is not split («14,7…» would read as a value), and «…» when something is left out. Only a first word
    longer than `chars` is cut inside."""
    if len(text) <= chars:
        return text
    head = text[:chars]
    if text[chars] != " ":
        space = head.rfind(" ")
        if space > 0:
            head = head[:space]
    return head.rstrip(" ,;:.") + "…"


def safe(name: str) -> str:
    """A field name as it can be written on the [trimmed] line (see _UNSAFE)."""
    return _UNSAFE.sub(lambda m: f"\\u{ord(m.group()):04x}", name)


def _opening(low: str, marker: str, start: int) -> int:
    """The next `marker` from `start` that opens a block: «<script» and «<style» only as whole tag names, followed
    by a space or «>» («<scripture>», «<stylesheet.css>», a self-closed «<script/>» are not blocks)."""
    while True:
        j = low.find(marker, start)
        if j < 0 or marker == "<!--":
            return j
        after = low[j + len(marker): j + len(marker) + 1]
        if after in (" ", "\t", "\n", "\r", "\f", ">"):
            return j
        start = j + 1


def _drop_blocks(text: str) -> str:
    """Comments, scripts and styles removed with their content, in one pass; an unclosed one runs to the end."""
    low = text.translate(_ASCII_LOWER)
    nxt = {o: _opening(low, o, 0) for o, _ in _HTML_BLOCKS}
    out, i = [], 0
    while True:
        live = [(p, o) for o, p in nxt.items() if p >= 0]
        if not live:
            out.append(text[i:])
            break
        j, o = min(live)
        out.append(text[i:j] + " ")
        close = dict(_HTML_BLOCKS)[o]
        k = low.find(close, j + len(o))
        if k < 0:
            break
        i = k + len(close) if close == "-->" else low.find(">", k) + 1
        if i <= 0:
            break
        for m, p in nxt.items():
            if 0 <= p < i:
                nxt[m] = _opening(low, m, i)
    return "".join(out)


def readable(text: str) -> str:
    """The words of a text, for a preview: HTML (scripts, styles and comments with their content, then tags and
    entities) and markdown marks removed, links reduced to their text, images dropped, whitespace collapsed."""
    if "<" in text and _HTML_SIGN.search(text):
        text = _HTML_TAG.sub(" ", _drop_blocks(text))
    if "&" in text:
        text = _ENTITY.sub(lambda e: html.unescape(e.group()), text)
    text = _MD_LINK.sub(r"\1", _MD_IMAGE.sub(" ", text))
    text = _MD_CODE.sub("", _MD_EM.sub(r"\1", _MD_STRONG.sub(r"\1", _MD_LINE.sub("", text))))
    return _BEFORE_MARK.sub(r"\1", _SPACE.sub(" ", text)).strip()


def previews(payload, hidden: list[str], chars: int, budget: int | None = None, count=len) -> str:
    """The text that follows the hidden groups when previews are on: for each hidden free-text field, its first
    distinct starts (`cut` of the `readable` text), as JSON strings (a newline or a quote cannot break the line), and
    how many more; texts longer than TEXT_LONG are documents, counted with the size of the longest instead. With a
    `budget` (in the unit of `count`), as many whole fields as fit, in order, and how many were left out."""
    if chars <= 0:
        return ""
    wanted = set(hidden)
    values: dict[str, dict[str, None]] = {}      # an ordered set per field: one pass, linear in the payload
    long: dict[str, list[int]] = {}              # per field: how many documents, and the longest (characters)
    for path, value in leaves(payload):
        if path in wanted and is_text(value):
            # beyond 8 × TEXT_LONG raw characters a value is a document whatever its markup: no cleaning to pay for
            words = "" if len(value) > TEXT_LONG * 8 else readable(value)
            if len(value) > TEXT_LONG * 8 or len(words) > TEXT_LONG:
                n = long.setdefault(path, [0, 0])
                n[0] += 1
                n[1] = max(n[1], len(value))
            elif len(words) >= TEXT_MIN:
                values.setdefault(path, {})[cut(words, chars)] = None
    parts = []
    for path in sorted(values.keys() | long.keys())[:TEXT_FIELDS]:
        texts = list(values.get(path, ()))
        shown = [json.dumps(_SURROGATE.sub("\ufffd", t), ensure_ascii=False) for t in texts[:TEXT_VALUES]]
        if len(texts) > TEXT_VALUES:
            shown[-1] += f" (+{len(texts) - TEXT_VALUES})"
        if path in long:
            n, size = long[path]
            shown.append(f"1 long text ({size} characters)" if n == 1 else
                         f"{n} long texts (up to {size} characters)")
        parts.append(f"{safe(path)} {', '.join(shown)}")
    fields = len(values.keys() | long.keys())

    def line(n: int) -> str:
        more = [f"+{fields - n} more fields"] if fields > n else []
        return TEXT_LABEL + "; ".join(parts[:n] + more) if n else ""
    n = len(parts)
    while budget is not None and n and count(line(n)) > budget:
        n -= 1
    return line(n)


class Trimmer:
    """Applies a policy to tool outputs and keeps the originals for `expand`."""

    def __init__(self, policy: Policy | None):
        self.policy = policy
        self.originals: dict[str, object] = {}
        self.hidden: dict[str, set] = {}
        self.tool_of: dict[str, str] = {}
        self._count = None
        # Shared by several clients (the HTTP proxy): the reply to an unknown
        # call_id must not name valid ones, or a client could expand another's
        # results. With one agent (stdio, function calling) naming them helps.
        self.shared = False
        self.on_forget = None                    # optional cleanup of HTTP ownership metadata
        # (tool, output, tokens of the output written whole) of the last result that
        # was compared with its whole form: the engine's breakdown reuses the count.
        self.last_whole: tuple[str, str, int] | None = None

    def count(self, text: str) -> int:
        """Tokens (o200k_base), or characters when tiktoken is not installed."""
        if self._count is None:
            from .measure import counter
            self._count, self.unit = counter()
        return self._count(text)

    def output(self, tool: str, out: str) -> str:
        return self.observe(tool, out).text

    def observe(self, tool: str, out: str, *, count_tokens=None) -> Observation:
        if self.policy is None:
            return Observation(out)
        trimmed = self.policy.apply(tool, out)
        proj = self.policy.projection
        payload = parse_output(out)
        if proj is None or tool not in proj.keep or not isinstance(payload, (dict, list)):
            return Observation(trimmed)
        if self.policy.tools.get(tool, {}).get("trim", "off") == "off":
            # The projection knows the tool but the config does not trim it (switched
            # off after too many expand requests): nothing is removed, so the result
            # is neither filtered, nor a candidate for sampling, nor "cheaper whole".
            return Observation(trimmed)
        # What the projection removes from this result: the fields seen while learning
        # and not kept; with `unseen="drop"`, also every field it never saw.
        if proj.unseen == "keep":
            drop = proj.seen.get(tool, set()) - proj.keep[tool]
            hidden = sorted({p for p, _ in leaves(payload) if p in drop})
        else:
            hidden = sorted({p for p, _ in leaves(payload) if p not in proj.keep[tool]})
        if not hidden:
            return Observation(trimmed)
        annotate = self.policy.expand_enabled(tool) or self.policy.config.get("expand_all")
        groups: dict[str, int] = {}
        for h in hidden:
            g = ".".join(h.split(".")[:2])
            groups[g] = groups.get(g, 0) + 1
        ordered = sorted(groups.items(), key=lambda x: (-x[1], x[0]))
        items = [safe(g) if n == 1 and g in hidden else f"{safe(g)} ({n} field{'' if n == 1 else 's'})" for g, n in ordered]
        listed = ", ".join(items[:MAX_GROUPS]) + (f" (+{len(items) - MAX_GROUPS} more groups)" if len(items) > MAX_GROUPS else "")
        # The simple way for small outputs: the line that says what was removed
        # has a fixed cost, and on a small or already compact result it can cost
        # more than the cut saves. Then the whole result goes on, without the line:
        # fewer tokens and nothing lost. (Counted with a fixed id in place of the
        # random one, so that the same result is treated the same everywhere.)
        whole = self.policy.apply(tool, out, project=False)
        candidate = f"{trimmed}{MARK}call_id=c00000000 hidden: {listed}" if annotate else trimmed
        count = count_tokens or self.count
        whole_size = count(whole)
        self.last_whole = (tool, out, whole_size)
        candidate_size = count(candidate)
        if candidate_size >= whole_size:
            return Observation(whole, FULL_CHEAPER, hidden=tuple(hidden))
        if not annotate:
            return Observation(trimmed, FILTERED, hidden=tuple(hidden))
        # The previews come after the choice, within a share of what the cut saves: the result goes on whole exactly
        # when it did without them (1.4), and keeps at least a quarter of its saving.
        listed += previews(payload, hidden, getattr(self.policy, "text_preview", 0),
                           budget=int((whole_size - candidate_size) * TEXT_SHARE), count=count)
        # Random ids: over HTTP several clients share one proxy, and a sequential
        # id would let one of them expand another's results.
        cid = "c" + secrets.token_hex(4)
        while self.on_forget is not None and cid in self.originals:
            cid = "c" + secrets.token_hex(4)
        while len(self.originals) >= MAX_ORIGINALS:
            self.forget(next(iter(self.originals)))
        self.originals[cid] = payload
        self.tool_of[cid] = tool
        self.hidden[cid] = set(hidden)
        return Observation(f"{trimmed}{MARK}call_id={cid} hidden: {listed}", FILTERED, cid, tuple(hidden))

    def expand(self, call_id: str, wanted: list[str]) -> str:
        return self.recover(call_id, wanted)[0]

    def forget(self, call_id: str) -> None:
        for kept in (self.originals, self.tool_of, self.hidden):
            kept.pop(call_id, None)
        if self.on_forget is not None:
            self.on_forget(call_id)

    def recover(self, call_id: str, wanted: list[str]) -> tuple[str, tuple[str, ...]]:
        """The reply to an `expand` request, and the paths it gave back.

        A path may name one element of a list by its position, from zero:
        `data[0].x` is `data[].x` for the first element of `data` only. The
        reply holds the requested elements in their original order, and an
        element that lacks the field keeps its place (an empty object), so that
        no value can be read as another record's. For what is learned from the
        request, an index counts as `[]`: asking for a field of one element is
        asking for that field."""
        payload = self.originals.get(call_id)
        if payload is None:
            return self._unknown(call_id), ()
        hidden = self.hidden.get(call_id, set())
        requested = sorted(hidden) if "*" in wanted else wanted
        fields: set[str] = set()
        got = _pick(payload, [_matches(w) for w in requested], fields,
                    addressed=[a for w in requested for a in _elements(w)])
        if not fields:
            groups = sorted({safe(".".join(h.split(".")[:2])) for h in hidden})
            return ("Error: no field matches " + ", ".join(wanted)[:200] + ". Hidden groups: "
                    + ", ".join(groups[:MAX_GROUPS]) + ('; or "*" for all of them.' if groups else ".")), ()
        compact = self.policy is not None and self.policy.compact
        return dumps(got if isinstance(got, (dict, list)) else {}, compact), tuple(sorted(fields))

    def _unknown(self, call_id: str) -> str:
        """The model made up or cut short an id, or asked about a result that was
        not trimmed: say which ids exist and what `expand` is for."""
        recent = list(self.originals)[-RECENT_IDS:][::-1]
        if self.shared:
            valid = "Copy the call_id exactly as that line writes it."
        elif recent:
            valid = "Most recent valid call_ids: " + ", ".join(f"{c} ({self.tool_of.get(c, '?')})" for c in recent) + "."
        else:
            valid = "No result has been trimmed so far."
        return (f"Error: unknown call_id {str(call_id)[:40]!r}. expand only works on a result that ends with a line "
                f"'[trimmed] call_id=...', with the call_id written there; a result without that line is complete "
                f"as it is, and expand cannot give more rows of it. {valid}")


class Engine:
    """`learner`: automatic mode (tracetrim.auto.Learner), or None to apply a
    fixed `policy` (or nothing). `structural`: re-encode every output after
    the projection ("toon", "headroom"), whatever the config says. `compact`:
    JSON without spaces after separators, whatever the config says; None (the
    default) turns it on in automatic mode, from the first call, and otherwise
    leaves it to the config. `shared`:
    several clients reach this engine (the HTTP proxy), so an error never names
    the call_ids of results it holds. `breakdown`: count every call three times
    (`contract.Sizes`), to tell what re-serialising saves from what removing
    fields saves; a measure, which changes neither the traces nor what the model
    receives. `keep_positions`: whether a list element left with no field keeps
    its place, whatever the config says; None leaves it to the config, where it
    is on unless `"keep_positions": false` (see `Policy`)."""

    def __init__(self, learner=None, policy: Policy | None = None, structural: str | None = None,
                 shared: bool = False, compact: bool | None = None, breakdown: bool = True,
                 keep_positions: bool | None = None, text_preview: int | None = None):
        self.learner = learner
        self.keep_positions = keep_positions
        self.text_preview = None if text_preview is None else check_preview(text_preview)
        self.structural = structural
        self.breakdown = breakdown
        # Lossless, so in automatic mode it does not wait for anything to be learned.
        self.compact = (True if learner is not None else None) if compact is None else compact
        if policy is None and learner is not None:
            policy = learner.initial_policy()
        if policy is None and (structural or self.compact):
            # Nothing learned or configured yet: no tool is trimmed, outputs are only re-encoded.
            policy = Policy({"format": "tracetrim/config@1", "tools": {}}, None)
        self.trimmer = Trimmer(self._adjusted(policy))
        self.trimmer.shared = shared
        self._expanded: set[str] = set()         # results already expanded once: a second request is a repeat

    def _adjusted(self, policy: Policy | None) -> Policy | None:
        """The engine's own settings over the config's."""
        if policy is not None and self.structural:
            policy.structural = self.structural
        if policy is not None and self.compact is not None:
            policy.compact = self.compact
        if policy is not None and self.keep_positions is not None:
            policy.keep_positions = self.keep_positions
        if policy is not None and self.text_preview is not None:
            policy.text_preview = self.text_preview
        return policy

    @property
    def policy(self) -> Policy | None:
        return self.trimmer.policy

    @property
    def trimming(self) -> bool:
        return self.policy is not None and bool(self.policy.tools)

    # -- one call ----------------------------------------------------------
    def observe(self, call: ToolCall, result: ToolResult) -> Observation:
        """Record what the tool sent; return what the model should receive.
        The tool has already run, once: only what is passed on can differ."""
        self.trimmer.last_whole = None
        obs = self._observe(call, result)
        if not self.breakdown:
            return obs
        sizes = self.sizes(call.tool, result, obs)
        if self.learner is not None:
            self.learner.measured(call.tool, sizes)
        return replace(obs, sizes=sizes)

    def sizes(self, tool: str, result: ToolResult, obs: Observation) -> Sizes:
        """The three counts of a call. The text is tokenised as few times as it
        takes: a count the trimmer already made is reused, and two texts that
        are the same are counted once."""
        count, raw, policy = self.trimmer.count, result.output, self.policy
        r = count(raw)
        if result.verbatim or policy is None or obs.text == raw:
            return Sizes(r, r, r)                # passed on as received
        known = self.trimmer.last_whole
        w = known[2] if known is not None and known[0] == tool and known[1] is raw else None
        if obs.exposure in (FULL_CHEAPER, SAMPLED):
            # Passed whole on purpose: the text is the whole output, re-written.
            s = count(obs.text) if w is None else w
            return Sizes(r, s, s)
        s = count(obs.text)
        if obs.exposure == FULL and (policy.projection is None
                                     or policy.tools.get(tool, {}).get("trim", "off") == "off"):
            return Sizes(r, s, s)                # a tool that is not trimmed: only re-written
        if w is None:
            whole = policy.apply(tool, raw, project=False)
            w = s if whole == obs.text else count(whole)
        return Sizes(r, w, s)

    def _observe(self, call: ToolCall, result: ToolResult) -> Observation:
        if self.learner is None:
            return Observation(result.output) if result.verbatim else self.trimmer.observe(call.tool, result.output)
        index = self.learner.call(call.tool, call.arguments, result.output, call.turn, session_key=call.session_key)
        if result.verbatim:
            return Observation(result.output)
        policy = self.policy                     # the one this result is filtered with (Learner.observed)
        obs = self.trimmer.observe(call.tool, result.output)
        if obs.exposure == FILTERED and self.learner.adaptive.sample_rate > 0:
            # Sampling: now and then a result that would be filtered goes on
            # whole, so that the model can use what is normally removed.
            whole = self.policy.apply(call.tool, result.output, project=False)
            # Counted with a fixed id in place of the random one, whose tokens vary:
            # the same call must weigh the same in every process.
            filtered = obs.text.replace(obs.call_id, "c00000000") if obs.call_id else obs.text
            if self.learner.sample(call.tool, self.learner.count(whole) - self.learner.count(filtered)):
                if obs.call_id:
                    self.trimmer.forget(obs.call_id)
                obs = Observation(whole, SAMPLED, None, obs.hidden)
        self.learner.observed(index, call, obs, policy)
        return obs

    def unknown_expand(self, call_id: str) -> str:
        text = self.trimmer._unknown(call_id)
        if self.breakdown and self.learner is not None:
            self.learner.expand_measured(self.trimmer.count(text))
        return text

    def expand(self, call_id: str, fields: list[str]) -> str:
        """Answer an `expand` request from the stored original: the tool is not called again."""
        tool = self.trimmer.tool_of.get(call_id)
        text, returned = self.trimmer.recover(call_id, fields)
        if tool is not None and self.learner is not None:
            hidden = self.trimmer.hidden.get(call_id, set())
            fields = [generic(w) for w in fields]          # an index names an element, not another field
            named = [w for w in fields if not is_broad(w, hidden)]
            recovered = sorted(set(returned) & hidden)
            self.learner.expand(
                call_id, tool, requested=list(fields), returned=list(returned), recovered=recovered,
                # Asked for by name or by group; what came back only with "everything" is not.
                targeted=[p for p in recovered
                          if any(p == w or p.startswith(w + ".") or p.startswith(w + "[]") for w in named)],
                broad=len(named) < len(fields),
                # A result counts as expanded once, and only when something the
                # projection had removed came back: a request that matches
                # nothing, or only fields that were there, corrects no cut.
                first=bool(recovered) and call_id not in self._expanded)
            if recovered:
                self._expanded.add(call_id)
                self._expanded &= self.trimmer.originals.keys()
        if self.breakdown and self.learner is not None:
            # Apart from the three counts of a call: what `expand` gives back is added
            # to what the model reads, and is never part of a result's saving.
            self.learner.expand_measured(self.trimmer.count(text))
        return text

    # -- one run -----------------------------------------------------------
    def done(self, answer: str | None) -> Path | None:
        """Close the run with the final answer (None or blank: telemetry only).

        Returns the trace written, or None when there was nothing to close: no
        call since the last closing. That makes closing idempotent: a repeated
        or retried `done` finds nothing pending and changes nothing. The answer
        is stored as the run's last message and is never treated as a tool
        output: it is not trimmed, not stored for `expand`, and closing does
        not call any tool.
        """
        return self.learner.done(answer) if self.learner is not None else None

    def refuse(self) -> None:
        """Count a closing the adapter refused because it carried no answer."""
        if self.learner is not None:
            self.learner.refused()

    def relearn(self) -> bool:
        """Regenerate the report from the runs on disk; reload the policy when
        one applies. True when a policy was (re)loaded. Slow: an async adapter
        runs it in a thread."""
        return self.install(self.learn_policy())

    def learn_policy(self) -> Policy | None:
        """The relearning, without installing its result: an async adapter runs this in a thread and installs the
        policy on its own loop (`install`), so that no call is trimmed half with the old policy, half with the new."""
        new = self.learner.relearn() if self.learner is not None else None
        if new is None and self.learner is not None and self.learner.last_ready is False and self.trimming:
            # The config just written trims nothing (fewer complete runs than needed: a trace no longer counted as
            # complete): the policy in place stops trimming too (REVISIONE-14b-septies: it went on filtering).
            new = Policy({"format": "tracetrim/config@1", "tools": {}}, None)
        return self._adjusted(new) if new is not None else None

    def install(self, policy: Policy | None) -> bool:
        """Put a policy from `learn_policy` in place. True when there was one."""
        if policy is not None:
            self.trimmer.policy = policy
        return policy is not None

    def close(self, answer: str | None) -> Path | None:
        path = self.done(answer)
        # Also when there was nothing to close but a relearning is owed (a retry after a failed one, or after a
        # publication another call finished): the closing is not confirmed with the policy of before.
        if path is not None or (self.learner is not None and self.learner.relearn_owed):
            self.relearn()
        return path
