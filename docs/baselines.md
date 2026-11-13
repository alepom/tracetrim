# Baselines: does learning add value over minification and a static filter?

A reviewer's central question for the preprint: a learned projection removes tokens, but how much of
that would a lossless minifier, or a competent static filter that never looks at a trace, remove
anyway? This page fixes the comparison **before any number is computed**. The rules below were
written from general API conventions and committed on their own; the commit order in the repository
shows that they precede the results. They are not to be changed after the results are seen. A rule
that turns out to be a bug (it crashes, or does not do what is written here) is fixed and the change
is recorded under *Deviations*, dated.

Offline, no spend: the original study's Phase A traces (`$TRACETRIM_TRACES/C1..C5/A*.jsonl`, eight
runs per case), read only. Script: `experiments/baselines.py`, results: `results/baselines.json`,
test: `tests/test_baselines.py`.

## Arms

All arms are applied to the same held-out calls: every tool call of every Phase A run, leave-one-out
per run and per case. The real-API cases (C1–C3) and the simulated ones (C4–C5) are reported
separately and never pooled.

1. **raw**: the tool outputs as returned.
2. **minified**: the same JSON written compactly (`separators=(",", ":")`, no indentation, no ASCII
   escapes). Nothing is removed. Outputs that are not a JSON object or array (prose, errors) are left
   as they are, in every arm, as the library does.
3. **static**: the static filter below, then minified. It reads only the payload it is given: no
   trace, no usage, no tool name, no other run.
4. **learned**: the library's default projection as it is served today (0.2+ defaults): learned from
   the *other* seven runs of the case with `analyse()` at its defaults (transformed values
   recognised), `learn()` at its defaults (prudent, full source, unseen fields kept, identifying
   fields protected), applied with `keep_empty=True, compact=True` (what `Policy` serves).
5. **learned+static**: the static filter applied to the learned arm's output, then minified.

## The static filter

Applied recursively to a parsed JSON payload, in this order.

**(b) Generic metadata keys.** A key is removed, with its whole value, at any depth, when its name,
compared case-insensitively, is exactly one of:

| Group | Names | Why it is metadata |
|---|---|---|
| Transport and task envelope | `version`, `api_version`, `status_code`, `status_message`, `time`, `cost`, `tasks_count`, `tasks_error`, `path`, `se_type`, `check_url`, `datetime` | API version, HTTP-like status of the call, timing and billing of the request, the endpoint path, the echo of the request type, a URL to poll for the result, when the request was served. They describe the call, not the data. |
| Request tracing and caching | `request_id`, `requestid`, `x_request_id`, `trace_id`, `correlation_id`, `idempotency_key`, `etag` | Identify the HTTP request or a cache version, for logs and retries. |
| Type tags and environment flags | `object`, `livemode` | A type discriminator repeated on every record (`"object": "invoice"`) and a test/live flag. |
| Pagination | `has_more`, `next_page_url`, `prev_page_url`, `previous_page_url`, `first_page_url`, `last_page_url`, `next_url`, `prev_url`, `previous_url`, `next_page`, `next_cursor`, `prev_cursor`, `previous_cursor` | How to fetch the next page; an agent that answers from the page it has does not need them. |
| Hypermedia | `_links` | HAL navigation links to related resources. |

**(a) Empty values.** Then `null`, the empty string `""`, empty lists `[]` and empty objects `{}` are
removed: as values of an object key, and as elements of a list. Removal is bottom-up, so a container
that becomes empty because everything in it was removed is removed too. `0`, `false` and
whitespace-only strings are kept: they carry information. If the whole payload becomes empty, an
empty container of the same kind is returned (a list stays a list), as the projection does.

Then the result is minified.

### What was deliberately left out, and why

Written before any number, to show the list is meant as a competent baseline, not a strawman in
either direction:

- `data`: in many APIs (Stripe lists, JSON:API) it *is* the payload. A filter dropping it would be a
  strawman that removes everything.
- `status`, `message`, `error`, `errors`: the status of a subscription or of a CI job, a commit
  message, an error the agent must report are content.
- `url`, `id`, `*_id`, `html_url`: identifiers; removing them is what made the agent invent
  identifiers in Study 1.
- `total_count`, `items_count`, `result_count`, `count`: they can answer "how many".
- `timestamp`, `created`, `updated_at`: dates of the records, content.
- bare `next`, `previous`, `self`, `links`, `meta`, `page`, `limit`, `offset`: common English words that
  also name content fields (the links of a parsed page, for instance).
- `node_id` and other API-specific names: the list is meant to be generic, not fitted to the tools of
  the five cases.

A note on provenance: some names (`tasks_count`, `tasks_error`, `se_type`, `check_url`, `path`, `time`,
`cost`) were given as examples in the request for this baseline and are the envelope of a search-data
API used in C1 and C2. Keeping them, if anything, favours the static filter on those cases. `path` and
`time` are ambiguous names and could also name content; they stay because they are in the request
and because the rule is fixed before the results, whatever they show.

## Measures

The definitions are Study 1's, reused from the library and the Phase A analysis, so that the numbers
are comparable with Table 1 of the paper.

- **Tokens**: `tiktoken` `o200k_base` (`tracetrim.measure.tokenizer`), each output counted once,
  summed over every call of the held-out runs. Reported as tokens removed with respect to raw and with
  respect to minified.
- **Usage of the held-out run**: Study 1's detection, `analyse(trace, normalize=False,
  comma_grouping=False)`, as in `experiments/analyze_a.py`. Sensitivity: the same misses counted
  against the library's current detection (`analyse()` at its defaults, which also recognises
  transformed values).
- **Miss**: a held-out structured call in which the arm removes a field (a leaf path, list indices
  collapsed) that the run used later. For the learned arm this is Study 1's `removes()` with the
  prudent projection; for every arm it is also computed as the used paths present in the raw payload
  and absent from the arm's output, and the two must agree for the learned arm.
- **Denominators**: misses are reported as counts and as a share of the held-out structured calls
  (the same denominator for every arm); the learned arm's trimmed calls (structured, tool seen in
  the other runs), Study 1's denominator, are reported too.
- **Input check**: before anything is reported, the number of runs, calls and raw tool-output tokens
  per case, and Study 1's leave-one-out misses recomputed with its own settings, must equal
  `results/phase_a.json`; the minified totals must equal `results/reserialize.json`. A parser failing
  silently would give plausible, wrong numbers.

## Reading rule

No threshold and no significance test (eight runs per case). Per case, the question is answered by
two comparisons on the same calls: tokens the learned arm removes beyond the static arm, and misses of
the learned arm against misses of the static arm. Learning adds value in a case when it removes
clearly more than static at a comparable number of misses; where it does not, the paper should say
so.

## Results (13 November 2026)

`TRACETRIM_TRACES=… PYTHONPATH=src python -m experiments.baselines`, offline, from the 40 Phase A
runs; every figure below is in `results/baselines.json` with its counts.

**Input check, passed for every case**: runs (8 per case), calls (C1 173, C2 136, C3 61, C4 84, C5 104),
raw tool-output tokens and Study 1's own leave-one-out (prudent tokens, trimmed calls, misses of both
projections) recomputed here equal `results/phase_a.json`; the minified totals equal
`results/reserialize.json`; for the learned arm, Study 1's `removes()` and the generic form of a miss
agree on every call. Outputs that are not JSON (C1 6, C2 46: page parses and failed searches, C5 40:
job logs) are left as they are in every arm.

### Tokens removed with respect to raw

| Case | Calls (structured) | Raw tokens | minified | static | learned | learned+static |
|---|---:|---:|---:|---:|---:|---:|
| C1 | 173 (167) | 817,865 | −24.6% | −25.9% | −63.3% | −63.4% |
| C2 | 136 (90) | 527,542 | −9.0% | −9.2% | −12.9% | −13.0% |
| C3 | 61 (61) | 2,262,885 | −15.7% | −20.1% | −51.9% | −54.9% |
| C4† | 84 (84) | 154,833 | −33.2% | −46.7% | −59.3% | −69.3% |
| C5† | 104 (64) | 599,808 | −18.3% | −20.1% | −65.0% | −66.5% |

### Tokens removed with respect to minified, and learning beyond static

| Case | static | learned | learned+static | learned vs static | learned+static vs static |
|---|---:|---:|---:|---:|---:|
| C1 | −1.7% | −51.3% | −51.4% | −50.5% | −50.6% |
| C2 | −0.2% | −4.3% | −4.4% | −4.1% | −4.2% |
| C3 | −5.3% | −43.0% | −46.5% | −39.8% | −43.6% |
| C4† | −20.1% | −39.0% | −54.0% | −23.7% | −42.4% |
| C5† | −2.2% | −57.2% | −59.0% | −56.2% | −58.0% |

Of the static filter's saving over minified, the metadata keys alone give C1 1.7%, C2 0.2%, C3 0.4%,
C4 8.0%, C5 1.1%; the empty values alone C1 0.0%, C2 0.0%, C3 5.0%, C4 12.4%, C5 1.2% (diagnostics,
`static_rules_vs_minified`; the rules were not changed).

### Misses: held-out structured calls in which the arm removes a field the run used later

Raw and minified: 0 in every case (checked). Share of the structured calls.

| Case | Structured calls | static | learned | learned+static |
|---|---:|---:|---:|---:|
| C1 | 167 | 0 (0.0%) | 1 (0.6%) | 1 (0.6%) |
| C2 | 90 | 0 (0.0%) | 8 (8.9%) | 8 (8.9%) |
| C3 | 61 | 0 (0.0%) | 2 (3.3%) | 2 (3.3%) |
| C4† | 84 | 45 (53.6%)* | 9 (10.7%) | 48 (57.1%)* |
| C5† | 64 | 0 (0.0%) | 3 (4.7%) | 3 (4.7%) |

Pooled within each group (never across): real C1–C3, removed vs raw minified −16.7%, static −19.8%,
learned −48.8%, learned+static −50.7%; misses static 0/318, learned 11/318. Simulated C4–C5: −21.3%,
−25.5%, −63.8%, −67.0%; misses static 45/148, learned 12/148, learned+static 51/148. With the library's
current usage detection (transformed values recognised) the misses are the same in every cell.
†Simulated tools.

*All 45 static misses are one field, `data[].object`, Stripe's type tag, and all are a
coincidence of the detection, not a use: the value `"customer"` (40 calls of `stripe_search_customers`)
or `"charge"` (5) matches the argument *name* of the agent's later calls
(`stripe_list_charges {"customer": "cus_…"}`), which the word matcher reads as a reuse of the value.
Without that field the static filter has no miss in C4, and learned+static has exactly the learned
arm's 9. The learned arm keeps `object` for the same reason: it learned the same coincidence from the
other runs.

### Reading

- **Learning adds value beyond a static filter in four cases of five.** On the same calls the learned
  projection removes 40–56% of what is left after the static filter in C1, C3 and C5, and 24% in C4,
  at 1–3 misses in C1, C3 and C5 (0.6–4.7% of calls) and 9 in C4 (10.7%), against none for static
  apart from the coincidence marked *. A field filter
  that knows nothing of the task cannot reach that: what it removes is metadata, and the payloads
  here carry little of it.
- **Where not: C2.** The content-gap analyst cites most of what the search results contain and the
  rest of its input is prose; learning removes 4.1% beyond static, at 8 misses in 90 calls (7 of them
  `items[].type`). Here the honest baseline is minification.
- **Why the static filter is weak on C1 and C2, and not a strawman.** The search-data MCP server
  already returns a trimmed envelope (`id`, `items`, `status_code`, `status_message`, `total_count`)
  with almost no empty value (4 nulls and 4 empty lists in 167 payloads): the static part of the job
  is done upstream, so the filter can only remove the status pair and `check_url`. On OpenAlex (C3)
  and the Stripe-like payloads (C4), which carry nulls, empty lists and per-record type tags, it
  removes 5% and 20% beyond minification.
- **Static and learned are complementary, not rivals.** The prudent projection keeps every field
  usage detection cannot judge, and nulls and booleans are never judgeable: so it keeps exactly what
  rule (a) and the `livemode`-type rules remove. Applied after the projection, the static filter
  adds 3.5 points in C3 and 15 points in C4 (−39.0% → −54.0% of minified), and no miss beyond the
  learned arm's except the `object` coincidence above.
- **Minification is a large share of every figure** (9–33% of raw): part of what Table 1 credits to
  the projection is lossless and would be obtained by any serialiser.

## Deviations

None. Two things were added after the first run, neither a change of rule: (1) switches to apply
rule (a) or rule (b) alone, to split the static saving between them (a diagnostic, not an arm);
(2) the values behind the static arm's misses, to check what was removed. The key list, the order of
the rules and the arms are as committed in the pre-registration.
