---
name: timeflow-add-session
description: Add work sessions (time entries) to the local TIMEFLOW time-tracking app on the user's Mac, with mandatory project verification before writing. Use this skill whenever the user mentions TimeFlow / TIMEFLOW, asks to log or add a work session, "dodaj sesję do TimeFlow", "zaloguj czas", "dopisz czas pracy do projektu", wants past work time recorded against a project, or asks to sync a work period into their time tracker — even if they don't name TimeFlow explicitly but refer to their local time tracking.
---

# TimeFlow: add a work session

TIMEFLOW is a local time-tracking app exposing an MCP server over HTTP on the user's Mac. This skill adds **manual work sessions** to it. The cardinal rule: **never write a session without first verifying the target project against the live project list** — project ids change, projects get frozen/archived, and a wrong guess pollutes the user's billing data.

## Connection

- Endpoint: `http://127.0.0.1:47892/mcp` (JSON-RPC 2.0, MCP streamable HTTP)
- Auth header: `Authorization: Bearer <token>`, where the token lives locally on the user's machine in `~/.timeflow_token` (or env `TIMEFLOW_TOKEN`). Read it with `$(cat ~/.timeflow_token)`; if the file is missing, ask the user for the token from TimeFlow's settings and save it there (`chmod 600`).
- The server only listens on localhost of the user's machine. If your shell runs **on that machine** (Claude Code, Codex CLI, terminal agents), call it directly — easiest via the bundled script:

```bash
scripts/timeflow.sh <tool_name> '<json_arguments>'
# e.g.
scripts/timeflow.sh list_projects '{}'
```

The script performs the MCP handshake (initialize → capture `Mcp-Session-Id` response header → `notifications/initialized`) and then issues a `tools/call`. If you cannot run the script, replicate those steps with curl — see `references/api.md` for the exact requests.

- If your shell is **sandboxed away from the user's machine** (Claude Cowork, ChatGPT with remote execution), you cannot reach 127.0.0.1 directly. Route the same curl commands through whatever host-execution channel you have (on macOS, e.g. an AppleScript/osascript bridge running `do shell script "curl ..."`). If no such channel exists, say so — do not fake success.
- If the server does not respond, TimeFlow is probably not running. Report that and stop; do not retry endlessly.

## Workflow

### 1. Establish what to log

You need: a **project**, a **start/end time** (`YYYY-MM-DDTHH:MM:SS`, local time), a short **title** describing the work, and a **session_type** (`meeting`, `call`, or `other` — default `other` for regular work). If the user gave a duration ("2h this afternoon") rather than exact times, propose concrete times and confirm.

### 2. Verify the project (mandatory — never skip)

Call `list_projects` and match the user's project reference against the results. Matching signals, in order of strength: exact name match → unambiguous substring/number match (e.g. "22_26" → `22_26_Metro_Content`) → the project's `assigned_folder_path` matching files/paths the user mentioned.

Rules:
- Add the session **only** on an unambiguous match to a project that is active (`excluded_at` null, `merged_into` null; prefer non-frozen).
- Two or more plausible candidates, or none → **ask the user**, listing the candidates with ids. Never guess and never create a new project unless explicitly asked.
- Use the numeric `id` from the live response; never a remembered id from a previous conversation.

### 3. Check for duplicates

TimeFlow also tracks app usage automatically, so manual entries can double-count time:

- `list_manual_sessions` with `{"filters":{"dateRange":{"start":"<date>","end":"<date>"},"limit":100}}` — skip if an existing manual session already covers (>50% overlap) the same range for the same project.
- `list_sessions` with the same dateRange (always pass a `limit`) — if auto-tracked sessions already assigned to this project overlap the requested range, tell the user before writing and let them decide.

### 4. Create the session

```bash
scripts/timeflow.sh create_manual_session '{"input":{"title":"<short description>","session_type":"other","project_id":<id>,"start_time":"2026-07-21T18:53:00","end_time":"2026-07-21T19:00:00"}}'
```

The response echoes the created session with its `id`. Report to the user: id, project name, time range, duration. If you created the entry on behalf of an automated sync, include a stable marker in the title (e.g. a source id in parentheses) so future runs can deduplicate.

### 5. Fixing mistakes

Wrong entry? `update_manual_session` / `set_manual_session_time` / `set_manual_session_title` edit it; `delete_manual_session {"id":N}` removes it. Prefer fixing over delete+recreate when only one field is wrong.

## Reference

`references/api.md` — raw curl handshake and the full catalog of TimeFlow MCP tools (projects, clients, auto-session assignment, reports). Read it when the task goes beyond adding a manual session.
