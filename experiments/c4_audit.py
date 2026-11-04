#!/usr/bin/env python3
"""Billing support: a stricter, post-hoc audit of every diagnosis (deviation 15).

  PYTHONPATH=src .venv/bin/python -m experiments.c4_audit

The frozen thermometer (quality.C4_TRUTH) looks for a keyword anywhere in a
ticket, so it accepts advice, negations and contradictions. This audit reads
only the agent's diagnosis and asks for the facts of the scenario
(simulated/worlds.py), and rejects a match that is negated:

  T1  two successful charges of 29,00 (a double charge)
  T2  the reason of the declined payment: insufficient funds
  T3  the current period ends on 20 November 2026
  T4  the refund of 49,00 is still pending
  T5  the invoice of 38,00 = plan 29,00 + 9,00 of extra storage

It is still a set of rules, not a reading: it is reported beside the frozen
test, not instead of it.
"""
import json
import re

from tracetrim.traces import load

from .checks import final_json
from .paths import ROOT, TRACES

NEG = re.compile(r"\b(non|nessun[oa]?|senza|not|no)\b[^.;]{0,25}$", re.I)
RULES = {
    "T1": [r"(due|2|two)\s+addebit|doppi[oa]\s+addebit|addebitat[oa]\s+due\s+volte|duplicat|double", r"29"],
    "T2": [r"fondi\s+insufficienti|insufficient|disponibilit[aà]\s+(di\s+fondi\s+)?insufficiente|saldo\s+insufficiente"],
    "T3": [r"20\s+novembre|2026-11-20|20/11"],
    "T4": [r"49", r"elaborazion|pending|in\s+corso|in\s+attesa|non\s+ancora\s+(completat|accreditat)"],
    "T5": [r"38", r"\b9([.,]00)?\b", r"storage|spazio|50\s*gb"],
}


def passes(text: str, patterns) -> bool:
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if not m or NEG.search(text[:m.start()]):
            return False
    return True


def audit(answer) -> dict:
    tickets = {str(t.get("id")): t for t in (answer or {}).get("ticket") or []}
    return {k: bool(tickets.get(k)) and passes(str(tickets[k].get("diagnosi", "")), RULES[k]) for k in RULES}


def main():
    from .quality import C4_TRUTH
    out = {}
    print(f"\n  {'group':<18}{'runs':>5}{'frozen test':>13}{'audit':>8}   tickets passing the frozen test but not the audit")
    groups = {}
    for p in sorted((TRACES / "C4").glob("*.jsonl")):
        if p.stem.startswith("pilot"):
            continue
        group = p.stem.rsplit("-", 1)[0] if "-" in p.stem else "A"
        groups.setdefault(group, []).append(p)
    for group, paths in groups.items():
        rows = []
        for p in paths:
            ans = final_json(load(p))
            tickets = {str(t.get("id")): t for t in (ans or {}).get("ticket") or []}
            frozen = {k: bool(tickets.get(k)) and f(json.dumps(tickets[k], ensure_ascii=False).lower())
                      for k, f in C4_TRUTH.items()}
            strict = audit(ans)
            rows.append({"run": p.stem, "frozen": sum(frozen.values()), "audit": sum(strict.values()),
                         "frozen_only": [k for k in RULES if frozen[k] and not strict[k]]})
        out[group] = rows
        fo = sorted({f"{r['run']}:{t}" for r in rows for t in r["frozen_only"]})
        print(f"  {group:<18}{len(rows):>5}{sum(r['frozen'] for r in rows):>9}/{5 * len(rows):<3}"
              f"{sum(r['audit'] for r in rows):>4}/{5 * len(rows):<3}  {', '.join(fo)}")
    (ROOT / "results" / "c4_audit.json").write_text(json.dumps(out, indent=2) + "\n")
    print("\n  written: results/c4_audit.json\n")


if __name__ == "__main__":
    main()
