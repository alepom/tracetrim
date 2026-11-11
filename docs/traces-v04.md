# The traces of the v0.4 measures

The runs of `protocol-v04.md` (E1 and E2) are also kept as a data set: the starting point for evaluating
**offline**, with no new spend, functions that are not written yet (projections by mode of use, learning from
errors, references and operations on stored results). Offline they can answer questions about tokens and about
fields lost; questions about the quality of an agent's answer need live runs.

## Where they are

| What | Where | In git |
|---|---|---|
| One run: trace, meta file, tool schemas | `traces/<case>/<run>.jsonl`, `.meta.json`, `.tools.json` | no |
| E2: the work directory of a sequence (tracetrim's own traces, evidence, reports) | `out/v04/e2/<case>/<arm>-s<n>/` | no |
| Spend ledger | `out/ledger.jsonl` | no |
| Tables, with counts, shares and field paths only | `results/v04/` | yes |

`traces/` and `out/` are git-ignored: the runs on C1 and C2 hold third-party API responses, which are not
redistributed. The runs on C4 are simulated and can be published.

## What a trace holds

One JSON object per line, in order: the two prompts (system, with any skill text the arm adds, and user), then
calls and messages. Every `call` has:

| Key | Meaning |
|---|---|
| `tool`, `arguments` | the call as the model wrote it |
| `output` | what the tool returned: the **original** |
| `sent` | what the model received, when tracetrim rewrote it (absent when it is the same text) |
| `exposure` | `full`, `filtered`, `sampled`, `full_cheaper`; absent for `expand` |
| `turn` | the model turn that emitted the call |
| `outcome` | `ok`, `tool_error` (the server marked the result as an error), `gate_refused` (the study's domain gate), `client_error` (no answer after the retries), `expand_error` |

A provider that reports an error as ordinary text, as DataForSEO does (`Error: API Error: ...`), is recorded as
`ok`: the server did not mark the result as an error. Such calls are told by the text of `output`.

`experiments.v04.calls(path)` reads a trace back as one record per call with all of these, `expand` included:
a test checks that nothing is missing. An `expand` call names in its arguments the `call_id` printed in the
`[trimmed]` line of the result it asks about.

Next to each trace, `<run>.tools.json` lists the tools the model was offered in that run, `expand` included:
name, description, argument schema, and a SHA-256 of the three. The meta file has the hashes, so that runs
offered the same tool can be recognised. For DataForSEO these are the server's own descriptions, not
responses; the server's address is in none of these files.

The meta file also has the arm, the skill text added and its tokens, billed usage per turn, tokens by category,
exposure and outcome counts, the DataForSEO calls per tool and, for E2, what happened to the fields the variant needs.

None of this changes a measure: it is recorded beside what the protocol measures.
