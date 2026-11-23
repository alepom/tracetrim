"""Offline, no model: would a hint on hidden free-text fields have flagged the facts the agent missed in v3c?

    .venv-lg-gemini/bin/python -m experiments.v3e.hints_offline --traces ../tracetrim-14/out/v3c/traces

Protocol v3c (gpt-5.6-terra, prudent policy P, skills 1 and 2) lost hidden facts that live in free-text fields
(`data[].description`). The `[trimmed]` line names the hidden groups but says nothing about what they hold: an
empty `description` and one that explains a credit look the same. This script replays every filtered result of the
P1 and P2 traces and computes, for each candidate hint, two things:

  signal   for each hidden fact the agent did NOT report, whether the hint on the result that held it would have
           pointed at it: `count` says how many records have text in that field (useful only if that is not every
           record); `preview-K` shows the first K characters of each distinct text, and the fact is "visible" if
           the fact's value can be read in the preview itself (experiments.v3b.hidden.matches);
  cost     the tokens (o200k_base) the hint adds to every filtered result, over all the results of the runs.

The previews are the library's own (tracetrim.engine.previews, with its limits: TEXT_MIN, TEXT_VALUES, TEXT_FIELDS),
so the cost describes the code that ships. The hidden fields are read from the traces, comparing each original with
the body the model received, not recomputed with the library. Read-only on the traces; the output is a table on stdout and results/v3e/hints_offline.json (no amounts: there is no model).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.checks import final_json
from experiments.v3b import facts
from experiments.v3b.hidden import matches
from experiments.v3b.tools import task_data
from tracetrim.engine import is_text, previews
from tracetrim.measure import counter
from tracetrim.traces import load
from tracetrim.usage import leaves, parse_output

PREVIEWS = (40, 80)
MARK = "\n[trimmed]"


def generic(path: str) -> str:
    """data[3].description -> data[].description (leaves already gives generic paths; kept for safety)."""
    import re
    return re.sub(r"\[\d+\]", "[]", path)


def hidden_of(event: dict):
    """The original payload of one filtered result and its hidden leaves, grouped by generic path (every value)."""
    original = parse_output(event["output"])
    sent = parse_output(event["sent"].split(MARK)[0])
    if not isinstance(original, (dict, list)) or not isinstance(sent, (dict, list)):
        return None, {}
    shown = {generic(p) for p, _ in leaves(sent)}
    out: dict[str, list] = {}
    for p, v in leaves(original):
        g = generic(p)
        if g not in shown:
            out.setdefault(g, []).append(v)
    return original, out


def hint(kind: str, payload, fields: dict[str, list]) -> str:
    """The text a hint would add to the `[trimmed]` line. `preview-K`: the library's previews (engine.previews).
    `count` (not implemented in the library, for comparison): how many records have text in each hidden field."""
    if kind != "count":
        return previews(payload, sorted(fields), int(kind.split("-")[1])) if payload is not None else ""
    parts = [f"{g}: text in {sum(map(is_text, vs))} of {len(vs)}" for g, vs in sorted(fields.items())
             if any(map(is_text, vs))]
    return ("; text: " + "; ".join(parts)) if parts else ""


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("results/v3e/hints_offline.json"))
    a = ap.parse_args(argv)
    count, unit = counter()
    assert unit == "tokens", "tiktoken is needed (CLAUDE.md: tokens are counted, not estimated)"
    kinds = ["count", *(f"preview-{k}" for k in PREVIEWS)]
    cost = {k: 0 for k in kinds}
    sent_tokens, filtered, with_text = 0, 0, 0
    missed, rows = [], []
    traces = sorted(a.traces.glob("C4b-*/*/P[12].jsonl"))      # the hard tasks: the ordinary ones have no key
    assert traces, f"no P1/P2 traces under {a.traces}"
    for tr in traces:
        task, rep, arm = tr.parts[-3], tr.parts[-2], tr.stem
        key = task_data(task)["key"]
        events = [json.loads(x) for x in tr.read_text().splitlines() if x.strip()]
        calls = [e for e in events if e.get("kind") == "call" and MARK in e.get("sent", "") and e["tool"] != "expand"]
        for e in calls:
            payload, fields = hidden_of(e)
            filtered += 1
            sent_tokens += count(e["sent"])
            with_text += any(is_text(v) for vs in fields.values() for v in vs)
            for k in kinds:
                h = hint(k, payload, fields)
                cost[k] += count(h) if h else 0
        answered = facts.tickets_of(final_json(load(tr)))
        for t in key["ticket"]:
            for f in t["facts"]:
                if "hidden" not in f:
                    continue
                ok = t["id"] in answered and facts.found(f, answered[t["id"]])
                if ok:
                    continue
                tool, path = f["hidden"]
                where = [e for e in calls if e["tool"] == tool
                         and any(generic(p) == path and matches(f, v) for p, v in leaves(parse_output(e["output"])))]
                row = {"slot": f"{task}/{rep}/{arm}", "ticket": t["id"], "template": t["template"], "field": path,
                       "kind": f["kind"], "in_filtered_result": bool(where)}
                for k in kinds:
                    if not where:
                        row[k] = None
                        continue
                    seen = [hidden_of(e) for e in where]
                    if k == "count":
                        # A signal only if the field is not text in every record: then «2 of 5» stands out.
                        row[k] = any(path in fs and 0 < sum(map(is_text, fs[path])) < len(fs[path]) for _, fs in seen)
                    else:
                        row[k] = any(matches(f, hint(k, pl, fs)) for pl, fs in seen)
                missed.append(row)
    summary = {
        "source": "protocol v3c traces, arms P1 and P2 (gpt-5.6-terra)",
        "traces": len(traces), "filtered_results": filtered, "filtered_results_with_hidden_text": with_text,
        "sent_tokens_of_filtered_results": sent_tokens,
        "hint_tokens": cost, "hint_share_of_sent": {k: v / sent_tokens for k, v in cost.items()},
        "missed_hidden_facts": len(missed),
        "missed_in_a_filtered_result": sum(r["in_filtered_result"] for r in missed),
        "flagged": {k: sum(bool(r[k]) for r in missed) for k in kinds},
        "missed": missed,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(summary, indent=1, ensure_ascii=False))

    print(f"{len(traces)} traces, {filtered} filtered results ({with_text} with hidden text), "
          f"{sent_tokens} tokens sent in them")
    print(f"missed hidden facts: {len(missed)}, of which in a filtered result: {summary['missed_in_a_filtered_result']}\n")
    print(f"{'hint':<12} {'flags missed':>13} {'added tokens':>13} {'share of sent':>14}")
    for k in kinds:
        print(f"{k:<12} {summary['flagged'][k]:>6} of {len(missed):<5} {cost[k]:>13} {summary['hint_share_of_sent'][k]:>13.1%}")
    by = {}
    for r in missed:
        b = by.setdefault(f"{r['template']}: {r['field']}", {"missed": 0, **{k: 0 for k in kinds}})
        b["missed"] += 1
        for k in kinds:
            b[k] += bool(r[k])
    print(f"\n{'kind of fact':<52} {'missed':>6} " + " ".join(f"{k:>11}" for k in kinds))
    for name, b in sorted(by.items()):
        print(f"{name:<52} {b['missed']:>6} " + " ".join(f"{b[k]:>11}" for k in kinds))


if __name__ == "__main__":
    main()
