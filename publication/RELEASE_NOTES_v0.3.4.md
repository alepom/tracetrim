**Agents read more than they use.** tracetrim learns, from your agent's own runs, which fields of each
tool output the agent actually uses, and trims the rest — with a telemetry report you can read and a
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

### What the study found

- On held-out runs, a learned projection removed 63% and 70% of tool-output tokens on two of the three
  agents with real APIs (12% on the third) and 69% and 77% on the two simulated ones; re-serialising the
  JSON alone, which removes nothing, accounts for up to 13% (real) and 17% (simulated). On the same
  conversations, the same two real-API agents: 59–68% (0.1), 46–53% (0.2), 17–28% for Headroom, 0–12% for
  re-serialisation alone; simulated: 69–77%, 57–61%, 31–33%, 7–17%.
- Learning from tool calls alone (what a gateway sees) missed a used field in 49% of held-out calls of
  the three agents on real APIs, against 4% when the learner also sees the final answer.
- Where it fails: values the agent transforms, fields used only to reason, and — without an id
  field — agents inventing ids. Identifiers are always kept since 0.2.

The studies ran on 0.1 and 0.2; the automatic mode, the multi-server proxy, OAuth and function calling
came after them and were checked end to end with a real agent, not evaluated under a protocol.

0.3.4 includes the fixes of three independent pre-publication reviews (proxy security, correctness, and
the paper): see the changelog.

See [CHANGELOG.md](CHANGELOG.md) for the changes by version.
