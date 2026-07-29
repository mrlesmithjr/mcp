# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Status**: ACTIVE
**Last Updated**: 2026-07-29

## Overview

Google Sheets MCP server for Claude Code (issue #110). All tools go through the Google Sheets API v4 (`GoogleSheetsClient`), following the same OAuth/plugin pattern as `contacts-tools` (People API) and `apple-eventkit-tools` (Calendar API) in this workspace. Requires OAuth (see `google_sheets_authorize`/`google_sheets_status` below) with credentials shared at `~/.config/google/credentials.json` across all Google-using packages.

## Setup

```bash
# Install
pip install -e .

# Register MCP server (user scope)
claude mcp add -s user sheets -- sheets-mcp
```

Copy the shared Google OAuth client credentials to `~/.config/google/credentials.json` (same file used by mail-tools' Gmail integration, apple-eventkit-tools' Calendar integration, and contacts-tools' People integration - one GCP app registration, Sheets API enabled on the same project), then run `google_sheets_authorize` once.

**Before the first authorize**, the Sheets API and the Drive API must both be enabled on that GCP project - neither is on by default even though the project already has Gmail/Calendar/People APIs enabled. This is a one-time Google Cloud requirement, not something the code can do for you: enable them at `console.cloud.google.com/apis/library/sheets.googleapis.com` and `console.cloud.google.com/apis/library/drive.googleapis.com`, or `gcloud services enable sheets.googleapis.com drive.googleapis.com` if using the CLI. Skipping this step surfaces as a 403 error on the first live API call, not at authorize time. The Drive API backs `sheet_list`/`sheet_share` only - every other tool here only ever calls the Sheets API.

**Existing users** (authorized before `sheet_list`/`sheet_share` were added): re-run `google_sheets_authorize` once to pick up the widened `drive` scope (see Key Patterns below). A pre-existing spreadsheets-only token 403s on `sheet_list`/`sheet_share` until re-authorized.

**Claude Desktop** is a separate registration path - it has no plugin/marketplace support and does not read Claude Code's MCP config, so it needs its own entry in `claude_desktop_config.json` pointing at an absolute path to `sheets-mcp` (there is no `~/.local/bin` symlink: the plugin installer only symlinks console scripts that do not end in `-mcp`, and `sheets-mcp` is this package's only script). Authorization is shared, not per-client - the token at `~/.config/sheets-tools/google_tokens.json` is whatever was last authorized from any client. Full runbook, including the empty-environment verification handshake and a troubleshooting table, is in `README.md` under Configuration -> Claude Desktop.

## Architecture

```
sheets_tools/
├── __init__.py
├── __main__.py
├── google_sheets.py  # GoogleSheetsClient(GoogleOAuthClient) - Google Sheets API v4
└── mcp_server.py     # FastMCP server (JSON output)
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `sheet_create(title, sheet_names?)` | Create a new spreadsheet, optionally with named tabs |
| `sheet_info(spreadsheet_id)` | Metadata: title, tabs, and each tab's dimensions. `title`/`sheets[].title` are wrapped with session security markers (issue #118) |
| `sheet_read_range(spreadsheet_id, range)` | Read a range in A1 notation. `values` (the whole 2D cell-content matrix) and `range` (Google's resolved range echo) are wrapped with session security markers (issue #118) |
| `sheet_write_range(spreadsheet_id, range, values)` | Overwrite a range. `range` is wrapped with session security markers (issue #118) |
| `sheet_append_row(spreadsheet_id, range, values)` | Append a row after the last populated row. `range` is wrapped with session security markers (issue #118) |
| `sheet_clear_range(spreadsheet_id, range)` | Clear cell contents, keep the tab. `range` is wrapped with session security markers (issue #118) |
| `sheet_add_sheet(spreadsheet_id, sheet_name)` | Add a new tab |
| `sheet_delete_rows(spreadsheet_id, sheet_id, start_index, end_index)` | Delete a range of rows |
| `sheet_delete_sheet(spreadsheet_id, sheet_id)` | Delete a tab |
| `sheet_list(name_contains?, limit?)` | List spreadsheets visible to the account (Drive API), optionally filtered by name. Each result's `title` is wrapped with session security markers (issue #118) |
| `sheet_share(spreadsheet_id, email, role?)` | Share a spreadsheet with another Google account (role: reader/commenter/writer, default writer) |
| `google_sheets_authorize(account?)` | Authorize the Google account for Sheets API access |
| `google_sheets_status(verify?)` | Show Sheets API authorization status |

### Prompt Injection Guarding (issue #118)

`sheet_info`, `sheet_read_range`, `sheet_write_range`, `sheet_append_row`, `sheet_clear_range`, and `sheet_list` return raw spreadsheet-derived content (spreadsheet/tab titles, cell values, resolved range echoes) into the model's context alongside this server's write-capable tools (`sheet_write_range`, `sheet_append_row`, `sheet_share`, `sheet_add_sheet`, `sheet_clear_range`, `sheet_delete_rows`, `sheet_delete_sheet`) - a spreadsheet shared with this account by someone else can carry an attacker-controlled title, tab name, or cell value, making this the same vulnerability class closed for mail-tools in issue #115. `mcp_server.py` uses the `prompt-security-utils` library the same way: `generate_markers()` runs once at module load, `security_instructions()` folds the resulting session markers into the `FastMCP(instructions=...)` string (appended after, not replacing, the pre-existing "Read sheet_info before writing..." guidance), and `_wrap_untrusted_field()`/`_wrap_sheet_titles()`/`_wrap_values()` wrap fields at the tool-return boundary before `json.dumps`. `_SECURITY_CONFIG` is `SecurityConfig(semantic_enabled=False, llm_screen_enabled=False)`, same rationale and same caveat as mail-tools: this avoids the fastembed model *download* at runtime, not the ~68MB `fastembed`/`onnxruntime` install weight `prompt-security-utils` pulls in unconditionally on every SessionStart venv rebuild.

**`values` wrapping granularity (issue #118's key design decision):** `sheet_read_range`'s `values` is a 2D array of arbitrary cell content with no fixed field names, structurally unlike mail-tools' handful of named string fields (subject/from/content). Per-cell `wrap_field()` was rejected: a 20x50 range is 1000 cells, and wrapping each individually would multiply that into 1000 separate wrapped objects (each carrying its own marker/warning/source_id overhead) for no real security benefit over wrapping the same content once. Instead `_wrap_values()` JSON-serializes the whole matrix into a single string and wraps it as one unit via `wrap_external_data()` (functionally equivalent to `wrap_field()`, but named/documented for "data read back from storage" - see the library's README), so the response carries exactly one wrapped object regardless of range size. The tradeoff: a caller must `json.loads()` the wrapped object's `data` string to get the `[[cell, cell, ...], ...]` array back, one extra parse step versus a plain array - acceptable since the value is only ever consumed by the LLM reading the response, not by other code in this repo.

**`range` wrapping (issue #118 follow-up, code review finding):** the `range` field echoed back by `sheet_read_range`, `sheet_write_range`, `sheet_append_row`, and `sheet_clear_range` is Google's *resolved* range, not a plain passthrough of the caller's argument. When a caller's A1 range omits the sheet-name segment (legal syntax, e.g. `"A1:C10"` instead of `"Sheet1!A1:C10"`, common for single-tab spreadsheets), Google resolves it server-side against the default/first tab and returns the range WITH that tab's actual title embedded (e.g. `"'attacker-controlled tab title'!A1:C10"`) - a second, independent leak path for the same attacker-controlled-title content `sheet_info`'s `sheets[].title` already guards. All four tools now wrap `range` via `_wrap_untrusted_field()`, the same single-string mechanism used for `title`, before an earlier version of this fix treated this as an accepted low-probability gap on the theory that real callers always include an explicit tab name - code review flagged that this can't be assumed for a shared spreadsheet whose caller doesn't control the range argument's exact form.

**Full sweep result (all 13 tools checked, per issue #118's "avoid the mail-tools four-round churn" lesson):**
- **Wrapped**: `sheet_info` (`title`, `sheets[].title`), `sheet_read_range` (`range`, `values` whole-blob), `sheet_write_range`/`sheet_append_row`/`sheet_clear_range` (`range`), `sheet_list` (`spreadsheets[].title`).
- **Not applicable - caller-supplied, not existing spreadsheet content**: `sheet_create`'s `title`/`sheets[].title` are the caller's own `title`/`sheet_names` arguments echoed back for a spreadsheet that did not exist before this call - there is no pre-existing content to be attacker-controlled. `sheet_add_sheet`'s `title` is likewise the caller's own `sheet_name` argument for a tab just created, echoed back by Google's `addSheet` reply.
- **Not applicable - no free-text spreadsheet content in the response**: `sheet_write_range`/`sheet_append_row`/`sheet_clear_range`'s remaining fields (`updated_rows`/`updated_columns`/`updated_cells`/`cleared` are booleans/integers only), `sheet_delete_rows`/`sheet_delete_sheet` (only echo the caller's own `spreadsheet_id`/`sheet_id`/indices), `sheet_share` (`permission_id` is a Google-generated opaque id, `email`/`role` are caller-supplied), `google_sheets_authorize`/`google_sheets_status` (local OAuth/config state, never spreadsheet content).

## Key Patterns

- `GoogleSheetsClient` only ever talks to one configured account (defaults to the sole authorized account, overridable via `~/.config/sheets-tools/config.json` or `GOOGLE_SHEETS_ACCOUNT` env var) - not a per-call multi-account client, same as `GooglePeopleClient`/`GoogleCalendarClient`. Every tool still operates on a caller-supplied `spreadsheet_id`, unlike Contacts/Calendar where the account itself owns the data.
- `sheet_id` (a tab's numeric id, used by `sheet_delete_rows`/`sheet_delete_sheet`) is distinct from `spreadsheet_id` (the whole document) and from a tab's title. `sheet_info` returns the `sheet_id` -> title mapping - always call it before a rows/sheet-delete operation on a spreadsheet you have not seen (enforced via MCP instructions).
- Ranges are URL-encoded via `_quote_range()` (`safe=""`) before being placed in the REST path - A1 notation routinely contains characters (`!`, `:`, spaces, quoted sheet names) that are unsafe unescaped in a URL path segment.
- `sheet_write_range`/`sheet_append_row` use `valueInputOption=USER_ENTERED` so formulas and dates are interpreted the same way as typing them into the Sheets UI, not stored as literal strings.
- `sheet_append_row` takes a single flat row (`values: list`), not a list of rows - the client wraps it as `{"values": [values]}` before the API call. `sheet_write_range` takes a list of rows (`values: list[list]`) since it can overwrite a multi-row range.
- `sheet_list`/`sheet_share` (issue #112) are Drive-backed, not Sheets-backed: the Sheets API v4 has no listing or sharing endpoint, so both go through `https://www.googleapis.com/drive/v3/...` instead of `SHEETS_API`. This is why `GoogleSheetsClient.SCOPES` includes `https://www.googleapis.com/auth/drive` on top of `spreadsheets`. That scope is broader than originally planned: Drive's own discovery document (`https://www.googleapis.com/discovery/v1/apis/drive/v3/rest`) lists `permissions.create` as accepting only `drive` or `drive.file`, not `drive.metadata` (despite `drive.metadata`'s description sounding like it should cover a permissions write) - and `drive.file` was rejected too, since it only grants access to files this app created or the user explicitly opened via a picker, which excludes "list and share a sheet I already have". The full `drive` scope is genuinely the least-broad option that works for both `files.list` and `permissions.create` against arbitrary pre-existing spreadsheets. `list_spreadsheets`/`share_spreadsheet` in `google_sheets.py` carry this reasoning in a comment - do not narrow the scope back to `drive.metadata` without re-checking the discovery doc.
- `list_spreadsheets`'s Drive `q` query string escapes `name_contains` via `_escape_drive_query_value` (backslash first, then single quote) before interpolating it - Drive's query grammar requires literal `\` and `'` inside a quoted string literal to be backslash-escaped. Skipping this breaks (or, worse, silently misinterprets) a query for a name containing an apostrophe, e.g. "Kim's Budget".
- `google_sheets_status`'s live-token check (`GoogleSheetsClient.probe_live`) cannot use a cheap "list my own resources" GET the way `contact_search`/Calendar's status check does - the Sheets API v4 has no per-account listing endpoint (that's Drive API). Instead it requests metadata for a deliberately invalid spreadsheet id: Google rejects the id with a 400/404 *after* accepting the Authorization header, so getting that error back already proves the token is live, and only those two status codes are swallowed. Everything else - a genuine 401-refresh failure (`token_revoked=True`), a 401 without `token_revoked` set, a 403 (API not enabled, insufficient scope, or quota), or a 5xx transient outage - is re-raised for `check_live()` to classify as dead, so a broken integration cannot be reported as live (issue #110 review fix). Do not swap this probe for a `sheet_create` call to check liveness, that would create a real spreadsheet on every status check.
- **Known gap (accepted, issue #112):** `probe_live()` only exercises the `spreadsheets` scope, not `drive`. This means `google_sheets_status(verify=True)` can report `live: true` for a token whose Drive access is actually stale (e.g. a pre-widened-scope token that was never re-authorized) - the Sheets-only probe passes, but `sheet_list`/`sheet_share` will still 403. A stale-Drive-scope 403 is caught and returned cleanly as `{"error": "Google API error 403: ..."}` by the tools' existing catch-all, so it fails safely, just not with an early warning from `google_sheets_status`. Extending `probe_live()` to also check Drive liveness is a deliberate follow-up, not done here.
- All tools return structured JSON; errors are returned as `{"error": "..."}` rather than raising
