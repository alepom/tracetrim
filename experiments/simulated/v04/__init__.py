"""A copy of the simulated billing world of C4, for experiment E2 of protocol-v04.md.

The world of the original study (`experiments/simulated/worlds.py`, server
`server_c4.py`) is not touched: this package derives a second world from it.
Two fields of every invoice change, and nothing else:

  collection_method   alternates between its two values along each customer's
                      invoices, newest first. In the original world the
                      invoices the tickets are about nearly all have the same
                      value, so an agent could state it without reading it.
  hosted_invoice_url  always present. In the original world it is null on
                      many invoices, and a link that does not exist cannot be asked for.

Deterministic: the original world's generator and seed (stripe spec, seed 4),
then the rule below with SEED. `rewrite` applies the same rule to a recorded
output of the original server, so that the Phase A traces can be read as if
they had been recorded on this world.
"""
import json
from functools import lru_cache

from experiments.simulated.worlds import stripe_world

SEED = 4
METHODS = ("charge_automatically", "send_invoice")
INVOICE_TOOLS = ("stripe_list_invoices", "stripe_retrieve_invoice")


def _url(invoice_id: str) -> str:
    return f"https://invoice.billing.example/i/{invoice_id}"


@lru_cache(maxsize=1)
def changes() -> dict:
    """invoice id -> the two values this world gives it."""
    W = stripe_world()
    out = {}
    for n, cus in enumerate(W["customers"]):
        mine = sorted((i for i in W["invoices"] if i["customer"] == cus["id"]), key=lambda i: -i["created"])
        for k, inv in enumerate(mine):
            out[inv["id"]] = {"collection_method": METHODS[(SEED + n + k) % 2],
                              "hosted_invoice_url": inv.get("hosted_invoice_url") or _url(inv["id"])}
    return out


@lru_cache(maxsize=1)
def world() -> dict:
    W = json.loads(json.dumps(stripe_world()))          # a deep copy: the original stays as it is
    for inv in W["invoices"]:
        inv.update(changes()[inv["id"]])
    return W


def rewrite(tool: str, output: str) -> str:
    """A recorded output of the original server, as this world's server returns it."""
    if tool not in INVOICE_TOOLS:
        return output
    try:
        payload = json.loads(output)
    except (TypeError, ValueError):
        return output
    invoices = payload.get("data") if isinstance(payload.get("data"), list) else [payload]
    for inv in invoices:
        if isinstance(inv, dict) and inv.get("id") in changes():
            inv.update(changes()[inv["id"]])
    return json.dumps(payload, indent=2, ensure_ascii=False)
