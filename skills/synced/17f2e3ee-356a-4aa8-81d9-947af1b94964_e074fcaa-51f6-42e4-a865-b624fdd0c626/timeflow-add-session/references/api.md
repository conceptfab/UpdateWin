# TimeFlow MCP — raw protocol and tool catalog

## Handshake with plain curl

```bash
URL="http://127.0.0.1:47892/mcp"
AUTH="Authorization: Bearer $(cat ~/.timeflow_token)"
CT='Content-Type: application/json'
ACC='Accept: application/json, text/event-stream'

# 1. initialize — read Mcp-Session-Id from RESPONSE HEADERS (curl -D -)
curl -s -D - -X POST "$URL" -H "$AUTH" -H "$CT" -H "$ACC" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"agent","version":"1.0"}}}'

# 2. initialized notification (include Mcp-Session-Id header from step 1)
curl -s -X POST "$URL" -H "$AUTH" -H "$CT" -H "$ACC" -H "Mcp-Session-Id: $SID" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3. any tool call
curl -s -X POST "$URL" -H "$AUTH" -H "$CT" -H "$ACC" -H "Mcp-Session-Id: $SID" -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_projects","arguments":{}}}'
```

Tool results come back as `result.content[0].text` containing JSON — parse that inner string.

Dates are `YYYY-MM-DD`; timestamps are `YYYY-MM-DDTHH:MM[:SS]` local time. Always pass a `limit` to `list_sessions` (e.g. 100).

## Tool catalog

### Manual sessions (the core of this skill)
| Tool | Arguments | Notes |
|---|---|---|
| `create_manual_session` | `{"input":{"title","session_type","project_id","start_time","end_time","app_id"?}}` | session_type: `meeting`, `call`, `other` |
| `list_manual_sessions` | `{"filters":{"dateRange"?,"projectId"?,"limit"?,"offset"?}}` | dedupe check before creating |
| `get_manual_session` | `{"id"}` | |
| `update_manual_session` | `{"id","input":{...full state...}}` | title/session_type/project_id/start/end required |
| `set_manual_session_time` | `{"id","start_time","end_time"}` | duration/date recalculated |
| `set_manual_session_title` | `{"id","title"}` | |
| `set_manual_session_type` | `{"id","session_type"}` | |
| `delete_manual_session` / `delete_manual_sessions` | `{"id"}` / `{"ids":[...]}` | destructive |

### Projects
| Tool | Arguments | Notes |
|---|---|---|
| `list_projects` | `{"date_range"?}` | active projects + stats; fields: `id`, `name`, `assigned_folder_path`, `frozen_at`, `excluded_at`, `merged_into`, `total_seconds` |
| `get_project` / `get_project_extra_info` | `{"id"}` / `{"id","date_range"}` | |
| `create_project` | `{"name","color","assigned_folder_path"?}` | color hex, e.g. `#38bdf8` |
| `set_project_status` | `{"project_id","status"}` | active / frozen / excluded / archived |
| `freeze_project`, `unfreeze_project`, `exclude_project`, `restore_project`, `delete_project` | `{"id"}` | delete is destructive |
| `merge_project` / `unmerge_project` | `{"source_id","target_id"}` / `{"id"}` | |
| `update_project_color` | `{"id","color"}` | |
| `update_project_hourly_rate` | `{"project_id","rate"}` | rate null clears |
| `list_excluded_projects`, `list_merged_projects`, `list_projects_with_client` | `{}` | |

### Auto-tracked sessions
| Tool | Arguments | Notes |
|---|---|---|
| `list_sessions` | `{"filters":{"dateRange"?,"projectId"?,"appId"?,"unassigned"?,"minDuration"?,"limit","offset"?}}` | app-usage sessions TimeFlow recorded itself |
| `assign_session_to_project` | `{"session_id","project_id","source"?}` | project_id null unassigns |
| `update_session_comment` | `{"session_id","comment"}` | comment null clears |
| `assign_app_to_project` | `{"app_id","project_id"}` | future sessions of that app inherit the project |

### Clients & reporting
`list_clients`, `create_client`, `update_client`, `set_project_client`, `get_clients_summary {"date_range"}`, `get_project_estimates {"date_range"}`.

### Project folders
`list_project_folders`, `add_project_folder {"path"}`, `remove_project_folder {"path"}`, `update_project_folder_meta`, `list_folder_project_candidates`, `create_project_from_folder {"folder_path"}`.
