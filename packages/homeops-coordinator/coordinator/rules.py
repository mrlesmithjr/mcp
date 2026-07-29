"""Deterministic rule table, no AI, pure Python logic.

To add a new event type: add an elif branch. No other files change.
"""

import json
import logging
import time
from datetime import date

import coordinator.reminders as reminders

logger = logging.getLogger(__name__)

# A treatment event older than this many days (measured from the treatment's
# own "date" field, not when the event was queued) is too old for a 24h
# irrigation suspension to still matter -- skip it rather than suspend
# irrigation on a stale event that finally got processed after a coordinator
# outage. Product-inventory/reorder checks are not time-sensitive and always
# run regardless of staleness.
TREATMENT_STALE_DAYS = 2

# suspend_irrigation(24) is time-sensitive (prevents product wash-off right
# after a treatment) and hits a live Hydrawise API over the network, so a
# single flaky hop should not silently skip the suspend. Retry with short
# backoff before giving up (refs #74). Scoped to this one call site only --
# not a general Hydrawise retry policy.
SUSPEND_IRRIGATION_MAX_ATTEMPTS = 3
SUSPEND_IRRIGATION_BACKOFF_SECONDS = 2


def process(event):
    """Apply coordinator rules for a single event. Returns result dict."""
    payload = event.get("payload", "{}")
    if isinstance(payload, str):
        payload = json.loads(payload)

    source = event.get("source", "")
    event_type = event.get("event_type", "")

    if source == "homeops" and event_type in ("task_done", "task_pause", "task_delete"):
        return _sync_reminders(payload.get("name", ""), event_type)

    if source == "lawnops" and event_type == "treatment_add":
        return _handle_treatment(payload)

    if source == "homeops" and event_type == "utility_add":
        return _handle_utility_add(payload)

    logger.warning("Unhandled event: source=%r event_type=%r", source, event_type)
    return {"action": "no_op", "reason": f"unhandled event type: {event_type}"}


# Statuses that warrant surfacing to the user; "ok" and "no_budget" are silent.
IRRIGATION_BUDGET_ALERT_STATUSES = ("warning", "over")


def check_irrigation_budget():
    """Daily timer-triggered check (refs #141), not an event -- called directly
    by coordinator.daemon, mirrors _handle_utility_add's reminder dedup pattern.

    Creates an Apple Reminder if LawnOps' irrigation budget_status is
    "warning" or "over", so the user can manually suspend irrigation
    (`lawnops irrigation suspend`) if they choose. No HA involvement, no
    automatic gating -- purely a human-in-the-loop notification.
    """
    from coordinator.lawnops_tools import get_irrigation_budget_status

    status = get_irrigation_budget_status()
    budget_status = status.get("budget_status")

    if budget_status not in IRRIGATION_BUDGET_ALERT_STATUSES:
        return {"action": "no_op", "reason": f"budget_status={budget_status!r}, no alert needed"}

    search_query = "Irrigation budget"
    existing = reminders.search(search_query)
    if existing:
        return {"action": "no_op", "reason": "irrigation budget reminder already exists"}

    pct_used = status.get("pct_used")
    reminders.create(
        title=f"Irrigation budget {budget_status}: {pct_used}% used",
        notes=(
            f"Budget status: {budget_status}\n"
            f"Pct used: {pct_used}%\n"
            f"Remaining: ${status.get('remaining')}\n"
            f"Projected month cost: ${status.get('projected_cost')} "
            f"(limit ${status.get('monthly_dollars_limit')})\n"
            f"Manually suspend if needed: lawnops irrigation suspend"
        ),
        priority=1 if budget_status == "over" else 5,
        due_date=date.today().isoformat(),
    )
    logger.info("Created irrigation budget %s reminder (pct_used=%s)", budget_status, pct_used)
    return {"action": "reminder_created", "budget_status": budget_status, "pct_used": pct_used}


def _handle_utility_add(payload):
    """Handle a homeops utility_add event.

    Checks YNAB Utilities balance against the new bill amount.
    Creates a Reminder if the category balance won't cover the bill.
    """
    from coordinator.ynab_tools import get_category_balance

    bill_type = payload.get("type", "utility")
    amount = payload.get("amount", 0)
    bill_date = payload.get("date", "")

    category = get_category_balance("Utilities", bill_date=bill_date or None)

    if category is None:
        logger.warning("Could not read YNAB Utilities balance, skipping check")
        return {"action": "no_op", "reason": "ynab.db unavailable or category not found"}

    balance = category["balance"]
    gap = amount - balance

    logger.info(
        "Utility %s bill $%.2f, Utilities balance $%.2f (gap $%.2f)",
        bill_type,
        amount,
        balance,
        gap,
    )

    if gap <= 0:
        return {"action": "no_op", "reason": f"Utilities balance ${balance:.2f} covers ${amount:.2f} bill"}

    # Underfunded, create Reminder if one doesn't already exist
    search_query = "Utilities underfunded"
    existing = reminders.search(search_query)
    if existing:
        return {"action": "no_op", "reason": "underfunded reminder already exists"}

    from datetime import date

    reminders.create(
        title=f"Utilities underfunded: ${gap:.2f} needed",
        notes=(
            f"{bill_type.title()} bill: ${amount:.2f}\n"
            f"Utilities balance: ${balance:.2f}\n"
            f"Gap: ${gap:.2f}, fund Utilities before bill clears."
        ),
        priority=1,
        due_date=date.today().isoformat(),
        due_time="08:00",
    )
    logger.info("Created underfunded Reminder, gap $%.2f for %s bill", gap, bill_type)
    return {"action": "reminder_created", "gap": gap, "bill_type": bill_type}


def _handle_treatment(payload):
    """Handle a lawnops treatment_add event.

    1. Suspend all irrigation for 24 hours so product isn't washed off,
       unless the treatment date is older than TREATMENT_STALE_DAYS (a
       coordinator outage can mean an event isn't processed until well
       after the treatment happened, at which point a 24h suspend is
       meaningless).
    2. Check product inventory, if qty is at 0, create a reorder Reminder.
       Always runs, regardless of staleness -- inventory isn't time-sensitive.
    """
    from coordinator.lawnops_tools import get_product, suspend_irrigation

    product_name = payload.get("product", "")
    area = payload.get("area", "")
    treatment_date = payload.get("date", "")
    steps = []

    is_stale, age_days = _treatment_age_is_stale(treatment_date)

    # 1. Suspend irrigation, unless the treatment date is stale.
    if is_stale:
        steps.append(f"irrigation suspend skipped: treatment date {treatment_date} is {age_days} days old")
        logger.info(
            "Skipping irrigation suspend for %s treatment on %s: date %s is %s days old (> %s)",
            product_name,
            area,
            treatment_date,
            age_days,
            TREATMENT_STALE_DAYS,
        )
    else:
        until_str = None
        last_error = None
        for attempt in range(1, SUSPEND_IRRIGATION_MAX_ATTEMPTS + 1):
            try:
                until_str, _ = suspend_irrigation(24)
                break
            except Exception as e:
                last_error = e
                if attempt < SUSPEND_IRRIGATION_MAX_ATTEMPTS:
                    wait = SUSPEND_IRRIGATION_BACKOFF_SECONDS * attempt
                    logger.warning(
                        "suspend_irrigation attempt %d/%d failed: %s, retrying in %ss",
                        attempt,
                        SUSPEND_IRRIGATION_MAX_ATTEMPTS,
                        e,
                        wait,
                    )
                    time.sleep(wait)

        if until_str is not None:
            steps.append(f"irrigation suspended until {until_str}")
            logger.info("Irrigation suspended until %s after %s treatment on %s", until_str, product_name, area)
        else:
            last_error_desc = f"{type(last_error).__name__}: {last_error}"
            logger.warning(
                "Could not suspend irrigation after %d attempts: %s",
                SUSPEND_IRRIGATION_MAX_ATTEMPTS,
                last_error_desc,
            )
            steps.append(
                f"irrigation suspend failed after {SUSPEND_IRRIGATION_MAX_ATTEMPTS} attempts: {last_error_desc}"
            )

    # 2. Check product inventory
    if product_name:
        try:
            product = get_product(product_name)
            if product is not None and product["qty_on_hand"] <= 0:
                search_query = f"Reorder: {product_name}"
                existing = reminders.search(search_query)
                if not existing:
                    reminders.create(
                        title=f"Reorder needed: {product['name']}",
                        notes=(
                            f"Qty on hand is 0 after treatment on {treatment_date}. "
                            f"Order replacement {product['unit']}."
                        ),
                        priority=5,
                    )
                    steps.append(f"reorder reminder created for {product_name}")
                    logger.info("Reorder reminder created for %s (qty=0)", product_name)
                else:
                    steps.append(f"reorder reminder already exists for {product_name}")
        except Exception as e:
            logger.warning("Product inventory check failed for %r: %s", product_name, e)

    return {"action": "treatment_handled", "steps": steps}


def _treatment_age_is_stale(treatment_date):
    """Return (is_stale, age_days) for a treatment event's date string.

    age_days is None when the date is missing or unparseable. In that case
    is_stale is always False -- fall through to today's behavior (suspend
    as normal) rather than crash or silently skip suspension on bad data.
    """
    if not treatment_date:
        logger.warning("Treatment event missing 'date' field, skipping staleness check")
        return False, None

    try:
        treatment_day = date.fromisoformat(treatment_date)
    except (TypeError, ValueError) as e:
        logger.warning("Could not parse treatment date %r (%s), skipping staleness check", treatment_date, e)
        return False, None

    age_days = (date.today() - treatment_day).days
    return age_days > TREATMENT_STALE_DAYS, age_days


def _sync_reminders(task_name, event_type):
    """Search for open Reminders matching task_name and complete them."""
    if not task_name:
        return {"action": "no_op", "reason": "no task name in payload"}

    matches = reminders.search(task_name)
    if not matches:
        logger.info("No open reminders found for %r (%s)", task_name, event_type)
        return {"action": "no_op", "reason": f"no reminders matched '{task_name}'"}

    completed = []
    for r in matches:
        result = reminders.complete(r["id"])
        if result:
            completed.append(r["title"])
            logger.info("Completed reminder: %r", r["title"])

    return {"action": "completed_reminders", "count": len(completed), "titles": completed}
