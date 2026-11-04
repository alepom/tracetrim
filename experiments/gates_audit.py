#!/usr/bin/env python3
"""What the gates do not check, measured on every run (post hoc, deviation 15).

  PYTHONPATH=src .venv/bin/python -m experiments.gates_audit

The frozen gates are narrower than the protocol's wording ("every number or
identifier ... matches that result: C1 keyword-volume, C2 URL-title, C3
DOI-title"). This measures, beside each gate and without changing it:

  C1  pairs the gate skips: keywords for which the run's own outputs give no volume
  C2  results whose title differs from the title the search API gave for that URL
      (the gate checks only that the URL was returned)
  C3  works whose declared DOI is not the DOI of the work with that OpenAlex id
      (the gate checks the title through the id); and the gate in its original
      form, before deviation 8, which deviation 8 promised to report

Outputs are read as the agent received them (checks.outputs).
"""
import json
import re

from tracetrim.traces import load
from tracetrim.usage import parse_output

from .checks import _norm, check, final_json, outputs
from .paths import ROOT, TRACES, keep_missing


def items_of(node, pred):
    if isinstance(node, dict):
        if pred(node):
            yield node
        for v in node.values():
            yield from items_of(v, pred)
    elif isinstance(node, list):
        for v in node:
            yield from items_of(v, pred)


def c1(ans, outs):
    vols = set()
    for _, o in outs:
        for n in items_of(parse_output(o), lambda n: isinstance(n.get("keyword"), str)):
            v = n.get("search_volume", (n.get("keyword_info") or {}).get("search_volume"))
            if isinstance(v, int):
                vols.add(n["keyword"].lower().strip())
    pairs = [str(k.get("keyword", "")).lower().strip()
             for v in (ans.get("piano_editoriale_brevissimo_termine") or []) + (ans.get("piano_editoriale_medio_periodo") or [])
             for k in v.get("keyword_correlate") or []]
    return {"pairs": len(pairs), "skipped_by_gate": sum(kw not in vols for kw in pairs)}


def c2(ans, outs):
    titles = {}
    for t, o in outs:
        if t == "serp_organic_live_advanced":
            for n in items_of(parse_output(o), lambda n: isinstance(n.get("url"), str) and "title" in n):
                titles.setdefault(n["url"], set()).add(_norm(n.get("title")))
    res = [r for t in ans.get("temi") or [] for r in t.get("risultati_rilevanti") or []]
    known = [r for r in res if r.get("url") in titles]
    return {"results": len(res), "url_found": len(known),
            "title_differs": sum(_norm(r.get("titolo")) not in titles[r["url"]] for r in known)}


def c3(ans, outs, path):
    doi_of = {}
    for _, o in outs:
        for w in items_of(parse_output(o), lambda n: isinstance(n.get("id"), str) and "openalex.org" in n.get("id", "")):
            doi_of[w["id"].rsplit("/", 1)[-1]] = str(w.get("doi") or "").lower().removeprefix("https://doi.org/")
    works = ans.get("works") or []
    bad = 0
    for w in works:
        wid = str(w.get("openalex_id") or "").rsplit("/", 1)[-1]
        doi = str(w.get("doi") or "").lower().removeprefix("https://doi.org/")
        if wid in doi_of and doi and doi not in ("none", "null") and doi != doi_of[wid]:
            bad += 1
    g = check(path, "C3")
    # The original gate: the same items without "the declared id must exist".
    trace = load(path)
    ok_struct, items = __import__("experiments.checks", fromlist=["check_c3"]).check_c3(final_json(trace) or {}, outputs(trace))
    original = [v for k, v in items if not k.startswith("openalex_id ")]
    return {"works": len(works), "doi_differs": bad,
            "gate_now": bool(g["json"] and g["structure"] and g["facts_ok"] == g["facts"]),
            "gate_original": bool(g["json"] and ok_struct and all(original))}


def main():
    out = {"cases": {}}
    for case, fn in (("C1", c1), ("C2", c2), ("C3", c3)):
        paths = sorted(p for p in (TRACES / case).glob("*.jsonl") if not p.stem.startswith("pilot"))
        if not paths:
            continue
        groups = {}
        for p in paths:
            trace = load(p)
            ans = final_json(trace) or {}
            r = fn(ans, outputs(trace), p) if case == "C3" else fn(ans, outputs(trace))
            group = p.stem.rsplit("-", 1)[0] if "-" in p.stem else "A"
            groups.setdefault(group, []).append(dict(r, run=p.stem))
        out["cases"][case] = groups
        print(f"\n  {case}")
        for g, rows in groups.items():
            tot = {k: sum(r[k] for r in rows) for k in rows[0] if k != "run"}
            print(f"    {g:<18}" + "  ".join(f"{k} {v}" for k, v in tot.items()))
    out = keep_missing(out, ROOT / "results" / "gates_audit.json", ("C1", "C2", "C3"))
    (ROOT / "results" / "gates_audit.json").write_text(json.dumps(out, indent=2) + "\n")
    print("\n  written: results/gates_audit.json\n")


if __name__ == "__main__":
    main()
