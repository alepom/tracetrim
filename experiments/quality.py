#!/usr/bin/env python3
"""Thermometers: graded quality of an answer, per case.

  .venv/bin/python -m experiments.quality traces/C4/B-*.jsonl

Gates (checks.py) say whether what the answer claims is true. Thermometers
say how much of the job it did. For C4 and C5 the scenario is ours, so the
correct diagnosis is known and is checked directly; for C1-C3 the metrics
are those of the protocol (C1: the pilot's editorial metrics).
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

from tracetrim.traces import load

from .checks import final_json
from .simulated.worlds import FAIL_TEST

BLOG = "alessiopomaro.it"


def _vol(k):
    try:
        return int(str(k.get("volume_ricerca")).replace(".", "").replace(",", ""))
    except ValueError:
        return None


def q_c1(a):
    voci = (a.get("piano_editoriale_brevissimo_termine") or []) + (a.get("piano_editoriale_medio_periodo") or [])
    kw = [k for v in voci for k in (v.get("keyword_correlate") or [])]
    vol = [x for x in (_vol(k) for k in kw) if x is not None]
    note = [str(v.get("note_di_redazione", "")) for v in voci]
    return {"items": len(voci),
            "median_volume": int(statistics.median(vol)) if vol else 0,
            "long_tail_pct": round(100 * sum(len(str(k.get("keyword", "")).split()) >= 4 for k in kw) / max(len(kw), 1)),
            "notes_with_blog_link_pct": round(100 * sum(BLOG in n for n in note) / max(len(note), 1))}


def q_c2(a):
    temi = a.get("temi") or []
    urls = {r.get("url") for t in temi for r in t.get("risultati_rilevanti") or []}
    own = {p.get("url") for t in temi for p in t.get("pagine_del_sito") or []}
    gaps = [g for t in temi for g in t.get("gap") or []]
    return {"topics": len(temi), "distinct_sources": len(urls - {None}),
            "own_pages_read": len({u for u in own if u and BLOG in u}),
            "gaps": len(gaps), "gaps_with_evidence_pct": round(100 * sum(bool(g.get("evidenze")) for g in gaps) / max(len(gaps), 1))}


def q_c3(a):
    works = a.get("works") or []
    years = [w.get("year") for w in works if isinstance(w.get("year"), int)]
    return {"works": len(works), "distinct_works": len({w.get("openalex_id") or w.get("doi") for w in works}),
            "recent_2023_pct": round(100 * sum(y >= 2023 for y in years) / max(len(years), 1)),
            "open_questions": len(a.get("open_questions") or [])}


# The known answers of the C4 scenario (worlds.py): one test per ticket.
C4_TRUTH = {
    "T1": lambda t: "due" in t or "two" in t or "doppi" in t or "duplic" in t,
    "T2": lambda t: "fondi" in t or "insufficient" in t or "insufficien" in t,
    "T3": lambda t: "20 novembre" in t or "20/11" in t or "2026-11-20" in t,
    "T4": lambda t: "elaboraz" in t or "pending" in t or "in corso" in t,
    "T5": lambda t: "50 gb" in t or "spazio" in t or "storage" in t,
}


# Added after Study 2 (deviation 11): the T2 test above looks at the whole
# ticket, so advice like "check that you have enough funds" passes it even
# when the diagnosis omits the reason. The strict test reads the diagnosis.
T2_REASON = re.compile(r"fondi (in)?sufficienti|disponibilit[aà] (di fondi )?insufficiente|insufficient|saldo insufficiente", re.I)


def q_c4(a):
    tickets = {str(t.get("id")): t for t in a.get("ticket") or []}
    right = {k: bool(tickets.get(k)) and f(json.dumps(tickets[k], ensure_ascii=False).lower())
             for k, f in C4_TRUTH.items()}
    strict = dict(right, T2=bool(T2_REASON.search(str((tickets.get("T2") or {}).get("diagnosi", "")))))
    return {"tickets": len(tickets), "correct_diagnoses": sum(right.values()),
            "correct_diagnoses_strict": sum(strict.values()),
            "evidence_per_ticket": round(sum(len(t.get("evidenze") or []) for t in tickets.values()) / max(len(tickets), 1), 1),
            "wrong": [k for k, v in right.items() if not v]}


def q_c5(a):
    causes = a.get("root_causes") or []
    text = json.dumps(causes, ensure_ascii=False)
    found = {"test": FAIL_TEST.split("::")[-1] in text, "lint": "F841" in text, "deploy": "timed out" in text}
    links = {"#142": 142 in sum((c.get("related_issues") or [] for c in causes), []),
             "#155": 155 in sum((c.get("related_pull_requests") or [] for c in causes), []),
             "#157": 157 in sum((c.get("related_pull_requests") or [] for c in causes), []),
             "#150": 150 in sum((c.get("related_issues") or [] for c in causes), [])}
    runs = {r for c in causes for r in c.get("runs") or []}
    return {"root_causes": len(causes), "causes_found": sum(found.values()), "links_found": sum(links.values()),
            "failed_runs_covered": len(runs), "missing": [k for k, v in {**found, **links}.items() if not v]}


Q = {"C1": q_c1, "C2": q_c2, "C3": q_c3, "C4": q_c4, "C5": q_c5}


def quality(path: Path) -> dict:
    a = final_json(load(path))
    return Q[path.parent.name](a) if a else {}


def main():
    for p in map(Path, sys.argv[1:]):
        print(f"  {p.parent.name}/{p.stem:<16} {json.dumps(quality(p), ensure_ascii=False)}")


if __name__ == "__main__":
    main()
