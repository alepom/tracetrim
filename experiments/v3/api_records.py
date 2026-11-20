"""Populate ordinary API metadata, keeping every task fact and relationship authoritative.

Field families follow the original Stripe and GitHub simulated servers. Generate URLs and
identifiers from the task's own objects. Optional financial adjustments stay empty or zero.
"""
from datetime import datetime, timedelta
import hashlib

from experiments.simulated.worlds import _log


def billing_records(world):
    invoices = {row["id"]: row for row in world["invoices"]}
    for c in world["customers"]:
        cid = c["id"]
        docs = sorted((d for d in invoices.values() if d["customer"] == cid), key=lambda d: d["created"])
        last = docs[-1]
        sub = "sub_" + cid.split("_")[1]
        address = {"line1": f"Via Esempio {len(world['subscriptions'])+1}", "line2": None,
                   "city": "Bologna", "state": "BO", "postal_code": "40100", "country": "IT"}
        credit = last["metadata"].get("closing_credit", 0)
        c.update(address=address, balance=-credit, business_name=None, cash_balance=None,
                 created=docs[0]["created"]-86400, default_source=None, delinquent=False,
                 description=None, discount=None, invoice_prefix=cid[-8:].upper(),
                 invoice_settings={"custom_fields": None, "default_payment_method": None,
                                   "footer": None, "rendering_options": None},
                 livemode=False, metadata={}, next_invoice_sequence=len(docs)+1, phone=None,
                 preferred_locales=["it"], shipping=None, tax_exempt="none", test_clock=None,
                 invoice_credit_balance={"eur": -credit}, individual_name=c["name"],
                 tax={"automatic_tax": "not_collecting", "location": {"country": "IT", "state": "BO",
                                                                      "source": "billing_address"}},
                 tax_ids={"object": "list", "data": [], "has_more": False, "url": f"/v1/customers/{cid}/tax_ids"})

        world["subscriptions"].append({"id": sub, "object": "subscription", "customer": cid,
            "status": "active", "created": c["created"], "currency": "eur", "livemode": False,
            "billing_cycle_anchor": docs[0]["created"], "cancel_at": None, "canceled_at": None,
            "cancel_at_period_end": False, "cancellation_details": {"comment": None, "reason": None},
            "collection_method": "send_invoice", "days_until_due": (last["due_date"]-last["created"])//86400, "default_source": None,
            "default_tax_rates": [], "description": None, "discounts": [], "ended_at": None,
            "latest_invoice": last["id"], "metadata": {}, "pause_collection": None,
            "payment_settings": {"payment_method_types": None, "save_default_payment_method": "off"},
            "pending_update": None, "schedule": None, "start_date": c["created"],
            "trial_start": None, "trial_end": None, "transfer_data": None})
        c["subscriptions"] = {"object": "list", "data": [world["subscriptions"][-1]], "has_more": False,
                              "url": f"/v1/customers/{cid}/subscriptions"}
        for num, inv in enumerate(docs, 1):
            url = f"https://billing.example.test/invoices/{inv['id']}"
            inv.update(object="invoice", account_country="IT", account_name="Billing Lab",
                       account_tax_ids=[], amount_overpaid=0, amount_shipping=0,
                       automatic_tax={"enabled": False, "status": None, "liability": None},
                       collection_method="send_invoice", customer_name=c["name"], customer_address=address,
                       customer_phone=None, customer_shipping=None, customer_tax_exempt="none",
                       customer_tax_ids=[], default_payment_method=None, default_source=None,
                       default_tax_rates=[], footer=None, hosted_invoice_url=url, invoice_pdf=url+".pdf",
                       livemode=False, number=f"{c['invoice_prefix']}-{num:04}", receipt_number=None,
                       shipping_cost=None, shipping_details=None, subtotal_excluding_tax=inv["subtotal"],
                       total_excluding_tax=inv["total"], total_taxes=[], test_clock=None,
                       period_start=inv["created"]-28*86400, period_end=inv["created"],
                       payment_settings={"default_mandate": None, "payment_method_types": None})
            inv["lines"].update(object="list", has_more=False, url=f"/v1/invoices/{inv['id']}/lines")
            for item in inv["lines"]["data"]:
                item.update(object="line_item", currency="eur", invoice=inv["id"],
                            livemode=False, metadata={}, taxes=[], discount_amounts=[],
                            period={"start": inv["period_start"], "end": inv["period_end"]})
    for charge in world["charges"]:
        inv = invoices[charge["invoice"]]
        ch = charge["id"]
        charge.update(amount_refunded=0, captured=True, refunded=False, disputed=False,
                      billing_details={"email": inv["customer_email"], "name": inv["customer_name"],
                                       "phone": None, "address": {"country": "IT", "city": None}},
                      application=None, application_fee=None, balance_transaction="txn_"+ch[3:],
                      receipt_email=inv["customer_email"], receipt_url=f"https://billing.example.test/receipts/{ch}",
                      failure_code=None, failure_message=None, fraud_details={}, livemode=False,
                      metadata={}, outcome=None, payment_method_details={"type": "customer_balance", "customer_balance": {}},
                      receipt_number=None, refunds={"object": "list", "data": [], "has_more": False,
                                                   "url": f"/v1/charges/{ch}/refunds"},
                      shipping=None, transfer_data=None)


def ci_records(world, repo, index):
    base = f"https://api.example.test/repos/lab/{repo}"
    web = f"https://ci.example.test/lab/{repo}"
    owner = {"login": "lab", "id": 1000+index, "type": "Organization", "site_admin": False,
             "url": "https://api.example.test/orgs/lab", "html_url": "https://ci.example.test/lab"}
    repository = {"id": world["runs"][0]["repository"]["id"], "name": repo, "full_name": f"lab/{repo}",
                  "owner": owner, "html_url": web, "url": base, "private": False, "fork": False,
                  "default_branch": "main"}
    for field, suffix in {"issues": "issues{/number}", "pulls": "pulls{/number}", "commits": "commits{/sha}",
                          "branches": "branches{/branch}", "contents": "contents/{+path}",
                          "labels": "labels{/name}", "releases": "releases{/id}", "tags": "tags",
                          "events": "events", "contributors": "contributors", "languages": "languages",
                          "comments": "comments{/number}", "milestones": "milestones{/number}",
                          "archive": "{archive_format}{/ref}", "hooks": "hooks", "keys": "keys{/key_id}",
                          "forks": "forks", "merges": "merges", "downloads": "downloads"}.items():
        repository[field+"_url"] = f"{base}/{suffix}"
    workflow_id = 300000 + index
    for run in world["runs"]:
        n = run["id"]
        url = f"{base}/actions/runs/{n}"
        date = datetime.fromisoformat(run["created_at"])
        end = (date+timedelta(minutes=5)).isoformat()
        run.update(url=url, html_url=f"{web}/actions/runs/{n}", path=".github/workflows/build.yml",
                   run_attempt=1, workflow_id=workflow_id, workflow_url=f"{base}/actions/workflows/{workflow_id}",
                   jobs_url=url+"/jobs", logs_url=url+"/logs", artifacts_url=url+"/artifacts",
                   check_suite_url=f"{base}/check-suites/{n}", cancel_url=url+"/cancel", rerun_url=url+"/rerun",
                   pull_requests=[], actor=owner, updated_at=end, repository=repository, head_repository=repository)
        for job in world["jobs"][str(n)]:
            jid = job["id"]
            job.update(run_url=url, head_sha=run["head_sha"], url=f"{base}/actions/jobs/{jid}",
                       html_url=f"{web}/actions/runs/{n}/job/{jid}", workflow_name=run["name"],
                       head_branch="main", status="completed", created_at=run["created_at"],
                       started_at=run["created_at"], completed_at=end, labels=["ubuntu-latest"],
                       check_run_url=f"{base}/check-runs/{jid}", runner_id=9000+index,
                       runner_name="linux-build", runner_group_id=1, runner_group_name="Default")
            failed = job["steps"][-1]["name"]
            for i, step in enumerate(job["steps"], 1):
                step.update(number=i, status="completed", started_at=run["created_at"], completed_at=end)
            # Use the same installation/test-log profile as the original simulated CI.
            # Keep the raw diagnostic as a standalone line for exact quotations.
            error = world["logs"][str(jid)].splitlines()[2]
            log = _log(job["name"], failed, [error])
            lines = []
            for tick, line in enumerate(log.splitlines(), 1):
                message = line.split("Z ", 1)[1]
                stamp = (date+timedelta(seconds=tick)).isoformat()
                lines.append(error if message == error else f"{stamp} {message}")
            world["logs"][str(jid)] = "\n".join(lines)
    # Like the original repository, the open backlog includes unrelated work.
    for i, topic in enumerate(("Contributor guide", "License headings", "Example screenshots",
                               "Keyboard shortcuts", "Release notes", "Glossary", "Help text")):
        number = 50000 + index*100 + i
        world["issues"].append({"number": number, "title": f"Refresh {topic.lower()}", "state": "open",
                                "body": f"Editorial maintenance of the {repo} {topic.lower()}.", "labels": []})
        if i < 3:
            world["pulls"].append({"number": number+20, "title": f"Revise {topic.lower()}", "state": "open",
                                   "body": f"Closes #{number}"})
    for rows, kind in ((world["issues"], "issues"), (world["pulls"], "pulls")):
        for row in rows:
            n = row["number"]
            url = f"{base}/{kind}/{n}"
            row.update(url=url, id=1000000+n, node_id=hashlib.sha256(url.encode()).hexdigest()[:24],
                       html_url=f"{web}/{'pull' if kind=='pulls' else kind}/{n}", locked=False,
                       user=owner, labels=[], assignee=None, assignees=[], milestone=None,
                       created_at="2026-12-17T12:00:00Z", updated_at="2026-12-17T12:00:00Z",
                       closed_at=None, comments_url=f"{base}/issues/{n}/comments", author_association="MEMBER")
            if kind == "issues":
                row.update(repository_url=base, events_url=url+"/events", labels_url=url+"/labels{/name}",
                           timeline_url=url+"/timeline", state_reason=None, comments=0, closed_by=None)
            else:
                row.update(diff_url=row["html_url"]+".diff", patch_url=row["html_url"]+".patch",
                           issue_url=f"{base}/issues/{n}", commits_url=url+"/commits",
                           review_comments_url=url+"/comments", statuses_url=url+"/statuses", draft=False,
                           merged_at=None, head={"ref": f"review/{n}", "repo": repository, "user": owner},
                           base={"ref": "main", "repo": repository, "user": owner})
