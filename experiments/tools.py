"""The tools the study's agents can call, exactly as the protocol fixes them.

DataForSEO goes through its MCP server, as in the pilot: the agent receives
the same text a hosted MCP integration would. OpenAlex goes through its REST
API: the agent receives the response body as is.

Two gates live here, not in the prompts, because a prompt is an
instruction and a gate is a guarantee:

  domain gate   parsing, Labs `target` and `site:` queries only on the
                author's domain (protocol, "Domain rule")
  quota gate    OpenAlex is used without an API key: stop before the free
                daily allowance runs out instead of failing mid-run
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

ROOT = Path(__file__).resolve().parent.parent
DFS_URL = "https://dfs.mcp.REDACTED.it/mcp"
OWN_DOMAIN = "alessiopomaro.it"
BLOCKED = ("Error: blocked by the study's domain rule. Only alessiopomaro.it may be "
           "parsed, used as a target or used in site: queries.")

CASE_TOOLS = {
    # The pilot's final DataForSEO set (course project, config/dataforseo-set-finale.txt).
    "C1": ["kw_data_google_trends_explore", "kw_data_google_ads_search_volume",
           "dataforseo_labs_google_keyword_suggestions", "dataforseo_labs_google_related_keywords",
           "dataforseo_labs_google_keyword_ideas", "serp_organic_live_advanced",
           "serp_youtube_organic_live_advanced", "dataforseo_labs_google_ranked_keywords",
           "dataforseo_labs_google_relevant_pages", "dataforseo_labs_search_intent",
           "dataforseo_labs_bulk_keyword_difficulty", "on_page_content_parsing"],
    "C2": ["serp_organic_live_advanced", "on_page_content_parsing"],
    "C3": ["openalex_search_works", "openalex_get_work"],
    # Simulated servers (amendment 1): every tool they expose.
    "C4": ["stripe_search_customers", "stripe_list_charges", "stripe_retrieve_charge", "stripe_list_refunds",
           "stripe_list_invoices", "stripe_retrieve_invoice", "stripe_list_subscriptions"],
    "C5": ["gh_list_workflow_runs", "gh_list_jobs_for_workflow_run", "gh_download_job_logs",
           "gh_list_issues", "gh_get_issue", "gh_list_pull_requests"],
}
SIMULATED = {"C4": "experiments.simulated.server_c4", "C5": "experiments.simulated.server_c5"}


def env(name: str) -> str | None:
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return None


def _own(host: str) -> bool:
    host = host.lower().strip().removeprefix("www.")
    return host == OWN_DOMAIN or host.endswith("." + OWN_DOMAIN)


def domain_gate(tool: str, args: dict) -> bool:
    """True when the call respects the domain rule."""
    if tool == "on_page_content_parsing":
        return _own(urllib.parse.urlparse(str(args.get("url", ""))).hostname or "")
    if "target" in args:
        t = str(args["target"])
        host = urllib.parse.urlparse(t if "://" in t else "//" + t).hostname or ""
        return _own(host)
    for v in args.values():
        for site in re.findall(r"site:\s*([^\s\"']+)", str(v)):
            host = urllib.parse.urlparse(site if "://" in site else "//" + site).hostname or ""
            if not _own(host):
                return False
    return True


class DataForSEO:
    """MCP client session, kept open for one run."""

    def __init__(self, allowed: list[str], module: str | None = None):
        self.allowed = allowed
        self.module = module        # a simulated server, launched over stdio
        self.rejections = 0
        self.retries = 0
        self.tool_failures = []     # calls that failed even after reconnecting

    def _transport(self):
        if self.module:
            return stdio_client(StdioServerParameters(command=sys.executable, args=["-m", self.module],
                                                      cwd=str(ROOT)))
        token = env("DFS_MCP_TOKEN")
        if not token:
            raise SystemExit("DFS_MCP_TOKEN missing in .env")
        http = create_mcp_http_client(headers={"X-MCP-Token": token})
        return streamable_http_client(DFS_URL, http_client=http, max_sse_event_size=16 * 1024 * 1024)

    async def _connect(self):
        self._cm = self._transport()
        read, write = await self._cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self.session = await self._session_cm.__aenter__()
        await self.session.initialize()

    async def _close(self, *exc):
        for cm in (getattr(self, "_session_cm", None), getattr(self, "_cm", None)):
            try:
                if cm is not None:
                    await cm.__aexit__(*(exc or (None, None, None)))
            except BaseException:
                pass                # a broken stream may refuse to close cleanly

    async def __aenter__(self):
        await self._connect()
        listed = {t.name: t for t in (await self.session.list_tools()).tools}
        missing = [n for n in self.allowed if n not in listed]
        if missing:
            raise SystemExit(f"tools missing on the MCP server: {missing}")
        self.schemas = []
        for n in self.allowed:
            t = listed[n]
            params = dict(t.input_schema or {"type": "object", "properties": {}})
            params.pop("$schema", None)
            self.schemas.append({"type": "function", "name": n,
                                 "description": t.description or "", "parameters": params, "strict": False})
        return self

    async def __aexit__(self, *exc):
        await self._close(*exc)

    async def call(self, name: str, args: dict) -> str:
        if not self.module and not domain_gate(name, args):
            self.rejections += 1
            return BLOCKED
        # A dropped stream kills the session: each retry reconnects. If the
        # call still fails, the agent gets an error message, as it would from
        # a real failing tool, and the run goes on, flagged.
        for attempt in range(3):
            try:
                r = await self.session.call_tool(name, args, read_timeout_seconds=240)
                break
            except Exception as e:      # MCPError, transport errors
                self.retries += 1
                print(f"    {name} {json.dumps(args, ensure_ascii=False)[:160]} failed "
                      f"({type(e).__name__}: {str(e)[:60]}), attempt {attempt + 1}/3", flush=True)
                if attempt == 2:
                    self.tool_failures.append({"tool": name, "arguments": args, "error": str(e)[:200]})
                    return "Error: the tool did not respond (the server closed the connection). Try a different request."
                await asyncio.sleep(5 * (attempt + 1))
                await self._close()
                await self._connect()
        parts = [getattr(c, "text", "") for c in (getattr(r, "content", None) or [])]
        return "".join(parts)


class OpenAlex:
    """REST client. No API key: the free allowance is read from the headers."""

    BASE = "https://api.openalex.org"
    MIN_REMAINING_USD = 0.005

    def __init__(self, allowed: list[str]):
        self.allowed = allowed
        self.rejections = 0
        self.remaining_usd = None
        self.quota_stop = False
        self.schemas = [
            {"type": "function", "name": "openalex_search_works", "strict": False,
             "description": "Search OpenAlex works. Returns the OpenAlex API response as JSON.",
             "parameters": {"type": "object", "required": ["search"], "properties": {
                 "search": {"type": "string", "description": "Full-text search query."},
                 "filter": {"type": "string", "description": "OpenAlex filter, e.g. 'publication_year:2023-2026,type:article'."},
                 "sort": {"type": "string", "description": "e.g. 'relevance_score:desc' or 'cited_by_count:desc'."},
                 "per_page": {"type": "integer", "minimum": 1, "maximum": 5, "description": "Results per page, 1-5."},
                 "page": {"type": "integer", "minimum": 1}}}},
            {"type": "function", "name": "openalex_get_work", "strict": False,
             "description": "Get one OpenAlex work by OpenAlex ID (W...) or DOI. Returns the OpenAlex API response as JSON.",
             "parameters": {"type": "object", "required": ["id"], "properties": {
                 "id": {"type": "string", "description": "OpenAlex ID like W4391234567, or a DOI."}}}},
        ]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def _get(self, path: str, params: dict) -> str:
        if self.remaining_usd is not None and self.remaining_usd < self.MIN_REMAINING_USD:
            self.quota_stop = True
            return "Error: OpenAlex daily quota exhausted for this run."
        url = f"{self.BASE}{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
        req = urllib.request.Request(url, headers={"User-Agent": "tracetrim-research"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8")
                rem = r.headers.get("x-ratelimit-remaining-usd")
                self.remaining_usd = float(rem) if rem is not None else self.remaining_usd
                return body
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self.quota_stop = True
            return f"Error: OpenAlex HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"

    async def call(self, name: str, args: dict) -> str:
        if name == "openalex_search_works":
            p = {"search": args.get("search", "")}
            for k in ("filter", "sort", "page"):
                if args.get(k):
                    p[k] = args[k]
            # Capped in the tool, not in the prompt: one work weighs ~6,000
            # tokens, and the pilot's 10 per page cost 1.14 USD in one run.
            p["per_page"] = max(1, min(5, int(args.get("per_page") or 5)))
            return self._get("/works", p)
        if name == "openalex_get_work":
            wid = str(args.get("id", "")).strip()
            if wid.lower().startswith(("10.", "doi:", "https://doi.org/")):
                wid = "doi:" + wid.removeprefix("doi:").removeprefix("https://doi.org/")
            return self._get("/works/" + urllib.parse.quote(wid, safe=":/"), {})
        return f"Error: unknown tool {name}"


def toolset(case: str):
    names = CASE_TOOLS[case]
    if case == "C3":
        return OpenAlex(names)
    return DataForSEO(names, SIMULATED.get(case))


def to_args(raw: str) -> dict:
    try:
        d = json.loads(raw or "{}")
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}
