#!/usr/bin/env python3
"""Would a projection per mode of use do better than one projection per tool? Offline.

  python -m experiments.modes            # results/v04/modes.json

A tool may be used in a few stable ways, told apart by an argument with few
values (a result type, a time range, a sort order). This asks, on the runs
already recorded, whether learning one projection per value of such an
argument would remove more, or lose less, than the projection per tool that
tracetrim learns. Nothing is run again: original outputs and arguments are
in the traces.

Runs. Phase A of the original study, E1 and E3. Two exclusions, both because
the measure would be wrong on them:
  - runs in which results reached the model trimmed (E1 arms T and ST, every
    run of E2): a field the model did not see cannot be seen reused, so those
    runs would agree with any cut. They are counted for the arguments only.
  - E3 on C1 and C2: the outputs are recorded ones, mostly those of another
    call, so the output does not belong to the arguments it is filed under.

Candidate arguments, per tool with at least MIN_CALLS calls: a top-level
argument that takes 2 to MAX_VALUES distinct values, compared whole; calls
without it are one more profile. Identifiers are left out (by the name of the
argument or the shape of the value, as in tracetrim.turns; e-mail addresses
too): an identifier names a thing, not a way of using the tool.

Evaluation, as in the study: each run is held out in turn. The general
projection (prudent) is learned from the other runs; the projection of a
profile from the calls of that profile in the other runs. A held-out call
whose profile has fewer than MIN_PROFILE_CALLS calls in the other runs is
treated with the general projection: the "new case". For the calls of the
tool: tokens after each projection (o200k_base), calls in which a field the
run then used was removed, and which fields.

The rule for "a benefit", written before looking at the numbers: on a tool,
projections per profile remove at least BENEFIT_POINTS more points of its
output tokens than the general one, without more calls losing a used field.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from tracetrim.measure import removes, tokenizer
from tracetrim.projection import identifying, learn
from tracetrim.traces import load
from tracetrim.turns import ID_NAME, ID_SHAPE
from tracetrim.usage import analyse

from .paths import ROOT

OUT = ROOT / "results" / "v04" / "modes.json"
MIN_CALLS = 10            # calls of a tool, in the runs evaluated, for it to be looked at
MAX_VALUES = 5            # distinct values of an argument, at most
MIN_PROFILE_CALLS = 3     # calls of a profile in the other runs, for it to get its own projection
BENEFIT_POINTS = 5
ABSENT = "(absent)"
WHOLE = re.compile(r"^(A\d+|E1-[RS]-\d+|E3-(pilot-)?[RS]-\d+)$")     # runs in which the model saw results whole
REPLAYED = ("C1", "C2")


def study_traces() -> Path:
    for p in [os.environ.get("TRACETRIM_TRACES"), ROOT / "traces", ROOT / "dataset" / "traces"]:
        if p and (Path(p) / "C4" / "A1.jsonl").exists():
            return Path(p)
    raise SystemExit("the traces of the original study are needed: set TRACETRIM_TRACES")


def runs(case: str) -> tuple[list[Path], list[Path]]:
    """(evaluated, counted for the arguments only)."""
    files = sorted(study_traces().joinpath(case).glob("A*.jsonl")) + sorted((ROOT / "traces" / case).glob("E*.jsonl"))
    whole = [p for p in files if WHOLE.match(p.stem) and not (p.stem.startswith("E3") and case in REPLAYED)]
    return whole, [p for p in files if p not in whole]


def arguments(text: str) -> dict:
    try:
        d = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {}
    return d if isinstance(d, dict) else {}


def value(args: dict, name: str) -> str:
    return json.dumps(args[name], sort_keys=True, ensure_ascii=False) if name in args else ABSENT


def is_identifier(name: str, values: set[str]) -> bool:
    plain = [v.strip('"') for v in values if v != ABSENT]
    return bool(ID_NAME.search(name) or identifying(name) or name == "email"
                or any(ID_SHAPE.match(v) or "@" in v for v in plain))


def candidates(calls: list[tuple[str, dict]], evaluated: Counter) -> dict[str, dict[str, Counter]]:
    """`{tool: {argument: Counter(value -> calls)}}` over every run, for the tools with enough evaluated calls."""
    seen: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    per_tool = Counter(tool for tool, _ in calls)
    for tool, args in calls:
        for name in args:
            seen[tool][name][value(args, name)] += 1
    out: dict[str, dict[str, Counter]] = {}
    for tool, args in seen.items():
        if evaluated[tool] < MIN_CALLS:
            continue
        for name, values in args.items():
            if not 2 <= len(values) <= MAX_VALUES or is_identifier(name, set(values)):
                continue
            if sum(values.values()) < per_tool[tool]:
                values[ABSENT] = per_tool[tool] - sum(values.values())
            out.setdefault(tool, {})[name] = values
    return out


def evaluate(case: str, count) -> dict:
    whole, others = runs(case)
    usages = [[c for c in analyse(load(p)) if c.tool != "expand"] for p in whole]
    extra = [(e.tool, arguments(e.text)) for p in others for e in load(p).calls if e.tool != "expand"]
    return dict(evaluate_runs(usages, extra, count), runs_counted_for_arguments_only=len(others))


def evaluate_runs(usages: list[list], extra: list[tuple[str, dict]], count) -> dict:
    """`usages`: the runs evaluated, as tracetrim.usage.analyse gives them; `extra`: (tool, arguments) of the
    calls of the other runs, which only say what values an argument takes."""
    every = [(c.tool, arguments(c.arguments)) for u in usages for c in u] + list(extra)
    cands = candidates(every, Counter(c.tool for u in usages for c in u))
    result = {"runs_evaluated": len(usages), "arguments_with_one_profile_in_the_runs_evaluated": 0, "tools": {}}
    if not cands:
        return result
    general = [learn(usages[:i] + usages[i + 1:], "prudent") for i in range(len(usages))]
    raw, after = {}, {}                                    # tokens per call, whole and after the general projection
    for i, u in enumerate(usages):
        for k, c in enumerate(u):
            if c.tool in cands and c.structured and c.tool in general[i].keep:
                raw[i, k], after[i, k] = count(c.output), count(general[i].apply(c.tool, c.output))
    for tool, args in sorted(cands.items()):
        calls = [(i, k, c) for i, u in enumerate(usages) for k, c in enumerate(u) if (i, k) in raw and c.tool == tool]
        if len(calls) < MIN_CALLS:
            continue
        lost_g = [sorted(f for f in c.used if removes(general[i], tool, f)) for i, _, c in calls]
        t = {"calls": len(calls), "tokens": sum(raw[i, k] for i, k, _ in calls),
             "general": {"tokens_after": sum(after[i, k] for i, k, _ in calls),
                         "calls_losing_a_used_field": sum(map(bool, lost_g)),
                         "fields_lost": sorted({f for fs in lost_g for f in fs})},
             "arguments": {}}
        t["general"]["removed"] = round(1 - t["general"]["tokens_after"] / t["tokens"], 4)
        for name, values in sorted(args.items()):
            mode = lambda c: value(arguments(c.arguments), name)                       # noqa: E731
            profiles: dict[str, dict] = {v: {"calls": 0, "runs": set(), "used": set()} for v in values}
            for i, _, c in calls:
                p = profiles.setdefault(mode(c), {"calls": 0, "runs": set(), "used": set()})
                p["calls"] += 1
                p["runs"].add(i)
                p["used"] |= c.used
            tokens, new, lost, learned = 0, 0, [], {}
            for i, k, c in calls:
                v = mode(c)
                if (i, v) not in learned:
                    same = [[x for x in u if x.tool == tool and x.structured and mode(x) == v]
                            for j, u in enumerate(usages) if j != i]
                    learned[i, v] = learn(same, "prudent") if sum(map(len, same)) >= MIN_PROFILE_CALLS else None
                p = learned[i, v]
                if p is None:                                # a new case: the general projection covers it
                    new += 1
                    tokens += after[i, k]
                    lost.append(sorted(f for f in c.used if removes(general[i], tool, f)))
                else:
                    tokens += count(p.apply(tool, c.output))
                    lost.append(sorted(f for f in c.used if removes(p, tool, f)))
            evaluated = {v: p for v, p in profiles.items() if p["calls"]}
            if len(evaluated) < 2:                           # its other values are only in runs that are not evaluated
                result["arguments_with_one_profile_in_the_runs_evaluated"] += 1
                continue
            used_all = [p["used"] for p in evaluated.values()]
            a = {"values_over_every_run": dict(values.most_common()),
                 "profiles": {v: {"calls": p["calls"], "runs": len(p["runs"]), "fields_used": len(p["used"]),
                                  "fields_used_here_only": sorted(p["used"] - set().union(*[u for u in used_all if u is not p["used"]]))[:12]}
                              for v, p in sorted(evaluated.items(), key=lambda x: -x[1]["calls"])},
                 "profiles_evaluated": len(evaluated),
                 "calls_per_profile_median": median(p["calls"] for p in evaluated.values()),
                 "calls_per_profile_min": min(p["calls"] for p in evaluated.values()),
                 "new_cases_covered_by_the_general_projection": new,
                 "tokens_after": tokens, "removed": round(1 - tokens / t["tokens"], 4),
                 "calls_losing_a_used_field": sum(map(bool, lost)),
                 "fields_lost": sorted({f for fs in lost for f in fs})}
            a["points_removed_more_than_general"] = round((a["removed"] - t["general"]["removed"]) * 100, 1)
            a["benefit"] = (a["points_removed_more_than_general"] >= BENEFIT_POINTS
                            and a["calls_losing_a_used_field"] <= t["general"]["calls_losing_a_used_field"])
            t["arguments"][name] = a
        if t["arguments"]:
            result["tools"][tool] = t
    return result


def analyse_all() -> dict:
    count = tokenizer()
    cases = {case: evaluate(case, count) for case in sorted(p.name for p in study_traces().iterdir() if p.is_dir())}
    rows = [a for c in cases.values() for t in c["tools"].values() for a in t["arguments"].values()]
    return {"what": "offline: one projection per value of a low-cardinality argument against one projection per tool, leave-one-run-out",
            "tokens": "tiktoken o200k_base", "projection": "prudent",
            "settings": {"min_calls": MIN_CALLS, "max_values": MAX_VALUES, "min_profile_calls": MIN_PROFILE_CALLS,
                         "benefit_points": BENEFIT_POINTS},
            "candidates": len(rows),
            "arguments_with_one_profile_in_the_runs_evaluated": sum(c["arguments_with_one_profile_in_the_runs_evaluated"] for c in cases.values()), "with_a_benefit": sum(a["benefit"] for a in rows),
            "removing_more_and_losing_more": sum(a["points_removed_more_than_general"] >= BENEFIT_POINTS and not a["benefit"] for a in rows),
            "cases": cases}


if __name__ == "__main__":
    res = analyse_all()
    OUT.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    print(f"{res['candidates']} candidate arguments · with a benefit: {res['with_a_benefit']} · "
          f"removing more and losing more: {res['removing_more_and_losing_more']}")
    print(f"{'case':<4} {'tool':<44}{'argument':<28}{'calls':>6}{'prof':>5}{'min':>4}{'new':>5}{'general':>9}{'by mode':>9}{'lost g':>7}{'lost m':>7}")
    for case, c in res["cases"].items():
        print(f"{case}: {c['runs_evaluated']} runs evaluated, {c['runs_counted_for_arguments_only']} counted for the arguments only")
        for tool, t in c["tools"].items():
            for name, a in t["arguments"].items():
                print(f"{case:<4} {tool:<44}{name:<28}{t['calls']:>6}{a['profiles_evaluated']:>5}{a['calls_per_profile_min']:>4}"
                      f"{a['new_cases_covered_by_the_general_projection']:>5}{t['general']['removed']:>9.0%}{a['removed']:>9.0%}"
                      f"{t['general']['calls_losing_a_used_field']:>7}{a['calls_losing_a_used_field']:>7}{'  BENEFIT' if a['benefit'] else ''}")
    print(OUT)
