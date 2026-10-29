# TIMEFLOW — adding work sessions (portable agent instructions)

> Self-contained version for agents without skill support: paste into `AGENTS.md` (Codex),
> ChatGPT custom instructions / project instructions, or any agent's system context.
> Requires the ability to run shell commands (curl) on the machine where TimeFlow runs.

You can log work time into TIMEFLOW, a local time-tracking app on the user's Mac.
Follow this whenever the user asks to add/log a work session, "dodaj sesję do TimeFlow",
"zaloguj czas", or wants past work recorded against a project.

## Connection

TimeFlow exposes an MCP server (JSON-RPC 2.0 over HTTP) at `http://127.0.0.1:47892/mcp`.
The auth token is stored locally on the user's machine in `~/.timeflow_token`.
Every request needs these headers:

```
Authorization: Bearer <contents of ~/.timeflow_token>
Content-Type: application/json
Accept: application/json, text/event-stream
```

If `~/.timeflow_token` is missing, ask the user for the token (TimeFlow settings) and save it:
`printf '%s' '<token>' > ~/.timeflow_token && chmod 600 ~/.timeflow_token`.

Per "conversation" with the server, do the handshake once:

```bash
URL="http://127.0.0.1:47892/mcp"
AUTH="Authorization: Bearer $(cat ~/.timeflow_token)"
CT='Content-Type: application/json'; ACC='Accept: application/json, text/event-stream'

# 1) initialize — the session id is in the RESPONSE HEADER Mcp-Session-Id (hence curl -D -)
SID=$(curl -s -D - -o /dev/null -X POST "$URL" -H "$AUTH" -H "$CT" -H "$ACC" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"agent","version":"1.0"}}}' \
  | tr -d '\r' | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}')

# 2) initialized notification
curl -s -o /dev/null -X POST "$URL" -H "$AUTH" -H "$CT" -H "$ACC" -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'
```

Then call tools with:

```bash
curl -s -X POST "$URL" -H "$AUTH" -H "$CT" -H "$ACC" -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"<TOOL>","arguments":<ARGS_JSON>}}'
```

Tool results arrive as `result.content[0].text` — a JSON string to parse.
If the server doesn't respond, TimeFlow isn't running: report that and stop.

## Workflow — in this order

1. **Establish what to log.** Project, start/end times (`YYYY-MM-DDTHH:MM:SS`, local),
   a short title, and a session_type (`meeting`, `call`, or `other`; default `other`).
   If the user gave only a duration, propose concrete times and confirm.

2. **Verify the project — mandatory, never skip.** Call `list_projects` (arguments `{}`)
   and match the user's reference against live results: exact name → unambiguous
   substring/number (e.g. "22_26" → `22_26_Metro_Content`) → `assigned_folder_path`
   matching paths the user mentioned. Only proceed on an unambiguous match to an active
   project (`excluded_at` null, `merged_into` null; prefer non-frozen). Ambiguous or no
   match → ask the user, listing candidates with ids. Use the numeric `id` from the live
   response — never a remembered one. Never create a new project unless explicitly asked.

3. **Check duplicates.** `list_manual_sessions` with
   `{"filters":{"dateRange":{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"},"limit":100}}` —
   skip if an existing manual entry overlaps >50% of the range for the same project.
   TimeFlow also auto-tracks app usage: `list_sessions` (same dateRange, always include
   `"limit":100`) — if auto-tracked time on this project already overlaps the range,
   warn the user before writing.

4. **Create.** Tool `create_manual_session`, arguments:
   ```json
   {"input":{"title":"Short description","session_type":"other","project_id":157,
             "start_time":"2026-07-21T18:53:00","end_time":"2026-07-21T19:00:00"}}
   ```
   The response echoes the created entry with its `id`. Report id, project, range,
   duration to the user.

5. **Fix mistakes** with `set_manual_session_time {"id","start_time","end_time"}`,
   `set_manual_session_title {"id","title"}`, `update_manual_session`, or
   `delete_manual_session {"id"}`. Prefer editing over delete+recreate.

## Other useful tools

`get_manual_session {"id"}` · `assign_session_to_project {"session_id","project_id"}`
(for auto-tracked sessions) · `get_project {"id"}` · `list_clients` ·
`get_clients_summary {"date_range":{"start","end"}}` · `get_project_estimates`.
