# Draft protocol v3 · Answer quality on held-out tasks

**Status: proposal for author review.** No freeze or evaluation has taken place. Approval of the design, preparation of the fixtures and a separate decision to fund the runs precede execution.

## Question and prior evidence

When the agent receives tool outputs projected by a policy learned on its earlier task, does its answer to a different task still meet the user's requirements? In paper v2 the stricter billing audit found 20 complete diagnoses out of 25 with prudent projection and 23 out of 25 with raw outputs (Study 2, `paper/generated/table_c4_audit.tex`). That audit used a familiar prompt. This study changes what the user needs while keeping the same tools. It examines transfer within each of five cases.

## Split and task cards

Learn each case's two fixed policies once from **only its original eight raw Phase A traces, A1–A8**, using the report settings at the study commit. Store their file hashes, the enabled tools and the hashes of the emitted configurations. Apply those same configurations to both models. Keep all earlier prompts, variant tasks, traces and answers out of evaluation. Keep every evaluation artifact out of learning, including across arms, repetitions and models. No promotions, inter-run learning, sampling or threshold tuning. A case with no enabled tools stays in the study and is flagged as such.

Each row below defines a distinct user assignment. Use the case's established language and JSON answer schema. For C1–C3 retain the corresponding instructions in `experiments/prompts/cN-system.md`; replace the original user request with the row's audience, topic and deliverable. C1 and C2 concern only alessiopomaro.it, with Italian search settings. For C4 and C5 retain the schema and read-only tools. Substitute the new ticket batch or repository snapshot and its stated task window for the old one. All arms in a block receive identical task wording.

| Card | User assignment | What a complete answer must establish |
|---|---|---|
| C1-A | Plan Italian articles for editors making podcast transcripts accessible. Separate urgent revisions from evergreen guidance. | Tie current topics to timely search evidence and evergreen topics to observed demand. Identify existing site coverage or document the search for it. |
| C1-B | Plan Italian articles for small publishers updating seasonal guides after their search traffic peaks. | Use trends to distinguish a recurring peak from a short-lived surge and explain which existing page to update, if any. |
| C1-C | Plan Italian articles for photographers organizing large image archives. Focus on search, tagging and descriptive captions. | Distinguish professional search intents, support priorities with observed keywords and distinguish new articles from overlap with current coverage. |
| C2-A | Prepare a content-gap brief on podcast transcripts, chapter navigation and episode summaries. | For each theme, compare SERP evidence with site coverage and justify a new page or revision. |
| C2-B | Brief the editor on image captions, image metadata and descriptive filenames. | Separate the three search intents, link evidence to the correct gap and identify the page to change or the missing page. |
| C2-C | Brief the editor on updating evergreen articles, merging overlapping articles and archiving obsolete ones. | For each editorial decision, cite the retrieved evidence and explain what the site already covers. |
| C3-A | Scan the literature on inter-rater agreement in ordinal human evaluation of generated text. | Distinguish agreement measures, note where their assumptions differ and connect these claims to retrieved works. |
| C3-B | Scan the literature on detecting duplicate scholarly records across bibliographic databases. | Compare identifier-based and metadata-based methods and identify a documented failure mode. |
| C3-C | Scan the literature on how image descriptions serve blind readers in museums. | Distinguish evidence about reader needs from automated description metrics and identify an open question. |
| C4-A | Five customers ask why their balances differ from an invoice. Reconcile, in order, a prorated upgrade, a credit note, a partial payment, a failed retry followed by success and two separate subscriptions. | For each customer, join the correct records, explain the direction of the discrepancy and give the supported next action. |
| C4-B | Five customers ask whether to pay again. Determine, in order, the outcomes of an expired payment method, an invoice already paid by another method, a pending payment, a settled refund and a voided invoice. | Distinguish open from settled states and explain whether a further payment or intervention is warranted for each. |
| C4-C | Five customers question renewal notices after, respectively, a scheduled cancellation, a resumed subscription, a trial conversion, a billing-cycle change and a downgrade at renewal. | Identify the effective date and subscription or invoice behind each notice and give the appropriate customer-facing reply. |
| C5-A | Triage the latest week on main after a toolchain update. Identify which failures share a missing formatter, which are independent snapshot mismatches and which issue or change addresses each. | Map runs to jobs and exact error lines, distinguish a common cause from independent failures and support each proposed link. |
| C5-B | Triage the latest week on main after a runner migration. Distinguish a missing font in PDF builds from a locale-dependent date test and identify any cause with no open fix. | Use log evidence and issue or change status to group failures and assign the next action. |
| C5-C | Triage the latest week on main after a documentation reorganization. Distinguish broken relative links from a stale generated table of contents and identify their owners' next actions. | Map failures to the responsible build step, cite exact errors and separate a relevant change from an unrelated one. |

Before freezing, turn these cards into literal prompts. For C4, prepare three independent five-customer worlds with synthetic email addresses, new object IDs and reconciled ledger facts accessible through the existing endpoints. Name the customers and their questions in the prompts. For C5, prepare three immutable snapshots with dated runs, jobs, logs, issues and pull requests. Put the diagnostic facts in the tools, not in the user's wording. A curator verifies each answer key against the fixture without running an agent. For C1–C3 freeze evidence-selection and abstention rules: where coverage, metadata or research evidence is missing, a documented search and justified limitation satisfies that part. Keep the case's minimum output requirements in the scoring key. Pin the calendar window for live queries at freeze.

Compare task text, intents and fixture identities to historical prompts including E2 variants. Replace overlaps **before** freeze. Hash literal prompts, fixtures, scoring keys, rubrics, reference evidence rules, code, model settings and the schedule in a manifest. Dry verification with local stubs must show that a complete answer can pass each gate and that a deliberately incomplete answer scores lower. No evaluation answer may be used to select a task or revise its key.

## Arms, allocation and models

| Arm | What reaches the agent |
|---|---|
| R · raw | The original tool output. |
| C · compact | Lossless JSON minification as in `experiments.baselines.minified`. Plain text passes through. |
| P · prudent | The delivered prudent policy with `protect_ids=all`, compact serialization, position safeguards and recoverable fields. |
| I · record | As P, with `protect_ids=record`. |

P and I offer the same expand tool and base recovery instructions. R and C have every field. The strategy skill is off throughout. Keep the model, parameters, tool implementations, system instructions apart from the recovery text, turn limits and output schemas constant within each model. Unit of comparison: the complete answer to one task. Five tickets within a billing answer do not count as five independent assignments.

Proposed design per model: 5 cases × 3 cards × 2 independent repetitions × 4 arms = **120 live runs**, 30 per arm. A second model repeats the complete design (240 total). The primary model is the reference in `experiments/prices.json`. Before freeze select an independently identified second tool-calling model if the author can fund the full replication. Pin snapshot identifiers, inference settings, maximum output and dates. If only the primary model is approved, keep the one-model limitation. Never choose the second model from first-model quality results.

For each model, card and repetition, run one block of all four arms in seeded, shuffled order. Freeze the seed and schedule, balance arm positions across blocks as evenly as possible, and interleave cases, repetitions and models. Start a fresh conversation per run, with no answer or memory from another run. In live cases complete each block close in time and log timestamps and provider failures. Score claims against the evidence actually available to that run. Live retrieval drift is a limitation. Simulated arms always see the identical immutable world.

## Outcomes and blinded scoring

**Binary gates.** Reuse the case checkers in `experiments/checks.py` on a local view containing the actual final answer and its raw source-tool returns. Preserve tool names and run-to-answer associations. Record JSON validity, required shape, number of verifiable items and number correct. A passing answer must have valid JSON, correct shape, at least one verifiable item and all checked items correct. Record zero verifiable items separately. C1 checks keyword–volume pairs, C2 evidence URLs, C3 work identifiers and titles, C4 object IDs and C5 run/job/log and linked issue or PR facts. These gates can accept omissions or the right identifier attached to the wrong explanation.

**Primary outcome: judged quality, 0–4 per answer.** Score three equally weighted dimensions separately: factual correctness and attribution to the right entity, completeness against the card's requirements, and usefulness of the explanation or next action. Before freeze, enumerate the required items per card and identify material errors (for example, attributing one customer's payment to another). Where a card calls for several tickets, themes or causes, check each against the key before assigning dimension scores.

| Score | Correctness | Completeness | Explanation or action |
|---:|---|---|---|
| 0 | Absent, unusable or materially contradicted throughout | No required item answered | No usable explanation or action |
| 1 | Main conclusion wrong or attached to the wrong entity | Fewer than half the required items | Advice conflicts with the evidence or task |
| 2 | At least one material claim wrong or unsupported, with some correct findings | At least half covered but a material requirement missing | Partly usable, with a material gap in reasoning or action |
| 3 | Main claims supported with a minor factual imprecision | All material requirements covered, one or more minor omissions | Usable and supported with a minor ambiguity |
| 4 | All material claims correct, supported and attributed correctly | All listed requirements covered | Explanation ties evidence to a specific, justified decision or limitation |

A missing or unusable final answer scores zero on all dimensions. Score an intelligible answer even if its JSON gate fails. For partial responses use the anchors dimension by dimension. The primary score is the mean across dimensions and two raters. Retain and publish aggregate dimension scores, raw rater disagreement and counts of material errors.

Two independent human readers competent in the case and language score each answer separately. A coordinator who does not score holds the sealed mapping. Give raters only the task, rubric, answer and normalized reference facts: full fixture truth for C4/C5, a standardized per-run evidence sheet from raw returns for C1–C3. Strip arm/model names, timing, call IDs used for recovery, prompt scaffolding and tool/expand annotations from the judging packet, without removing any substantive claim or evidence ID. Present individual answers in independently shuffled order under opaque random IDs. Calibrate on written examples outside the held-out set. Lock both score sheets before unmasking. Record suspected unblinding, report ordinal weighted agreement by dimension, and keep both original ratings even if a third blinded reader later clarifies a discrepancy. Private provider data stays inside the authorized judging environment.

**Secondary diagnostics.** Run `tracetrim.grounding.unsupported` on the run with original tool evidence, label it as a screen for unsupported numbers and codes, and report its count per answer beside gates and quality. Record turns, tool and expand calls, tokens delivered including recovery replies, and raw-output tokens with `o200k_base`. Record model usage separately. Show compression and quality side by side, including cases where the configured policy trims nothing.

## Hypotheses and planned analysis

H1 (primary): P preserves judged quality relative to R within a proposed margin of **0.25 points on the 0–4 scale**, one quarter of a rubric level. The author must accept or revise this practical tolerance before freeze. H2 (secondary): I preserves quality relative to P using the same margin. C versus R describes the effect of serialization alone, and P versus C describes the additional effect of the delivered projection and recovery mechanism. H3 (secondary): P serves fewer tool-result tokens than C averaged over the predefined cases. Report case-specific regressions even when the overall contrast favors projection.

For each model separately, compute the paired difference within each complete block, average the two repetitions per card, then give equal weight to cards within cases and cases overall. Report each card and case, the mean difference and a 95% interval from a stratified paired bootstrap (10,000 draws, fixed seed 311, resample whole cards within case retaining arms and repetitions). For H1 the lower endpoint must exceed −0.25 to support preservation on this task set; an interval straddling the margin is inconclusive. Apply the same descriptive rule to H2. Report H3 as a paired token difference with its interval. Report paired gate failures and grounding counts alongside the primary score. H1 on model one is the sole confirmatory contrast; report replication and secondary comparisons separately. Three cards per case give coarse uncertainty estimates. These selected cases and tasks cannot support a universal equivalence claim.

## Stopping, costs and custody

Use separate, private campaign ledgers and the project's cap and warning checks. No new run starts without author approval, explicit caps, or after a warning until reviewed. Stop the DataForSEO cases after their first eight runs (one complete four-arm block per case, scheduled before any other provider block) so the author can reconcile provider usage. Count failed requests. Retain the existing 40-turn and 80-call guards. Guard exhaustion, a model refusal or a malformed answer is an outcome; score the available response. Retry a transient infrastructure failure at most once in its original block, record both attempts, and flag any still-missing arm. Never retry a disappointing answer. Stop at the fixed sample or an operational limit, with no quality-based early stopping. Report incomplete blocks and attrition by arm. Use complete blocks for the main contrasts and show worst/best possible quality bounds for missing arm scores (0 and 4). An interrupted campaign cannot be declared a successful replication.

Estimate the plan privately with `experiments.budget.measured` from the original raw-run metadata, with recovery overhead from the skill and tool definition. Reprice the second model at its approved rates before committing to the replication. Include provider fees separately and review remaining headroom at each checkpoint. Publish only relative costs if reported.

Enforce the author's domain allowlist in the tool wrapper on all website targets and page reads. Use DataForSEO for web search. Exclude Tavily. Keep raw DataForSEO payloads, SERP text, third-party URLs, judging packets and private ledgers local. Only derived field paths without values and permitted aggregates may leave the private store for these cases. OpenAlex and synthetic material follow `LICENSES.md`. Log actual input files and counts before analysis. Later freeze and execution require separate authorization. Record any post-freeze deviation with its date, reason and affected blocks.
