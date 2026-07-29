"""Gmail API integration - proper archive/delete/label operations.

Handles OAuth2 token management and Gmail REST API calls for operations
that Mail.app/ScriptingBridge can't do (archive = remove INBOX label).
"""

import base64
import json
import logging
import time
import webbrowser
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "mail-tools"
CREDENTIALS_FILE = CONFIG_DIR / "gmail_credentials.json"
TOKENS_FILE = CONFIG_DIR / "gmail_tokens.json"

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"

# Gmail's messages.batchModify endpoint accepts at most 1000 ids per call.
BATCH_MODIFY_LIMIT = 1000

# Gmail's messages.list endpoint returns at most 500 ids per page.
SEARCH_PAGE_SIZE = 500

# Backoff for a rate-limited (429/quota-403) chunk failure: retried with
# exponential delay rather than immediately fanning out into per-message
# calls, which would pile hundreds of new requests on top of the very quota
# pressure that caused the failure.
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_BASE_DELAY_SECONDS = 1


class GmailError(Exception):
    """Raised when Gmail API operations fail.

    Carries the HTTP status code (when known) so callers can distinguish a
    rate-limit/quota failure (429, or a quota-flavored 403) from a genuine
    per-message error (e.g. a 400 for one bad id in a batch) - the two need
    very different retry strategies.

    Also carries token_revoked (issue #57) so check_live can identify a
    confirmed-dead refresh token structurally, rather than string-matching
    the error message - a message-wording change would otherwise silently
    break dead-token detection.
    """

    def __init__(self, message: str, status_code: int | None = None, token_revoked: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.token_revoked = token_revoked


def _is_rate_limited(error: GmailError) -> bool:
    """True if a GmailError represents a rate-limit/quota failure.

    429 is always a rate limit. Gmail also returns 403 for quota/rate
    exhaustion (e.g. userRateLimitExceeded, rateLimitExceeded) alongside
    unrelated 403s (permission errors), so those are only treated as
    rate-limited when the error body mentions quota/rate.
    """
    if error.status_code == 429:
        return True
    if error.status_code == 403:
        message = str(error).lower()
        return "quota" in message or "rate" in message
    return False


def _decode_body_data(data: str | None) -> str:
    """Decode one MIME part's base64url-encoded body data (issue #87).

    Gmail's API returns body.data as unpadded base64url; urlsafe_b64decode
    requires a multiple-of-4 length, so padding is added back before
    decoding. Malformed/undecodable data returns "" rather than raising -
    a single bad part should never fail the whole message read.
    """
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _find_plain_text_part(payload: dict) -> str:
    """Depth-first search for a text/plain part ONLY - never falls back to
    text/html, at any level of recursion (issue #87 code-review follow-up).

    This is pass 1 of _extract_plain_text_body's two-pass MIME walk. A
    single-pass walk that recurses into a nested multipart and accepts
    THAT subtree's own html fallback returns the wrong content when a
    message has multiple nested multipart siblings and only a LATER
    sibling has actual plain text - e.g. multipart/mixed containing a
    multipart/related (inline images, html only, no plain part) ordered
    before a multipart/alternative (has both plain and html): the first
    sibling's html-fallback result would win, and the second sibling's
    real plain-text part would never even be inspected. Sibling order
    depends on the sending mail client, not on anything this code
    controls, so this is a real-world shape, not a contrived edge case.

    By searching for plain text ONLY across every sibling before any html
    fallback is considered anywhere (see _extract_plain_text_body's pass
    2), plain text anywhere in the tree wins over html anywhere in the
    tree, not just within one sibling's own subtree.
    """
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    parts = payload.get("parts", [])

    if mime_type == "text/plain" and body.get("data"):
        return _decode_body_data(body["data"])

    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return _decode_body_data(part["body"]["data"])

    for part in parts:
        if part.get("mimeType", "").startswith("multipart/"):
            found = _find_plain_text_part(part)
            if found:
                return found

    return ""


def _extract_plain_text_body(payload: dict) -> str:
    """Depth-first search of a Gmail message payload for its text body
    (issue #87), preferring text/plain and falling back to text/html.

    Handles the common shapes: a single-part message with the body
    directly on the payload, and a multipart message (typically
    multipart/alternative for plain+html, sometimes nested inside
    multipart/mixed alongside attachments) with the body in payload.parts.

    Two-pass walk (issue #87 code-review follow-up): pass 1
    (_find_plain_text_part) searches the ENTIRE tree for plain text with
    no html fallback anywhere, so a later sibling's plain-text part is
    never shadowed by an earlier sibling's html-only fallback - see
    _find_plain_text_part's docstring for the concrete multi-sibling
    scenario this fixes. Only when pass 1 comes up completely empty does
    pass 2 fall back to html, first at this level and then by recursing
    into nested multiparts again (now allowed to accept an html result).
    """
    found = _find_plain_text_part(payload)
    if found:
        return found

    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    parts = payload.get("parts", [])

    if mime_type == "text/html" and body.get("data"):
        return _decode_body_data(body["data"])
    for part in parts:
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            return _decode_body_data(part["body"]["data"])

    # Pass 2: no plain text anywhere in the tree (pass 1 exhausted every
    # sibling) and no direct/top-level html either - recurse into nested
    # multiparts again, now allowed to accept an html fallback from
    # within a sibling's own subtree.
    for part in parts:
        if part.get("mimeType", "").startswith("multipart/"):
            found = _extract_plain_text_body(part)
            if found:
                return found

    # Single-part message with no explicit Content-Type but a body
    # anyway - return it rather than nothing.
    if not parts and body.get("data"):
        return _decode_body_data(body["data"])

    return ""


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler to capture OAuth2 redirect."""

    code = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        _OAuthCallbackHandler.code = query.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><h2>Authorization complete.</h2><p>You can close this tab.</p></body></html>")

    def log_message(self, format, *args):
        pass  # Suppress request logging


class GmailClient:
    """Gmail API client with OAuth2 token management.

    Stores per-account tokens in gmail_tokens.json keyed by email.
    """

    def __init__(self):
        self._credentials = self._load_credentials()
        self._tokens = self._load_tokens()
        # email -> {label_name: label_id}, lazily populated by list_labels()/
        # resolve_label_id() - see resolve_label_id's docstring for why this
        # lives on the instance rather than being re-fetched every call.
        self._label_cache: dict[str, dict[str, str]] = {}

    @staticmethod
    def is_available() -> bool:
        """Check if Gmail API credentials are configured."""
        return CREDENTIALS_FILE.exists()

    def _load_credentials(self) -> dict:
        if not CREDENTIALS_FILE.exists():
            return {}
        with open(CREDENTIALS_FILE) as f:
            data = json.load(f)
        return data.get("installed", data.get("web", {}))

    def _load_tokens(self) -> dict:
        """Load per-account tokens: {email: {access_token, refresh_token, ...}}"""
        if not TOKENS_FILE.exists():
            return {}
        with open(TOKENS_FILE) as f:
            return json.load(f)

    def _save_tokens(self):
        """Write gmail_tokens.json atomically - same temp-file+rename pattern
        as rules.save_rules(), so an interrupted write never leaves a
        truncated/invalid token file (this one holds every account's
        refresh_token, so corruption is worse than sender_rules.json's).
        """
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = TOKENS_FILE.with_suffix(".json.tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(self._tokens, f, indent=2)
                f.write("\n")
            tmp.replace(TOKENS_FILE)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def is_authorized(self, email: str) -> bool:
        """Check if we have a valid (or refreshable) token for an email.

        Case-insensitive (Gmail addresses aren't case-sensitive), matching
        _account_matches()'s existing case-insensitive convention for
        account filters elsewhere in this package. A case mismatch here
        used to fall through to MailManager.get_unread_count()'s
        ScriptingBridge/Mail.app-launch path - exactly what issue #101
        was fixed to avoid - for no reason other than input casing.
        """
        email_lower = email.lower()
        return any(stored.lower() == email_lower and "refresh_token" in info for stored, info in self._tokens.items())

    def check_live(self, email: str) -> dict:
        """Verify email's token actually works via a lightweight authenticated
        call (users.getProfile), rather than just checking a refresh_token key
        exists in gmail_tokens.json (issue #57 - a revoked/expired token was
        reporting identical to a live one, e.g. after Google's "Testing" OAuth
        publishing status silently expires an unused refresh token after 7
        days).

        Returns {"live": True} on success, or {"live": False, "reason": ...}
        when the token is genuinely dead - _api_call's own 401 retry already
        tried and failed to refresh it, so this cannot be a merely-expired
        access token still recoverable via refresh.

        Dead-token detection is structural (GmailError.token_revoked), not a
        string match against the error message - matching on wording would
        silently stop working the moment that message changes.

        Any OTHER failure (network outage, transient API/quota error) is
        deliberately NOT caught here - it propagates as GmailError/OSError so
        callers (gmail_status) can report "couldn't check" distinctly from
        "confirmed dead", instead of misreporting a network hiccup as a dead
        token.
        """
        try:
            self._api_call(email, "GET", "profile")
            return {"live": True}
        except GmailError as e:
            if e.token_revoked:
                return {"live": False, "reason": str(e)}
            raise

    def authorize(self, email: str) -> dict:
        """Run OAuth2 flow for a Gmail account. Opens browser for consent.

        Returns: {authorized: True, email}
        """
        if not self._credentials:
            raise GmailError(f"No Gmail credentials found. Place credentials at {CREDENTIALS_FILE}")

        client_id = self._credentials["client_id"]
        client_secret = self._credentials["client_secret"]

        # Start local server for OAuth callback
        server = HTTPServer(("localhost", 0), _OAuthCallbackHandler)
        port = server.server_address[1]
        redirect_uri = f"http://localhost:{port}"

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "login_hint": email,
        }

        auth_url = f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"
        logger.info("Opening browser for Gmail authorization: %s", email)
        webbrowser.open(auth_url)

        # Wait for callback (timeout after 120s)
        server.timeout = 120
        _OAuthCallbackHandler.code = None

        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()
        thread.join(timeout=120)
        server.server_close()

        code = _OAuthCallbackHandler.code
        if not code:
            raise GmailError("OAuth authorization timed out or was cancelled")

        # Exchange code for tokens
        token_data = self._exchange_code(code, redirect_uri, client_id, client_secret)
        self._tokens[email] = {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "token_type": token_data.get("token_type", "Bearer"),
        }
        self._save_tokens()

        return {"authorized": True, "email": email}

    def _exchange_code(self, code, redirect_uri, client_id, client_secret) -> dict:
        """Exchange authorization code for access/refresh tokens."""
        data = urlencode(
            {
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
        ).encode()

        req = Request(
            "https://oauth2.googleapis.com/token",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = urlopen(req)
        return json.loads(resp.read())

    def _refresh_token(self, email: str, _reloaded: bool = False) -> str:
        """Refresh an expired access token.

        If the in-memory refresh token is revoked/expired, reload from disk and
        retry once - handles the case where another GmailClient instance (e.g.
        the one used by gmail_authorize) already saved fresh tokens to disk.
        """
        token_info = self._tokens.get(email, {})
        refresh_token = token_info.get("refresh_token")
        if not refresh_token:
            raise GmailError(f"No refresh token for {email}. Re-authorize with gmail_authorize.")

        data = urlencode(
            {
                "client_id": self._credentials["client_id"],
                "client_secret": self._credentials["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode()

        req = Request(
            "https://oauth2.googleapis.com/token",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            resp = urlopen(req)
            result = json.loads(resp.read())
        except HTTPError as e:
            if e.code == 400 and not _reloaded:
                # In-memory refresh token may be stale - reload from disk and retry.
                fresh_tokens = self._load_tokens()
                fresh_rt = fresh_tokens.get(email, {}).get("refresh_token", "")
                if fresh_rt and fresh_rt != refresh_token:
                    self._tokens = fresh_tokens
                    return self._refresh_token(email, _reloaded=True)
            error_body = e.read().decode() if e.fp else str(e)
            raise GmailError(f"Token refresh failed for {email}: {error_body}", token_revoked=True)

        self._tokens[email]["access_token"] = result["access_token"]
        self._save_tokens()
        return result["access_token"]

    def _api_call(self, email: str, method: str, path: str, body: dict | None = None, retry: bool = True) -> dict:
        """Make a Gmail API call with auto-refresh."""
        token_info = self._tokens.get(email, {})
        access_token = token_info.get("access_token")
        if not access_token:
            # A refresh_token with no access_token is a partial/corrupted
            # entry that can never succeed without re-authorization - treat
            # it the same as a confirmed-dead token (issue #57 follow-up).
            raise GmailError(
                f"No access token for {email}. Run gmail_authorize first.",
                token_revoked=bool(token_info.get("refresh_token")),
            )

        url = f"{GMAIL_API}/users/me/{path}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        req = Request(url, method=method, headers=headers)
        if body:
            req.data = json.dumps(body).encode()

        try:
            resp = urlopen(req)
            if resp.status == 204:
                return {}
            return json.loads(resp.read())
        except HTTPError as e:
            if e.code == 401 and retry:
                # Token expired - refresh and retry
                self._refresh_token(email)
                return self._api_call(email, method, path, body, retry=False)
            error_body = e.read().decode() if e.fp else str(e)
            raise GmailError(f"Gmail API error {e.code}: {error_body}", status_code=e.code)

    def _find_message_id(self, email: str, rfc_message_id: str) -> str | None:
        """Find Gmail internal message ID from RFC Message-ID header."""
        # Gmail search uses rfc822msgid: operator.
        # URL-encode the message ID so characters like @ don't break the query.
        clean_id = rfc_message_id.strip("<>")
        encoded_query = quote(f"rfc822msgid:{clean_id}", safe=":")
        result = self._api_call(email, "GET", f"messages?q={encoded_query}&maxResults=1")
        messages = result.get("messages", [])
        return messages[0]["id"] if messages else None

    def search_ids(self, email: str, query: str, max_results: int | None = None) -> list[str]:
        """Search Gmail with its own query syntax, returning internal message ids directly.

        Unlike _find_message_id (which translates a single known rfc822
        Message-ID into a Gmail-internal id), this issues a query search
        that can match thousands of messages and paginates through
        nextPageToken (up to SEARCH_PAGE_SIZE ids per page) to collect all
        of them - no rfc822 translation involved at all, since the ids come
        straight back from Gmail's own index.

        Paginating is cheap: messages.list costs 5 quota units per call
        regardless of page size, so even 1000+ matches costs a handful of
        calls, not one per message.
        """
        encoded_query = quote(query)
        ids: list[str] = []
        page_token = None
        while True:
            page_size = SEARCH_PAGE_SIZE
            if max_results is not None:
                remaining = max_results - len(ids)
                if remaining <= 0:
                    break
                page_size = min(page_size, remaining)

            path = f"messages?q={encoded_query}&maxResults={page_size}"
            if page_token:
                path += f"&pageToken={page_token}"

            result = self._api_call(email, "GET", path)
            messages = result.get("messages", [])
            ids.extend(m["id"] for m in messages)

            page_token = result.get("nextPageToken")
            if not page_token or not messages:
                break

        if max_results is not None:
            ids = ids[:max_results]
        return ids

    def search_messages_preview(self, email: str, query: str, sample_size: int = 10) -> dict:
        """Dry-run preview for a bulk query: exact match count plus a small sample.

        Runs search_ids() fully first - paginated messages.list calls only,
        cheap even at thousands of matches - to get an exact matched_count
        (not Gmail's own resultSizeEstimate, which is only approximate).
        Metadata (subject/from/date) is then fetched via individual
        messages.get calls for ONLY the first `sample_size` ids, never for
        the full matched set - fetching metadata for every match would
        defeat the entire point of avoiding per-message calls at scale.
        """
        gmail_ids = self.search_ids(email, query)
        sample = [self._get_message_preview(email, gmail_id) for gmail_id in gmail_ids[:sample_size]]
        return {"matched_count": len(gmail_ids), "sample": sample}

    def _get_message_preview(self, email: str, gmail_id: str) -> dict:
        """Fetch subject/from/date metadata for one message id (sample use only)."""
        path = f"messages/{gmail_id}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date"
        result = self._api_call(email, "GET", path)
        headers = {h["name"]: h["value"] for h in result.get("payload", {}).get("headers", [])}
        return {
            "message_id": gmail_id,
            "subject": headers.get("Subject"),
            "from": headers.get("From"),
            "date": headers.get("Date"),
        }

    def archive_messages(self, email: str, rfc_message_ids: list[str]) -> list[dict]:
        """Archive multiple Gmail messages via a single batched INBOX/TRASH label removal.

        Smoke-tested 2026-07-01 against a live Gmail account (issue #46):
        inserted 3 disposable test messages, archived one first (removing its
        INBOX label out of band), then called batchModify with
        removeLabelIds=["INBOX", "TRASH"] across all three in one request.
        The call succeeded silently - no error - and correctly no-op'd the
        label removal on the message that already lacked INBOX. That confirms
        batchModify is safe to use unconditionally here, same as
        delete_messages/mark_read_messages/mark_unread_messages below.

        Resolves each rfc822 Message-ID to a Gmail-internal id (one GET per
        id - unavoidable given rfc822 ids as input), then issues one chunked
        batchModify call per <=1000 ids instead of a GET-labels-then-modify
        POST per message.
        """
        results = self._batch_modify_by_rfc_ids(email, rfc_message_ids, remove_label_ids=["INBOX", "TRASH"])
        for r in results:
            r["archived"] = r.pop("modified")
            if r["archived"]:
                r["destination"] = "All Mail"
        return results

    def delete_messages(self, email: str, rfc_message_ids: list[str]) -> list[dict]:
        """Delete multiple Gmail messages via a single batched TRASH label add.

        Resolves each rfc822 Message-ID to a Gmail-internal id (one GET per
        id - unavoidable given rfc822 ids as input), then issues one chunked
        batchModify call per <=1000 ids instead of a trash() POST per message.
        """
        results = self._batch_modify_by_rfc_ids(
            email, rfc_message_ids, add_label_ids=["TRASH"], remove_label_ids=["INBOX"]
        )
        for r in results:
            r["deleted"] = r.pop("modified")
        return results

    def batch_modify(
        self,
        email: str,
        gmail_ids: list[str],
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> list[str]:
        """Batch-modify labels on Gmail-internal message ids, chunked to <=1000 per call.

        Costs a flat 50 quota units per chunk regardless of chunk size, vs.
        5 units per message via individual modify() calls - the difference
        that keeps bulk operations (1000+ messages) under Gmail's rate limits.

        If a chunk-level call fails with a rate-limit/quota error (429, or a
        quota-flavored 403), it is retried with exponential backoff - NOT
        fanned out into per-message calls, since that would add hundreds of
        new requests at the exact moment quota pressure is highest. After
        RATE_LIMIT_MAX_RETRIES the GmailError is re-raised so the caller sees
        the failure instead of it being silently swallowed.

        Any other chunk-level failure (e.g. one bad id causing a 400) is
        retried per-message, so a single bad id doesn't drop the whole chunk.

        Returns the list of gmail_ids that were successfully modified.
        """
        if not gmail_ids:
            return []

        body = {}
        if add_label_ids:
            body["addLabelIds"] = add_label_ids
        if remove_label_ids:
            body["removeLabelIds"] = remove_label_ids

        succeeded = []
        for i in range(0, len(gmail_ids), BATCH_MODIFY_LIMIT):
            chunk = gmail_ids[i : i + BATCH_MODIFY_LIMIT]
            attempt = 0
            while True:
                try:
                    self._api_call(email, "POST", "messages/batchModify", {**body, "ids": chunk})
                    succeeded.extend(chunk)
                    break
                except GmailError as e:
                    if _is_rate_limited(e):
                        attempt += 1
                        if attempt > RATE_LIMIT_MAX_RETRIES:
                            logger.error(
                                "batchModify rate-limited for chunk of %d ids after %d retries, giving up: %s",
                                len(chunk),
                                RATE_LIMIT_MAX_RETRIES,
                                e,
                            )
                            raise
                        delay = RATE_LIMIT_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                        logger.warning(
                            "batchModify rate-limited (attempt %d/%d) for chunk of %d ids, backing off %ds: %s",
                            attempt,
                            RATE_LIMIT_MAX_RETRIES,
                            len(chunk),
                            delay,
                            e,
                        )
                        time.sleep(delay)
                        continue
                    logger.warning("batchModify failed for chunk of %d ids, retrying per-message: %s", len(chunk), e)
                    for gmail_id in chunk:
                        try:
                            self._api_call(email, "POST", f"messages/{gmail_id}/modify", body)
                            succeeded.append(gmail_id)
                        except GmailError:
                            continue
                    break
        return succeeded

    def modify_by_gmail_id(
        self,
        email: str,
        gmail_ids: list[str],
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> list[dict]:
        """Batch-modify labels on ids that are ALREADY known Gmail-internal
        ids (issue #89) - unlike _batch_modify_by_rfc_ids(), this performs no
        rfc822 Message-ID resolution step.

        Used by MailManager's Pass 3 direct-id lookup fallback:
        message_exists() has already confirmed which account owns a
        Gmail-internal id that Pass 1's INBOX scan and Pass 2's rfc822msgid:
        search couldn't resolve. Re-running that id through
        archive_messages()/delete_messages()/mark_read_messages()/
        mark_unread_messages() (all of which call _batch_modify_by_rfc_ids()
        and treat their input as an rfc822 Message-ID to search for via
        rfc822msgid:) would search for a bare Gmail-internal hex id using an
        operator that only matches actual RFC 5322 Message-IDs - the exact
        failure mode this issue exists to fix. This method calls
        batch_modify() directly with the id as-is.

        Returns one {"message_id", "modified", "account"} dict per input id,
        the same shape _batch_modify_by_rfc_ids() returns before its own
        callers rename "modified" to "archived"/"deleted"/"marked" -
        MailManager's Pass 3 callers do that same renaming themselves.
        """
        succeeded = set(
            self.batch_modify(email, gmail_ids, add_label_ids=add_label_ids, remove_label_ids=remove_label_ids)
        )
        return [{"message_id": gid, "modified": gid in succeeded, "account": email} for gid in gmail_ids]

    def _batch_modify_by_rfc_ids(
        self,
        email: str,
        rfc_message_ids: list[str],
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> list[dict]:
        """Resolve RFC822 Message-IDs to Gmail-internal ids, then batch_modify.

        One _find_message_id GET per id (unavoidable - rfc822 ids are the
        input contract, and Gmail has no "modify by rfc822msgid" shortcut),
        followed by chunked batchModify calls instead of a modify POST per
        message. Returns per-message dicts with a "modified" bool.
        """
        id_map: dict[str, str] = {}  # gmail_id -> rfc_id
        results = []
        for rfc_id in rfc_message_ids:
            gmail_id = self._find_message_id(email, rfc_id)
            if gmail_id:
                id_map[gmail_id] = rfc_id
            else:
                results.append({"message_id": rfc_id, "modified": False, "reason": "not found via Gmail API"})

        succeeded = set(
            self.batch_modify(
                email, list(id_map.keys()), add_label_ids=add_label_ids, remove_label_ids=remove_label_ids
            )
        )

        for gmail_id, rfc_id in id_map.items():
            results.append({"message_id": rfc_id, "modified": gmail_id in succeeded, "account": email})

        return results

    def mark_read_messages(self, email: str, rfc_message_ids: list[str]) -> list[dict]:
        """Mark multiple Gmail messages read by batch-removing the UNREAD label.

        Each result dict carries "marked" as a bool (success/failure) and a
        separate "status" label ("read") naming the operation attempted -
        keeps "marked" a consistent type instead of a string on success and
        a bool on failure.
        """
        results = self._batch_modify_by_rfc_ids(email, rfc_message_ids, remove_label_ids=["UNREAD"])
        for r in results:
            r["marked"] = r.pop("modified")
            r["status"] = "read"
        return results

    def mark_unread_messages(self, email: str, rfc_message_ids: list[str]) -> list[dict]:
        """Mark multiple Gmail messages unread by batch-adding the UNREAD label.

        See mark_read_messages() for the "marked"/"status" field shape.
        """
        results = self._batch_modify_by_rfc_ids(email, rfc_message_ids, add_label_ids=["UNREAD"])
        for r in results:
            r["marked"] = r.pop("modified")
            r["status"] = "unread"
        return results

    def list_authorized_accounts(self) -> list[str]:
        """List emails that have been authorized."""
        return [email for email, t in self._tokens.items() if "refresh_token" in t]

    # ── Sending (issue #50) ──

    def _build_mime_raw(
        self,
        from_email: str,
        to: str,
        subject: str,
        body: str,
        cc: str | None = None,
        bcc: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> str:
        """Build an RFC 2822 MIME message and base64url-encode it for Gmail's
        `raw` send/draft field, per Gmail's sending guide.

        In-Reply-To/References are only set when replying (both callers pass
        the original message's rfc822 Message-ID for each) - each is
        stripped of any surrounding angle brackets first, then re-wrapped as
        `<id>`, so a caller passing either a bare id or an already-bracketed
        one produces the same well-formed header either way.
        """
        msg = EmailMessage()
        msg["From"] = from_email
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = f"<{in_reply_to.strip('<>')}>"
        if references:
            msg["References"] = f"<{references.strip('<>')}>"
        msg.set_content(body)
        return base64.urlsafe_b64encode(msg.as_bytes()).decode()

    def send_message(
        self,
        email: str,
        to: str,
        subject: str,
        body: str,
        cc: str | None = None,
        bcc: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
        thread_id: str | None = None,
    ) -> dict:
        """Send a message via Gmail API (messages.send). 100 quota units/call.

        Passing thread_id keeps the message in an existing Gmail conversation
        view - Gmail's own threadId is what actually does that, not a full
        References chain (ScriptingBridge doesn't expose the original
        message's own References header to build one).
        """
        raw = self._build_mime_raw(
            email, to, subject, body, cc=cc, bcc=bcc, in_reply_to=in_reply_to, references=references
        )
        payload = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        result = self._api_call(email, "POST", "messages/send", payload)
        return {"to": to, "subject": subject, "status": "sent", "gmail_id": result.get("id")}

    def create_draft(
        self,
        email: str,
        to: str,
        subject: str,
        body: str,
        cc: str | None = None,
        bcc: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
        thread_id: str | None = None,
    ) -> dict:
        """Create a draft via Gmail API (drafts.create).

        Needed because mail_compose/mail_reply default to draft-only via a
        `send` bool - the same raw-message construction as send_message,
        just wrapped in drafts.create's {"message": {...}} envelope.
        """
        raw = self._build_mime_raw(
            email, to, subject, body, cc=cc, bcc=bcc, in_reply_to=in_reply_to, references=references
        )
        message_payload = {"raw": raw}
        if thread_id:
            message_payload["threadId"] = thread_id
        result = self._api_call(email, "POST", "drafts", {"message": message_payload})
        return {"to": to, "subject": subject, "status": "draft", "gmail_draft_id": result.get("id")}

    def resolve_thread(self, email: str, rfc_message_id: str) -> dict | None:
        """Resolve an rfc822 Message-ID to its Gmail-internal id and threadId,
        for reply threading.

        One _find_message_id GET (rfc822msgid: search) plus one minimal
        messages.get - cheap, and the only way to learn threadId from an
        rfc822 Message-ID. Returns None (not an exception) when the message
        isn't found via the Gmail API, so callers can fall back to sending
        unthreaded rather than failing the whole reply.
        """
        gmail_id = self._find_message_id(email, rfc_message_id)
        if gmail_id is None:
            return None
        result = self._api_call(email, "GET", f"messages/{gmail_id}?format=minimal")
        return {"gmail_id": gmail_id, "thread_id": result.get("threadId")}

    # ── Listing/search (issue #50) ──

    def _get_message_metadata(self, email: str, gmail_id: str) -> dict:
        """Fetch subject/from/date/read/flagged metadata for one message id.

        Mirrors the shape ScriptingBridge-based message serialization
        produces (message_id/subject/from/date/read/flagged - see
        mail.py's _batch_serialize_messages) so downstream routing can
        treat Gmail-API and ScriptingBridge results interchangeably: the
        raw RFC 2822 Date header is parsed and reformatted as ISO 8601 to
        match _batch_serialize_messages's `datetime...isoformat()` output,
        and subject falls back to "(no subject)" when empty/missing,
        matching ScriptingBridge's convention.
        read/flagged are derived from labelIds: UNREAD absent means read,
        STARRED present means flagged.
        """
        path = f"messages/{gmail_id}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date"
        result = self._api_call(email, "GET", path)
        headers = {h["name"]: h["value"] for h in result.get("payload", {}).get("headers", [])}
        label_ids = result.get("labelIds", [])

        date_str = None
        raw_date = headers.get("Date")
        if raw_date:
            try:
                date_str = parsedate_to_datetime(raw_date).astimezone().isoformat()
            except (TypeError, ValueError):
                pass

        return {
            "message_id": gmail_id,
            "subject": headers.get("Subject") or "(no subject)",
            "from": headers.get("From"),
            "date": date_str,
            "read": "UNREAD" not in label_ids,
            "flagged": "STARRED" in label_ids,
        }

    def list_messages(self, email: str, mailbox: str = "INBOX", limit: int = 20) -> list[dict]:
        """List messages in a Gmail mailbox via the API, most recent first.

        `mailbox` maps to a Gmail label query - "INBOX" uses Gmail's own
        INBOX label directly; anything else goes through a raw `in:<mailbox>`
        query. Fetches at most `limit` ids via a single messages.list call
        (maxResults=limit, no pagination - unlike search_ids()'s exhaustive
        pagination), then one metadata GET per id.
        """
        path = f"messages?maxResults={limit}"
        if mailbox.upper() == "INBOX":
            path += "&labelIds=INBOX"
        else:
            mailbox_query = 'in:"' + mailbox.lower() + '"'
            path += f"&q={quote(mailbox_query)}"
        result = self._api_call(email, "GET", path)
        gmail_ids = [m["id"] for m in result.get("messages", [])][:limit]
        return [self._get_message_metadata(email, gid) for gid in gmail_ids]

    def search_messages(self, email: str, query: str, limit: int = 20) -> list[dict]:
        """Search Gmail with its own query syntax via the API, capped at
        `limit` results.

        Unlike search_ids() (built for exhaustive counts via full
        pagination), this issues a single messages.list call with
        maxResults=limit - callers here want a bounded result page, not an
        exact total.
        """
        path = f"messages?q={quote(query)}&maxResults={limit}"
        result = self._api_call(email, "GET", path)
        gmail_ids = [m["id"] for m in result.get("messages", [])][:limit]
        return [self._get_message_metadata(email, gid) for gid in gmail_ids]

    def get_message(self, email: str, gmail_id: str) -> dict | None:
        """Fetch full message content (metadata + body) for one Gmail-internal
        message id in a single account (issue #87).

        Used by MailManager.read_message() to resolve a Gmail-internal id
        as returned by list_messages()/search_messages()/search_ids() for
        Gmail-authorized accounts - ScriptingBridge's messageId()-based
        lookup can never match these (they are not rfc822 Message-IDs), so
        this fetches directly by Gmail's own id (format=full, which
        includes the body) instead of translating through rfc822msgid:
        like _find_message_id() does for the reverse direction.

        Returns None (not an exception) for a 404 - "this account doesn't
        have this id" is an expected, common outcome when a caller tries
        several authorized accounts in turn (mail_read has no account
        parameter to scope the lookup to one account upfront), not a
        genuine failure. Any other GmailError (auth, network, quota)
        propagates so callers can distinguish "not this account" from
        "this account's connection is broken" - see
        MailManager._call_gmail_batch, which read_message() uses to catch
        and skip on either outcome.

        The returned "content" is the full plain-text body, untruncated -
        MailManager.read_message() applies MAX_CONTENT_LENGTH truncation
        uniformly for both this and the ScriptingBridge path.
        """
        try:
            result = self._api_call(email, "GET", f"messages/{gmail_id}?format=full")
        except GmailError as e:
            if e.status_code == 404:
                return None
            raise

        payload = result.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        label_ids = result.get("labelIds", [])

        date_str = None
        raw_date = headers.get("Date")
        if raw_date:
            try:
                date_str = parsedate_to_datetime(raw_date).astimezone().isoformat()
            except (TypeError, ValueError):
                pass

        return {
            "message_id": gmail_id,
            "subject": headers.get("Subject") or "(no subject)",
            "from": headers.get("From"),
            "date": date_str,
            "read": "UNREAD" not in label_ids,
            "flagged": "STARRED" in label_ids,
            "content": _extract_plain_text_body(payload),
        }

    def message_exists(self, email: str, gmail_id: str) -> bool:
        """Lightweight existence/ownership check for one Gmail-internal
        message id in a single account (issue #89).

        Used by MailManager's Pass 3 direct-id lookup fallback (shared by
        archive_messages/delete_messages/_mark_messages) to find which
        Gmail-authorized account owns an id that neither Pass 1's per-account
        INBOX scan nor Pass 2's rfc822msgid: search could resolve - the
        common case being a message that no longer carries the INBOX label
        (already archived, or filtered straight to a category/label on
        arrival) and is identified by a bare Gmail-internal hex id rather
        than an RFC 5322 Message-ID (rfc822msgid: only matches the latter).

        Uses format=minimal - the cheapest fetch that still confirms the id
        resolves on this account (just id/threadId/labelIds, no headers or
        body) - since the caller only needs to confirm ownership before
        issuing its own batchModify with the desired label changes, not the
        message's content or metadata like get_message() fetches.

        Returns False (not an exception) for a 404, same contract as
        get_message() - "this account doesn't have this id" is an expected
        outcome when trying several authorized accounts in turn, not a
        genuine failure. Any other GmailError (auth, network, quota)
        propagates so callers can distinguish "not this account" from "this
        account's connection is broken" via MailManager._call_gmail_batch.
        """
        try:
            self._api_call(email, "GET", f"messages/{gmail_id}?format=minimal")
            return True
        except GmailError as e:
            if e.status_code == 404:
                return False
            raise

    def get_unread_counts(self, email: str) -> list[dict]:
        """Live per-label unread counts for an account (issue #101).

        Replaces MailManager.get_unread_count()'s ScriptingBridge
        mb.unreadCount() for Gmail-authorized accounts, to avoid IMAP-sync
        lag as a general class of problem and the Mail.app-launch
        dependency entirely for a call scoped to one Gmail-authorized
        account.

        users.labels.list (list_labels()) does not include unread counts,
        only id/name/type - only users.labels.get returns messagesTotal/
        messagesUnread/threadsTotal/threadsUnread for one label at a time,
        so this issues one GET per label returned by list_labels().

        A single label's GET failing (transient network error, rate limit)
        is logged and that one label skipped, rather than discarding every
        other label's already-fetched count for the same account - the
        caller only sees a fully empty/zero result on a failure that
        affects every label (e.g. a dead token, caught by
        MailManager._call_gmail_batch one level up), not on one flaky
        label among many healthy ones.

        Returns one {"mailbox": label_name, "unread": messagesUnread} entry
        per label with messagesUnread > 0 - mirrors
        MailManager.get_unread_count()'s ScriptingBridge shape (which also
        only reports mailboxes with unread > 0), so the JSON shape returned
        to callers is unaffected by which backend produced it.
        """
        results = []
        for label in self.list_labels(email):
            try:
                detail = self._api_call(email, "GET", f"labels/{label['id']}")
            except Exception as e:
                logger.warning("Failed to fetch unread count for label %s (%s): %s", label["name"], email, e)
                continue
            unread = detail.get("messagesUnread", 0)
            if unread:
                results.append({"mailbox": label["name"], "unread": unread})
        return results

    # ── Custom labels (issue #58) ──

    def list_labels(self, email: str) -> list[dict]:
        """List all labels (system + user-created) for an account.

        Wraps GET users/me/labels - each entry has id/name/type ("system"
        or "user"). Used to resolve a custom label name to its
        Gmail-internal id (see resolve_label_id) and to recover from a
        create_label() 409 by finding the label that already exists.
        """
        result = self._api_call(email, "GET", "labels")
        return result.get("labels", [])

    def create_label(self, email: str, name: str) -> dict:
        """Create a user label, treating "already exists" as success.

        POSTs users/me/labels with labelListVisibility/messageListVisibility
        both set to show - Gmail's own defaults leave a newly created label
        hidden from the label list and message list until a user manually
        surfaces it, which would make an applied label invisible in the
        Gmail UI even though the API call succeeded.

        A 409 means a label with this exact name already exists - a real
        possibility given resolve_label_id's cache-miss-then-create path
        can race across two calls/processes, or the label was created
        directly in Gmail's UI between this process's cache refresh and
        this call. Gmail has no upsert endpoint, so recovery is to re-list
        and return the existing label rather than raising - this is what
        makes label creation idempotent per issue #58's Definition of
        Done. If the re-list genuinely can't find a same-named label (a
        surprising state - the 409 said it exists), the original error is
        re-raised rather than silently swallowed.
        """
        try:
            return self._api_call(
                email,
                "POST",
                "labels",
                {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
            )
        except GmailError as e:
            if e.status_code == 409:
                for label in self.list_labels(email):
                    if label["name"] == name:
                        return label
            raise

    def find_label(self, email: str, name: str) -> dict | None:
        """Resolve a label name to {"id", "name"} without ever creating it.

        Checks the per-email _label_cache first (populated by an earlier
        list_labels()/resolve_label_id()/find_label() call within this
        process). On a cache miss, refreshes from list_labels() once -
        covers a label created outside this process (e.g. directly in
        Gmail's UI), which the cache has no other way to learn about - and
        returns None only if the name is still missing after that refresh.

        This is the cache-check-then-list-refresh half of what used to be
        resolve_label_id() (issue #85): resolve_label_id() now calls this
        first and only creates on a None result. delete_label(),
        rename_label(), and bulk_action()'s remove_label path share this
        same lookup, since none of them should ever create a label as a
        side effect of looking one up - see resolve_label_id()'s docstring
        for the caching contract (process-lifetime singleton, no lock).
        """
        cache = self._label_cache.setdefault(email, {})
        if name in cache:
            return {"id": cache[name], "name": name}

        for label in self.list_labels(email):
            cache[label["name"]] = label["id"]
        if name in cache:
            return {"id": cache[name], "name": name}

        return None

    def resolve_label_id(self, email: str, name: str) -> tuple[str, bool]:
        """Resolve a label name to its Gmail-internal id, creating it if needed.

        Returns (label_id, created) - created is True only when this call
        itself created the label, never when it was found via the cache or
        a fresh list.

        Delegates the lookup to find_label() (issue #85's refactor - no
        behavior change from before, just de-duplicated logic that
        delete_label()/rename_label()/bulk_action()'s remove_label path
        also need) and only calls create_label() on a None result.

        _label_cache lives on the GmailClient instance, which MailManager
        holds as a process-lifetime singleton (see mcp_server.py's
        _mail()), so repeated resolve_label_id() calls for the same name
        within one session cost at most one list_labels() call and one
        create_label() call total, not one round-trip per
        bulk_action()/apply_rules() invocation - true as long as the MCP
        SDK dispatches tool calls synchronously on one thread (it does
        today; the cache has no lock, so a future SDK version that runs
        sync tools in a thread pool could race two cache misses into an
        extra list/create call - harmless since create_label()'s 409
        recovery still converges on the same id, just no longer "exactly
        one" call).
        """
        found = self.find_label(email, name)
        if found is not None:
            return found["id"], False

        label = self.create_label(email, name)
        self._label_cache.setdefault(email, {})[name] = label["id"]
        return label["id"], True

    def delete_label(self, email: str, name: str) -> dict | None:
        """Delete a user label by exact name.

        find_label() first (issue #85) - a name that doesn't resolve, even
        after find_label()'s own fresh list_labels() refresh, short-circuits
        to None with zero API calls, rather than issuing a DELETE Gmail
        would 404 on. This is what makes MailManager.delete_label()'s
        "already gone" no-op possible without ever hitting the API twice
        for the same already-deleted name.

        Otherwise issues DELETE users/me/labels/{id}, then purges `name`
        from the per-email _label_cache so a subsequent
        find_label()/resolve_label_id() call for the same name doesn't
        return a stale, now-deleted id.

        This method can never touch a message's INBOX/UNREAD/TRASH state -
        it is structurally incapable of a batchModify call, since it only
        ever issues the one DELETE labels/{id} request.
        """
        found = self.find_label(email, name)
        if found is None:
            return None

        self._api_call(email, "DELETE", f"labels/{found['id']}")
        self._label_cache.get(email, {}).pop(name, None)
        return found

    def rename_label(self, email: str, old_name: str, new_name: str) -> dict | None:
        """Rename a user label in place, preserving its Gmail-internal id.

        find_label(old_name) first; None short-circuits to None with zero
        API calls, same guard as delete_label(). Otherwise PATCHes
        users/me/labels/{id} with the SAME id and the new name - Gmail has
        no separate rename endpoint, but a PATCH that only changes `name`
        never touches any message's labelIds, so no batchModify call is
        needed: every message that had the label keeps it, now under the
        new name.

        Cache: pops old_name, sets new_name -> the same id, so a
        subsequent resolve_label_id()/find_label() call by the new name
        resolves without re-listing or re-creating, and a call by the OLD
        name (now genuinely absent) correctly creates a brand new label
        rather than resurrecting the renamed one.
        """
        found = self.find_label(email, old_name)
        if found is None:
            return None

        result = self._api_call(email, "PATCH", f"labels/{found['id']}", {"id": found["id"], "name": new_name})
        cache = self._label_cache.setdefault(email, {})
        cache.pop(old_name, None)
        cache[new_name] = found["id"]
        return result
