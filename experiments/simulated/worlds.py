"""The two simulated worlds: what the tools of C4 and C5 can return.

Objects come from the OpenAPI schemas through `synth.Synth`; this module only
fixes the scenario, i.e. the handful of fields each task depends on. Every
field set here is listed in this file, so what is ours stays inspectable.
"""
from __future__ import annotations

from functools import lru_cache

from .synth import Synth, iso, ts

# --------------------------------------------------------------------------- #
# C4 — billing support on a Stripe-shaped API
# --------------------------------------------------------------------------- #

STRIPE_IDS = {"customer": "cus", "charge": "ch", "invoice": "in", "subscription": "sub",
              "refund": "re", "line_item": "il", "subscription_item": "si", "balance_transaction": "txn"}

CUSTOMERS = [
    # email, name, scenario
    ("giulia.ferri@example.com", "Giulia Ferri", "double_charge"),
    ("marco.rinaldi@example.com", "Marco Rinaldi", "failed_payment"),
    ("sara.conti@example.com", "Sara Conti", "cancel"),
    ("luca.moretti@example.com", "Luca Moretti", "refund_pending"),
    ("elena.galli@example.com", "Elena Galli", "unknown_invoice"),
    ("paolo.bruno@example.com", "Paolo Bruno", None),
    ("anna.ricci@example.com", "Anna Ricci", None),
    ("davide.costa@example.com", "Davide Costa", None),
]
PLAN = ("Pro plan, monthly", 2900)


@lru_cache(maxsize=1)
def stripe_world() -> dict:
    st = Synth("stripe-spec3.json", seed=4, id_prefixes=STRIPE_IDS)
    card_schema = st.resolve(st.schema("payment_method_details")["properties"]["card"])
    W = {"customers": [], "charges": [], "invoices": [], "subscriptions": [], "refunds": []}

    def charge(cus, amount, days, status="succeeded", refunded=False, **extra):
        c = st.make("charge", {"customer": cus["id"], "amount": amount, "amount_captured": amount if status == "succeeded" else 0,
                               "amount_refunded": amount if refunded else 0, "refunded": refunded,
                               "currency": "eur", "status": status, "paid": status == "succeeded",
                               "captured": status == "succeeded", "created": ts(days), "livemode": True,
                               "description": PLAN[0], "receipt_email": cus["email"],
                               "failure_code": None, "failure_message": None, "disputed": False}, "charge")
        c["payment_method_details"] = {"type": "card", "card": st.value(card_schema, "card", 2, "charge")}
        c["refunds"] = {"object": "list", "data": [], "has_more": False, "url": f"/v1/charges/{c['id']}/refunds"}
        for k, v in extra.items():
            c[k] = v
        W["charges"].append(c)
        return c

    def invoice(cus, amount, days, status="paid", lines=None, **extra):
        inv = st.make("invoice", {"customer": cus["id"], "customer_email": cus["email"], "currency": "eur",
                                  "amount_due": amount, "amount_paid": amount if status == "paid" else 0,
                                  "amount_remaining": 0 if status == "paid" else amount, "total": amount,
                                  "subtotal": amount, "status": status, "created": ts(days),
                                  "period_end": ts(days), "livemode": True}, "invoice")
        inv["number"] = f"A1B2C3-{len(W['invoices']) + 1:04d}"
        items = []
        for desc, amt in lines or [(PLAN[0], amount)]:
            li = st.make("line_item", {"amount": amt, "currency": "eur", "description": desc,
                                       "invoice": inv["id"], "quantity": 1}, "line_item")
            items.append(li)
        inv["lines"] = {"object": "list", "data": items, "has_more": False, "url": f"/v1/invoices/{inv['id']}/lines"}
        for k, v in extra.items():
            inv[k] = v
        W["invoices"].append(inv)
        return inv

    for email, name, scenario in CUSTOMERS:
        cus = st.make("customer", {"email": email, "name": name, "currency": "eur", "livemode": True,
                                   "created": ts(400), "delinquent": scenario == "failed_payment"}, "customer")
        W["customers"].append(cus)
        sub = st.make("subscription", {"customer": cus["id"], "status": "active", "currency": "eur",
                                       "cancel_at_period_end": False, "cancel_at": None, "canceled_at": None,
                                       "created": ts(400), "livemode": True}, "subscription")
        si = st.make("subscription_item", {"subscription": sub["id"], "quantity": 1,
                                           "current_period_start": ts(6), "current_period_end": ts(-24)},
                     "subscription_item")
        sub["items"] = {"object": "list", "data": [si], "has_more": False,
                        "url": f"/v1/subscription_items?subscription={sub['id']}"}
        W["subscriptions"].append(sub)
        # Three months of normal history for everyone: the noise around each case.
        for m in (66, 36):
            invoice(cus, PLAN[1], m)
            charge(cus, PLAN[1], m)
        if scenario == "double_charge":
            invoice(cus, PLAN[1], 6)
            charge(cus, PLAN[1], 6)
            charge(cus, PLAN[1], 6, created=ts(6) + 47)   # 47 s later, same amount: the duplicate
        elif scenario == "failed_payment":
            invoice(cus, PLAN[1], 6, status="open", next_payment_attempt=ts(-2), attempt_count=2)
            charge(cus, PLAN[1], 6, status="failed", failure_code="card_declined",
                   failure_message="Your card has insufficient funds.",
                   outcome={"network_status": "declined_by_network", "reason": "insufficient_funds",
                            "risk_level": "normal", "seller_message": "The bank returned the decline code `insufficient_funds`.",
                            "type": "issuer_declined"})
            charge(cus, PLAN[1], 4, status="failed", failure_code="card_declined",
                   failure_message="Your card has insufficient funds.")
        elif scenario == "cancel":
            invoice(cus, PLAN[1], 6)
            charge(cus, PLAN[1], 6)
        elif scenario == "refund_pending":
            invoice(cus, 4900, 9, lines=[("Annual onboarding session", 4900)])
            c = charge(cus, 4900, 9, description="Annual onboarding session")
            r = st.make("refund", {"amount": 4900, "charge": c["id"], "currency": "eur", "status": "pending",
                                   "created": ts(5), "reason": "requested_by_customer"}, "refund")
            c["refunds"]["data"].append(r)
            W["refunds"].append(r)
            invoice(cus, PLAN[1], 6)
            charge(cus, PLAN[1], 6)
        elif scenario == "unknown_invoice":
            invoice(cus, PLAN[1] + 900, 6, lines=[(PLAN[0], PLAN[1]), ("Extra storage 50 GB", 900)])
            charge(cus, PLAN[1] + 900, 6)
        else:
            invoice(cus, PLAN[1], 6)
            charge(cus, PLAN[1], 6)
    return W


# --------------------------------------------------------------------------- #
# C5 — CI and issue triage on a GitHub-shaped API
# --------------------------------------------------------------------------- #

REPO = "example-org/storefront-api"
GH_TOP = {"body", "assignees", "author_association", "reactions", "timeline_url", "state_reason", "closed_by", "draft"}
FAIL_TEST = "tests/test_refunds.py::test_partial_refund_rounding"


def _log(job: str, failing_step: str | None, error: list[str]) -> str:
    """A plain-text job log, as the logs endpoint returns it: timestamps,
    setup noise, and, when the job fails, the error lines."""
    lines, t = [], 0
    def w(msg):
        nonlocal t
        t += 1
        lines.append(f"2026-10-26T08:{t // 60:02d}:{t % 60:02d}.{t * 37 % 1000:03d}Z {msg}")
    w(f"##[group]Run {job}")
    for i in range(1, 25):
        w(f"Downloading dependency {i}/24 from https://pypi.example.test/simple/pkg{i}/")
    w("##[endgroup]")
    for i in range(1, 41):
        w(f"tests/test_module_{i:02d}.py::test_case_{i:02d} PASSED [{i * 2:>3}%]")
    if failing_step:
        w(f"##[group]{failing_step}")
        for e in error:
            w(e)
        w(f"##[error]Process completed with exit code 1.")
        w("##[endgroup]")
    else:
        w("All checks passed.")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def github_world() -> dict:
    gh = Synth("github-api.json", seed=5, top_level_optional=GH_TOP)
    users = [f"dev-{n}" for n in ("ada", "bruno", "carla", "dario", "erika")]
    W = {"runs": [], "jobs": {}, "logs": {}, "issues": [], "pulls": []}

    def user(i):
        u = gh.make("simple-user", {"login": users[i % len(users)], "type": "User", "site_admin": False})
        return u

    plan = [
        # days ago, workflow, branch, conclusion, failing job, failing step, error lines
        (6.5, "CI", "main", "success", None, None, None),
        (6.2, "Lint", "main", "success", None, None, None),
        (5.8, "CI", "main", "failure", "test (3.12)", "Run pytest",
         [f"FAILED {FAIL_TEST} - AssertionError: assert Decimal('10.01') == Decimal('10.00')",
          "=========== 1 failed, 412 passed in 93.21s ==========="]),
        (5.1, "Deploy staging", "main", "failure", "deploy", "Wait for rollout",
         ["Error: timed out waiting for the condition: deployment/storefront-api did not become ready in 600s"]),
        (4.9, "CI", "fix/refund-rounding", "success", None, None, None),
        (4.2, "CI", "main", "failure", "test (3.12)", "Run pytest",
         [f"FAILED {FAIL_TEST} - AssertionError: assert Decimal('10.01') == Decimal('10.00')",
          "=========== 1 failed, 412 passed in 95.02s ==========="]),
        (3.6, "Lint", "main", "failure", "ruff", "Run ruff check",
         ["src/storefront/refunds.py:88:5: F841 Local variable `rounded` is assigned to but never used",
          "Found 1 error."]),
        (3.0, "CI", "main", "success", None, None, None),
        (2.4, "CI", "main", "failure", "test (3.13)", "Run pytest",
         [f"FAILED {FAIL_TEST} - AssertionError: assert Decimal('10.01') == Decimal('10.00')",
          "=========== 1 failed, 412 passed in 91.77s ==========="]),
        (1.9, "Deploy staging", "main", "success", None, None, None),
        (1.2, "CI", "chore/bump-ruff", "success", None, None, None),
        (0.6, "CI", "main", "success", None, None, None),
    ]
    files = {"CI": "ci.yml", "Lint": "lint.yml", "Deploy staging": "deploy-staging.yml"}
    jobs_of = {"CI": ["test (3.12)", "test (3.13)", "build"], "Lint": ["ruff"], "Deploy staging": ["deploy"]}
    run_id, job_id = 18_204_000_000, 51_830_000_000
    for n, (days, wf, branch, concl, fjob, fstep, err) in enumerate(plan, 1):
        run_id += 7_311
        run = gh.make("workflow-run", {
            "id": run_id, "name": wf, "display_title": f"{wf} on {branch}", "head_branch": branch,
            "event": "push", "status": "completed", "conclusion": concl, "run_number": 1400 + n,
            "run_attempt": 1, "path": f".github/workflows/{files[wf]}", "created_at": iso(days),
            "updated_at": iso(days, 11), "html_url": f"https://github.example.test/{REPO}/actions/runs/{run_id}"})
        run["actor"] = user(n)
        W["runs"].append(run)
        W["jobs"][run_id] = []
        for jname in jobs_of[wf]:
            job_id += 977
            failed = jname == fjob
            steps_names = ["Set up job", "Checkout", "Set up Python", "Install dependencies",
                           fstep if failed else ("Run pytest" if wf == "CI" and jname != "build" else "Run"),
                           "Post Checkout", "Complete job"]
            steps = []
            for i, sname in enumerate(steps_names, 1):
                bad = failed and sname == fstep
                after = failed and i > steps_names.index(fstep) + 1 and sname != "Complete job"
                steps.append({"name": sname, "status": "completed",
                              "conclusion": "failure" if bad else ("skipped" if after else "success"),
                              "number": i, "started_at": iso(days), "completed_at": iso(days)})
            job = gh.make("job", {"id": job_id, "run_id": run_id, "name": jname, "workflow_name": wf,
                                  "head_branch": branch, "status": "completed",
                                  "conclusion": "failure" if failed else "success",
                                  "started_at": iso(days), "completed_at": iso(days, 11),
                                  "html_url": f"https://github.example.test/{REPO}/actions/runs/{run_id}/job/{job_id}"})
            job["steps"] = steps
            W["jobs"][run_id].append(job)
            W["logs"][job_id] = _log(jname, fstep if failed else None, err or [])

    issue_plan = [
        (142, "Flaky: test_partial_refund_rounding fails intermittently on CI", ["bug", "flaky-test"],
         f"`{FAIL_TEST}` fails on some CI runs with a rounding difference of 0.01. Seen on main."),
        (150, "Staging deploy times out waiting for rollout", ["infra"],
         "The Deploy staging workflow sometimes times out after 600s. Readiness probe may be too strict."),
        (118, "Add pagination to /orders endpoint", ["enhancement"], "Large accounts get slow responses."),
        (121, "Document refund webhooks", ["docs"], "The webhook payload for refunds is not documented."),
        (127, "Upgrade to Python 3.13 in Docker image", ["chore"], "CI already tests 3.13."),
        (133, "Order export CSV has wrong encoding", ["bug"], "Accented characters are garbled in Excel."),
        (139, "Rate limit headers missing on 429", ["bug"], "Clients cannot back off correctly."),
        (145, "Improve error message for invalid coupon", ["enhancement"], "Return the coupon code in the message."),
        (148, "Remove deprecated v1 checkout endpoint", ["chore"], "Scheduled for the next minor release."),
        (151, "Search by customer email is case sensitive", ["bug"], "Lowercase both sides before comparing."),
    ]
    for i, (num, title, labels, body) in enumerate(issue_plan):
        iss = gh.make("issue", {"number": num, "title": title, "state": "open", "body": body,
                                "created_at": iso(30 - i * 2), "updated_at": iso(5 - i * 0.3),
                                "html_url": f"https://github.example.test/{REPO}/issues/{num}",
                                "locked": False, "comments": i % 4})
        iss["labels"] = [gh.make("label", {"name": l, "default": False}) for l in labels]
        iss["user"] = user(i)
        iss.pop("pull_request", None)
        W["issues"].append(iss)

    pr_plan = [
        (155, "Fix rounding in partial refunds", "fix/refund-rounding",
         "Use Decimal quantize with ROUND_HALF_EVEN in refunds. Fixes #142."),
        (157, "Bump ruff to 0.9 and fix new lint errors", "chore/bump-ruff",
         "Updates ruff; removes an unused variable in refunds.py."),
        (153, "Add pagination to /orders", "feat/orders-pagination", "Implements #118."),
        (156, "Docs: refund webhooks", "docs/refund-webhooks", "Closes #121."),
    ]
    for i, (num, title, branch, body) in enumerate(pr_plan):
        pr = gh.make("pull-request-simple", {"number": num, "title": title, "state": "open", "body": body,
                                              "draft": False, "created_at": iso(5 - i), "updated_at": iso(1),
                                              "html_url": f"https://github.example.test/{REPO}/pull/{num}"})
        pr["head"] = dict(pr.get("head") or {}, ref=branch, label=f"example-org:{branch}")
        pr["base"] = dict(pr.get("base") or {}, ref="main", label="example-org:main")
        pr["user"] = user(i + 2)
        W["pulls"].append(pr)
    return W
