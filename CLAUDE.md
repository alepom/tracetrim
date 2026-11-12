# tracetrim

Library + preprint: learn from agent traces which fields of tool outputs the
agent actually uses, and trim the rest. Born from the measurements of the
course project `~/progetti/accademia-server-mcp` (scripts `misura-riuso.py`,
`replay-proiezione.py`): the library reproduces those numbers exactly on the
11 PED exports, and that equivalence is the first regression check.

## Rules

- **Every number in the paper is recomputable**: a script produces it from a
  results file. The paper's tables and numeric text are generated, never typed.
- **Tokens are counted, not estimated**: tiktoken `o200k_base`.
- **Check the input before reporting a result.** A parser that fails silently
  produces plausible wrong numbers; `measure` always prints what it read.
- **Freeze the protocol before running new experiments** (`protocol.md`), as in
  `~/progetti/lab-test-AP/classificatori`, which is the template for paper,
  Zenodo and GitHub materials.
- **Nothing is published by Claude.** GitHub, Zenodo records and releases are
  created and published by the author.
- **What the tests cost is never published** (author's decision, 2026-11-12):
  no amount spent, no spend estimate, no spend cap in the paper, the docs, the
  protocols, the results files or any other versioned file. Costs appear only
  as changes relative to a reference arm (at the providers' list prices, which
  are public). Token counts can appear.
- Spend caps and warnings are local: `experiments/caps.local.json` (git-ignored,
  read by `experiments/caps.py`); without it every paid run is refused. Recompute
  plans with `python -m experiments.budget`, never by hand, and keep the figures
  out of versioned files. The spend ledgers stay in `out/`.

## Data constraints

- **Inputs name only the author's own domains** (alessiopomaro.it): the site of
  the editorial plan, the pages to parse, Labs `target` parameters. Enforced
  in the tool wrapper as a gate, not asked in the prompt. SERP results
  naturally contain third-party domains: measured, never published in clear.
- **Search goes through DataForSEO, not Tavily.** Tavily's Platform Terms
  §3.2(x) forbid disclosing "performance information or analysis relating to
  the Services". DataForSEO's terms allow aggregates and field names; raw
  responses are not licensed for redistribution, so for those cases only derived
  per-call records are published (field paths, no values: deviation 12 in `protocol.md`).
- `traces/` and `out/` are git-ignored: real traces contain third-party API
  payloads and stay local until the providers' terms are checked.
- The course exports contain a client domain: usable for local measurement,
  never published.

## Languages

Code, README, paper: English. The popular article for the author's audience:
Italian (`publication/articolo_divulgativo_IT.md`).

## Environment

`.venv/bin/python`; install with `TMPDIR=$PWD/.tmp-pip` (`/tmp` is a 2 GB tmpfs).
