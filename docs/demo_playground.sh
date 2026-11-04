#!/usr/bin/env bash
# Demo with two MCP servers, both reachable by the OpenAI Playground:
#
#   billing    the server the agent already uses (simulated, Stripe-shaped, C4)
#   tracetrim  our proxy, placed in front of billing, with the 0.2 configuration
#
#   docs/demo_playground.sh             # start both, print the two URLs, show both logs
#   docs/demo_playground.sh --headroom  # tracetrim also applies Headroom (experimental)
#
# Environment (optional, recommended): DEMO_TOKEN (same token for both servers),
# NGROK_DOMAIN (static domain, used for the tracetrim URL so it never changes).
#
# Everything stays up until Ctrl+C: the scenes are switched in the Playground,
# not here. In this terminal, [billing] lines show each call reaching the
# server the agent already used; [tracetrim] lines show what the proxy removed.
set -euo pipefail
cd "$(dirname "$0")/.."

BILLING_PORT=8798
TRACETRIM_PORT=8799
TOKEN=${DEMO_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')}
EXTRA=()
[ "${1:-}" = "--headroom" ] && EXTRA=(--structural headroom)
mkdir -p out/demo
[ -f out/demo/report.config.json ] || .venv/bin/tracetrim report traces/C4/A*.jsonl -o out/demo/report.html

PIDS=()
cleanup() { kill "${PIDS[@]}" 2>/dev/null || true; }
trap cleanup EXIT

# Two tunnels from one ngrok agent: the user's config (authtoken) plus ours.
{
  echo 'version: "3"'
  echo 'tunnels:'
  echo '  billing:'
  echo '    proto: http'
  echo "    addr: $BILLING_PORT"
  echo '  tracetrim:'
  echo '    proto: http'
  echo "    addr: $TRACETRIM_PORT"
  [ -n "${NGROK_DOMAIN:-}" ] && echo "    domain: $NGROK_DOMAIN"
} > out/demo/ngrok-demo.yml
ngrok start --config "$HOME/.config/ngrok/ngrok.yml" --config out/demo/ngrok-demo.yml billing tracetrim \
  --log stdout > out/demo/ngrok.log 2>&1 &
PIDS+=($!)

url_of() {
  curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c '
import json, sys
try:
    for t in json.load(sys.stdin)["tunnels"]:
        if t["name"] == sys.argv[1] and t["public_url"].startswith("https"):
            print(t["public_url"]); break
except Exception:
    pass' "$1"
}
BILLING_URL="" ; TRACETRIM_URL=""
for _ in $(seq 30); do
  BILLING_URL=$(url_of billing); TRACETRIM_URL=$(url_of tracetrim)
  [ -n "$BILLING_URL" ] && [ -n "$TRACETRIM_URL" ] && break
  sleep 1
done
if [ -z "$BILLING_URL" ] || [ -z "$TRACETRIM_URL" ]; then
  echo "ngrok did not open both tunnels: see out/demo/ngrok.log"; exit 1
fi

.venv/bin/python -m experiments.simulated.serve_http --port "$BILLING_PORT" --token "$TOKEN" \
  --allowed-host "${BILLING_URL#https://}" --allowed-host "127.0.0.1:$BILLING_PORT" > out/demo/billing.log 2>&1 &
PIDS+=($!)
sleep 2
.venv/bin/tracetrim proxy --upstream-url "http://127.0.0.1:$BILLING_PORT/mcp" --header "Authorization=Bearer $TOKEN" \
  --config out/demo/report.config.json "${EXTRA[@]}" \
  --http "$TRACETRIM_PORT" --token "$TOKEN" --allowed-host "${TRACETRIM_URL#https://}" > out/demo/tracetrim.log 2>&1 &
PIDS+=($!)

cat <<EOF

  Playground -> Tools -> Add -> MCP server   (header for both: Authorization: Bearer $TOKEN)

    billing    $BILLING_URL/mcp       the MCP the agent already uses
    tracetrim  $TRACETRIM_URL/mcp       in front of billing

  Scene 1: connect only billing, run the agent.
  Scene 2: add tracetrim, switch billing off, run again.

  Ctrl+C stops servers and tunnels.

EOF
tail -n +1 -f out/demo/billing.log out/demo/tracetrim.log | grep --line-buffered -E '^\[(billing|tracetrim)\]'
