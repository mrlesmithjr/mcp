"""Unit tests for GoogleSheetsClient (issue #110).

Two halves: pure-Python helper tests (range quoting, spreadsheet
serialization against fixture dicts - no client instantiation), and
API-call tests that mock GoogleOAuthClient.request() (no live network, no
OAuth) to exercise the actual HTTP-calling methods.
"""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from mcp_common.google_oauth import GoogleOAuthError

from sheets_tools.google_sheets import (
    DRIVE_API,
    SHEETS_API,
    GoogleSheetsClient,
    SheetsError,
    _escape_drive_query_value,
    _quote_range,
    _serialize_spreadsheet,
)


class TestQuoteRange:
    def test_encodes_bang_and_colon(self):
        assert _quote_range("Sheet1!A1:C10") == "Sheet1%21A1%3AC10"

    def test_encodes_spaces_and_quotes_in_sheet_name(self):
        assert _quote_range("'My Sheet'!A:A") == "%27My%20Sheet%27%21A%3AA"


class TestEscapeDriveQueryValue:
    def test_escapes_single_quote(self):
        assert _escape_drive_query_value("Kim's Budget") == "Kim\\'s Budget"

    def test_escapes_backslash_before_quote_substitution(self):
        # A literal backslash must be escaped first so a pre-existing
        # backslash isn't re-escaped by the quote substitution.
        assert _escape_drive_query_value("back\\slash") == "back\\\\slash"

    def test_plain_value_is_unchanged(self):
        assert _escape_drive_query_value("Budget 2026") == "Budget 2026"


class TestSerializeSpreadsheet:
    def test_maps_properties_and_sheets(self):
        raw = {
            "spreadsheetId": "abc123",
            "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/abc123",
            "properties": {"title": "Budget"},
            "sheets": [
                {
                    "properties": {
                        "sheetId": 0,
                        "title": "Sheet1",
                        "index": 0,
                        "gridProperties": {"rowCount": 1000, "columnCount": 26},
                    }
                }
            ],
        }
        result = _serialize_spreadsheet(raw)

        assert result["spreadsheet_id"] == "abc123"
        assert result["title"] == "Budget"
        assert result["url"] == "https://docs.google.com/spreadsheets/d/abc123"
        assert result["sheets"] == [
            {"sheet_id": 0, "title": "Sheet1", "index": 0, "row_count": 1000, "column_count": 26}
        ]

    def test_no_sheets_returns_empty_list(self):
        raw = {"spreadsheetId": "abc123", "properties": {"title": "Empty"}}
        result = _serialize_spreadsheet(raw)
        assert result["sheets"] == []


# ── API-call tests (mocked request(), no live network/OAuth) ──


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A GoogleSheetsClient with credential/token dirs and config redirected to tmp_path."""
    monkeypatch.setattr("mcp_common.google_oauth.config_dir", lambda name: tmp_path / name)
    (tmp_path / "google").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sheets-tools").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("sheets_tools.google_sheets.load_layered_config", lambda tool_name, env_map: {})

    c = GoogleSheetsClient()
    c._tokens = {c.account: {"access_token": "at", "refresh_token": "rt"}}
    return c


class TestCreateSpreadsheet:
    def test_creates_without_sheet_names(self, client):
        response = {"spreadsheetId": "new1", "properties": {"title": "My Sheet"}, "sheets": []}
        with patch.object(client, "request", return_value=response) as mock_request:
            result = client.create_spreadsheet(title="My Sheet")

        assert result["spreadsheet_id"] == "new1"
        call_args = mock_request.call_args.args
        assert call_args[1] == "POST"
        assert call_args[2] == SHEETS_API
        body = mock_request.call_args.kwargs["body"]
        assert body == {"properties": {"title": "My Sheet"}}

    def test_creates_with_sheet_names(self, client):
        response = {"spreadsheetId": "new1", "properties": {"title": "Budget"}, "sheets": []}
        with patch.object(client, "request", return_value=response) as mock_request:
            client.create_spreadsheet(title="Budget", sheet_names=["Income", "Expenses"])

        body = mock_request.call_args.kwargs["body"]
        assert body["sheets"] == [{"properties": {"title": "Income"}}, {"properties": {"title": "Expenses"}}]


class TestGetSpreadsheet:
    def test_returns_serialized_metadata(self, client):
        response = {"spreadsheetId": "abc", "properties": {"title": "Sheet"}, "sheets": []}
        with patch.object(client, "request", return_value=response):
            result = client.get_spreadsheet("abc")
        assert result["spreadsheet_id"] == "abc"

    def test_missing_spreadsheet_raises(self, client):
        with patch.object(client, "request", return_value={}):
            with pytest.raises(SheetsError, match="Spreadsheet not found"):
                client.get_spreadsheet("missing")


class TestReadRange:
    def test_returns_values(self, client):
        response = {"range": "Sheet1!A1:B2", "values": [["a", "b"], ["c", "d"]]}
        with patch.object(client, "request", return_value=response) as mock_request:
            result = client.read_range("abc", "Sheet1!A1:B2")

        assert result["values"] == [["a", "b"], ["c", "d"]]
        call_args = mock_request.call_args.args
        assert call_args[1] == "GET"
        assert "Sheet1%21A1%3AB2" in call_args[2]

    def test_empty_range_returns_empty_values(self, client):
        with patch.object(client, "request", return_value={"range": "Sheet1!A1:A1"}):
            result = client.read_range("abc", "Sheet1!A1:A1")
        assert result["values"] == []


class TestWriteRange:
    def test_overwrites_range(self, client):
        response = {"updatedRange": "Sheet1!A1:B1", "updatedRows": 1, "updatedColumns": 2, "updatedCells": 2}
        with patch.object(client, "request", return_value=response) as mock_request:
            result = client.write_range("abc", "Sheet1!A1:B1", [["x", "y"]])

        assert result["updated_cells"] == 2
        call_args = mock_request.call_args.args
        assert call_args[1] == "PUT"
        assert "valueInputOption=USER_ENTERED" in call_args[2]
        body = mock_request.call_args.kwargs["body"]
        assert body == {"values": [["x", "y"]]}


class TestAppendRow:
    def test_wraps_single_row_and_appends(self, client):
        response = {"updates": {"updatedRange": "Sheet1!A2:C2", "updatedRows": 1, "updatedCells": 3}}
        with patch.object(client, "request", return_value=response) as mock_request:
            result = client.append_row("abc", "Sheet1!A1:C1", ["2026-07-10", "Rent", 1500])

        assert result["range"] == "Sheet1!A2:C2"
        call_args = mock_request.call_args.args
        assert call_args[1] == "POST"
        assert ":append" in call_args[2]
        assert "insertDataOption=INSERT_ROWS" in call_args[2]
        body = mock_request.call_args.kwargs["body"]
        assert body == {"values": [["2026-07-10", "Rent", 1500]]}


class TestClearRange:
    def test_clears_and_reports_range(self, client):
        response = {"clearedRange": "Sheet1!A2:C100"}
        with patch.object(client, "request", return_value=response) as mock_request:
            result = client.clear_range("abc", "Sheet1!A2:C100")

        assert result == {"range": "Sheet1!A2:C100", "cleared": True}
        call_args = mock_request.call_args.args
        assert call_args[1] == "POST"
        assert ":clear" in call_args[2]


class TestAddSheet:
    def test_adds_and_returns_new_sheet_id(self, client):
        response = {"replies": [{"addSheet": {"properties": {"sheetId": 42, "title": "Q3"}}}]}
        with patch.object(client, "request", return_value=response) as mock_request:
            result = client.add_sheet("abc", "Q3")

        assert result == {"spreadsheet_id": "abc", "sheet_id": 42, "title": "Q3", "added": True}
        call_args = mock_request.call_args.args
        assert ":batchUpdate" in call_args[2]
        body = mock_request.call_args.kwargs["body"]
        assert body == {"requests": [{"addSheet": {"properties": {"title": "Q3"}}}]}


class TestDeleteRows:
    def test_sends_half_open_range(self, client):
        with patch.object(client, "request", return_value={}) as mock_request:
            result = client.delete_rows("abc", sheet_id=0, start_index=1, end_index=5)

        assert result == {
            "spreadsheet_id": "abc",
            "sheet_id": 0,
            "start_index": 1,
            "end_index": 5,
            "deleted": True,
        }
        body = mock_request.call_args.kwargs["body"]
        assert body["requests"][0]["deleteDimension"]["range"] == {
            "sheetId": 0,
            "dimension": "ROWS",
            "startIndex": 1,
            "endIndex": 5,
        }


class TestDeleteSheet:
    def test_deletes_by_sheet_id(self, client):
        with patch.object(client, "request", return_value={}) as mock_request:
            result = client.delete_sheet("abc", sheet_id=99)

        assert result == {"spreadsheet_id": "abc", "sheet_id": 99, "deleted": True}
        body = mock_request.call_args.kwargs["body"]
        assert body == {"requests": [{"deleteSheet": {"sheetId": 99}}]}


class TestListSpreadsheets:
    def test_returns_mapped_list(self, client):
        response = {
            "files": [
                {"id": "abc123", "name": "Budget", "modifiedTime": "2026-07-01T00:00:00Z"},
                {"id": "def456", "name": "Lawn Log", "modifiedTime": "2026-06-15T00:00:00Z"},
            ]
        }
        with patch.object(client, "request", return_value=response) as mock_request:
            result = client.list_spreadsheets()

        assert result == [
            {"spreadsheet_id": "abc123", "title": "Budget", "modified_time": "2026-07-01T00:00:00Z"},
            {"spreadsheet_id": "def456", "title": "Lawn Log", "modified_time": "2026-06-15T00:00:00Z"},
        ]
        call_args = mock_request.call_args.args
        assert call_args[1] == "GET"
        assert call_args[2].startswith(f"{DRIVE_API}/files?")

        parsed = parse_qs(urlparse(call_args[2]).query)
        assert parsed["q"][0] == "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
        assert parsed["fields"][0] == "files(id,name,modifiedTime)"
        assert parsed["pageSize"][0] == "50"

    def test_no_files_returns_empty_list(self, client):
        with patch.object(client, "request", return_value={}):
            result = client.list_spreadsheets()
        assert result == []

    def test_name_contains_appends_escaped_clause(self, client):
        with patch.object(client, "request", return_value={"files": []}) as mock_request:
            client.list_spreadsheets(name_contains="Kim's Budget")

        call_args = mock_request.call_args.args
        parsed = parse_qs(urlparse(call_args[2]).query)
        assert parsed["q"][0] == (
            "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false and name contains 'Kim\\'s Budget'"
        )

    def test_limit_clamped_to_drive_page_size_max(self, client):
        with patch.object(client, "request", return_value={"files": []}) as mock_request:
            client.list_spreadsheets(limit=5000)

        call_args = mock_request.call_args.args
        parsed = parse_qs(urlparse(call_args[2]).query)
        assert parsed["pageSize"][0] == "1000"


class TestShareSpreadsheet:
    def test_shares_with_default_writer_role(self, client):
        response = {"id": "perm1"}
        with patch.object(client, "request", return_value=response) as mock_request:
            result = client.share_spreadsheet("abc123", "kim@example.com")

        assert result == {
            "spreadsheet_id": "abc123",
            "email": "kim@example.com",
            "role": "writer",
            "permission_id": "perm1",
            "shared": True,
        }
        call_args = mock_request.call_args.args
        assert call_args[1] == "POST"
        assert call_args[2] == f"{DRIVE_API}/files/abc123/permissions?fields=id"
        body = mock_request.call_args.kwargs["body"]
        assert body == {"type": "user", "role": "writer", "emailAddress": "kim@example.com"}
        # Regression: sendNotificationEmail must stay absent so Google's
        # default (send the notification) applies - do not silently start
        # suppressing it.
        assert "sendNotificationEmail" not in body

    def test_shares_with_explicit_role(self, client):
        with patch.object(client, "request", return_value={"id": "perm2"}) as mock_request:
            client.share_spreadsheet("abc123", "kim@example.com", role="reader")

        body = mock_request.call_args.kwargs["body"]
        assert body["role"] == "reader"

    def test_invalid_role_raises_without_calling_request(self, client):
        with patch.object(client, "request") as mock_request:
            with pytest.raises(SheetsError, match="Invalid role"):
                client.share_spreadsheet("abc123", "kim@example.com", role="owner")
        mock_request.assert_not_called()


class TestProbeLive:
    def test_no_error_on_success(self, client):
        with patch.object(client, "request", return_value={}) as mock_request:
            client.probe_live()
        call_args = mock_request.call_args.args
        assert call_args[1] == "GET"
        assert "__live_check__" in call_args[2]

    def test_swallows_non_revoked_error(self, client):
        """A 400/404 for the bogus id proves the token authenticated fine -
        probe_live must not propagate this as a failure.
        """
        with patch.object(
            client, "request", side_effect=GoogleOAuthError("bad id", status_code=400, token_revoked=False)
        ):
            client.probe_live()  # must not raise

    def test_swallows_404_error(self, client):
        """A 404 for the bogus id is the other expected proof-of-life
        response - also must not propagate.
        """
        with patch.object(
            client, "request", side_effect=GoogleOAuthError("not found", status_code=404, token_revoked=False)
        ):
            client.probe_live()  # must not raise

    def test_reraises_token_revoked_error(self, client):
        with patch.object(client, "request", side_effect=GoogleOAuthError("refresh failed", token_revoked=True)):
            with pytest.raises(GoogleOAuthError):
                client.probe_live()

    def test_reraises_403_error(self, client):
        """403 (Sheets API not enabled, insufficient scope, or quota
        exceeded) is a real failure and must not be swallowed just because
        it isn't the 401/token_revoked case.
        """
        with patch.object(
            client, "request", side_effect=GoogleOAuthError("forbidden", status_code=403, token_revoked=False)
        ):
            with pytest.raises(GoogleOAuthError):
                client.probe_live()

    def test_reraises_401_without_token_revoked(self, client):
        """A repeat 401 after a refresh attempt that GoogleOAuthClient
        didn't mark token_revoked=True must still surface as a failure,
        not be swallowed as proof-of-life.
        """
        with patch.object(
            client, "request", side_effect=GoogleOAuthError("unauthorized", status_code=401, token_revoked=False)
        ):
            with pytest.raises(GoogleOAuthError):
                client.probe_live()

    def test_reraises_500_error(self, client):
        """A transient 5xx outage is a real failure and must not be
        swallowed.
        """
        with patch.object(
            client, "request", side_effect=GoogleOAuthError("server error", status_code=500, token_revoked=False)
        ):
            with pytest.raises(GoogleOAuthError):
                client.probe_live()
