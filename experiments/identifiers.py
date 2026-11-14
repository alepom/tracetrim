#!/usr/bin/env python3
"""Which identifiers to protect: every field named like one (the current rule), or only the record's own.

  PYTHONPATH=src .venv/bin/python -m experiments.identifiers

Since 0.2 the prudent projection keeps every field whose name marks an identifier (`id`, `*_id`, `url`, `name`,
...), at any depth, because without the record's identifier an agent made identifiers up (Study 1). In what is
left after trimming, those fields weigh most on two cases (experiments.remaining): there they are mostly ids
and links of nested entities (a work's concepts, an issue's labels, an invoice's API self links), not the
record's own.

Variant "record + names" ("labels"): names and titles at any depth, ids and links at record level only:
the library's `protect_ids="record"` since 1.1.
Variant "record": protect an identifying field only when it belongs to the record itself, i.e. its path has
at most one list level and no object between the list and the field (`results[].id`, `items[].url`, `id`), and
not `data[].refunds.url` or `results[].authorships[].author.id`. Fields used in the training runs are kept in
both variants, as always.

Held out as in experiments.remaining. Misses: held-out calls in which a field the run used is removed.
Exploratory; no model is called: whether an agent makes up nested identifiers when they are removed is not
measured here. Numbers: results/v1/identifiers.json.
"""
from __future__ import annotations

import json

from tracetrim.measure import removes, tokenizer
from tracetrim.projection import identifying, learn
from tracetrim.traces import load
from tracetrim.usage import analyse, leaves

from .paths import ROOT, TRACES
from .remaining import CASES

OUT = ROOT / "results" / "v1" / "identifiers.json"


def record_only(path: str) -> bool:
    """The first rule tried, without names and titles at any depth: three more misses, all names and titles,
    which is why `protect_ids="record"` in the library (the "labels" arm here) keeps them."""
    if path.count("[]") > 1:
        return False
    tail = path.rsplit("[]", 1)[-1].lstrip(".")
    return "." not in tail


def variant(train, rule):
    p = learn(train, protect_ids=False)
    for t, seen in p.seen.items():
        p.keep[t] |= {x for x in seen if identifying(x) and rule(x)}
    return p


def main():
    count = tokenizer()
    out = {}
    for case in CASES:
        usages = [analyse(load(x)) for x in sorted((TRACES / case).glob("A*.jsonl"))]
        r = {"raw_structured": 0, "all": 0, "labels": 0, "record": 0, "none": 0, "calls": 0,
             "miss_all": 0, "miss_labels": 0, "miss_record": 0, "miss_none": 0}
        for i, calls in enumerate(usages):
            train = usages[:i] + usages[i + 1:]
            ps = {"all": learn(train), "labels": learn(train, protect_ids="record"), "record": variant(train, record_only),
                  "none": learn(train, protect_ids=False)}
            for c in calls:
                if not c.structured or c.tool not in ps["all"].keep:
                    continue
                r["calls"] += 1
                r["raw_structured"] += count(c.output)
                for k, p in ps.items():
                    r[k] += count(p.apply(c.tool, c.output, keep_empty=True, compact=True))
                    r[f"miss_{k}"] += any(removes(p, c.tool, f) for f in c.used | c.echo)
        out[case] = r

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(f"{'caso':<5}{'chiamate':>9}{'tutti gli id':>14}{'record + nomi':>15}{'solo record':>13}{'nessuno':>10}"
          "   mancati: tutti / record+nomi / record / nessuno")
    for case, r in out.items():
        pct = lambda k: f"{100 * (1 - r[k] / r['raw_structured']):.1f}%"
        print(f"{case:<5}{r['calls']:>9}{pct('all'):>14}{pct('labels'):>15}{pct('record'):>13}{pct('none'):>10}   "
              f"{r['miss_all']:>4} / {r['miss_labels']:>4} / {r['miss_record']:>4} / {r['miss_none']:>4}")
    print("\npercentuali = token tolti sui token grezzi delle chiamate strutturate filtrate (JSON compatto incluso)")


if __name__ == "__main__":
    main()
