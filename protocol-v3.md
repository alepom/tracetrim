# Protocol v3 · Does a learned projection preserve answer quality on new tasks?

**Frozen preparation, before live evaluation.** The manifest is `protocol-v3.lock`. Author approval to start the paid campaign is separate. A change to a frozen input requires a dated deviation, review and a new fingerprint before using it. This protocol supersedes the draft at commit `5bff673`.

## Design

In the paper v2 billing audit, the prudent arm produced 20 complete diagnoses out of 25, compared with 23 out of 25 for raw outputs (`paper/generated/table_c4_audit.tex`). C4 tests the observed quality risk. C5 tests transfer in CI triage, where the learned policy removes a large share of tool-output tokens. Both servers, tool returns, questions and reference answers are synthetic and redistributable. Inference is limited to these two task families.

Use one tool-calling model, the identifier in `experiments/prices.json`, with the Responses API and the study's default inference settings. Record the returned model identifier and run date. A second model would require a separate decision and protocol. There are ten held-out tasks per case, two fresh runs per task and arm, and three arms: 20 independent task units, 40 complete blocks and 120 scheduled answers. The five tickets within a billing task are components of one answer.

| Arm | Exposure |
|---|---|
| R | Unaltered output from the original simulated tools. |
| P | Delivered prudent policy with default identifier protection (`all`), compact JSON, preserved positions and recoverable hidden fields. |
| I | Identical procedure with `protect_ids=record`. |

P and I add only the base recovery instructions (`tracetrim.skill.text(done=False)`) and the same expand tool. Disable strategy instructions, automatic relearning, promotions and sampling. Use a fresh engine and model conversation per run. Keep system and user texts, fixture, tools and settings identical within the block apart from recovery instructions and exposure. R has all fields. This design compares the delivered options, including their serialization and recovery overhead.

## Split, fixtures and schedule

`experiments/v3/material/C4-*.json` and `C5-*.json` hold each literal user prompt, world and reference answer. The agent receives **only** its case's original system prompt and the literal user prompt through the model interface. Its read-only server exposes the matching world's data. Never supply keys, rubric, schedule or original historical answers to the agent. The billing assignments cover payment reconciliation, seat adjustments, prepaid credit, metering corrections, discounts, reactivation fees, returned licences, instalments, migration charges and replacement invoices. Each contains five new customers. Ten separate CI repositories contain distinct log causes, recurring failures and both linked and missing changes.

Policies are learned once from **only A1–A8 of the corresponding case**. Configurations, projections and training input hashes are frozen under `material/`. All evaluation tasks, repetitions and answers remain excluded from learning. The novelty audit compares literal prompts and scenario evidence identifiers, email addresses and diagnostic strings against historical inputs, including E2 billing variants. Similar tools and schema are intentional. New work is defined by distinct accounting questions, customers or diagnostic evidence and new worlds, rather than by a novel API.

Generate worlds and block order with seed 120311. `material/schedule.json` gives the exact order: shuffle task–repetition blocks, then cycle shuffled permutations of R, P and I. Each block serves the same task in all three arms. Use a new conversation on every slot. The private randomization for blind judging is separate from this public schedule.

## Outcomes and analysis fixed before evaluation

Primary outcome is the mean of three rubric dimensions per answer, each from 0 to 4. Follow the full frozen instructions and examples in `experiments/v3/rubric.md`. Two independently blinded passes yield the mean of their two scores for each dimension, except where adjudicated below. Report dimension scores, severe factual errors and missing material items alongside the composite.

**H1.** P preserves quality relative to R within **0.5 rubric points**. This is half a rubric level, the smallest difference the coordination team expects one reader to distinguish reliably with this task count. Compute P minus R within each repetition, average paired differences within task, then average tasks equally within cases and weight both cases equally. Compute a paired, stratified percentile bootstrap interval (95%, 10,000 draws, seed 311; resample tasks within case, carrying both repetitions and all arms together). Preservation requires the lower endpoint strictly above −0.5. An interval spanning the boundary is inconclusive. An interval wholly below the boundary indicates a material loss. Always report the full interval and the point estimate, also by case. Apply −0.25 as a **descriptive sensitivity analysis**. I minus R is secondary and uses the same estimator; do not promote a secondary contrast after observing outcomes.

Use existing `experiments.checks.check` on final answer plus raw tool results. A gate passes only with parsable JSON, correct shape, positive verified-item denominator and all checked items correct. Record zero denominators separately. Compute `tracetrim.grounding.unsupported` as a secondary count of unsupported numbers and codes. C4 identifiers and C5 job/log references can pass binary checks while the diagnosis is incomplete. Show paired gate failures and grounding alongside judged quality. Record calls, turns, recovery requests, raw/delivered output tokens including recovery replies (tiktoken `o200k_base`) and aggregate relative costs. A missing or unusable model answer receives zero on all dimensions. An intelligible answer is rated even if its JSON fails.

## Blind judge and custody

Astra judges every answer in **two separate sessions with no shared memory**, in independently randomized order and with different opaque IDs. A coordinator script seals the three private assignments in ignored `out/v3/`. The manifest contains only the SHA-256 of the mapping. The seed of the public schedule cannot reconstruct it. The judge receives the assigned packet directory only. No access to the mapping, traces, answer keys, generation metadata or the other pass's ratings before finishing. A fresh isolated session handles adjudication.

Each packet contains the task with its system instructions, the entire rubric, the final answer and the original evidence: a fixed series of raw MCP returns collected from the immutable world, including detail queries and logs. All arms receive the same reference evidence for a task, so a missing agent query cannot hide a factual omission from the judge. The coordinator strips recovery annotations and technical mentions from answers, counts **answers affected** once each and reports the count after blind scoring. Retain original answers privately during judgment. Packet IDs and filenames contain no execution or arm label.

Lock both score sheets before comparing. If **any dimension differs by at least two points**, a third pass sees the same evidence under a fresh ID and rates the entire answer without prior scores. Use its score for each disputed dimension and the mean of the original ratings for the others. Report the original passes' exact agreement, share within one point, quadratic-weighted kappa per dimension (undefined when its denominator vanishes), number adjudicated and suspected unblinding. Agreement measures repeatability of one AI judge. Astra knows the design but is blind to each answer's arm. A shared model family may favor familiar styles across all arms; this is a possible bias even with blinding. Publish these limitations with results.

## Stopping and operational checks

Use the separate private v3 ledger and local campaign warning/stop settings. Account for every provider attempt. Pause at the warning for review and refuse new calls at the stop. Stop after the prespecified sample, or earlier on an operational limit. No early stopping based on quality. Keep the study's 40-turn and 80-call guards. A refusal, invalid answer or guard exhaustion is an outcome. Retry an infrastructure failure at most once under a new attempt ID in the same slot; retain both attempts and the reason. An unresolved infrastructure failure is missing, distinct from a model that provided no usable answer. Report attrition by arm, calculate the main contrasts on complete paired blocks and show 0/4 worst- and best-case bounds for missing scores. If a case has no usable pairs, the combined endpoint is unavailable. Report incomplete campaigns explicitly.

Before authorizing a live run, verify the lock, mapping, training hashes, tool availability and campaign limits. The offline rehearsal uses a scripted model with no provider client. Reproduction commands, from the repository with `PYTHONPATH=src` and `TRACETRIM_TRACES` set to the original study traces:

```sh
python -m experiments.v3.freeze --verify
python -m experiments.v3.dry --dry-run out/new-v3-rehearsal
```

Worlds and ordering can be rebuilt with `python -m experiments.v3.worlds`, policies and overlap audit with `python -m experiments.v3.prepare --e2-traces PATH`, and canonical tool evidence with `python -m experiments.v3.dry --reference-only` in a scratch checkout. Compare their hashes to the manifest. For the later campaign the coordinator consumes trace JSONL at `out/v3/traces/TASK/REPETITION/ARM.jsonl` with final message and original tool returns. Use `python -m experiments.v3.coordinator pack` then `adjudicate` after the two blind scores are locked. The coordinator records documented infrastructure losses or unstarted slots separately in private `missing.json` (slot to reason). It requires a trace for every other slot. After adjudication, `finish` verifies the first two score-sheet fingerprints, combines the disputed dimensions and computes agreement. Release the mapping only after all ratings are locked. Publishable records contain synthetic data. Absolute spend, provider credentials and local financial controls stay private.
