"""A synthetic billing tool for the regression tests: every value is invented.

Each invoice carries what the agent writes (id, amount, date, status), fields
it never mentions (remarks, footer), an identifier it never mentions
(`account_id`) and a value too generic to attribute (`has_more`).
"""
import json

REMARKS = {f"remark_{i}": f"internal remark number {i} about this record" for i in range(8)}
# (invoice, amount in cents, created as a Unix timestamp, the date as the agent writes it)
RUNS = [("in_1001", 2900, 1795168800, "20 November 2026"),
        ("in_1002", 4550, 1795255200, "21 November 2026"),
        ("in_1003", 12000, 1795341600, "22 November 2026"),
        ("in_1004", 7825, 1795428000, "23 November 2026")]
TOOL = "list_invoices"


def payload(i: int) -> dict:
    inv, amount, created, _ = RUNS[i]
    return {"has_more": False, "data": [{
        "id": inv, "account_id": f"acct_77{i}", "amount_due": amount, "created": created, "status": "open",
        "hosted_invoice_url": f"https://pay.example.test/i/{inv}",
        "footer": "Thank you for your business with us", **REMARKS}]}


def arguments(i: int) -> dict:
    return {"customer": f"cus_A{i}"}


def answer(i: int) -> str:
    inv, amount, _, date = RUNS[i]
    return f"Invoice {inv} is open: {amount / 100:.2f} EUR due, issued on {date}."


def output(i: int) -> str:
    return json.dumps(payload(i))
