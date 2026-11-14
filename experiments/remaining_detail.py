#!/usr/bin/env python3
"""Detail of experiments.remaining: which values and which fields make up what is left.

  PYTHONPATH=src .venv/bin/python -m experiments.remaining_detail

Same held-out setting as experiments.remaining (prudent projection learned from the other seven runs, current
defaults, compact JSON). Three questions:

1. unjudged fields: what kind of values they hold (null, boolean, empty string, number, string), and how many
   tokens removing each kind would save;
2. identifiers kept and not used in the held-out run: which paths weigh most;
3. a columnar JSON encoding, still valid JSON, of lists of records with the same (flattened) keys:
   {"columns": [...], "rows": [[...], ...]}; tokens against compact JSON on the same projected payloads.

Exploratory; no model is called. Numbers: results/v1/remaining_detail.json.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from tracetrim.measure import tokenizer
from tracetrim.projection import _EMPTY, dumps, identifying, learn, project
from tracetrim.traces import load
from tracetrim.usage import analyse, leaves

from .paths import ROOT, TRACES
from .remaining import CASES, category, training_sets

OUT = ROOT / "results" / "v1" / "remaining_detail.json"


def kind(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if v == "":
        return "empty string"
    return "string"


def drop_paths(payload, paths):
    p = project(payload, set(), drop=set(paths), keep_empty=True)
    return ([] if isinstance(payload, list) else {}) if p is _EMPTY else p


def flatten(d, prefix=""):
    """A record as dotted keys; None when it holds a list (a record that is not flat)."""
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and v:
            sub = flatten(v, key)
            if sub is None:
                return None
            out.update(sub)
        elif isinstance(v, (list, dict)):
            return None
        else:
            out[key] = v
    return out


def columnar(v):
    """Lists of at least two records with the same flattened keys become columns + rows. Lossless: the
    dotted keys rebuild the records (keys holding a dot are left alone)."""
    if isinstance(v, dict):
        return {k: columnar(x) for k, x in v.items()}
    if isinstance(v, list):
        if len(v) >= 2 and all(isinstance(x, dict) for x in v):
            flat = [flatten(x) for x in v]
            if all(f is not None for f in flat) and not any("." in k for x in v for k in x):
                cols = list(flat[0])
                if cols and all(list(f) == cols for f in flat):
                    return {"columns": cols, "rows": [[f[c] for c in cols] for f in flat]}
        return [columnar(x) for x in v]
    return v


def main():
    count = tokenizer()
    out = {}
    for case in CASES:
        paths = sorted((TRACES / case).glob("A*.jsonl"))
        usages = [analyse(load(p)) for p in paths]
        by_kind = Counter()
        kind_tokens = Counter()
        ident = Counter()
        prudent = col = 0
        for i, calls in enumerate(usages):
            train = usages[:i] + usages[i + 1:]
            proj = learn(train)
            used_tr, judg_tr, seen_tr = training_sets(train)
            for c in calls:
                if not c.structured or c.tool not in proj.keep:
                    continue
                pr = json.loads(proj.apply(c.tool, c.output, keep_empty=True, compact=True))
                base = count(dumps(pr, compact=True))
                prudent += base
                col += count(dumps(columnar(pr), compact=True))
                here = c.used | c.echo
                groups = defaultdict(set)
                idp = set()
                for p, v in leaves(pr):
                    cat = category(p, here, used_tr[c.tool], judg_tr[c.tool], seen_tr[c.tool])
                    if cat == "unjudged":
                        by_kind[kind(v)] += 1
                        groups[kind(v)].add(p)
                    elif cat == "identifier":
                        idp.add(p)
                # A path can hold values of several kinds across a list: the marginal saving of a kind
                # removes its paths whole, so the kinds are attributed by the kind of most of its values.
                for k, ps in groups.items():
                    kind_tokens[k] += base - count(dumps(drop_paths(pr, ps), compact=True))
                for p in idp:
                    ident[f"{c.tool}: {p}"] += base - count(dumps(drop_paths(pr, {p}), compact=True))
        out[case] = {
            "unjudged_values": dict(by_kind),
            "unjudged_tokens_by_kind": dict(kind_tokens),
            "identifier_tokens_top": dict(ident.most_common(8)),
            "identifier_tokens_total_marginal": sum(ident.values()),
            "prudent": prudent,
            "columnar": col,
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    print("1) Campi non giudicabili rimasti: token che si toglierebbero per tipo di valore (% dei token prudenti)")
    kinds = ("null", "boolean", "empty string", "number", "string")
    print(("{:<5}" + "{:>14}" * len(kinds)).format("caso", *kinds))
    for case, d in out.items():
        kt = d["unjudged_tokens_by_kind"]
        print(("{:<5}" + "{:>14}" * len(kinds)).format(case, *(f"{100 * kt.get(k, 0) / d['prudent']:.1f}%" for k in kinds)))
    print("\n2) Encoding a colonne (JSON valido) contro JSON compatto, sugli stessi payload proiettati")
    for case, d in out.items():
        print(f"  {case}: {d['prudent']:>9,} → {d['columnar']:>9,} token ({100 * (d['columnar'] / d['prudent'] - 1):+.1f}%)")


if __name__ == "__main__":
    main()
