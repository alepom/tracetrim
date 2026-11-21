"""The blind judge of the v3 campaign: a Gemini model on Vertex AI, one call per task (PAID).

  python -m experiments.v3.judge_gemini prepare 1    # build the requests of pass 1, check them, no spend
  python -m experiments.v3.judge_gemini estimate 1   # Vertex token count of those requests (not billed)
  python -m experiments.v3.judge_gemini run 1 [N]    # judge the next N groups of pass 1 (PAID)
  python -m experiments.v3.judge_gemini status 1

Deviations from protocol-v3.md decided by the author before any score (experiments/v3/DEVIATIONS.md):
the judge is this script, not an Astra session, and the cards of a pass are judged grouped by task.
For every task: one call, the full rubric as system instruction, the task and its reference evidence
once, then its answers under neutral labels A, B, ... in an order drawn with the pass's seed. Each
answer is scored on its own against the rubric and the evidence, never ranked against the others.

Labels map to the cards' opaque ids, inside this script: that is not the arm mapping, which this
script never reads. Every pass is independent: nothing of another pass enters a request.

Private and git-ignored: out/v3/judge/pass-N/ (plan, requests, raw responses), the text copy of the
requests checked against never_publish before sending, out/v3/judge-ledger.jsonl (usage and cost of
every attempt). When every group of a pass is scored, out/v3/scores-N.json in the format the
coordinator reads ({id: {correctness, completeness, action}}).

A malformed answer (not JSON, wrong labels, a score outside 0-4) is never repaired: the call is made
once more, and if that fails too the group is reported in failures.json without scores.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from experiments.caps import caps
from experiments.paths import ROOT

from . import coordinator
from .live import Where as Campaign
from .live import spent as campaign_spent

PRIVATE = coordinator.PRIVATE
PRICES = ROOT / "experiments/v3/prices-judge.json"
NEVER_PUBLISH = Path.home() / "progetti/tracetrim/.private/never_publish.txt"
DIMS = coordinator.DIMS
SEEDS = {1: 120401, 2: 120402, 3: 120403}        # the order of the answers within a task, one seed per pass
MAX_ATTEMPTS = 2                                 # a malformed or failed answer is asked once more, then reported
LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

INSTRUCTION = """You are the blind judge of a study on the answers of a tool-using agent.

The user message contains one task, the original reference tool returns for that task (the same for every
answer), and several independent answers to that task under neutral labels. The answers were produced in
separate runs; their order and labels carry no information.

Score EACH answer ON ITS OWN, against the rubric below and the reference evidence, as if it were the only
answer you had read. Do not compare, rank or calibrate the answers against each other, and do not let one
answer change what you expect of another. Give each of the three dimensions an integer from 0 to 4, and a
brief rationale (at most three sentences) for that answer.

Return only the JSON object required by the response schema: one entry per label.

# Rubric

"""


class Budget(SystemExit):
    """A cap stops the judging: nothing new starts."""


class Malformed(ValueError):
    pass


# -- private places ------------------------------------------------------------

class Where:
    def __init__(self, root: Path = PRIVATE):
        self.root = Path(root)
        self.judge = self.root / "judge"
        self.ledger = self.root / "judge-ledger.jsonl"
        self.reviewed = self.root / "judge-warning-reviewed"
        self.campaign = Campaign(self.root)

    def cards(self, n: int) -> Path:
        return self.root / f"pass-{n}"

    def pass_dir(self, n: int) -> Path:
        return self.judge / f"pass-{n}"

    def scores(self, n: int) -> Path:
        return self.root / f"scores-{n}.json"


def _lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def _append(path: Path, line: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def prices() -> dict:
    p = json.loads(PRICES.read_text())
    if not p.get("verified"):
        raise SystemExit(f"{PRICES.name}: the price of {p['model']} is not verified: paid judging is refused")
    return p


def cost(usage: dict, p: dict) -> float:
    cached = usage.get("cached", 0)
    return ((usage["input"] - cached) * p["input_per_mtok"] + cached * p["cached_input_per_mtok"]
            + usage["output"] * p["output_per_mtok"]) / 1e6


def spent(where: Where) -> float:
    """The whole v3 study: the campaign's ledger and the judge's."""
    return campaign_spent(where.campaign) + sum(e["cost_usd"] for e in _lines(where.ledger))


def may_start(where: Where, limits: tuple[float, float] | None = None) -> float:
    warn, stop = limits or caps("v3")
    if stop <= 0:
        raise Budget("no v3 caps in experiments/caps.local.json: paid judging is refused")
    total = spent(where)
    if total >= stop:
        raise Budget(f"stop rule: the v3 study is at or above the stop cap ({total:.2f} USD)")
    if total >= warn and not where.reviewed.exists():
        raise Budget(f"warning: the v3 study is at or above the warning ({total:.2f} USD). Report it and record "
                     f"the review in {where.reviewed} before going on.")
    return total


# -- groups, labels, requests --------------------------------------------------

def key(task: str) -> str:
    return hashlib.sha256(task.encode()).hexdigest()[:12]


def plan(cards_dir: Path, seed: int) -> list[dict]:
    """Groups of cards by task text, in a fixed order; within each, the cards shuffled with the seed
    and labelled A, B, ... Deterministic: the same cards and seed give the same plan."""
    groups: dict[str, list[dict]] = {}
    for p in sorted(cards_dir.glob("*.json")):
        card = json.loads(p.read_text())
        if card["id"] != p.stem:
            raise ValueError(f"{p.name}: id differs from the file name")
        groups.setdefault(card["task"], []).append(card)
    rng = random.Random(seed)
    out = []
    for task in sorted(groups, key=key):
        cards = sorted(groups[task], key=lambda c: c["id"])
        first = cards[0]
        if any(c["evidence"] != first["evidence"] or c["rubric"] != first["rubric"] for c in cards):
            raise ValueError("cards of one task with different evidence or rubric")
        rng.shuffle(cards)
        if len(cards) > len(LABELS):
            raise ValueError("too many answers for one task")
        out.append({"group": key(task), "labels": {LABELS[i]: c["id"] for i, c in enumerate(cards)},
                    "task": task, "rubric": first["rubric"], "evidence": first["evidence"],
                    "answers": {LABELS[i]: c["answer"] for i, c in enumerate(cards)}})
    return out


def request(group: dict) -> dict:
    """System instruction and user text of one call."""
    parts = ["# Task given to the agent", "", group["task"].strip(), "",
             "# Original reference tool returns (the same for every answer)", "",
             json.dumps(group["evidence"], ensure_ascii=False, indent=1), "",
             f"# Answers to score, each on its own ({len(group['answers'])})"]
    for label, answer in group["answers"].items():
        parts += ["", f"## Answer {label}", "", answer.strip() or "(empty answer)"]
    return {"system": INSTRUCTION + group["rubric"], "user": "\n".join(parts) + "\n"}


def schema(labels) -> dict:
    score = {"type": "integer", "enum": [0, 1, 2, 3, 4]}
    entry = {"type": "object", "properties": {"correctness": score, "completeness": score, "action": score,
                                              "rationale": {"type": "string"}},
             "required": ["correctness", "completeness", "action", "rationale"]}
    return {"type": "object", "properties": {label: entry for label in labels}, "required": list(labels)}


def parse(text: str, labels) -> dict:
    """The scores of one response, or Malformed. Nothing is repaired or guessed."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as e:
        raise Malformed(f"not JSON: {e}") from e
    if not isinstance(data, dict) or set(data) != set(labels):
        raise Malformed(f"labels {sorted(data) if isinstance(data, dict) else type(data).__name__}, "
                        f"expected {sorted(labels)}")
    out = {}
    for label, row in data.items():
        if not isinstance(row, dict) or any(type(row.get(d)) is not int or not 0 <= row[d] <= 4 for d in DIMS):
            raise Malformed(f"label {label}: invalid scores")
        out[label] = {d: row[d] for d in DIMS}
    return out


def prepare(where: Where, n: int) -> list[dict]:
    """The plan and the requests of a pass, written privately, and the text copy checked."""
    folder = where.pass_dir(n)
    folder.mkdir(parents=True, exist_ok=True)
    groups = plan(where.cards(n), SEEDS[n])
    stored = folder / "plan.json"
    labels = [{"group": g["group"], "labels": g["labels"]} for g in groups]
    if stored.exists():
        if json.loads(stored.read_text()) != labels:
            raise ValueError(f"{stored}: the cards or the seed changed since the plan was written")
    else:
        stored.write_text(json.dumps(labels, indent=1) + "\n")
    text = folder / "requests.txt"
    text.write_text("".join(f"=== {g['group']}\n{r['system']}\n{r['user']}\n"
                            for g in groups for r in [request(g)]), encoding="utf-8")
    private_terms(text)
    return groups


def private_terms(path: Path) -> None:
    """The never_publish check on the text sent to the provider: must count 0. The list is never printed."""
    if not NEVER_PUBLISH.exists():
        raise SystemExit("never_publish list not found: nothing is sent")
    r = subprocess.run(["grep", "-c", "-i", "-F", "-f", str(NEVER_PUBLISH), str(path)], capture_output=True, text=True)
    if r.stdout.strip() != "0":
        raise SystemExit(f"{path}: the never_publish check does not give 0: nothing is sent")


# -- the call ------------------------------------------------------------------

def config(req: dict, labels, p: dict):
    from google.genai import types
    thinking = types.ThinkingConfig(thinking_level=getattr(types.ThinkingLevel, p["thinking_level"]))
    return types.GenerateContentConfig(system_instruction=req["system"], response_mime_type="application/json",
                                       response_json_schema=schema(labels), thinking_config=thinking)


async def generate(client, model: str, req: dict, cfg) -> tuple[object, list]:
    """One request, made again only on the transient statuses of experiments/gemini.py, every repeat recorded."""
    from experiments.gemini import ATTEMPTS, RETRY_STATUS
    retries = []
    for attempt in range(ATTEMPTS):
        try:
            return await client.aio.models.generate_content(model=model, contents=req["user"], config=cfg), retries
        except Exception as e:                       # noqa: BLE001
            code = getattr(e, "code", None)
            if code not in RETRY_STATUS or attempt == ATTEMPTS - 1:
                raise
            retries.append({"attempt": attempt + 1, "code": code, "status": str(getattr(e, "status", ""))})
            await asyncio.sleep(5.0 * 2 ** attempt)


def text_of(r) -> str:
    cand = r.candidates[0] if getattr(r, "candidates", None) else None
    parts = cand.content.parts if cand is not None and cand.content is not None and cand.content.parts else []
    return "".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))


def usage_of(r) -> dict:
    u = getattr(r, "usage_metadata", None)
    if u is None:
        return {"input": 0, "cached": 0, "output": 0}
    return {"input": u.prompt_token_count or 0, "cached": u.cached_content_token_count or 0,
            "output": (u.candidates_token_count or 0) + (u.thoughts_token_count or 0),
            "thinking": u.thoughts_token_count or 0}


def judged(where: Where, n: int) -> dict:
    """group -> its scores by label, for the groups closed in this pass."""
    return {e["group"]: e["scores"] for e in _lines(where.pass_dir(n) / "results.jsonl")}


def attempts(where: Where, n: int, group: str) -> int:
    return sum(1 for e in _lines(where.ledger) if e["pass"] == n and e["group"] == group)


async def judge_group(where: Where, n: int, group: dict, client, p: dict, limits=None) -> dict | None:
    """At most MAX_ATTEMPTS calls; the scores, or None with the group in failures.json."""
    labels = list(group["labels"])
    req = request(group)
    folder = where.pass_dir(n)
    while attempts(where, n, group["group"]) < MAX_ATTEMPTS:
        may_start(where, limits)
        k = attempts(where, n, group["group"]) + 1
        stem = folder / f"{group['group']}.{k}"
        stem.with_suffix(".request.json").write_text(json.dumps(req | {"labels": labels, "model": p["model"]},
                                                                ensure_ascii=False, indent=1) + "\n")
        t0 = time.time()
        line = {"pass": n, "group": group["group"], "attempt": k, "model": p["model"],
                "thinking_level": p["thinking_level"],
                "started": datetime.fromtimestamp(t0, timezone.utc).isoformat(timespec="seconds")}
        try:
            r, retries = await generate(client, p["model"], req, config(req, labels, p))
        except Exception as e:                       # noqa: BLE001
            _append(where.ledger, line | {"ok": False, "reason": f"{type(e).__name__}: {str(e)[:300]}",
                                          "usage": {"input": 0, "cached": 0, "output": 0}, "cost_usd": 0.0})
            continue
        usage, text = usage_of(r), text_of(r)
        cand = r.candidates[0] if getattr(r, "candidates", None) else None
        stem.with_suffix(".response.json").write_text(json.dumps(
            {"text": text, "usage": usage, "finish_reason": str(getattr(cand, "finish_reason", None)),
             "model_version": getattr(r, "model_version", None), "retries": retries}, ensure_ascii=False, indent=1) + "\n")
        try:
            scores = parse(text, labels)
            reason = None
        except Malformed as e:
            scores, reason = None, str(e)
        _append(where.ledger, line | {"ok": scores is not None, "reason": reason, "usage": usage,
                                      "retries": len(retries), "seconds": round(time.time() - t0, 1),
                                      "model_version": getattr(r, "model_version", None),
                                      "cost_usd": round(cost(usage, p), 6)})
        if scores is not None:
            _append(folder / "results.jsonl", {"group": group["group"], "attempt": k, "scores": scores})
            return scores
    fails = folder / "failures.json"
    gone = json.loads(fails.read_text()) if fails.exists() else {}
    gone[group["group"]] = [e.get("reason") for e in _lines(where.ledger) if e["pass"] == n and e["group"] == group["group"]]
    fails.write_text(json.dumps(gone, indent=1) + "\n")
    return None


def sheet(where: Where, n: int, groups: list[dict]) -> Path | None:
    """scores-N.json once every group of the pass is scored: label scores back to card ids."""
    done = judged(where, n)
    if set(done) != {g["group"] for g in groups}:
        return None
    out = {}
    for g in groups:
        for label, card in g["labels"].items():
            out[card] = done[g["group"]][label]
    path = where.scores(n)
    if path.exists():
        if json.loads(path.read_text()) != out:
            raise ValueError(f"{path} exists with other scores: a locked sheet is never rewritten")
        return path
    path.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    return path


async def run(where: Where, n: int, client, p: dict, limit: int | None = None, limits=None) -> dict:
    groups = prepare(where, n)
    failures = where.pass_dir(n) / "failures.json"
    todo = [g for g in groups if g["group"] not in judged(where, n)
            and g["group"] not in (json.loads(failures.read_text()) if failures.exists() else {})]
    for g in todo[:limit] if limit is not None else todo:
        scores = await judge_group(where, n, g, client, p, limits)
        print(f"  pass {n} group {g['group']}: {'scored' if scores else 'FAILED, see failures.json'}", flush=True)
    return status(where, n)


def status(where: Where, n: int) -> dict:
    folder = where.pass_dir(n)
    plan_ = json.loads((folder / "plan.json").read_text()) if (folder / "plan.json").exists() else []
    fails = json.loads((folder / "failures.json").read_text()) if (folder / "failures.json").exists() else {}
    calls = [e for e in _lines(where.ledger) if e["pass"] == n]
    return {"pass": n, "groups": len(plan_), "scored": len(judged(where, n)), "failed": sorted(fails),
            "calls": len(calls), "malformed_or_failed_calls": sum(not e["ok"] for e in calls),
            "sheet": where.scores(n).exists()}


def estimate(where: Where, n: int, client, model: str) -> dict:
    """Vertex's own token count of the requests (count_tokens: nothing generated, nothing billed)."""
    total = 0
    for g in prepare(where, n):
        req = request(g)
        total += client.models.count_tokens(model=model, contents=req["system"] + "\n" + req["user"]).total_tokens
    return {"pass": n, "model": model, "input_tokens": total}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["prepare", "estimate", "run", "status"])
    ap.add_argument("pass_", metavar="PASS", type=int, choices=[1, 2, 3])
    ap.add_argument("count", nargs="?", type=int)
    ap.add_argument("--model", help="estimate only: another model to count with")
    a = ap.parse_args(argv)
    where = Where()
    if a.command == "status":
        print(json.dumps(status(where, a.pass_), indent=2))
        return
    if a.command == "prepare":
        groups = prepare(where, a.pass_)
        print(f"pass {a.pass_}: {len(groups)} groups, {sum(len(g['labels']) for g in groups)} cards; "
              f"never_publish check 0")
        return
    from experiments.gemini import vertex_client
    client = vertex_client()
    if a.command == "estimate":
        print(json.dumps(estimate(where, a.pass_, client, a.model or json.loads(PRICES.read_text())["model"])))
        return
    from .live import verify_frozen
    verify_frozen()
    p = prices()
    may_start(where)
    print(json.dumps(asyncio.run(run(where, a.pass_, client, p, a.count)), indent=2))
    groups = plan(where.cards(a.pass_), SEEDS[a.pass_])
    path = sheet(where, a.pass_, groups)
    print(f"score sheet: {path}" if path else "score sheet: not yet (groups left or failed)")


if __name__ == "__main__":
    main()
