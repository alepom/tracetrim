# Experimental protocol (frozen)

Status: **frozen on 2026-11-04**, approved by the author, committed and tagged `protocol-v1` before any run of this
protocol, pilot included. Changes after freezing go in "Deviations" at the bottom, dated, with the reason.

Pilot evidence already available, and declared as such in the paper: the 11 traces of the course project
(`accademia-server-mcp`, agent "PED", Oct 2026) and the replay of 2026-11-03. They motivated this protocol and are
reported as an exploratory study, not as a test of the hypotheses below. They contain a client domain and are never published.

## Research questions

| # | Question | Measured by |
|---|---|---|
| RQ1 | What share of tool-output tokens carries fields the agent visibly uses? | usage analysis on Phase A traces |
| RQ2 | How much does a projection learned from other traces trim, and how often does it hide a field the held-out trace uses? | leave-one-out, Phase A |
| RQ3 | Does the agent's output keep its quality when tool outputs are projected? | replay with one variable changed, Phase B |
| RQ4 | Where must the learner sit: does seeing only tool calls (MCP proxy) suffice? | leave-one-out with `source=calls` vs `full`, Phase A |
| RQ5 | Does a projection learned on one task transfer to another task using the same tools? | learn on C1, apply to C2, Phase A |
| RQ6 | With an `expand` tool for hidden fields, how often is it called, and what does the trimmed loop cost end to end? | live runs, Phase C |

## Hypotheses (stated before Phase A)

- H1: the prudent projection removes at least 30% of structured tool-output tokens in C1 (pilot: 36-56% per trace).
- H2: in Phase B the prudent arm passes every gate in at least as many runs as the raw arm, and every thermometer median
  lies inside the raw arm's [min, max].
- H3: `source=calls` hides a used field in more than three times as many calls as `source=full` (pilot: 124 vs 28 of 201).
- H4: cross-task transfer (C1 → C2) misses more fields than within-task learning on C2.

A hypothesis that fails is reported as failed. No hypothesis is added after Phase A starts.

## Cases

| Case | Agent | Tools | Input names only | Output checked against |
|---|---|---|---|---|
| C1 | editorial plan for a website | DataForSEO MCP: Labs, keyword data, SERP, Trends, on-page parsing | alessiopomaro.it | volumes and keywords in the tool outputs |
| C2 | content-gap research briefing | DataForSEO MCP: SERP, on-page parsing | alessiopomaro.it | URLs and titles in the SERP outputs |
| C3 | literature scan | OpenAlex API (CC0) | — | DOIs and titles in the tool outputs |

**Domain rule, enforced in code, not in the prompt.** Every input names only the author's domains. The tool wrapper
rejects `on_page_content_parsing` on any URL outside `alessiopomaro.it` and any Labs `target` outside it, returning a
fixed error message. SERP results naturally list third-party domains: they are measured, never published in clear.
Every rejection is logged and counted.

Prompts are written for this study, versioned in `experiments/prompts/`, and fixed at freezing.

## Agent loop

Our own loop with function calling (OpenAI Responses API), because hosted MCP never exposes tool outputs to the client.
The loop calls the same DataForSEO MCP server used in the pilot as an MCP client, so outputs are byte-identical to what a
hosted integration would receive. Every event is written to a trace in the tracetrim JSONL format.

- Model: `gpt-5.6-terra`, default parameters, for every phase. Model id and date are stored with every trace.
- No hard cap on tool calls; the budget instruction of the pilot's B5 prompt is kept in C1.

## Phases

**Pilot (P).** One run per case, to check the loop, the traces and the costs. Not used in any result.

**Phase A — collection.** 10 live runs per case with raw tool outputs. Used for RQ1, RQ2, RQ4, RQ5.
Leave-one-out within each case; transfer: learn on all 10 C1 traces, apply to C2 traces (shared tools only).

**Phase B — replay.** For each case, 2 traces chosen *before* looking at their content: the 3rd and the 7th of Phase A.
The conversation is replayed up to the final answer, changing only the tool messages. Arms:

| Arm | Tool messages |
|---|---|
| raw | as received |
| headroom | Headroom `compress` (version recorded), most aggressive configuration as in the pilot |
| prudent | learned projection, prudent, from the other 9 traces of the case |
| aggressive | learned projection, aggressive, same source |

3 runs per arm and trace, interleaved (raw, headroom, prudent, aggressive, then again) so that drifts of the service hit
all arms alike. TOON and the hand-written projection (C1, `serp_organic_live_advanced` only) are reported as token
baselines, not replayed.

**Phase C — live loop with `expand`.** C1 and C2, 5 runs per arm: raw vs prudent projection plus an `expand(call_id,
fields)` tool that returns hidden fields. Measures billed tokens (usage API, cache included), turns, `expand` calls,
gates and thermometers.

## Measures

- Tokens: tiktoken `o200k_base` for tool outputs; `usage` from the API for what is billed. Never estimated.
- **Gates** (binary): output parses; required structure present; domain rule never violated; **facts**: every number or
  identifier the output attributes to a tool result matches that result (C1: keyword ↔ volume; C2: URL ↔ title;
  C3: DOI ↔ title).
- **Thermometers** (graded): C1 reuses the pilot's metrics (`confronta-qualita.py`: volume median and mean, long-tail
  share, topics covered, items proposed); C2: distinct sources cited, own pages linked, gaps identified;
  C3: distinct works cited, share of recent works.
- Reporting: median [min-max] per arm. With 3-5 runs per arm no significance test is claimed; H2 is judged with the
  band rule above. An effect inside the raw arm's band is reported as "not distinguishable from run-to-run variation".

## Budget and stop rule

Model prices assumed for the estimate: 2 USD / 1M input tokens, 12 USD / 1M output tokens (author's listing for
gpt-5.6-terra, **to be verified**; no cache discount assumed, so the estimate is conservative). Run sizes from the pilot:
live C1 run 117,978 input / ~7,500 output tokens; C1 replay 106,509 / ~7,000; C2 live 54,644 / ~2,000 (course,
"Concetto 5"); C3 assumed equal to C2 until the pilot measures it.

| Phase | Runs | Estimate (USD) |
|---|---:|---:|
| P | 3 | 0.6 |
| A | 30 | 5.9 |
| B | 72 | 13.5 |
| C | 20 | 4.6 |
| **Total** | **125** | **24.6** |

Model spend cap: **30 USD**. The running total is computed from `usage` after every run; at 25 USD the author is
warned, at 30 USD everything stops. DataForSEO calls are billed separately on the provider account and estimated after
the pilot, before Phase A.

## Data and publication

Following the providers' terms read on 2026-11-03 (local copies in `legal/`):

- Published: code (MIT), paper (CC BY 4.0), aggregate results, field paths, per-run metrics.
- Traces are published only **pseudonymised**: every word token replaced by a keyed hash, identically in tool outputs
  and agent text, so that usage detection gives the same results; original token counts stored alongside. The key is
  not published. Raw traces stay local.
- C3 (OpenAlex, CC0) traces may be published in clear.
- No DataForSEO or SERP excerpt is reproduced in the paper beyond field names and structure.

## AI assistance

The author set the questions, the constraints and the domain rule. An AI coding agent (Claude Code) proposed the
protocol, writes the code, runs the experiments and drafts the manuscript under the author's direction. Declared in the
paper, as in the author's previous study.

## Amendment 1 — adopted on 2026-11-04, before any run of Phase A

Motivated by the pilot and by the author's request that the system be general, not tied to SEO. Where it conflicts
with the sections above, this amendment prevails. Approved by the author and tagged `protocol-v2`.

The budget and the stop rule govern how the study is run; the paper describes the resulting design (runs per case,
arms, traces) and its limitations, not the spending cap.

**A1. Two non-SEO cases, with simulated tools.**

| Case | Agent | Tools | Output checked against |
|---|---|---|---|
| C4 | customer support for an online shop: answer a batch of customer tickets | simulated MCP server: orders, shipments, customers, tickets | order numbers, statuses and dates in the tool outputs |
| C5 | software maintenance: triage failing CI runs and open issues of a repository | simulated MCP server: issues, pull requests, CI runs, job logs | issue/PR numbers and failing job names in the tool outputs |

The simulated servers return payloads whose **field names and nesting follow the public reference documentation** of
widely used APIs of each domain (the exact documents are listed in `experiments/simulated/SOURCES.md` before Phase A).
Values are synthetic, generated by a seeded script, and contain no real person or company. Because we design these
payloads, C4 and C5 are reported **separately** from the real-API cases C1-C3 and never pooled with them.

**A2. Budget.** Model spend cap raised to **40 USD** (author's decision of 2026-11-04). The pilot measured far larger
contexts than the estimate (C3: 510,871 input tokens in the last turn), so the design is reduced to fit, as computed by
`python -m experiments.budget --c3-scale 0.5 --live 8 --arms 4 --replays 2 --traces 1 --phase-c C1,C4 --phase-c-runs 3`
(31.50 USD to spend, plus 1.69 USD spent in the pilot):

- OpenAlex search returns at most **5** results per page (was 10).
- Phase A: **8** live runs per case (was 10), for C1-C5.
- Phase B: **1** trace per case, the 3rd of Phase A, **2** runs per arm (was 2 traces, 3 runs).
- Phase C: C1 and C4, **3** runs per arm (was C1 and C2, 5 runs).

With 2 runs per arm, Phase B can detect only large quality losses; this is stated in the paper as a limitation.

**A3. Arms of Phase B.** raw · prudent · aggressive · **prudent + structural** (lossless clean-up: no ASCII escapes,
no null or empty values; then TOON for arrays of uniform objects). Headroom is reported as a token-only baseline on the
same traces, not replayed: its quality effect was already replayed in the pilot study.

**A4. Fields never seen while learning pass through** (`unseen="keep"`, the library default from now on): an API that
adds a field must not have it hidden silently. All results are reported with this policy; the strict policy
(`unseen="drop"`, which reproduces the pilot's numbers exactly) is reported as a sensitivity analysis. On the 11 pilot
traces: misses 21/201 with `keep`, 28/201 with `drop`; trimmed tokens -49% / -59% (prudent / aggressive) with `keep`.

**A5. Pilots for C4 and C5**, one run each, before Phase A; excluded from results.

## Deviations

All recorded on 2026-11-04, during the pilot, before any run of Phase A.

0. **Loop bug found in the pilot.** With `previous_response_id`, the Responses API does not carry `instructions` over:
   every turn after the first ran without the system prompt, and all three pilot answers ignored the output format.
   Fixed (instructions re-sent every turn) before Phase A. The pilot runs are excluded, as planned.

1. **Prompts written after freezing.** The protocol said prompts would be fixed at freezing; they were written during
   the pilot. They are fixed (committed) before Phase A, and the pilot is excluded from all results, as planned.
   C1's system prompt is the pilot's B5 prompt with three removals: the social signals given in input, the native web
   search (not available in this loop) and the social component of "trend intensity".
2. **Tool-level caps and guards, made explicit.** OpenAlex search returns at most 10 results per page, enforced in the
   tool (one work weighs ~6,000 tokens; 200 per page would exceed the budget). A safety guard stops a run at 80 tool
   calls or 40 turns, well above the 50 calls the C1 prompt allows: it is not an experimental cap, and a run that hits
   it is flagged and excluded.
3. **OpenAlex without an API key.** The keyless free allowance (0.10 USD/day, ~100 searches) is enough for C3; the
   tool stops before it runs out. If OpenAlex starts requiring a key, the author is asked before going on.
4. **Verification pilot (2026-11-04), after the fix of deviation 0.** One more run of C1-C3 (`pilot2`) and the first
   runs of C4 and C5 (`pilot`), all excluded from results. All five answers parse and have the required structure; facts
   verified 100% in every case after one correction to the C5 gate (a cause may name several jobs, separated by `;`:
   each name is now verified on its own). Measured cost per live run replaces the estimates in the budget script.
5. **Simulated tools fixed before Phase A** (amendment 1, A1): Stripe and GitHub OpenAPI descriptions at the commits
   listed in `experiments/simulated/SOURCES.md`, with the presence rule stated there (top-level fields as the APIs
   return them; below the top level, required fields and the branch a `type` names). Prompts of C4 (Italian, as its
   customers) and C5 (English) written during the pilot, fixed with this commit.
6. **Infrastructure abort in Phase A (2026-11-04).** Run C1 A7 aborted when the DataForSEO MCP server closed the
   stream mid-call (`SSE stream ended without a response`); no trace was written. Tool calls now retry transient
   transport errors twice before aborting, and an aborted run is written to the spend ledger with its partial usage.
   The lost usage of C1 A7 is recorded as an estimate at its upper bound (0.469 USD, the most expensive C1 run).
   The repeat failed at the same point (the retries reused the dead session; its partial usage, 0.198 USD, is in the
   ledger). Final rule: each retry opens a new session; after three failed attempts the agent receives an error
   message, as from a real failing tool, the run continues and is flagged `tool_failure` with the failing call in its
   metadata. Flagged runs are kept and reported; a sensitivity analysis excludes them.
7. **`expand` listing changed before Phase C (2026-11-04).** The first implementation listed the first 20 hidden field
   paths in alphabetical order. While Phase B was running, C4 showed that a filtered agent lost a date held in
   `data[].items` (see the C4 results); in Phase C such a field could have been cut from the list, and the agent would
   not have known it could ask for it. The listing now groups hidden fields two levels deep, largest groups first
   (at most 25 groups), and `expand` accepts a group. Changed before any Phase C run; Phases A and B are unaffected.
8. **C3 gate tightened after Phase B (2026-11-04).** The C3 gate accepted a work when its OpenAlex id OR its DOI
   matched a returned work. In the structural arm an agent wrote the prompt's example id (`W4391234567`) for every
   work, and the gate passed it because the DOIs matched. A declared OpenAlex id must now exist in the tool outputs.
   The fix was made after seeing the data; it is applied to every run of every phase, and both versions are reported.

9. **Phase C delayed, not changed (2026-11-04).** The script meant to finish Phase C while the author was offline
   waited for "no run in progress" by matching a process name that also appeared in its own launcher's command
   line, so it waited forever. No run was lost or altered: the run in progress finished, the other eight were started
   later from the session. Only the timing of Phase C changed.

---

# Confirmation study (Study 2) — frozen on 2026-11-04

Motivated by Phase B, which found three mechanisms by which the learned projection loses information. Study 2 tests
whether two safeguards, added to the library as version 0.2, remove those losses while keeping most of the saving.
Approved by the author and tagged `protocol-v3` before any of its runs.

## Safeguards under test (tracetrim 0.2)

- **S1 — identifying fields are kept** whatever the usage says: `id`, `*_id`, `uuid`, `guid`, `doi`, `url`,
  `html_url`, `uri`, `href`, `slug`, `number`, `title`, `name`, `display_name` (`learn(..., protect_ids=True)`).
- **S2 — transformed values are recognised** (`analyse(..., normalize=True)`): Unix timestamps and ISO dates also
  match as dates (ISO, day/month/year, day and month name in Italian and English); integers in fields named like
  money (`amount`, `price`, `total`, `subtotal`, `balance`, `cost`, `fee`, `tax`) also match as major units with two
  decimals; URLs also match by their last path segment.

`expand` (the third safeguard) needs a live loop; its evidence is Phase C.

## Design

Same conversations as Phase B: trace A3 of each case, replayed up to the final answer, only the tool messages changed.
Projections are learned from the same seven traces (A1-A8 without A3). Arms, interleaved:

| Arm | Tool messages |
|---|---|
| raw | as returned |
| v02 | prudent projection learned with S1 + S2 |
| v02-structural | the same, then clean-up and TOON |

5 runs per arm and case (75 replays). Same model, same closing message, same gates and thermometers as Phase B;
the C3 gate is the tightened one (deviation 8).

## Hypotheses (stated before any run of Study 2)

- **H5 (C4, timestamp):** in every v02 run, all five tickets are diagnosed correctly (T3 included).
- **H6 (C3, identifiers):** in every v02 run, the C3 gates pass, i.e. no OpenAlex id is invented.
- **H7 (C5, titles):** in every v02 run, all four expected issue and pull-request links are found (#150 included).
- **H8 (saving):** the v02 arm keeps at most 70% of the raw tool-message tokens in C1, C3, C4 and C5 (a saving of
  at least 30%; C2 is exempt, its saving was 12% before any safeguard). Offline estimate, made before any run:
  -53%, -46%, -57%, -61%.
- Secondary, as in Phase B: the band rule on every thermometer, against the raw arm's 5 runs.

The structural arm is reported against the same outcomes, as exploratory.

## Budget

Mean Phase B replay cost per case, cache included: 0.45 USD for the five cases, so about 7 USD for 75 replays.
The spend cap of 40 USD and its stop rule are unchanged.

## Deviations of Study 2

10. **C5 gate, job separator (2026-11-04, after Study 2).** An answer named two valid jobs as
    `test (3.13) / test (3.12)`; the gate split job names only on `;`, `,` and "and", and counted the pair as one
    unknown job. `/` is now a separator too. Applied to every run of every phase.
11. **C4 thermometer, T2 (2026-11-04, after Study 2).** The frozen test for ticket T2 looked for "fondi" or
    "insufficient" anywhere in the ticket, so advice to the customer ("check that you have sufficient funds") passed
    it even when the diagnosis omitted the reason. A strict test, reading only the diagnosis, is added and reported
    beside the frozen one (`correct_diagnoses_strict`, `H5_strict_posthoc`); H5 is evaluated as frozen. Under the strict
    test the v02 arm loses the decline reason in every replay: the reason arrives in English and the agent translates
    it, a fourth loss mechanism that safeguard S2 does not cover.
12. **Publication of traces (2026-11-04).** The protocol promised pseudonymised traces for the cases built on the
    search-data API, with "the same analysis results". Pseudonymising the values that matter (volumes are numbers)
    would change those results, so the promise cannot be kept as written. Instead: traces of C3 (CC0 catalogue), C4
    and C5 (simulated) are released in full; for C1 and C2 only derived per-call records are released (tool, field
    paths used, echoed and judgeable, token counts), which reproduce the usage analysis without redistributing the
    provider's data.
13. **Phase C, editorial-plan gate (2026-11-04, after publication review).** The live loop stores the outputs the
    agent received, so in the prudent arm the frozen C1 gate checked only the (keyword, volume) pairs still visible
    in the projected outputs (22, 43 and 26 of 60) and skipped the rest. The frozen result is kept; every pair is also
    re-checked against the raw outputs of all the other C1 runs of the same day
    (`experiments/recheck_phase_c.py`, `results/phase_c_recheck.json`) and reported beside it. The sensitivity
    analysis promised in deviation 6 (without runs with a failed tool call) is reported for Phases A and C.
