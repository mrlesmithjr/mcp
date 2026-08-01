"""Utility bill tracking and trend analysis."""

from homeops.db.connection import get_db

VALID_TYPES = ["water", "electric", "gas", "internet", "trash"]


def add_utility_bill(config, bill_date, utility_type, amount, usage=None, notes=None):
    """Log a monthly utility bill.

    Args:
        bill_date: Month in YYYY-MM format
        utility_type: water, electric, gas, internet, trash
        amount: Dollar amount
        usage: Usage string (e.g. "12,000 gal", "1,200 kWh")
        notes: Optional notes
    """
    conn = get_db(config)
    utility_type = utility_type.lower()
    if utility_type not in VALID_TYPES:
        raise RuntimeError(f"Invalid utility type '{utility_type}'. Valid: {', '.join(VALID_TYPES)}")

    conn.execute(
        "INSERT INTO utility_bills (bill_date, type, amount, usage, notes) VALUES (?, ?, ?, ?, ?)",
        (bill_date, utility_type, amount, usage, notes),
    )

    # Also log to unified costs table
    conn.execute(
        "INSERT INTO costs (date, category, amount, description, source) VALUES (?, ?, ?, ?, 'utility')",
        (bill_date + "-01", f"utility:{utility_type}", amount, f"{utility_type} bill"),
    )

    conn.commit()
    conn.close()

    return {
        "date": bill_date,
        "type": utility_type,
        "amount": amount,
        "usage": usage,
    }


def delete_utility_bill(config, bill_id):
    """Delete a utility bill by ID. Returns True if deleted."""
    conn = get_db(config)
    result = conn.execute("DELETE FROM utility_bills WHERE id = ?", (bill_id,))
    conn.commit()
    rowcount = result.rowcount
    conn.close()
    return rowcount > 0


def get_utility_trend(config, utility_type, months=12):
    """Get monthly trend for a utility type."""
    conn = get_db(config)
    rows = conn.execute(
        "SELECT * FROM utility_bills WHERE type = ? ORDER BY bill_date DESC LIMIT ?",
        (utility_type.lower(), months),
    ).fetchall()
    conn.close()

    results = [dict(r) for r in rows]
    results.reverse()  # Chronological order
    return results


def get_utility_summary(config, year=None):
    """Get utility spending summary by type."""
    conn = get_db(config)
    if year:
        rows = conn.execute(
            "SELECT type, COUNT(*) as months, SUM(amount) as total, "
            "AVG(amount) as avg, MIN(amount) as min, MAX(amount) as max "
            "FROM utility_bills WHERE bill_date LIKE ? "
            "GROUP BY type ORDER BY total DESC",
            (f"{year}%",),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT type, COUNT(*) as months, SUM(amount) as total, "
            "AVG(amount) as avg, MIN(amount) as min, MAX(amount) as max "
            "FROM utility_bills GROUP BY type ORDER BY total DESC",
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def evaluate_utility_anomalies(config, threshold=0.20):
    """Flag utility types whose latest bill is more than `threshold` above their trailing baseline average (baseline excludes the latest bill itself, issue #146)."""
    anomalies = []
    for utility_type in VALID_TYPES:
        bills = get_utility_trend(config, utility_type, months=7)
        if len(bills) < 7:
            continue

        baseline = bills[:-1]
        latest = bills[-1]

        baseline_avg = sum(b["amount"] for b in baseline) / len(baseline)
        latest_amount = latest["amount"]

        if baseline_avg <= 0:
            continue

        if latest_amount > baseline_avg * (1 + threshold):
            pct_over = ((latest_amount / baseline_avg) - 1) * 100
            anomalies.append(
                {
                    "type": utility_type,
                    "latest_amount": latest_amount,
                    "baseline_avg": baseline_avg,
                    "pct_over": pct_over,
                }
            )

    return anomalies


def get_all_utility_history(config, months=12):
    """Get all utility bills across types, most recent first."""
    conn = get_db(config)
    rows = conn.execute(
        "SELECT * FROM utility_bills ORDER BY bill_date DESC, type LIMIT ?",
        (months * len(VALID_TYPES),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
