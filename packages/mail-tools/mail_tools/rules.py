"""Sender-based rule persistence for mail_apply_rules (issue #48).

Categories map a group of sender query fragments to a default bulk action,
persisted as ~/.config/mail-tools/sender_rules.json - same CONFIG_DIR
convention gmail.py already uses for gmail_credentials.json/gmail_tokens.json.
No new dependency, no second config-root path.
"""

import json
from datetime import date, datetime, timedelta

from mail_tools.gmail import CONFIG_DIR

RULES_FILE = CONFIG_DIR / "sender_rules.json"


def load_rules() -> dict:
    """Load sender rules, or an empty {"version": 1, "categories": []} default
    if the config file doesn't exist yet.
    """
    if not RULES_FILE.exists():
        return {"version": 1, "categories": []}
    with open(RULES_FILE) as f:
        return json.load(f)


def save_rules(data: dict) -> None:
    """Persist sender rules atomically - write to a temp file, then
    Path.replace() onto the real path, so a process kill mid-write (Ctrl-C,
    OOM-kill, host reboot) can never leave sender_rules.json truncated or
    invalid. apply_rules() calls this once per category processed, which
    raises the exposure window versus a single end-of-run write.
    """
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RULES_FILE.with_suffix(".json.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        tmp.replace(RULES_FILE)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def build_category_query(category: dict, base_filter: str = "is:unread") -> str:
    """Build a Gmail search query for a category: its `senders` fragments
    joined with " OR ", wrapped in one paren group, then the base filter
    appended - e.g. "(from:amazon.com OR from:target.com) is:unread".

    Raises ValueError if `senders` is missing or empty - an unguarded
    `"() " + base_filter` query is not a no-match query, it's a degenerate
    Gmail query that collapses to just `base_filter` (e.g. "is:unread"),
    which would silently target every unread message in the account. A
    hand-edited sender_rules.json category left with an empty/missing
    `senders` list must fail loudly here rather than reach the Gmail API.
    """
    senders = category.get("senders", [])
    if not senders:
        raise ValueError(f"category {category.get('id')!r} has no senders - refusing to build a query")
    joined = " OR ".join(senders)
    return f"({joined}) {base_filter}"


def stale_categories(rules: dict, as_of: date) -> list[dict]:
    """Categories whose review_after_days has elapsed since last_reviewed,
    regardless of status - purely informational, never triggers a mutation.

    Only considers categories where review_after_days is set (not null/None)
    and last_reviewed is present.
    """
    stale = []
    for category in rules.get("categories", []):
        review_after_days = category.get("review_after_days")
        last_reviewed = category.get("last_reviewed")
        if review_after_days is None or not last_reviewed:
            continue
        last_reviewed_date = datetime.strptime(last_reviewed, "%Y-%m-%d").date()
        due_date = last_reviewed_date + timedelta(days=review_after_days)
        if due_date < as_of:
            stale.append(category)
    return stale
