#!/usr/bin/env python3
"""Does learning add value over minification and a static filter?

  export TRACETRIM_TRACES=$HOME/progetti/tracetrim/traces
  PYTHONPATH=src .venv/bin/python -m experiments.baselines     # tables + results/baselines.json

Offline, no spend. The arms, the static filter and the measures were fixed in
docs/baselines.md and committed before this script computed anything:

  raw             the tool outputs as returned
  minified        the same JSON written compactly, nothing removed
  static          a filter that never reads a trace (generic metadata keys,
                  then empty values), then minified
  learned         the library's default prudent projection as served today,
                  learned leave-one-out from the other runs of the case
  learned+static  the static filter applied to the learned output

Every arm is applied to the same held-out calls: every call of every Phase A
run. Tokens and misses are Study 1's definitions (experiments/analyze_a.py),
and the inputs are checked against results/phase_a.json and
results/reserialize.json before anything is written: a parser that silently
skipped outputs would give plausible, wrong numbers.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from tracetrim.measure import leave_one_out, removes, tokenizer
from tracetrim.projection import dumps, learn
from tracetrim.traces import load
from tracetrim.usage import leaves, parse_output
from tracetrim.usage import analyse

from .paths import ROOT, STUDY_TRACES
from .reserialize_baseline import reserialize

CASES = ("C1", "C2", "C3", "C4", "C5")
REAL, SIMULATED = ("C1", "C2", "C3"), ("C4", "C5")
ARMS = ("raw", "minified", "static", "learned", "learned+static")
RES = ROOT / "results"
OUT = RES / "baselines.json"

# The list of docs/baselines.md, rule (b), as pre-registered: compared
# case-insensitively with the key name, at any depth. Not to be tuned on the
# results (tests/test_baselines.py checks it equals the documented list).
STATIC_KEYS = frozenset({
    # transport and task envelope
    "version", "api_version", "status_code", "status_message", "time", "cost", "tasks_count",
    "tasks_error", "path", "se_type", "check_url", "datetime",
    # request tracing and caching
    "request_id", "requestid", "x_request_id", "trace_id", "correlation_id", "idempotency_key", "etag",
    # type tags and environment flags
    "object", "livemode",
    # pagination
    "has_more", "next_page_url", "prev_page_url", "previous_page_url", "first_page_url", "last_page_url",
    "next_url", "prev_url", "previous_url", "next_page", "next_cursor", "prev_cursor", "previous_cursor",
    # hypermedia
    "_links",
})
_GONE = object()



def _ranked(counts: Counter) -> dict:
    """Most frequent first, ties by name: the file is the same at every run (sets iterate in hash order)."""
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0]))))

def static_payload(node, keys: bool = True, empties: bool = True):
    """Rule (b), then rule (a), bottom-up, on a parsed payload. It is given the
    payload and nothing else: no tool name, no trace, no usage. `keys` and
    `empties` switch one rule off, only to split the static saving between
    them (a diagnostic, not an arm)."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if keys and isinstance(k, str) and k.lower() in STATIC_KEYS:
                continue
            r = static_payload(v, keys, empties)
            if r is not _GONE:
                out[k] = r
        # emptied by the filter, or empty to begin with
        return out if out or not empties else _GONE
    if isinstance(node, list):
        out = [r for v in node if (r := static_payload(v, keys, empties)) is not _GONE]
        return out if out or not empties else _GONE
    # `0` and `False` are information; `== ""` is False for both.
    return _GONE if empties and (node is None or (isinstance(node, str) and node == "")) else node


def static(output: str, keys: bool = True, empties: bool = True) -> str:
    """The static arm: filter, then minify. Prose and errors as they are, as in every arm."""
    p = parse_output(output)
    if not isinstance(p, (dict, list)):
        return output
    r = static_payload(p, keys, empties)
    return dumps(([] if isinstance(p, list) else {}) if r is _GONE else r, compact=True)


def minified(output: str) -> str:
    return reserialize(output, minify=True)


def leaf_paths(output: str) -> set[str] | None:
    p = parse_output(output)
    return {path for path, _ in leaves(p)} if isinstance(p, (dict, list)) else None


def removed_used(c, out: str, used: set[str]) -> set[str]:
    """Used fields of this call that the arm's output no longer contains: the
    generic form of a miss, the same for every arm."""
    if not c.structured or not used:
        return set()
    after = leaf_paths(out)
    if after is None:                             # the arm did not touch a non-JSON output
        return set()
    before = {path for path, _ in leaves(c.payload)}
    return {f for f in used if f in before and f not in after}


def check_inputs(case, names, study1, count):
    """Runs, calls, raw tokens and Study 1's own leave-one-out must equal the
    published results before this case is measured."""
    a = json.loads((RES / "phase_a.json").read_text())["cases"][case]
    assert names == a["runs"], f"{case}: runs {names} != phase_a {a['runs']}"
    calls = sum(len(u) for u in study1)
    assert calls == a["calls"], f"{case}: {calls} calls != phase_a {a['calls']}"
    rows = leave_one_out(names, study1, source="full", unseen="keep", protect_ids=False)
    for k in ("raw", "prudent", "trimmed_calls", "misses", "misses_prudent"):
        got = sum(getattr(r, k) for r in rows)
        assert got == a["loo_full"][k], f"{case}: Study 1 {k} {got} != phase_a {a['loo_full'][k]}"
    return {"runs": len(names), "calls": calls, "raw": a["loo_full"]["raw"],
            "study1_misses_aggressive": a["loo_full"]["misses"],
            "study1_misses_prudent": a["loo_full"]["misses_prudent"],
            "study1_trimmed_calls": a["loo_full"]["trimmed_calls"]}


def measure(case, count):
    paths = sorted((STUDY_TRACES / case).glob("A*.jsonl"))
    names = [p.stem for p in paths]
    traces = [load(p) for p in paths]
    # Study 1's detection: what a miss is counted against (primary).
    study1 = [analyse(t, normalize=False, comma_grouping=False) for t in traces]
    # The library's defaults: what the learned arm learns from, and the sensitivity.
    default = [analyse(t) for t in traces]
    check = check_inputs(case, names, study1, count)

    tokens = Counter()
    structured = trimmed = 0
    misses = {a: 0 for a in ARMS}
    misses_sens = {a: 0 for a in ARMS}
    missed_fields = {a: Counter() for a in ARMS}
    # The values behind the static arm's misses: a rule that never reads a
    # trace can only miss by name, so what it removed is worth showing.
    static_missed_values = Counter()
    per_tool = defaultdict(lambda: Counter())
    for i, (calls, calls_d) in enumerate(zip(study1, default)):
        proj = learn(default[:i] + default[i + 1:])          # prudent, full, keep, ids protected
        for c, cd in zip(calls, calls_d):
            assert (c.tool, c.output) == (cd.tool, cd.output), f"{case} {names[i]}: analyses out of step"
            learned = proj.apply(c.tool, c.output, keep_empty=True, compact=True)
            outs = {"raw": c.output, "minified": minified(c.output), "static": static(c.output),
                    "learned": learned, "learned+static": static(learned)}
            structured += c.structured
            is_trimmed = c.structured and c.tool in proj.keep
            trimmed += is_trimmed
            t = per_tool[c.tool]
            t["calls"] += 1
            for arm, out in outs.items():
                n = count(out)
                tokens[arm] += n
                t[arm] += n
                lost = removed_used(c, out, c.used)
                misses[arm] += bool(lost)
                missed_fields[arm].update(lost)
                if arm == "static":
                    static_missed_values.update(f"{f} = {v!r}" for f in lost
                                                for v in {v for p, v in leaves(c.payload) if p == f})
                misses_sens[arm] += bool(removed_used(c, out, cd.used))
            # Which rule the static saving comes from: diagnostics, not arms.
            tokens["static_keys_only"] += count(static(c.output, empties=False))
            tokens["static_empties_only"] += count(static(c.output, keys=False))
            # Study 1's own form of the learned arm's miss must agree with the generic one.
            s1 = is_trimmed and any(removes(proj, c.tool, f) for f in c.used)
            assert s1 == bool(removed_used(c, learned, c.used)), f"{case} {names[i]} {c.tool}: miss forms disagree"

    assert tokens["raw"] == check["raw"], f"{case}: raw {tokens['raw']} != phase_a {check['raw']}"
    rs = json.loads((RES / "reserialize.json").read_text())["cases"][case]["phase_a"]
    assert tokens["minified"] == rs["minified"], f"{case}: minified {tokens['minified']} != reserialize {rs['minified']}"
    assert misses["minified"] == 0 == misses["raw"], f"{case}: a lossless arm lost a field"

    raw, mini = tokens["raw"], tokens["minified"]
    return {
        "input_check": check | {"structured_calls": structured, "minified_matches_reserialize_json": True},
        "tokens": dict(tokens),
        "removed_vs_raw": {a: 1 - tokens[a] / raw for a in ARMS},
        "removed_vs_minified": {a: 1 - tokens[a] / mini for a in ARMS},
        # What learning removes beyond the static filter, on its own and on top of it.
        "removed_vs_static": {a: 1 - tokens[a] / tokens["static"] for a in ("learned", "learned+static")},
        "static_rules_vs_minified": {k: 1 - tokens[f"static_{k}_only"] / mini for k in ("keys", "empties")},
        "misses": {a: {"calls": misses[a], "of_structured_calls": structured,
                       "share": misses[a] / structured if structured else 0} for a in ARMS},
        "learned_trimmed_calls": trimmed,
        "misses_sensitivity_default_detection": {
            a: {"calls": misses_sens[a], "of_structured_calls": structured} for a in ARMS},
        "missed_fields": {a: _ranked(missed_fields[a]) for a in ARMS if missed_fields[a]},
        "static_missed_values": _ranked(static_missed_values),
        "per_tool": {tool: dict(v) for tool, v in sorted(per_tool.items())},
    }


def pooled(res, cases):
    keep = [c for c in cases if c in res]
    if not keep:
        return None
    tok = Counter()
    for c in keep:
        tok.update(res[c]["tokens"])
    s = sum(res[c]["input_check"]["structured_calls"] for c in keep)
    m = {a: sum(res[c]["misses"][a]["calls"] for c in keep) for a in ARMS}
    return {"cases": keep, "tokens": dict(tok),
            "removed_vs_raw": {a: 1 - tok[a] / tok["raw"] for a in ARMS},
            "removed_vs_minified": {a: 1 - tok[a] / tok["minified"] for a in ARMS},
            "removed_vs_static": {a: 1 - tok[a] / tok["static"] for a in ("learned", "learned+static")},
            "misses": {a: {"calls": m[a], "of_structured_calls": s, "share": m[a] / s} for a in ARMS}}


def pct(x):
    return f"{-100 * x:+.1f}%" if abs(x) >= 0.0005 else "0.0%"


def main():
    count = tokenizer()
    res = {}
    for case in CASES:
        if not any((STUDY_TRACES / case).glob("A*.jsonl")):
            print(f"  {case}: no Phase A traces in {STUDY_TRACES}, skipped")
            continue
        res[case] = measure(case, count)
    # The source is named, never as a local path: the file is published.
    out = {"traces": "the Phase A traces of Studies 1-2 ($TRACETRIM_TRACES, or dataset/traces)", "arms": list(ARMS), "static_keys": sorted(STATIC_KEYS),
           "definitions": "docs/baselines.md", "cases": res,
           "pooled_real": pooled(res, REAL), "pooled_simulated": pooled(res, SIMULATED)}

    print(f"\n  BASELINES: Phase A, leave-one-out, every call of every run (tokens o200k_base)\n")
    hdr = f"  {'case':<5}{'runs':>5}{'calls':>6}{'raw tokens':>12}" + "".join(f"{a:>16}" for a in ARMS[1:])
    print("  tokens removed vs raw")
    print(hdr)
    for case, r in res.items():
        ic = r["input_check"]
        print(f"  {case:<5}{ic['runs']:>5}{ic['calls']:>6}{ic['raw']:>12,}"
              + "".join(f"{pct(r['removed_vs_raw'][a]):>16}" for a in ARMS[1:]))
    print("\n  tokens removed vs minified")
    print(f"  {'case':<5}{'minified tok':>23}" + "".join(f"{a:>16}" for a in ARMS[2:]))
    for case, r in res.items():
        print(f"  {case:<5}{r['tokens']['minified']:>23,}" + "".join(f"{pct(r['removed_vs_minified'][a]):>16}" for a in ARMS[2:]))
    print("\n  misses: held-out structured calls where the arm removes a field used later (Study 1 detection)")
    print(f"  {'case':<5}{'structured':>11}" + "".join(f"{a:>16}" for a in ARMS[1:]) + f"{'sens. learned':>15}{'sens. static':>14}")
    for case, r in res.items():
        s = r["input_check"]["structured_calls"]
        ms = r["misses_sensitivity_default_detection"]
        print(f"  {case:<5}{s:>11}" + "".join(f"{r['misses'][a]['calls']:>9} ({r['misses'][a]['share']:.0%})" for a in ARMS[1:])
              + f"{ms['learned']['calls']:>15}{ms['static']['calls']:>14}")
    for name in ("pooled_real", "pooled_simulated"):
        p = out[name]
        if p:
            print(f"\n  {name} {p['cases']}: removed vs raw " + ", ".join(f"{a} {pct(p['removed_vs_raw'][a])}" for a in ARMS[1:])
                  + "; misses " + ", ".join(f"{a} {p['misses'][a]['calls']}/{p['misses'][a]['of_structured_calls']}" for a in ARMS[2:]))
    print("\n  input check: runs, calls, raw tokens and Study 1's misses equal results/phase_a.json;"
          " minified totals equal results/reserialize.json")
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n  written: {OUT.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
