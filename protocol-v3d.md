# Protocol v3d · Do the stress-test loss and the skill-2 fix hold with a second agent model?

**Preregistered on 23 November 2026, before any run.** The schedule, the scripts and the library under test are
frozen in `protocol-v3d.lock` before the first paid run. Protocols v3, v3b and v3c are unchanged; v3d has its own
files (`experiments/v3d/`, `out/v3d/`) and its own private spend caps (`v3d`).

## Question

Protocols v3b and v3c ran one agent model, `gpt-5.6-terra`. On billing tasks that need fields the frozen prudent
projection removes, raw outputs (R) gave 100% of the hidden facts, the prudent arm with the skill of version 1
(P1) about 84–85%, and the same arm with the skill of version 2 (P2), which asks the agent to check hidden fields
before saying information is missing, 92.3%. Both findings rest on one model. **With a model of another family,
`gemini-3.8-flash`, is there the same loss with P1, and does version 2 of the skill reduce it?**

## Arms

| Arm | Projection | Skill and `expand` text |
|---|---|---|
| R | none: raw outputs, no `expand`, no added instructions | — |
| P1 | frozen prudent policy P of protocol v3 (`experiments/v3/material/policies/C4/P`), no relearning | version 1 |
| P2 | the same policy | version 2 |

Model `gemini-3.8-flash` on Vertex AI (global endpoint), through the adapter `experiments/gemini.py` of protocol
v04-E3, thinking at the model's default level. Loop, guards, retry rule and trace format are those of protocols
v3–v3c (`experiments/v3/live.py`): 40 turns, 80 calls, one retry of an infrastructure failure, a fresh engine and
conversation per run. Costs are computed with the Vertex prices in `experiments/prices-e3.json` (the study's
ledger and caps; the run metadata keep the field name of the v3 loop).

## Tasks and runs

The 12 hard tasks of protocol v3b, version 2 (`C4b-01` to `C4b-12`), with their worlds, keys and 54 hidden facts
unchanged. **3 repetitions × 3 arms = 108 runs.** The schedule (`experiments/v3d/material/schedule.json`, seed
231123) shuffles the blocks (task, repetition); each block runs the three arms in one of the six orders, balanced
across blocks and shuffled with the same seed. The first block is the pilot and counts as part of the campaign if
the code does not change after it.

## Measures

- **Primary:** the share of hidden facts reported correctly (`experiments.v3b.facts.score_trace`,
  `hidden_share`), per task averaged over repetitions.
- **H1 (primary):** P2 − P1 > 0, paired by task, 95% bootstrap interval of protocol v3b. «Improved» if the lower
  bound is above 0; «no improvement shown» otherwise.
- **Secondary, descriptive:** P1 − R and P2 − R with intervals (is there a loss with this model, and how much of it
  version 2 closes); the share of all facts; gates (`experiments.checks.check`); cost per run relative to R;
  calls, turns, `expand` calls and runs with `expand`; the hidden facts by kind, reported and reached.
- Comparisons with the `gpt-5.6-terra` results of v3b and v3c are **descriptive**: different runs, different model.

## Stop rules and spend

- Caps: entry `v3d` of the private, git-ignored `experiments/caps.local.json`, set with the author's budget;
  the estimate, from the usage of v3b and v3c at the Vertex prices, is in the private report only (no amount
  appears in versioned files, `CLAUDE.md`).
- A run that fails twice for infrastructure is recorded as missing, as in v3–v3c. If more than 10% of the slots
  are missing, the analysis reports it and H1 is computed on complete blocks only.
- No judge: every measure is computed by scripts from the frozen keys.

## Deviations

Any change after the freeze is written here, dated, with the reason, before the analysis is run.
