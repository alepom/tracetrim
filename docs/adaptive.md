# Adaptive learning

How automatic mode corrects a projection while the agent works: evidence from `expand`, promotion of
fields, sampling without filter. Unreleased: this page describes the branch
`feat/adaptive-learning-mcp-skills`. Everything here was checked by tests and by an offline demo with a
scripted agent, and then measured on one model in a small experiment: [measures-v04.md](measures-v04.md).
In short: fields the agent recovers and writes come back by themselves; promotion and sampling made no
measurable difference there, and what they are for (a value used and never written, a field never asked
for) did not occur. Both stay off by default.

Promotion and sampling are off unless you turn them on. Evidence is always recorded.

```bash
tracetrim proxy --servers mcp.json --auto tracetrim-data/ --promote --sample-rate 0.1
```

```python
from tracetrim.auto import AutoTrim
from tracetrim.evidence import Adaptive

tt = AutoTrim("tracetrim-data/", adaptive=Adaptive(promote=True, sample_rate=0.1))
```

## What already happened before this work

A field the agent gets back with `expand` and then **writes** is found by ordinary learning: its value
is in the run's answer, so the next learning pass keeps it. That needs no promotion and was true in
0.3.5. What ordinary learning cannot see:

- a field the agent needs and never writes (it translates it, or only decides from it);
- a field the agent would use and never asks for, because it does not call `expand`.

Until now the first case ended with the whole tool no longer trimmed. The second had no way out.

## Evidence

During a run tracetrim notes how each result was exposed and every `expand` request: the tool, the
original result (the call's position in the run's trace), the names asked for, the paths given back.
When the run closes this becomes evidence, per field and per kind, as the set of runs that showed it:

| Kind | Meaning |
| - | - |
| `requested` | the agent asked for the field, or for its group, by name |
| `recovered` | an `expand` gave it back, however it was asked for |
| `reused` | after getting it back, the agent wrote its value |
| `seen_full` | the field reached the agent in a sampled, unfiltered result |
| `used_in_full` | ... and the agent then wrote its value |

- **Runs are counted, not requests.** A retry, or five requests for one field in a run, are one piece
  of evidence. For the switch-off of a tool, a result counts as expanded once, and only when a removed
  field came back.
- **Asking for everything is not asking for each field.** `"*"`, or a prefix that spans several of the
  groups listed in the `[trimmed]` line, is `recovered` for what it returned and `requested` for nothing.
- **Not seen reused is not useless.** `seen_full` without `used_in_full` says the value was not written
  again. Reasoning is not visible, so nothing is ever removed on that ground.
- With an MCP proxy the evidence also records the server and the upstream tool a call went to.

### What `expand` accepts

- **A path, a group, or everything**: `items[].title`, `items[]`, `"*"`. A path stands for everything under it.
- **The position of an element**, from zero: `data[0].url` is `data[].url` for the first element of `data`
  only, and `data[2]` is the third element whole. The reply holds the elements asked for and no others, in
  their original order; positions can be nested (`data[0].lines[1].sku`) and mixed with plain paths, which
  still mean every element. As evidence an index counts as `[]`: asking for a field of one element is
  asking for that field. Slices (`items[10:]`) are not paths: `expand` gives back what was removed from a
  result, never more rows than the tool returned ([rows.md](rows.md)).
- **An unknown `call_id`** gets a reply that says so and recalls that only a result ending with a
  `[trimmed]` line can be expanded. With one agent (stdio, function calling) it also names the three most
  recent valid ids with their tools. The HTTP proxy never names them: several clients can reach it, and a
  valid id would let one of them expand another's results; there the reply says to copy the id from the
  `[trimmed]` line. It counts against no tool. Until now the reply was `Error: unknown call_id.` and nothing else; in the measures, agents that
  got it had made up or cut short the id, mostly to ask for more rows of a result that was whole.

## Promotion (`--promote`)

A field the projection removes is kept from then on when:

| Evidence | Runs needed | Option |
| - | - | - |
| reused after recovery, or used in a sampled result | 1 | `Adaptive(promote_reused_runs=...)` |
| requested by name, with no visible reuse | 2 | `--promote-runs`, `Adaptive(promote_requested_runs=...)` |

One run is enough when the agent had the value and wrote it; a request alone could be a whim, so it has
to come back in a second, independent run. Promotion starts once trimming has started.

- **The targeted correction comes first.** Fields are promoted before the tool is judged, and a tool
  that just got a promotion starts counting again. With promotion on, a tool is switched off only if the
  requests also came from as many runs as a promotion needs. Switching the whole tool off stays as the
  last resort: it still happens when requests remain frequent, for example an agent that always asks
  for everything.
- **Evidence from more than one run** (`--switch-off-runs N`, `Adaptive(switch_off_runs=N)`; **default
  2**). The expanded results must come from at least N distinct runs: the switch-off is the last resort
  and waits at least as long as a promotion by request. With `--switch-off-runs 1`, the rule up to 0.3.5,
  one run is enough: four filtered results of a tool, two of them expanded, and the tool stops being
  trimmed when that run closes, the first run included. The larger of this and the runs a promotion needs
  applies. Recalculated offline on the ten sequences of E2 (`python -m experiments.switch_off_recalc`,
  `results/v04/switch_off.json`): one tool was switched off as run, in the first run of a sequence; with 2
  or 3 runs asked for, none would have been at that point. What the agent would have asked of that tool in
  the later runs, had it stayed trimmed, is not in the traces.
- **A field that came back by itself counts as a correction too.** When a recovered field is written and
  ordinary learning keeps it, the tool's count starts again as after a promotion: the requests that asked
  for it are settled. The report lists these fields.
- **No oscillation.** Evidence only accumulates and nothing demotes a field on its own.
- **The way back.** Every promotion is kept in `evidence.json` with its reason and the runs behind it.

  ```bash
  tracetrim promotions tracetrim-data/                              # list them
  tracetrim promotions tracetrim-data/ --revert list_orders 'data[].refund_note'
  tracetrim promotions tracetrim-data/ --revert                     # all of them
  tracetrim promotions tracetrim-data/ --restore list_orders 'data[].refund_note'
  ```

  A reverted field is removed again and is not promoted a second time. A running proxy picks the change
  up at its next closing. The operator's choice prevails: if ordinary learning would keep a reverted
  field (its value was seen written), the field stays removed and the report lists it as to review.

## Sampling without filter (`--sample-rate`)

Now and then a result that would be trimmed goes to the agent whole, so that the agent can use what is
normally removed and tracetrim can see it. The tool runs once: only what is passed on differs.

| Option | Default | Meaning |
| - | - | - |
| `--sample-rate` | 0 (off) | share of the results that would be trimmed, passed whole |
| `--sample-max-share` | 0.2 | cost limit: the tokens sampling adds stay within this share of the tokens trimming removed, per tool |
| `--sample-seed` | 0 | the sequence is a function of seed, tool and call number |

- With the default limit at most about one result in six is sampled, whatever the rate, and none until
  trimming has removed something.
- **Restarts.** The call number is in `state.json`: the sequence goes on where it was.
- **Schema changes.** A field the server adds passes through, as before. A field it renames is a new
  field. A promotion whose field no longer exists has nothing to apply to and stays on record.
- Sampled results are counted apart: they are not among the filtered results the switch-off counts,
  and the savings the report estimates are computed on the original outputs, sampled or not.

## The simple way for small outputs (always on)

The line that lists what was removed has a fixed cost, some tens of tokens. On a small or already
compact result it can cost more than the cut saves. So, for every result: when the filtered text, line
included, counts **at least as many tokens** as the whole result re-serialised, the whole result is
passed, without the line. Fewer tokens, and nothing removed.

- Counted with tiktoken `o200k_base`; without tiktoken the comparison is on characters, and the report
  names the unit.
- For learning this is an unfiltered exposure that is **not random**. It has its own kind
  (`full_cheaper`), is counted apart from filtered and from sampled results, and uses none of the
  sampling quota. The report counts, per tool, the results passed this way.
- It applies wherever a projection is applied: automatic mode, `--config`, `AutoTrim`.

## Measures that change nothing

- **List elements reused.** For each outermost list of a tool's output: elements received, elements
  with a value the agent wrote afterwards, whether those are the first ones, and how far down the
  deepest is; next to them, the arguments that fix the size (`limit`, `per_page`, `top`, `depth`...,
  recognised by name: a heuristic). Rows are never cut. `tracetrim.lists`.
- **Argument cardinality.** For each argument of a tool, in how many calls it appears and how many
  distinct values it took. Only counts: the values stay in the traces. `tracetrim.arguments`.

Both are in the report page and in `report.json` (`lists`, `arguments`), also from `tracetrim report`.

## Calls per turn, and a hint on the skill's strategy

When the calls of a run say which model turn made them (`AutoTrim.output(..., turn=...)`; an MCP proxy does
not see turns), the report has one more measure per run: the turns, the turns that made several calls, and
an estimate of the calls that could have been made in an earlier turn.

- **The estimate is a heuristic** (`tracetrim.turns`). A call depends on an earlier result only when one of
  its arguments is an identifier that result returned, recognised by the name of the argument or the shape
  of the value; any other argument is taken as known from the start. The longest chain gives the fewest
  turns a run needed. It cannot know that a model wanted to read one result before asking for the next:
  it is a lower bound on the turns.
- **The hint.** When at most 20% of the turns with calls make several calls, and at least 25% of the calls
  could have been made earlier, over at least 2 runs, the report adds a suggestion: the strategy level of
  the skill ([skill.md](skill.md)) might reduce the turns. It points to the measure behind it, E3 in
  [measures-v04.md](measures-v04.md).
- **Manual, always.** The suggestion is marked MANUAL and changes no configuration: tracetrim never turns
  the strategy on. The thresholds are `tracetrim report --strategy-max-several`, `--strategy-min-earlier`
  and `--strategy-min-runs`, and the arguments of `tracetrim.turns.summarise`; in automatic mode the
  defaults apply, and the report records the thresholds it used.
- On the runs of E3 the hint is given for the agent on which the model made one call per turn (5% of the
  turns with several calls, 77% of the calls could have been earlier) and not for the one on which it
  already grouped them (66%, 19%), nor for either agent once the strategy was on.

## Files

| File | Content |
| - | - |
| `state.json` | counters: runs, filtered and expanded results, sampling, closings |
| `evidence.json` | evidence per tool and field (run names), promotions with their reasons |
| `evidence/run-0007.jsonl` | the notes of one run: exposures and `expand` requests |
| `savings.json` | per run and tool: tokens the tools sent, the same outputs re-written whole, tokens the model received; replies of `expand` apart (since 0.4.1) |
| `report.json`, `report.html` | section `savings`: where the saving of each run comes from, re-serialisation and fields; section `adaptive`: promotions, their effect on tokens, evidence, sampling; `turns`: calls per turn |

Every file is written aside and renamed: a process killed while writing leaves the previous content.
One lock protects what tool calls and the learning thread share. Work directories of 0.3.5 load as they are.

## The offline demo

```bash
python -m experiments.adaptive_demo        # writes results/checks/adaptive_demo.json
```

A scripted agent and a simulated tool, through both adapters (`AutoTrim` and the MCP proxy), with the
same outcome in both. After five runs the task changes and needs a field that was being removed; ten
runs follow. A check of the mechanism, not a measurement: a real agent decides by itself whether to
call `expand`.

| The agent... | Without the new functions | With |
| - | - | - |
| asks the field back and writes it | kept again from run 2 (ordinary learning) | the same; the promotion is on record |
| asks it back and never writes it | never: after 2 requests the tool is switched off, saving left 11% | `--promote`: kept from run 3, saving left 54% |
| never asks | never: 10 answers of 10 lack the field | `--sample-rate 0.34`: kept from run 5 to 10 depending on the seed (median 6, twenty seeds) |

Before the change the saving on this tool was 61%.

## Limits

- Evidence from `expand` exists only if the agent calls it. Sampling is the only route to a field the
  agent never asks for, and it costs tokens.
- An agent has another way to a removed field: calling another tool that returns it and is not trimmed.
  It was seen in the measures. It leaves no `expand` evidence, though ordinary learning still sees the
  value written; on an API billed per call it has a price.
- Reuse is detected textual reuse, with the blind spots of usage detection.
- One run at a time per proxy or `AutoTrim`.
- An `expand` on a result of an earlier run is recorded as requested and recovered; reuse is not
  looked for.
