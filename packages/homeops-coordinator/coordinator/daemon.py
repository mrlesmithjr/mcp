"""Coordinator polling daemon, processes events every 60 seconds."""

import logging
import time
from datetime import datetime

import coordinator.ynab_tools as ynab_tools
from coordinator.events import get_pending_events, mark_processed
from coordinator.rules import check_irrigation_budget, process

logger = logging.getLogger(__name__)

POLL_INTERVAL = 60
YNAB_POLL_EVERY = 15  # cycles (~15 minutes)

# Wall-clock (not cycle-count) gated, fires once per calendar day.
IRRIGATION_BUDGET_CHECK_HOUR = 4
IRRIGATION_BUDGET_CHECK_MINUTE = 30


def run():
    """Run the coordinator daemon loop. Blocks indefinitely."""
    logger.info("Coordinator daemon starting (poll interval: %ds)", POLL_INTERVAL)

    cycle = 0
    last_budget_check_date = None
    while True:
        try:
            _process_pending()
            if cycle % YNAB_POLL_EVERY == 0:
                _poll_ynab_planned()
            last_budget_check_date = _check_irrigation_budget_daily(last_budget_check_date)
        except Exception:
            logger.exception("Unhandled error in poll cycle, continuing")
        cycle += 1
        time.sleep(POLL_INTERVAL)


def _process_pending():
    events = get_pending_events()
    if not events:
        return

    logger.info("Processing %d pending event(s)", len(events))
    processed_ids = []

    for event in events:
        try:
            result = process(event)
            logger.info(
                "Event %d (%s/%s): %s",
                event["id"],
                event["source"],
                event["event_type"],
                result.get("action"),
            )
            processed_ids.append(event["id"])
        except Exception:
            logger.exception(
                "Failed to process event %d (%s/%s), will retry next poll",
                event["id"],
                event["source"],
                event["event_type"],
            )
            # Leave as pending, do NOT add to processed_ids

    mark_processed(processed_ids)


def _check_irrigation_budget_daily(last_check_date):
    """Run check_irrigation_budget() once daily, at/after IRRIGATION_BUDGET_CHECK_HOUR:MINUTE."""
    # last_check_date is in-memory only (not persisted). A daemon restart
    # after today's check already ran will harmlessly re-check once more
    # this calendar day -- accepted, since check_irrigation_budget's own
    # reminder dedup (search-before-create) already makes a repeat a no-op.
    now = datetime.now()
    today = now.date().isoformat()
    if last_check_date == today:
        return last_check_date
    if (now.hour, now.minute) < (IRRIGATION_BUDGET_CHECK_HOUR, IRRIGATION_BUDGET_CHECK_MINUTE):
        return last_check_date

    try:
        result = check_irrigation_budget()
        logger.info("Irrigation budget check: %s", result.get("action"))
    except Exception:
        logger.exception("Irrigation budget check failed, will retry tomorrow")

    return today


def _poll_ynab_planned():
    """Auto-complete planned expenses matched by a cleared transaction."""
    matches = ynab_tools.poll_planned_expenses()
    if not matches:
        return
    for m in matches:
        completed = ynab_tools.complete_planned_expense(m["plan_id"])
        if completed:
            logger.info(
                "Auto-completed planned expense #%d %r $%.2f matched %r on %s",
                m["plan_id"],
                m["category_name"],
                m["amount"],
                m["payee_name"],
                m["transaction_date"],
            )
