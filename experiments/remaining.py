#!/usr/bin/env python3
"""What is left after trimming, and why: where tracetrim could still save.

  PYTHONPATH=src .venv/bin/python -m experiments.remaining

On the eight runs per case of the original study (traces/C*/A*.jsonl, whole results, no trimming), each
run held out in turn, as at run time: the prudent projection learned from the other seven with the current
defaults (identifiers protected, unseen fields kept, normalisers on), applied with empty containers kept and
compact JSON. For every held-out call the tokens the model would still receive are split by why each field
is still there:

  used        the held-out run reused it (the oracle: what a perfect projection for this run would keep)
  identifier  not used here, kept because its name marks an identifier
  elsewhere   not used here, used in at least one of the training runs
  unjudged    not used anywhere, kept because usage detection cannot judge it (booleans, nulls, ...)
  unseen      never seen while learning, kept because unseen fields are kept

The categories are removed one after the other, in this order (unseen, unjudged, elsewhere, identifier): each
step's tokens are what removing that category would save on top of the previous steps. What is left at the
end is the oracle. Prose and other unstructured outputs are counted apart: the projection does not touch them.

Also, on the same outputs: a tabular re-encoding (TOON, `tracetrim.structural`) of the projected payloads.

Exploratory, in no protocol; no model is called. Numbers: results/v1/remaining.json.
"""
from __future__ import annotations

import json
from collections import defaultdict

from tracetrim.measure import tokenizer
from tracetrim.projection import _EMPTY, dumps, identifying, learn, project
from tracetrim.structural import to_toon
from tracetrim.traces import load
from tracetrim.usage import analyse, leaves

from .paths import ROOT, TRACES

CASES = ("C1", "C2", "C3", "C4", "C5")
STEPS = ("unseen", "unjudged", "elsewhere", "identifier")
OUT = ROOT / "results" / "v1" / "remaining.json"


def training_sets(train):
    used, judgeable, seen = defaultdict(set), defaultdict(set), defaultdict(set)
    for calls in train:
        for c in calls:
            if c.structured:
                used[c.tool] |= c.used | c.echo
                judgeable[c.tool] |= c.judgeable
                seen[c.tool] |= {p for p, _ in leaves(c.payload)}
    return used, judgeable, seen


def category(p, used_here, used_tr, judg_tr, seen_tr):
    if p in used_here:
        return "used"
    if p not in seen_tr:
        return "unseen"
    if identifying(p):
        return "identifier"
    if p in used_tr:
        return "elsewhere"
    if p not in judg_tr:
        return "unjudged"
    return "other"                      # should not happen: the prudent projection removes it


def keep_only(payload, paths):
    p = project(payload, set(paths), keep_empty=True)
    return ([] if isinstance(payload, list) else {}) if p is _EMPTY else p


def main():
    count = tokenizer()
    out = {}
    for case in CASES:
        paths = sorted((TRACES / case).glob("A*.jsonl"))
        if len(paths) != 8:
            raise SystemExit(f"{case}: expected 8 traces, found {len(paths)}")
        usages = [analyse(load(p)) for p in paths]
        t = defaultdict(int)
        per_tool = defaultdict(lambda: defaultdict(int))
        for i, calls in enumerate(usages):
            train = usages[:i] + usages[i + 1:]
            proj = learn(train)
            used_tr, judg_tr, seen_tr = training_sets(train)
            for c in calls:
                raw = count(c.output)
                t["raw"] += raw
                if not c.structured:
                    t["prose"] += raw
                    continue
                t["compact"] += count(dumps(c.payload, compact=True))
                if c.tool not in proj.keep:
                    # a tool never seen while learning passes whole (compact)
                    t["untrimmed_tool"] += count(dumps(c.payload, compact=True))
                    continue
                pr = json.loads(proj.apply(c.tool, c.output, keep_empty=True, compact=True))
                here = c.used | c.echo
                cats = {p: category(p, here, used_tr[c.tool], judg_tr[c.tool], seen_tr[c.tool]) for p, _ in leaves(pr)}
                t["other_paths"] += sum(v == "other" for v in cats.values())
                cur = pr
                tok = count(dumps(cur, compact=True))
                t["prudent"] += tok
                pt = per_tool[c.tool]
                pt["prudent"] += tok
                t["toon_prudent"] += count(to_toon(pr))
                for step in STEPS:
                    keep = [p for p, k in cats.items() if k not in STEPS[: STEPS.index(step) + 1]]
                    nxt = keep_only(pr, keep)
                    n = count(dumps(nxt, compact=True))
                    t[step] += tok - n
                    pt[step] += tok - n
                    tok, cur = n, nxt
                t["oracle"] += tok
                pt["oracle"] += tok
                t["toon_oracle"] += count(to_toon(cur))
        out[case] = {"totals": dict(t), "per_tool": {k: dict(v) for k, v in per_tool.items()}}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    # Tabella: tutto in percentuale dei token grezzi del caso, così i casi si confrontano.
    hdr = ("caso", "grezzi", "testo", "compatto", "prudente", "restano: unseen", "unjudged", "elsewhere",
           "identif.", "usati", "TOON/prud.")
    print(("{:<5}" + "{:>10}" * (len(hdr) - 1)).format(*hdr))
    for case, d in out.items():
        t = d["totals"]
        r = t["raw"]
        pct = lambda k: f"{100 * t.get(k, 0) / r:.1f}%"
        toon = f"{100 * (t['toon_prudent'] / t['prudent'] - 1):+.1f}%" if t.get("prudent") else "—"
        print(("{:<5}" + "{:>10}" * (len(hdr) - 1)).format(
            case, f"{r:,}", pct("prose"), pct("compact"), pct("prudent"),
            pct("unseen"), pct("unjudged"), pct("elsewhere"), pct("identifier"), pct("oracle"), toon))
    print("\npercentuali sui token grezzi del caso; 'prudente' = ciò che il modello riceve dalle chiamate strutturate;"
          "\nunseen…identif. = token che toglierebbe ciascuna categoria; usati = l'oracolo; testo = output non strutturati;"
          "\nTOON/prud. = TOON contro JSON compatto, sugli stessi payload proiettati.")
    for case, d in out.items():
        if d["totals"].get("other_paths"):
            print(f"ATTENZIONE {case}: {d['totals']['other_paths']} campi fuori categoria")


if __name__ == "__main__":
    main()
