#!/usr/bin/env python3
"""The simulated billing server (C4) over streamable HTTP, for demos.

  .venv/bin/python -m experiments.simulated.serve_http --port 8798 --token SECRET

It stands for "the MCP server the agent already uses": no tracetrim code is
on this path. Every tool call is logged on stderr with the size of what it
returns, so that in a demo one can see the calls reach this server whether
the agent talks to it directly or through the tracetrim proxy.
"""
import argparse
import json
import sys

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from experiments.simulated import server_c4


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--port", type=int, default=8798)
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--token", required=True, help="required as 'Authorization: Bearer TOKEN'")
    a.add_argument("--allowed-host", action="append", default=[])
    o = a.parse_args()

    security = TransportSecuritySettings(enable_dns_rebinding_protection=bool(o.allowed_host),
                                         allowed_hosts=o.allowed_host, allowed_origins=[])
    app = server_c4.app.streamable_http_app(transport_security=security)

    async def guarded(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        if dict(scope.get("headers") or []).get(b"authorization", b"").decode() != f"Bearer {o.token}":
            await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        # Read the request to log tool calls, then hand the same body to the app.
        chunks, more = [], True
        while more:
            m = await receive()
            chunks.append(m.get("body", b""))
            more = m.get("more_body", False)
        body = b"".join(chunks)
        try:
            msg = json.loads(body or b"{}")
            if isinstance(msg, dict) and msg.get("method") == "tools/call":
                p = msg.get("params") or {}
                print(f"[billing] {p.get('name')} {json.dumps(p.get('arguments') or {}, ensure_ascii=False)[:80]}",
                      file=sys.stderr, flush=True)
        except ValueError:
            pass
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await app(scope, replay, send)

    print(f"[billing] simulated billing MCP server on http://{o.host}:{o.port}/mcp", file=sys.stderr, flush=True)
    uvicorn.run(guarded, host=o.host, port=o.port, log_level="warning")


if __name__ == "__main__":
    main()
