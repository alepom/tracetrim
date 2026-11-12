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

import json
import re
import secrets
from pathlib import Path

from .contract import FILTERED, FULL_CHEAPER, SAMPLED, Observation, ToolCall, ToolResult
from .evidence import is_broad
from .projection import dumps
from .runtime import Policy
from .usage import leaves, parse_output

EXPAND = {
    "name": "expand",
    "description": ("Some tool results are trimmed to save context: the fields removed are listed in the line "
                    "'[trimmed] call_id=... hidden: ...' after the result. Call expand with that call_id and "
                    "the fields or groups you need, to get their values from the original result."),
    "inputSchema": {"type": "object", "required": ["call_id", "fields"], "properties": {
        "call_id": {"type": "string"},
        "fields": {"type": "array", "items": {"type": "string"}}}},
}
MAX_GROUPS = 25
MAX_ORIGINALS = 256          # originals kept for expand; the oldest go first
MARK = "\n[trimmed] "        # starts the line that follows a filtered result
RECENT_IDS = 3               # valid call_ids named in the reply to an unknown one
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

    def count(self, text: str) -> int:
        """Tokens (o200k_base), or characters when tiktoken is not installed."""
        if self._count is None:
            from .measure import counter
            self._count, self.unit = counter()
        return self._count(text)

    def output(self, tool: str, out: str) -> str:
        return self.observe(tool, out).text

    def observe(self, tool: str, out: str) -> Observation:
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
        items = [g if n == 1 and g in hidden else f"{g} ({n} field{'' if n == 1 else 's'})" for g, n in ordered]
        listed = ", ".join(items[:MAX_GROUPS]) + (f" (+{len(items) - MAX_GROUPS} more groups)" if len(items) > MAX_GROUPS else "")
        # The simple way for small outputs: the line that says what was removed
        # has a fixed cost, and on a small or already compact result it can cost
        # more than the cut saves. Then the whole result goes on, without the line:
        # fewer tokens and nothing lost. (Counted with a fixed id in place of the
        # random one, so that the same result is treated the same everywhere.)
        whole = self.policy.apply(tool, out, project=False)
        candidate = f"{trimmed}{MARK}call_id=c00000000 hidden: {listed}" if annotate else trimmed
        if self.count(candidate) >= self.count(whole):
            return Observation(whole, FULL_CHEAPER, hidden=tuple(hidden))
        if not annotate:
            return Observation(trimmed, FILTERED, hidden=tuple(hidden))
        # Random ids: over HTTP several clients share one proxy, and a sequential
        # id would let one of them expand another's results.
        cid = "c" + secrets.token_hex(4)
        while len(self.originals) >= MAX_ORIGINALS:
            old = next(iter(self.originals))
            self.originals.pop(old)
            self.tool_of.pop(old, None)
            self.hidden.pop(old, None)
        self.originals[cid] = payload
        self.tool_of[cid] = tool
        self.hidden[cid] = set(hidden)
        return Observation(f"{trimmed}{MARK}call_id={cid} hidden: {listed}", FILTERED, cid, tuple(hidden))

    def expand(self, call_id: str, wanted: list[str]) -> str:
        return self.recover(call_id, wanted)[0]

    def forget(self, call_id: str) -> None:
        for kept in (self.originals, self.tool_of, self.hidden):
            kept.pop(call_id, None)

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
            groups = sorted({".".join(h.split(".")[:2]) for h in hidden})
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
    the call_ids of results it holds."""

    def __init__(self, learner=None, policy: Policy | None = None, structural: str | None = None,
                 shared: bool = False, compact: bool | None = None):
        self.learner = learner
        self.structural = structural
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
        if self.learner is None:
            return Observation(result.output) if result.verbatim else self.trimmer.observe(call.tool, result.output)
        index = self.learner.call(call.tool, call.arguments, result.output, call.turn)
        if result.verbatim:
            return Observation(result.output)
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
        self.learner.observed(index, call, obs)
        return obs

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
        new = self.learner.relearn() if self.learner is not None else None
        if new is not None:
            self.trimmer.policy = self._adjusted(new)
        return new is not None

    def close(self, answer: str | None) -> Path | None:
        path = self.done(answer)
        if path is not None:
            self.relearn()
        return path
