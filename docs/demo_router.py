#!/usr/bin/env python3
"""One public address for the two demo servers, routed by path.

  python docs/demo_router.py --port 8800 --route /billing=8798 --route /tracetrim=8799

The free ngrok plan gives one domain: two tunnels on it would share it and
ngrok would spread requests between them at random. So the demo opens one
tunnel, to this router, which forwards

  /billing/...    -> 127.0.0.1:8798/...    (the agent's own MCP server)
  /tracetrim/...  -> 127.0.0.1:8799/...    (tracetrim, and its /report)

Bodies are streamed both ways (MCP over streamable HTTP answers with SSE);
the Host header is kept, so each server's allowed-host check still applies.
"""
import argparse

import httpx
import uvicorn

HOP = {b"connection", b"keep-alive", b"transfer-encoding", b"upgrade", b"proxy-connection", b"te", b"trailer"}


def build(routes: dict[str, int]):
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0))

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                m = await receive()
                if m["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif m["type"] == "lifespan.shutdown":
                    await client.aclose()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        path = scope["path"]
        target = next(((p, port) for p, port in routes.items() if path == p or path.startswith(p + "/")), None)
        if target is None:
            await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"not found: use /billing/mcp or /tracetrim/mcp"})
            return
        prefix, port = target
        body = b""
        while True:
            m = await receive()
            body += m.get("body", b"")
            if not m.get("more_body"):
                break
        url = f"http://127.0.0.1:{port}{path[len(prefix):] or '/'}"
        if scope.get("query_string"):
            url += "?" + scope["query_string"].decode()
        headers = [(k, v) for k, v in scope["headers"] if k.lower() not in HOP and k.lower() != b"content-length"]
        req = client.build_request(scope["method"], url, headers=headers, content=body)
        resp = await client.send(req, stream=True)
        try:
            await send({"type": "http.response.start", "status": resp.status_code,
                        "headers": [(k, v) for k, v in resp.headers.raw if k.lower() not in HOP]})
            async for chunk in resp.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            await resp.aclose()

    return app


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("--port", type=int, default=8800)
    a.add_argument("--route", action="append", required=True, help="/prefix=PORT")
    o = a.parse_args()
    routes = {r.split("=")[0]: int(r.split("=")[1]) for r in o.route}
    uvicorn.run(build(routes), host="127.0.0.1", port=o.port, log_level="warning")
