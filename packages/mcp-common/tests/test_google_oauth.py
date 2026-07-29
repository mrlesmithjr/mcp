"""Unit tests for GoogleOAuthClient (issues #59/#60's shared OAuth base class).

Pure-Python tests with urlopen/HTTPServer monkeypatched - no real OAuth
tokens, no network calls, mirroring mail-tools' test_gmail.py structurally.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from mcp_common.google_oauth import GoogleOAuthClient, GoogleOAuthError


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A GoogleOAuthClient with credential/token dirs redirected to tmp_path."""
    monkeypatch.setattr("mcp_common.google_oauth.config_dir", lambda name: tmp_path / name)
    (tmp_path / "google").mkdir(parents=True, exist_ok=True)
    (tmp_path / "test-tool").mkdir(parents=True, exist_ok=True)
    return GoogleOAuthClient(tool_name="test-tool", scopes=["https://example.com/scope"])


class TestAvailability:
    def test_is_available_false_without_credentials_file(self, client):
        assert client.is_available() is False

    def test_is_available_true_with_credentials_file(self, client):
        client.credentials_path.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}))
        assert client.is_available() is True

    def test_is_authorized_requires_refresh_token(self, client):
        client._tokens = {"a@example.com": {"access_token": "at"}}
        assert client.is_authorized("a@example.com") is False

        client._tokens = {"a@example.com": {"access_token": "at", "refresh_token": "rt"}}
        assert client.is_authorized("a@example.com") is True

    def test_list_authorized_accounts_filters_to_refresh_token_present(self, client):
        client._tokens = {
            "a@example.com": {"refresh_token": "rt"},
            "b@example.com": {"access_token": "at"},
        }
        assert client.list_authorized_accounts() == ["a@example.com"]


class TestRequest:
    def test_missing_access_token_with_refresh_token_present_is_token_revoked(self, client):
        client._tokens = {"a@example.com": {"refresh_token": "rt-only"}}

        with pytest.raises(GoogleOAuthError) as exc_info:
            client.request("a@example.com", "GET", "https://example.com/x")

        assert exc_info.value.token_revoked is True

    def test_missing_access_token_with_no_refresh_token_is_not_token_revoked(self, client):
        client._tokens = {}

        with pytest.raises(GoogleOAuthError) as exc_info:
            client.request("a@example.com", "GET", "https://example.com/x")

        assert exc_info.value.token_revoked is False

    def test_successful_request_returns_parsed_json(self, client):
        client._tokens = {"a@example.com": {"access_token": "at", "refresh_token": "rt"}}

        class FakeResp:
            status = 200

            def read(self):
                return b'{"ok": true}'

        with patch("mcp_common.google_oauth.urlopen", return_value=FakeResp()):
            result = client.request("a@example.com", "GET", "https://example.com/x")

        assert result == {"ok": True}

    def test_204_returns_empty_dict(self, client):
        client._tokens = {"a@example.com": {"access_token": "at", "refresh_token": "rt"}}

        class FakeResp:
            status = 204

            def read(self):
                return b""

        with patch("mcp_common.google_oauth.urlopen", return_value=FakeResp()):
            result = client.request("a@example.com", "GET", "https://example.com/x")

        assert result == {}

    def test_401_triggers_one_refresh_retry(self, client):
        client._tokens = {"a@example.com": {"access_token": "stale", "refresh_token": "rt"}}
        client._credentials = {"client_id": "cid", "client_secret": "sec"}

        calls = {"n": 0}

        def fake_urlopen(req):
            calls["n"] += 1
            if calls["n"] == 1:
                raise HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=io.BytesIO(b"{}"))

            class FakeResp:
                status = 200

                def read(self):
                    return b'{"ok": true}'

            return FakeResp()

        with (
            patch("mcp_common.google_oauth.urlopen", side_effect=fake_urlopen),
            patch.object(client, "_refresh_token", return_value="fresh-token") as mock_refresh,
        ):
            result = client.request("a@example.com", "GET", "https://example.com/x")

        mock_refresh.assert_called_once_with("a@example.com")
        assert result == {"ok": True}

    def test_non_401_error_raises_with_status_code(self, client):
        client._tokens = {"a@example.com": {"access_token": "at", "refresh_token": "rt"}}

        def fake_urlopen(req):
            raise HTTPError(req.full_url, 500, "Server Error", hdrs=None, fp=io.BytesIO(b"boom"))

        with patch("mcp_common.google_oauth.urlopen", side_effect=fake_urlopen):
            with pytest.raises(GoogleOAuthError) as exc_info:
                client.request("a@example.com", "GET", "https://example.com/x")

        assert exc_info.value.status_code == 500


class TestRefreshToken:
    def test_no_refresh_token_raises(self, client):
        client._tokens = {"a@example.com": {}}

        with pytest.raises(GoogleOAuthError):
            client._refresh_token("a@example.com")

    def test_refresh_failure_raises_with_token_revoked(self, client):
        client._tokens = {"a@example.com": {"refresh_token": "rt-dead"}}
        client._credentials = {"client_id": "cid", "client_secret": "secret"}

        def fake_urlopen(req):
            raise HTTPError(req.full_url, 400, "Bad Request", hdrs=None, fp=io.BytesIO(b'{"error": "invalid_grant"}'))

        with (
            patch("mcp_common.google_oauth.urlopen", side_effect=fake_urlopen),
            patch.object(client, "_load_tokens", return_value={}),
        ):
            with pytest.raises(GoogleOAuthError) as exc_info:
                client._refresh_token("a@example.com")

        assert exc_info.value.token_revoked is True

    def test_stale_in_memory_token_reloads_from_disk_and_retries_once(self, client):
        """A 400 on the in-memory refresh token reloads from disk (another
        process may have already refreshed it) and retries exactly once.
        """
        client._tokens = {"a@example.com": {"refresh_token": "rt-stale"}}
        client._credentials = {"client_id": "cid", "client_secret": "secret"}

        calls = {"n": 0}

        def fake_urlopen(req):
            calls["n"] += 1
            if calls["n"] == 1:
                raise HTTPError(req.full_url, 400, "Bad Request", hdrs=None, fp=io.BytesIO(b"{}"))

            class FakeResp:
                def read(self):
                    return b'{"access_token": "fresh-at"}'

            return FakeResp()

        with (
            patch("mcp_common.google_oauth.urlopen", side_effect=fake_urlopen),
            patch.object(client, "_load_tokens", return_value={"a@example.com": {"refresh_token": "rt-fresh"}}),
            patch.object(client, "_save_tokens"),
        ):
            token = client._refresh_token("a@example.com")

        assert token == "fresh-at"
        assert calls["n"] == 2


class TestCheckLive:
    def test_successful_probe_reports_live(self, client):
        result = client.check_live("a@example.com", probe=lambda: None)
        assert result == {"live": True}

    def test_token_revoked_probe_reports_not_live_with_reason(self, client):
        def probe():
            raise GoogleOAuthError("dead token", token_revoked=True)

        result = client.check_live("a@example.com", probe=probe)
        assert result == {"live": False, "reason": "dead token"}

    def test_non_token_error_propagates(self, client):
        def probe():
            raise GoogleOAuthError("network blip", token_revoked=False)

        with pytest.raises(GoogleOAuthError):
            client.check_live("a@example.com", probe=probe)


class TestSaveTokensAtomicWrite:
    def test_interrupted_write_leaves_original_tokens_untouched(self, client, monkeypatch):
        original = {"a@example.com": {"refresh_token": "rt-original"}}
        client._tokens = original
        client._save_tokens()

        monkeypatch.setattr(
            "mcp_common.google_oauth.json.dump", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full"))
        )
        client._tokens = {"a@example.com": {"refresh_token": "rt-new"}}

        with pytest.raises(OSError, match="disk full"):
            client._save_tokens()

        assert json.loads(client.tokens_path.read_text()) == original
        tmp_file = client.tokens_path.with_suffix(".json.tmp")
        assert not tmp_file.exists()


class TestGoogleOAuthError:
    def test_defaults_token_revoked_to_false(self):
        assert GoogleOAuthError("boom").token_revoked is False

    def test_carries_status_code(self):
        err = GoogleOAuthError("boom", status_code=429)
        assert err.status_code == 429
