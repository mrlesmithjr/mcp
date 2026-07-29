"""Shared Google OAuth2 client for MCP tools that call a Google REST API.

Generalizes the OAuth engine originally written for mail-tools' GmailClient
(mail_tools/gmail.py, issues #43-#58) so Calendar (issue #59) and Contacts
(issue #60) tools don't each duplicate the local-HTTPServer redirect-capture
flow, token refresh/retry dance, and atomic token-file writes.

Credentials (the OAuth *client* - one GCP app registration, shared across
every Google-using package) live at a single shared location:

    ~/.config/google/credentials.json

Copy the existing Gmail OAuth client credentials file here once - no new
GCP app registration is needed, just enabling the relevant API (Calendar,
People, ...) on the same GCP project and adding its scope(s) to the OAuth
consent screen.

Tokens (the per-account *grant* - scoped to whatever scopes were actually
authorized) are per-package, since a Calendar-scoped token and a
Contacts-scoped token are not interchangeable even for the same Google
account:

    ~/.config/<tool_name>/<token_filename>

mail-tools' own ~/.config/mail-tools/gmail_tokens.json is untouched by this
module - GmailClient is NOT retrofitted onto this base class in this pass.
"""

from __future__ import annotations

import json
import logging
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from mcp_common.paths import config_dir

logger = logging.getLogger(__name__)

_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleOAuthError(Exception):
    """Raised when a Google OAuth2 flow or an authenticated API call fails.

    Carries the HTTP status code (when known) so callers can distinguish a
    rate-limit/quota failure from a genuine per-request error, and
    token_revoked (mirroring mail-tools' GmailError, issue #57) so
    check_live() can identify a confirmed-dead refresh token structurally,
    rather than string-matching the error message - a message-wording
    change would otherwise silently break dead-token detection.
    """

    def __init__(self, message: str, status_code: int | None = None, token_revoked: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.token_revoked = token_revoked


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler to capture the OAuth2 redirect's `code` query param."""

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


class GoogleOAuthClient:
    """Generic Google OAuth2 client: token storage, refresh, and authenticated
    requests against any Google REST API.

    Subclasses (GoogleCalendarClient, GooglePeopleClient, ...) pass their own
    tool_name/scopes/token_filename to __init__ and build their API-specific
    methods on top of request().
    """

    def __init__(self, tool_name: str, scopes: list[str], token_filename: str = "google_tokens.json"):
        self.tool_name = tool_name
        self.scopes = scopes
        self.credentials_path = config_dir("google") / "credentials.json"
        self.tokens_path = config_dir(tool_name) / token_filename
        self._credentials = self._load_credentials()
        self._tokens = self._load_tokens()

    # ── Availability / status ──

    def is_available(self) -> bool:
        """True if the shared Google OAuth client credentials file exists."""
        return self.credentials_path.exists()

    def is_authorized(self, account_id: str) -> bool:
        """True if a refresh token is on file for account_id.

        Does not verify the token still works - see check_live() for that.
        """
        return account_id in self._tokens and "refresh_token" in self._tokens[account_id]

    def list_authorized_accounts(self) -> list[str]:
        """List account ids that have a refresh_token on file."""
        return [account for account, t in self._tokens.items() if "refresh_token" in t]

    def check_live(self, account_id: str, probe: Callable[[], None]) -> dict:
        """Verify account_id's token actually works via a caller-supplied
        cheap authenticated call, rather than just checking a refresh_token
        key exists on disk (mirrors GmailClient.check_live, issue #57 - a
        revoked/expired token otherwise reports identical to a live one).

        `probe` should be a zero-arg callable that issues one lightweight
        authenticated request and raises on failure (e.g. a `lambda:
        self.request(account_id, "GET", some_cheap_url)`).

        Returns {"live": True} on success, or {"live": False, "reason": ...}
        when the token is genuinely dead - request()'s own 401 retry already
        tried and failed to refresh it, so this cannot be a merely-expired
        access token still recoverable via refresh.

        Any OTHER failure (network outage, transient API error) is
        deliberately NOT caught here - it propagates as GoogleOAuthError so
        callers can report "couldn't check" distinctly from "confirmed dead".
        """
        try:
            probe()
            return {"live": True}
        except GoogleOAuthError as e:
            if e.token_revoked:
                return {"live": False, "reason": str(e)}
            raise

    # ── Credential / token file I/O ──

    def _load_credentials(self) -> dict:
        if not self.credentials_path.exists():
            return {}
        with open(self.credentials_path) as f:
            data = json.load(f)
        return data.get("installed", data.get("web", {}))

    def _load_tokens(self) -> dict:
        """Load per-account tokens: {account_id: {access_token, refresh_token, ...}}"""
        if not self.tokens_path.exists():
            return {}
        with open(self.tokens_path) as f:
            return json.load(f)

    def _save_tokens(self):
        """Write the token file atomically (temp-file+rename), same pattern
        as GmailClient._save_tokens - holds every authorized account's
        refresh_token, so an interrupted write must never leave a
        truncated/corrupt file.
        """
        self.tokens_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.tokens_path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(self._tokens, f, indent=2)
                f.write("\n")
            tmp.replace(self.tokens_path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    # ── Authorization flow ──

    def authorize(self, account_id: str) -> dict:
        """Run the OAuth2 consent flow for one account. Opens a browser.

        Same local-HTTPServer redirect-capture approach as
        mail-tools' GmailClient.authorize.

        Returns {"authorized": True, "account_id": account_id}.
        """
        if not self._credentials:
            raise GoogleOAuthError(f"No Google OAuth credentials found. Place credentials at {self.credentials_path}")

        client_id = self._credentials["client_id"]
        client_secret = self._credentials["client_secret"]

        server = HTTPServer(("localhost", 0), _OAuthCallbackHandler)
        port = server.server_address[1]
        redirect_uri = f"http://localhost:{port}"

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "access_type": "offline",
            "prompt": "consent",
            "login_hint": account_id,
        }

        auth_url = f"{_AUTH_URL}?{urlencode(params)}"
        logger.info("Opening browser for %s authorization: %s", self.tool_name, account_id)
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
            raise GoogleOAuthError("OAuth authorization timed out or was cancelled")

        token_data = self._exchange_code(code, redirect_uri, client_id, client_secret)
        self._tokens[account_id] = {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "token_type": token_data.get("token_type", "Bearer"),
        }
        self._save_tokens()

        return {"authorized": True, "account_id": account_id}

    def _exchange_code(self, code, redirect_uri, client_id, client_secret) -> dict:
        """Exchange an authorization code for access/refresh tokens."""
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
            _TOKEN_URL,
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = urlopen(req)
        return json.loads(resp.read())

    def _refresh_token(self, account_id: str, _reloaded: bool = False) -> str:
        """Refresh an expired access token.

        If the in-memory refresh token is revoked/expired, reload from disk
        and retry once - handles the case where another process instance
        (e.g. the one used by an *_authorize tool) already saved fresh
        tokens to disk.
        """
        token_info = self._tokens.get(account_id, {})
        refresh_token = token_info.get("refresh_token")
        if not refresh_token:
            raise GoogleOAuthError(f"No refresh token for {account_id}. Re-authorize this account.")

        data = urlencode(
            {
                "client_id": self._credentials["client_id"],
                "client_secret": self._credentials["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode()

        req = Request(
            _TOKEN_URL,
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
                fresh_rt = fresh_tokens.get(account_id, {}).get("refresh_token", "")
                if fresh_rt and fresh_rt != refresh_token:
                    self._tokens = fresh_tokens
                    return self._refresh_token(account_id, _reloaded=True)
            error_body = e.read().decode() if e.fp else str(e)
            raise GoogleOAuthError(f"Token refresh failed for {account_id}: {error_body}", token_revoked=True)

        self._tokens[account_id]["access_token"] = result["access_token"]
        self._save_tokens()
        return result["access_token"]

    # ── Authenticated requests ──

    def request(self, account_id: str, method: str, url: str, body: dict | None = None, retry: bool = True) -> dict:
        """Make an authenticated request against a full URL, auto-refreshing
        the access token on a 401 once.

        Unlike GmailClient._api_call (which builds the URL from a Gmail-
        specific path template rooted at users/me), callers here pass the
        complete URL, since different Google APIs have very different path
        shapes (Calendar's /calendars/{id}/events vs. People's
        /people/{id}:updateContact).

        Returns the parsed JSON body, or {} for a 204 No Content response.
        """
        token_info = self._tokens.get(account_id, {})
        access_token = token_info.get("access_token")
        if not access_token:
            # A refresh_token with no access_token is a partial/corrupted
            # entry that can never succeed without re-authorization - treat
            # it the same as a confirmed-dead token (mirrors GmailClient).
            raise GoogleOAuthError(
                f"No access token for {account_id}. Run the authorize tool first.",
                token_revoked=bool(token_info.get("refresh_token")),
            )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        req = Request(url, method=method, headers=headers)
        if body is not None:
            req.data = json.dumps(body).encode()

        try:
            resp = urlopen(req)
            if resp.status == 204:
                return {}
            raw = resp.read()
            return json.loads(raw) if raw else {}
        except HTTPError as e:
            if e.code == 401 and retry:
                self._refresh_token(account_id)
                return self.request(account_id, method, url, body, retry=False)
            error_body = e.read().decode() if e.fp else str(e)
            raise GoogleOAuthError(f"Google API error {e.code}: {error_body}", status_code=e.code)
