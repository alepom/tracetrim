# Protocol v3 · Deviations

Written on 21 November 2026, **after** the 120 answers were collected and the two blind card sets were
packed, and **before** any answer was scored. The commit that adds this file precedes the first request to the
judge. The arm mapping (`out/v3/mapping.json`) was sealed in 12a and has not been opened.

## D1 · The judge is a Gemini model on Vertex AI, called by a script

**Frozen text.** Astra judges every answer in two separate sessions with no shared memory, plus a fresh
session for adjudication.

**Change.** The judge is `gemini-3.8-flash` on Vertex AI, global endpoint, thinking level `HIGH`, which is
the highest level the SDK offers. The judge is called by `experiments/v3/judge_gemini.py`:
- each pass is a separate series of stateless calls;
- the adjudication pass uses fresh calls too;
- nothing of one pass, scores included, enters a request of another;
- other generation settings are left at the model's defaults;
- the output is structured with a JSON schema. For each answer the judge returns the three integer
  dimensions (0–4) and a brief rationale. The rationale is kept privately and never used in scoring.

**Why.** It is the author's decision. A scripted judge is reproducible and leaves an auditable record of
every request and response. Among Pro-tier models no list price could be documented, so by the rule set
before any score the Flash model was chosen, at its documented list price
(`experiments/v3/prices-judge.json`).

**Blinding.** It is unchanged:
- the script reads only the anonymous card sets, `out/v3/pass-N/`, built by the frozen coordinator, with
  arm cues scrubbed from the answers;
- it never reads the mapping, the traces or the generation metadata;
- the Claude Code session that ran the campaign executes the script but gives no scores, and does not read the
  judge's responses before both score sheets are locked.

**Change in the stated limitation.** The agent is an OpenAI model and the judge a Google model, so the
same-family preference named in the protocol does not apply. A judge that is a language model, aware of
the task design but not of the arm, remains a limitation.

## D2 · Cards are judged grouped by task

**Frozen text.** Each packet is judged on its own.

**Change.** One call per task and pass. The call contains:
- the rubric in full as system instruction, preceded by a fixed judging instruction;
- the task text and the reference evidence once, since they are identical for every answer to the task;
- that task's answers under neutral labels A, B, … (six in passes 1 and 2), in an order drawn with a
  fixed seed per pass: 120401, 120402 and 120403.

The instruction requires scoring **each answer on its own**, against the rubric and the evidence, without
comparing, ranking or calibrating the answers against each other. Labels map to opaque card IDs inside
the script, and the score sheets keep the coordinator's format (`scores-N.json`, keyed by card ID).

**Why.** The evidence, about 50,000 tokens per task, would otherwise be sent six times per pass.

**Blinding.** Every group holds both repetitions of all three arms of the task, in random order under
neutral labels. A possible contrast effect between answers read together therefore affects all arms
alike, but it is a limitation to report.

## D3 · Malformed judge output

A response that is not valid JSON, has the wrong labels or holds a score outside 0–4 is never repaired. The
call is made once more, and if it fails again the group is reported without scores. No score is invented,
corrected or discarded. The adjudication rule (a third reading for any dimension differing by two points or
more) and the combination of scores are those of the frozen coordinator.

## D4 · Suspected unblinding

The protocol asks to record suspected unblinding. A scripted judge has no separate channel for it, so the
judge is not asked. Arm cues are removed by the coordinator before packing: the count of affected answers
is reported after scoring, and the packed answers contain no residual cue.

## Earlier operational choices (REPORT-12b)

- The frozen manifest covers every `*.py` under `experiments/v3/`. The scripts added after the freeze are
  checked instead by `experiments.v3.live.verify_frozen`: all frozen files unchanged, mapping fingerprint
  equal, and only the named scripts added (`live.py`, `analysis.py`, `judging.py`, `judge_gemini.py`).
- The first block of the schedule served as the pilot and counts as the first block of the campaign.
- The score sheet holds only the three dimensions. Severe factual errors and missing material items are not
  counted separately.
