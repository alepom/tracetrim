**Agents read more than they visibly reuse.** tracetrim learns, from your agent's own runs, which fields of each
tool output the agent reuses in what it writes, and trims the rest — with a telemetry report you can read and a
way back (`expand`) for anything it removed.

Paper: [10.5281/zenodo.23953934](https://doi.org/10.5281/zenodo.23953934) · Software and data:
[10.5281/zenodo.23954125](https://doi.org/10.5281/zenodo.23954125)

### Use it

- **In front of your MCP servers**, with no change to your agent's code: copy your `mcpServers` block
  into `mcp.json`, run `tracetrim proxy --servers mcp.json --auto tracetrim-data/`, point the agent at
  tracetrim instead of the servers, and add one line to its instructions. It observes the first runs,
  then trims on its own; the report is kept up to date from the first run.
- **With function calling**, no MCP: `tracetrim.auto.AutoTrim`, three calls in your loop.
- **Credentials**: headers and environment variables (with `${VAR}`), or OAuth with a one-time
  `tracetrim login`.
- **Hosted agents** (OpenAI Playground, Responses API): the proxy over HTTP with a bearer token.

### What the exploratory study found

- On held-out runs, a learned projection removed 63% and 70% of tool-output tokens on two of the three
  agents with real APIs (12% on the third) and 69% and 77% on the two simulated ones. Against JSON already
  minified, the 0.2 projection still removes 39% and 35% (real) and 36% and 53% (simulated); on the agent
  that cites most of what it receives, a minifier alone does better. On the same conversations, Headroom
  (stateless, aggressive setting) removed 17–28% on those two real-API agents and 31–33% on the simulated ones.
- Learning from tool calls alone (what a gateway sees) missed a used field in 49% of the held-out
  structured calls it trims (156/317) on the three real-API agents, against 4% when the learner also sees
  the final answer.
- Where it fails: values the agent transforms, fields used only to reason, and — without an id
  field — agents inventing ids. Identifiers are always kept since 0.2. The safeguards repaired the failures
  found, on the same conversations; how often quality is preserved on new tasks and models is still open.

The studies ran on 0.1 and 0.2; the automatic mode, the multi-server proxy, OAuth and function calling
came after them and were checked end to end with a real agent, not evaluated under a protocol.
0.3.4 includes the fixes of three internal reviews and three external review rounds.

Changes by version: [CHANGELOG.md](https://github.com/alepom/tracetrim/blob/master/CHANGELOG.md).
