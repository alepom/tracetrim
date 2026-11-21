"""The objective co-primary outcome of protocol-v3b.md: the share of key facts an answer reports.

No judge. The answer is the final JSON message of a trace (experiments.checks.final_json); each
ticket of the key is matched by its id (T1...T5) and each of its facts is looked up in that ticket
object only, all its fields together:

  customer  the ticket's `customer_id` equals the key's
  id        the Stripe identifier appears in the ticket
  amount    the amount in cents appears in euros with two decimals, comma or point, with or
            without a thousands separator (amounts are never whole euros, by construction)
  count     the number, in digits or as an Italian word, directly followed by its unit (TB, licenze)

A missing answer, unparsable JSON or a missing ticket scores zero on the facts concerned.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from experiments.checks import final_json
from tracetrim.traces import load

WORDS = {2: "due", 3: "tre", 4: "quattro", 5: "cinque", 6: "sei", 7: "sette", 8: "otto", 9: "nove"}


def amount_pattern(cents: int) -> re.Pattern:
    euros, cc = divmod(abs(cents), 100)
    whole = {str(euros), f"{euros:,}".replace(",", "."), f"{euros:,}", f"{euros:,}".replace(",", " ")}
    alt = "|".join(sorted(map(re.escape, whole), key=len, reverse=True))
    return re.compile(rf"(?<![\d.,])(?:{alt})[,.]{cc:02d}(?!\d)")


def found(fact: dict, ticket: dict) -> bool:
    if fact["kind"] == "customer":
        return ticket.get("customer_id") == fact["value"]
    text = json.dumps(ticket, ensure_ascii=False)
    if fact["kind"] == "id":
        return fact["value"] in text
    if fact["kind"] == "amount":
        return amount_pattern(fact["value"]).search(text) is not None
    if fact["kind"] == "count":
        n = fact["value"]
        number = rf"(?:{n}|{WORDS[n]})" if n in WORDS else str(n)
        return re.search(rf"(?<![\d.,]){number}\s*{fact['unit']}", text, re.I) is not None
    raise ValueError(f"unknown fact kind {fact['kind']}")


def score_answer(answer, key: dict) -> dict:
    tickets = {}
    if isinstance(answer, dict) and isinstance(answer.get("ticket"), list):
        tickets = {str(t.get("id")): t for t in answer["ticket"] if isinstance(t, dict)}
    total = correct = hidden = hidden_ok = 0
    per_ticket = {}
    for k in key["ticket"]:
        t = tickets.get(k["id"])
        ok = [bool(t) and found(f, t) for f in k["facts"]]
        per_ticket[k["id"]] = {"template": k["template"], "correct": sum(ok), "facts": len(ok)}
        total += len(ok)
        correct += sum(ok)
        for f, o in zip(k["facts"], ok):
            if "hidden" in f:
                hidden += 1
                hidden_ok += o
    return {"share": correct / total if total else 0.0, "correct": correct, "facts": total,
            "hidden_share": hidden_ok / hidden if hidden else None, "hidden_correct": hidden_ok,
            "hidden_facts": hidden, "per_ticket": per_ticket, "parsed": bool(tickets)}


def score_trace(path: Path, key: dict) -> dict:
    return score_answer(final_json(load(path)), key)
