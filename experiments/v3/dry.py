"""Exercise the frozen tasks, real local MCP tools and exposure policies with a scripted agent."""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import re

from tracetrim.contract import ToolCall, ToolResult
from tracetrim.engine import Engine
from tracetrim.grounding import unsupported
from tracetrim.runtime import Policy
from tracetrim.traces import load
from experiments.checks import check
from . import coordinator
from .tools import TaskTools, task_data
from .worlds import DATA, write


def args_key(tool, args):
    return tool, json.dumps(args, sort_keys=True)


async def reference_calls(task, tools):
    """Capture original answers through MCP, including the detail endpoints and empty lists."""
    calls = {}

    async def call(tool, **args):
        text = await tools.call(tool, args)
        if tools.last_is_error or text.startswith("Error"):
            raise ValueError(f"{task}: failed reference call {tool}")
        calls[args_key(tool, args)] = text
        return json.loads(text) if tool != "gh_download_job_logs" else text

    data = task_data(task)
    if task.startswith("C4"):
        for email in re.findall(r"[\w.]+@example\.test", data["prompt"]):
            customer, = (await call("stripe_search_customers", email=email))["data"]
            cid = customer["id"]
            invoices = (await call("stripe_list_invoices", customer=cid))["data"]
            charges = (await call("stripe_list_charges", customer=cid))["data"]
            await call("stripe_list_subscriptions", customer=cid)
            assert len(invoices) == 2
            for inv in invoices:
                detail = await call("stripe_retrieve_invoice", invoice=inv["id"])
                assert detail == inv and sum(l["amount"] for l in inv["lines"]["data"]) == inv["total"]
                paid = sum(c["amount_captured"] for c in charges if c["invoice"] == inv["id"])
                assert paid == inv["amount_paid"]
                assert inv["amount_remaining"] == (0 if inv["status"] == "void" else inv["total"] - paid)
            for charge in charges:
                assert await call("stripe_retrieve_charge", charge=charge["id"]) == charge
                await call("stripe_list_refunds", charge=charge["id"])
            key, = [k for k in data["key"]["ticket"] if k["customer_id"] == cid]
            assert [e["oggetto"] for e in key["evidenze"]] == [i["id"] for i in reversed(invoices)]
            latest = invoices[0]
            for field, label in (("total", "totale"), ("amount_paid", "pagato"), ("amount_remaining", "residuo")):
                value = latest[field]
                assert f"{label} {value//100},{value%100:02d} EUR" in key["diagnosi"]
    else:
        runs = (await call("gh_list_workflow_runs", branch="main", status="failure"))["workflow_runs"]
        issues = await call("gh_list_issues", state="open")
        pulls = await call("gh_list_pull_requests", state="open")
        for issue in issues:
            assert await call("gh_get_issue", issue_number=issue["number"]) == issue
        for run in runs:
            job, = (await call("gh_list_jobs_for_workflow_run", run_id=run["id"]))["jobs"]
            log = await call("gh_download_job_logs", job_id=job["id"])
            cause, = [c for c in data["key"]["root_causes"] if run["id"] in c["runs"]]
            assert cause["error"] in log.splitlines() and cause["job"] == job["name"]
            assert cause["step"] in [s["name"] for s in job["steps"] if s["conclusion"] == "failure"]
            issue, = [i for i in issues if i["number"] in cause["related_issues"]]
            assert issue["body"] == cause["next_action"]
            assert [p["number"] for p in pulls if f"#{issue['number']}" in p["body"]] == cause["related_pull_requests"]
    assert {tool for tool, _ in calls} == {s["name"] for s in tools.schemas}
    return calls


def evidence_rows(raw):
    return [{"tool": t, "arguments": json.loads(a), "output": raw[t, a]} for t, a in sorted(raw)]


def stub(task, arm, raw, path):
    """The fake agent only reads delivered fields. Original returns are for the recorder/checkers."""
    data = task_data(task)
    case = task[:2]
    policy = None if arm == "R" else Policy.load(DATA / "policies" / case / arm / "config.json")
    engine = Engine(policy=policy, breakdown=False)
    events = [{"kind": "prompt", "text": data["prompt"]}]
    counts = Counter()

    def ask(tool, **args):
        original = raw[args_key(tool, args)]
        obs = engine.observe(ToolCall(tool, args), ToolResult(original))
        events.append({"kind": "call", "tool": tool, "arguments": args, "output": original, "sent": obs.text})
        counts[obs.exposure] += 1
        if obs.call_id and not counts["expand"]:
            recovered = engine.expand(obs.call_id, ["*"])
            assert json.loads(recovered), "expand must recover actual fields"
            events.append({"kind": "call", "tool": "expand", "arguments": {"call_id": obs.call_id, "fields": ["*"]},
                           "output": recovered})
            counts["expand"] += 1
        if arm == "R":
            assert original == obs.text
        return obs.text.split("\n[trimmed]")[0]

    if case == "C4":
        tickets = []
        for i, email in enumerate(re.findall(r"[\w.]+@example\.test", data["prompt"]), 1):
            customer, = json.loads(ask("stripe_search_customers", email=email))["data"]
            cid = customer["id"]
            invoices = json.loads(ask("stripe_list_invoices", customer=cid))["data"]
            ask("stripe_list_charges", customer=cid)
            tickets.append({"id": f"T{i}", "customer_id": cid, "diagnosi": "Stub.",
                            "evidenze": [{"oggetto": invoices[0]["id"]}]})
        answer = {"ticket": tickets}
    else:
        runs = json.loads(ask("gh_list_workflow_runs", branch="main", status="failure"))["workflow_runs"]
        causes = []
        for run in runs:
            job, = json.loads(ask("gh_list_jobs_for_workflow_run", run_id=run["id"]))["jobs"]
            log = ask("gh_download_job_logs", job_id=job["id"])
            diagnostic, = [line for line in log.splitlines() if not line.startswith("2026-")]
            causes.append({"runs": [run["id"]], "job": job["name"], "error": diagnostic})
        answer = {"root_causes": causes}
    # An identical masking challenge in every arm; never add this to a live answer.
    answer["note"] = "expand(call_id=c1234abcd) [trimmed]"
    events.append({"kind": "message", "text": json.dumps(answer, ensure_ascii=False)})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as f:
        for event in events:
            print(json.dumps(event, ensure_ascii=False), file=f)
    gates = check(path, case)
    assert gates["structure"] and gates["facts"] and gates["facts_ok"] == gates["facts"]
    unsupported(load(path))
    return counts


async def run(directory, reference_only):
    refs = DATA / "reference"
    slots = coordinator.schedule()
    materials = {}
    for task in sorted({s.split("/")[0] for s in slots}):
        async with TaskTools(task) as tools:
            original = await reference_calls(task, tools)
        rows = evidence_rows(original)
        ref = refs / f"{task}.json"
        if reference_only:
            write(ref, rows)
        else:
            assert json.loads(ref.read_text()) == rows
        materials[task] = original
    if reference_only:
        return

    # An independent disposable map for stubs keeps live pseudonyms sealed.
    directory.mkdir(parents=True, exist_ok=False)
    sealed = coordinator.seal(directory)
    totals = {arm: Counter() for arm in ("R", "P", "I")}
    for slot in slots:
        task, rep, arm = slot.split("/")
        totals[arm].update(stub(task, arm, materials[task], directory / "traces" / (slot + ".jsonl")))
    assert all(totals[a]["filtered"] and totals[a]["expand"] for a in ("P", "I"))
    hints = coordinator.packets(directory, directory / "traces", refs, sealed)
    assert hints == len(slots)
    for i in (1, 2):
        scores = {}
        for p in sorted((directory / f"pass-{i}").glob("*.json")):
            packet = coordinator.load(p)
            assert set(packet) == {"id", "task", "rubric", "answer", "evidence"}
            assert packet["id"] == p.stem and not coordinator.CUE.search(packet["answer"])
            scores[p.stem] = dict.fromkeys(coordinator.DIMS, 2)
        assert len(scores) == len(slots)
        if i == 2:
            scores[next(iter(scores))]["action"] = 4
        write(directory / f"scores-{i}.json", scores)
    assert coordinator.adjudicate(directory, directory / "traces", refs, sealed) == 1
    third, = (directory / "pass-3").glob("*.json")
    write(directory / "scores-3.json", {third.stem: dict.fromkeys(coordinator.DIMS, 3)})
    assert coordinator.finish(directory, sealed) == len(slots)
    judged = coordinator.load(directory / "judged.json")
    assert sum(r["action"] == 3 for r in judged["scores"].values()) == 1
    assert judged["agreement"]["action"]["exact"] == (len(slots)-1) / len(slots)
    print(json.dumps({"stub_runs": len(slots), "worlds": len(materials), "artificial_cues": hints,
                      "cards_per_pass": len(scores), "third_pass_cards": 1, "exposures": totals}, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--reference-only", action="store_true")
    g.add_argument("--dry-run", type=Path)
    a = p.parse_args()
    asyncio.run(run(a.dry_run, a.reference_only))
