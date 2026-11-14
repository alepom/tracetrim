# What was measured for 1.1

After 1.0, an analysis of where tracetrim could still save, and of what it could check, on the runs already
recorded (no model called, no protocol), and one live measure of the option it led to (protocol-v11.md). Every number below comes from a script in `experiments/`
and a file in `results/v1/`. These measures are not part of the paper.

## What is left after trimming

`python -m experiments.remaining`, `results/v1/remaining.json`.

On the eight runs per case of the original study, each run held out in turn and trimmed as at run time (the
prudent projection learned from the other seven, current defaults, compact JSON), the tokens the model still
receives are split by why each field is still there. Shares of each case's raw tool-output tokens:

| Case | Reaches the model | Used by the run | Identifiers not used | Used in other runs | Not judgeable | Prose (untouched) |
|---|---:|---:|---:|---:|---:|---:|
| C1 | 36.7% | 16.1% | 8.1% | 8.9% | 3.6% | 0.0% |
| C2 | 42.2% | 25.7% | 5.9% | 7.8% | 2.8% | 44.9% |
| C3 | 48.1% | 14.1% | 15.9% | 9.0% | 8.4% | 0.0% |
| C4 | 40.7% | 16.7% | 7.5% | 5.7% | 10.8% | 0.0% |
| C5 | 19.5% | 2.0% | 14.5% | 0.3% | 2.8% | 15.5% |

"Reaches the model" counts structured outputs; prose is counted apart. "Used by the run" is what a perfect
projection for that run would keep. The categories are removed one after the other (fields never seen while
learning, not judgeable, used in other runs, identifiers): each column is what removing it would save after
the previous ones.

- **Identifiers are the largest share left on C3 and C5.** There they are mostly ids and links of nested
  entities: a work's concepts, authors and institutions on OpenAlex; an issue's labels and its author's links
  on GitHub. Not the record's own.
- **Fields used in other runs** (6–9%) are the variance of the task: the projections per mode of use measured
  for 0.4 gave no benefit (`docs/measures-v04.md`).
- **Not judgeable** values are mostly booleans (14.4% of what C4 still receives, 8.0% on C3, 6.7% on C5) and
  small numbers (6–7% on every case) (`python -m experiments.remaining_detail`,
  `results/v1/remaining_detail.json`). Removing them safely needs a run with and a run without the field: not
  done here.
- **Prose** is 45% of C2's tokens and 16% of C5's: field projection does not reach it.

## The format is nearly exhausted

Same payloads, already projected and written as compact JSON (`experiments.remaining_detail`):

| Case | Columnar JSON (still JSON) | TOON (`tracetrim.structural`) |
|---|---:|---:|
| C1 | −1.5% | +11.3% |
| C2 | −0.4% | +6.0% |
| C3 | −4.9% | +9.6% |
| C4 | −0.4% | +12.4% |
| C5 | −1.3% | +13.1% |

Lists of records with the same flattened keys, written as `{"columns": [...], "rows": [[...]]}`, save at
most 5%. The TOON encoder of `--structural toon` is larger than compact JSON on these payloads: its tables
need flat records, and its indentation costs more than JSON's punctuation once spaces are gone. No change.

## Which identifiers to protect: `--protect-ids record`

`python -m experiments.identifiers`, `results/v1/identifiers.json`.

Since 0.2 every field named like an identifier is kept, at any depth. Two narrower rules, held out as above;
tokens removed on the trimmed structured calls, and calls in which a field the run used is removed:

| Case | Calls | All (default) | Record + names (`record`) | Record only | None | Misses: all / record / record only / none |
|---|---:|---:|---:|---:|---:|---|
| C1 | 166 | 63.3% | 64.1% | 64.4% | 67.6% | 2 / 2 / 3 / 3 |
| C2 | 90 | 23.4% | 24.6% | 24.7% | 25.4% | 8 / 8 / 9 / 9 |
| C3 | 61 | 51.9% | 62.0% | 63.4% | 63.4% | 2 / 2 / 3 / 3 |
| C4 | 84 | 59.3% | 65.2% | 65.6% | 66.6% | 9 / 9 / 9 / 9 |
| C5 | 64 | 76.9% | 89.5% | 89.8% | 92.5% | 3 / 3 / 3 / 3 |

- **Record only** (ids and links with at most one list level and no object between the list and the field)
  lost three more fields than the default, one per case on C1–C3: all of them names or titles
  (`items[].links[].title`, `results[].authorships[].institutions[].display_name`).
- **Record + names** keeps names and titles at any depth as well. It removes 10.1 more points on C3, 12.6 on
  C5 and 5.9 on C4, and loses no field the default keeps.
- What this cannot show: whether an agent makes up a nested identifier it no longer sees. The study saw
  agents make up the record's own identifier, which both rules keep; nested ones were never removed in a
  live run before the measure below. So `record` is an option, off by default; the check further below is what
  would catch a made-up identifier.

## Live: `record` against the rule of 1.0 (protocol-v11.md, E4)

`python -m experiments.run --e4 analyse --json`, `results/v1/e4.json`. Frozen before the first paid run
(`out/e4/frozen.json`); 30 runs, 5 per arm and case, none flagged, none repeated. Both arms trim with a
configuration fixed for every run (tracetrim's own, from the case's Phase A traces) and offer `expand`; they
differ only in the identifier rule. Medians per arm:

| Case | Saving T (all) | Saving TR (record) | Difference | Cost per run, TR vs T | Gates T / TR | Unsupported values T / TR |
|---|---:|---:|---:|---:|---|---|
| C3 | 49.5% | 56.0% | +6.6 points | −2.8% | 5/5 / 5/5 | 0 / 0 |
| C5 | 64.0% | 74.6% | +10.6 points | −19.3% | 5/5 / 5/5 | 2 / 2 |
| C4 | 51.6% | 57.5% | +5.9 points | −4.4% | 5/5 / 5/5 | 0 / 0 |

"Saving" is 1 − tokens the model received from tool results / tokens of the tools' outputs; with the `expand`
replies added in, it changes by less than one point (C5) or not at all. The unsupported values on C5 are `600`
(a deployment's default timeout, written from what the model knows), twice in each arm.

- **H1 holds** on C3 (+6.6) and C5 (+10.6); C4, described: +5.9, as offline. On C3 the gain is smaller than
  offline (+10.1); the live searches return other works than the recorded ones, and why the share differs was
  not examined.
- **H2 and H3 hold** on every case. Calls and `expand` calls have the same median in the two arms; turns too,
  except on C5 (5 for T, 6 for TR: 7 and 8 turns in two TR runs), where the cost per run still fell.
- **The answers, read one by one** after the results (not in the protocol):
  - C3: every work cited, in all ten runs, is in the run's OpenAlex outputs with the same title, DOI and year
    (`openalex_id` matched to the record); 10–12 works per answer, on topic in both arms, with syntheses and
    open questions of the same quality.
  - C4: the strict audit (`experiments.c4_audit`) passes 21 of 25 diagnoses in each arm. The four misses are the
    same in both: the reason of the declined payment, written as "a card error" rather than "insufficient
    funds" (as in E1, `docs/measures-v04.md`). Double charge, cancellation date, refund status and the higher
    invoice are right in every run.
  - C5: the ten answers agree: three root causes, the same runs, jobs and steps, every error line found verbatim
    in the logs, the same issues and pull requests.
- **What follows, as the protocol says**: `record` is a candidate for the default in a later minor release,
  with `--protect-ids all` to restore the rule of 1.0. In 1.1 it stays an option.

What this cannot show: one model, five runs per arm, one task per case; no arm without trimming.

## Values the answer cites and no tool returned

`python -m experiments.grounding`, `results/v1/grounding.json`; the check is `tracetrim.grounding`.

At every closing tracetrim has the answer and the original of every output of the run. It looks for the
numbers of three digits or more, and the codes containing them, that the answer cites, under the same forms
usage detection recognises (amounts in minor units, dates from timestamps, grouped numbers), in the outputs
and in the agent's own arguments; a rounding of a value, and a value times 100 written as a percentage, count as
found (added after a review; the results below are unchanged by it). Years, so any 4-digit number from 1900 to
2099, are not checked. A value also unsupported where nothing was
filtered is not held against trimming.

On every recorded run with an answer, by arm (Study 1: Phase B replays of 0.1; Study 2: Phase D replays of
0.2; E1 of `protocol-v04.md`, live):

- **It finds what the study found.** On C3, Study 1, the projection had removed the works' identifiers and
  the agent wrote plausible ones (`W4391234567`, `W4391234568`, …): 24 values in the prudent arm, 23 in the
  aggressive one, none in the raw arm. One also in the structural arm.
- **It stays quiet elsewhere.** No value held against trimming in Study 2 (with the safeguards of 0.2), in
  E1, or on C1, C2, C4 and C5 in any study.
- **What it lists without blaming trimming**: a sum the agent computed (`58,00`, C4, also without
  trimming), a deployment's default timeout it knew (`600`, C5, in every arm), one token of a search result
  page (C2, an arm without trimming).
- **The first version of the script was wrong.** It read a filtered result together with its `[trimmed]`
  line, which does not parse as JSON, so amounts and dates were matched only as words: 181 values on C4 were
  reported as coming from hidden fields. Split from that line, there are none. The library splits it the
  same way, and a test holds it.

Also on the live runs of Study 1 (Phase C, C1 and C4, three raw and three prudent runs each): nothing listed.
The one pair out of 180 that the post-hoc re-check of those runs found wrong (`results/phase_c_recheck.json`)
is a volume written as 0 for a keyword whose outputs gave 40: a value too small to attribute, and an
association between two values, which this check does not see. It does not see either a true value given to
the wrong item (the volume of one keyword attributed to another).

## Commands

```bash
PYTHONPATH=src .venv/bin/python -m experiments.remaining
PYTHONPATH=src .venv/bin/python -m experiments.remaining_detail
PYTHONPATH=src .venv/bin/python -m experiments.identifiers
TRACETRIM_TRACES=/path/to/traces PYTHONPATH=src .venv/bin/python -m experiments.grounding   # E1 runs, if elsewhere
```

C1 and C2 hold third-party API responses and are not in the dataset: the scripts need the local traces. For
those cases `results/v1/grounding.json` keeps counts only.
