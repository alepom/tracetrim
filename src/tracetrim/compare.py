"""Compare prudent recipes on held-out runs using the same decisions as the runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from . import measure, report
from .contract import FILTERED, FULL_CHEAPER
from .engine import MARK, Trimmer
from .projection import learn
from .runtime import Policy
from .traces import load
from .usage import analyse, learning_flags


CHOICES = {"protect_ids": ("all", "record"), "learning": ("1.1", "1.0"),
           "keep_positions": ("true", "false"), "unseen": ("keep", "drop"), "compact": ("true", "false")}
METRICS = ("tokens", "miss_calls", "full_cheaper")
METHOD = ("Leave-one-out. Used fields judged with the rules of 1.1 for both sides. "
          "Prudent filtering with expand on all learned tools. No thresholds, promotions or sampling.")
LIMIT = ("Only expand requests recorded in the supplied trace files are counted. "
         "Replay cannot predict expand requests or changes to the final answer.")
TOKEN_NOTE = ("o200k_base, including [trimmed]. Deterministic annotation IDs in replay. "
              "Random production IDs can vary by a few tokens per annotation.")


def recipe(text):
    values = {k: choices[0] for k, choices in CHOICES.items()}
    supplied = set()
    for pair in text.split(","):
        key, _, value = pair.strip().partition("=")
        if key in supplied or key not in CHOICES or value not in CHOICES[key]:
            raise argparse.ArgumentTypeError(f"invalid or repeated recipe setting: {pair!r}")
        values[key] = value
        supplied.add(key)
    for key in ("compact", "keep_positions"):
        values[key] = values[key] == "true"
    return values


def read_runs(paths):
    paths = [Path(p) for p in paths]
    basenames = Counter(p.name for p in paths)
    inputs, runs, seen = [], [], {}
    for p in paths:
        try:
            trace = load(p)
            for e in trace.events:
                if not isinstance(e.text, str) or not isinstance(e.tool, str) or e.tool == "?":
                    raise ValueError("invalid text or missing tool name")
                if e.turn is not None and not isinstance(e.turn, int):
                    raise ValueError("invalid turn number")
                if e.output is not None and not isinstance(e.output, str):
                    raise ValueError("invalid output text")
        except (OSError, ValueError, SyntaxError, AttributeError, TypeError) as exc:
            raise ValueError(f"{p}: {exc}") from exc
        # Compare parsed events so a renamed copy or a change in JSONL indentation cannot enter training.
        digest = hashlib.sha256(json.dumps([vars(e) for e in trace.events]).encode()).digest()
        if digest in seen:
            raise ValueError(f"duplicate trace: {p} and {seen[digest]}")
        seen[digest] = p
        name = p.name if basenames[p.name] == 1 else str(p)
        calls = [e for e in trace.events if e.kind == "call"]
        ordinary = [(i, e) for i, e in enumerate(calls, 1) if e.tool != "expand"]
        item = {"file": str(p), "run": name, "calls_read": len(calls),
                "observed_expand": sum(e.tool == "expand" for e in calls)}
        inputs.append(item)
        if not trace.events or trace.events[-1].kind != "message" or not trace.events[-1].text.strip():
            item["excluded"] = "missing final answer"
        elif not ordinary or any(e.output is None for _, e in ordinary):
            item["excluded"] = "missing original tool output"
        elif any(MARK + "call_id=" in e.output for _, e in ordinary):
            item["excluded"] = "tool output contains a trimming annotation"
        else:
            runs.append((name, trace, [i for i, _ in ordinary]))
    return inputs, runs


def _trimmer(training, config):
    projection = learn(training, "prudent", unseen=config["unseen"], protect_ids=config["protect_ids"])
    tools = {t: {"trim": "prudent", "expand": True} for t in projection.keep}
    return Trimmer(Policy({"tools": tools, "compact": config["compact"],
                           "keep_positions": config["keep_positions"]}, projection))


def _served(trimmer, call, handle, count):
    obs = trimmer.observe(call.tool, call.output, count_tokens=count)
    text = obs.text
    if obs.call_id:
        body, annotation = text.rsplit(MARK, 1)
        text = body + MARK + annotation.replace("call_id=" + obs.call_id, "call_id=" + handle, 1)
        trimmer.forget(obs.call_id)
    hidden = set(obs.hidden) if obs.exposure == FILTERED else set()
    return count(text), hidden, obs.exposure == FULL_CHEAPER


def _metrics():
    return {k: 0 for k in METRICS}


def _row():
    return {"calls": 0, "a": _metrics(), "b": _metrics()}


def compute(paths, a, b):
    inputs, runs = read_runs(paths)
    warnings = [f"{i['file']}: excluded ({i['excluded']})" for i in inputs if "excluded" in i]
    if len(runs) < 2:
        raise ValueError("\n".join(warnings + [f"need at least 2 eligible runs, found {len(runs)}"]))
    if len(runs) < report.MIN_TRACES:
        warnings.append(f"small sample: {len(runs)} eligible runs (fewer than {report.MIN_TRACES})")

    # Keep expand arguments among the words actually written by the model, while excluding its
    # replies from learning and served-token totals. The held-out judgement always comes from 1.1.
    versions = sorted({"1.1", a["learning"], b["learning"]})
    analysed_runs = [{v: [c for c in analyse(trace, **learning_flags(v)) if c.tool != "expand"]
                        for v in versions} for _, trace, _ in runs]
    configs = {"a": a, "b": b}
    count = measure.tokenizer(ordinary_text=True)
    tools = defaultdict(_row)
    missed = defaultdict(lambda: {"a": Counter(), "b": Counter()})
    examples, whole_calls = {}, []
    for (name, _, numbers), (held_out, others) in zip(runs, measure.folds(analysed_runs), strict=True):
        trimmers = {s: _trimmer([run[c["learning"]] for run in others], c) for s, c in configs.items()}
        for number, call in zip(numbers, held_out["1.1"], strict=True):
            ref = {"run": name, "call": number, "tool": call.tool}
            handle = "c" + hashlib.sha256(f"{name}/{number}".encode()).hexdigest()[:8]
            row = tools[call.tool]
            row["calls"] += 1
            hidden, whole = {}, []
            for side, trimmer in trimmers.items():
                tokens, hidden[side], cheaper = _served(trimmer, call, handle, count)
                fields = hidden[side] & call.used
                row[side]["tokens"] += tokens
                row[side]["miss_calls"] += bool(fields)
                row[side]["full_cheaper"] += cheaper
                missed[call.tool][side].update(fields)
                if cheaper:
                    whole.append(side)
            if whole:
                whole_calls.append({**ref, "sides": whole})
            if hidden["a"] != hidden["b"] and call.tool not in examples and len(examples) < 3:
                path = min(hidden["a"] ^ hidden["b"])
                examples[call.tool] = {**ref, "field": path, "hidden_by": "a" if path in hidden["a"] else "b"}

    totals = _row()
    for tool, row in tools.items():
        totals["calls"] += row["calls"]
        for side in configs:
            for k in METRICS:
                totals[side][k] += row[side][k]
        counts = missed[tool]
        row["missed_fields"] = {path: {s: counts[s][path] for s in configs}
                                for path in sorted(counts["a"].keys() | counts["b"].keys())}
    for row in [*tools.values(), totals]:
        row["delta"] = {k: row["b"][k] - row["a"][k] for k in METRICS}
    return {"format": "tracetrim/compare@1", "configurations": configs, "method": METHOD, "tokens": TOKEN_NOTE,
            "inputs": inputs, "eligible_runs": len(runs), "warnings": warnings, "totals": totals,
            "tools": dict(sorted(tools.items())), "full_cheaper_calls": whole_calls,
            "divergences": list(examples.values()), "observed_expand": sum(i["observed_expand"] for i in inputs),
            "limitations": LIMIT}


def table(data):
    print(data["method"])
    print(data["tokens"])
    print(f"Read {len(data['inputs'])} files and {sum(i['calls_read'] for i in data['inputs'])} calls. "
          f"Evaluated {data['eligible_runs']} runs and {data['totals']['calls']} tool outputs.")
    for side, config in data["configurations"].items():
        print(side.upper() + ": " + ", ".join(f"{k}={str(v).lower()}" for k, v in config.items()))
    width = max(len(tool) for tool in [*data["tools"], "TOTAL"])
    print(f"{'Tool':<{width}}  Calls   Tokens A   Tokens B   Delta B-A   Miss A/B   Full cheaper A/B")
    for tool, row in [*data["tools"].items(), ("TOTAL", data["totals"])]:
        a, b = row["a"], row["b"]
        print(f"{tool:<{width}}  {row['calls']:5}  {a['tokens']:9}  {b['tokens']:9}  {row['delta']['tokens']:+10}   "
              f"{a['miss_calls']}/{b['miss_calls']}          {a['full_cheaper']}/{b['full_cheaper']}")
    print("Missed fields (calls A/B):")
    for tool, row in data["tools"].items():
        for field, n in row["missed_fields"].items():
            print(f"  {tool} {field}  {n['a']}/{n['b']}")
    if data["full_cheaper_calls"]:
        print("Calls served whole, cheaper than trimmed:")
    for item in data["full_cheaper_calls"]:
        print(f"  {','.join(item['sides']).upper()}: {item['run']} #{item['call']} {item['tool']}")
    if data["divergences"]:
        print("Examples of divergence:")
    for item in data["divergences"]:
        print(f"  {item['run']} #{item['call']} {item['tool']} {item['field']}: hidden only by {item['hidden_by'].upper()}")
    print(f"Observed expand requests: {data['observed_expand']}. {data['limitations']}")


def command(args):
    try:
        result = compute(args.traces, args.a, args.b)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    for warning in result["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)
    if getattr(args, "html", None):
        from . import compare_html, store
        store.write_atomic(Path(args.html), compare_html.render(result))
        print(f"page: {args.html}", file=sys.stderr)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        table(result)
