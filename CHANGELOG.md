# Changelog

Versions up to 0.3.5 were developed on 2026-11-04, together with the study described in the
[paper](https://doi.org/10.5281/zenodo.23953934). The studies ran on 0.1 (Study 1) and 0.2 (Study 2);
later versions add ways to use the library, and were checked end to end, not evaluated under a protocol.

## Unreleased

Easier to put tracetrim in front of the servers you already use. **Nothing changes in what the model
receives**: the engine, the proxy, learning and the skill are untouched.

- **`tracetrim init`.** Reads the MCP configuration of a host (`--host claude-desktop`, `claude-code`,
  `cursor`, `vscode`, or any file with `--file`) and puts tracetrim in front of its servers. Without
  `--write` it is a dry run: it shows which servers go behind tracetrim, which stay in the host and why,
  the host's new entry and the servers file. With `--write` it makes a backup next to the host's file,
  moves the servers to `WORKDIR/servers.json` (mode `0600`) and leaves one `tracetrim` entry in their
  place, with the absolute path of the executable. `tracetrim init --undo BACKUP` restores the file byte
  for byte, and refuses when it changed since. Running `init` again wraps nothing twice and duplicates
  no server. `--http PORT` prints the steps for a host that reaches tracetrim over HTTP, without
  rewriting it.
- **Keys are never shown.** No value of the host's file is printed, in a dry run or in an error: not of
  `env` or `headers`, not an argument, not the path or the query of a URL.
- **References stay references.** `${VAR}` and `${VAR:-default}` (Claude Code) and `${env:NAME}` (Cursor,
  VS Code) are still resolved by the host, which passes them to tracetrim; `servers.json` uses
  `${NAME}`. A server tracetrim cannot start exactly as the host did (a field it does not read, SSE,
  `${input:…}`, `${workspaceFolder}`, …) stays in the host, untouched.
- **One work directory per project**: `~/.tracetrim/HOST` for the user's file,
  `~/.tracetrim/HOST/FOLDER-HASH` for a project's. `init` refuses to write `servers.json` inside a git
  repository that does not ignore it (`--force` to do it anyway). Claude Code's `~/.claude.json` is never
  opened: for the user scope `init` prints the `claude mcp` commands.
- **[docs/hosts.md](https://github.com/alepom/tracetrim/blob/master/docs/hosts.md)**: the format of each
  host with its source, and a table of what was verified host by host (connection, tools,
  `tracetrim_done`, the skill, `expand`), with how to verify a host yourself using `--log-client`. The
  README's short way starts from `tracetrim init`.

## 0.4.1

Clearer reports and diagnostics. **Nothing changes in what the model receives**: the same calls give the
same text and the same traces as 0.4.0, which a test checks with the new counts on and off.

- **Where the saving comes from.** Every call is counted three times with the same tokenizer
  (`o200k_base`): the output as the tool sent it, the same output as tracetrim writes it with nothing
  removed, and what the model receives, the `[trimmed]` line included. The saving is the sum of two
  parts, exactly: *re-serialisation* (compact JSON, nothing removed) and *fields* (what the projection
  removes on top). The proxy's log says both for the run and for the session, as in
  `(-39%: -27% format, -12% fields)`; the report has a section per run and per tool, `report.json` a new
  key `savings`, and the work directory a new file `savings.json`. While learning, the saving from
  fields is zero and the one from re-serialisation is not, and it shows. What `expand` gives back stays
  apart. `Observation.sizes` carries the three counts; `--no-breakdown`, `AutoTrim(breakdown=False)` and
  `Engine(breakdown=False)` turn the count off. `python -m experiments.breakdown_replay TRACES` plays
  recorded runs again and prints the split per run.
- **Runs followed by calls after an answer.** The report now explains its count: it adds up two cases
  tracetrim cannot tell apart, an agent that closed too early and the next task starting within the idle
  pause, whose length it states. On a host where the user opens the next conversation right away it grows
  at every run, and by itself is not a sign of a problem. The counting and the key
  `answered_runs_followed_by_calls` are unchanged; `auto` has a new key `idle_seconds`.
- **Diagnostics of the client: `--log-client FILE`** (off by default). One JSON line per request of the
  client: time, an anonymous id of the session (a salted hash), the method; for `initialize` what the
  client declares (`clientInfo`, `protocolVersion`, `capabilities` with its extensions), for a resource or
  a skill its uri, for a tool call the name of the tool. Never arguments, results, headers or tokens. It
  tells whether a host asked for the tracetrim skill
  ([docs/skill.md](https://github.com/alepom/tracetrim/blob/master/docs/skill.md)). Over stdio and HTTP.
- README and docs no longer call the functions of 0.4 "unreleased".
- `docs/skill.md` records the first host checked with `--log-client`: the OpenAI Playground does not ask
  for the skill, and calls `tracetrim_done` from the tool's description.

## 0.4.0

Developed from 2026-11-11 to 2026-11-12, after the studies of the paper: checked by tests and by the
measures in `docs/measures-v04.md`, which are not part of the paper. Nothing here changes the numbers
of the paper. The version archived on Zenodo, and cited in `CITATION.cff`, is still 0.3.4.

- **The tracetrim skill** ([docs/skill.md](https://github.com/alepom/tracetrim/blob/master/docs/skill.md)).
  The instructions for `tracetrim_done` and `expand` are one versioned text, on two levels: *base*,
  always on, and *strategy* (how to organise tool calls), off unless asked for. The proxy publishes it
  through the MCP Skills extension (`io.modelcontextprotocol/skills`): `skills/list`, `skills/get`, and
  the `SKILL.md` as a resource, with digest and size. `--skill-strategy` adds the strategy, `--no-skill`
  publishes nothing. For function calling, `AutoTrim(strategy=...)` and `AutoTrim.instructions`; for
  hosts that do not load skills, `tracetrim skill` prints the text.
- **One engine.** The MCP proxy and `AutoTrim` are now adapters over `tracetrim.engine`, which speaks a
  small contract with no SDK behind it (`tracetrim.contract`: call, result, observation, evidence). The
  contract keeps apart what tracetrim receives from a tool and what it shows the model.
- **Closing a run is robust.** A repeated or retried `tracetrim_done` changes nothing and gets the same
  reply; closings are serialised. The report counts the two cases in which the boundary of a run may be
  wrong: closings refused for lack of an answer, and runs closed by an answer and followed by calls
  within the idle pause.
- **Adaptive learning** ([docs/adaptive.md](https://github.com/alepom/tracetrim/blob/master/docs/adaptive.md)),
  in automatic mode, for the proxy and `AutoTrim` alike:
  - *Evidence from `expand`*, always recorded: per field, the runs in which it was requested by name,
    recovered, reused after recovery, seen in an unfiltered result, used there. A request for
    everything is not a request for each field. `evidence.json`, `evidence/run-*.jsonl`.
  - *Promotion* (`--promote`, `Adaptive(promote=True)`; off by default): a field with enough evidence is
    kept from then on, before the whole tool is switched off. Reasons and runs are kept;
    `tracetrim promotions WORKDIR [--revert | --restore]` is the way back.
  - *Sampling without filter* (`--sample-rate`; off by default): now and then a result that would be
    trimmed is passed whole. The tool runs once; the sequence depends on a seed; a cost limit ties the
    tokens added to those removed.
  - The report has a section on promotions, their effect on tokens, evidence and sampling.
- **Measures that change nothing**, in every report: list elements reused, with the arguments that fix
  the size of a result (rows are never cut), and the number of distinct values of each argument.
- **Persistence.** Files in the work directory are written aside and renamed; a lock protects the state
  shared by tool calls and the learning thread; a closing whose write fails can be tried again.
- **Protocol of the new measures**: `protocol-v04.md`, frozen on 2026-11-11 before any paid run, apart from
  `protocol.md`, which is unchanged. Its estimate comes from `python -m experiments.budget --v04`, its
  preconditions from `python -m experiments.v04_preconditions`. No paid run has been made.
- **Harness of the new measures**: `python -m experiments.run --v04 schedule | next | dry-run DIR`, with the
  stop rules of the protocol and a dry run that spends nothing. The runs are kept as a data set for later
  offline evaluations ([docs/traces-v04.md](https://github.com/alepom/tracetrim/blob/master/docs/traces-v04.md)).
- **First measures of the new functions** ([docs/measures-v04.md](https://github.com/alepom/tracetrim/blob/master/docs/measures-v04.md)):
  100 runs of `gpt-5.6-terra` under `protocol-v04.md`, frozen before the first paid run. Small experiments on
  one model, reported with the hypotheses stated beforehand.
  - *The skill's strategy* (E1, 50 runs): it did not reduce turns, tool calls or cost, and did not override the
    parameter values the prompts prescribe. That model already issues calls together in about three turns out
    of four; the effect on models that do not was not measured. The strategy stays off by default.
  - *Adaptive learning* (E2, 50 runs): a field the agent recovers with `expand` and writes is kept from the
    next run, in 16 cases of 16. Promotion and sampling made no measurable difference: the situations they are
    for did not occur. Both stay off by default.
  - *Trimming*, in the same runs: tool-output tokens down 44% and the cost of a run down 30% and 24% on the two
    cases with trimmed arms, with the gates passed in 5 runs of 5.
- **The strategy on a second model** (E3, `protocol-v04-e3.md`, frozen on 2026-11-12): 38 runs of
  `gemini-3.8-flash`, with simulated tools and with outputs already recorded in place of the search-data
  provider. The two hypotheses on turns failed as stated. On the agent where that model issues one call per
  turn, the median went from 17 turns to 6 with the strategy, at the same completeness; where it already
  groups its calls, little changed. The strategy stays off by default.
  `python -m experiments.run --e3 ...`, `python -m experiments.analyze_v04 --e3`.
- **`expand` takes the position of an element**: `data[0].url` is `data[].url` for the first element only;
  the reply holds the elements asked for, in their order. As evidence an index counts as `[]`.
- **A hint on the skill's strategy, in the report.** When the runs record their turns, the report measures
  the turns with several calls and estimates, with a declared heuristic, the calls that could have been made
  in an earlier turn. If the agent makes nearly always one call per turn and many calls did not have to
  wait, it suggests trying the strategy: a MANUAL suggestion, with documented thresholds
  (`tracetrim report --strategy-max-several / --strategy-min-earlier / --strategy-min-runs`). Nothing is
  switched on.
- **Switch-off on evidence from more runs** (`--switch-off-runs N`, `Adaptive(switch_off_runs=N)`; default 2):
  a tool stops being trimmed only when its expanded results come from N distinct runs. `--switch-off-runs 1`
  restores the rule up to 0.3.5.
- **Offline analyses of the recorded runs**, none of them in a protocol
  ([docs/measures-v04.md](https://github.com/alepom/tracetrim/blob/master/docs/measures-v04.md)): projections
  per mode of use show no benefit on 18 candidate arguments and are not implemented; a design note on rows
  ([docs/rows.md](https://github.com/alepom/tracetrim/blob/master/docs/rows.md)), which tracetrim still never cuts.
- **Offline demo**: `python -m experiments.adaptive_demo` (scripted agent, simulated tool, both adapters).
- **Compact JSON from the first call.** In automatic mode every JSON output reaches the model without
  indentation and without the spaces after `,` and `:`, from the first call of the first run: lossless,
  so it does not wait for learning. Generated configs carry `"compact": true`; `--no-compact`,
  `AutoTrim(compact=False)` and `Engine(compact=False)` turn it off. Found in a test in the OpenAI
  Playground with DataForSEO's MCP server (six runs, three to learn): of the tokens removed after
  learning, about half came from re-serialisation and half from hidden fields; compact separators would
  have taken off about 24–27% in the three learning runs and about ten more points in the next ones
  (an estimate from the recorded outputs, not a measurement). The experiments of `protocol-v04.md` and the
  offline demo keep the serialisation they were run with.
- **Changed behaviour:**
  - In automatic mode JSON outputs are compacted from the first call: until 0.3.5 the learning runs
    received them exactly as the tool sent them, and later runs as JSON with spaces after separators.
    Text that is not JSON is untouched; traces still record what the tool sent. A config written before
    0.4 (no `compact` key) keeps the spaces when applied with `--config`.
  - The reply of `expand` to an unknown `call_id` now says that only a result with a `[trimmed]` line can be
    expanded and, with one agent (stdio, function calling), names the most recent valid ids with their
    tools. The HTTP proxy, which several clients can reach, never names them. Until 0.3.5 it was
    `Error: unknown call_id.`
  - A tool is switched off only when its expanded results come from at least two distinct runs. Until
    0.3.5 one run was enough, the first one included; `--switch-off-runs 1` restores that rule.
  - *The simple way for small outputs*, always on: when a filtered result, with the line that lists
    what was removed, would count at least as many tokens as the whole result re-serialised, the whole
    result is passed without the line. Small or compact results that 0.3.5 trimmed and annotated now
    arrive whole. Counted in the report as its own kind, apart from filtered and sampled results.
  - The switch-off of a tool counts filtered results that were expanded, each once and only when a
    removed field came back. Until 0.3.5 it counted every `expand` request, repeats and failed ones
    included.
  - `tracetrim_done` without an answer is now refused, and the run stays open. Until 0.3.5 it closed
    the run as one without answer.
  - `AutoTrim(structural=...)` re-encodes outputs from the first call, as the proxy always did. Until
    0.3.5 it waited for the first learned config.
  - In automatic mode, and with `--config`, the proxy now declares the `resources` capability (one
    resource: the skill), announces the Skills extension (in `server/discover`, and in `initialize` to
    clients that declare it) and sets the server's `instructions` to one sentence that points to the
    skill. `--no-skill` restores the previous capabilities and instructions.
  - `serverInfo.version` is tracetrim's version; it was empty.
  - The description of `tracetrim_done`'s `answer` says "the answer only, not the conversation".
- **Fixed, from a code review of 2026-11-12** (each defect reproduced by running code, each now a test):
  - `expand` left out the list elements that lack a requested field, so a model reading the reply in order
    could take one record's value for another's (in 0.3.5 too). An addressed element now keeps its place,
    empty when it has nothing; `"*"` and indexed requests alike.
  - With a projection learned with `--unseen drop`, fields never seen while learning were removed with no
    `[trimmed]` line and could not be asked back (in 0.3.5 too). They are now listed and expandable.
  - A top-level list trimmed to nothing became `{}` (in 0.3.5 too); it stays a list, `[]`.
  - A result passed whole on purpose (sampled, or cheaper whole) went out with a trimmed `structuredContent`.
  - The switch-off counted a run whose `expand` gave nothing back, and an expansion of a result filtered
    before the tool's counters started again; a field the operator reverted was taken as settled by
    ordinary learning, and wiped the tool's counters.
  - `tracetrim promotions --revert/--restore` relearned with the default settings instead of those of the
    work directory, and could switch off a tool the proxy would keep.
  - `expand` called with one field name as a string read it as a list of characters.
  - Two threads writing the same file of the work directory could collide on the temporary file.
  - A tool that returned an empty string stopped the report with a division by zero (in 0.3.5 too).
  - `AutoTrim.structural` can be set again, as up to 0.3.5.
  - Automatic mode without a tokenizer (`tracetrim[measure]`) stopped at its first relearning, in the middle
    of the agent's work (in 0.3.5 too); it now says so at start-up.
  - `tracetrim proxy` accepted adaptive options it then ignored (`--promote-runs` without `--promote`,
    `--sample-max-share` or `--sample-seed` without `--sample-rate`, any of them without `--auto`) and
    answered an out-of-range value with a traceback; it now refuses them with a message.
- **Fixed:** a tool could be switched off one run after its field had come back. When the agent asks a
  field back with `expand` and writes its value, ordinary learning keeps the field from the next run; the
  requests that asked for it still counted against the tool, and a tool called twice a run reached the
  switch-off threshold with them. The count now starts again when a recovered field is kept, as it does
  after a promotion; a tool that goes on asking for other fields is still switched off. Found in the dry
  run of `protocol-v04.md`, before any paid run.
- **Fixed:** `expand` requests made after the last closing were not saved when the proxy stopped; a
  trace written just before a crash could be overwritten by the next run.
- **No spend in versioned files.** The end-to-end checks (`results/checks/auto_check_*.json`) no longer
  carry the cost of their runs, and `experiments.auto_check`, which calls the model, runs only with
  `--paid`.
- Files written by earlier versions (work directories, configs, projections, traces, state) load as
  before: `tests/test_regression_035.py`, written on the untouched 0.3.5 code.

## 0.3.5

Published on PyPI. No change in behaviour from 0.3.4, the version archived on Zenodo.

- Install with `pip install "tracetrim[measure,proxy]"`; installing from GitHub stays as an alternative.
- Package metadata: keywords, classifiers, the software DOI and an SPDX license expression; the
  README's links are absolute, so they work on the PyPI page.
- Wording: descriptions, CLI help and the report's footer say "visibly reuses", as the paper does
  (detected textual reuse, not use).

## 0.3.4

Fixes from three internal reviews and two external review rounds before publication.

- **Proxy security**: DNS-rebinding protection always on (local addresses on the proxy's port, plus
  `--allowed-host`), also for `/report`; tokens compared in constant time; `--token` can come from
  `$TRACETRIM_TOKEN`; `expand` call ids are random, and the store of originals is bounded.
- **Proxy correctness**: upstream errors and non-text content (images, resources) pass through
  unchanged; an upstream tool named `expand` or `tracetrim_done` is prefixed with its server name.
- **Usage detection** also matches numbers written with comma separators (`1,600`); at run time,
  lists and objects that were already empty are kept, so "no results" does not look like "hidden".
  Both are off in the scripts that reproduce the studies.
- The report's suggestion to cache repeated calls is now a manual action: the config never cached.
- The proxy accepts local browser origins on any port (MCP Inspector), never prints the token, warns when
  it is reachable from outside without one, and passes `structuredContent` through with errors.
- The simulated servers download their pinned OpenAPI descriptions on first use and check the hash, so
  a fresh clone runs the demo and the tests; analyses run on the released dataset keep the cases whose
  traces are not released; `experiments/misses_from_derived.py` recomputes the misses of the two
  search-data cases from the released records.
- OAuth token files are created readable by the owner only; `tracetrim learn --v01`; Python 3.10
  compatibility of `tracetrim login`; `AutoTrim.expand` tolerates malformed arguments.
- External review, round 2: the billing audit requires exact amounts, year and outcome (same counts); the full
  minifier is the baseline in Figure 5, with the 0.2 projection measured against minified JSON; H2 on the
  computable part of the protocol's list; abstract and conclusion speak of visible reuse and repaired failures, as an
  exploratory study; the proxy returns untouched results as sent (`_meta` included) and keeps `_meta` when it trims;
  the proxy's blindness to turns is documented.
- External review: usage compared by model turn when traces record it (`"turn"`, also in `AutoTrim.output`),
  with no effect on the study's figures (deviation 14); thermometers and gates measured against the protocol's
  wording, with a stricter audit of the billing diagnoses and of what the gates skip (deviation 15); a full-minifier
  baseline; the proxy keeps `structuredContent` (trimmed with the text when the text is trimmed), records errors
  and non-text results for telemetry, refuses tool-name clashes, and `expand` accepts `"*"` and says what is hidden
  when nothing matches; analyses import without the agent SDKs (`experiments/paths.py`); `requirements-study.txt`.
- Experiments: the gates did not parse projected outputs (they end with the `[trimmed]` line); the line
  is now cut and every Phase C run re-checked, with no gate result changed; a post-hoc re-check finds one
  unsupported volume among 180 reported keyword–volume pairs (deviation 13).
- Paper: real-API and simulated cases are reported separately in every range; the editorial-plan
  measure outside the band is reported without reading a direction into it;
  the lossless part of the savings (re-serialisation alone) is reported; H3 fails in one case;
  misses are reported as calls, and real-API and simulated cases are no longer pooled; the Study 2 band
  uses the frozen thermometers only, and the shift in the editorial plan's keyword choice is reported;
  related work on Headroom and mcp-compressor corrected; prices verified.

## 0.3.3

- **Function calling without MCP**: `tracetrim.auto.AutoTrim` goes into the agent's own loop
  (`output`, `expand`, `done`), with the same rules as the proxy's automatic mode.
- 16 MB SSE events (the SDK default of 1 MB is too small for large search results).
- Demo: one ngrok tunnel with path routing (the free plan has one domain).
- `min_runs` is at least 2: trimming is decided on held-out runs.
- Paper: figures on where tracetrim acts, how usage is detected, where the tokens go per tool, the live
  runs and the comparison with Headroom, all generated from `results/` and `traces/`
  (`experiments/paper_figures.py`). The Headroom version used for the measurements (0.39.1) is now
  read from the results.

## 0.3.2

- **OAuth** towards upstream MCP servers (discovery, dynamic registration, PKCE, refresh), with
  `tracetrim login` / `logout`; tokens in `~/.config/tracetrim/oauth/`.

## 0.3.1

- **One proxy in front of several MCP servers** (`--servers mcp.json`, the `mcpServers` block clients
  already use, with custom headers and `${VAR}` expansion): one `expand`, one `tracetrim_done`, one report.
- An unfinished run is kept on shutdown, over HTTP too.

## 0.3.0

- **Automatic mode** (`--auto WORKDIR`): the proxy observes, learns and trims on its own. The agent
  passes its final answer to `tracetrim_done`; the report is kept up to date from the first run (live at
  `/report` over HTTP); after `--min-runs` runs only the report's automatic actions are applied; a tool
  expanded too often stops being trimmed.

## 0.2.0

- **Safeguards on by default** (Study 2): S1 keeps identifying fields, S2 recognises transformed values
  (dates written from Unix times, amounts from minor units, URL last segments). `--v01` reproduces 0.1.
- MCP proxy (stdio and streamable HTTP, bearer token, allowed hosts), `expand`, live per-call log,
  optional structural compression (TOON-style, or Headroom as an experimental backend).
- Bilingual telemetry report with automatic and manual actions, and the runtime config it generates.

## 0.1.0

- Learned projections from execution traces (prudent and aggressive), leave-one-out measurement,
  readers for JSONL traces and OpenAI Playground exports. Used for Study 1.
