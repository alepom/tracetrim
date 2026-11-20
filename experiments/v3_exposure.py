"""Replay A1–A8 requests against each synthetic v3 task. No model client.

PYTHONPATH=src TRACETRIM_TRACES=... python -m experiments.v3_exposure --output out/exposure.json
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from tracetrim.contract import ToolCall, ToolResult
from tracetrim.engine import Engine, MARK
from tracetrim.measure import tokenizer
from tracetrim.runtime import Policy
from experiments.paths import STUDY_TRACES, PROMPTS
from experiments.v3.tools import TaskTools, task_data
from experiments.v3.worlds import ARMS, CASES, DATA, write


def history(case):
    hashes = json.loads((DATA / "training.json").read_text())
    runs = []
    for i in range(1, 9):
        path = STUDY_TRACES / case / f"A{i}.jsonl"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == hashes[f"{case}/{path.name}"]
        calls = []
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event["kind"] == "call":
                args = event["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                assert isinstance(args, dict) and isinstance(event["output"], str) and event["output"]
                calls.append((event["tool"], args, event["output"]))
        assert calls
        runs.append(calls)
    return runs


def measure(case, runs):
    count = tokenizer()
    total = {}
    for arm in ARMS:
        per_tool = {}
        policy = None if arm == "R" else Policy.load(DATA / "policies" / case / arm / "config.json")
        for calls in runs:
            engine = Engine(policy=policy, breakdown=False)
            for tool, args, original in calls:
                obs = engine.observe(ToolCall(tool, args), ToolResult(original))
                text = obs.text
                if obs.call_id:
                    body, annotation = text.rsplit(MARK, 1)
                    text = body + MARK + annotation.replace(obs.call_id, "c00000000", 1)
                row = per_tool.setdefault(tool, Counter())
                row["tokens"] += count(text)
                row["calls"] += 1
                row[obs.exposure] += 1
        total[arm] = {"total": sum(per_tool.values(), Counter()), "tools": per_tool}
    return total


def bind(old, new, mapping):
    """Retain list rank and parent association. Cycle ranks if a new list is shorter."""
    for i, item in enumerate(old):
        if new:
            mapping[item["id"]] = new[i % len(new)]["id"]


async def replay(task, runs):
    case = task[:2]
    email_map = {}
    if case == "C4":
        emails = re.findall(r"[\w.]+@example\.com", (PROMPTS / "c4-user.md").read_text())
        customers = task_data(task)["world"]["customers"]
        assert len(set(emails)) == len(customers) == 5
        email_map = dict(zip(emails, [c["email"] for c in customers]))
    results = []
    async with TaskTools(task) as tools:
        for calls in runs:
            ids = {}
            responses = []
            for tool, original_args, original in calls:
                args = dict(original_args)
                if "email" in args:
                    args["email"] = email_map[args["email"]]
                for k in ("customer", "charge", "run_id", "job_id"):
                    if k in args:
                        args[k] = ids[args[k]]
                text = await tools.call(tool, args)
                if tools.last_is_error or text.startswith("Error"):
                    raise RuntimeError(f"{task}: invalid result from {tool}")
                if tool != "gh_download_job_logs":
                    a, b = json.loads(original), json.loads(text)
                    assert not isinstance(b, dict) or ("error" not in b and b.get("status") != "404")
                    if tool in ("stripe_search_customers", "stripe_list_charges"):
                        bind(a["data"], b["data"], ids)
                    if tool == "gh_list_workflow_runs":
                        bind(a["workflow_runs"], b["workflow_runs"], ids)
                    if tool == "gh_list_jobs_for_workflow_run":
                        bind([j for j in a["jobs"] if j["conclusion"] == "failure"],
                             [j for j in b["jobs"] if j["conclusion"] == "failure"], ids)
                responses.append((tool, args, text))
            results.append(responses)
    return measure(case, results)


async def main(path):
    report = {}
    for case in CASES:
        runs = history(case)
        original = measure(case, runs)
        tasks = {}
        for i in range(1, 11):
            task = f"{case}-{i:02}"
            tasks[task] = {"arms": await replay(task, runs)}
        summed = {arm: sum((t["arms"][arm]["total"] for t in tasks.values()), Counter()) for arm in ARMS}
        for arm in ARMS:
            assert summed[arm]["calls"] == 10 * original[arm]["total"]["calls"]
        p, r = summed["P"], summed["R"]
        passed = p["tokens"] <= .70*r["tokens"] and p["full_cheaper"] <= .25*p["calls"]
        report[case] = {"historical": original, "tasks": tasks, "pooled": summed, "passes": passed}
        print(case, json.dumps(summed), "PASS" if passed else "FAIL", flush=True)
    write(path, report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output))
