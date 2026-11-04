#!/usr/bin/env bash
# Demo: the tracetrim proxy in front of the simulated billing server (C4),
# exposed with ngrok so that the OpenAI Playground can reach it.
#
#   docs/demo_playground.sh trim             # the 0.2 configuration (default)
#   docs/demo_playground.sh raw              # same server, nothing trimmed: the baseline
#   docs/demo_playground.sh trim-headroom    # 0.2 configuration, then Headroom (experimental)
#
# Environment (both optional, both recommended so that nothing changes between modes):
#   DEMO_TOKEN    the secret the Playground sends as "Authorization: Bearer ..."
#   NGROK_DOMAIN  your static ngrok domain, e.g. something.ngrok-free.app
#
# The proxy prints one line per tool call while the agent works: keep this
# terminal next to the Playground.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE=${1:-trim}
PORT=8799
TOKEN=${DEMO_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')}

case "$MODE" in
  raw)           ARGS=() ;;
  trim)          ARGS=(--config out/demo/report.config.json) ;;
  trim-headroom) ARGS=(--config out/demo/report.config.json --structural headroom) ;;
  *) echo "mode: raw | trim | trim-headroom"; exit 2 ;;
esac

# The configuration is learned from the eight Phase A runs of C4.
[ -f out/demo/report.config.json ] || .venv/bin/tracetrim report traces/C4/A*.jsonl -o out/demo/report.html

if [ -n "${NGROK_DOMAIN:-}" ]; then
  ngrok http "$PORT" --url="$NGROK_DOMAIN" --log stdout > out/demo/ngrok.log 2>&1 &
else
  ngrok http "$PORT" --log stdout > out/demo/ngrok.log 2>&1 &
fi
NGROK_PID=$!
trap 'kill $NGROK_PID 2>/dev/null || true' EXIT

URL=""
for _ in $(seq 30); do
  URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c '
import json, sys
try:
    t = json.load(sys.stdin)["tunnels"]
    print(next((x["public_url"] for x in t if x["public_url"].startswith("https")), ""))
except Exception:
    print("")' || true)
  [ -n "$URL" ] && break
  sleep 1
done
[ -n "$URL" ] || { echo "ngrok did not start: see out/demo/ngrok.log"; exit 1; }

cat <<EOF

  Playground -> Tools -> Add -> MCP server
    URL       $URL/mcp
    Header    Authorization: Bearer $TOKEN
    Approval  never
  System message  experiments/prompts/c4-system.md
  User message    experiments/prompts/c4-user.md

  mode: $MODE      (Ctrl+C to stop proxy and tunnel)

EOF

.venv/bin/tracetrim proxy --upstream-cmd "$PWD/.venv/bin/python -m experiments.simulated.server_c4" \
  "${ARGS[@]}" --http "$PORT" --token "$TOKEN" --allowed-host "${URL#https://}"
