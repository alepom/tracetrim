"""Generate immutable synthetic tasks, accounting entries, CI logs and reference keys."""
from datetime import datetime, timezone
import json
from itertools import permutations
import random

from experiments.paths import ROOT
from .api_records import billing_records, ci_records

DATA = ROOT / "experiments/v3/material"
SEED = 120311
CASES = ("C4", "C5")
ARMS = ("R", "P", "I")

# Each assignment asks the same five ticket holders a different accounting question.
# The final two columns name a signed line item and the evidence needed to explain it.
BILLING = [
    ("Scadenziario", "Quale parte della fattura più recente resta da versare dopo il bonifico registrato?", "Acconto tramite bonifico", "reconcile"),
    ("Cambio posti", "Quanto incide sulla mia ultima fattura la variazione dei posti a metà periodo, e per quanti posti?", "Conguaglio posti a metà periodo", "change"),
    ("Credito prepagato", "Quanto del credito prepagato è stato scalato dal rinnovo e quanto del credito iniziale rimane disponibile dopo il rinnovo?", "Utilizzo credito prepagato", "credit"),
    ("Rettifica consumi", "Quale rettifica dei consumi spiega la differenza rispetto alla fattura precedente?", "Rettifica lettura consumi", "change"),
    ("Sconto fedeltà", "È stato applicato lo sconto fedeltà promesso? Indicate l'effetto sul totale.", "Sconto fedeltà", "optional"),
    ("Riattivazione", "Qual è la quota una tantum dovuta alla riattivazione rispetto al canone?", "Quota di riattivazione", "fee"),
    ("Licenze restituite", "Quante licenze sono state stornate e qual è il credito per ciascuna?", "Storno licenze restituite", "credit"),
    ("Rata finale", "Per chiudere la rata devo versare tutto l'importo originario oppure solo un residuo? Qual è la data di scadenza della rata finale?", "Anticipo su rata", "reconcile"),
    ("Migrazione", "La migrazione ha comportato un supplemento nella fattura corrente? Se sì, quanto?", "Supplemento migrazione", "optional"),
    ("Fattura sostitutiva", "Quale fattura sostituisce il documento annullato e quanto devo ancora pagare?", "Ricalcolo fattura sostitutiva", "replace"),
]
# For each repository, two independent causes. One recurs twice, the other once.
CI = [
    ("typesetter", ("pdf", "Load fonts", "Font family Marula missing", "Bundle the Marula font"), ("calendar", "Render date", "Expected lunedi, got Monday", "Pin the renderer locale")),
    ("gallery", ("preview", "Build thumbnails", "Thumbnail width 321 exceeds 320", "Restore thumbnail width"), ("index", "Sort captions", "Caption order differs at entry 8", "Sort captions by display order")),
    ("atlas", ("maps", "Read coordinates", "Axis order north-east is invalid", "Restore east-first axes"), ("legend", "Draw scale", "Missing unit label nautical-mile", "Add the distance unit label")),
    ("songbook", ("audio", "Mix tracks", "Sample rate 44100 differs from session", "Resample imported tracks"), ("scores", "Render score", "Clef glyph unavailable", "Install score glyphs")),
    ("bookshop", ("catalog", "Import editions", "Edition ISBN column absent", "Restore the edition column"), ("sort", "Order authors", "Accent collation differs at row 7", "Use the reference collation")),
    ("museum", ("kiosk", "Load exhibits", "Exhibit thumbnail missing for fern", "Include the fern thumbnail"), ("tour", "Check duration", "Walk duration exceeds route limit", "Correct route segment duration")),
    ("weatherboard", ("units", "Convert wind", "Wind unit knots unexpectedly rendered as mph", "Apply the knots conversion"), ("chart", "Format ticks", "Midnight tick rendered twice", "Deduplicate midnight ticks")),
    ("handbook", ("links", "Verify chapters", "Chapter anchor forest-floor unresolved", "Update the chapter anchor"), ("contents", "Build index", "Generated contents lacks appendix Q", "Regenerate the contents index")),
    ("timetable", ("service", "Load holidays", "Holiday timetable missing Tuesday service", "Add the holiday service"), ("display", "Group platforms", "Platform B grouped with C", "Fix platform grouping")),
    ("archive", ("scan", "Validate pages", "Page sequence skips folio seven", "Restore the missing folio"), ("export", "Write manifest", "Manifest lists obsolete cover sheet", "Remove the obsolete cover entry")),
]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def billing(index, rng):
    title, question, label, kind = BILLING[index]
    world = {k: [] for k in ("customers", "charges", "invoices", "refunds", "subscriptions")}
    tickets, key = [], []
    def ident(prefix):
        return f"{prefix}_{rng.getrandbits(56):014x}"
    def cents(n):
        return f"{n // 100},{n % 100:02d} EUR"

    for j, name in enumerate(("Ada", "Teo", "Mia", "Leo", "Eva")):
        email = f"{name.lower()}.{rng.getrandbits(32):08x}@example.test"
        cid = ident("cus")
        customer = {"id": cid, "object": "customer", "name": name, "email": email, "currency": "eur"}
        world["customers"].append(customer)
        base = rng.randrange(32, 110) * 100
        change = rng.randrange(3, 10) * 100
        if kind in ("credit", "optional") or (kind == "change" and j % 2):
            change = -change
        if kind == "reconcile" or (kind == "optional" and j % 2):
            change = 0
        units = (j % 3) + 1
        if index in (1, 6):
            change *= units
        credit_left = rng.randrange(2, 10) * 100
        total = base + change
        paid = (total, total // 2, 0, total // 4, total)[j]
        now = 1797292800 + j * 3600
        ids = []
        for previous in (True, False):
            inv_id = ident("in")
            ids.append(inv_id)
            lines = [{"id": ident("il"), "description": "Canone", "amount": base, "quantity": 1}]
            if not previous and change:
                lines.append({"id": ident("il"), "description": label, "amount": change, "quantity": 1})
            gross = sum(x["amount"] for x in lines)
            void = previous and kind == "replace"
            received = (0 if void else gross) if previous else paid
            balance = 0 if void else gross - received
            status = "void" if void else ("open" if balance else "paid")
            created = now - (28 * 86400 if previous else 0)
            invoice = {"id": inv_id, "object": "invoice", "customer": cid, "customer_email": email,
                       "created": created, "currency": "eur", "status": status, "subtotal": gross,
                       "total": gross, "amount_due": gross, "amount_paid": received,
                       "amount_remaining": balance, "lines": {"data": lines}, "metadata": {"batch": title}}
            invoice["due_date"] = created + (10 + j) * 86400
            if not previous:
                if index in (1, 6):
                    lines[-1].update(quantity=units, unit_amount=change // units)
                if index == 2:
                    invoice["metadata"].update(opening_credit=credit_left-change, closing_credit=credit_left)
                if kind == "replace":
                    invoice["from_invoice"] = {"invoice": ids[0], "action": "revision"}
            world["invoices"].append(invoice)
            if received:
                world["charges"].append({"id": ident("ch"), "object": "charge", "customer": cid,
                    "invoice": inv_id, "paid": True, "status": "succeeded", "amount": received,
                    "amount_captured": received, "created": created + 120, "currency": "eur"})
        action = f"Versare solo il residuo di {cents(total-paid)}." if total > paid else "Nessun altro versamento."
        topic_fact = f"{label}: {change/100:+.2f} EUR." if kind != "reconcile" else f"Pagamento ricevuto: {cents(paid)}."
        if kind == "replace":
            topic_fact += " Precedente annullata."
        if index in (1, 6):
            topic_fact += f" Quantità {units}, variazione unitaria {cents(abs(change)//units)}."
        if index == 2:
            topic_fact += f" Credito iniziale {cents(credit_left-change)}, disponibile {cents(credit_left)}."
        if index == 7:
            due = datetime.fromtimestamp(invoice["due_date"], timezone.utc).date()
            topic_fact += f" Scadenza {due.isoformat()}."
        diagnosis = f"{topic_fact} Canone {cents(base)}, totale {cents(total)}, pagato {cents(paid)}, residuo {cents(total-paid)}."
        key.append({"id": f"T{j+1}", "customer_id": cid, "diagnosi": diagnosis,
                    "evidenze": [{"oggetto": ids[0], "fatto": "Fattura precedente"},
                                 {"oggetto": ids[1], "fatto": diagnosis}],
                    "risposta_al_cliente": f"{diagnosis} {action}", "azioni_interne": [action]})
        tickets.append(f"T{j+1} — {email}: {question}")
    billing_records(world)
    prompt = f"Coda assistenza «{title}». Riconcilia le fatture con i pagamenti per ciascun cliente. " \
             "Spiega quali documenti confronti e quale azione serve.\n" + "\n".join(tickets)
    return {"prompt": prompt, "world": world, "key": {"ticket": key}}


def ci(index, rng):
    repo, first, second = CI[index]
    world = {"runs": [], "jobs": {}, "logs": {}, "issues": [], "pulls": []}
    causes = []
    reviewed = index % 2
    repeated = (index // 2) % 2
    base = 700000000 + index * 10000 + rng.randrange(1000)
    for i, (job, step, error, action) in enumerate((first, second)):
        issue = 10000 + index * 40 + i
        pr = issue + 12
        world["issues"].append({"number": issue, "title": error, "body": action, "state": "open", "labels": []})
        if i == reviewed:
            world["pulls"].append({"number": pr, "title": action, "body": f"Resolves #{issue}", "state": "open"})
        runs = []
        for repeat in range(2 if i == repeated else 1):
            run = base + 10*i + repeat
            runs.append(run)
            created = datetime.fromtimestamp(1797292800 + (2*i + repeat)*86400, timezone.utc).isoformat()
            world["runs"].append({"id": run, "name": "Build", "head_branch": "main", "status": "completed",
                                  "conclusion": "failure", "created_at": created, "run_number": run-base+1,
                                  "head_sha": f"{rng.getrandbits(160):040x}"})
            job_id = run + 2000
            world["jobs"][str(run)] = [{"id": job_id, "run_id": run, "name": job, "conclusion": "failure",
                                       "steps": [{"name": "Prepare", "conclusion": "success"},
                                                 {"name": step, "conclusion": "failure"}]}]
            world["logs"][str(job_id)] = f"Checkout complete\nStarting {step}\n{error}\nJob ended."
        causes.append({"summary": error, "workflow": "Build", "runs": runs, "job": job, "step": step,
                       "error": error, "related_issues": [issue], "related_pull_requests": [pr] if i == reviewed else [],
                       "next_action": action})
    world["issues"].append({"number": issue+20, "title": "Update handbook credits", "state": "open", "labels": []})
    for run in world["runs"]:
        url = f"https://ci.example.test/{repo}/actions/runs/{run['id']}"
        run.update(html_url=url, jobs_url=f"{url}/jobs", logs_url=f"{url}/logs",
                   artifacts_url=f"{url}/artifacts", event="push", display_title="Build release assets",
                   head_commit={"id": run["head_sha"], "message": "Update release bundle"},
                   repository={"id": base-100, "name": repo, "full_name": f"lab/{repo}"})
    world["runs"].sort(key=lambda r: r["created_at"])
    ci_records(world, repo, index)
    prompt = (f"Repository: lab/{repo}. The release owner needs a handoff for 14–20 December 2026. "
              f"Investigate the {first[0]} and {second[0]} checks on main. Group repeated failures, "
              "quote the decisive log lines, and distinguish the fix already under review from work still to open.")
    return {"prompt": prompt, "world": world, "key": {"root_causes": causes,
            "summary": f"Review the {(first, second)[reviewed][0]} fix and prepare a change for {(second, first)[reviewed][0]}."}}


def generate():
    blocks = []
    for case, build in (("C4", billing), ("C5", ci)):
        for i in range(10):
            task = f"{case}-{i+1:02}"
            write(DATA / f"{task}.json", build(i, random.Random(f"{SEED}/{task}")))
            blocks.extend((task, rep) for rep in (1, 2))
    rng = random.Random(SEED)
    rng.shuffle(blocks)
    # Use all six permutations per cycle, then four different permutations.
    cycle = list(permutations(ARMS))
    orders = []
    while len(orders) < len(blocks):
        rng.shuffle(cycle)
        orders.extend(cycle)
    write(DATA / "schedule.json", [f"{task}/{rep}/{arm}" for (task, rep), order in zip(blocks, orders)
                                   for arm in order])


if __name__ == "__main__":
    generate()
