# Sources of the simulated tools (C4, C5)

Fixed before Phase A, as required by protocol amendment 1 (A1).

The simulated MCP servers return payloads generated **from the public OpenAPI
descriptions** of two widely used APIs: every field name, nesting level and type
comes from the schema; values are synthetic, produced by a seeded generator
(`synth.py`), and contain no real person, company or account.

| Case | API | OpenAPI description | Commit | Version | License | sha256 of the file |
|---|---|---|---|---|---|---|
| C4 billing support | Stripe API | `stripe/openapi`, `openapi/spec3.json` | `b213739d4879835a5af50f15daaa3a41ae267985` (2026-11-03) | `2026-09-30.endive` | MIT | `7cff4cc46d0654101a36a3302f7544640f5f73e4f3cad6f264f0e23c19fa1776` |
| C5 CI and issue triage | GitHub REST API | `github/rest-api-description`, `descriptions/api.github.com/api.github.com.json` | `99b1432b55b9a71d0881fd792a87f1807cf4c8f2` (2026-11-04) | `1.1.4` | MIT | `25c06a5ada7843db70fc54f8888df8f616e8689206c5da4bc60badf9bc577594` |

The spec files are downloaded into `specs/` (git-ignored, 22 MB) by:

```
curl -sLo specs/stripe-spec3.json https://raw.githubusercontent.com/stripe/openapi/b213739d4879835a5af50f15daaa3a41ae267985/openapi/spec3.json
curl -sLo specs/github-api.json  https://raw.githubusercontent.com/github/rest-api-description/99b1432b55b9a71d0881fd792a87f1807cf4c8f2/descriptions/api.github.com/api.github.com.json
```

## Schemas used

- Stripe: `customer`, `charge`, `invoice`, `subscription`, `refund`, `dispute`,
  and the list envelope (`object: "list"`, `data`, `has_more`, `url`) the API wraps
  around every list endpoint.
- GitHub: `issue`, `pull-request-simple`, `workflow-run`, `job`; job logs are plain
  text, as `GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs` returns them.

## Generation rules (what is ours, and therefore declared)

- **Every property in the schema is present**, as the real APIs return full objects.
  Expandable Stripe references (`anyOf: [string, object]`) stay unexpanded ids, the
  API's default. Nested objects beyond depth 4 are emitted as null when nullable.
- Values follow the schema type and format; names and enumerations come from the
  schema. A small number of fields that the tasks depend on (amounts, statuses,
  dates, failure messages, job steps, error lines) are set explicitly by the case's
  scenario, listed in `worlds.py`.
- Seeds: C4 = 4, C5 = 5. The same seed always produces the same payloads.

Because we generate the payloads, C4 and C5 are reported separately from the
real-API cases C1-C3 and never pooled with them.
