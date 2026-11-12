# Rows: a design note

tracetrim removes **fields**. It never removes **rows**: a tool that returns ten results gives the agent
ten results, each with fewer fields. This note is about what a cut of rows would take, written before any
of it is built. Nothing described here exists in the code.

Numbers: `results/v04/rows.json`, recomputed from the recorded runs by `python -m experiments.rows`.
Since 0.4.

## What the agents asked for

In the 100 runs of E1 and E2 (`docs/measures-v04.md`) the agents called `expand` 38 times. Ten requests gave
nothing back:

| Reply | Requests | What was asked |
|---|---:|---|
| unknown `call_id` | 8 | 7 asked for rows: `items` on its own, or `items[10:]`; one for a group of fields |
| no field matches | 2 | a field of one element, `data[0].…`, on a valid id |

- **The seven requests for rows used an id that did not exist**: made up, or cut short (`decaf?`,
  `f4fb?`). `expand` works on results that carry a `[trimmed]` line; these requests were not about one of
  them. The agent wanted **more rows than it had been given**, the eleventh onwards, and reached for the
  only tool that seemed to give back something withheld.
- That is a request tracetrim cannot serve: it keeps the original of a result, and the original has ten
  rows because the call asked for ten. More rows are another call to the tool.
- **The two requests with an index are now answered**: `data[0].x` is read as `data[].x` for that element.
  And an unknown id now gets a reply that names the valid ones and says that `expand` cannot give more
  rows (`docs/adaptive.md`).

So the agents did not ask to see fewer rows, and nothing in these runs asks for a cut. The question is the
other one: would a cut of rows be safe, if one wanted the saving?

## How many rows are reused

For every list a tool returns, in the 112 runs in which the model saw whole results (the eight runs per
case of the original study, E1 without trimming, E3): how many elements came back, and which positions hold
a value the run wrote afterwards. Reuse is detected as for fields, with the same blind spots.

| | Search-data API (C1, C2) | Other APIs (C3, C4, C5) |
|---|---:|---:|
| Lists with a reused element | 12 | 11 |
| Calls with a reused element | 592 | 641 |
| … where the reused elements are exactly the first ones | 150 (25%) | 626 (98%) |
| … where the last element received is reused | 325 (55%) | 518 (81%) |
| … that lose a reused element if the list is cut to 5 | 542 (92%) | 12 (2%) |
| … that lose a reused element if the list is cut to 3 | 553 (93%) | 182 (28%) |

On the other APIs the lists are short (one to five elements, ten for two tools) and the reused elements
are the first ones. On the search-data API they are ten or twenty elements long and the reuse is spread:
58% to 89% of the elements on the four keyword tools called most, 65% and 72% of a results page.

## Why a cut by position does not hold on the search-data API

- **The order of the rows is not the order of their use.** The prompts ask for ten rows sorted by search
  volume or by relevance; the agent then picks by intent, by topic, by what is missing from the site. The
  reused rows are the first ones in one call out of four.
- **The size is already the prompt's.** `limit` 10 and `depth` 10 are prescribed, and respected in every
  run. A list of ten of which seven are reused has little left to cut.
- **Every cut point loses something.** Keeping the first five would have removed a reused row in 92% of the
  calls; in more than half the calls the last row itself is reused.
- **The rows the agent skips are not noise to it.** A keyword it does not write may be the one it compared
  the others with. That is the blind spot of the whole method, and on rows it has no safeguard such as the
  identifying fields kept for a record.
- **A removed row cannot be told from a row that does not exist.** A hidden field leaves its record there,
  with a line that says what is missing. A hidden row changes what the agent believes the tool found: "three
  competitors" instead of ten.

## Three designs, each with its risk

**1. Tell, don't cut.** Keep every row and report, per tool and list, how many are reused and from where
(the report does this already: *List elements reused*). The saving comes from the person who lowers `limit`
in the prompt or in the tool's default.
- *Risk:* none at run time. It saves nothing unless someone acts, and the measure is a lower bound: a row
  read and not written counts as unused.

**2. Rows in two steps.** Past a number of elements, send the first K whole and the rest as a compact index:
one line per row with its identifying fields only (the keyword, the title, the URL), and an `expand` that
takes positions (`items[7]`) to get a row back whole. The paths with an index that `expand` now accepts are
the piece of this that exists.
- *Risk:* the agent chooses rows by fields that the index does not carry (search volume, rank, date), so
  it either asks for most rows back, one round trip each, or decides on less than it had. K and the fields
  of the index would have to be learned per tool, and on the lists measured here most rows are reused: the
  index would often cost more than it saves. It also presents the cut as a property of the result, which is
  where the confusion of the seven requests above came from.

**3. Pass the size on.** Learn the number of rows a tool's results are used to, and lower the size argument
of the call itself (`limit`, `depth`) before it reaches the tool. The saving is real, and on an API billed
per row it is also money.
- *Risk:* this changes the call, not its result, and tracetrim does not do that anywhere: today the tool
  always runs as the agent asked. A row not requested cannot be recovered with `expand`, only with another
  call. The name of a size argument is a guess (`depth` is a number of results on one API and a crawl depth
  on another), and a prescribed value would be overridden silently, the very thing the measures checked the
  skill's strategy did not do.

**Where this leaves it.** The first design is in place. The second is worth a trial only on a tool with long
lists of which few rows are reused: among those measured, the video results (20 received, 16% reused in
the calls with any reuse) and the two keyword tools that return 20, where a row was reused in 2 calls of 22
and 2 of 20. The third stays out: it would make tracetrim change what the
agent asked for.
