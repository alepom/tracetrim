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
import secrets
from pathlib import Path

from .contract import FILTERED, FULL, RECOVERED, Evidence, Observation, ToolCall, ToolResult
from .projection import project
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


class Trimmer:
    """Applies a policy to tool outputs and keeps the originals for `expand`."""

    def __init__(self, policy: Policy | None):
        self.policy = policy
        self.originals: dict[str, object] = {}
        self.hidden: dict[str, set] = {}
        self.tool_of: dict[str, str] = {}

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
        drop = proj.seen.get(tool, set()) - proj.keep[tool]
        hidden = sorted({p for p, _ in leaves(payload) if p in drop})
        if not hidden:
            return Observation(trimmed)
        if not self.policy.expand_enabled(tool) and not self.policy.config.get("expand_all"):
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
        groups: dict[str, int] = {}
        for h in hidden:
            g = ".".join(h.split(".")[:2])
            groups[g] = groups.get(g, 0) + 1
        ordered = sorted(groups.items(), key=lambda x: (-x[1], x[0]))
        items = [g if n == 1 and g in hidden else f"{g} ({n} field{'' if n == 1 else 's'})" for g, n in ordered]
        listed = ", ".join(items[:MAX_GROUPS]) + (f" (+{len(items) - MAX_GROUPS} more groups)" if len(items) > MAX_GROUPS else "")
        return Observation(f"{trimmed}{MARK}call_id={cid} hidden: {listed}", FILTERED, cid, tuple(hidden))

    def expand(self, call_id: str, wanted: list[str]) -> str:
        return self.recover(call_id, wanted)[0]

    def recover(self, call_id: str, wanted: list[str]) -> tuple[str, tuple[str, ...]]:
        """The reply to an `expand` request, and the paths it gave back."""
        payload = self.originals.get(call_id)
        if payload is None:
            return "Error: unknown call_id.", ()
        hidden = self.hidden.get(call_id, set())
        if "*" in wanted:
            fields = set(hidden)
        else:
            fields = {p for p, _ in leaves(payload)
                      if any(p == w or p.startswith(w + ".") or p.startswith(w + "[]") for w in wanted)}
        if not fields:
            groups = sorted({".".join(h.split(".")[:2]) for h in hidden})
            return ("Error: no field matches " + ", ".join(wanted)[:200] + ". Hidden groups: "
                    + ", ".join(groups[:MAX_GROUPS]) + ('; or "*" for all of them.' if groups else ".")), ()
        got = project(payload, fields)
        return json.dumps(got if isinstance(got, (dict, list)) else {}, ensure_ascii=False), tuple(sorted(fields))


class Engine:
    """`learner`: automatic mode (tracetrim.auto.Learner), or None to apply a
    fixed `policy` (or nothing). `structural`: re-encode every output after
    the projection ("toon", "headroom"), whatever the config says."""

    def __init__(self, learner=None, policy: Policy | None = None, structural: str | None = None):
        self.learner = learner
        self.structural = structural
        if policy is None and learner is not None:
            policy = learner.initial_policy()
        if policy is None and structural:
            policy = Policy({"format": "tracetrim/config@1", "tools": {}}, None)
        self.trimmer = Trimmer(self._with_structural(policy))

    def _with_structural(self, policy: Policy | None) -> Policy | None:
        if policy is not None and self.structural:
            policy.structural = self.structural
        return policy

    @property
    def policy(self) -> Policy | None:
        return self.trimmer.policy

    @property
    def trimming(self) -> bool:
        return self.policy is not None and bool(self.policy.tools)

    # -- one call ----------------------------------------------------------
    def observe(self, call: ToolCall, result: ToolResult) -> Observation:
        """Record what the tool sent; return what the model should receive."""
        if self.learner is not None:
            self.learner.call(call.tool, call.arguments, result.output, call.turn)
        if result.verbatim:
            return Observation(result.output)
        obs = self.trimmer.observe(call.tool, result.output)
        if obs.call_id and self.learner is not None:
            self.learner.trimmed(call.tool)
        return obs

    def expand(self, call_id: str, fields: list[str]) -> str:
        """Answer an `expand` request from the stored original: the tool is not called again."""
        tool = self.trimmer.tool_of.get(call_id)
        text, paths = self.trimmer.recover(call_id, fields)
        if tool is not None and self.learner is not None:
            self.learner.evidence(Evidence(RECOVERED, tool, call_id, paths))
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
            self.trimmer.policy = self._with_structural(new)
        return new is not None

    def close(self, answer: str | None) -> Path | None:
        path = self.done(answer)
        if path is not None:
            self.relearn()
        return path
