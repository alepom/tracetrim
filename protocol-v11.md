# Protocol v1.1 (E4): which identifiers to protect, live

Frozen before the first paid run: `experiments/e4.py freeze` records the SHA-256 of this file and of
`experiments/e4.py`, and no run starts if either has changed since. Deviations go at the end, dated.

## Question

On the recorded runs (`docs/measures-v1.md`), protecting only the record's own identifiers, with names and
titles at any depth (`protect_ids="record"`), removed 10.1 more points of tool-output tokens on C3 and 12.6 on
C5 than the rule of 1.0, with no extra miss. Offline replays cannot show what an agent does when it no longer
sees a nested identifier. Here: with the model live, does `record` remove more, and does the agent still pass
its checks and make nothing up?

## Design

- **Cases**: C3 (literature scan, OpenAlex, real API), C5 (CI-failure report, simulated server), C4 (billing
  support, simulated server). The prompts of the original study (`experiments/prompts/cN-*.md`). C1 and C2 are
  left out: the offline gain there is about one point, and their provider is billed per call.
- **Arms**, both with the trimming text of the skill (`expand`, as E1's arm T) and a configuration fixed for
  every run, built by tracetrim's own report from the case's eight Phase A traces:
  - **T**: the identifier rule of 1.0 (`protect_ids="all"`);
  - **TR**: `protect_ids="record"`.
  Nothing is learned between runs. No arm without trimming: the saving is measured on each run's own outputs
  (what the tools returned against what the model received), and cost is compared between the two arms.
- **Runs**: 5 per arm per case, 30 in all, in rounds (C4 T, C4 TR, C5 T, C5 TR, C3 T, C3 TR; then again),
  so that a drift of the service touches both arms alike.
- **Model**: the one of E1 and E2 (`experiments/prices.json`), OpenAI Responses API, the loop of
  `experiments/v04.py`.
- **Stop rules**: a cap and a warning of their own (`experiments/caps.py`, campaign `e4`, never published), on
  a ledger of their own (`out/ledger-e4.jsonl`). A run cut by an error of the model's service is made again
  under the next free id and both are reported. The safety guard of the study (80 calls, 40 turns) holds.

## Measures

Per run, from the meta file and the trace:
- **saving**: 1 − (tokens the model received from tool results) / (tokens of the tools' outputs), `expand`
  replies counted apart and then also added in;
- **gates**: `experiments.checks.check` (valid JSON, structure, facts found in the run's outputs);
- **unsupported values**: `tracetrim.grounding.unsupported` on the trace (numbers and codes of the answer
  that no tool output of the run contains);
- **cost per run** at list prices, **turns**, **calls**, **`expand` calls**.

## Hypotheses, stated before the runs

- **H1 (more saving)**: on C3 and on C5, the median saving of TR exceeds that of T by at least 5 points.
  Reported per case. C4: described, no threshold (offline: +5.9 points).
- **H2 (checks)**: on each case, TR passes the gates in at least as many runs as T.
- **H3 (nothing made up)**: on each case, the TR runs together hold no more unsupported values than the T runs.
- Described, no hypothesis: cost per run of TR relative to T (median), turns, calls, `expand` calls.

## What follows from the results

- H1 on at least one of C3 and C5, with H2 and H3 on every case: `record` is a candidate for the default in a
  later minor release, with `--protect-ids all` to restore the rule of 1.0 (STABILITY.md).
- Otherwise it stays an option, off by default, and the results say why.

## What this cannot show

One model; five runs per arm; one task per case. No arm without trimming: the cost of either arm against raw
outputs is not measured here. The gates check identifiers, structure and facts by rule, not the quality of the
answer as a reader would judge it.

## Deviations

1. (2026-11-14, after the 30 runs.) `experiments/e4.py` changed after the
   freeze in its analysis only: `analyse --json` leaves out the cost in USD of each run and arm, which the
   project never publishes, and keeps the cost of TR relative to T; the first `results/v1/e4.json`, written
   with them and never committed, was written again without. No run, measure or hypothesis changed.
