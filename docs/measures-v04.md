# What was measured on the v0.4 work

The measures of `protocol-v04.md`, made on 2026-11-11: 100 runs of `gpt-5.6-terra` through tracetrim's
function-calling adapter. Results: `results/v04/e1.json`, `results/v04/e2.json`, recomputed from the runs by
`python -m experiments.analyze_v04`. The protocol was frozen before the first paid run; its five deviations, all
decided before the runs they concern, are at its end.

These are small experiments on one model. They say what happened in these runs, not what holds in general.

## The hypotheses, as stated before the runs

| | Hypothesis | Outcome | On how many cases |
|---|---|---|---|
| H1 | With the strategy text, gates and coverage of the prompt's requirements are no worse | **failed** | 4 comparisons of 5 hold; in C1, strategy with trimming passed the gates in 4 runs of 5 against 5 of 5 |
| H2 | On the billing case the strategy lowers the number of turns | **failed** | median 4 turns with it; minimum 3 without |
| H3 | — | removed before freezing | it concerned arms that are not run |
| H4 | The strategy does not undo the saving of trimming | **failed on C4**, holds on C1 | 2 cases |
| H5 | A field recovered with `expand` and written in the answer is kept from the next run | **holds** | 16 recoveries of 16, in 10 sequences |
| H6 | A field the agent uses and rewords needs promotion to come back | **not testable** | the model reworded it in 2 sequences only, both with promotion on, and wrote it at the next run |
| H7 | Sampling reaches a field the agent does not ask for | **failed** | one run per group: the requirement was met in both |

## E1 — the strategy level of the skill

Four arms (strategy text × trimming), five runs each, on three agents: an editorial plan and a content-gap
briefing on a real search-data API (C1, C2), billing support on a simulated API (C4). On C2 only the two arms
without trimming: the configuration tracetrim builds there trims no tool. 50 runs.

- **On this model the strategy changed nothing that could be measured.** Turns, tool calls, billed tokens and cost
  stayed inside the ranges of the arms without it, in all three cases.
- **This model already issues calls together**: without the strategy, 77%, 68% and 77% of the turns that made a
  call made several. The effect of the strategy on a model that issues calls one at a time was not measured.
- **The strategy did not override the prompts.** The parameter values the prompts prescribe were respected in all
  50 runs.
- **One run asked a question instead of working**, in the arm with strategy and trimming. One in 25 with the
  strategy, none in 25 without, none in the 60 live runs of the original study.
- **Trimming**, which E1 was not about: tool-output tokens down 44% on C1 and on C4, cost of a run down 30% and
  24%, gates passed in 5 runs of 5 in the trimmed arm of both.

The strategy stays off by default.

## E2 — adaptive learning

The task starts needing two fields the projection removes. Sequences of five runs in one work directory, seeded
with the eight runs of the original study. On the simulated case (a copy of its world, `experiments/simulated/v04`):
promotion off/on × sampling off/on, two sequences per arm, 40 runs. On the real case: the arms without promotion
(base, sampling), one sequence each, 10 runs.

- **The fields came back by themselves.** In every sequence the model got them once, wrote them, and from the
  next run they were in the filtered results with no `expand`: from the second run in 8 sequences of 10, from the
  third in 2. The requirement the variant adds was met in all 50 runs.
- **Promotion made no difference here**, because the model wrote the values it recovered: ordinary learning was
  enough. Whether promotion helps when a value is used and never written is still open.
- **Sampling only cost tokens here**: on the simulated case about 35% of tool-output tokens removed against 42%
  without it. The model never needed it: it asked with `expand` or found another way.
- **Another way to a removed field.** In two runs of the simulated case the model did not call `expand`: it called
  another tool, whose results are not trimmed, and read the fields there. That is a limit of the design on that
  case (H7 could hardly be tested), and a behaviour to know: on an API billed per call that route has a price.
  On the real case no other tool returns the two fields, and the model used `expand` in both sequences.
- **A tool was switched off once**, on the real case: in the first run of the base sequence the model asked back
  the values of the Trends tool, and the tool stopped being trimmed. The saving of that sequence went from 33% to
  between 26% and 38% in the later runs; in the sampling sequence, where it did not happen, it stayed at 35–40%.
- **`expand` requests that gave nothing back**: 10 of 38. Eight times the model made up or truncated the id of
  the result, asking for `items` or for the rows after the tenth, mostly on results that were not trimmed: it
  seemed to want more rows, not hidden fields. Twice it wrote an index (`data[0]`) where paths use `data[]`.

Promotion and sampling stay off by default.

## Spend

| | Runs | Model, USD |
|---|---:|---:|
| E1 | 50 | 12.46 |
| E2, simulated case | 40 | 2.63 |
| E2, real case | 10 | 3.99 |
| **Total** | **100** | **19.08** |

Three more attempts were cut short by errors of the model's service and repeated; their cost is in the totals. The
real cases made 813 calls to the search-data provider, billed apart.

## What these measures do not show

- One model. Other models may issue calls one at a time, call `expand` differently, reword more.
- Few runs: five per arm in E1; in E2 two sequences per arm on the simulated case and one on the real case, which
  is a single trajectory per arm.
- The gates and the test of the added requirement look for identifiers, links, names and keywords.
- Only the function-calling adapter was exercised. That the MCP proxy takes the same decisions is covered by a
  test; whether a host loads the skill over MCP was not tried.
- Promotion where reuse cannot be seen, and sampling where the agent has no other way, were not put to the test:
  the model's behaviour did not create those situations.

## Commands

```bash
python -m experiments.analyze_v04                 # the tables, from the runs (they are not in git: see docs/traces-v04.md)
python -m experiments.run --v04 schedule          # the runs of the protocol, in order
python -m experiments.run --v04 dry-run /tmp/dry  # every arm with a stub in place of the model: no spend
python -m experiments.budget --v04                # the estimate made before the runs
python -m experiments.v04_preconditions           # what the protocol takes for granted
```

The last three need the traces of the original study (`TRACETRIM_TRACES`, or the Zenodo dataset at the repository root).
