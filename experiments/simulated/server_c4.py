"""Simulated MCP server for C4: a Stripe-shaped billing API (stdio).

Responses are serialised with 2-space indentation, as the Stripe API returns
them. List endpoints use Stripe's list envelope.
"""
import json

from mcp.server.mcpserver import MCPServer

from experiments.simulated.worlds import stripe_world

app = MCPServer("stripe-billing-sim")
W = stripe_world()


def out(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def listing(data, url):
    return out({"object": "list", "data": data, "has_more": False, "url": url})


def not_found(kind, id_):
    return out({"error": {"code": "resource_missing", "message": f"No such {kind}: '{id_}'",
                          "param": "id", "type": "invalid_request_error"}})


@app.tool()
def stripe_search_customers(email: str) -> str:
    """Search customers by email (Stripe `GET /v1/customers/search`). Returns the search result envelope."""
    data = [c for c in W["customers"] if c["email"].lower() == email.strip().lower()]
    return out({"object": "search_result", "data": data, "has_more": False, "next_page": None,
                "url": "/v1/customers/search"})


@app.tool()
def stripe_list_charges(customer: str, limit: int = 10) -> str:
    """List the charges of a customer, newest first (Stripe `GET /v1/charges`). limit: 1-100."""
    data = sorted((c for c in W["charges"] if c["customer"] == customer), key=lambda c: -c["created"])
    return listing(data[:max(1, min(100, limit))], "/v1/charges")


@app.tool()
def stripe_retrieve_charge(charge: str) -> str:
    """Retrieve a charge by id (Stripe `GET /v1/charges/{charge}`)."""
    c = next((c for c in W["charges"] if c["id"] == charge), None)
    return out(c) if c else not_found("charge", charge)


@app.tool()
def stripe_list_refunds(charge: str) -> str:
    """List the refunds of a charge (Stripe `GET /v1/refunds?charge=`)."""
    return listing([r for r in W["refunds"] if r["charge"] == charge], "/v1/refunds")


@app.tool()
def stripe_list_invoices(customer: str, limit: int = 10) -> str:
    """List the invoices of a customer, newest first (Stripe `GET /v1/invoices`). limit: 1-100."""
    data = sorted((i for i in W["invoices"] if i["customer"] == customer), key=lambda i: -i["created"])
    return listing(data[:max(1, min(100, limit))], "/v1/invoices")


@app.tool()
def stripe_retrieve_invoice(invoice: str) -> str:
    """Retrieve an invoice by id (Stripe `GET /v1/invoices/{invoice}`)."""
    i = next((i for i in W["invoices"] if i["id"] == invoice), None)
    return out(i) if i else not_found("invoice", invoice)


@app.tool()
def stripe_list_subscriptions(customer: str) -> str:
    """List the subscriptions of a customer (Stripe `GET /v1/subscriptions`)."""
    return listing([s for s in W["subscriptions"] if s["customer"] == customer], "/v1/subscriptions")


if __name__ == "__main__":
    app.run()
