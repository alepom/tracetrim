"""Simulated MCP server for C5: a GitHub-shaped repository API (stdio).

JSON responses are serialised with 2-space indentation; job logs are plain
text, as `GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs` returns them.
"""
import json

from mcp.server.mcpserver import MCPServer

from experiments.simulated.worlds import REPO, github_world

app = MCPServer("github-repo-sim")
W = github_world()


def out(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def page(items, per_page, page_n):
    per_page = max(1, min(100, per_page))
    start = (max(1, page_n) - 1) * per_page
    return items[start:start + per_page]


def not_found():
    return out({"message": "Not Found", "documentation_url": "https://docs.github.com/rest",
                "status": "404"})


@app.tool()
def gh_list_workflow_runs(branch: str = "", status: str = "", per_page: int = 10, page_n: int = 1) -> str:
    """List workflow runs of the repository, newest first (GitHub `GET /repos/{owner}/{repo}/actions/runs`).
    branch: filter by head branch. status: e.g. 'failure', 'success', 'completed'. per_page: 1-100."""
    runs = [r for r in reversed(W["runs"])
            if (not branch or r["head_branch"] == branch)
            and (not status or status in (r["status"], r["conclusion"]))]
    return out({"total_count": len(runs), "workflow_runs": page(runs, per_page, page_n)})


@app.tool()
def gh_list_jobs_for_workflow_run(run_id: int) -> str:
    """List the jobs of a workflow run, with their steps (GitHub `GET .../actions/runs/{run_id}/jobs`)."""
    jobs = W["jobs"].get(int(run_id))
    return out({"total_count": len(jobs), "jobs": jobs}) if jobs is not None else not_found()


@app.tool()
def gh_download_job_logs(job_id: int) -> str:
    """Download the plain-text log of a job (GitHub `GET .../actions/jobs/{job_id}/logs`)."""
    return W["logs"].get(int(job_id)) or not_found()


@app.tool()
def gh_list_issues(state: str = "open", labels: str = "", per_page: int = 10, page_n: int = 1) -> str:
    """List issues of the repository (GitHub `GET /repos/{owner}/{repo}/issues`).
    labels: comma-separated label names. per_page: 1-100."""
    want = {l.strip() for l in labels.split(",") if l.strip()}
    items = [i for i in W["issues"] if i["state"] == state or state == "all"]
    if want:
        items = [i for i in items if want <= {l["name"] for l in i["labels"]}]
    return out(page(items, per_page, page_n))


@app.tool()
def gh_get_issue(issue_number: int) -> str:
    """Get one issue (GitHub `GET /repos/{owner}/{repo}/issues/{issue_number}`)."""
    i = next((i for i in W["issues"] if i["number"] == int(issue_number)), None)
    return out(i) if i else not_found()


@app.tool()
def gh_list_pull_requests(state: str = "open", per_page: int = 10, page_n: int = 1) -> str:
    """List pull requests of the repository (GitHub `GET /repos/{owner}/{repo}/pulls`). per_page: 1-100."""
    items = [p for p in W["pulls"] if p["state"] == state or state == "all"]
    return out(page(items, per_page, page_n))


if __name__ == "__main__":
    app.run()
