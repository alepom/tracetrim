"""Overlap of the v3b tasks with protocol v3 and the historical C4 inputs (no spend).

Identifiers and email addresses of every v3b world and prompt must not appear in the v3 material,
the v3 campaign traces, the original study's C4 traces (A, B, C, E2 and the rest) or the C4 prompts;
no v3b ticket question may repeat a v3 or historical question."""
import json
import re

from experiments.checks import STRIPE_ID
from experiments.paths import PROMPTS, ROOT, STUDY_TRACES
from experiments.v3.worlds import BILLING, DATA as V3_DATA

from .worlds import CANDIDATES, DATA

EMAIL = re.compile(r"[\w.]+@example\.test")


def tokens(text):
    return set(STRIPE_ID.findall(text)) | set(EMAIL.findall(text))


def main():
    ours, questions = set(), set()
    for i in range(1, CANDIDATES + 1):
        text = (DATA / f"C4b-{i:02}.json").read_text()
        ours |= tokens(text)
        prompt = json.loads(text)["prompt"]
        questions |= {line.split(": ", 1)[1] for line in prompt.splitlines()[1:]}
    sources = sorted(V3_DATA.rglob("*.json")) + sorted((ROOT / "out/v3/traces").rglob("*.jsonl")) \
        + sorted(STUDY_TRACES.glob("C4*/*.jsonl")) + sorted(PROMPTS.glob("c4*"))
    clash, old_questions = set(), {q for _, q, _, _ in BILLING}
    for path in sources:
        text = path.read_text(errors="replace")
        clash |= ours & tokens(text)
        old_questions |= {q for q in questions if q in text}
    print(json.dumps({"v3b_identifiers_and_emails": len(ours), "files_compared": len(sources),
                      "shared": len(clash), "repeated_questions": len(questions & old_questions)}))
    if clash or questions & old_questions:
        raise SystemExit("overlap found")


if __name__ == "__main__":
    main()
