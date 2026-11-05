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

Amounts must be whole ("29,00 €", never 129 or 29,50), the date must carry
its year, and a failed double charge or a completed refund is rejected. It is
still a set of rules, not a reading: reported beside the frozen test, not
instead of it, as stricter rules rather than confirmation.
"""
import json
import re

from tracetrim.traces import load

from .checks import final_json
from .paths import ROOT, TRACES

NEG = re.compile(r"\b(non|nessun[oa]?|senza|not|no)\b[^.;]{0,40}$", re.I)


def amount(n: int) -> str:
    """An amount in euro, written whole: "29,00 €", "€29", "29 euro"; never 129 or 29,50."""
    v = rf"{n}(?:[.,]00)?"
    # After the amount: no further digit, nor a decimal part (",50"); punctuation is fine.
    return rf"(?<![\d.,]){v}\s*(?:€|euro\b|eur\b)|(?:€|eur\b)\s*{v}(?!\d|[.,]\d)"


# Every pattern must match (not negated); no FORBID pattern may match.
RULES = {
    "T1": [r"(due|2|two)\s+addebit|doppi[oa]\s+addebit|addebitat[oa]\s+due\s+volte|duplicat|double", amount(29)],
    "T2": [r"fondi\s+insufficienti|insufficient\s+funds|disponibilit[aà]\s+(di\s+fondi\s+)?insufficiente|saldo\s+insufficiente"],
    "T3": [r"20\s+novembre\s+2026|2026-11-20|20/11/2026"],
    "T4": [amount(49), r"elaborazion|pending|in\s+corso|in\s+attesa|non\s+ancora\s+(completat|accreditat)"],
    "T5": [amount(38), amount(9), r"storage|spazio|50\s*gb"],
}
FORBID = {
    # The double charge is of two successful charges; the refund is still open.
    "T1": [r"\b(fallit[io]|rifiutat[io]|non\s+(sono\s+)?andat[io]\s+a\s+buon\s+fine|failed|declined)\b"],
    "T4": [r"\b(rimborso|refund)\b[^.;]{0,40}\b(completat[oa]|accreditat[oa]|annullat[oa]|fallit[oa]|canceled|failed)\b"],
}


def passes(text: str, patterns, forbid=()) -> bool:
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if not m or NEG.search(text[:m.start()]):
            return False
    return not any(re.search(f, text, re.I) for f in forbid)


def audit(answer) -> dict:
    tickets = {str(t.get("id")): t for t in (answer or {}).get("ticket") or []}
    return {k: bool(tickets.get(k)) and passes(str(tickets[k].get("diagnosi", "")), RULES[k], FORBID.get(k, ()))
            for k in RULES}


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
