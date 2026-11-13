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

## Deviations

None so far.
