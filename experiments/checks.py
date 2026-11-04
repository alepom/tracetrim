#!/usr/bin/env python3
"""Gates: does the answer parse, have the required shape, and say only
things the tools actually returned?

  .venv/bin/python -m experiments.checks traces/C4/pilot.jsonl
  .venv/bin/python -m experiments.checks traces/*/A*.jsonl        # a table, one row per run

A gate is binary per item; the run's score is the share of verifiable items
that pass. The truth is always the RAW tool outputs of the trace, never the
projected ones: in Phase B the answer is judged against what the tool really
said, whatever the agent was shown.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tracetrim.traces import load
from tracetrim.usage import leaves, parse_output


def final_json(trace):
    """The last agent message, parsed. Fenced code blocks are tolerated."""
    msgs = [e.text for e in trace.events if e.kind == "message" and e.text.strip()]
    if not msgs:
        return None
    t = re.sub(r"^\s*```(?:json)?|```\s*$", "", msgs[-1].strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def outputs(trace):
    return [(e.tool, e.output or "") for e in trace.events if e.kind == "call" and e.output is not None]


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


# --------------------------------------------------------------------------- #
# one checker per case: (structure ok, [(item, verified)])
# --------------------------------------------------------------------------- #

def check_c1(ans, outs):
    truth = {}
    for _, o in outs:
        p = parse_output(o)
        if not isinstance(p, (dict, list)):
            continue
        def visit(n):
            if isinstance(n, dict):
                if isinstance(n.get("keyword"), str):
                    for v in (n.get("search_volume"), (n.get("keyword_info") or {}).get("search_volume")):
                        if isinstance(v, int):
                            truth.setdefault(n["keyword"].lower().strip(), set()).add(v)
                for v in n.values():
                    visit(v)
            elif isinstance(n, list):
                for v in n:
                    visit(v)
        visit(p)
    b = ans.get("piano_editoriale_brevissimo_termine") or []
    m = ans.get("piano_editoriale_medio_periodo") or []
    ok = isinstance(b, list) and isinstance(m, list) and len(b) > 0 and len(m) > 0
    items = []
    for v in b + m:
        for k in v.get("keyword_correlate") or []:
            kw = str(k.get("keyword", "")).lower().strip()
            try:
                vol = int(str(k.get("volume_ricerca")).replace(".", "").replace(",", ""))
            except ValueError:
                vol = None
            if kw in truth:
                items.append((f"volume {kw}", vol in truth[kw]))
    return ok, items


def check_c2(ans, outs):
    serp = " ".join(o for t, o in outs if t == "serp_organic_live_advanced")
    temi = ans.get("temi") or []
    ok = isinstance(temi, list) and len(temi) > 0
    items = []
    for t in temi:
        for r in t.get("risultati_rilevanti") or []:
            items.append((f"url {r.get('url')}", bool(r.get("url")) and str(r.get("url")) in serp))
        for g in t.get("gap") or []:
            for u in g.get("evidenze") or []:
                items.append((f"evidence {u}", str(u) in serp))
    return ok, items


def check_c3(ans, outs):
    by_id, by_doi = {}, {}
    for _, o in outs:
        p = parse_output(o)
        works = (p.get("results") if isinstance(p, dict) and "results" in p else [p]) if isinstance(p, dict) else []
        for w in works or []:
            if isinstance(w, dict) and w.get("id"):
                by_id[str(w["id"]).rsplit("/", 1)[-1]] = _norm(w.get("title") or w.get("display_name"))
                if w.get("doi"):
                    by_doi[str(w["doi"]).lower().removeprefix("https://doi.org/")] = _norm(w.get("title") or w.get("display_name"))
    works = ans.get("works") or []
    ok = isinstance(works, list) and len(works) > 0
    items = []
    for w in works:
        wid = str(w.get("openalex_id") or "").rsplit("/", 1)[-1]
        doi = str(w.get("doi") or "").lower().removeprefix("https://doi.org/")
        title = by_id.get(wid) or (by_doi.get(doi) if doi and doi != "null" else None)
        items.append((f"work {wid or doi}", title is not None and _norm(w.get("title")) == title))
        # A declared identifier must exist: a valid DOI does not excuse an
        # invented OpenAlex id (Phase B showed an agent copying the prompt's example).
        if wid and wid not in ("None", "null"):
            items.append((f"openalex_id {wid}", wid in by_id))
    return ok, items


STRIPE_ID = re.compile(r"\b(?:cus|ch|in|sub|re|il|si)_[A-Za-z0-9]{14}\b")


def check_c4(ans, outs):
    seen = set(STRIPE_ID.findall(" ".join(o for _, o in outs)))
    tickets = ans.get("ticket") or []
    ok = isinstance(tickets, list) and len(tickets) == 5
    items = []
    for t in tickets:
        cited = set(STRIPE_ID.findall(json.dumps(t, ensure_ascii=False)))
        for c in sorted(cited):
            items.append((f"id {c}", c in seen))
    return ok, items


def check_c5(ans, outs):
    runs, jobs, numbers, logs = set(), set(), set(), []
    for tool, o in outs:
        p = parse_output(o)
        if tool == "gh_download_job_logs":
            logs.append(o)
        if isinstance(p, dict):
            for r in p.get("workflow_runs") or []:
                runs.add(r.get("id"))
            for j in p.get("jobs") or []:
                jobs.add(j.get("name"))
            if "number" in p:
                numbers.add(p["number"])
        elif isinstance(p, list):
            numbers |= {x.get("number") for x in p if isinstance(x, dict)}
    logtext = "\n".join(logs)
    causes = ans.get("root_causes") or []
    ok = isinstance(causes, list) and len(causes) > 0
    items = []
    for c in causes:
        for r in c.get("runs") or []:
            items.append((f"run {r}", r in runs))
        # Several jobs may share a cause: "test (3.12); test (3.13)".
        for j in re.split(r"\s*[;,]\s*|\s+and\s+", str(c.get("job") or "")):
            if j:
                items.append((f"job {j}", j in jobs))
        if c.get("error"):
            items.append(("error line", str(c["error"]).strip() in logtext))
        for n in (c.get("related_issues") or []) + (c.get("related_pull_requests") or []):
            items.append((f"#{n}", n in numbers))
    return ok, items


CHECKERS = {"C1": check_c1, "C2": check_c2, "C3": check_c3, "C4": check_c4, "C5": check_c5}


def check(path: Path, case: str | None = None) -> dict:
    trace = load(path)
    case = case or path.parent.name
    ans = final_json(trace)
    meta_p = path.with_suffix(".meta.json")
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    res = {"run": f"{case}/{path.stem}", "json": ans is not None, "structure": False,
           "facts_ok": 0, "facts": 0, "domain_rule": meta.get("domain_rejections", 0) >= 0}
    if ans is not None:
        ok, items = CHECKERS[case](ans, outputs(trace))
        res["structure"] = ok
        res["facts"] = len(items)
        res["facts_ok"] = sum(v for _, v in items)
        res["failed"] = [k for k, v in items if not v][:8]
    return res


def main():
    paths = [Path(p) for p in sys.argv[1:]]
    print(f"\n  {'run':<18}{'json':>6}{'shape':>7}{'facts verified':>18}")
    print("  " + "-" * 49)
    for p in paths:
        r = check(p)
        f = f"{r['facts_ok']}/{r['facts']}" + (f" ({r['facts_ok'] / r['facts']:.0%})" if r["facts"] else "")
        print(f"  {r['run']:<18}{'yes' if r['json'] else 'NO':>6}{'yes' if r['structure'] else 'NO':>7}{f:>18}")
        for k in r.get("failed", []):
            print(f"      not verified: {k[:90]}")
    print()


if __name__ == "__main__":
    main()
