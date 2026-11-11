# Protocol of the measures on the adaptive work (v0.4)

Status: **draft of 2026-11-11, not frozen**. It is frozen when the author approves it: the approval, its date
and the commit are recorded here, before the first paid run. Changes after freezing go in "Deviations" at
the bottom, dated, with the reason.

This protocol is separate from `protocol.md`, which stays as it is with the results of the original study.
The paper reports that study (tracetrim 0.1 and 0.2, November 2026). What follows measures functions written
after it, on the branch `feat/adaptive-learning-mcp-skills`: nothing here changes a number of the paper.

Two experiments:

- **E1** — does the *strategy* level of the tracetrim skill change how an agent works, and at what price in
  completeness?
- **E2** — when a task starts needing a field that tracetrim removes, does tracetrim go back to keeping it,
  after how many runs, and how much saving is left?

## What is under test

The software at the commit recorded at freezing. Relevant settings, all in `docs/skill.md` and `docs/adaptive.md`:

| Function | E1 | E2 |
|---|---|---|
| Projection | prudent, safeguards of 0.2, `expand` | the same, in automatic mode |
| Simple way for small outputs | on (it is always on) | on |
| Skill, base level (`expand`) | in the arms that trim | in every arm |
| Skill, strategy level | the variable | off |
| Promotion of fields | **off** | the variable |
| Sampling without filter | **off** | the variable |

Model: `gpt-5.6-terra`, default parameters, as in the original study. Model id and date are stored with every run.
Agent loop: the study's own (`experiments/run.py`, OpenAI Responses API, function calling), with tracetrim in the
loop through `AutoTrim` or a fixed `Engine`: the function-calling adapter. The MCP proxy is not in these
measures; that both adapters take the same decisions is covered by a test, not by this protocol.

## Preconditions, computed before any run

`python -m experiments.v04_preconditions` (no model, no spend) writes `results/v04/preconditions.json` from the
eight Phase A traces of each case. On 2026-11-11:

| Case | Held-out saving, prudent | Tools the delivered configuration trims |
|---|---:|---|
| C1 | 58% | keyword suggestions, ranked keywords, Trends explore, YouTube SERP (4 of 11) |
| C2 | 9% | **none** (0 of 2) |
| C4 | 57% | invoices, charges, customer search, subscriptions (4 of 5) |

The configuration is the one tracetrim builds by itself: a tool is trimmed when it carries at least 5% of the
tool-output tokens and its held-out saving is at least 20%. In C2 the organic SERP saves 16% and the page parser
nothing, so **no C2 tool is trimmed**. Two consequences, stated now:

- in E1 the arms T and ST of C2 trim nothing: on C2 they measure only that tracetrim in the loop changes nothing;
- C2 cannot be the real case of E2, where a removed field must exist. E2's real case is C1.

## E1 — the strategy of the skill

### Design

Four arms, same prompt, tools and model:

| Arm | Strategy text | Trimming |
|---|---|---|
| R | no | no |
| S | yes | no |
| T | no | yes |
| ST | yes | yes |

- **Strategy text**: `tracetrim.skill.text(strategy=True, done=False, expand=<arm trims>)`, version 1, appended to the
  case's system prompt after a blank line. Arm S gets the strategy alone; ST the `expand` part and the strategy;
  T the `expand` part alone; R nothing.
- **Trimming**: a fixed configuration, built once per case from its eight Phase A traces with the delivered rules
  (`tracetrim report`), applied by an `Engine` with no learner. Nothing is learned between runs: the runs of an arm
  are independent. The trimmed arms offer the `expand` tool.
- **Cases**: C1 (editorial plan, DataForSEO, 11 tools), C2 (content-gap briefing, DataForSEO, 2 tools), C4 (billing
  support, simulated Stripe-shaped API, 7 tools). C1 and C2 are the two cases on real APIs with many calls per
  run, where there is something to organise; C4 is the simulated case with a live-loop baseline in the original
  study (Phase C) and five independent tickets, which invite calls issued together. Prompts are those of the
  original study, unchanged.
- **Runs**: 5 per arm and case, 60 in all. Interleaved by case: R, S, T, ST, then again, so that a drift of the
  service or of the search results hits all arms alike.

### Measures

Per run, all computed by script from the trace and its meta file:

1. **Task success**: the gates of the original study (`experiments/checks.py`), and for C4 the stricter audit
   (`experiments/c4_audit.py`) beside the keyword test.
2. **Coverage of the prompt's explicit requirements**: a share of checks passed, per case. The checks, fixed here:
   - C1: both plans present with 10 to 15 items each; at most 50 tool calls; every `limit` passed is 10; every SERP
     `depth` is 10; at most 5 keywords per Trends call.
   - C2: the three themes present; 2 or 3 queries analysed per theme; at least one page of the site per theme; every
     gap has at least one evidence URL; every SERP call passes Italy, Italian and `depth` 10; proposals not empty.
   - C4: five tickets; each with a customer id, a diagnosis, at least one piece of evidence and a reply; no technical
     identifier in the reply to the customer.

   The checks on argument values are there on purpose: the strategy must not override what the prompt prescribes.
3. **Turns and calls**: model turns, tool calls, calls per turn (median and maximum), repeated identical calls,
   calls that returned an error, `expand` calls.
4. **Tokens billed**: input, cached input and output from the API `usage`, and the cost at `experiments/prices.json`.
5. **Tokens by category**, counted with tiktoken `o200k_base`: tool definitions; instructions added (skill text);
   tool outputs as passed on, split into unfiltered, filtered, passed whole by the simple way; `expand` replies;
   final answer.
6. **List elements** (`tracetrim.lists`): recorded for every run, as a description, with no hypothesis attached.
7. DataForSEO calls per run (C1, C2).

Reported as median [min–max] per arm and case. A reduction in calls or turns is never reported alone: it is shown
beside success and coverage of the same runs.

### Hypotheses (stated before any run)

- **H1 — completeness holds.** In each case, arm S passes every gate in at least as many runs as R, and its
  coverage median lies inside R's [min, max] or above it. The same for ST against T.
- **H2 — fewer turns.** In C4, where the tickets are independent, the median number of turns of S is below R's
  minimum. For C1 and C2 no direction is predicted: their prompts already prescribe an order of work.
- **H3 — trimming is neutral where it trims nothing.** In C2, T against R and ST against S: every measure of one
  arm inside the other's [min, max].
- **H4 — the strategy does not undo the saving.** In C1 and C4, the billed input tokens of ST have a median not
  above T's maximum.

A hypothesis that fails is reported as failed. With five runs per arm no significance test is claimed: a
difference inside the other arm's range is reported as not distinguishable from run-to-run variation.

### What counts as success

For the strategy to be recommended as a default it must pass H1 in all three cases. Fewer turns or tokens with H1
failed is reported as a cost, not a gain. If H1 holds and nothing else moves, the result is "no measurable effect",
and the strategy stays off by default.

## E2 — adaptive learning

### Design

- **Starting point**: a work directory seeded with the eight Phase A traces of the case, so that automatic mode
  starts from the projection learned on them (`min_runs` 5). Each sequence gets its own copy.
- **Variant of the task**: the case's user prompt plus one requirement that needs fields the projection removes.
  The prompts are in `experiments/prompts/` and fixed here.

  | Case | Requirement added | Fields needed | Removed now, and never used in Phase A |
  |---|---|---|---|
  | C1 (real) | name the most relevant YouTube channel and the views of its video, for the topics analysed on YouTube (`c1v-user.md`) | `items[].channel_name`, `items[].views_count` of the YouTube SERP | yes, both; present in 16 of 16 calls, 2 calls per run |
  | C4 (simulated) | for tickets about an invoice, give the link to the invoice page and say whether it is charged automatically or sent for payment (`c4v-user.md`) | `data[].hosted_invoice_url`, `data[].collection_method` of the invoice list | yes, both; present in 16 of 16 calls, 1 to 3 calls per run |

  The values of three of these fields are expected to be written in the answer (a name, a number, a link). The
  fourth, `collection_method`, is expected to be used and reworded ("addebitata automaticamente"): the case in
  which reuse cannot be seen. Whether the model does so is observed, not assumed.
- **Arms**: promotion off/on × sampling off/on.

  | Arm | Promotion | Sampling |
  |---|---|---|
  | base | off | off |
  | promotion | on, thresholds of the defaults (2 runs by request, 1 by reuse) | off |
  | sampling | off | on |
  | promotion+sampling | on | on |

  **Sampling in this experiment: rate 0.25, cost share 0.5.** These are settings of the experiment, not the
  defaults (0, and 0.2): with a share of 0.5 at most one result in three is sampled, and a tool called twice a run
  can be sampled from its second run on. With the default share a field would rarely come back within five runs.
  Seed: the sequence number.
- **Sequences**: a sequence is 5 consecutive runs of the variant in one work directory; learning accumulates
  along it. C4: 2 sequences per arm (40 runs). C1: 1 sequence per arm (20 runs), because of its cost.
  Sequences of different arms are interleaved run by run.
- The strategy level of the skill is off. The base level (`expand`) is in every arm.

### Measures

Per run: everything listed for E1, plus

1. **Requirement met**: the answer carries what the variant asks. C1: at least one channel name that appears in
   that run's YouTube outputs. C4: for each invoice ticket, a link that appears in that run's invoice outputs, and a
   statement on how the invoice is collected (a keyword test, declared as such).
2. **How the field reached the model**: in the filtered result (kept), through `expand`, in a sampled result, or not
   at all.
3. **Evidence recorded** for the needed fields, by kind, and the promotions decided, with their runs.
4. **Exposure counts**: results filtered, expanded, sampled, passed whole by the simple way; tool switched off or not.

Per sequence: the first run from which every filtered result carries the field with no `expand`; `expand` calls in
all; runs whose answer lacks the requirement; whether a tool was switched off; saving on tool-output tokens in
the last run, against the same outputs unfiltered.

### Hypotheses (stated before any run)

- **H5 — written fields come back by themselves.** In every arm, a field recovered with `expand` and written in the
  answer is kept from the next run on. Promotion makes no difference to it.
- **H6 — a reworded field needs promotion.** For `collection_method` in C4: with promotion on, the field is kept
  from the third run at the latest and the invoice tool is still trimmed at the end of the sequence; with promotion
  off, the field is never kept and the tool is switched off or expanded in every run. The saving left is higher
  with promotion in both sequences.
- **H7 — sampling reaches what is not asked for.** In the runs where the model does not call `expand` for a needed
  field, the requirement is met more often in the sampling arms than in the others.

H6 and H7 depend on what the model does (calling `expand`, rewording): if the precondition does not occur, the
hypothesis is reported as **not testable in these runs**, with the counts, not as confirmed or failed.

### What counts as success

E2 supports turning promotion on by default only if H6 holds in both C4 sequences and promotion never lowers the
share of runs that meet the requirement. It supports sampling only if H7 holds and the tokens sampling adds stay
within the declared share. Any other outcome leaves both off by default.

## What cannot be concluded

- **One model.** Whether other models call `expand`, follow the strategy or reword values in the same way is untested.
- **Few runs.** Five runs per arm in E1; in E2 two sequences per arm on the simulated case and **one** on the real
  case. No significance test; a single sequence is a single trajectory.
- **Keyword gates.** The gates of C4 and the "requirement met" tests check for identifiers, links and keywords:
  they accept a wrong sentence that contains the right words. The stricter C4 audit is reported beside them.
- **Simulated case.** C4's payloads were generated from a public API description. Its content is not real, and
  in the recorded traces `collection_method` takes one value in 46 of 48 invoices: a model could guess it.
- **The demo is not evidence.** `experiments/adaptive_demo.py` uses a scripted agent. It showed that the mechanism
  works; E2 is the first measure of what a model does with it.
- **Reuse is detected textual reuse**, with the blind spots described in the paper.
- **The proxy.** Only the function-calling adapter is exercised. Hosts that load the skill over MCP are not tested.
- **C2 trims nothing**, so E1 says nothing about trimming on C2.
- **Savings are those of the configuration as delivered.** Other thresholds would trim other tools.

## Budget and stop rule

Estimate: `python -m experiments.budget --v04` (needs the original study's meta files, `$TRACETRIM_TRACES`). Every
run is costed like a raw live run of its case in the original study (median of Phase A and of the raw arm of
Phase C), which is conservative for the trimmed arms; the skill text is added at the uncached price for every
turn; E2's variants are assumed to cost 1.25 times the original task, an assumption the first runs will correct.

| | Runs | USD |
|---|---:|---:|
| E1: 4 arms × 5 runs × C1, C2, C4 | 60 | 14.55 |
| E2: C1, 4 arms × 1 sequence × 5 runs | 20 | 10.43 |
| E2: C4, 4 arms × 2 sequences × 5 runs | 40 | 4.13 |
| Tuning: prompts of the variants, dry runs of the harness (an allowance) | | 5.00 |
| **Total** | **120** | **34.11** |

If every run cost the maximum seen for its case: 39.27 USD. Measured cost per raw live run: C1 0.415 (maximum
0.506), C2 0.219 (0.244), C4 0.082 (0.091).

- **Cap 50 USD, warning at 40**, on a new ledger (`out/ledger.jsonl` of this checkout; the original study's is
  not touched). The running total is computed from `usage` after every run, aborted ones included; at the cap
  `experiments/run.py` refuses to start a run, as it did.
- Prices: `experiments/prices.json`, verified on 2026-11-04.
- **DataForSEO** is billed apart, on the provider's account. Expected calls: C1 920 (40 runs × 23; 1,040 at the
  maximum seen per run), C2 330 (20 runs × 16.5; 400). The original study made 528 live calls on these two
  cases. The calls of every run are counted and reported; the author checks the spend on the provider's panel.

## Data rules

Those of `CLAUDE.md` and of `protocol.md`:

- inputs name only the author's domain; the gate is in the tool wrapper, and every rejection is counted;
- raw DataForSEO responses are not published: for C1 and C2 only derived per-call records (field paths, no values);
- the address of the author's DataForSEO MCP server appears in no versioned file, report or log;
- traces and `out/` stay local and git-ignored. C4 is simulated: its traces can be published.

## Before the first paid run (no spend)

1. `experiments/run.py`: arms R, S, T, ST and the E2 arms; the system prompt composed with the skill text; the cap
   and warning of this protocol; new traces written under this checkout, never under the original study's.
2. `experiments/coverage.py`: the checks listed above. `experiments/analyze_v04.py`: the tables, from the meta
   files, with every number recomputable.
3. A dry run of each arm with a stub in place of the model, to check traces, meta files, ledger and stop rule.
4. `python -m experiments.v04_preconditions` again, at the commit to be measured.

## AI assistance

As in the original study: the author set the questions and the constraints; an AI coding agent (Claude Code)
proposed this protocol, wrote the code, will run the experiments and draft the report, under the author's direction
and with a second AI session reviewing each step.

## Deviations

None yet.
