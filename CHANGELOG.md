# Changelog

All versions were developed on 2026-11-04, together with the study described in the
[paper](https://doi.org/10.5281/zenodo.23953934). The studies ran on 0.1 (Study 1) and 0.2 (Study 2);
later versions add ways to use the library, and were checked end to end, not evaluated under a protocol.

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
  editorial-plan volume of 180 written without a source (deviation 13).
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
