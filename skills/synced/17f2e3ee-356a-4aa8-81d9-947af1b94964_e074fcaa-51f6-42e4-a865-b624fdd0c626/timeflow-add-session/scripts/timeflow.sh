#!/usr/bin/env bash
# timeflow.sh — call a TIMEFLOW MCP tool from the machine TimeFlow runs on.
# Usage: timeflow.sh <tool_name> ['<json_arguments>']
# Example: timeflow.sh list_projects '{}'
#          timeflow.sh create_manual_session '{"input":{"title":"Work","session_type":"other","project_id":157,"start_time":"2026-07-21T18:00:00","end_time":"2026-07-21T19:00:00"}}'
set -euo pipefail

URL="http://127.0.0.1:47892/mcp"

# Token is read locally, not hardcoded: env TIMEFLOW_TOKEN wins, else ~/.timeflow_token.
TOKEN_FILE="${TIMEFLOW_TOKEN_FILE:-$HOME/.timeflow_token}"
if [ -n "${TIMEFLOW_TOKEN:-}" ]; then
  TOKEN="$TIMEFLOW_TOKEN"
elif [ -f "$TOKEN_FILE" ]; then
  TOKEN=$(tr -d '[:space:]' < "$TOKEN_FILE")
else
  echo "ERROR: TimeFlow token not found. Save it with: printf '%s' '<token>' > $TOKEN_FILE && chmod 600 $TOKEN_FILE" >&2
  exit 1
fi
H_AUTH="Authorization: Bearer ${TOKEN}"
H_CT="Content-Type: application/json"
H_ACC="Accept: application/json, text/event-stream"

if [ $# -lt 1 ]; then
  echo "Usage: $0 <tool_name> ['<json_arguments>']" >&2
  exit 2
fi
TOOL="$1"
ARGS="${2:-\{\}}"

# 1) initialize — capture the Mcp-Session-Id response header.
#    The server occasionally drops a request under quick successive calls, so retry a few times.
SID=""
for attempt in 1 2 3; do
  SID=$(curl -s --max-time 10 -D - -o /dev/null -X POST "$URL" \
    -H "$H_AUTH" -H "$H_CT" -H "$H_ACC" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"timeflow-skill","version":"1.0"}}}' \
    | tr -d '\r' | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}') || true
  [ -n "$SID" ] && break
  sleep 2
done

if [ -z "${SID:-}" ]; then
  echo "ERROR: TimeFlow MCP not reachable at $URL — is TimeFlow running on this machine?" >&2
  exit 1
fi

# 2) initialized notification
curl -s --max-time 10 -o /dev/null -X POST "$URL" \
  -H "$H_AUTH" -H "$H_CT" -H "$H_ACC" -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3) tools/call
curl -s --max-time 30 -X POST "$URL" \
  -H "$H_AUTH" -H "$H_CT" -H "$H_ACC" -H "Mcp-Session-Id: $SID" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"${TOOL}\",\"arguments\":${ARGS}}}"
echo
