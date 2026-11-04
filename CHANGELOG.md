# Changelog

All versions were developed on 2026-11-04, together with the study described in the
[paper](https://doi.org/10.5281/zenodo.23953934). The studies ran on 0.1 (Study 1) and 0.2 (Study 2);
later versions add ways to use the library, and were checked end to end, not evaluated under a protocol.

## 0.3.4

Fixes from an independent pre-publication review.

- **Proxy security**: DNS-rebinding protection always on (local addresses on the proxy's port, plus
  `--allowed-host`), also for `/report`; tokens compared in constant time; `--token` can come from
  `$TRACETRIM_TOKEN`; `expand` call ids are random, and the store of originals is bounded.
- **Proxy correctness**: upstream errors and non-text content (images, resources) pass through
  unchanged; an upstream tool named `expand` or `tracetrim_done` is prefixed with its server name.
- **Usage detection** also matches numbers written with comma separators (`1,600`); at run time,
  lists and objects that were already empty are kept, so "no results" does not look like "hidden".
  Both are off in the scripts that reproduce the studies.
- The report's suggestion to cache repeated calls is now a manual action: the config never cached.
- OAuth token files are created readable by the owner only; `tracetrim learn --v01`; Python 3.10
  compatibility of `tracetrim login`; `AutoTrim.expand` tolerates malformed arguments.
- Paper: the Phase C gate is described as it worked, with a post-hoc re-check (deviation 13);
  the lossless part of the savings (re-serialisation alone) is reported; H3 fails in one case;
  misses are reported as calls; the Study 2 band uses the frozen thermometers only; related work on
  Headroom and mcp-compressor corrected; prices verified.

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
- Custom headers and `${VAR}` expansion in `mcp.json`.

## 0.3.1

- **One proxy in front of several MCP servers** (`--servers mcp.json`, the `mcpServers` block clients
  already use): one `expand`, one `tracetrim_done`, one report.
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
