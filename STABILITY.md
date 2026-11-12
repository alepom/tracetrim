# Stability

What tracetrim promises not to break, from 1.0 on, and how a change is announced when it has to come.
Versions follow [semantic versioning](https://semver.org/): within 1.x nothing listed here as stable
breaks; a break waits for 2.0. The promises are held by tests: `tests/test_stability.py` (this page)
and `tests/test_regression_035.py` (files written by 0.3.5).

## What the model receives

tracetrim sits between an agent and its tools, so its most important interface is what the model sees.

**Stable**
- The tools tracetrim adds: `expand` (arguments `call_id`, `fields`) and `tracetrim_done` (argument
  `answer`), with their names and required arguments. The reply to `tracetrim_done` is `Recorded.`
- The line after a trimmed result: it starts with `[trimmed] call_id=` and lists what was removed after
  ` hidden: `.
- The skill's address, `skill://tracetrim/SKILL.md`. Its text may be improved within 1.x; every change
  to the text gets a new skill version (`tracetrim.skill.VERSION`), so a host never sees two texts under
  one version.

**Changes in behaviour.** What the model receives with the default options can change in a minor
release only when a measure or a test shows it is better. Each such change:
- is listed in the CHANGELOG under *Changed behaviour*;
- comes with an option that restores the previous behaviour for the rest of 1.x (as `--no-compact`
  and `--switch-off-runs 1` do for the changes of 0.4).

New functions that change what the model receives arrive **off by default**.

## Command line

**Stable**: the commands `learn`, `apply`, `measure`, `report`, `proxy`, `promotions`, `skill` and
`init`, with the options their `--help` lists, and their meaning. New options may be added.

**Not stable**: the log lines on standard error, the layout of `report.html`, and the wording of the
report's suggestions. They are written for people, and they improve. The lines of `--log-client` are
diagnostics: fields may be added to them, but one promise holds, since it is about privacy: they never
hold arguments, results, headers, tokens or the values of what a client declares.

## Python

**Stable**
- The names exported by `tracetrim`: `Projection`, `learn`, `project`, `Trace`, `Event`, `load`,
  `analyse`.
- `tracetrim.auto.AutoTrim`, the adapter for function calling:
  - the first two arguments, `workdir` and `min_runs`;
  - the keyword arguments `structural`, `strategy`, `adaptive`, `compact`, `breakdown`;
  - `output`, `expand`, `done`, `offered`, `instructions`, `report`, `expand_tool`, `trimming`.

  Pass every argument after `min_runs` by keyword.
- `tracetrim.evidence.Adaptive` and its fields.
- `tracetrim.runtime.Policy`: `Policy.load`, `apply`.
- `tracetrim.contract`: `ToolCall`, `ToolResult`, `Observation`, `Sizes`, `Evidence` and the exposure
  constants.

**Provisional**: `tracetrim.engine.Engine`, the core the adapters share. It may change in a minor
release, with a note in the CHANGELOG.

**Internal**: every other module and name, and every name that starts with `_`.

## Files

Everything tracetrim learns lives in files that a later version has to read.

**The promise.** Every 1.x version reads the work directories, configs and traces written by 0.3.5
and later, and goes on learning from them.

| File | Format |
|---|---|
| `report.config.json` (also any config passed with `--config`) | `tracetrim/config@1` |
| `report.projection.json` | `tracetrim/projection@1` |
| `report.json` | `tracetrim/report@1` |
| `state.json` | `tracetrim/state@1` (since 0.7; earlier files have no `format` and load the same) |
| `evidence.json` | `tracetrim/evidence@1` |
| `savings.json` | `tracetrim/savings@1` |
| `tools.json` | `tracetrim/tools@1` |
| backup note written by `init` | `tracetrim/init-backup@1` |
| `traces/run-*.jsonl` | one JSON object per line: `{"kind": "call", "tool", "arguments", "output"}`, optionally with `"turn"`, then `{"kind": "message", "text"}` |
| `evidence/run-*.jsonl` | read back by tracetrim; its fields are not a public schema |

**Rules for a format**
- A format's version changes only when an older reader would misread the file. Adding a key is not
  such a change: readers ignore keys they do not know.
- A key of `report.json` keeps its meaning. Keys may be added, not removed or reused.
- `report.html` is a page for people, not a format.

## Deprecations

Something stable that has to go is first **deprecated**, in a minor release:
- it keeps working, with a warning on standard error or a `DeprecationWarning`;
- the CHANGELOG says what replaces it;
- it is removed no earlier than the next major release.

## Dependencies

Python 3.10 or later. The extras (`measure`, `proxy`, `headroom`) keep their names; their version
ranges may move in a minor release, and the CHANGELOG says so. `headroom` is experimental and outside
these promises.
