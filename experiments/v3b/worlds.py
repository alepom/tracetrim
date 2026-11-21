"""Generate the candidate billing tasks of protocol-v3b.md: hard by construction.

Each task is a support queue of five tickets on a Stripe-shaped world. Every ticket is drawn from
seven templates. Five of them put a fact of the key in a field the frozen prudent policy (P) of
protocol v3 removes from a list call: the customer's current credit balance and its description,
a credit note issued after payment, the description of a partial refund, the memo of an invoice,
the link from a replacement invoice to the void one. Two add traps without a hidden fact: a
declined charge followed by a successful one, and a duplicate account with a similar name under
the same email. Amounts are never whole euros, so a match in the answer is not a coincidence.

The key is a list of facts per ticket, written here, before any run: what the objective score
(experiments/v3b/facts.py) checks in the answer. `hidden` names the tool and path where the fact
lives in a list call that P trims, for the check in experiments/v3b/hidden.py.

  python -m experiments.v3b.worlds          # writes experiments/v3b/material/C4b-NN.json
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import random

from experiments.paths import ROOT
from experiments.v3.api_records import billing_records

DATA = ROOT / "experiments/v3b/material"
SEED = 130421
CANDIDATES = 18
BASE = 1798761600                      # 2027-01-01, after every date of protocol v3
DAY = 86400
MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
          "ottobre", "novembre", "dicembre")
NAMES = ("Ada", "Teo", "Mia", "Leo", "Eva", "Ugo", "Noa", "Ivo", "Lia", "Rio")
SURNAMES = ("Neri", "Galli", "Conti", "Fabbri", "Moretti", "Riva", "Sala", "Villa", "Bassi", "Costa")
TITLES = ("Solleciti di gennaio", "Rimborsi e accrediti", "Verifica saldi", "Contestazioni addebiti",
          "Fatture riemesse", "Chiusura trimestre", "Coda priorità alta", "Riconciliazione pagamenti",
          "Reclami fatturazione", "Controllo crediti", "Pratiche aperte", "Assistenza amministrativa",
          "Verifiche contabili", "Coda clienti business", "Seguito disservizi", "Saldi e note di credito",
          "Documenti contestati", "Ultimi pagamenti")
HIDDEN_TEMPLATES = ("credit_note", "balance", "partial_refund", "replacement", "memo")
TRAP_TEMPLATES = ("failed_retry", "duplicate")


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def date(ts):
    d = datetime.fromtimestamp(ts, timezone.utc)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def eur(c):
    return f"{c // 100},{c % 100:02d} EUR"


class Builder:
    def __init__(self, rng):
        self.rng = rng
        self.world = {k: [] for k in ("customers", "charges", "invoices", "refunds", "subscriptions")}
        self.extra = {"invoices": {}, "charges": {}, "customers": {}}     # fields applied after billing_records

    def ident(self, prefix):
        return f"{prefix}_{self.rng.getrandbits(56):014x}"

    def amount(self, lo, hi):
        """Cents, never a whole euro."""
        while True:
            c = self.rng.randrange(lo * 100, hi * 100)
            if c % 100 and c % 10:
                return c

    def customer(self, name, email):
        c = {"id": self.ident("cus"), "object": "customer", "name": name, "email": email, "currency": "eur"}
        self.world["customers"].append(c)
        return c

    def invoice(self, c, created, lines, paid, void=False, **extra):
        gross = sum(x["amount"] for x in lines)
        inv = {"id": self.ident("in"), "object": "invoice", "customer": c["id"], "customer_email": c["email"],
               "created": created, "currency": "eur", "subtotal": gross, "total": gross, "amount_due": gross,
               "amount_paid": 0 if void else paid, "amount_remaining": 0 if void else gross - paid,
               "status": "void" if void else ("paid" if paid == gross else "open"),
               "lines": {"data": [{"id": self.ident("il"), "quantity": 1, **x} for x in lines]},
               "metadata": {}, "due_date": created + 14 * DAY}
        self.world["invoices"].append(inv)
        self.extra["invoices"][inv["id"]] = {"description": None, "post_payment_credit_notes_amount": 0,
                                             "pre_payment_credit_notes_amount": 0, "from_invoice": None,
                                             "latest_revision": None, "starting_balance": 0,
                                             "ending_balance": 0, **extra}
        return inv

    def charge(self, c, inv, amount, created, failed=False):
        ch = {"id": self.ident("ch"), "object": "charge", "customer": c["id"], "invoice": inv["id"],
              "paid": not failed, "status": "failed" if failed else "succeeded", "amount": amount,
              "amount_captured": 0 if failed else amount, "created": created, "currency": "eur"}
        self.world["charges"].append(ch)
        return ch

    def finish(self):
        billing_records(self.world)
        for kind in ("invoices", "charges", "customers"):
            rows = {r["id"]: r for r in self.world[kind]}
            for i, fields in self.extra[kind].items():
                rows[i].update(fields)
        # Not in the protocol-v3 worlds: a credit balance is read from the customer's `balance` alone.
        for c in self.world["customers"]:
            c.pop("invoice_credit_balance", None)
            c.setdefault("description", None)
        return self.world


def canone(b, base):
    return [{"description": "Canone", "amount": base}]


# -- the seven ticket templates ------------------------------------------------
# Each returns (question, facts). A fact: {"kind": customer|id|amount|count, "value", "unit"?, "hidden"?}.

def credit_note(b, c, t):
    base = b.amount(40, 120)
    k = b.amount(8, 30)
    b.invoice(c, t - 30 * DAY, canone(b, base), base)
    last = b.invoice(c, t, canone(b, base), base, post_payment_credit_notes_amount=k)
    b.charge(c, last, base, t + 3600)
    b.extra["customers"][c["id"]] = {"balance": -k}
    q = (f"Per il disservizio di {MONTHS[(datetime.fromtimestamp(t, timezone.utc).month - 2) % 12]} mi avevate promesso "
         "una nota di credito sull'ultima fattura: è stata emessa, di quale importo, e devo ancora pagare qualcosa?")
    return q, [{"kind": "id", "value": last["id"]},
               {"kind": "amount", "value": k, "hidden": ["stripe_list_invoices", "data[].post_payment_credit_notes_amount"]},
               {"kind": "amount", "value": base}]


def balance(b, c, t):
    base = b.amount(40, 120)
    old = b.amount(5, 25)
    gift = b.amount(5, 25)
    b.invoice(c, t - 30 * DAY, canone(b, base), base)
    last = b.invoice(c, t, canone(b, base), base, starting_balance=-old, ending_balance=-old)
    b.charge(c, last, base, t + 3600)
    when = t + 9 * DAY
    b.extra["customers"][c["id"]] = {"balance": -(old + gift),
                                     "description": f"Accredito di cortesia di {eur(gift)} registrato il {date(when)}"}
    q = "Quanto credito ho oggi sul conto, e da dove viene l'ultimo accredito che mi avete fatto?"
    return q, [{"kind": "amount", "value": old + gift, "hidden": ["stripe_search_customers", "data[].balance"]},
               {"kind": "amount", "value": gift, "hidden": ["stripe_search_customers", "data[].description"]}]


def partial_refund(b, c, t):
    base = b.amount(40, 90)
    unit = b.amount(9, 25)
    seats = b.rng.randrange(4, 9)
    back = b.rng.randrange(2, seats)
    b.invoice(c, t - 30 * DAY, canone(b, base), base)
    lines = canone(b, base) + [{"description": "Licenze aggiuntive", "amount": unit * seats, "quantity": seats,
                                "unit_amount": unit}]
    total = base + unit * seats
    last = b.invoice(c, t, lines, total)
    ch = b.charge(c, last, total, t + 3600)
    refund = {"id": b.ident("re"), "object": "refund", "amount": unit * back, "charge": ch["id"],
              "created": t + 6 * DAY, "currency": "eur", "reason": "requested_by_customer",
              "description": f"Storno di {back} licenze non utilizzate", "status": "succeeded",
              "balance_transaction": "txn_" + ch["id"][3:] + "r", "metadata": {}, "payment_intent": None,
              "receipt_number": None, "source_transfer_reversal": None, "transfer_reversal": None}
    b.world["refunds"].append(refund)
    b.extra["charges"][ch["id"]] = {"amount_refunded": unit * back, "refunds": {
        "object": "list", "data": [refund], "has_more": False, "url": f"/v1/charges/{ch['id']}/refunds"}}
    q = ("Sull'addebito dell'ultima fattura ho ricevuto solo un rimborso parziale: per quante licenze, "
         "e quanto ho pagato al netto del rimborso?")
    return q, [{"kind": "id", "value": ch["id"]}, {"kind": "amount", "value": unit * back},
               {"kind": "amount", "value": total - unit * back},
               {"kind": "count", "value": back, "unit": "licenz",
                "hidden": ["stripe_list_charges", "data[].refunds.data[].description"]}]


def replacement(b, c, t):
    base = b.amount(40, 120)
    total = b.amount(60, 160)
    part = b.amount(10, 50)
    v1 = b.invoice(c, t - 60 * DAY, canone(b, base), 0, void=True)
    v2 = b.invoice(c, t - 40 * DAY, [{"description": "Canone e servizi", "amount": total}], 0, void=True)
    new = b.invoice(c, t - 30 * DAY, [{"description": "Canone e servizi (riemissione)", "amount": total}], part,
                    from_invoice={"action": "revision", "invoice": v2["id"]})
    late = b.invoice(c, t - 5 * DAY, [{"description": "Canone (riemissione)", "amount": base}], base,
                     from_invoice={"action": "revision", "invoice": v1["id"]})
    b.extra["invoices"][v1["id"]]["latest_revision"] = late["id"]
    b.extra["invoices"][v2["id"]]["latest_revision"] = new["id"]
    b.charge(c, new, part, t - 29 * DAY)
    b.charge(c, late, base, t - 5 * DAY + 3600)
    q = (f"La fattura con data {date(v2['created'])} è stata annullata: quale documento la sostituisce "
         "e quanto devo ancora versare?")
    # The hidden fact is the link: the replacement's `from_invoice.invoice` holds the void invoice's id.
    return q, [{"kind": "id", "value": new["id"]},
               {"kind": "id", "value": v2["id"], "hidden": ["stripe_list_invoices", "data[].from_invoice.invoice"]},
               {"kind": "amount", "value": total - part}]


def memo(b, c, t):
    base = b.amount(40, 120)
    extra = b.amount(15, 60)
    tb = b.rng.randrange(2, 9)
    b.invoice(c, t - 30 * DAY, canone(b, base), base)
    b.invoice(c, t, canone(b, base) + [{"description": "Supplemento migrazione dati", "amount": extra}], 0,
              description=f"Supplemento per {tb} TB migrati oltre la soglia inclusa nel piano")
    q = "In fattura compare un supplemento per la migrazione: per quanti TB, di quale importo, e quanto devo in totale?"
    return q, [{"kind": "amount", "value": extra},
               {"kind": "count", "value": tb, "unit": "TB", "hidden": ["stripe_list_invoices", "data[].description"]},
               {"kind": "amount", "value": base + extra}]


def failed_retry(b, c, t):
    base = b.amount(40, 120)
    b.invoice(c, t - 30 * DAY, canone(b, base), base)
    last = b.invoice(c, t, canone(b, base), base)
    bad = b.charge(c, last, base, t + 3600, failed=True)
    good = b.charge(c, last, base, t + 2 * DAY)
    b.extra["charges"][bad["id"]] = {"failure_code": "card_declined",
                                     "failure_message": "La carta è stata rifiutata dalla banca emittente.",
                                     "outcome": {"type": "issuer_declined", "reason": "generic_decline",
                                                 "network_status": "declined_by_network", "risk_level": "normal",
                                                 "seller_message": "The bank did not return any further details."}}
    q = "Mi è arrivata la notifica di un pagamento rifiutato: l'ultima fattura risulta ancora da pagare?"
    return q, [{"kind": "id", "value": good["id"]}, {"kind": "id", "value": bad["id"]}, {"kind": "amount", "value": base}]


def duplicate(b, c, t):
    """`c` is the account in use; a duplicate with a similar name shares the email."""
    base = b.amount(40, 120)
    part = b.amount(10, 35)
    first, last_name = c["name"].split()
    dup = b.customer(f"{first[0]}. {last_name}", c["email"])
    old = b.invoice(dup, t - 50 * DAY, canone(b, base), 0, void=True)
    b.extra["customers"][dup["id"]] = {"description": f"Profilo duplicato chiuso il {date(t - 45 * DAY)}"}
    b.invoice(c, t - 30 * DAY, canone(b, base), base)
    last = b.invoice(c, t, canone(b, base), part)
    b.charge(c, last, part, t + 3600)
    q = "Ho ricevuto un sollecito di pagamento: per quale fattura, e quanto devo ancora?"
    return q, [{"kind": "id", "value": last["id"]}, {"kind": "amount", "value": base - part}]


TEMPLATES = {f.__name__: f for f in (credit_note, balance, partial_refund, replacement, memo, failed_retry, duplicate)}


def billing(index, rng):
    b = Builder(rng)
    chosen = rng.sample(sorted(TEMPLATES), 5)
    tickets, key = [], []
    names = rng.sample(range(len(NAMES)), 5)
    for j, (name_i, template) in enumerate(zip(names, chosen)):
        name = f"{NAMES[name_i]} {SURNAMES[rng.randrange(len(SURNAMES))]}"
        email = f"{NAMES[name_i].lower()}.{rng.getrandbits(32):08x}@example.test"
        c = b.customer(name, email)
        t = BASE + (10 + 3 * j) * DAY + rng.randrange(8, 18) * 3600
        question, facts = TEMPLATES[template](b, c, t)
        key.append({"id": f"T{j + 1}", "template": template, "customer_id": c["id"],
                    "facts": [{"kind": "customer", "value": c["id"]}] + facts})
        tickets.append(f"T{j + 1} — {email}: {question}")
    world = b.finish()
    title = TITLES[index]
    prompt = (f"Coda assistenza «{title}». Riconcilia fatture, pagamenti, rimborsi e crediti per ciascun cliente. "
              "Spiega quali documenti confronti e quale azione serve.\n" + "\n".join(tickets))
    return {"prompt": prompt, "world": world, "key": {"ticket": key}}


def generate():
    for i in range(CANDIDATES):
        task = f"C4b-{i + 1:02}"
        write(DATA / f"{task}.json", billing(i, random.Random(f"{SEED}/{task}")))


if __name__ == "__main__":
    generate()
