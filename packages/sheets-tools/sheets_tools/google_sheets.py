"""Google Sheets API client (issue #110), mirroring the pattern used by
contacts_tools/google_people.py (People API) and
apple_eventkit_tools/google_calendar.py (Calendar API): a thin subclass of
GoogleOAuthClient exposing spreadsheet-specific methods on top of
self.request().

Unlike GooglePeopleClient/GoogleCalendarClient, this client is not scoped to
"my data for one account" - every method call operates on a caller-supplied
spreadsheet_id. self.account still identifies which authorized Google
account's OAuth token is used to make the call (a single configured account,
same as every other Google-backed tool in this workspace - not a per-call
multi-account client).
"""

from __future__ import annotations

import logging
from urllib.parse import quote, urlencode

from mcp_common.config import load_layered_config
from mcp_common.google_oauth import GoogleOAuthClient, GoogleOAuthError

logger = logging.getLogger(__name__)

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_API = "https://www.googleapis.com/drive/v3"

_ENV_MAP = {"account": "GOOGLE_SHEETS_ACCOUNT"}
_DRIVE_LIST_PAGE_SIZE_MAX = 1000
_VALID_SHARE_ROLES = {"reader", "commenter", "writer"}


class SheetsError(Exception):
    """Raised when a Google Sheets API operation fails."""


def _quote_range(range_: str) -> str:
    """URL-encode an A1-notation range for use in a REST path segment.

    A1 ranges routinely contain characters (spaces, '!', ':', quotes around
    a sheet name) that are not safe unencoded in a URL path, so nothing is
    left unescaped (safe="").
    """
    return quote(range_, safe="")


def _escape_drive_query_value(value: str) -> str:
    """Escape a literal value for embedding in a Drive API `q` string.

    Drive's query grammar requires literal backslashes and single quotes
    inside a quoted string literal to be backslash-escaped (backslash first,
    so an existing backslash isn't re-escaped by the quote substitution) -
    see https://developers.google.com/drive/api/guides/ref-search-terms.
    Without this, a name like "Kim's Budget" would produce an invalid/
    misinterpreted query.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _serialize_spreadsheet(raw: dict) -> dict:
    """Convert a Sheets API Spreadsheet resource into a flat summary dict:
    id, title, url, and one entry per tab (sheet_id, title, index,
    row_count, column_count).
    """
    properties = raw.get("properties", {})
    sheets = []
    for sheet in raw.get("sheets", []) or []:
        sheet_props = sheet.get("properties", {})
        grid = sheet_props.get("gridProperties", {})
        sheets.append(
            {
                "sheet_id": sheet_props.get("sheetId"),
                "title": sheet_props.get("title"),
                "index": sheet_props.get("index"),
                "row_count": grid.get("rowCount"),
                "column_count": grid.get("columnCount"),
            }
        )
    return {
        "spreadsheet_id": raw.get("spreadsheetId"),
        "title": properties.get("title"),
        "url": raw.get("spreadsheetUrl"),
        "sheets": sheets,
    }


class GoogleSheetsClient(GoogleOAuthClient):
    """Google Sheets API v4 client: create/read/write/clear ranges, manage tabs."""

    # NOTE: the drive scope is broader than originally planned - see the
    # "Scope" note on list_spreadsheets/share_spreadsheet below. Drive API's
    # own discovery document (fetched at implementation time from
    # https://www.googleapis.com/discovery/v1/apis/drive/v3/rest) lists
    # permissions.create as accepting only drive or drive.file, not
    # drive.metadata - despite drive.metadata's description ("View and
    # manage metadata of files") sounding like it should cover a
    # permissions write. drive.file was rejected too: it only grants access
    # to files this app created or the user explicitly opened via a picker,
    # which excludes the "list and share a sheet I already have" use case
    # this issue exists for. That leaves the full drive scope as the only
    # option that actually works for both files.list and permissions.create
    # against arbitrary pre-existing spreadsheets.
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    def __init__(self):
        super().__init__(tool_name="sheets-tools", scopes=self.SCOPES)
        cfg = load_layered_config("sheets-tools", _ENV_MAP)
        self.account = cfg.get("account") or self._sole_authorized_account()

    def _sole_authorized_account(self) -> str | None:
        """Account to use when none is configured.

        There is no meaningful hardcoded default here - the account is
        whichever Google account the installing user authorized. When exactly
        one is on file, use it (the overwhelmingly common single-account
        case). With zero or several, leave it unset so request() raises a
        clear "not authorized" error naming the account, rather than silently
        acting as someone else. Override with GOOGLE_SHEETS_ACCOUNT or
        config.json's "account" key.
        """
        accounts = self.list_authorized_accounts()
        return accounts[0] if len(accounts) == 1 else None

    def probe_live(self) -> None:
        """Cheap, read-only call for google_sheets_status's check_live: the
        Sheets API v4 has no per-account listing endpoint (that's Drive
        API, out of scope for this issue - see issue #110's "Out of scope"
        section), so contact_search/calendar's "list my own resources"
        probe pattern doesn't apply here. Instead this requests metadata
        for a deliberately invalid spreadsheet id - Google rejects the id
        with a 400/404 *after* accepting the Authorization header, so
        getting that error back (rather than a 401) already proves the
        token is live. Any other error (403 not-enabled/insufficient-scope/
        quota, 5xx transient outage, or a genuine 401 that GoogleOAuthClient
        didn't manage to mark token_revoked=True) means the token/API call
        is not actually working, so it is re-raised for check_live() to
        classify - only 400/404 is swallowed as proof-of-life.
        """
        try:
            self.request(self.account, "GET", f"{SHEETS_API}/__live_check__")
        except GoogleOAuthError as e:
            if e.status_code not in (400, 404):
                raise

    # ── Spreadsheet-level ──

    def create_spreadsheet(self, title: str, sheet_names: list[str] | None = None) -> dict:
        """Create a new spreadsheet, optionally with named tabs (the default
        single "Sheet1" tab is used when sheet_names is omitted).
        """
        body: dict = {"properties": {"title": title}}
        if sheet_names:
            body["sheets"] = [{"properties": {"title": name}} for name in sheet_names]
        raw = self.request(self.account, "POST", SHEETS_API, body=body)
        return _serialize_spreadsheet(raw)

    def get_spreadsheet(self, spreadsheet_id: str) -> dict:
        """Fetch spreadsheet metadata: title, tabs, and each tab's dimensions."""
        raw = self.request(self.account, "GET", f"{SHEETS_API}/{spreadsheet_id}")
        if not raw or not raw.get("spreadsheetId"):
            raise SheetsError(f"Spreadsheet not found: {spreadsheet_id}")
        return _serialize_spreadsheet(raw)

    # ── Values (range read/write) ──

    def read_range(self, spreadsheet_id: str, range_: str) -> dict:
        """Read a range in A1 notation. Returns {range, values}; values is a
        list of rows (each a list of cell values), possibly empty if the
        range has no data.
        """
        url = f"{SHEETS_API}/{spreadsheet_id}/values/{_quote_range(range_)}"
        raw = self.request(self.account, "GET", url)
        return {"range": raw.get("range", range_), "values": raw.get("values", [])}

    def write_range(self, spreadsheet_id: str, range_: str, values: list[list]) -> dict:
        """Overwrite a range with values (a list of rows, each a list of
        cell values). USER_ENTERED so formulas/dates are interpreted the
        same way as typing them into the Sheets UI, not stored as literal
        strings.
        """
        url = f"{SHEETS_API}/{spreadsheet_id}/values/{_quote_range(range_)}?valueInputOption=USER_ENTERED"
        raw = self.request(self.account, "PUT", url, body={"values": values})
        return {
            "range": raw.get("updatedRange", range_),
            "updated_rows": raw.get("updatedRows"),
            "updated_columns": raw.get("updatedColumns"),
            "updated_cells": raw.get("updatedCells"),
        }

    def append_row(self, spreadsheet_id: str, range_: str, values: list) -> dict:
        """Append a single row (a flat list of cell values) after the last
        populated row in range_. Google resolves the exact insertion point
        server-side (INSERT_ROWS) - it is not necessarily range_'s literal
        end row.
        """
        url = (
            f"{SHEETS_API}/{spreadsheet_id}/values/{_quote_range(range_)}:append"
            "?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
        )
        raw = self.request(self.account, "POST", url, body={"values": [values]})
        updates = raw.get("updates", {})
        return {
            "range": updates.get("updatedRange", range_),
            "updated_rows": updates.get("updatedRows"),
            "updated_cells": updates.get("updatedCells"),
        }

    def clear_range(self, spreadsheet_id: str, range_: str) -> dict:
        """Clear cell contents in a range without deleting the tab itself."""
        url = f"{SHEETS_API}/{spreadsheet_id}/values/{_quote_range(range_)}:clear"
        raw = self.request(self.account, "POST", url, body={})
        return {"range": raw.get("clearedRange", range_), "cleared": True}

    # ── Structural (batchUpdate) ──

    def add_sheet(self, spreadsheet_id: str, sheet_name: str) -> dict:
        """Add a new tab to an existing spreadsheet."""
        body = {"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]}
        raw = self.request(self.account, "POST", f"{SHEETS_API}/{spreadsheet_id}:batchUpdate", body=body)
        replies = raw.get("replies", [])
        props = replies[0].get("addSheet", {}).get("properties", {}) if replies else {}
        return {
            "spreadsheet_id": spreadsheet_id,
            "sheet_id": props.get("sheetId"),
            "title": props.get("title", sheet_name),
            "added": True,
        }

    def delete_rows(self, spreadsheet_id: str, sheet_id: int, start_index: int, end_index: int) -> dict:
        """Delete rows [start_index, end_index) (0-indexed, end exclusive -
        same half-open convention Sheets' deleteDimension request uses) from
        the tab identified by sheet_id (the tab's numeric id, not its name -
        see sheet_info for the mapping).
        """
        body = {
            "requests": [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": start_index,
                            "endIndex": end_index,
                        }
                    }
                }
            ]
        }
        self.request(self.account, "POST", f"{SHEETS_API}/{spreadsheet_id}:batchUpdate", body=body)
        return {
            "spreadsheet_id": spreadsheet_id,
            "sheet_id": sheet_id,
            "start_index": start_index,
            "end_index": end_index,
            "deleted": True,
        }

    def delete_sheet(self, spreadsheet_id: str, sheet_id: int) -> dict:
        """Delete a tab identified by its numeric sheet_id (not its title)."""
        body = {"requests": [{"deleteSheet": {"sheetId": sheet_id}}]}
        self.request(self.account, "POST", f"{SHEETS_API}/{spreadsheet_id}:batchUpdate", body=body)
        return {"spreadsheet_id": spreadsheet_id, "sheet_id": sheet_id, "deleted": True}

    # ── Drive-backed discovery/sharing ──

    def list_spreadsheets(self, name_contains: str | None = None, limit: int = 50) -> list[dict]:
        """List spreadsheets visible to the authorized account via the Drive
        API - the Sheets API v4 has no listing endpoint of its own. Filter
        to spreadsheets whose name contains name_contains (an exact
        substring the caller supplies; Drive's `contains` operator does its
        own tokenized/prefix matching under the hood, not necessarily a
        literal substring match).

        limit is clamped to Drive's per-request pageSize maximum of 1000 -
        pagination beyond one page is not implemented, matching this
        issue's "what sheets do I already have" discovery use case rather
        than a full account-wide export.

        Returns [{spreadsheet_id, title, modified_time}, ...].
        """
        query = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
        if name_contains:
            query += f" and name contains '{_escape_drive_query_value(name_contains)}'"
        params = {
            "q": query,
            "fields": "files(id,name,modifiedTime)",
            "pageSize": min(limit, _DRIVE_LIST_PAGE_SIZE_MAX),
        }
        url = f"{DRIVE_API}/files?{urlencode(params)}"
        raw = self.request(self.account, "GET", url)
        return [
            {
                "spreadsheet_id": f.get("id"),
                "title": f.get("name"),
                "modified_time": f.get("modifiedTime"),
            }
            for f in raw.get("files", []) or []
        ]

    def share_spreadsheet(self, spreadsheet_id: str, email: str, role: str = "writer") -> dict:
        """Grant email access to spreadsheet_id via a Drive permission.

        role must be one of reader/commenter/writer - "owner" is
        deliberately not accepted (ownership transfer needs
        transferOwnership=true plus same-domain constraints, out of scope
        for this tool - see issue #112).

        sendNotificationEmail is deliberately left unset in the request
        body so Google's default (send the notification) applies - the
        motivating use case is sharing with a named person who should know
        they got access, not a silent grant.
        """
        if role not in _VALID_SHARE_ROLES:
            raise SheetsError(f"Invalid role {role!r}: must be one of {sorted(_VALID_SHARE_ROLES)}")
        url = f"{DRIVE_API}/files/{spreadsheet_id}/permissions?fields=id"
        body = {"type": "user", "role": role, "emailAddress": email}
        raw = self.request(self.account, "POST", url, body=body)
        return {
            "spreadsheet_id": spreadsheet_id,
            "email": email,
            "role": role,
            "permission_id": raw.get("id"),
            "shared": True,
        }
