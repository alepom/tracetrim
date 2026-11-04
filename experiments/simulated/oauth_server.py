#!/usr/bin/env python3
"""A local MCP server protected by OAuth, to test tracetrim's OAuth client without a browser.

  python -m experiments.simulated.oauth_server --port 8897 [--ttl 3]

It is its own authorization server, as many MCP servers are: discovery
(protected resource and authorization server metadata), dynamic client
registration, authorization code with PKCE, refresh tokens. The login page is
skipped: /authorize approves at once and redirects with the code, so a test can
follow the redirect itself. Access tokens live `--ttl` seconds, to exercise
renewal. One tool, `whoami`, returns which client and which token were used.

Every grant is logged on stderr as [oauth] lines.
"""
import argparse
import secrets
import sys
import time

from mcp.server.auth.provider import AccessToken, AuthorizationCode, RefreshToken, construct_redirect_uri
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import Context, MCPServer


def log(line):
    print(f"[oauth] {line}", file=sys.stderr, flush=True)


class Provider:
    def __init__(self, ttl: int):
        self.ttl = ttl
        self.clients, self.codes, self.access, self.refresh = {}, {}, {}, {}

    async def get_client(self, client_id):
        return self.clients.get(client_id)

    async def register_client(self, client_info):
        self.clients[client_info.client_id] = client_info
        log(f"client registered: {client_info.client_name}")

    async def authorize(self, client, params):
        code = secrets.token_urlsafe(16)
        self.codes[code] = AuthorizationCode(
            code=code, scopes=params.scopes or ["read"], expires_at=time.time() + 300, client_id=client.client_id,
            code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly, resource=params.resource)
        log("authorization approved (no login page in the simulator)")
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client, code):
        return self.codes.get(code)

    def _issue(self, client_id, scopes, resource):
        a, r = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
        self.access[a] = AccessToken(token=a, client_id=client_id, scopes=scopes,
                                     expires_at=int(time.time()) + self.ttl, resource=resource)
        self.refresh[r] = RefreshToken(token=r, client_id=client_id, scopes=scopes, resource=resource)
        from mcp.shared.auth import OAuthToken
        return OAuthToken(access_token=a, token_type="Bearer", expires_in=self.ttl, refresh_token=r,
                          scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        self.codes.pop(authorization_code.code, None)
        log("token issued (authorization code)")
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token):
        return self.refresh.get(refresh_token)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        self.refresh.pop(refresh_token.token, None)
        log("token renewed (refresh token)")
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token):
        t = self.access.get(token)
        if t and t.expires_at and t.expires_at < time.time():
            return None
        return t

    async def revoke_token(self, token):
        self.access.pop(getattr(token, "token", ""), None)
        self.refresh.pop(getattr(token, "token", ""), None)


def build(port: int, ttl: int) -> MCPServer:
    base = f"http://127.0.0.1:{port}"
    provider = Provider(ttl)
    server = MCPServer("oauth-sim", auth_server_provider=provider, auth=AuthSettings(
        issuer_url=base, resource_server_url=f"{base}/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=["read"],
                                                              default_scopes=["read"]),
        required_scopes=["read"], validate_token_resource=True))

    from pydantic import BaseModel

    class Who(BaseModel):
        user: str
        plan: str
        internal: dict

    @server.tool()
    def whoami(ctx: Context) -> Who:
        """Which client is calling, with a payload of fields the agent does not need. Typed, so that the
        server also sends structured content (the proxy must pass it through)."""
        return Who(user="demo", plan="pro", internal={f"note_{i}": f"remark {i}" for i in range(10)})

    return server


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("--port", type=int, default=8897)
    a.add_argument("--ttl", type=int, default=3600)
    o = a.parse_args()
    log(f"OAuth-protected MCP server on http://127.0.0.1:{o.port}/mcp (access tokens: {o.ttl}s)")
    build(o.port, o.ttl).run("streamable-http", host="127.0.0.1", port=o.port)
