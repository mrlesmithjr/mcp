"""iMessage read/write layer - SQLite reads from chat.db, AppleScript sends."""

import json
import logging
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Apple's epoch offset: 2001-01-01 00:00:00 UTC in Unix time
_APPLE_EPOCH = 978307200

# chat.db stores dates in nanoseconds since Apple epoch
_NS_FACTOR = 1_000_000_000

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
ACCESS_FILE = Path.home() / ".config" / "imessage-tools" / "access.json"


def _apple_to_datetime(apple_ns: int | None) -> str | None:
    """Convert Apple nanosecond timestamp to ISO 8601 string."""
    if not apple_ns:
        return None
    unix_ts = (apple_ns / _NS_FACTOR) + _APPLE_EPOCH
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).astimezone().isoformat()


def _run_applescript(script: str) -> str:
    """Run an AppleScript and return stdout."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"AppleScript error: {result.stderr.strip()}")
    return result.stdout.strip()


def _escape_applescript(text: str) -> str:
    """Escape a string for safe inclusion in AppleScript double-quoted strings."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


class MessagesManager:
    """Read from chat.db, send via AppleScript, access-controlled."""

    def __init__(self):
        if not CHAT_DB.exists():
            raise FileNotFoundError(f"chat.db not found at {CHAT_DB}")
        self._access = self._load_access()

        from imessage_tools.contacts import ContactResolver

        self._contacts = ContactResolver()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_access(self) -> dict:
        if not ACCESS_FILE.exists():
            raise RuntimeError(
                f"Missing required config: allow. "
                f"Create {ACCESS_FILE} with an allowlist of approved handles. "
                f"See access.json.example for the expected format."
            )
        with open(ACCESS_FILE) as f:
            return json.load(f)

    def _is_allowed(self, handle_id: str) -> bool:
        """Check if a handle (phone/email) is in the allowlist."""
        allowed = self._access.get("allow", [])
        if not allowed:
            return False
        # Normalize: strip spaces, compare
        normalized = handle_id.replace(" ", "").replace("(", "").replace(")", "").replace("-", "")
        return any(normalized == a.replace(" ", "").replace("(", "").replace(")", "").replace("-", "") for a in allowed)

    def _chat_guid_for_handle(self, handle: str) -> str | None:
        """Look up the chat GUID for a DM with a given handle from chat.db."""
        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT c.guid
                FROM chat c
                JOIN chat_handle_join chj ON chj.chat_id = c.rowid
                JOIN handle h ON h.rowid = chj.handle_id
                WHERE h.id = ?
                  AND c.style = 43
                ORDER BY c.rowid DESC
                LIMIT 1
                """,
                (handle,),
            ).fetchone()
            return row["guid"] if row else None
        finally:
            conn.close()

    def reload_access(self):
        """Reload access.json from disk."""
        self._access = self._load_access()

    def _save_access(self):
        """Write current access config back to disk."""
        ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ACCESS_FILE, "w") as f:
            json.dump(self._access, f, indent=2)
            f.write("\n")

    def access_list(self) -> list[str]:
        """Return the current allowlist."""
        return self._access.get("allow", [])

    def access_add(self, handle: str) -> dict:
        """Add a handle to the allowlist and persist."""
        allowed = self._access.setdefault("allow", [])
        normalized = handle.replace(" ", "").replace("(", "").replace(")", "").replace("-", "")
        # Check if already present
        for existing in allowed:
            existing_norm = existing.replace(" ", "").replace("(", "").replace(")", "").replace("-", "")
            if existing_norm == normalized:
                return {"added": False, "handle": handle, "reason": "already in allowlist"}
        allowed.append(normalized)
        self._save_access()
        return {"added": True, "handle": normalized}

    def access_remove(self, handle: str) -> dict:
        """Remove a handle from the allowlist and persist."""
        allowed = self._access.get("allow", [])
        normalized = handle.replace(" ", "").replace("(", "").replace(")", "").replace("-", "")
        new_allowed = [
            a for a in allowed if a.replace(" ", "").replace("(", "").replace(")", "").replace("-", "") != normalized
        ]
        if len(new_allowed) == len(allowed):
            return {"removed": False, "handle": handle, "reason": "not found in allowlist"}
        self._access["allow"] = new_allowed
        self._save_access()
        return {"removed": True, "handle": normalized}

    def chat_list(self, days: int = 30) -> list[dict]:
        """List recent chats with last message date, scoped to allowlist.

        Args:
            days: Only show chats with messages in the last N days (default: 30)
        """
        conn = self._conn()
        try:
            # Calculate cutoff in Apple nanoseconds
            import time

            cutoff_unix = time.time() - (days * 86400)
            cutoff_apple_ns = int((cutoff_unix - _APPLE_EPOCH) * _NS_FACTOR)

            rows = conn.execute(
                """
                SELECT c.guid, c.chat_identifier, c.display_name, c.service_name,
                       c.style,
                       MAX(cmj.message_date) as last_message_date
                FROM chat c
                LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.rowid
                GROUP BY c.rowid
                HAVING last_message_date > ?
                ORDER BY last_message_date DESC
                """,
                (cutoff_apple_ns,),
            ).fetchall()

            chats = []
            for row in rows:
                identifier = row["chat_identifier"] or ""
                participants = self._get_chat_participants(conn, row["guid"])
                is_group = len(participants) > 1

                if is_group:
                    # Allow if any participant is in allowlist
                    if not any(self._is_allowed(p) for p in participants):
                        continue
                    chat_type = "group"
                    resolved = [self._contacts.resolve_or_handle(p) for p in participants]
                    display = row["display_name"] or ", ".join(resolved)
                else:
                    if not self._is_allowed(identifier):
                        continue
                    chat_type = "dm"
                    display = self._contacts.resolve_or_handle(identifier)

                chats.append(
                    {
                        "chat_id": row["guid"],
                        "identifier": identifier,
                        "display_name": display,
                        "type": chat_type,
                        "service": row["service_name"],
                        "last_message": _apple_to_datetime(row["last_message_date"]),
                    }
                )

            return chats
        finally:
            conn.close()

    def _get_chat_participants(self, conn: sqlite3.Connection, chat_guid: str) -> list[str]:
        """Get participant handles for a chat."""
        rows = conn.execute(
            """
            SELECT h.id
            FROM handle h
            JOIN chat_handle_join chj ON chj.handle_id = h.rowid
            JOIN chat c ON c.rowid = chj.chat_id
            WHERE c.guid = ?
            """,
            (chat_guid,),
        ).fetchall()
        return [r["id"] for r in rows]

    def chat_messages(self, chat_id: str, limit: int = 50) -> list[dict]:
        """Read messages from a specific chat by GUID."""
        # Verify chat is allowed
        conn = self._conn()
        try:
            chat_row = conn.execute(
                "SELECT chat_identifier, style FROM chat WHERE guid = ?",
                (chat_id,),
            ).fetchone()
            if not chat_row:
                raise ValueError(f"Chat not found: {chat_id}")

            participants = self._get_chat_participants(conn, chat_id)
            is_group = len(participants) > 1

            if is_group:
                if not any(self._is_allowed(p) for p in participants):
                    raise PermissionError("Chat not in allowlist")
            else:
                if not self._is_allowed(chat_row["chat_identifier"]):
                    raise PermissionError(f"Handle {chat_row['chat_identifier']} not in allowlist")

            rows = conn.execute(
                """
                SELECT m.guid, m.text, m.date, m.is_from_me,
                       m.associated_message_type,
                       h.id as handle,
                       m.cache_has_attachments,
                       m.attributedBody
                FROM message m
                JOIN chat_message_join cmj ON cmj.message_id = m.rowid
                JOIN chat c ON c.rowid = cmj.chat_id
                LEFT JOIN handle h ON m.handle_id = h.rowid
                WHERE c.guid = ?
                ORDER BY m.date DESC
                LIMIT ?
                """,
                (chat_id, limit),
            ).fetchall()

            messages = []
            for row in rows:
                text = row["text"]
                # Try to extract text from attributedBody if text is None
                if text is None and row["attributedBody"]:
                    text = self._extract_attributed_text(row["attributedBody"])
                # If still no text and has attachments, label it
                if not text and row["cache_has_attachments"]:
                    text = "(attachment)"

                raw_sender = row["handle"] or "unknown"
                sender = "me" if row["is_from_me"] else self._contacts.resolve_or_handle(raw_sender)

                messages.append(
                    {
                        "id": row["guid"],
                        "text": text,
                        "date": _apple_to_datetime(row["date"]),
                        "from_me": bool(row["is_from_me"]),
                        "sender": sender,
                        "has_attachments": bool(row["cache_has_attachments"]),
                    }
                )

            # Return in chronological order
            messages.reverse()
            return messages
        finally:
            conn.close()

    def _extract_attributed_text(self, blob: bytes) -> str | None:
        """Extract plain text from NSAttributedString binary blob."""
        try:
            import re

            decoded = blob.decode("utf-8", errors="replace")
            # Find readable strings of 5+ chars, skip known class/key names
            skip = {
                "streamtyped",
                "NSMutableAttributedString",
                "NSAttributedString",
                "NSObject",
                "NSMutableString",
                "NSString",
                "NSDictionary",
                "NSArray",
                "NSValue",
                "NS.bytes",
                "NS.keys",
                "NS.objects",
                "__kIMMessagePartAttributeName",
            }
            matches = re.findall(r"[\x20-\x7e]{5,}", decoded)
            for m in matches:
                stripped = m.strip()
                if stripped and stripped not in skip and not stripped.startswith("NS"):
                    # Strip binary encoding prefix artifacts (e.g. "+Y", "+N", "+>")
                    if len(stripped) > 2 and stripped[0] == "+" and not stripped[1].isdigit():
                        stripped = stripped[2:]
                    # Skip internal attribute keys
                    if stripped.startswith("__kIM") or stripped.startswith("kIM"):
                        continue
                    # Skip quoted attribute keys
                    if stripped.startswith('"__kIM') or stripped.startswith('"kIM'):
                        continue
                    return stripped
            return None
        except Exception:
            return None

    def unread_summary(self) -> list[dict]:
        """Get chats with unread messages, scoped to allowlist."""
        conn = self._conn()
        try:
            # Messages where is_read = 0 and is_from_me = 0
            rows = conn.execute(
                """
                SELECT c.guid, c.chat_identifier, c.display_name, c.style,
                       COUNT(*) as unread_count,
                       MAX(m.date) as latest_date
                FROM message m
                JOIN chat_message_join cmj ON cmj.message_id = m.rowid
                JOIN chat c ON c.rowid = cmj.chat_id
                WHERE m.is_from_me = 0
                  AND m.is_read = 0
                  AND m.text IS NOT NULL
                GROUP BY c.rowid
                ORDER BY latest_date DESC
                """
            ).fetchall()

            results = []
            for row in rows:
                identifier = row["chat_identifier"] or ""
                participants = self._get_chat_participants(conn, row["guid"])
                is_group = len(participants) > 1

                if is_group:
                    if not any(self._is_allowed(p) for p in participants):
                        continue
                    resolved = [self._contacts.resolve_or_handle(p) for p in participants]
                    display = row["display_name"] or ", ".join(resolved)
                else:
                    if not self._is_allowed(identifier):
                        continue
                    display = self._contacts.resolve_or_handle(identifier)

                results.append(
                    {
                        "chat_id": row["guid"],
                        "display_name": display,
                        "unread_count": row["unread_count"],
                        "latest_message": _apple_to_datetime(row["latest_date"]),
                    }
                )

            return results
        finally:
            conn.close()

    def search_messages(self, query: str, limit: int = 20) -> list[dict]:
        """Search message text across allowed chats.

        Paginates through matches in date-descending order, applying the
        allowlist filter to each page before deciding whether to fetch more -
        rather than truncating to a fixed candidate window before filtering.
        The allowlist is opt-in and chat.db accumulates years of handles, so
        a fixed multiplier over `limit` can be entirely consumed by
        disallowed handles, silently under-returning or zero-returning
        results even when enough allowed matches exist further down the
        date-ordered result set.

        Pagination uses a keyset (seek) cursor on (m.date, m.rowid) rather
        than SQL OFFSET. chat.db is Messages.app's live database and is
        continuously written while we read it: OFFSET pagination is
        positional, so a row inserted between two page fetches shifts every
        later row's offset by one, which can re-return the last row of the
        previous page (chat.db has no dedup-by-id downstream) while silently
        skipping the row that should have landed at the new page boundary.
        A keyset cursor seeks strictly past the last row actually returned,
        so newly inserted rows (which sort above the cursor, being newer)
        never perturb pages already walked past. `ORDER BY m.date DESC` alone
        also leaves ties on the same timestamp non-deterministically ordered
        across separate query executions; adding `m.rowid DESC` as a
        tiebreaker makes the ordering total and the cursor unambiguous.
        """
        conn = self._conn()
        try:
            results = []
            batch_size = max(limit * 3, 100)
            like_query = f"%{query}%"
            last_date: int | None = None
            last_rowid: int | None = None

            base_select = """
                SELECT m.guid, m.text, m.date, m.rowid as msg_rowid, m.is_from_me,
                       h.id as handle,
                       c.guid as chat_guid, c.chat_identifier, c.display_name, c.style
                FROM message m
                JOIN chat_message_join cmj ON cmj.message_id = m.rowid
                JOIN chat c ON c.rowid = cmj.chat_id
                LEFT JOIN handle h ON m.handle_id = h.rowid
                WHERE m.text LIKE ?
            """

            while len(results) < limit:
                if last_date is None:
                    rows = conn.execute(
                        base_select + " ORDER BY m.date DESC, m.rowid DESC LIMIT ?",
                        (like_query, batch_size),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        base_select
                        + " AND (m.date < ? OR (m.date = ? AND m.rowid < ?))"
                        + " ORDER BY m.date DESC, m.rowid DESC LIMIT ?",
                        (like_query, last_date, last_date, last_rowid, batch_size),
                    ).fetchall()

                if not rows:
                    break  # source exhausted - no more candidates past the cursor

                for row in rows:
                    identifier = row["chat_identifier"] or ""
                    participants = self._get_chat_participants(conn, row["chat_guid"])
                    is_group = len(participants) > 1

                    if is_group:
                        if not any(self._is_allowed(p) for p in participants):
                            continue
                    else:
                        if not self._is_allowed(identifier):
                            continue

                    raw_sender = row["handle"] or "unknown"
                    sender = "me" if row["is_from_me"] else self._contacts.resolve_or_handle(raw_sender)
                    chat_name = row["display_name"] or self._contacts.resolve_or_handle(identifier)

                    results.append(
                        {
                            "message_id": row["guid"],
                            "chat_id": row["chat_guid"],
                            "chat_name": chat_name,
                            "text": row["text"],
                            "date": _apple_to_datetime(row["date"]),
                            "from_me": bool(row["is_from_me"]),
                            "sender": sender,
                        }
                    )

                    if len(results) >= limit:
                        break

                # Advance the cursor to the last row of this batch (not the
                # last row we kept) so the next page always seeks past every
                # candidate we already considered, allowed or not.
                last_row = rows[-1]
                last_date = last_row["date"]
                last_rowid = last_row["msg_rowid"]

            return results
        finally:
            conn.close()

    def resolve_recipient(self, to: str) -> str:
        """Resolve a recipient - accepts phone number, email, or contact name.

        Returns the phone number to send to, or raises if not found/not allowed.
        """
        # If it looks like a phone number or email, use directly
        if to.startswith("+") or "@" in to or to.replace("-", "").replace(" ", "").isdigit():
            return to

        # Try contact name resolution
        matches = self._contacts.search_by_name(to)
        if not matches:
            raise ValueError(f"No contact found matching '{to}'")

        # Filter to allowed numbers only
        allowed_matches = []
        for match in matches:
            for phone in match["phones"]:
                if self._is_allowed(phone["number"]):
                    allowed_matches.append(
                        {
                            "name": match["name"],
                            "number": phone["number"],
                            "label": phone["label"],
                        }
                    )

        if not allowed_matches:
            names = ", ".join(m["name"] for m in matches)
            raise PermissionError(
                f"Found contact(s) ({names}) but no allowed phone numbers. Use access_add to allow their number first."
            )

        if len(allowed_matches) == 1:
            return allowed_matches[0]["number"]

        # Multiple matches - raise with options for caller to disambiguate
        options = [f"{m['name']} ({m['label']}: {m['number']})" for m in allowed_matches]
        raise ValueError(
            f"Multiple allowed numbers found for '{to}': {', '.join(options)}. Specify the phone number directly."
        )

    def _is_chat_allowed(self, chat_id: str) -> bool:
        """Check if a chat ID is allowed by verifying its participants."""
        conn = self._conn()
        try:
            participants = self._get_chat_participants(conn, chat_id)
            return any(self._is_allowed(p) for p in participants)
        finally:
            conn.close()

    def _is_chat_id(self, to: str) -> bool:
        """Check if the 'to' value is a chat ID (group or DM)."""
        return to.startswith("any;") or to.startswith("iMessage;") or to.startswith("SMS;")

    def send_message(self, to: str, text: str, files: list[str] | None = None) -> dict:
        """Send an iMessage to a person or group chat.

        Args:
            to: Chat ID (from chat_list), phone number, email, or contact name
            text: Message text
            files: Optional list of absolute file paths to attach
        """
        if self._is_chat_id(to):
            return self._send_to_chat(to, text, files)
        else:
            return self._send_to_handle(to, text, files)

    def _send_to_handle(self, handle: str, text: str, files: list[str] | None = None) -> dict:
        """Send to an individual by phone number, email, or contact name."""
        handle = self.resolve_recipient(handle)

        if not self._is_allowed(handle):
            raise PermissionError(f"Handle {handle} is not in the allowlist")

        escaped_text = _escape_applescript(text)
        escaped_handle = _escape_applescript(handle)

        script = f'''
tell application "Messages"
    set targetService to 1st account whose service type = iMessage
    set targetBuddy to participant "{escaped_handle}" of targetService
    send "{escaped_text}" to targetBuddy
end tell
'''
        _run_applescript(script)

        result = {"sent": True, "to": handle, "text": text}
        self._send_files(escaped_handle, files, result, mode="handle")
        return result

    def _send_to_chat(self, chat_id: str, text: str, files: list[str] | None = None) -> dict:
        """Send to a group chat or DM by chat ID."""
        if not self._is_chat_allowed(chat_id):
            raise PermissionError(f"Chat {chat_id} has no allowed participants")

        escaped_text = _escape_applescript(text)
        escaped_chat_id = _escape_applescript(chat_id)

        script = f'''
tell application "Messages"
    set targetChat to chat id "{escaped_chat_id}"
    send "{escaped_text}" to targetChat
end tell
'''
        _run_applescript(script)

        # Resolve display name for the response
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT display_name, chat_identifier FROM chat WHERE guid = ?",
                (chat_id,),
            ).fetchone()
            display = None
            if row:
                display = row["display_name"]
                if not display:
                    participants = self._get_chat_participants(conn, chat_id)
                    resolved = [self._contacts.resolve_or_handle(p) for p in participants]
                    display = ", ".join(resolved)
        finally:
            conn.close()

        result = {"sent": True, "to": chat_id, "chat_name": display, "text": text}
        self._send_files(escaped_chat_id, files, result, mode="chat")
        return result

    def _send_files(self, escaped_target: str, files: list[str] | None, result: dict, mode: str):
        """Send file attachments to a handle or chat."""
        if not files:
            return
        sent_files = []
        for filepath in files:
            if not os.path.isabs(filepath) or not os.path.exists(filepath):
                sent_files.append({"file": filepath, "error": "File not found"})
                continue
            escaped_path = _escape_applescript(filepath)
            if mode == "chat":
                file_script = f'''
tell application "Messages"
    set targetChat to chat id "{escaped_target}"
    send POSIX file "{escaped_path}" to targetChat
end tell
'''
            else:
                file_script = f'''
tell application "Messages"
    set targetService to 1st account whose service type = iMessage
    set targetBuddy to participant "{escaped_target}" of targetService
    send POSIX file "{escaped_path}" to targetBuddy
end tell
'''
            try:
                _run_applescript(file_script)
                sent_files.append({"file": filepath, "sent": True})
            except Exception as e:
                sent_files.append({"file": filepath, "error": str(e)})
        result["files"] = sent_files
