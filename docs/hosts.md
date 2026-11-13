# Hosts: putting tracetrim in front of the servers you already use

Two things are on this page: how `tracetrim init` rewrites the MCP configuration of a host, and
what is known, host by host, about how tracetrim behaves there. The second table holds **only what was
verified**, with the date and the way; everything else says *not verified*, and the last section
explains how to verify a host yourself.

## `tracetrim init`

```bash
tracetrim init --host cursor            # dry run: shows what would change, writes nothing
tracetrim init --host cursor --write    # does it
tracetrim init --undo ~/.cursor/mcp.json.tracetrim-backup-20261112-213045
```

Without `--write` nothing is written. The dry run shows which servers go behind tracetrim, which stay
in the host and why, the entry the host gets, and the servers file. With `--write`:

1. a backup of the host's file is made next to it, `FILE.tracetrim-backup-DATE-TIME`, with a small
   note beside it (`….json`: a fingerprint of what `init` wrote, no content);
2. the servers that move are written to `WORKDIR/servers.json`, readable only by you (mode `0600`);
3. in the host, one entry `tracetrim` takes their place:
   `/absolute/path/to/tracetrim proxy --servers WORKDIR/servers.json --auto WORKDIR`. The path is
   absolute because graphical hosts often do not have the terminal's `PATH`.

`--undo BACKUP` puts the host's file back byte for byte. It refuses, unless `--force`, when the file is
no longer the one `init` wrote: restoring would lose what you changed since. The work directory is left
as it is.

Running `init` again is safe: a host that already goes through tracetrim is not wrapped a second time,
servers you added since join the same `servers.json`, and a server that is already there is not
duplicated. A server with the same name and a different definition stops `init` before anything is
written. An entry that already runs `tracetrim proxy` counts as tracetrim in any form
(`--servers PATH`, `--servers=PATH`, `python -m tracetrim.cli`); one without `--servers`
(`--upstream-url`, `--upstream-cmd`) stays as it is and is not moved.

`init` also stops before writing anything when:

- the servers file holds more than an `mcpServers` object (a whole client's file): point the entry's
  `--servers` to a file of its own;
- there is no tracetrim entry in the host but `servers.json` already holds servers that are not
  moved now (left by an earlier `init`, then undone): they are listed, and `--force` keeps them;
- the host's file or `servers.json` changed between the moment `init` read them and the moment it
  would write: run it again;
- `--file` is a `.claude.json`, Claude Code's own state file (see below).

| Option | |
|---|---|
| `--host` | `claude-desktop`, `claude-code`, `cursor`, `vscode` |
| `--file PATH` | the host's file, when it is not in the documented place |
| `--scope user\|project` | for a host with both (Cursor). Default: the user's file for Claude Desktop and Cursor, the project's file in the current directory for Claude Code and VS Code |
| `--workdir DIR` | where `servers.json`, the traces and the report go |
| `--exclude NAME` | leave this server in the host (repeatable) |
| `--name NAME` | the name of the entry, if the host already has a server called `tracetrim` |
| `--http PORT` | (1-65535) for a host that reaches tracetrim over HTTP: the host's file is not rewritten, `servers.json` is written and the steps are printed |
| `--write` | do it |
| `--force` | write `servers.json` inside a git repository that does not ignore it, or keep servers already in it that are not moved now; with `--undo`, restore a file that changed, or one whose note is missing or unreadable |

### The work directory

tracetrim learns per work directory, so `init` uses one per project:

- the user's file: `~/.tracetrim/HOST`;
- a project's file: `~/.tracetrim/HOST/FOLDER-HASH`, where `HASH` is the first 8 characters of the
  SHA-256 of the project's absolute path. Two projects never share what was learned.

Both are outside any repository. `servers.json` holds your servers' keys: `init` refuses to write it
inside a git repository that does not ignore it (unless `--force`) — the file actually written, also
when an existing entry's `--servers` points outside the work directory — and warns when the directory is
inside a project whose `.mcp.json` is under version control.

### Keys are never shown

No value of the host's file is printed, in a dry run or in an error: not of `env` or `headers`, not an
argument (the official examples put keys there too), not the path or the query of a URL. What is shown
is names, the number of arguments, and the scheme and host of a URL; a hidden value is shown as `****`.
Of an existing tracetrim entry only what `init` writes is shown (its options, the servers file and
the work directory, references in `env`); any other field (`cwd`, `auth`, `envFile`, …) appears by its
name alone. A file that is not valid JSON is
reported by line and column, and is not rewritten (a file with comments has to be edited by hand).

### What each host's file looks like

Read in the official documentation on 12 November 2026.

| Host | File | Key | Variables the host resolves | Source |
|---|---|---|---|---|
| Claude Desktop | Linux `~/.config/Claude/claude_desktop_config.json`, macOS `~/Library/Application Support/Claude/claude_desktop_config.json`, Windows `%APPDATA%\Claude\claude_desktop_config.json` | `mcpServers` | none documented | [modelcontextprotocol.io](https://modelcontextprotocol.io/docs/develop/connect-local-servers) |
| Claude Code | `.mcp.json` at the project root | `mcpServers` | `${VAR}`, `${VAR:-default}` in `command`, `args`, `env`, `url`, `headers` | [code.claude.com](https://code.claude.com/docs/en/mcp) |
| Cursor | `~/.cursor/mcp.json`, `.cursor/mcp.json` | `mcpServers` | `${env:NAME}`, `${userHome}`, `${workspaceFolder}`, … in `command`, `args`, `env`, `url`, `headers` | [cursor.com](https://cursor.com/docs/context/mcp) |
| VS Code | `.vscode/mcp.json` | `servers` (with `inputs`) | `${input:ID}`, `${env:NAME}`, `${workspaceFolder}`, … | [code.visualstudio.com](https://code.visualstudio.com/docs/copilot/reference/mcp-configuration) |

- **Claude Code, user and local scope.** Those servers are kept in `~/.claude.json`, together with the
  application's state. `init` neither writes nor reads that file:
  `tracetrim init --host claude-code --scope user` prints the steps (`claude mcp remove NAME --scope user`,
  `claude mcp add --transport stdio --scope user tracetrim -- …`). A `.claude.json` given with
  `--file` is refused for the same reason.
- **A project's file is usually shared.** The entry `init` writes holds paths of your machine (the
  executable, the work directory), and the backup sits next to the file: keep both out of what you
  commit, or use the user's file.
- **VS Code.** The documentation calls the mechanism that reads `.vscode/mcp.json` (the *Local
  harness*) deprecated, to be removed in February 2027, and points new configurations to
  `~/.copilot/mcp-config.json` and to `.mcp.json`, which use `mcpServers`. `init` supports
  `.vscode/mcp.json`; the new files are not supported yet.

### Which servers move, and which stay

A server goes behind tracetrim only if tracetrim can start it exactly as the host did. It stays in the
host, untouched, and the output says why, when:

- it has a field tracetrim does not read: `envFile`, `oauth`, `auth`, `headersHelper`, `timeout`,
  `sandbox`, `dev`, …;
- it uses the SSE transport;
- it uses a variable only the host can resolve: `${input:…}`, `${workspaceFolder}`, `${userHome}`, …;
- a value has a `$` that the host leaves as it is and tracetrim would expand (in Claude Desktop, any
  `$NAME` or `${NAME}`);
- a value of `env` is not text, or a field is `null`;
- you excluded it with `--exclude`.

**References to environment variables are kept as references.** The host goes on resolving them and
passes them to tracetrim; `servers.json` uses `${NAME}`. The output lists every reference translated.

| Host | In the host's file | In the `tracetrim` entry, `env` | In `servers.json` |
|---|---|---|---|
| Claude Code | `${VAR}` | `"VAR": "${VAR}"` | `${VAR}` |
| Claude Code | `${VAR:-default}` | `"VAR": "${VAR:-default}"` | `${VAR}` |
| Cursor, VS Code | `${env:NAME}` | `"NAME": "${env:NAME}"` | `${NAME}` |
| Claude Desktop | values are literal | — | the same literal value |

### Known limits

- **OAuth.** A remote server without `headers` is, for tracetrim, an OAuth server. The login you made
  in the host does not carry over: after `init`, run `tracetrim login --servers WORKDIR/servers.json`
  once.
- **Claude Desktop extensions** (`.mcpb`) and connectors added from *Settings → Connectors* are not in
  `claude_desktop_config.json`: `init` does not see them.
- **Windows.** The mode `0600` is a POSIX permission: on Windows `servers.json` has the permissions of
  its folder. tracetrim expands `%NAME%`, `$$` and `%%` there as well, so a value holding one of them
  (two `%20` in one URL count as `%NAME%`), or a reference between single quotes, stays in the host.

## What to expect from an interactive host

In a host such as Claude Code or Cursor, where a person talks to the agent:

- **Learning needs `tracetrim_done`.** A run that does not end with it counts for the telemetry report
  and not for learning. A host that does not load the skill needs the line `init` prints in the
  agent's instructions.
- **Each work directory learns from what passes through it.** Very different tasks in the same
  directory make trimming less reliable: this is why `init` uses one directory per project.

## What was verified, host by host

| Host | Connects over | Tools pass | Calls `tracetrim_done` | Loads the skill | `expand` works | Hides tool definitions by itself | Verified: when and how |
|---|---|---|---|---|---|---|---|
| OpenAI Playground | HTTP | yes | yes: 7 runs of 7, at least 2 of them without the line in the prompt | **no**: the client declares only the extension `io.modelcontextprotocol/ui` and asks neither `skills/list` nor `resources/read` | yes | not verified | skill: 12 November 2026, one run with `--log-client` (0.4.1); the rest: 12 November 2026, six runs on DataForSEO's server with the 0.4 branch and one with 0.4.1 |
| Responses API | not verified | not verified | not verified | not verified | not verified | **only if asked**: `tool_search` with `defer_loading: true` (`gpt-5.4` and later); documentation, read 12 November 2026 | — |
| Claude Desktop | not verified | not verified | not verified | not verified | not verified | not verified | — |
| Claude Code | stdio, entry written by `tracetrim init` (references in `env` resolved by the host) | yes | yes: 1 run of 1, without the line in the prompt | **read, not loaded**: the client (2.1.322) declares only `io.modelcontextprotocol/tasks`, not the Skills extension; it lists the resources, and the model read `skill://tracetrim/SKILL.md` through its tool for MCP resources before the first call, after the server's instructions | not tried: nothing was trimmed yet | **yes, by default**: MCP tool search defers the definitions (`ENABLE_TOOL_SEARCH=false` turns it off); documentation, read 12 November 2026 | 12 November 2026, one run with `--log-client` (0.4.1 proxy, `init` of 0.5) on a simulated orders server |
| Claude Code on Windows | stdio, entry written by `tracetrim init` in PowerShell (Windows paths, `tracetrim.exe`) | yes | yes: 1 run of 1 | **read, not loaded**, as on Linux: the model read `skill://tracetrim/SKILL.md` as an MCP resource | not tried: nothing was trimmed yet | yes, by default (see the note) | 13 November 2026, tracetrim 0.7.0 from PyPI with the Windows Python, on a simulated orders server; `init` left in the host a server with `100%%` in its `env`, as its Windows rules require. The first start after installing timed out (see the notes) |
| Cursor | not verified | not verified | not verified | not verified | not verified | according to its blog ("dynamic context discovery"), **not verified** | — |
| VS Code | stdio, entry written by `tracetrim init` (references in `env` resolved by the host), folder opened from WSL | yes | yes: 1 run of 1, without the line in the prompt | **no**: the client (1.144.1) declares only `io.modelcontextprotocol/ui` and asks neither for the skill nor for resources | not tried: nothing was trimmed yet | not verified | 13 November 2026, one run in Copilot Chat's agent mode with `--log-client`, tracetrim 0.7.0 from PyPI, on a simulated orders server |
| MCP Inspector | stdio | not verified | does not apply: no model | the skill is served correctly: outcome `verified` | not verified | does not apply: no model | 11 November 2026, MCP Inspector CLI 2.13.0, `--method skills/list --verify` ([docs/skill.md](skill.md)) |

Notes:

- The Playground's client presents itself as `openai-mcp (Responses API)` 1.0.0. It lists the tools
  with `server/discover` (protocol 2026-07-28) and holds the conversation after `initialize`
  (2025-11-25). It called `tracetrim_done` guided by the tool's description alone.
- MCP Inspector shows that the skill is served as the extension asks, not that a model follows it.
- **Windows, first start.** Right after `pip install`, the first start of the proxy took more than
  the 30 seconds Claude Code waits for a server to connect (Windows checks the new files the first time
  they run, while the host starts its other servers too); the next start connected in 2 seconds. If a
  host reports that `tracetrim` did not connect, start it again, or give servers more time: for Claude
  Code, set `MCP_TIMEOUT` (milliseconds) before starting it, as in `$env:MCP_TIMEOUT=90000; claude`.
  Since 1.0, `tracetrim init --write` starts the new entry once itself (up to 120 seconds), so the
  slow first start happens there, not inside the host.
- VS Code shows what tracetrim writes on standard error (its per-call log) in *Output → MCP: tracetrim*
  with the label `[warning]`: that is the channel, not a problem. `--quiet` silences it.
- *Hides tool definitions by itself*: whether the host or the API loads tool definitions only when the
  model looks for them. Where it does, the report's section on tool definitions counts what tracetrim
  publishes, not what the model reads on each turn, and its suggestion on tools never called matters
  less. Sources: Claude Code, <https://code.claude.com/docs/en/mcp> ("Configure tool search"); Responses
  API, <https://developers.openai.com/api/docs/guides/tools-tool-search>; Cursor, a blog post,
  <https://cursor.com/blog/dynamic-context-discovery>, not its documentation. The measure and the
  reasons why tracetrim hides no tool: [docs/definitions.md](definitions.md).
- `init` writes a stdio entry for Claude Desktop, Claude Code, Cursor and VS Code, in the format their
  documentation gives. That the host then starts it and passes the tools is what *not verified* means.

## Verifying a host yourself

Add `--log-client FILE` to the arguments of the `tracetrim` entry (or to the command, over HTTP),
restart the host and hold one short conversation that needs a tool. The file gets one JSON line per
request of the client; it never holds arguments, results, headers or tokens
([docs/skill.md](skill.md) has the details).

| To know whether | Look for |
|---|---|
| the host connects and the tools pass | `initialize` (or `server/discover`), then `tools/list` and a `tools/call` with the name of one of your tools |
| it calls `tracetrim_done` | a `tools/call` named `tracetrim_done` at the end of the run |
| it loads the skill | `io.modelcontextprotocol/skills` among the extensions it declares, then `skills/list` or `skills/get`, or `resources/read` of `skill://tracetrim/SKILL.md` |
| its model reads the skill anyway | no Skills extension declared, but `resources/list` and then `resources/read` of `skill://tracetrim/SKILL.md` in the middle of a conversation: the model fetched it through the host's tool for MCP resources (Claude Code does this) |
| `expand` works | after learning has started trimming, a `tools/call` named `expand` (and the report's count of expansions) |

To report what you found, open an issue at <https://github.com/alepom/tracetrim/issues> with the name
and version of the host, the date, the version of tracetrim, and the answer to the four questions
above. The lines of the log can be attached as they are: they hold no content of the conversation.
