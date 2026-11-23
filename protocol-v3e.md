# Protocol v3e · Do text previews on the `[trimmed]` line recover the facts hidden in free text?

**Preregistered on 23 November 2026, before any run.** The schedule, the scripts and the library under test are
frozen in `protocol-v3e.lock` before the first paid run. Protocols v3–v3d are unchanged; v3e has its own files
(`experiments/v3e/`, `out/v3e/`) and its own private spend caps (`v3e`).

## Question

In protocols v3b and v3c (`gpt-5.6-terra`), the prudent projection lost hidden facts that sat in free-text fields:
a customer's balance `description`, an invoice memo. Version 2 of the skill raised the hidden facts reported from
85.1% to 92.3%, and the rest stayed lost: the `[trimmed]` line named `data[].description`, but an empty description
and one that explained a credit looked the same. Protocol v3d (`gemini-3.8-flash`) lost nothing, because that model
expanded the hidden fields in every run.

tracetrim 1.5 adds text previews, off by default: with `text_preview=N` the `[trimmed]` line also shows, after
` · text, first characters: `, the start of the hidden free-text fields, cut after a whole word within N
characters (`tracetrim.engine.previews`). Replayed on the 72 hard-task traces of v3c (P1 and P2), a 40-character
preview would have put 38 of the 40 missed hidden facts on the line itself, for 0.8% more tokens in the trimmed
results (`experiments/v3e/hints_offline.py`, `results/v3e/hints_offline.json`).
That replay shows the information would be there; it does not show the model uses it.

**With `gpt-5.6-terra`, does the preview raise the share of hidden facts reported over the default of 1.4 (skill
version 2), and does it cost more where nothing is hidden?** The answer decides whether to recommend the previews,
or to turn them on by default in a later version.

## Arms

| Arm | Projection | Skill | Previews |
|---|---|---|---|
| P2 | frozen prudent policy P of protocol v3 (`experiments/v3/material/policies/C4/P`), no relearning | version 2 | off |
| P2T | the same policy | version 2 | `text_preview=40` |

The arms differ **only** in the previews; the JSON of every trimmed result is the same in both (checked on the v3c
traces before the freeze). Model, loop, guards, retry rule and trace format are those of v3c
(`experiments/v3/live.py`): `gpt-5.6-terra`, Responses API, default inference settings, 40 turns, 80 calls, one
retry of an infrastructure failure, a fresh engine and conversation per run. 40 characters was chosen on the v3c
traces, before any v3e run; it is a parameter of the arm, not a result.

## Tasks and runs

- **Hard tasks**: the 12 tasks of protocol v3b, version 2 (`C4b-01` to `C4b-12`), with their worlds, keys and 54
  hidden facts unchanged. 3 repetitions × 2 arms = 72 runs.
- **Ordinary tasks**: the 10 tasks of protocol v3 (`C4-01` to `C4-10`), 1 repetition × 2 arms = 20 runs.

The schedule (`experiments/v3e/material/schedule.json`, seed 241123) shuffles the blocks (task, repetition); each
block runs both arms, in an order balanced across blocks and shuffled with the same seed. The first block is the
pilot and counts as part of the campaign if the code does not change after it.

## Measures

- **Primary:** the share of hidden facts reported correctly (`experiments.v3b.facts.score_trace`, `hidden_share`)
  on the hard tasks.
- **H1 (primary):** P2T − P2 > 0, paired within repetition, averaged within task, tasks equal, percentile bootstrap
  95% of protocol v3b. «Improved» if the lower end is above 0; «no improvement shown» otherwise.
- **Secondary, descriptive:** the share of all facts; hidden facts by kind, how they reached the model (list,
  detail, `expand`), how many were on a preview, and how many were reported with the preview as the only way they
  reached the model; calls, turns, `expand` calls, tokens delivered and cost P2T/P2 on hard and ordinary tasks; on
  the ordinary tasks, gates (`experiments.checks.check`) and values not supported by the outputs
  (`tracetrim.grounding.unsupported`).
- Costs only as ratios, never as amounts.

## Decision

The analysis recommends turning the previews on by default (a later minor version, listed under *Changed
behaviour*, with `--text-preview 0` restoring 1.4) only if all three hold: H1 «improved»; cost P2T/P2 on the
ordinary tasks at most 1.10; gates on the ordinary tasks not fewer in P2T than in P2. Otherwise the previews stay
off by default, documented with this result.

## Limits declared in advance

- **The hard tasks put the fact at the start of the text** (for example «Accredito di cortesia di 6,14 EUR…»).
  With real data the fact can sit after the first 40 characters, where only `expand` reaches it. A positive H1
  says the previews work when the fact is near the start, not that they always do.
- One agent model, the one that showed the loss. v3d showed that a model which always expands has nothing to gain.
- No judge: every measure is computed by scripts from the frozen keys.

## Stop rules and spend

- Caps: entry `v3e` of the private, git-ignored `experiments/caps.local.json`, set with the author's budget; the
  estimate, from the v3c ledger, is in the private report only (`CLAUDE.md`).
- A run that fails twice for infrastructure is recorded as missing, as in v3–v3d. If more than 10% of the slots
  are missing, the analysis reports it and H1 is computed on complete blocks only.

## Deviations

Any change after the freeze is written here, dated, with the reason, before the analysis is run.
