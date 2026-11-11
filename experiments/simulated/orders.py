"""A simulated order tool for the adaptive-learning demo: every value is invented.

`list_orders(customer, limit)` returns the customer's orders, newest first.
Each order carries what a support agent usually reports (id, status, total,
date), fields it usually ignores (carrier, a refund note, internal remarks),
an identifier it never mentions (`warehouse_id`) and a flag too generic to
attribute (`gift`). The content depends only on the arguments, so the same
call returns the same payload through any adapter.
"""
import hashlib
import json

CARRIERS = ("Northwind Freight", "Bluebird Parcel", "Harbor Line Express", "Summit Courier Network")
STATUSES = ("shipped", "processing", "delivered", "returned")
NOTES = ("manual review required before any refund on this order",
         "refunds on this order are settled automatically within limits",
         "refund blocked until the open dispute on this order is closed",
         "partial refund already issued, remaining amount needs approval")
REMARKS = ("packed by the evening shift after the second stock check",
           "label reprinted once because the first scan failed",
           "customer asked for delivery during office hours only",
           "pallet moved between docks before the carrier pickup",
           "invoice copy sent again on request of the customer",
           "address validated against the postal register twice")


def _n(seed: str, mod: int) -> int:
    return int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % mod


def list_orders(customer: str, limit: int = 3) -> dict:
    orders = []
    for i in range(max(1, min(50, int(limit)))):
        s = f"{customer}:{i}"
        orders.append({
            "id": f"ord_{_n(s, 900000) + 100000}",
            "customer_id": customer,
            "warehouse_id": f"wh_{_n(s + 'w', 90) + 10}",
            "status": STATUSES[_n(s + "s", len(STATUSES))],
            "total": 1000 + _n(s + "t", 90000),                              # cents
            "created": 1793491200 + 86400 * _n(s + "d", 30) - 3600 * i,       # November 2026, newest first
            "gift": bool(_n(s + "g", 2)),
            "carrier": CARRIERS[_n(s + "c", len(CARRIERS))],
            "refund_note": NOTES[_n(s + "n", len(NOTES))],
            **{f"remark_{k}": REMARKS[(k + _n(s + "r", len(REMARKS))) % len(REMARKS)] for k in range(6)},
        })
    return {"object": "list", "has_more": False, "data": orders}


def output(customer: str, limit: int = 3) -> str:
    return json.dumps(list_orders(customer, limit), indent=2)
