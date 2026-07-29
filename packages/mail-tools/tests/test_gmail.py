"""Unit tests for GmailClient batching logic (issue #44).

Pure-Python tests against GmailClient with _api_call monkeypatched - no
real OAuth tokens, no network, no Mail.app dependency.
"""

from __future__ import annotations

import base64
import io
import json
from datetime import datetime
from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from mail_tools.gmail import BATCH_MODIFY_LIMIT, SEARCH_PAGE_SIZE, GmailClient, GmailError, _is_rate_limited


def _decode_raw(raw: str):
    """Decode a base64url `raw` field back into an email.message.EmailMessage
    (the default policy so get_content()/EmailMessage headers work)."""
    return message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)


@pytest.fixture
def client():
    """A GmailClient with credential/token loading skipped."""
    with (
        patch.object(GmailClient, "_load_credentials", return_value={}),
        patch.object(GmailClient, "_load_tokens", return_value={}),
    ):
        return GmailClient()


class TestIsAuthorized:
    """is_authorized() must be case-insensitive (Gmail addresses aren't
    case-sensitive) - a case mismatch previously fell through
    MailManager.get_unread_count()'s fast path straight into a
    ScriptingBridge/Mail.app-launch call, for no reason other than input
    casing (code review finding on issue #101's reopened fix).
    """

    def test_exact_case_match(self, client):
        client._tokens = {"user@gmail.com": {"refresh_token": "rt"}}
        assert client.is_authorized("user@gmail.com") is True

    def test_differently_cased_input_still_matches(self, client):
        client._tokens = {"user@gmail.com": {"refresh_token": "rt"}}
        assert client.is_authorized("User@Gmail.com") is True

    def test_differently_cased_stored_key_still_matches(self, client):
        client._tokens = {"User@Gmail.com": {"refresh_token": "rt"}}
        assert client.is_authorized("user@gmail.com") is True

    def test_unknown_email_returns_false(self, client):
        client._tokens = {"user@gmail.com": {"refresh_token": "rt"}}
        assert client.is_authorized("someone-else@gmail.com") is False

    def test_missing_refresh_token_returns_false(self, client):
        client._tokens = {"user@gmail.com": {"access_token": "tok"}}
        assert client.is_authorized("user@gmail.com") is False


class TestBatchModify:
    def test_empty_ids_makes_no_api_call(self, client):
        with patch.object(client, "_api_call") as mock_call:
            result = client.batch_modify("a@example.com", [], add_label_ids=["UNREAD"])
        assert result == []
        mock_call.assert_not_called()

    def test_single_chunk_calls_batch_modify_once(self, client):
        ids = [f"id{i}" for i in range(10)]
        with patch.object(client, "_api_call", return_value={}) as mock_call:
            result = client.batch_modify("a@example.com", ids, remove_label_ids=["UNREAD"])
        assert result == ids
        mock_call.assert_called_once_with(
            "a@example.com", "POST", "messages/batchModify", {"removeLabelIds": ["UNREAD"], "ids": ids}
        )

    def test_chunks_ids_at_the_1000_limit(self, client):
        ids = [f"id{i}" for i in range(BATCH_MODIFY_LIMIT + 500)]
        with patch.object(client, "_api_call", return_value={}) as mock_call:
            result = client.batch_modify("a@example.com", ids, add_label_ids=["TRASH"])
        assert mock_call.call_count == 2
        first_chunk = mock_call.call_args_list[0].args[3]["ids"]
        second_chunk = mock_call.call_args_list[1].args[3]["ids"]
        assert len(first_chunk) == BATCH_MODIFY_LIMIT
        assert len(second_chunk) == 500
        assert result == ids

    def test_chunk_failure_retries_per_message(self, client):
        """A chunk-level 400 (e.g. a bad id in the batch) falls back to
        per-message modify calls instead of dropping the whole chunk.
        """
        ids = ["good1", "bad", "good2"]

        def fake_api_call(email, method, path, body=None):
            if path == "messages/batchModify":
                raise GmailError("Gmail API error 400: invalid id")
            if path == "messages/bad/modify":
                raise GmailError("Gmail API error 400: invalid id")
            return {}

        with patch.object(client, "_api_call", side_effect=fake_api_call):
            result = client.batch_modify("a@example.com", ids, remove_label_ids=["UNREAD"])

        assert result == ["good1", "good2"]


class TestBatchModifyByRfcIds:
    def test_resolves_ids_and_marks_modified(self, client):
        with (
            patch.object(client, "_find_message_id", side_effect=lambda email, rfc_id: f"gmail-{rfc_id}"),
            patch.object(client, "batch_modify", return_value=["gmail-a", "gmail-b"]),
        ):
            results = client._batch_modify_by_rfc_ids("a@example.com", ["a", "b"], remove_label_ids=["UNREAD"])

        by_id = {r["message_id"]: r for r in results}
        assert by_id["a"]["modified"] is True
        assert by_id["b"]["modified"] is True
        assert by_id["a"]["account"] == "a@example.com"

    def test_unresolved_rfc_id_reports_not_found(self, client):
        with (
            patch.object(client, "_find_message_id", return_value=None),
            patch.object(client, "batch_modify", return_value=[]),
        ):
            results = client._batch_modify_by_rfc_ids("a@example.com", ["missing"], add_label_ids=["TRASH"])

        assert results == [{"message_id": "missing", "modified": False, "reason": "not found via Gmail API"}]

    def test_batch_modify_call_omits_failed_gmail_ids_from_succeeded(self, client):
        with (
            patch.object(client, "_find_message_id", side_effect=lambda email, rfc_id: f"gmail-{rfc_id}"),
            patch.object(client, "batch_modify", return_value=["gmail-a"]),
        ):
            results = client._batch_modify_by_rfc_ids("a@example.com", ["a", "b"], remove_label_ids=["UNREAD"])

        by_id = {r["message_id"]: r for r in results}
        assert by_id["a"]["modified"] is True
        assert by_id["b"]["modified"] is False


class TestMarkReadUnreadMessages:
    def test_mark_read_messages_uses_remove_unread_label(self, client):
        with patch.object(
            client,
            "_batch_modify_by_rfc_ids",
            return_value=[{"message_id": "a", "modified": True, "account": "a@example.com"}],
        ) as mock_helper:
            results = client.mark_read_messages("a@example.com", ["a"])
        mock_helper.assert_called_once_with("a@example.com", ["a"], remove_label_ids=["UNREAD"])
        assert results[0]["marked"] is True
        assert results[0]["status"] == "read"

    def test_mark_unread_messages_uses_add_unread_label(self, client):
        with patch.object(
            client,
            "_batch_modify_by_rfc_ids",
            return_value=[{"message_id": "a", "modified": True, "account": "a@example.com"}],
        ) as mock_helper:
            results = client.mark_unread_messages("a@example.com", ["a"])
        mock_helper.assert_called_once_with("a@example.com", ["a"], add_label_ids=["UNREAD"])
        assert results[0]["marked"] is True
        assert results[0]["status"] == "unread"

    def test_mark_read_messages_reports_false_on_failure(self, client):
        with patch.object(
            client,
            "_batch_modify_by_rfc_ids",
            return_value=[{"message_id": "a", "modified": False, "account": "a@example.com"}],
        ):
            results = client.mark_read_messages("a@example.com", ["a"])
        assert results[0]["marked"] is False
        assert results[0]["status"] == "read"


class TestDeleteMessages:
    def test_delete_messages_uses_batch_modify_with_trash_label(self, client):
        with patch.object(
            client,
            "_batch_modify_by_rfc_ids",
            return_value=[{"message_id": "a", "modified": True, "account": "a@example.com"}],
        ) as mock_helper:
            results = client.delete_messages("a@example.com", ["a"])
        mock_helper.assert_called_once_with("a@example.com", ["a"], add_label_ids=["TRASH"], remove_label_ids=["INBOX"])
        assert results[0]["deleted"] is True


class TestArchiveMessages:
    """Issue #46: archive_messages was converted from a per-message
    GET-current-labels-then-modify flow to the same batch_modify() pattern
    as delete_messages/mark_read_messages/mark_unread_messages, after a live
    smoke test confirmed batchModify succeeds silently on a mixed batch
    (some messages already missing the INBOX label).
    """

    def test_archive_messages_uses_batch_modify_removing_inbox_and_trash(self, client):
        with patch.object(
            client,
            "_batch_modify_by_rfc_ids",
            return_value=[{"message_id": "a", "modified": True, "account": "a@example.com"}],
        ) as mock_helper:
            results = client.archive_messages("a@example.com", ["a"])
        mock_helper.assert_called_once_with("a@example.com", ["a"], remove_label_ids=["INBOX", "TRASH"])
        assert results[0]["archived"] is True
        assert results[0]["destination"] == "All Mail"

    def test_archive_messages_omits_destination_on_failure(self, client):
        with patch.object(
            client,
            "_batch_modify_by_rfc_ids",
            return_value=[{"message_id": "a", "modified": False, "reason": "not found via Gmail API"}],
        ):
            results = client.archive_messages("a@example.com", ["a"])
        assert results[0]["archived"] is False
        assert "destination" not in results[0]

    def test_archive_message_singular_method_no_longer_exists(self, client):
        """The per-message archive_message() method was removed entirely
        (issue #46) - a regression that reintroduces it and calls it from
        anywhere would silently reintroduce the N-GET-per-message cost this
        issue eliminated.
        """
        assert not hasattr(client, "archive_message")


class TestSearchIds:
    """Issue #45: search_ids returns Gmail-internal ids directly from a
    native query search, paginating via nextPageToken - no rfc822
    translation involved (contrast with _find_message_id).
    """

    def test_single_page_returns_all_ids(self, client):
        def fake_api_call(email, method, path):
            assert "q=" in path
            return {"messages": [{"id": "a"}, {"id": "b"}]}

        with patch.object(client, "_api_call", side_effect=fake_api_call) as mock_call:
            ids = client.search_ids("a@example.com", "is:unread")

        assert ids == ["a", "b"]
        mock_call.assert_called_once()

    def test_paginates_across_multiple_pages(self, client):
        pages = [
            {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "tok1"},
            {"messages": [{"id": "c"}], "nextPageToken": None},
        ]
        calls = []

        def fake_api_call(email, method, path):
            calls.append(path)
            return pages[len(calls) - 1]

        with patch.object(client, "_api_call", side_effect=fake_api_call):
            ids = client.search_ids("a@example.com", "from:x@example.com")

        assert ids == ["a", "b", "c"]
        assert len(calls) == 2
        assert "pageToken=tok1" in calls[1]
        assert "pageToken" not in calls[0]

    def test_no_matches_returns_empty_list(self, client):
        with patch.object(client, "_api_call", return_value={}):
            assert client.search_ids("a@example.com", "from:nobody@example.com") == []

    def test_respects_max_results_across_pages(self, client):
        pages = [
            {"messages": [{"id": f"id{i}"} for i in range(SEARCH_PAGE_SIZE)], "nextPageToken": "tok1"},
            {"messages": [{"id": "extra1"}, {"id": "extra2"}], "nextPageToken": None},
        ]
        calls = []

        def fake_api_call(email, method, path):
            calls.append(path)
            return pages[len(calls) - 1]

        with patch.object(client, "_api_call", side_effect=fake_api_call):
            ids = client.search_ids("a@example.com", "is:unread", max_results=SEARCH_PAGE_SIZE + 1)

        assert len(ids) == SEARCH_PAGE_SIZE + 1
        assert "maxResults=1" in calls[1]

    def test_query_is_url_encoded(self, client):
        with patch.object(client, "_api_call", return_value={}) as mock_call:
            client.search_ids("a@example.com", "from:a@b.com is:unread")

        path = mock_call.call_args.args[2]
        assert "from:a@b.com" not in path
        assert "%3A" in path


class TestSearchMessagesPreview:
    """Issue #45: the dry-run preview must give an exact matched_count (via
    full search_ids pagination, which is cheap) but only fetch metadata for
    a small sample - not for the full matched set.
    """

    def test_matched_count_reflects_full_search_ids_result(self, client):
        gmail_ids = [f"id{i}" for i in range(25)]
        with (
            patch.object(client, "search_ids", return_value=gmail_ids),
            patch.object(client, "_get_message_preview", side_effect=lambda email, gid: {"message_id": gid}),
        ):
            result = client.search_messages_preview("a@example.com", "is:unread")

        assert result["matched_count"] == 25

    def test_sample_capped_at_sample_size_even_with_many_matches(self, client):
        gmail_ids = [f"id{i}" for i in range(1000)]
        with (
            patch.object(client, "search_ids", return_value=gmail_ids),
            patch.object(
                client, "_get_message_preview", side_effect=lambda email, gid: {"message_id": gid}
            ) as mock_preview,
        ):
            result = client.search_messages_preview("a@example.com", "is:unread", sample_size=10)

        assert len(result["sample"]) == 10
        assert mock_preview.call_count == 10

    def test_empty_matches_returns_empty_sample(self, client):
        with patch.object(client, "search_ids", return_value=[]):
            result = client.search_messages_preview("a@example.com", "from:nobody@example.com")

        assert result == {"matched_count": 0, "sample": []}

    def test_get_message_preview_extracts_headers(self, client):
        with patch.object(
            client,
            "_api_call",
            return_value={
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Hello"},
                        {"name": "From", "value": "a@example.com"},
                        {"name": "Date", "value": "Mon, 1 Jul 2026 00:00:00 +0000"},
                    ]
                }
            },
        ):
            preview = client._get_message_preview("a@example.com", "gid1")

        assert preview == {
            "message_id": "gid1",
            "subject": "Hello",
            "from": "a@example.com",
            "date": "Mon, 1 Jul 2026 00:00:00 +0000",
        }


class TestApiCallPreservesStatusCode:
    """Code review follow-up on #44: GmailError must carry the HTTP status
    code so batch_modify() can distinguish a 429/quota-403 (rate limit) from
    a genuine bad-id 400 - previously every non-401 error flattened to a
    plain GmailError with no status.
    """

    def test_non_401_http_error_raises_gmail_error_with_status_code(self, client):
        client._tokens = {"a@example.com": {"access_token": "tok"}}

        def fake_urlopen(req):
            raise HTTPError(
                req.full_url, 429, "Too Many Requests", hdrs=None, fp=io.BytesIO(b'{"error": "rate limited"}')
            )

        with patch("mail_tools.gmail.urlopen", side_effect=fake_urlopen):
            with pytest.raises(GmailError) as exc_info:
                client._api_call("a@example.com", "GET", "messages")

        assert exc_info.value.status_code == 429


class TestGmailErrorTokenRevokedTagging:
    """Code review follow-up on #57: the raise sites that actually mean
    "this token is dead" must tag GmailError.token_revoked=True themselves,
    rather than check_live inferring it from message text.
    """

    def test_refresh_token_failure_raises_with_token_revoked(self, client):
        client._tokens = {"a@example.com": {"refresh_token": "rt-dead"}}
        client._credentials = {"client_id": "cid", "client_secret": "secret"}

        def fake_urlopen(req):
            raise HTTPError(req.full_url, 400, "Bad Request", hdrs=None, fp=io.BytesIO(b'{"error": "invalid_grant"}'))

        with (
            patch("mail_tools.gmail.urlopen", side_effect=fake_urlopen),
            patch.object(client, "_load_tokens", return_value={}),
        ):
            with pytest.raises(GmailError) as exc_info:
                client._refresh_token("a@example.com")

        assert exc_info.value.token_revoked is True

    def test_missing_access_token_with_refresh_token_present_is_token_revoked(self, client):
        """A refresh_token with no access_token is a partial/corrupted entry
        that can never succeed without re-authorization - classify it the
        same as a confirmed-dead token.
        """
        client._tokens = {"a@example.com": {"refresh_token": "rt-only"}}

        with pytest.raises(GmailError) as exc_info:
            client._api_call("a@example.com", "GET", "profile")

        assert exc_info.value.token_revoked is True

    def test_missing_access_token_with_no_refresh_token_is_not_token_revoked(self, client):
        """No token entry at all means "never authorized", not "revoked" -
        must not be misreported as a dead token.
        """
        client._tokens = {}

        with pytest.raises(GmailError) as exc_info:
            client._api_call("a@example.com", "GET", "profile")

        assert exc_info.value.token_revoked is False

    def test_gmail_error_defaults_token_revoked_to_false(self):
        assert GmailError("boom").token_revoked is False


class TestIsRateLimited:
    def test_429_is_always_rate_limited(self):
        assert _is_rate_limited(GmailError("boom", status_code=429)) is True

    def test_403_with_quota_wording_is_rate_limited(self):
        assert _is_rate_limited(GmailError("Gmail API error 403: userRateLimitExceeded", status_code=403)) is True

    def test_403_without_quota_wording_is_not_rate_limited(self):
        assert _is_rate_limited(GmailError("Gmail API error 403: insufficientPermissions", status_code=403)) is False

    def test_400_is_not_rate_limited(self):
        assert _is_rate_limited(GmailError("Gmail API error 400: invalid id", status_code=400)) is False

    def test_no_status_code_is_not_rate_limited(self):
        assert _is_rate_limited(GmailError("boom")) is False


class TestBatchModifyRateLimitBackoff:
    """A 429 (or quota-flavored 403) chunk failure must back off and retry
    the WHOLE chunk, never fan out into per-message calls - that's exactly
    the wrong behavior when quota pressure is already the problem.
    """

    def test_429_retries_whole_chunk_with_backoff_then_succeeds(self, client):
        calls = {"n": 0}

        def fake_api_call(email, method, path, body=None):
            if path == "messages/batchModify":
                calls["n"] += 1
                if calls["n"] < 3:
                    raise GmailError("Gmail API error 429: rate limit exceeded", status_code=429)
                return {}
            raise AssertionError("must not fall back to per-message modify on a rate-limit failure")

        with (
            patch.object(client, "_api_call", side_effect=fake_api_call),
            patch("mail_tools.gmail.time.sleep") as mock_sleep,
        ):
            result = client.batch_modify("a@example.com", ["a", "b"], remove_label_ids=["UNREAD"])

        assert result == ["a", "b"]
        assert calls["n"] == 3
        assert mock_sleep.call_count == 2

    def test_429_exhausts_retries_and_raises_instead_of_per_message_fallback(self, client):
        def fake_api_call(email, method, path, body=None):
            if path == "messages/batchModify":
                raise GmailError("Gmail API error 429: rate limit exceeded", status_code=429)
            raise AssertionError("must not fall back to per-message modify on a rate-limit failure")

        with (
            patch.object(client, "_api_call", side_effect=fake_api_call),
            patch("mail_tools.gmail.time.sleep"),
            pytest.raises(GmailError),
        ):
            client.batch_modify("a@example.com", ["a", "b"], remove_label_ids=["UNREAD"])

    def test_quota_403_treated_like_429_not_per_message_fallback(self, client):
        def fake_api_call(email, method, path, body=None):
            if path == "messages/batchModify":
                raise GmailError("Gmail API error 403: userRateLimitExceeded", status_code=403)
            raise AssertionError("must not fall back to per-message modify on a quota failure")

        with (
            patch.object(client, "_api_call", side_effect=fake_api_call),
            patch("mail_tools.gmail.time.sleep"),
            pytest.raises(GmailError),
        ):
            client.batch_modify("a@example.com", ["a", "b"], remove_label_ids=["UNREAD"])

    def test_non_rate_limit_403_still_falls_back_to_per_message(self, client):
        def fake_api_call(email, method, path, body=None):
            if path == "messages/batchModify":
                raise GmailError("Gmail API error 403: insufficientPermissions", status_code=403)
            return {}

        with patch.object(client, "_api_call", side_effect=fake_api_call):
            result = client.batch_modify("a@example.com", ["a", "b"], remove_label_ids=["UNREAD"])

        assert result == ["a", "b"]


class TestSaveTokensAtomicWrite:
    """_save_tokens() got the same temp-file+rename treatment as
    rules.save_rules() (issue #54) - it holds every account's refresh_token,
    so a truncated write here is worse than a truncated sender_rules.json.
    """

    def test_interrupted_write_leaves_original_tokens_untouched(self, client, tmp_path, monkeypatch):
        tokens_file = tmp_path / "gmail_tokens.json"
        monkeypatch.setattr("mail_tools.gmail.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("mail_tools.gmail.TOKENS_FILE", tokens_file)

        original = {"a@example.com": {"refresh_token": "rt-original"}}
        client._tokens = original
        client._save_tokens()

        monkeypatch.setattr("mail_tools.gmail.json.dump", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
        client._tokens = {"a@example.com": {"refresh_token": "rt-new"}}

        with pytest.raises(OSError, match="disk full"):
            client._save_tokens()

        assert json.loads(tokens_file.read_text()) == original
        tmp_file = tokens_file.with_suffix(".json.tmp")
        assert not tmp_file.exists()


class TestCheckLive:
    """Issue #57: check_live() must actually call the Gmail API (users.
    getProfile) rather than just checking gmail_tokens.json has an entry -
    a revoked/expired refresh token was previously indistinguishable from
    a live one.
    """

    def test_successful_profile_call_reports_live(self, client):
        with patch.object(client, "_api_call", return_value={"emailAddress": "a@example.com"}) as mock_call:
            result = client.check_live("a@example.com")

        mock_call.assert_called_once_with("a@example.com", "GET", "profile")
        assert result == {"live": True}

    def test_dead_refresh_token_reports_not_live_with_reason(self, client):
        """_api_call already tried and failed to refresh a dead token before
        raising - this is not a merely-expired access token, it's confirmed
        dead and needs gmail_authorize again.

        Classification is structural (GmailError.token_revoked), not a
        string match against the message - a message wording change must
        not silently break dead-token detection (code review follow-up on
        issue #57).
        """
        with patch.object(
            client,
            "_api_call",
            side_effect=GmailError("some unrelated wording", token_revoked=True),
        ):
            result = client.check_live("a@example.com")

        assert result["live"] is False
        assert result["reason"] == "some unrelated wording"

    def test_unrelated_gmail_error_propagates_instead_of_reporting_dead(self, client):
        """A transient/quota GmailError unrelated to token refresh must not
        be swallowed into a false "dead" report - the caller (gmail_status)
        needs to tell "confirmed dead" apart from "couldn't check". An error
        that happens to mention "Token refresh failed" in its message but is
        NOT tagged token_revoked must still propagate.
        """
        with patch.object(
            client,
            "_api_call",
            side_effect=GmailError("Token refresh failed - but not really, this is a decoy message"),
        ):
            with pytest.raises(GmailError, match="decoy"):
                client.check_live("a@example.com")


class TestBuildMimeRaw:
    """Issue #50: _build_mime_raw builds a real RFC 2822 message and
    base64url-encodes it for Gmail's raw send/draft field - assertions
    decode the raw field and check actual MIME headers/body, not just
    "was some string produced".
    """

    def test_basic_headers_and_body(self, client):
        raw = client._build_mime_raw("me@gmail.com", "you@example.com", "Hello", "Hi there")
        msg = _decode_raw(raw)

        assert msg["From"] == "me@gmail.com"
        assert msg["To"] == "you@example.com"
        assert msg["Subject"] == "Hello"
        assert msg.get_content().strip() == "Hi there"
        assert msg["Cc"] is None
        assert msg["Bcc"] is None
        assert msg["In-Reply-To"] is None
        assert msg["References"] is None

    def test_cc_and_bcc_headers_set_when_provided(self, client):
        raw = client._build_mime_raw(
            "me@gmail.com", "you@example.com", "Hello", "Hi", cc="cc@example.com", bcc="bcc@example.com"
        )
        msg = _decode_raw(raw)

        assert msg["Cc"] == "cc@example.com"
        assert msg["Bcc"] == "bcc@example.com"

    def test_in_reply_to_and_references_wrapped_in_angle_brackets(self, client):
        raw = client._build_mime_raw(
            "me@gmail.com",
            "you@example.com",
            "Re: Hello",
            "Reply body",
            in_reply_to="abc123@mail.gmail.com",
            references="abc123@mail.gmail.com",
        )
        msg = _decode_raw(raw)

        assert msg["In-Reply-To"] == "<abc123@mail.gmail.com>"
        assert msg["References"] == "<abc123@mail.gmail.com>"

    def test_already_bracketed_ids_are_not_double_wrapped(self, client):
        raw = client._build_mime_raw(
            "me@gmail.com",
            "you@example.com",
            "Re: Hello",
            "Reply body",
            in_reply_to="<abc123@mail.gmail.com>",
            references="<abc123@mail.gmail.com>",
        )
        msg = _decode_raw(raw)

        assert msg["In-Reply-To"] == "<abc123@mail.gmail.com>"
        assert msg["References"] == "<abc123@mail.gmail.com>"


class TestSendMessage:
    def test_send_message_posts_raw_and_returns_gmail_id(self, client):
        with patch.object(client, "_api_call", return_value={"id": "gid-sent"}) as mock_call:
            result = client.send_message("me@gmail.com", "you@example.com", "Hello", "Body text")

        mock_call.assert_called_once()
        args = mock_call.call_args.args
        assert args[0] == "me@gmail.com"
        assert args[1] == "POST"
        assert args[2] == "messages/send"
        body = args[3]
        assert "threadId" not in body
        msg = _decode_raw(body["raw"])
        assert msg["To"] == "you@example.com"
        assert msg["Subject"] == "Hello"
        assert msg.get_content().strip() == "Body text"

        assert result == {"to": "you@example.com", "subject": "Hello", "status": "sent", "gmail_id": "gid-sent"}

    def test_send_message_includes_thread_id_when_provided(self, client):
        with patch.object(client, "_api_call", return_value={"id": "gid-sent"}) as mock_call:
            client.send_message("me@gmail.com", "you@example.com", "Hello", "Body", thread_id="thread-abc")

        body = mock_call.call_args.args[3]
        assert body["threadId"] == "thread-abc"

    def test_send_message_sets_reply_headers(self, client):
        with patch.object(client, "_api_call", return_value={"id": "gid-sent"}) as mock_call:
            client.send_message(
                "me@gmail.com",
                "you@example.com",
                "Re: Hello",
                "Reply",
                in_reply_to="orig-id@mail.gmail.com",
                references="orig-id@mail.gmail.com",
            )

        body = mock_call.call_args.args[3]
        msg = _decode_raw(body["raw"])
        assert msg["In-Reply-To"] == "<orig-id@mail.gmail.com>"
        assert msg["References"] == "<orig-id@mail.gmail.com>"


class TestCreateDraft:
    def test_create_draft_wraps_raw_in_message_envelope(self, client):
        with patch.object(client, "_api_call", return_value={"id": "gid-draft"}) as mock_call:
            result = client.create_draft("me@gmail.com", "you@example.com", "Hello", "Body text")

        args = mock_call.call_args.args
        assert args[0] == "me@gmail.com"
        assert args[1] == "POST"
        assert args[2] == "drafts"
        body = args[3]
        assert "message" in body
        assert "threadId" not in body["message"]
        msg = _decode_raw(body["message"]["raw"])
        assert msg["Subject"] == "Hello"

        assert result == {
            "to": "you@example.com",
            "subject": "Hello",
            "status": "draft",
            "gmail_draft_id": "gid-draft",
        }

    def test_create_draft_includes_thread_id_when_provided(self, client):
        with patch.object(client, "_api_call", return_value={"id": "gid-draft"}) as mock_call:
            client.create_draft("me@gmail.com", "you@example.com", "Hello", "Body", thread_id="thread-abc")

        body = mock_call.call_args.args[3]
        assert body["message"]["threadId"] == "thread-abc"


class TestResolveThread:
    """Issue #50: resolve_thread reuses _find_message_id then one minimal
    messages.get to learn threadId - and returns None (not an exception)
    when the rfc822 Message-ID isn't found via the Gmail API.
    """

    def test_found_message_returns_gmail_id_and_thread_id(self, client):
        with (
            patch.object(client, "_find_message_id", return_value="gmail-internal-id"),
            patch.object(
                client, "_api_call", return_value={"id": "gmail-internal-id", "threadId": "thread-1"}
            ) as mock_call,
        ):
            result = client.resolve_thread("me@gmail.com", "orig-id@mail.gmail.com")

        assert result == {"gmail_id": "gmail-internal-id", "thread_id": "thread-1"}
        mock_call.assert_called_once_with("me@gmail.com", "GET", "messages/gmail-internal-id?format=minimal")

    def test_not_found_returns_none(self, client):
        with (
            patch.object(client, "_find_message_id", return_value=None),
            patch.object(client, "_api_call") as mock_call,
        ):
            result = client.resolve_thread("me@gmail.com", "missing-id@mail.gmail.com")

        assert result is None
        mock_call.assert_not_called()


class TestGetMessageMetadata:
    def test_derives_read_and_flagged_from_label_ids(self, client):
        with patch.object(
            client,
            "_api_call",
            return_value={
                "labelIds": ["INBOX", "STARRED"],
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Hello"},
                        {"name": "From", "value": "a@example.com"},
                        {"name": "Date", "value": "Mon, 1 Jul 2026 00:00:00 +0000"},
                    ]
                },
            },
        ):
            result = client._get_message_metadata("me@gmail.com", "gid1")

        expected_date = parsedate_to_datetime("Mon, 1 Jul 2026 00:00:00 +0000").astimezone().isoformat()
        assert result == {
            "message_id": "gid1",
            "subject": "Hello",
            "from": "a@example.com",
            "date": expected_date,
            "read": True,
            "flagged": True,
        }

    def test_unread_and_unflagged_when_labels_present(self, client):
        with patch.object(
            client,
            "_api_call",
            return_value={"labelIds": ["INBOX", "UNREAD"], "payload": {"headers": []}},
        ):
            result = client._get_message_metadata("me@gmail.com", "gid1")

        assert result["read"] is False
        assert result["flagged"] is False

    def test_date_formatted_as_iso8601_matching_scriptingbridge_shape(self, client):
        """The raw RFC 2822 Date header must be normalized to the same ISO
        8601 shape mail.py's _batch_serialize_messages produces, not passed
        through as the raw header string (issue #50 review finding)."""
        with patch.object(
            client,
            "_api_call",
            return_value={
                "labelIds": [],
                "payload": {"headers": [{"name": "Date", "value": "Wed, 15 Jul 2026 09:30:00 -0400"}]},
            },
        ):
            result = client._get_message_metadata("me@gmail.com", "gid1")

        # ISO 8601 dates start YYYY-MM-DD, unlike the RFC 2822 "Wed, 15 Jul ..." shape.
        assert result["date"] != "Wed, 15 Jul 2026 09:30:00 -0400"
        datetime.fromisoformat(result["date"])  # raises if not valid ISO 8601

    def test_subject_defaults_to_no_subject_when_missing(self, client):
        """Matches ScriptingBridge's "(no subject)" fallback convention,
        rather than Gmail's own default of None (issue #50 review finding)."""
        with patch.object(
            client,
            "_api_call",
            return_value={"labelIds": [], "payload": {"headers": []}},
        ):
            result = client._get_message_metadata("me@gmail.com", "gid1")

        assert result["subject"] == "(no subject)"


class TestListMessages:
    def test_default_inbox_uses_inbox_label_and_caps_at_limit(self, client):
        with (
            patch.object(
                client,
                "_api_call",
                return_value={"messages": [{"id": f"id{i}"} for i in range(5)]},
            ) as mock_call,
            patch.object(client, "_get_message_metadata", side_effect=lambda email, gid: {"message_id": gid}),
        ):
            results = client.list_messages("me@gmail.com", limit=3)

        path = mock_call.call_args.args[2]
        assert "labelIds=INBOX" in path
        assert "maxResults=3" in path
        assert len(results) == 3

    def test_non_inbox_mailbox_uses_in_query(self, client):
        with (
            patch.object(client, "_api_call", return_value={"messages": []}) as mock_call,
            patch.object(client, "_get_message_metadata"),
        ):
            client.list_messages("me@gmail.com", mailbox="Archive", limit=10)

        path = mock_call.call_args.args[2]
        assert "labelIds=INBOX" not in path
        assert "q=" in path

    def test_multi_word_mailbox_name_is_quoted_in_query(self, client):
        """A bare `in:all mail` query is parsed by Gmail's search grammar as
        two unquoted terms rather than matching the "All Mail" label - the
        mailbox value must be quoted (issue #50 review finding)."""
        with (
            patch.object(client, "_api_call", return_value={"messages": []}) as mock_call,
            patch.object(client, "_get_message_metadata"),
        ):
            client.list_messages("me@gmail.com", mailbox="All Mail", limit=10)

        path = mock_call.call_args.args[2]
        assert "in%3A%22all%20mail%22" in path


class TestSearchMessages:
    def test_search_messages_caps_results_at_limit(self, client):
        with (
            patch.object(
                client,
                "_api_call",
                return_value={"messages": [{"id": f"id{i}"} for i in range(10)]},
            ) as mock_call,
            patch.object(client, "_get_message_metadata", side_effect=lambda email, gid: {"message_id": gid}),
        ):
            results = client.search_messages("me@gmail.com", "from:a@example.com", limit=4)

        path = mock_call.call_args.args[2]
        assert "maxResults=4" in path
        assert "q=" in path
        assert len(results) == 4

    def test_search_messages_no_matches_returns_empty_list(self, client):
        with patch.object(client, "_api_call", return_value={}):
            assert client.search_messages("me@gmail.com", "from:nobody@example.com") == []


class TestGetMessage:
    """Issue #87: get_message() fetches full message content (metadata +
    body) by Gmail-internal id, backing MailManager.read_message()'s
    Gmail-authorized routing.
    """

    def test_returns_metadata_and_decoded_plain_text_body(self, client):
        body_b64 = base64.urlsafe_b64encode(b"Hello from Gmail").decode().rstrip("=")
        with patch.object(
            client,
            "_api_call",
            return_value={
                "labelIds": ["INBOX"],
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "Subject", "value": "Hello"},
                        {"name": "From", "value": "a@example.com"},
                        {"name": "Date", "value": "Mon, 1 Jul 2026 00:00:00 +0000"},
                    ],
                    "body": {"data": body_b64},
                },
            },
        ) as mock_call:
            result = client.get_message("me@gmail.com", "gid1")

        mock_call.assert_called_once_with("me@gmail.com", "GET", "messages/gid1?format=full")
        assert result["message_id"] == "gid1"
        assert result["subject"] == "Hello"
        assert result["from"] == "a@example.com"
        assert result["read"] is True
        assert result["flagged"] is False
        assert result["content"] == "Hello from Gmail"

    def test_multipart_alternative_prefers_plain_text_over_html(self, client):
        plain_b64 = base64.urlsafe_b64encode(b"plain body").decode().rstrip("=")
        html_b64 = base64.urlsafe_b64encode(b"<p>html body</p>").decode().rstrip("=")
        with patch.object(
            client,
            "_api_call",
            return_value={
                "labelIds": [],
                "payload": {
                    "mimeType": "multipart/alternative",
                    "headers": [],
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": plain_b64}},
                        {"mimeType": "text/html", "body": {"data": html_b64}},
                    ],
                },
            },
        ):
            result = client.get_message("me@gmail.com", "gid1")

        assert result["content"] == "plain body"

    def test_404_returns_none_not_error(self, client):
        with patch.object(client, "_api_call", side_effect=GmailError("not found", status_code=404)):
            assert client.get_message("me@gmail.com", "missing-gid") is None

    def test_non_404_error_propagates(self, client):
        with (
            patch.object(client, "_api_call", side_effect=GmailError("token revoked", status_code=401)),
            pytest.raises(GmailError),
        ):
            client.get_message("me@gmail.com", "gid1")


class TestMessageExists:
    """Issue #89: message_exists() is a lightweight existence/ownership
    check for a Gmail-internal id, backing MailManager's Pass 3 direct-id
    lookup fallback in archive_messages/delete_messages/_mark_messages.
    """

    def test_returns_true_and_uses_minimal_format(self, client):
        with patch.object(client, "_api_call", return_value={"id": "gid1", "labelIds": ["INBOX"]}) as mock_call:
            assert client.message_exists("me@gmail.com", "gid1") is True

        mock_call.assert_called_once_with("me@gmail.com", "GET", "messages/gid1?format=minimal")

    def test_404_returns_false_not_error(self, client):
        with patch.object(client, "_api_call", side_effect=GmailError("not found", status_code=404)):
            assert client.message_exists("me@gmail.com", "missing-gid") is False

    def test_non_404_error_propagates(self, client):
        with (
            patch.object(client, "_api_call", side_effect=GmailError("token revoked", status_code=401)),
            pytest.raises(GmailError),
        ):
            client.message_exists("me@gmail.com", "gid1")


class TestModifyByGmailId:
    """Issue #89: modify_by_gmail_id() batch-modifies labels on ids that are
    ALREADY known Gmail-internal ids, skipping the rfc822 Message-ID
    resolution _batch_modify_by_rfc_ids() performs - the step that made
    Pass 3's direct-id lookup necessary in the first place (rfc822msgid:
    search can never match a bare Gmail-internal hex id).
    """

    def test_calls_batch_modify_directly_with_no_id_translation(self, client):
        with patch.object(client, "batch_modify", return_value=["gid1", "gid2"]) as mock_batch_modify:
            results = client.modify_by_gmail_id("me@gmail.com", ["gid1", "gid2"], remove_label_ids=["INBOX", "TRASH"])

        mock_batch_modify.assert_called_once_with(
            "me@gmail.com", ["gid1", "gid2"], add_label_ids=None, remove_label_ids=["INBOX", "TRASH"]
        )
        assert results == [
            {"message_id": "gid1", "modified": True, "account": "me@gmail.com"},
            {"message_id": "gid2", "modified": True, "account": "me@gmail.com"},
        ]

    def test_id_absent_from_succeeded_reports_modified_false(self, client):
        with patch.object(client, "batch_modify", return_value=["gid1"]):
            results = client.modify_by_gmail_id("me@gmail.com", ["gid1", "gid2"], add_label_ids=["UNREAD"])

        by_id = {r["message_id"]: r["modified"] for r in results}
        assert by_id == {"gid1": True, "gid2": False}

    def test_never_calls_find_message_id_rfc822_resolution(self, client):
        """Regression guard: modify_by_gmail_id() must never resolve its
        input as an rfc822 Message-ID - that is exactly the lookup that
        fails for a bare Gmail-internal id (issue #89)."""
        with (
            patch.object(client, "batch_modify", return_value=["gid1"]) as mock_batch_modify,
            patch.object(client, "_find_message_id") as mock_find,
        ):
            client.modify_by_gmail_id("me@gmail.com", ["gid1"], remove_label_ids=["UNREAD"])

        mock_find.assert_not_called()
        mock_batch_modify.assert_called_once()


class TestExtractPlainTextBody:
    """Issue #87: _extract_plain_text_body() walks a Gmail message payload's
    MIME tree for the best available text content.
    """

    def test_single_part_text_plain(self):
        from mail_tools.gmail import _extract_plain_text_body

        data = base64.urlsafe_b64encode(b"just text").decode().rstrip("=")
        payload = {"mimeType": "text/plain", "body": {"data": data}}
        assert _extract_plain_text_body(payload) == "just text"

    def test_falls_back_to_html_when_no_plain_part(self):
        from mail_tools.gmail import _extract_plain_text_body

        html_data = base64.urlsafe_b64encode(b"<b>html only</b>").decode().rstrip("=")
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [{"mimeType": "text/html", "body": {"data": html_data}}],
        }
        assert _extract_plain_text_body(payload) == "<b>html only</b>"

    def test_recurses_into_nested_multipart_mixed(self):
        from mail_tools.gmail import _extract_plain_text_body

        plain_data = base64.urlsafe_b64encode(b"nested plain").decode().rstrip("=")
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [{"mimeType": "text/plain", "body": {"data": plain_data}}],
                },
                {"mimeType": "application/pdf", "body": {"attachmentId": "att1"}},
            ],
        }
        assert _extract_plain_text_body(payload) == "nested plain"

    def test_no_body_anywhere_returns_empty_string(self):
        from mail_tools.gmail import _extract_plain_text_body

        assert _extract_plain_text_body({"mimeType": "multipart/mixed", "parts": []}) == ""

    def test_plain_text_in_later_sibling_wins_over_earlier_sibling_html_only_fallback(self):
        """Code-review regression test (issue #87): a multipart/mixed with
        two nested multipart siblings - a multipart/related (inline
        images, html only, no plain part) ordered BEFORE a
        multipart/alternative (has both plain and html) - must return the
        second sibling's real plain text, not the first sibling's html
        fallback. A single-pass walk that accepts the first sibling's
        recursive result (even when that result is itself an html
        fallback) returns the wrong content here; sibling order depends
        on the sending mail client, not on this code.
        """
        from mail_tools.gmail import _extract_plain_text_body

        html_only_data = base64.urlsafe_b64encode(b"<p>html only, no plain in this branch</p>").decode().rstrip("=")
        plain_data = base64.urlsafe_b64encode(b"real plain text").decode().rstrip("=")
        html_data = base64.urlsafe_b64encode(b"<p>alternative html</p>").decode().rstrip("=")
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/related",
                    "parts": [
                        {"mimeType": "text/html", "body": {"data": html_only_data}},
                        {"mimeType": "image/png", "body": {"attachmentId": "att1"}},
                    ],
                },
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": plain_data}},
                        {"mimeType": "text/html", "body": {"data": html_data}},
                    ],
                },
            ],
        }
        assert _extract_plain_text_body(payload) == "real plain text"


class TestListLabels:
    """Issue #58: list_labels() wraps GET users/me/labels."""

    def test_returns_labels_list(self, client):
        with patch.object(
            client,
            "_api_call",
            return_value={"labels": [{"id": "Label_1", "name": "financial-statements", "type": "user"}]},
        ) as mock_call:
            result = client.list_labels("me@gmail.com")

        mock_call.assert_called_once_with("me@gmail.com", "GET", "labels")
        assert result == [{"id": "Label_1", "name": "financial-statements", "type": "user"}]

    def test_no_labels_key_returns_empty_list(self, client):
        with patch.object(client, "_api_call", return_value={}):
            assert client.list_labels("me@gmail.com") == []


class TestGetUnreadCounts:
    """Issue #101: get_unread_counts() fetches live per-label unread counts
    via users.labels.get, one GET per label returned by list_labels() -
    list_labels()/users.labels.list has no unread-count field.
    """

    def test_fetches_messages_unread_per_label(self, client):
        labels = [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "IMPORTANT", "name": "IMPORTANT", "type": "system"},
        ]
        label_details = {
            "INBOX": {"id": "INBOX", "messagesUnread": 2},
            "IMPORTANT": {"id": "IMPORTANT", "messagesUnread": 5},
        }

        def fake_api_call(email, method, path):
            assert method == "GET"
            label_id = path.removeprefix("labels/")
            return label_details[label_id]

        with (
            patch.object(client, "list_labels", return_value=labels),
            patch.object(client, "_api_call", side_effect=fake_api_call) as mock_call,
        ):
            result = client.get_unread_counts("me@gmail.com")

        assert result == [
            {"mailbox": "INBOX", "unread": 2},
            {"mailbox": "IMPORTANT", "unread": 5},
        ]
        assert mock_call.call_count == 2

    def test_zero_unread_labels_are_excluded(self, client):
        labels = [{"id": "SENT", "name": "SENT", "type": "system"}]

        with (
            patch.object(client, "list_labels", return_value=labels),
            patch.object(client, "_api_call", return_value={"id": "SENT", "messagesUnread": 0}),
        ):
            result = client.get_unread_counts("me@gmail.com")

        assert result == []

    def test_missing_messages_unread_key_treated_as_zero(self, client):
        labels = [{"id": "DRAFT", "name": "DRAFT", "type": "system"}]

        with (
            patch.object(client, "list_labels", return_value=labels),
            patch.object(client, "_api_call", return_value={"id": "DRAFT"}),
        ):
            result = client.get_unread_counts("me@gmail.com")

        assert result == []

    def test_no_labels_makes_no_detail_calls(self, client):
        with (
            patch.object(client, "list_labels", return_value=[]),
            patch.object(client, "_api_call") as mock_call,
        ):
            result = client.get_unread_counts("me@gmail.com")

        assert result == []
        mock_call.assert_not_called()

    def test_one_label_failure_does_not_discard_others(self, client):
        """A transient failure fetching one label's detail (rate limit,
        network blip) must not discard unread counts already fetched for
        other labels in the same call - only that one label is skipped.
        """
        labels = [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "IMPORTANT", "name": "IMPORTANT", "type": "system"},
            {"id": "STARRED", "name": "STARRED", "type": "system"},
        ]
        label_details = {
            "INBOX": {"id": "INBOX", "messagesUnread": 2},
            "STARRED": {"id": "STARRED", "messagesUnread": 1},
        }

        def fake_api_call(email, method, path):
            label_id = path.removeprefix("labels/")
            if label_id == "IMPORTANT":
                raise RuntimeError("transient 500")
            return label_details[label_id]

        with (
            patch.object(client, "list_labels", return_value=labels),
            patch.object(client, "_api_call", side_effect=fake_api_call),
        ):
            result = client.get_unread_counts("me@gmail.com")

        assert result == [
            {"mailbox": "INBOX", "unread": 2},
            {"mailbox": "STARRED", "unread": 1},
        ]


class TestCreateLabel:
    """Issue #58: create_label() must be idempotent - a 409 (already exists)
    is recovered via re-list, not raised, so calling twice never raises and
    never creates a duplicate.
    """

    def test_creates_label_with_show_visibility(self, client):
        with patch.object(
            client, "_api_call", return_value={"id": "Label_1", "name": "financial-statements"}
        ) as mock_call:
            result = client.create_label("me@gmail.com", "financial-statements")

        mock_call.assert_called_once_with(
            "me@gmail.com",
            "POST",
            "labels",
            {"name": "financial-statements", "labelListVisibility": "labelShow", "messageListVisibility": "show"},
        )
        assert result == {"id": "Label_1", "name": "financial-statements"}

    def test_409_recovers_existing_label_instead_of_raising(self, client):
        """Second call races into a 409 (label already created, e.g. by an
        earlier call or another process) - must return the existing label,
        not raise, and must not attempt to create a second one.
        """
        call_count = {"n": 0}

        def fake_api_call(email, method, path, body=None):
            call_count["n"] += 1
            if method == "POST":
                raise GmailError("Gmail API error 409: label exists", status_code=409)
            return {"labels": [{"id": "Label_1", "name": "financial-statements", "type": "user"}]}

        with patch.object(client, "_api_call", side_effect=fake_api_call):
            result = client.create_label("me@gmail.com", "financial-statements")

        assert result == {"id": "Label_1", "name": "financial-statements", "type": "user"}

    def test_409_with_no_matching_label_found_reraises(self, client):
        """A surprising state - the 409 claimed the label exists but a
        re-list can't find it - must not be silently swallowed.
        """

        def fake_api_call(email, method, path, body=None):
            if method == "POST":
                raise GmailError("Gmail API error 409: label exists", status_code=409)
            return {"labels": []}

        with patch.object(client, "_api_call", side_effect=fake_api_call):
            with pytest.raises(GmailError):
                client.create_label("me@gmail.com", "financial-statements")

    def test_non_409_error_propagates(self, client):
        with patch.object(client, "_api_call", side_effect=GmailError("boom", status_code=500)):
            with pytest.raises(GmailError, match="boom"):
                client.create_label("me@gmail.com", "financial-statements")


class TestResolveLabelId:
    """Issue #58: resolve_label_id() caches per email so repeated calls for
    the same name cost at most one list_labels() call and one create_label()
    call total within a process lifetime.
    """

    def test_cache_hit_makes_no_api_call(self, client):
        client._label_cache["me@gmail.com"] = {"financial-statements": "Label_1"}

        with patch.object(client, "_api_call") as mock_call:
            label_id, created = client.resolve_label_id("me@gmail.com", "financial-statements")

        assert label_id == "Label_1"
        assert created is False
        mock_call.assert_not_called()

    def test_cache_miss_found_via_list_does_not_create(self, client):
        with patch.object(
            client,
            "_api_call",
            return_value={"labels": [{"id": "Label_1", "name": "financial-statements", "type": "user"}]},
        ) as mock_call:
            label_id, created = client.resolve_label_id("me@gmail.com", "financial-statements")

        assert label_id == "Label_1"
        assert created is False
        mock_call.assert_called_once_with("me@gmail.com", "GET", "labels")

    def test_missing_label_is_created(self, client):
        def fake_api_call(email, method, path, body=None):
            if method == "GET":
                return {"labels": []}
            return {"id": "Label_9", "name": "financial-statements"}

        with patch.object(client, "_api_call", side_effect=fake_api_call):
            label_id, created = client.resolve_label_id("me@gmail.com", "financial-statements")

        assert label_id == "Label_9"
        assert created is True

    def test_repeated_calls_for_same_name_cost_one_list_and_one_create(self, client):
        """The core caching guarantee: 5 resolve_label_id() calls for the
        same name within one process cost exactly one list_labels()-
        equivalent GET and one create_label() POST, not five of each.
        """
        calls = {"get": 0, "post": 0}

        def fake_api_call(email, method, path, body=None):
            if method == "GET":
                calls["get"] += 1
                return {"labels": []}
            calls["post"] += 1
            return {"id": "Label_9", "name": "financial-statements"}

        with patch.object(client, "_api_call", side_effect=fake_api_call):
            for _ in range(5):
                label_id, created = client.resolve_label_id("me@gmail.com", "financial-statements")
                assert label_id == "Label_9"

        assert calls["get"] == 1
        assert calls["post"] == 1

    def test_different_emails_have_independent_caches(self, client):
        client._label_cache["a@gmail.com"] = {"financial-statements": "Label_1"}

        with patch.object(
            client,
            "_api_call",
            return_value={"labels": [{"id": "Label_2", "name": "financial-statements", "type": "user"}]},
        ) as mock_call:
            label_id, created = client.resolve_label_id("b@gmail.com", "financial-statements")

        assert label_id == "Label_2"
        mock_call.assert_called_once()


class TestFindLabel:
    """Issue #85: find_label() is the cache-check-then-list-refresh half of
    resolve_label_id(), minus the create-on-miss tail - it never creates.
    """

    def test_cache_hit_makes_no_api_call(self, client):
        client._label_cache["me@gmail.com"] = {"financial-statements": "Label_1"}

        with patch.object(client, "_api_call") as mock_call:
            found = client.find_label("me@gmail.com", "financial-statements")

        assert found == {"id": "Label_1", "name": "financial-statements"}
        mock_call.assert_not_called()

    def test_cache_miss_found_via_list(self, client):
        with patch.object(
            client,
            "_api_call",
            return_value={"labels": [{"id": "Label_1", "name": "financial-statements", "type": "user"}]},
        ) as mock_call:
            found = client.find_label("me@gmail.com", "financial-statements")

        assert found == {"id": "Label_1", "name": "financial-statements"}
        mock_call.assert_called_once_with("me@gmail.com", "GET", "labels")

    def test_genuinely_not_found_returns_none(self, client):
        with patch.object(client, "_api_call", return_value={"labels": []}) as mock_call:
            found = client.find_label("me@gmail.com", "financial-statements")

        assert found is None
        mock_call.assert_called_once_with("me@gmail.com", "GET", "labels")


class TestResolveLabelIdRefactorRegression:
    """Issue #85: resolve_label_id() was refactored to delegate its lookup
    to find_label() - these confirm the refactor didn't change behavior.
    """

    def test_still_creates_exactly_once_on_genuine_miss(self, client):
        def fake_api_call(email, method, path, body=None):
            if method == "GET":
                return {"labels": []}
            return {"id": "Label_9", "name": "financial-statements"}

        with patch.object(client, "_api_call", side_effect=fake_api_call) as mock_call:
            label_id, created = client.resolve_label_id("me@gmail.com", "financial-statements")

        assert label_id == "Label_9"
        assert created is True
        assert mock_call.call_count == 2  # one GET (via find_label), one POST (create)

    def test_cache_hit_avoids_any_api_call(self, client):
        client._label_cache["me@gmail.com"] = {"financial-statements": "Label_1"}

        with patch.object(client, "_api_call") as mock_call:
            label_id, created = client.resolve_label_id("me@gmail.com", "financial-statements")

        assert label_id == "Label_1"
        assert created is False
        mock_call.assert_not_called()


class TestDeleteLabel:
    """Issue #85: delete_label() finds the label first (no API call on a
    not-found name), then DELETEs and purges it from the cache.
    """

    def test_found_issues_delete_and_purges_cache(self, client):
        client._label_cache["me@gmail.com"] = {"financial-statements": "Label_1"}

        with patch.object(client, "_api_call", return_value={}) as mock_call:
            result = client.delete_label("me@gmail.com", "financial-statements")

        mock_call.assert_called_once_with("me@gmail.com", "DELETE", "labels/Label_1")
        assert result == {"id": "Label_1", "name": "financial-statements"}
        assert "financial-statements" not in client._label_cache["me@gmail.com"]

    def test_not_found_makes_zero_api_calls(self, client):
        with patch.object(client, "_api_call", return_value={"labels": []}) as mock_call:
            result = client.delete_label("me@gmail.com", "ghost-label")

        assert result is None
        # find_label's list refresh is the only call - never a DELETE.
        mock_call.assert_called_once_with("me@gmail.com", "GET", "labels")


class TestRenameLabel:
    """Issue #85: rename_label() finds the label first (no API call on a
    not-found name), then PATCHes the same id and updates the cache.
    """

    def test_found_issues_patch_and_updates_cache(self, client):
        client._label_cache["me@gmail.com"] = {"financial-statements": "Label_1"}

        with patch.object(client, "_api_call", return_value={"id": "Label_1", "name": "statements"}) as mock_call:
            result = client.rename_label("me@gmail.com", "financial-statements", "statements")

        mock_call.assert_called_once_with(
            "me@gmail.com", "PATCH", "labels/Label_1", {"id": "Label_1", "name": "statements"}
        )
        assert result == {"id": "Label_1", "name": "statements"}
        cache = client._label_cache["me@gmail.com"]
        assert "financial-statements" not in cache
        assert cache["statements"] == "Label_1"

    def test_not_found_makes_zero_api_calls(self, client):
        with patch.object(client, "_api_call", return_value={"labels": []}) as mock_call:
            result = client.rename_label("me@gmail.com", "ghost-label", "new-name")

        assert result is None
        # find_label's list refresh is the only call - never a PATCH.
        mock_call.assert_called_once_with("me@gmail.com", "GET", "labels")
