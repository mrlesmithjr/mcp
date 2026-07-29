"""Deep mail search via Mail.app's SQLite Envelope Index.

Searches across ALL mailboxes (INBOX, All Mail, Sent, Archive, etc.)
for all configured accounts. Instant results regardless of mailbox size.

Mail.app stores dates with a 31-year offset from Unix epoch (Apple's
Core Data reference date: 2001-01-01). We correct for this in output.
"""

import os
import sqlite3
from datetime import datetime

# Mail.app's Core Data epoch offset
# Dates are stored as seconds since 2001-01-01, not 1970-01-01
APPLE_EPOCH_OFFSET = 978307200  # seconds between 1970-01-01 and 2001-01-01


def _find_db_path():
    """Find Mail's Envelope Index, regardless of version directory."""
    mail_dir = os.path.expanduser("~/Library/Mail")
    if not os.path.isdir(mail_dir):
        return None
    # Find the highest V* directory (V10, V11, etc.)
    versions = sorted(
        [d for d in os.listdir(mail_dir) if d.startswith("V") and d[1:].isdigit()],
        key=lambda d: int(d[1:]),
        reverse=True,
    )
    for v in versions:
        path = os.path.join(mail_dir, v, "MailData", "Envelope Index")
        if os.path.exists(path):
            return path
    return None


DB_PATH = _find_db_path()


def deep_search(query, limit=20):
    """Search all mail by subject or sender.

    Args:
        query: Search text (case-insensitive, matched against subject and sender)
        limit: Maximum results

    Returns:
        List of dicts with date, subject, sender_email, sender_name, mailbox
    """
    if DB_PATH is None or not os.path.exists(DB_PATH):
        raise RuntimeError(f"Mail index not found at {DB_PATH}")

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    try:
        # Build search terms - split query into words for AND matching
        words = query.lower().split()
        where_clauses = []
        params = []

        for word in words:
            where_clauses.append("(LOWER(s.subject) LIKE ? OR LOWER(a.address) LIKE ? OR LOWER(a.comment) LIKE ?)")
            pattern = f"%{word}%"
            params.extend([pattern, pattern, pattern])

        # OR between words - match any term
        where_sql = " OR ".join(where_clauses)

        rows = conn.execute(
            f"""
            SELECT
                m.date_received,
                s.subject,
                a.address as sender_email,
                a.comment as sender_name,
                mb.url as mailbox_url,
                m.read
            FROM messages m
            JOIN subjects s ON m.subject = s.ROWID
            JOIN addresses a ON m.sender = a.ROWID
            JOIN mailboxes mb ON m.mailbox = mb.ROWID
            WHERE {where_sql}
              AND m.deleted = 0
            ORDER BY m.date_received DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()

        results = []
        for row in rows:
            date_str = None
            if row["date_received"]:
                try:
                    dt = datetime.fromtimestamp(row["date_received"])
                    date_str = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

            # Extract mailbox name from URL
            mailbox = _parse_mailbox_url(row["mailbox_url"])

            results.append(
                {
                    "date": date_str,
                    "subject": row["subject"],
                    "sender_email": row["sender_email"],
                    "sender_name": row["sender_name"],
                    "mailbox": mailbox,
                    "read": bool(row["read"]),
                }
            )

        return results

    finally:
        conn.close()


def _parse_mailbox_url(url):
    """Extract a readable mailbox name from Mail's internal URL.

    URLs look like: imap://UUID/%5BGmail%5D/All%20Mail
    """
    if not url:
        return "unknown"

    import urllib.parse

    decoded = urllib.parse.unquote(url)

    # Extract the path after the account UUID
    if "/" in decoded:
        parts = decoded.split("/")
        # Find the mailbox part (after [Gmail] or similar)
        mailbox_parts = []
        for part in parts:
            if part.startswith("[") or part in ("INBOX",):
                mailbox_parts.append(part)
            elif mailbox_parts:
                mailbox_parts.append(part)

        if mailbox_parts:
            return "/".join(mailbox_parts).replace("[Gmail]/", "")

    return url.split("/")[-1] if "/" in url else "unknown"
