"""Evidence that the model needed a field the projection removes, and what to do about it.

During a run the engine notes two things next to the calls: how each result
was exposed (filtered, or sampled without filter) and every `expand` request
(what was asked, what was given back). When the run closes, the notes and the
final answer become evidence, kept per field and per KIND, as the set of runs
in which it was observed:

  requested     the model asked for the field, or for its group, by name
  recovered     an `expand` gave it back, in whatever way it was asked for
  reused        after getting it back, the model wrote its value
  seen_full     the field reached the model in a sampled, unfiltered result
  used_in_full  ... and the model then wrote its value

Counting runs, not requests, is the deduplication: a retry, or ten requests
for the same field in one run, are one piece of evidence. A request for
everything (`"*"`, or a prefix that spans several groups) is recorded as
`recovered` for what it returned and as `requested` for nothing: wanting the
whole result is not wanting each field.

`seen_full` without `used_in_full` means the value was not seen written
again. It is not proof that the field is useless (reasoning is not visible),
and nothing is ever removed because of it.

Promotion (opt-in): a field is added to what the projection keeps when
  - it was reused after recovery, or used in a sampled result, in at least
    `promote_reused_runs` runs (default 1: the model had it and wrote it), or
  - it was requested by name in at least `promote_requested_runs` runs
    (default 2: no visible reuse, so one run could be a whim).
A promoted field stays promoted: evidence only accumulates, so the projection
cannot oscillate. The way back is explicit (`revert`): a reverted field is
removed again and is not promoted a second time.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .contract import KINDS, RECOVERED, REQUESTED, REUSED, SEEN_FULL, USED_IN_FULL, Evidence
from .store import write_json
from .usage import keys_of, leaves, ngrams, parse_output, transformed_keys, words

FORMAT = "tracetrim/evidence@1"


@dataclass
class Adaptive:
    """What automatic mode does beyond 0.3.5. Everything is off by default."""
    promote: bool = False              # add fields to the projection on evidence
    promote_requested_runs: int = 2    # runs in which a field was asked for by name
    promote_reused_runs: int = 1       # runs in which it was reused after recovery, or used in a sampled result
    sample_rate: float = 0.0           # share of the calls that would be filtered, passed whole instead
    # Cost limit: the tokens sampling adds may not exceed this share of the
    # tokens filtering has removed so far, per tool.
    sample_max_share: float = 0.2
    sample_seed: int = 0               # the sampling sequence is a function of (seed, tool, call number)

    def __post_init__(self):
        if not 0 <= self.sample_rate <= 1 or self.sample_max_share < 0:
            raise ValueError("sample_rate is between 0 and 1, sample_max_share is not negative")
        if self.promote_requested_runs < 1 or self.promote_reused_runs < 1:
            raise ValueError("promotion thresholds are at least 1 run")


def empty() -> dict:
    return {"format": FORMAT, "tools": {}, "promotions": []}


def load(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return empty()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise ValueError(f"{path.name}: not a tracetrim evidence file")
    return data


def save(path: str | Path, data: dict) -> None:
    write_json(path, data)


def is_broad(request: str, hidden) -> bool:
    """A request for everything: `"*"`, or a prefix that reaches into more than
    one of the groups the `[trimmed]` line lists (two levels deep)."""
    if request == "*":
        return True
    groups = {".".join(h.split(".")[:2]) for h in hidden
              if h == request or h.startswith(request + ".") or h.startswith(request + "[]")}
    return len(groups) > 1


def _text(event: dict) -> str:
    args = event.get("arguments", "")
    return args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)


def reused_paths(events: list[dict], answer: str, call_index: int, after: int, paths) -> set[str]:
    """Of `paths` in the output of call `call_index`, those with a value the
    agent wrote from event `after` on (later arguments, the answer) and not
    before: the same test as usage detection, with the recovery as the moment."""
    payload = parse_output(events[call_index].get("output"))
    if not isinstance(payload, (dict, list)) or not paths:
        return set()
    before = ngrams(words(" ".join(_text(e) for e in events[:after])))
    later = ngrams(words(" ".join(_text(e) for e in events[after:]) + " " + (answer or "")))
    wanted, found = set(paths), set()
    for path, value in leaves(payload):
        if path in wanted and path not in found:
            keys = keys_of(value) + transformed_keys(value, path)
            if any(k in later and k not in before for k in keys):
                found.add(path)
    return found


def derive(events: list[dict], notes: list[dict], answer: str | None) -> tuple[list[Evidence], set[str]]:
    """The evidence of one run, and the tools that got a request for everything."""
    out: list[Evidence] = []
    broad: set[str] = set()
    for n in notes:
        tool, cid, index = n["tool"], n.get("call_id"), n.get("index")
        if n["kind"] == "expand":
            if n["targeted"]:
                out.append(Evidence(REQUESTED, tool, cid, tuple(n["targeted"])))
            if n["recovered"]:
                out.append(Evidence(RECOVERED, tool, cid, tuple(n["recovered"])))
                if answer and index is not None:
                    again = reused_paths(events, answer, index, n["after"], n["recovered"])
                    if again:
                        out.append(Evidence(REUSED, tool, cid, tuple(sorted(again))))
            if n["broad"]:
                broad.add(tool)
        elif n["kind"] == "observation" and n["exposure"] == "sampled":
            out.append(Evidence(SEEN_FULL, tool, None, tuple(n["hidden"])))
            if answer:
                used = reused_paths(events, answer, index, index + 1, n["hidden"])
                if used:
                    out.append(Evidence(USED_IN_FULL, tool, None, tuple(sorted(used))))
    return out, broad


def merge(data: dict, run: str, found: list[Evidence], broad: set[str], origins: dict) -> None:
    """Add one run's evidence: per field and kind, the runs that showed it."""
    for e in found:
        fields = data["tools"].setdefault(e.tool, {"fields": {}, "broad_requests": []})["fields"]
        for path in e.paths:
            runs = fields.setdefault(path, {})
            if run not in runs.setdefault(e.kind, []):
                runs[e.kind].append(run)
    for tool in broad:
        t = data["tools"].setdefault(tool, {"fields": {}, "broad_requests": []})
        if run not in t["broad_requests"]:
            t["broad_requests"].append(run)
    for tool, origin in origins.items():
        if tool in data["tools"]:
            data["tools"][tool]["origin"] = origin


def decide(data: dict, a: Adaptive, run: str) -> list[dict]:
    """New promotions, appended to `data["promotions"]` and returned."""
    known = {(p["tool"], p["field"]) for p in data["promotions"]}
    new = []
    for tool, t in sorted(data["tools"].items()):
        for path, runs in sorted(t["fields"].items()):
            if (tool, path) in known:
                continue
            for kind, threshold in ((REUSED, a.promote_reused_runs), (USED_IN_FULL, a.promote_reused_runs),
                                    (REQUESTED, a.promote_requested_runs)):
                if len(runs.get(kind, [])) >= threshold:
                    new.append({"tool": tool, "field": path, "reason": kind, "threshold": threshold,
                                "runs": list(runs[kind]), "evidence": {k: len(runs.get(k, [])) for k in KINDS},
                                "decided_after": run, "status": "active",
                                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                    break
    data["promotions"] += new
    return new


def promoted(data: dict, status: str = "active") -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for p in data["promotions"]:
        if p["status"] == status:
            out.setdefault(p["tool"], set()).add(p["field"])
    return out


def revert(data: dict, tool: str | None = None, field: str | None = None) -> list[dict]:
    """Take promotions back: all of them, a tool's, or one field's. A reverted
    field is removed by the projection again and is not promoted a second time."""
    changed = []
    for p in data["promotions"]:
        if p["status"] == "active" and tool in (None, p["tool"]) and field in (None, p["field"]):
            p["status"] = "reverted"
            p["reverted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            changed.append(p)
    return changed


def restore(data: dict, tool: str, field: str) -> list[dict]:
    """Undo a revert: the promotion is active again."""
    changed = [p for p in data["promotions"] if p["status"] == "reverted" and (p["tool"], p["field"]) == (tool, field)]
    for p in changed:
        p["status"] = "active"
        p.pop("reverted_at", None)
    return changed
