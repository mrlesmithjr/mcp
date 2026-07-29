"""ScriptingBridge bridge - read/write Apple Mail.

Communicates with Mail.app via macOS ScriptingBridge. Mail.app will
auto-launch if not already running. All configured accounts (iCloud,
Gmail, Exchange, etc.) are accessible through a single interface.

Performance notes:
- Each ScriptingBridge property access is an Apple Event IPC call.
- Minimize per-message property lookups in loops.
- Use account-scoped INBOX queries instead of the unified inbox to
  catch messages across all accounts.
- Batch operations collect targets in a single pass before acting.
"""

import concurrent.futures
import logging
import subprocess
from datetime import date, datetime

from ScriptingBridge import SBApplication

logger = logging.getLogger(__name__)


def _sync_mail_app():
    """Trigger Mail.app to sync with servers after Gmail API changes."""
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Mail" to check for new mail'],
            capture_output=True,
            timeout=10,
        )
        logger.info("Triggered Mail.app sync")
    except Exception as e:
        logger.warning("Failed to trigger Mail.app sync: %s", e)


# Maximum content length returned per message to keep responses manageable
MAX_CONTENT_LENGTH = 4000


class MailError(Exception):
    """Raised when Mail operations fail."""


class MailManager:
    """Manages Apple Mail access through ScriptingBridge."""

    def __init__(self):
        self._app = None
        self._account_cache = None
        self._gmail = None
        self._init_gmail()

    def _init_gmail(self):
        """Initialize Gmail API client if credentials are available."""
        try:
            from mail_tools.gmail import GmailClient

            if GmailClient.is_available():
                self._gmail = GmailClient()
                logger.info("Gmail API client initialized")
            else:
                logger.info("No Gmail credentials - using Mail.app fallback for Gmail accounts")
        except Exception as e:
            logger.warning("Failed to init Gmail API client: %s", e)

    def _get_account_email(self, account) -> str | None:
        """Get the primary email address for an account."""
        emails = list(account.emailAddresses()) if account.emailAddresses() else []
        return emails[0] if emails else None

    def _is_gmail_api_account(self, account) -> bool:
        """Check if this account should use Gmail API (Gmail + authorized)."""
        if not self._gmail:
            return False
        email = self._get_account_email(account)
        if not email:
            return False
        # Check if it's a Gmail account (has "All Mail" mailbox) and is authorized
        has_all_mail = any(mb.name() == "All Mail" for mb in account.mailboxes())
        return has_all_mail and self._gmail.is_authorized(email)

    @property
    def app(self):
        """Lazy-init the Mail.app ScriptingBridge connection."""
        if self._app is None:
            self._app = SBApplication.applicationWithBundleIdentifier_("com.apple.mail")
            if self._app is None:
                raise MailError("Could not connect to Mail.app")
            logger.info("Connected to Mail.app")
        return self._app

    def _get_accounts(self):
        """Get accounts with caching to avoid repeated Apple Events."""
        if self._account_cache is None:
            self._account_cache = list(self.app.accounts())
        return self._account_cache

    def _get_account_inboxes(self, account_name=None):
        """Get (account, inbox_mailbox) pairs for all or one account.

        This is the core performance improvement - instead of using
        mail.inbox() (unified, misses some accounts), we query each
        account's INBOX directly.
        """
        results = []
        for acct in self._get_accounts():
            if account_name:
                if not _account_matches(acct, account_name):
                    continue

            for mb in acct.mailboxes():
                if mb.name() == "INBOX":
                    results.append((acct, mb))
                    break

        return results

    def _get_archive_info(self, account):
        """Determine archive strategy for an account.

        Returns: ("archive", mailbox) | ("mark_read", None)
        """
        mailbox_map = {}
        for mb in account.mailboxes():
            mailbox_map[mb.name()] = mb

        if "Archive" in mailbox_map:
            return "archive", mailbox_map["Archive"]
        elif "All Mail" in mailbox_map:
            # Gmail: ScriptingBridge cannot remove INBOX labels.
            # moveTo_(All Mail) is a no-op (messages are already there).
            # msg.delete() sends to Trash, not archive.
            # Best we can do natively is mark as read.
            return "mark_read", None
        else:
            return "mark_read", None

    # ── Public API ──

    def list_accounts(self):
        """Return all configured mail accounts."""
        results = []
        for acct in self._get_accounts():
            emails = list(acct.emailAddresses()) if acct.emailAddresses() else []
            results.append(
                {
                    "name": acct.name(),
                    "emails": emails,
                }
            )
        return results

    def list_mailboxes(self, account_name=None):
        """List mailboxes with unread counts (skips message_count for speed)."""
        results = []
        for acct in self._get_accounts():
            if account_name and not _account_matches(acct, account_name):
                continue
            for mb in acct.mailboxes():
                results.append(
                    {
                        "name": mb.name(),
                        "account": acct.name(),
                        "unread_count": mb.unreadCount(),
                    }
                )
        return results

    def list_messages(self, mailbox="INBOX", account=None, limit=20, unread_only=False):
        """List messages from inbox across all accounts (or one account).

        When mailbox is INBOX and no account is specified, aggregates
        messages from every account's INBOX - not just the unified view.

        Gmail-authorized accounts (issue #50) route through
        GmailClient.list_messages() instead of ScriptingBridge for the two
        INBOX cases (unified and single-account) and for an explicit
        account's arbitrary mailbox - see _is_gmail_api_account. ScriptingBridge
        is now used only for non-Gmail (e.g. iCloud) accounts, and for an
        arbitrary mailbox looked up with no account given (there's no way to
        know which account's mailbox _find_mailbox() will resolve to before
        picking a route in that case).

        All three cases (unified inbox, one account's INBOX, arbitrary
        mailbox) resolve their target message collection then always
        batch-serialize via _batch_serialize_messages() (ScriptingBridge) or
        GmailClient.list_messages() (Gmail) with no early truncation from
        `limit` - sorting, unread_only filtering, and the limit slice all
        happen last, against the full fetched set. Truncating to `limit`
        before filtering can undercount or return empty results even when
        matching messages exist further down the raw-order list.

        Caveat specific to the Gmail path: GmailClient.list_messages() fetches
        a single page (maxResults=<n>, no pagination), unlike ScriptingBridge's
        full-mailbox fetch. _gmail_list_fetch_limit() pads that page size when
        unread_only is requested to reduce (not eliminate) the risk of an
        older unread Gmail message falling outside the fetched page.
        """
        if mailbox.upper() == "INBOX" and account is None:
            # Query each account's INBOX directly using batch property
            # fetching with per-account timeout protection.
            # Some Gmail accounts hang on arrayByApplyingSelector_ when
            # Mail.app is syncing - we skip those rather than blocking.
            results = []

            def _fetch_account_inbox(acct_inbox_pair):
                acct, inbox_mb = acct_inbox_pair
                acct_name = acct.name()
                acct_results = self._list_account_inbox(acct, inbox_mb, limit, unread_only)
                for r in acct_results:
                    r["account"] = acct_name
                return acct_results

            for pair in self._get_account_inboxes():
                acct_name = pair[0].name()
                results.extend(
                    self._call_with_timeout(lambda p=pair: _fetch_account_inbox(p), acct_name, action="fetching INBOX")
                )
        elif mailbox.upper() == "INBOX" and account:
            pairs = self._get_account_inboxes(account)
            if not pairs:
                raise MailError(f"INBOX not found for account: {account}")
            acct, inbox_mb = pairs[0]
            results = self._list_account_inbox(acct, inbox_mb, limit, unread_only)
            for r in results:
                r["account"] = acct.name()
        else:
            acct = self._find_account(account) if account else None
            if acct is not None and self._is_gmail_api_account(acct):
                gmail_email = self._get_account_email(acct)
                results = self._gmail.list_messages(
                    gmail_email, mailbox=mailbox, limit=_gmail_list_fetch_limit(limit, unread_only)
                )
                for r in results:
                    r["account"] = acct.name()
            else:
                mb_msgs = self._get_mailbox_messages(mailbox, account)
                results = _batch_serialize_messages(mb_msgs)

        # Sort by date descending, apply unread filter, then slice to limit
        # last - the full set has already been fetched above.
        results.sort(key=lambda m: m.get("date") or "", reverse=True)
        if unread_only:
            results = [r for r in results if not r["read"]]
        return results[:limit]

    def _call_with_timeout(self, fn, acct_name, timeout=5, action="fetching"):
        """Run fn() in a single-worker thread pool with a per-account timeout,
        logging and swallowing any failure so one hung or erroring account
        never blocks or drops results from the rest.

        Shared by list_messages()'s and search_messages()'s unified
        (all-accounts) branches, where a Gmail account can hang on
        arrayByApplyingSelector_ while Mail.app is mid-sync. Returns an
        empty list on timeout or any other exception - never raises.

        The executor is shut down with wait=False, so a timed-out call
        returns to the caller immediately once the timeout elapses; it does
        not block waiting for the hung worker thread to finish. Python
        threads cannot be force-killed, so the abandoned worker keeps running
        in the background until it eventually completes (or the process
        exits) - it is orphaned, not cancelled.
        """
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(fn)
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning(f"Timeout {action} for {acct_name}, skipping")
            return []
        except Exception as e:
            logger.warning(f"Error {action} for {acct_name}: {e}")
            return []
        finally:
            executor.shutdown(wait=False)

    def _call_gmail_batch(self, fn, email, action):
        """Run a Gmail API batch call, catching a dead/expired token (or any
        other per-account failure) so it doesn't abort a bulk op targeting
        other, healthy accounts (issue #83).

        Mirrors _call_with_timeout's log-and-skip style, minus the timeout -
        these are direct HTTPS calls (GmailClient._api_call), not
        ScriptingBridge IPC that can hang. Shared by archive_messages(),
        delete_messages(), and _mark_messages() for their Pass 1
        (per-account), Pass 2 (rfc822msgid fallback across all authorized
        accounts), and Pass 3 (direct-id lookup across all authorized
        accounts, issue #89) Gmail calls - Pass 2 and Pass 3 are the cases
        that most reliably hit a dead-token account, since both loop every
        authorized account regardless of which one the target ids actually
        belong to.

        Returns fn()'s result on success, or None on failure - callers use
        the None sentinel to skip that account's contribution.
        """
        try:
            return fn()
        except Exception as e:
            logger.warning(f"Gmail API error {action} for {email}, skipping account: {e}")
            return None

    def _resolve_missing_gmail_ids(self, missing_ids):
        """Pass 3 (issue #89): resolve still-unfound ids to their owning
        Gmail account by checking each Gmail-authorized account directly via
        GmailClient.message_exists(), for ids that neither Pass 1's
        per-account INBOX scan nor Pass 2's rfc822msgid: search could
        resolve.

        This is the fallback for a message that has left INBOX (archived
        already, or filtered straight to a category/label on arrival) and is
        identified by a bare Gmail-internal hex id rather than an rfc822
        Message-ID: Pass 1 can't see it because it's not in INBOX, and Pass
        2's rfc822msgid: operator only matches actual RFC 5322 Message-IDs,
        never a Gmail-internal id. mail_bulk_action resolves the exact same
        ids instantly because it queries Gmail's search index directly
        instead of doing id-based lookups scoped to INBOX/rfc822msgid.

        Only ids with no "@" are attempted - Gmail-internal ids never contain
        one (same heuristic read_message() uses for its own Gmail routing,
        issue #87); an rfc822-style id reaching this helper is a genuinely
        missing non-Gmail message and is left unresolved with zero extra API
        calls, so non-Gmail-account behavior is unaffected.

        Shared by archive_messages/delete_messages/_mark_messages so one
        id-to-account resolution loop backs all four affected tools instead
        of four near-duplicate copies - see CLAUDE.md's "Two backends"
        section. A dead/expired token (or any other per-account failure) on
        one authorized account is logged and skipped via _call_gmail_batch,
        same as Pass 2, so it can never abort resolution for ids that belong
        to a different, healthy account.

        Returns {gmail_id: account_email} for every id confirmed to exist on
        some authorized account. This helper only resolves OWNERSHIP - it
        never mutates a message's labels. Callers group the returned ids by
        account and issue the actual mutation via that account's own plural
        batch method (archive_messages/delete_messages/mark_read_messages/
        mark_unread_messages).
        """
        resolved: dict[str, str] = {}
        if not self._gmail:
            return resolved

        remaining = [mid for mid in missing_ids if "@" not in mid]
        for email in self._gmail.list_authorized_accounts():
            if not remaining:
                break
            still_unresolved = []
            for mid in remaining:
                exists = self._call_gmail_batch(
                    lambda e=email, m=mid: self._gmail.message_exists(e, m),
                    email,
                    "resolving message id (Pass 3 direct lookup)",
                )
                if exists:
                    resolved[mid] = email
                else:
                    still_unresolved.append(mid)
            remaining = still_unresolved

        return resolved

    def _list_account_inbox(self, acct, inbox_mb, limit, unread_only):
        """Fetch one account's INBOX messages, routing Gmail-authorized
        accounts through the Gmail API instead of ScriptingBridge.

        Shared by list_messages()'s unified-inbox and single-account-INBOX
        branches. Does not set "account" on results - callers do that once,
        consistently, for both branches.
        """
        if self._is_gmail_api_account(acct):
            gmail_email = self._get_account_email(acct)
            return self._gmail.list_messages(
                gmail_email, mailbox="INBOX", limit=_gmail_list_fetch_limit(limit, unread_only)
            )
        return _batch_serialize_messages(inbox_mb.messages())

    def read_message(self, message_id):
        """Read a specific message by ID, including full content.

        ScriptingBridge's fast unified-inbox check runs first (a single
        cheap IPC call) since it covers the common non-Gmail case
        immediately. If that misses AND message_id doesn't look like an
        rfc822 Message-ID (no "@" - RFC 5322's msg-id syntax always
        requires one, while Gmail's internal ids returned by
        mail_list/mail_search/mail_bulk_action for Gmail-authorized
        accounts are bare hex strings with no "@"), every Gmail-authorized
        account is tried in turn via GmailClient.get_message() (issue #87)
        BEFORE falling back to ScriptingBridge's slow all-mailboxes scan -
        a Gmail-internal id never matches ScriptingBridge's
        messageId()-based lookup, so scanning every mailbox of every
        account first would only waste time before this Gmail check
        inevitably runs anyway. The "@" heuristic also means the common
        rfc822-Message-ID case (all non-Gmail accounts, plus Gmail ids
        looked up by rfc822 id elsewhere) skips the Gmail branch entirely
        and goes straight to the slow scan exactly as before - unaffected.

        read_message() takes no account parameter (Gmail's API is scoped
        per account, unlike ScriptingBridge's account-spanning scan), so
        which account owns a given Gmail id is unknown upfront - each
        authorized account is tried until one succeeds. A dead token or
        any other per-account failure (via _call_gmail_batch) is logged
        and skipped rather than aborting the whole lookup, same as the
        Pass 2 fallback in _mark_messages/archive_messages/delete_messages.

        The slow ScriptingBridge scan (all mailboxes, all accounts) still
        runs last, for non-Gmail messages filed away outside the unified
        inbox (Trash, Archive, Sent, etc.) - unaffected by the
        Gmail-authorized routing above.
        """
        msg = self._find_message_unified_inbox(message_id)
        if msg is not None:
            return self._serialize_message_content(msg, message_id)

        if self._gmail and "@" not in message_id:
            for email in self._gmail.list_authorized_accounts():
                gmail_result = self._call_gmail_batch(
                    lambda e=email: self._gmail.get_message(e, message_id), email, "reading message"
                )
                if gmail_result is not None:
                    content = gmail_result.pop("content", "") or ""
                    return self._finalize_content(gmail_result, content)

        msg = self._find_message_all_mailboxes(message_id)
        if msg is not None:
            return self._serialize_message_content(msg, message_id)

        raise MailError(f"Message not found: {message_id}")

    def _serialize_message_content(self, msg, message_id):
        """Build the read_message() result dict for a ScriptingBridge message.

        Shared by both the fast unified-inbox path and the slow
        all-mailboxes fallback path in read_message() so content
        truncation stays identical either way.
        """
        # Build result with minimal IPC calls - skip recipient iteration
        date = msg.dateReceived()
        date_str = None
        if date:
            try:
                ts = date.timeIntervalSince1970()
                date_str = datetime.fromtimestamp(ts).astimezone().isoformat()
            except Exception:
                date_str = str(date)

        result = {
            "message_id": message_id,
            "subject": msg.subject() or "(no subject)",
            "from": msg.sender() or None,
            "date": date_str,
            "read": bool(msg.readStatus()),
            "flagged": bool(msg.flaggedStatus()),
        }

        # Content - resolve SBObject lazily
        raw_content = msg.content()
        try:
            content = raw_content.get() if hasattr(raw_content, "get") else (raw_content or "")
        except Exception:
            content = str(raw_content) if raw_content else ""
        if not isinstance(content, str):
            content = str(content) if content else ""

        return self._finalize_content(result, content)

    def _finalize_content(self, result, content):
        """Truncate content to MAX_CONTENT_LENGTH and set truncated/full_length,
        the same way regardless of whether content came from ScriptingBridge
        or the Gmail API.
        """
        if len(content) > MAX_CONTENT_LENGTH:
            result["content"] = content[:MAX_CONTENT_LENGTH]
            result["truncated"] = True
            result["full_length"] = len(content)
        else:
            result["content"] = content
            result["truncated"] = False
        return result

    def search_messages(self, query, mailbox="INBOX", account=None, limit=20):
        """Search messages, routing Gmail-authorized accounts through the
        Gmail API for the INBOX case (issue #50) - see _is_gmail_api_account.

        IMPORTANT - Gmail and ScriptingBridge search semantics DIVERGE, they
        are not the same search behavior with a different transport:
          - ScriptingBridge (non-Gmail/iCloud accounts, and any account when
            mailbox != "INBOX") does a client-side, case-insensitive
            substring match of `query` against subject OR sender text only -
            no body search, no operators.
          - Gmail-authorized accounts searched with mailbox="INBOX" send
            `query` straight into GmailClient.search_messages(), i.e.
            Gmail's own full-text query engine - it searches message
            bodies too, and understands Gmail search operators
            (from:, subject:, has:attachment, newer_than:, etc). A query
            that matches nothing via the ScriptingBridge substring rule can
            match plenty via Gmail, and vice versa (e.g. a bare word that
            only appears in a message body matches on Gmail, never on
            ScriptingBridge).
        Do not assume identical result sets across account types for the
        same query string, and do not present Gmail search results as if
        they came from a subject/sender substring match.

        Gmail routing is INBOX-only: GmailClient.search_messages() has no
        mailbox-scoping parameter (unlike list_messages(), which supports
        arbitrary `in:<mailbox>` queries) - a Gmail account searched with a
        non-INBOX mailbox continues to use ScriptingBridge. Callers who need
        Gmail search scoped to a specific label can add a Gmail `in:`
        operator directly to `query` instead.

        Searches across all account INBOXes when no account specified.
        """
        query_lower = query.lower()

        def _search_account_inbox(acct, inbox_mb):
            if self._is_gmail_api_account(acct):
                gmail_email = self._get_account_email(acct)
                return self._gmail.search_messages(gmail_email, query=query, limit=limit)
            all_results = _batch_serialize_messages(inbox_mb.messages())
            matched = []
            for msg_data in all_results:
                subject = (msg_data.get("subject") or "").lower()
                sender = (msg_data.get("from") or "").lower()
                if query_lower in subject or query_lower in sender:
                    matched.append(msg_data)
            return matched

        if mailbox.upper() == "INBOX" and account is None:
            # Per-account timeout protection, same as list_messages()'s
            # unified branch - some Gmail accounts hang on
            # arrayByApplyingSelector_ when Mail.app is mid-sync.
            results = []
            for acct, inbox_mb in self._get_account_inboxes():
                acct_name = acct.name()
                acct_results = self._call_with_timeout(
                    lambda a=acct, mb=inbox_mb: _search_account_inbox(a, mb), acct_name, action="searching INBOX"
                )
                for r in acct_results:
                    r["account"] = acct_name
                results.extend(acct_results)
        elif mailbox.upper() == "INBOX" and account:
            pairs = self._get_account_inboxes(account)
            if not pairs:
                raise MailError(f"INBOX not found for account: {account}")
            acct, inbox_mb = pairs[0]
            results = _search_account_inbox(acct, inbox_mb)
            for r in results:
                r["account"] = acct.name()
        else:
            mb = self._find_mailbox(mailbox, account)
            if mb is None:
                raise MailError(f"Mailbox not found: {mailbox}")
            all_results = _batch_serialize_messages(mb.messages())
            results = []
            for msg_data in all_results:
                subject = (msg_data.get("subject") or "").lower()
                sender = (msg_data.get("from") or "").lower()
                if query_lower in subject or query_lower in sender:
                    results.append(msg_data)

        # Sort by date descending before slicing, matching list_messages() -
        # otherwise a multi-account unified search returns accounts'
        # results concatenated in account-iteration order, not date order.
        results.sort(key=lambda m: m.get("date") or "", reverse=True)
        return results[:limit]

    def _summarize_gmail_unread(self, email: str) -> tuple[list[dict], int]:
        """Fetch and sum one Gmail-authorized account's unread counts.

        Shared by get_unread_count()'s fast path and its per-account loop
        branch, so "call the API, treat a dead-token None as no results,
        sum" lives in exactly one place rather than being copy-pasted
        between the two Gmail-routing branches.
        """
        mailboxes = self._call_gmail_batch(lambda: self._gmail.get_unread_counts(email), email, "getting unread counts")
        if mailboxes is None:
            mailboxes = []
        return mailboxes, sum(mb["unread"] for mb in mailboxes)

    def get_unread_count(self, account=None):
        """Get unread message counts per account/mailbox.

        Gmail-authorized accounts (issue #101) route through
        GmailClient.get_unread_counts() instead of ScriptingBridge's
        mb.unreadCount() - see _is_gmail_api_account. mb.unreadCount()
        reads Mail.app's locally IMAP-synced mailbox state, which requires
        Mail.app to be running (auto-launching/foregrounding it if not).
        Other accounts (e.g. iCloud) are unaffected and keep using
        mb.unreadCount() as before.

        Fast path below: when `account` is passed and is itself a
        Gmail-authorized email address - checked directly against
        gmail_tokens.json via GmailClient.is_authorized(), never via a
        ScriptingBridge account lookup - this resolves entirely through
        GmailClient and never calls self._get_accounts()/self.app at all.
        A dead/expired token degrades gracefully (via _call_gmail_batch,
        same as every other Gmail-routed operation in this file) rather
        than raising.

        This fast path's authorization check (is_authorized() alone) is
        deliberately weaker than _is_gmail_api_account()'s (which also
        requires an "All Mail" mailbox on the ScriptingBridge account
        object): verifying "All Mail" or that the account is still present
        in self._get_accounts() at all would require the exact
        ScriptingBridge/Mail.app-launch call this fast path exists to
        avoid. The accepted risk is narrow - a Gmail address whose OAuth
        token is still valid but whose Mail.app account was since removed
        (nothing in this codebase prunes gmail_tokens.json on account
        removal) answers with live, accurate Gmail data for an account
        Mail.app no longer manages, instead of returning nothing. Both
        Gmail-routing branches use the account's email address (not
        acct.name()) as the "account" key in their result, since the fast
        path has no Mail.app display name to offer - keeping the two
        Gmail branches internally consistent, even though that diverges
        from the ScriptingBridge branch's (and every other Gmail-routed
        tool's) use of acct.name().

        ScriptingBridge enumeration below is still used - unavoidably -
        for every other case: no `account` filter (the full account list
        has to be discovered somehow, including non-Gmail accounts), or an
        `account` filter that names a Mail.app display name rather than a
        raw email address (resolving a display name to an account requires
        ScriptingBridge in the first place, so there is no way to avoid it
        for that case).
        """
        if account and self._gmail and self._gmail.is_authorized(account):
            mailboxes, total_unread = self._summarize_gmail_unread(account)
            if total_unread == 0:
                return []
            return [{"account": account, "total_unread": total_unread, "mailboxes": mailboxes}]

        results = []
        for acct in self._get_accounts():
            if account and not _account_matches(acct, account):
                continue

            if self._is_gmail_api_account(acct):
                gmail_email = self._get_account_email(acct)
                mailboxes, acct_total = self._summarize_gmail_unread(gmail_email)
                if acct_total > 0 or not account:
                    results.append(
                        {
                            "account": gmail_email,
                            "total_unread": acct_total,
                            "mailboxes": mailboxes,
                        }
                    )
                continue

            acct_total = 0
            mailboxes = []
            for mb in acct.mailboxes():
                unread = mb.unreadCount()
                if unread > 0:
                    mailboxes.append(
                        {
                            "mailbox": mb.name(),
                            "unread": unread,
                        }
                    )
                acct_total += unread

            if acct_total > 0 or not account:
                results.append(
                    {
                        "account": acct.name(),
                        "total_unread": acct_total,
                        "mailboxes": mailboxes,
                    }
                )

        return results

    def mark_read(self, message_id):
        """Mark a single message as read - thin wrapper over mark_read_messages."""
        result = self.mark_read_messages([message_id])
        if result["results"]:
            return result["results"][0]
        raise MailError(f"Message not found: {message_id}")

    def mark_unread(self, message_id):
        """Mark a single message as unread - thin wrapper over mark_unread_messages."""
        result = self.mark_unread_messages([message_id])
        if result["results"]:
            return result["results"][0]
        raise MailError(f"Message not found: {message_id}")

    def mark_read_messages(self, message_ids):
        """Mark multiple messages as read in a single pass - account-aware."""
        return self._mark_messages(message_ids, read=True)

    def mark_unread_messages(self, message_ids):
        """Mark multiple messages as unread in a single pass - account-aware."""
        return self._mark_messages(message_ids, read=False)

    def _mark_messages(self, message_ids, read):
        """Mark multiple messages read/unread in a single pass - account-aware.

        Mirrors archive_messages/delete_messages: Pass 1 batch-scans each
        account's INBOX (Gmail accounts via GmailClient.batch_modify, others
        via setReadStatus_), Pass 2 falls back to Gmail's rfc822msgid search
        for ids not found in any INBOX, and Pass 3 (issue #89) directly
        checks each Gmail-authorized account for a still-unresolved
        Gmail-internal id via _resolve_missing_gmail_ids() - the fallback
        that finally reaches a message that has left INBOX entirely (already
        archived, or filtered straight to a category/label on arrival),
        which neither Pass 1 nor Pass 2 can see. Unlike archive/delete (which
        give up on a non-Gmail message still missing from INBOX after Pass
        2), a Pass 4 ScriptingBridge mailbox scan covers non-Gmail accounts,
        since mark-read/unread is plausibly called on messages already filed
        in Important/All Mail.

        HARD INVARIANT (issue #50): Pass 4 must NEVER scan a Gmail-authorized
        account, under any circumstance. This is not an incidental gap left
        over because Pass 1/2/3 "should" already cover Gmail - it is
        deliberate exclusion, enforced by the `if
        self._is_gmail_api_account(acct): continue` guard below. Gmail ops
        are Gmail-API-only per this issue's whole purpose: falling back to
        ScriptingBridge for a Gmail account would reintroduce the exact
        per-account mailbox-brute-force behavior #50 was written to remove,
        silently, only on the rare not-found path where it's hardest to
        notice in testing. If a Gmail id is not found by Pass 1's INBOX scan,
        Pass 2's rfc822msgid search, or Pass 3's direct-id lookup, it is
        reported `not_found` - it must not fall through to a mailbox scan.
        Do not remove or "fix" that guard; see the regression test asserting
        a Gmail-authorized account's mailboxes are never scanned in Pass 4.
        """
        target_ids = set(message_ids)
        results = []
        found_ids = set()
        used_gmail_api = False
        label = "read" if read else "unread"

        for acct, inbox_mb in self._get_account_inboxes():
            use_gmail_api = self._is_gmail_api_account(acct)
            gmail_email = self._get_account_email(acct) if use_gmail_api else None

            gmail_batch = []
            to_process = []
            for msg, mid in _match_messages_by_id(inbox_mb.messages(), target_ids):
                if use_gmail_api:
                    gmail_batch.append(mid)
                else:
                    to_process.append((msg, mid))
                found_ids.add(mid)

            if gmail_batch and gmail_email:
                if read:
                    api_results = self._call_gmail_batch(
                        lambda: self._gmail.mark_read_messages(gmail_email, gmail_batch),
                        gmail_email,
                        f"marking {label}",
                    )
                else:
                    api_results = self._call_gmail_batch(
                        lambda: self._gmail.mark_unread_messages(gmail_email, gmail_batch),
                        gmail_email,
                        f"marking {label}",
                    )
                if api_results is None:
                    for mid in gmail_batch:
                        results.append(
                            {
                                "message_id": mid,
                                "marked": False,
                                "status": label,
                                "account": acct.name(),
                                "reason": "Gmail account error - see logs",
                            }
                        )
                else:
                    used_gmail_api = True
                    results.extend(api_results)

            for msg, mid in to_process:
                msg.setReadStatus_(read)
                results.append({"message_id": mid, "marked": True, "status": label, "account": acct.name()})

        # Pass 2: Gmail API fallback for messages not found in any INBOX.
        # _find_message_id() uses rfc822msgid: which searches all labels,
        # so this handles messages that were moved out of INBOX.
        missing = target_ids - found_ids
        if missing and self._gmail:
            for email in self._gmail.list_authorized_accounts():
                if not missing:
                    break
                if read:
                    api_results = self._call_gmail_batch(
                        lambda e=email: self._gmail.mark_read_messages(e, list(missing)),
                        email,
                        f"marking {label} (Pass 2 fallback)",
                    )
                else:
                    api_results = self._call_gmail_batch(
                        lambda e=email: self._gmail.mark_unread_messages(e, list(missing)),
                        email,
                        f"marking {label} (Pass 2 fallback)",
                    )
                if api_results is None:
                    continue
                for r in api_results:
                    if r.get("marked"):
                        used_gmail_api = True
                        found_ids.add(r["message_id"])
                        missing.discard(r["message_id"])
                        results.append(r)

        # Pass 3 (issue #89): direct-id lookup fallback for Gmail-internal
        # ids that Pass 1's INBOX scan and Pass 2's rfc822msgid search
        # couldn't find - see archive_messages()'s Pass 3 comment and
        # _resolve_missing_gmail_ids()'s docstring. Still Gmail-API-only:
        # only ids resolved to an authorized account are mutated here. Uses
        # modify_by_gmail_id() directly, NOT mark_read_messages()/
        # mark_unread_messages() - those treat their input as rfc822
        # Message-IDs and would re-run the exact rfc822msgid search that
        # already failed to resolve these ids in Pass 2.
        if missing:
            id_to_account = self._resolve_missing_gmail_ids(missing)
            by_account: dict[str, list[str]] = {}
            for mid, email in id_to_account.items():
                by_account.setdefault(email, []).append(mid)
            for email, ids in by_account.items():
                label_ids_kwarg = {"remove_label_ids": ["UNREAD"]} if read else {"add_label_ids": ["UNREAD"]}
                api_results = self._call_gmail_batch(
                    lambda e=email, i=ids, kw=label_ids_kwarg: self._gmail.modify_by_gmail_id(e, i, **kw),
                    email,
                    f"marking {label} (Pass 3 direct lookup)",
                )
                if api_results is None:
                    continue
                for r in api_results:
                    r["marked"] = r.pop("modified")
                    r["status"] = label
                    if r["marked"]:
                        used_gmail_api = True
                        found_ids.add(r["message_id"])
                        missing.discard(r["message_id"])
                        results.append(r)

        # Pass 4: ScriptingBridge mailbox-scan fallback, non-Gmail accounts
        # ONLY. This exclusion is a hard invariant, not an optional scoping
        # detail - see the HARD INVARIANT note in this method's docstring.
        # Never remove this guard to "also" cover Gmail accounts here.
        if missing:
            for acct in self._get_accounts():
                if not missing:
                    break
                if self._is_gmail_api_account(acct):
                    continue  # Gmail accounts: Gmail-API-only, always. Never scan via ScriptingBridge.
                for mb in acct.mailboxes():
                    if not missing:
                        break
                    try:
                        mb_msgs = mb.messages()
                    except Exception:
                        continue
                    for msg, mid in _match_messages_by_id(mb_msgs, missing):
                        try:
                            msg.setReadStatus_(read)
                            results.append({"message_id": mid, "marked": True, "status": label, "account": acct.name()})
                            found_ids.add(mid)
                            missing.discard(mid)
                        except Exception:
                            continue

        for mid in missing:
            results.append({"message_id": mid, "marked": False, "status": label, "reason": "not found"})

        marked_count = sum(1 for r in results if r.get("marked"))
        if used_gmail_api:
            _sync_mail_app()
        return {"results": results, "marked": marked_count, "not_found": len(missing)}

    def flag_message(self, message_id, flagged=True):
        """Flag or unflag a message."""
        msg = self._find_message_fast(message_id)
        if msg is None:
            raise MailError(f"Message not found: {message_id}")
        msg.setFlaggedStatus_(flagged)
        return {"message_id": message_id, "flagged": flagged}

    def move_message(self, message_id, target_mailbox, target_account=None):
        """Move a message to a different mailbox."""
        msg = self._find_message_fast(message_id)
        if msg is None:
            raise MailError(f"Message not found: {message_id}")

        mb = self._find_mailbox(target_mailbox, target_account)
        if mb is None:
            raise MailError(f"Mailbox not found: {target_mailbox}")

        msg.moveTo_(mb)
        return {"message_id": message_id, "moved_to": target_mailbox}

    def archive_message(self, message_id):
        """Archive a single message - account-aware."""
        result = self.archive_messages([message_id])
        if result["results"]:
            return result["results"][0]
        raise MailError(f"Message not found: {message_id}")

    def archive_messages(self, message_ids):
        """Archive multiple messages in a single pass - account-aware.

        Uses Gmail API for Gmail accounts (proper INBOX label removal).
        Falls back to ScriptingBridge for iCloud/other accounts.

        Gmail-authorized accounts resolve target ids through up to three
        passes: Pass 1 batch-scans each account's INBOX, Pass 2 falls back
        to a Gmail rfc822msgid: search across every authorized account, and
        Pass 3 (issue #89) directly checks each authorized account for a
        still-unresolved Gmail-internal id (no "@") via
        _resolve_missing_gmail_ids() - the fallback that finally reaches a
        message that has left INBOX entirely (already archived, or filtered
        straight to a category/label on arrival), which neither Pass 1 nor
        Pass 2 can see.
        """
        target_ids = set(message_ids)
        results = []
        found_ids = set()

        for acct, inbox_mb in self._get_account_inboxes():
            use_gmail_api = self._is_gmail_api_account(acct)
            gmail_email = self._get_account_email(acct) if use_gmail_api else None
            strategy, archive_mb = self._get_archive_info(acct)

            # Collect matching messages from INBOX with a single batch
            # messageId fetch instead of one .messageId() IPC call per message.
            gmail_batch = []
            to_process = []
            for msg, mid in _match_messages_by_id(inbox_mb.messages(), target_ids):
                if use_gmail_api:
                    gmail_batch.append(mid)
                else:
                    to_process.append((msg, mid))
                found_ids.add(mid)

            # Gmail API batch archive
            if gmail_batch and gmail_email:
                api_results = self._call_gmail_batch(
                    lambda: self._gmail.archive_messages(gmail_email, gmail_batch), gmail_email, "archiving"
                )
                if api_results is None:
                    for mid in gmail_batch:
                        results.append(
                            {
                                "message_id": mid,
                                "archived": False,
                                "account": acct.name(),
                                "reason": "Gmail account error - see logs",
                            }
                        )
                else:
                    results.extend(api_results)

            # ScriptingBridge archive for non-Gmail accounts
            for msg, mid in to_process:
                if strategy == "archive":
                    msg.moveTo_(archive_mb)
                    results.append(
                        {
                            "message_id": mid,
                            "archived": True,
                            "destination": archive_mb.name(),
                            "account": acct.name(),
                        }
                    )
                else:
                    msg.setReadStatus_(True)
                    results.append(
                        {
                            "message_id": mid,
                            "archived": False,
                            "fallback": "marked_read",
                            "account": acct.name(),
                        }
                    )

        # Pass 2: Gmail API fallback for messages not found in any INBOX.
        # _find_message_id() uses rfc822msgid: which searches all labels,
        # so this handles messages that were moved out of INBOX.
        missing = target_ids - found_ids
        if missing and self._gmail:
            for email in self._gmail.list_authorized_accounts():
                if not missing:
                    break
                api_results = self._call_gmail_batch(
                    lambda e=email: self._gmail.archive_messages(e, list(missing)), email, "archiving (Pass 2 fallback)"
                )
                if api_results is None:
                    continue
                for r in api_results:
                    if r.get("archived"):
                        found_ids.add(r["message_id"])
                        missing.discard(r["message_id"])
                        results.append(r)

        # Pass 3 (issue #89): direct-id lookup fallback for Gmail-internal
        # ids that Pass 1's INBOX scan and Pass 2's rfc822msgid search
        # couldn't find - e.g. a message already archived out of INBOX, or
        # filtered straight to a category/label on arrival. See
        # _resolve_missing_gmail_ids()'s docstring. Uses modify_by_gmail_id()
        # directly, NOT archive_messages() - that treats its input as rfc822
        # Message-IDs and would re-run the exact rfc822msgid search that
        # already failed to resolve these ids in Pass 2.
        if missing:
            id_to_account = self._resolve_missing_gmail_ids(missing)
            by_account: dict[str, list[str]] = {}
            for mid, email in id_to_account.items():
                by_account.setdefault(email, []).append(mid)
            for email, ids in by_account.items():
                api_results = self._call_gmail_batch(
                    lambda e=email, i=ids: self._gmail.modify_by_gmail_id(e, i, remove_label_ids=["INBOX", "TRASH"]),
                    email,
                    "archiving (Pass 3 direct lookup)",
                )
                if api_results is None:
                    continue
                for r in api_results:
                    r["archived"] = r.pop("modified")
                    if r["archived"]:
                        r["destination"] = "All Mail"
                        found_ids.add(r["message_id"])
                        missing.discard(r["message_id"])
                        results.append(r)

        for mid in missing:
            results.append(
                {
                    "message_id": mid,
                    "archived": False,
                    "reason": "not found in any INBOX, rfc822 Message-ID search, or direct Gmail id lookup",
                }
            )

        archived_count = sum(1 for r in results if r.get("archived"))
        # Sync Mail.app if any Gmail API operations were performed
        if any(r.get("destination", "").startswith("All Mail") for r in results):
            _sync_mail_app()
        return {"results": results, "archived": archived_count, "not_found": len(missing)}

    def delete_message(self, message_id):
        """Move a single message to trash."""
        result = self.delete_messages([message_id])
        if result["results"]:
            return result["results"][0]
        raise MailError(f"Message not found: {message_id}")

    def delete_messages(self, message_ids):
        """Delete multiple messages in a single pass.

        Uses Gmail API for Gmail accounts (proper Trash).
        Falls back to ScriptingBridge for iCloud/other accounts.

        Gmail-authorized accounts resolve target ids through up to three
        passes - see archive_messages()'s docstring; Pass 3 (issue #89) is
        shared via _resolve_missing_gmail_ids().
        """
        target_ids = set(message_ids)
        results = []
        found_ids = set()

        for acct, inbox_mb in self._get_account_inboxes():
            use_gmail_api = self._is_gmail_api_account(acct)
            gmail_email = self._get_account_email(acct) if use_gmail_api else None

            # Collect matching messages from INBOX with a single batch
            # messageId fetch instead of one .messageId() IPC call per message.
            gmail_batch = []
            to_delete = []
            for msg, mid in _match_messages_by_id(inbox_mb.messages(), target_ids):
                if use_gmail_api:
                    gmail_batch.append(mid)
                else:
                    to_delete.append((msg, mid))
                found_ids.add(mid)

            # Gmail API batch delete
            if gmail_batch and gmail_email:
                api_results = self._call_gmail_batch(
                    lambda: self._gmail.delete_messages(gmail_email, gmail_batch), gmail_email, "deleting"
                )
                if api_results is None:
                    for mid in gmail_batch:
                        results.append(
                            {
                                "message_id": mid,
                                "deleted": False,
                                "account": acct.name(),
                                "reason": "Gmail account error - see logs",
                            }
                        )
                else:
                    results.extend(api_results)

            # ScriptingBridge delete for non-Gmail accounts
            for msg, mid in to_delete:
                msg.delete()
                results.append({"message_id": mid, "deleted": True, "account": acct.name()})

        # Pass 2: Gmail API fallback for messages not found in any INBOX.
        # _find_message_id() uses rfc822msgid: which searches all labels,
        # so this handles messages that were moved out of INBOX.
        missing = target_ids - found_ids
        if missing and self._gmail:
            for email in self._gmail.list_authorized_accounts():
                if not missing:
                    break
                api_results = self._call_gmail_batch(
                    lambda e=email: self._gmail.delete_messages(e, list(missing)), email, "deleting (Pass 2 fallback)"
                )
                if api_results is None:
                    continue
                for r in api_results:
                    if r.get("deleted"):
                        found_ids.add(r["message_id"])
                        missing.discard(r["message_id"])
                        results.append(r)

        # Pass 3 (issue #89): direct-id lookup fallback for Gmail-internal
        # ids that Pass 1's INBOX scan and Pass 2's rfc822msgid search
        # couldn't find - see archive_messages()'s Pass 3 comment and
        # _resolve_missing_gmail_ids()'s docstring. Uses modify_by_gmail_id()
        # directly, NOT delete_messages() - that treats its input as rfc822
        # Message-IDs and would re-run the exact rfc822msgid search that
        # already failed to resolve these ids in Pass 2.
        if missing:
            id_to_account = self._resolve_missing_gmail_ids(missing)
            by_account: dict[str, list[str]] = {}
            for mid, email in id_to_account.items():
                by_account.setdefault(email, []).append(mid)
            for email, ids in by_account.items():
                api_results = self._call_gmail_batch(
                    lambda e=email, i=ids: self._gmail.modify_by_gmail_id(
                        e, i, add_label_ids=["TRASH"], remove_label_ids=["INBOX"]
                    ),
                    email,
                    "deleting (Pass 3 direct lookup)",
                )
                if api_results is None:
                    continue
                for r in api_results:
                    r["deleted"] = r.pop("modified")
                    if r["deleted"]:
                        found_ids.add(r["message_id"])
                        missing.discard(r["message_id"])
                        results.append(r)

        for mid in missing:
            results.append(
                {
                    "message_id": mid,
                    "deleted": False,
                    "reason": "not found in any INBOX, rfc822 Message-ID search, or direct Gmail id lookup",
                }
            )

        deleted_count = sum(1 for r in results if r.get("deleted"))
        # Sync Mail.app if any Gmail API operations were performed
        if self._gmail and deleted_count > 0:
            _sync_mail_app()
        return {"results": results, "deleted": deleted_count, "not_found": len(missing)}

    # Maps a bulk_action `action` name to the remove_label_ids list passed to
    # batch_modify. archive_and_mark_read combines both label sets into ONE
    # list so it costs a single batchModify call, not two. `hold` (issue #58)
    # removes nothing - it exists purely so a category/call can request
    # apply_label without also archiving or mark-reading.
    _BULK_ACTION_LABELS = {
        "archive": ["INBOX", "TRASH"],
        "mark_read": ["UNREAD"],
        "archive_and_mark_read": ["INBOX", "TRASH", "UNREAD"],
        "hold": [],
    }

    def bulk_action(self, account, query, action, confirm=False, apply_label=None, remove_label=None):
        """Search Gmail directly (native query syntax) and bulk archive/mark-read/label matches.

        Gmail-API-only - checks self._gmail.is_authorized(account) directly
        rather than resolving a ScriptingBridge account object, since this
        tool never touches Mail.app/ScriptingBridge at all (no closed-world
        fallback exists or is wanted here).

        action="hold" performs no archive/read mutation - it exists so a
        category can be purely labeled (issue #58) or unlabeled (issue #85)
        without also being archived or marked read. A hold call with
        neither apply_label nor remove_label would be a fully no-op
        mutation, which is a user error rather than something to silently
        accept - it raises MailError instead.

        remove_label (issue #85) removes a custom Gmail label from every
        match, in the SAME batchModify call as the action's own removals
        (and apply_label's add, when both are set - the "swap" case: move
        matches from one label to another in a single API call).
        Resolution uses find_label(), NEVER resolve_label_id() - removing a
        label that was never applied to any message is a legitimate no-op,
        not something worth silently creating an empty label to "fix". A
        remove_label name that doesn't resolve contributes nothing to
        remove_label_ids and is reported via remove_label_found=False in
        the result, in both confirm states - it never raises.

        confirm=False (default): dry run only. Returns matched_count and a
        sample of matches (plus apply_label/remove_label, echoed back, when
        requested - remove_label also reports remove_label_found so a
        typo'd name is visible before confirm=True); makes no
        batch_modify/mutation call regardless of how many messages match.

        confirm=True: re-runs the search fresh (search_ids, not a reused
        dry-run count - time may have passed since any earlier preview call).
        When apply_label is set, resolves it to a label id via
        GmailClient.resolve_label_id() first (creating the label if it
        doesn't exist yet) and passes it as add_label_ids. remove_label_ids
        is built as a NEW list (self._BULK_ACTION_LABELS[action] plus the
        resolved remove_label id, if any) - never mutating the shared
        class-level list in _BULK_ACTION_LABELS in place. One batchModify
        call does the label-add, the label-remove, and the action's own
        label-remove together. Triggers _sync_mail_app() afterward when
        anything was modified, matching archive_messages/delete_messages.
        """
        if action not in self._BULK_ACTION_LABELS:
            raise MailError(f"Invalid action: {action!r}. Must be one of: {', '.join(self._BULK_ACTION_LABELS)}")

        # Whitespace-only (" ") is truthy but not a usable label name -
        # strip before every truthiness check below so it's treated the
        # same as apply_label/remove_label being omitted entirely, not
        # passed through to create_label()/find_label() as a
        # whitespace-named label.
        if apply_label:
            apply_label = apply_label.strip() or None
        if remove_label:
            remove_label = remove_label.strip() or None

        if action == "hold" and not apply_label and not remove_label:
            raise MailError(
                "action='hold' with no apply_label or remove_label would do nothing - specify one of them to "
                "tag/untag matches, or choose a mutating action (archive, mark_read, archive_and_mark_read)"
            )

        if not self._gmail or not self._gmail.is_authorized(account):
            raise MailError(
                f"Account not authorized for Gmail API: {account}. "
                "mail_bulk_action is Gmail-API-only (no Mail.app fallback) - run gmail_authorize first."
            )

        if not confirm:
            preview = self._gmail.search_messages_preview(account, query)
            result = {"matched_count": preview["matched_count"], "sample": preview["sample"], "query": query}
            if apply_label:
                result["apply_label"] = apply_label
            if remove_label:
                result["remove_label"] = remove_label
                result["remove_label_found"] = self._gmail.find_label(account, remove_label) is not None
            return result

        gmail_ids = self._gmail.search_ids(account, query)
        remove_label_ids = list(self._BULK_ACTION_LABELS[action])

        add_label_ids = None
        label_created = False
        if apply_label:
            label_id, label_created = self._gmail.resolve_label_id(account, apply_label)
            add_label_ids = [label_id]

        remove_label_found = None
        if remove_label:
            found = self._gmail.find_label(account, remove_label)
            remove_label_found = found is not None
            if found is not None:
                remove_label_ids = remove_label_ids + [found["id"]]

        modified_ids = self._gmail.batch_modify(
            account, gmail_ids, add_label_ids=add_label_ids, remove_label_ids=remove_label_ids
        )

        if modified_ids:
            _sync_mail_app()

        result = {
            "matched_count": len(gmail_ids),
            "modified_count": len(modified_ids),
            "action": action,
            "query": query,
        }
        if apply_label:
            result["apply_label"] = apply_label
            result["label_created"] = label_created
        if remove_label:
            result["remove_label"] = remove_label
            result["remove_label_found"] = remove_label_found
        return result

    def list_labels(self, account):
        """List every label (system + custom) for a Gmail-authorized account.

        Gmail-API-only, same guard as bulk_action() - Mail.app/ScriptingBridge
        has no equivalent concept of Gmail's flat label namespace, so there
        is no closed-world fallback to fall back to.
        """
        if not self._gmail or not self._gmail.is_authorized(account):
            raise MailError(
                f"Account not authorized for Gmail API: {account}. "
                "mail_labels is Gmail-API-only (no Mail.app fallback) - run gmail_authorize first."
            )
        return self._gmail.list_labels(account)

    def delete_label(self, account, name, confirm=False):
        """Delete a custom Gmail label by name, previewing message impact first.

        Gmail-API-only, same guard as list_labels()/bulk_action().
        Idempotent by design (issue #85's Definition of Done): a name that
        doesn't resolve returns {"label": name, "found": False} in EITHER
        confirm state, never an error - calling this twice on an
        already-deleted label (or a name that was never a label) is a
        safe, structurally guaranteed no-op.

        Deleting a label can never change a message's INBOX/UNREAD/TRASH
        state - GmailClient.delete_label() only ever issues a DELETE
        users/me/labels/{id} call, which is structurally incapable of a
        batchModify/message-state mutation.

        confirm=False (default): looks up the label and its
        messages_affected count (via search_messages_preview's exact
        matched_count) but does not delete. confirm=True deletes, after
        computing that same messages_affected count first - so the
        response always reports how many messages carried the label,
        regardless of confirm state.
        """
        name = name.strip() if name else name
        if not self._gmail or not self._gmail.is_authorized(account):
            raise MailError(
                f"Account not authorized for Gmail API: {account}. "
                "mail_label_delete is Gmail-API-only (no Mail.app fallback) - run gmail_authorize first."
            )

        found = self._gmail.find_label(account, name)
        if found is None:
            return {"label": name, "found": False}

        # Quoted so a multi-word label name (e.g. "Needs Review") is matched
        # as a single label: term, not split into label:Needs plus a
        # free-text "Review" term - an unquoted query would silently under-
        # or over-count messages_affected for any label name with a space.
        messages_affected = self._gmail.search_messages_preview(account, f'label:"{name}"')["matched_count"]

        if not confirm:
            return {"label": name, "found": True, "messages_affected": messages_affected}

        self._gmail.delete_label(account, name)
        return {"label": name, "deleted": True, "messages_affected": messages_affected}

    def rename_label(self, account, old_name, new_name):
        """Rename a custom Gmail label, preserving its Gmail-internal id.

        Gmail-API-only, same guard as list_labels()/delete_label(). Unlike
        delete_label() (tolerant of a not-found name, since it's the same
        idempotent-delete no-op either way), old_name here names a single
        specific target - a typo should surface immediately as a
        MailError, not silently no-op, so a not-found old_name raises
        rather than returning a structured "not found" response.

        Every message that carried the label keeps it under the new name:
        GmailClient.rename_label() PATCHes the same label id in place, it
        never reassigns any message's labelIds.
        """
        old_name = old_name.strip() if old_name else old_name
        new_name = new_name.strip() if new_name else new_name
        if not self._gmail or not self._gmail.is_authorized(account):
            raise MailError(
                f"Account not authorized for Gmail API: {account}. "
                "mail_label_rename is Gmail-API-only (no Mail.app fallback) - run gmail_authorize first."
            )

        found = self._gmail.find_label(account, old_name)
        if found is None:
            raise MailError(f"Label not found: {old_name!r}")

        self._gmail.rename_label(account, old_name, new_name)
        return {"old_name": old_name, "new_name": new_name, "renamed": True}

    def apply_rules(self, account, category_ids=None, confirm=False):
        """Apply persisted sender rules (mail_tools/rules.py) to an account,
        calling the EXISTING bulk_action() once per targeted category - no
        query-building or batch-modify logic is reimplemented here.

        Targets all status=="active" categories by default, or the explicit
        subset named in category_ids (comma-separated ids). Regardless of
        what's requested, any category whose status is not "active" is
        filtered out BEFORE any mutation ever happens - this is a hard
        safety requirement (issue #48), not a convention: a
        leave_alone/paused/needs_review category named in category_ids under
        confirm=True still produces zero mutations for that category,
        reported in `skipped` with an explicit reason.

        A category whose default_action is "hold" is filtered out the same
        way UNLESS it also carries an apply_label (issue #58) - a
        hold+apply_label category is deliberately targeted, since "hold"
        only means "never archive/mark-read", not "never touch at all".
        bulk_action() itself enforces that a hold action always requires
        apply_label, so a hold category with no apply_label reaching
        bulk_action would raise rather than silently no-op; filtering it out
        here instead keeps that case a clean, expected `skipped` entry.

        confirm=False (default): dry-run per targeted active category via
        bulk_action(confirm=False), aggregating matched_count. Also returns
        stale_reviews (rules.stale_categories(), covering ALL categories
        regardless of status) as an informational nudge. No mutation happens
        in this mode, structurally - the same bulk_action(confirm=False)
        branch that guarantees mail_bulk_action never mutates.

        confirm=True: re-calls bulk_action(confirm=True) per targeted
        category (bulk_action itself re-runs the search fresh - no reuse of
        a dry-run count), aggregating modified_count, and writes back
        last_reviewed=today via save_rules() after EACH category's mutation
        completes (not once after the full loop) - see in-loop comment for
        why. Skipped categories are never touched.
        """
        from mail_tools.rules import build_category_query, load_rules, save_rules, stale_categories

        rules = load_rules()
        all_categories = rules.get("categories", [])
        by_id = {c["id"]: c for c in all_categories}

        if category_ids:
            requested_ids = [cid.strip() for cid in category_ids.split(",") if cid.strip()]
        else:
            requested_ids = [c["id"] for c in all_categories if c.get("status") == "active"]

        targeted = []
        skipped = []
        for cid in requested_ids:
            category = by_id.get(cid)
            if category is None:
                skipped.append({"id": cid, "reason": "unknown category id"})
            elif category.get("status") != "active":
                skipped.append({"id": cid, "reason": f"status is {category.get('status')!r}, not 'active'"})
            elif category.get("default_action") == "hold" and not category.get("apply_label"):
                skipped.append(
                    {"id": cid, "reason": "default_action is 'hold' with no apply_label (never auto-mutated)"}
                )
            elif not category.get("senders"):
                # An empty or missing `senders` list would make
                # build_category_query() raise (or, absent that guard,
                # produce a degenerate "() is:unread" query that Gmail
                # treats as a no-op filter, matching every unread message
                # in the account). Reject it here, before any query is
                # built or bulk_action is ever called, same as the status
                # and hold checks above.
                skipped.append({"id": cid, "reason": "no senders configured - refusing to build a query"})
            else:
                targeted.append(category)

        today = date.today().isoformat()
        categories_result = []
        total_matched = 0
        total_modified = 0
        for category in targeted:
            query = build_category_query(category)
            action_result = self.bulk_action(
                account,
                query,
                category["default_action"],
                confirm=confirm,
                apply_label=category.get("apply_label"),
            )
            total_matched += action_result.get("matched_count", 0)
            total_modified += action_result.get("modified_count", 0)
            categories_result.append(
                {
                    "id": category["id"],
                    "label": category.get("label"),
                    **action_result,
                }
            )
            if confirm:
                # last_reviewed marks that the category was actually
                # executed via bulk_action(confirm=True) this cycle - not
                # that it matched at least one message. A zero-match run
                # (modified_count == 0) is still a completed review: the
                # system checked Gmail and found nothing to act on. Treating
                # that as "not reviewed" would make the category perpetually
                # resurface via stale_categories() even though it was, in
                # fact, checked on schedule.
                category["last_reviewed"] = today
                # Save after EACH category, not once after the whole loop.
                # bulk_action() mutations against Gmail are irreversible;
                # if a later category in `targeted` raises mid-loop, any
                # earlier category's last_reviewed bump must already be on
                # disk, or it's lost when the exception propagates past a
                # single save-at-the-end call.
                save_rules(rules)

        response = {
            "account": account,
            "categories": categories_result,
            "skipped": skipped,
            "total_matched_count": total_matched,
        }
        if confirm:
            response["total_modified_count"] = total_modified
        else:
            response["stale_reviews"] = stale_categories(rules, date.today())
        return response

    def compose_message(self, to, subject, body, cc=None, bcc=None, from_account=None, send=False):
        """Create a new outgoing message (draft or send).

        Routes to the Gmail API when from_account resolves to a
        Gmail-authorized account (issue #50) - see _is_gmail_api_account.
        When from_account is omitted, there is no way to know which account
        Mail.app would pick as its implicit default before choosing a route,
        so this keeps the existing ScriptingBridge default-account path as a
        documented, intentional exception: account resolution (and Gmail
        routing) is skipped entirely when from_account isn't given.

        When from_account is given but doesn't resolve to a known account
        (typo, stale name), this raises rather than silently falling through
        to Mail.app's default-identity ScriptingBridge send - a caller who
        explicitly asked for a specific account needs to know that request
        was ignored.
        """
        acct = self._find_account(from_account) if from_account else None
        if from_account and acct is None:
            raise MailError(f"Account not found: {from_account}")
        if acct is not None and self._is_gmail_api_account(acct):
            gmail_email = self._get_account_email(acct)
            if send:
                return self._gmail.send_message(gmail_email, to, subject, body, cc=cc, bcc=bcc)
            return self._gmail.create_draft(gmail_email, to, subject, body, cc=cc, bcc=bcc)

        # ScriptingBridge path: iCloud/other accounts, or from_account omitted.
        mail = self.app

        props = {
            "subject": subject,
            "content": body,
            "visible": not send,
        }

        msg_class = mail.classForScriptingClass_("outgoing message")
        msg = msg_class.alloc().initWithProperties_(props)
        mail.outgoingMessages().addObject_(msg)

        to_class = mail.classForScriptingClass_("to recipient")
        for addr in _parse_addresses(to):
            recip = to_class.alloc().initWithProperties_({"address": addr})
            msg.toRecipients().addObject_(recip)

        if cc:
            cc_class = mail.classForScriptingClass_("cc recipient")
            for addr in _parse_addresses(cc):
                recip = cc_class.alloc().initWithProperties_({"address": addr})
                msg.ccRecipients().addObject_(recip)

        if bcc:
            bcc_class = mail.classForScriptingClass_("bcc recipient")
            for addr in _parse_addresses(bcc):
                recip = bcc_class.alloc().initWithProperties_({"address": addr})
                msg.bccRecipients().addObject_(recip)

        if acct and acct.emailAddresses():
            msg.setSender_(list(acct.emailAddresses())[0])

        if send:
            msg.send()

        return {
            "to": to,
            "subject": subject,
            "status": "sent" if send else "draft",
        }

    def reply_to_message(self, message_id, body, reply_all=False, send=False):
        """Reply to a message.

        Routes to the Gmail API when the original message's account is
        Gmail-authorized (issue #50) - see _is_gmail_api_account. The reply
        is threaded via resolve_thread(); if resolve_thread can't find the
        message via the Gmail API (e.g. it was moved/deleted since
        ScriptingBridge last saw it), the reply is still sent/drafted, just
        without In-Reply-To/References/threadId, rather than raising - a
        degraded (unthreaded) reply is more useful than a hard failure.
        """
        msg, acct = self._find_message_fast_with_account(message_id)
        if msg is None:
            raise MailError(f"Message not found: {message_id}")

        original_subject = msg.subject() or ""
        original_sender = msg.sender() or ""
        original_message_id = msg.messageId() or None

        reply_to = _extract_email(original_sender)
        re_subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"

        to_addrs = reply_to
        if reply_all:
            extras = []
            for r in msg.toRecipients():
                addr = r.address()
                if addr:
                    extras.append(addr)
            for r in msg.ccRecipients():
                addr = r.address()
                if addr:
                    extras.append(addr)
            if extras:
                to_addrs = f"{reply_to}, {', '.join(extras)}"

        if acct is not None and self._is_gmail_api_account(acct):
            gmail_email = self._get_account_email(acct)
            thread_id = None
            if original_message_id:
                thread_info = self._gmail.resolve_thread(gmail_email, original_message_id)
                if thread_info is None:
                    logger.warning(
                        "resolve_thread found no Gmail match for %s - sending reply unthreaded",
                        original_message_id,
                    )
                else:
                    thread_id = thread_info["thread_id"]
            if send:
                return self._gmail.send_message(
                    gmail_email,
                    to_addrs,
                    re_subject,
                    body,
                    in_reply_to=original_message_id,
                    references=original_message_id,
                    thread_id=thread_id,
                )
            return self._gmail.create_draft(
                gmail_email,
                to_addrs,
                re_subject,
                body,
                in_reply_to=original_message_id,
                references=original_message_id,
                thread_id=thread_id,
            )

        return self.compose_message(
            to=to_addrs,
            subject=re_subject,
            body=body,
            send=send,
        )

    # ── Internal helpers ──

    def _get_mailbox_messages(self, mailbox, account):
        """Get messages from a specific mailbox."""
        if mailbox.upper() == "INBOX" and account:
            for acct, inbox_mb in self._get_account_inboxes(account):
                return inbox_mb.messages()
            raise MailError(f"INBOX not found for account: {account}")

        mb = self._find_mailbox(mailbox, account)
        if mb is None:
            raise MailError(f"Mailbox not found: {mailbox}")
        return mb.messages()

    def _find_account(self, name):
        """Find an account by name or email (case-insensitive)."""
        for acct in self._get_accounts():
            if _account_matches(acct, name):
                return acct
        return None

    def _find_mailbox(self, mailbox_name, account_name=None):
        """Find a mailbox by name, optionally scoped to an account."""
        mb_lower = mailbox_name.lower()

        if account_name:
            acct = self._find_account(account_name)
            if acct is None:
                return None
            for mb in acct.mailboxes():
                if mb.name().lower() == mb_lower:
                    return mb
        else:
            for acct in self._get_accounts():
                for mb in acct.mailboxes():
                    if mb.name().lower() == mb_lower:
                        return mb
        return None

    def _find_message_fast(self, message_id):
        """Find a message by ID using batch ID fetch for speed.

        Searches the unified inbox first (fastest path), then falls back
        to scanning all mailboxes per account so messages in Trash,
        Archive, Sent, etc. can also be found.
        """
        msg = self._find_message_unified_inbox(message_id)
        if msg is not None:
            return msg
        return self._find_message_all_mailboxes(message_id)

    def _find_message_unified_inbox(self, message_id):
        """Fast path: check the unified inbox only, via a single batch
        arrayByApplyingSelector_ IPC call.

        Split out of _find_message_fast (issue #87) so read_message() can
        run the Gmail-authorized-account check between this fast path and
        the slow _find_message_all_mailboxes() fallback below.
        """
        sb_msgs = self.app.inbox().messages()
        all_ids = list(sb_msgs.arrayByApplyingSelector_("messageId"))

        try:
            idx = all_ids.index(message_id)
            return sb_msgs[idx]
        except ValueError:
            return None

    def _find_message_all_mailboxes(self, message_id):
        """Slow path: scan all mailboxes across all accounts.

        Split out of _find_message_fast (issue #87) - see
        _find_message_unified_inbox's docstring.
        """
        for acct in self._get_accounts():
            for mb in acct.mailboxes():
                try:
                    mb_msgs = mb.messages()
                    mb_ids = list(mb_msgs.arrayByApplyingSelector_("messageId"))
                    idx = mb_ids.index(message_id)
                    return mb_msgs[idx]
                except (ValueError, Exception):
                    continue

        return None

    def _find_message_fast_with_account(self, message_id):
        """Same lookup as _find_message_fast, but also returns the owning
        account - needed for reply routing (Gmail API vs ScriptingBridge,
        issue #50).

        Unlike _find_message_fast's fast path (the unified self.app.inbox(),
        which has no notion of which account a message belongs to), this
        scans each account's own INBOX via _get_account_inboxes() first -
        still one batch arrayByApplyingSelector_ call per account via
        _match_messages_by_id, not a per-message IPC call. Falls back to
        scanning all mailboxes per account, same as _find_message_fast's
        slow path.
        """
        for acct, inbox_mb in self._get_account_inboxes():
            for msg, mid in _match_messages_by_id(inbox_mb.messages(), {message_id}):
                return msg, acct

        for acct in self._get_accounts():
            for mb in acct.mailboxes():
                try:
                    mb_msgs = mb.messages()
                    mb_ids = list(mb_msgs.arrayByApplyingSelector_("messageId"))
                    idx = mb_ids.index(message_id)
                    return mb_msgs[idx], acct
                except (ValueError, Exception):
                    continue

        return None, None


# ── Helpers ──

# GmailClient.list_messages()/search_messages() fetch a single page
# (maxResults=<n>, no pagination - see gmail.py) rather than the full
# mailbox ScriptingBridge always fetches. When unread_only is requested,
# pad the requested page size so a run of already-read recent messages
# doesn't push a genuinely-unread older message out of the fetched page
# before list_messages()'s merged sort/filter/limit ever sees it. This is a
# mitigation, not a guarantee - see list_messages()'s docstring.
_GMAIL_LIST_FETCH_PAD = 5
_GMAIL_LIST_FETCH_CAP = 200


def _gmail_list_fetch_limit(limit, unread_only):
    """Page size to request from GmailClient.list_messages() for one account."""
    if not unread_only:
        return limit
    return min(limit * _GMAIL_LIST_FETCH_PAD, _GMAIL_LIST_FETCH_CAP)


def _account_matches(acct, name):
    """Check if an account matches a name or email (case-insensitive)."""
    name_lower = name.lower()
    if acct.name().lower() == name_lower:
        return True
    for email in acct.emailAddresses() or []:
        if email.lower() == name_lower:
            return True
    return False


def _match_messages_by_id(sb_messages, target_ids):
    """Find messages whose messageId is in target_ids via one batch IPC call.

    Replaces a Python loop calling msg.messageId() once per message (N Apple
    Event IPC calls) with a single arrayByApplyingSelector_ call, then a
    set-intersection filter. Returns a list of (msg, message_id) pairs.
    """
    mids = list(sb_messages.arrayByApplyingSelector_("messageId"))
    return [(sb_messages[i], mid) for i, mid in enumerate(mids) if mid in target_ids]


def _batch_serialize_messages(sb_messages, limit=None):
    """Batch-serialize messages using arrayByApplyingSelector_ for speed.

    Instead of 6 IPC calls per message (N*6 total), this makes exactly
    6 IPC calls regardless of message count - one per property.
    Returns a list of dicts.
    """
    subjects = list(sb_messages.arrayByApplyingSelector_("subject"))
    senders = list(sb_messages.arrayByApplyingSelector_("sender"))
    mids = list(sb_messages.arrayByApplyingSelector_("messageId"))
    dates = list(sb_messages.arrayByApplyingSelector_("dateReceived"))
    reads = list(sb_messages.arrayByApplyingSelector_("readStatus"))
    flags = list(sb_messages.arrayByApplyingSelector_("flaggedStatus"))

    count = len(subjects)
    if limit and limit < count:
        count = limit

    results = []
    for i in range(count):
        date_str = None
        d = dates[i]
        if d and d != 0:  # NSNull/missing value check
            try:
                ts = d.timeIntervalSince1970()
                date_str = datetime.fromtimestamp(ts).astimezone().isoformat()
            except Exception:
                pass

        results.append(
            {
                "message_id": mids[i] if mids[i] else None,
                "subject": subjects[i] or "(no subject)",
                "from": senders[i] or None,
                "date": date_str,
                "read": bool(reads[i]),
                "flagged": bool(flags[i]),
            }
        )

    return results


def _parse_addresses(addr_string):
    """Split a comma-separated address string into individual addresses."""
    return [a.strip() for a in addr_string.split(",") if a.strip()]


def _extract_email(sender_string):
    """Extract email address from 'Name <email>' format."""
    if "<" in sender_string and ">" in sender_string:
        start = sender_string.index("<") + 1
        end = sender_string.index(">")
        return sender_string[start:end]
    return sender_string.strip()
