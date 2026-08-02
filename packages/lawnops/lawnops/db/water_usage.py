"""Water usage correlation - irrigation runtime vs the authoritative config cost-per-minute.

Water bills from YNAB are pulled in only for baseline/informational context
(issue #44); irrigation cost is `irrigation_minutes * config cost-per-minute`,
not a regression against bill amounts.
"""

import sqlite3
from pathlib import Path

from lawnops.db.connection import get_db

# Default ynab-tools database location
DEFAULT_YNAB_DB = Path.home() / ".local" / "share" / "ynab-tools" / "ynab.db"

# Default water utility payee names
DEFAULT_WATER_PAYEES: list[str] = []


def _get_ynab_db(config):
    """Open a read-only connection to the YNAB database."""
    ynab_cfg = config.get("ynab", {})
    db_path = Path(ynab_cfg.get("db_path", str(DEFAULT_YNAB_DB))).expanduser()
    if not db_path.exists():
        raise RuntimeError(f"YNAB database not found at {db_path}. Run 'ynab sync' first.")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _get_water_bills(config):
    """Pull monthly water bills from YNAB.

    Returns dict of {YYYY-MM: amount_dollars}.
    """
    ynab_cfg = config.get("ynab", {})
    payees = ynab_cfg.get("water_payees", DEFAULT_WATER_PAYEES)

    conn = _get_ynab_db(config)
    try:
        placeholders = ",".join("?" for _ in payees)
        rows = conn.execute(
            f"""
            SELECT strftime('%Y-%m', date) as month, SUM(ABS(amount)) as total
            FROM transactions
            WHERE payee_name IN ({placeholders})
              AND amount < 0
              AND deleted = 0
            GROUP BY month
            ORDER BY month
        """,
            payees,
        ).fetchall()

        return {r["month"]: round(r["total"], 2) for r in rows}
    finally:
        conn.close()


def _zone_gpm_map(config) -> dict[int, float]:
    """Build zone -> GPM lookup from config, with sensible defaults.

    Config key: hydrawise.zone_gpm  (e.g. {"1": 2.5, "2": 0.75})
    Falls back to hydrawise.default_gpm (default 2.5) for any unlisted zone.
    """
    hw_cfg = config.get("hydrawise", {})
    default_gpm = float(hw_cfg.get("default_gpm", 2.5))
    raw = hw_cfg.get("zone_gpm", {})
    return {int(k): float(v) for k, v in raw.items()}, default_gpm


def _get_irrigation_monthly(config, year=None):
    """Pull monthly irrigation totals from lawnops DB, including gallon estimates.

    Returns dict of {YYYY-MM: {minutes, runs, estimated_gallons}}.
    """
    conn = get_db(config)
    zone_gpm, default_gpm = _zone_gpm_map(config)

    try:
        query = """
            SELECT strftime('%Y-%m', date) as month,
                   zone_number,
                   ROUND(SUM(duration_min), 2) as zone_minutes,
                   COUNT(*) as run_count
            FROM irrigation_runs
        """
        params = []
        if year:
            query += " WHERE strftime('%Y', date) = ?"
            params.append(str(year))
        query += " GROUP BY month, zone_number ORDER BY month, zone_number"

        rows = conn.execute(query, params).fetchall()

        # Aggregate per month, computing gallons per zone
        monthly: dict[str, dict] = {}
        for r in rows:
            month = r["month"]
            zone = r["zone_number"]
            mins = r["zone_minutes"] or 0
            runs = r["run_count"]
            gpm = zone_gpm.get(zone, default_gpm)
            gallons = round(mins * gpm, 1)

            if month not in monthly:
                monthly[month] = {"minutes": 0.0, "runs": 0, "estimated_gallons": 0.0}
            monthly[month]["minutes"] = round(monthly[month]["minutes"] + mins, 1)
            monthly[month]["runs"] += runs
            monthly[month]["estimated_gallons"] = round(monthly[month]["estimated_gallons"] + gallons, 1)

        return monthly
    finally:
        conn.close()


def _compute_baseline(months):
    """Compute the baseline (non-irrigation) water bill from low-runtime months.

    "Minimal" irrigation = less than 5 minutes for the month (winterization
    tests, etc). Outliers (>1.75x median) are dropped to avoid a double-payment
    month skewing the baseline. `months` is a list of dicts with at least
    `month`, `irrigation_minutes`, `water_bill`.

    Returns (baseline_avg, baseline_months). This baseline is informational
    context (and feeds `_bill_regressed_months`'s diagnostic regression below)
    -- it does not drive the authoritative irrigation cost in
    `get_water_usage_report` (see issue #44).
    """
    baseline_candidates = [
        (m["month"], m["water_bill"]) for m in months if m["irrigation_minutes"] < 5 and m["water_bill"] is not None
    ]

    if len(baseline_candidates) >= 3:
        bills_sorted = sorted(b for _, b in baseline_candidates)
        median = bills_sorted[len(bills_sorted) // 2]
        baseline_filtered = [(m, b) for m, b in baseline_candidates if b <= median * 1.75]
    else:
        baseline_filtered = baseline_candidates

    baseline_months = [m for m, _ in baseline_filtered]
    baseline_bills = [b for _, b in baseline_filtered]
    baseline_avg = round(sum(baseline_bills) / len(baseline_bills), 2) if baseline_bills else None
    return baseline_avg, baseline_months


def _bill_regressed_months(config, year=None):
    """Bill-regressed per-month irrigation cost, diagnostic only (issue #44).

    Reproduces the pre-#44 regression (`bill - baseline`, divided by minutes)
    that `get_water_usage_report` used before its per-month cost became the
    config-authoritative cost-per-minute. Retained only so
    `irrigation_analytics._weighted_cpm_from_bills` can still compare the
    configured rate against what water bills actually show
    (`observed_cpm_from_bills`) -- it is never used for a cost projection.

    Returns a list of dicts: `month`, `irrigation_minutes`, `water_bill`,
    `estimated_irrigation_cost` (bill-derived), `cost_per_minute` (bill-derived).
    """
    water_bills = _get_water_bills(config)
    irrigation = _get_irrigation_monthly(config, year)

    all_months = sorted(set(list(water_bills.keys()) + list(irrigation.keys())))
    if year:
        all_months = [m for m in all_months if m.startswith(str(year))]

    months = []
    for month in all_months:
        irr = irrigation.get(month, {"minutes": 0, "runs": 0, "estimated_gallons": 0.0})
        months.append(
            {
                "month": month,
                "irrigation_minutes": irr["minutes"],
                "water_bill": water_bills.get(month),
            }
        )

    baseline_avg, _baseline_months = _compute_baseline(months)

    for m in months:
        if m["irrigation_minutes"] > 0 and m["water_bill"] is not None and baseline_avg is not None:
            est_cost = max(0, round(m["water_bill"] - baseline_avg, 2))
            cost_per_min = round(est_cost / m["irrigation_minutes"], 4) if m["irrigation_minutes"] > 0 else None
            m["estimated_irrigation_cost"] = est_cost
            m["cost_per_minute"] = cost_per_min
        else:
            m["estimated_irrigation_cost"] = None
            m["cost_per_minute"] = None

    return months


def get_water_usage_report(config, year=None):
    """Correlate monthly irrigation runtime with the authoritative cost-per-minute.

    Per-month and average irrigation cost are `irrigation_minutes * cost_per_minute`,
    where `cost_per_minute` is the same authoritative value `irrigation_budget` and
    `irrigation_pace` use (`_compute_dynamic_cpm` in `irrigation_analytics.py`,
    issue #42 / #44) -- not a regression against imported water bills. The
    per-month `water_bill` field is kept only as informational context; it no
    longer feeds the cost or cost-per-minute figures.

    Returns structured dict for CLI display or MCP JSON output.
    """
    # Local import to avoid a circular import at module load time:
    # irrigation_analytics.py imports helpers from this module at the top
    # level, so this module cannot import irrigation_analytics.py at its own
    # top level. By call time both modules are already fully loaded.
    from lawnops.db.irrigation_analytics import _compute_dynamic_cpm

    water_bills = _get_water_bills(config)
    irrigation = _get_irrigation_monthly(config, year)

    # Collect all months from both sources
    all_months = sorted(set(list(water_bills.keys()) + list(irrigation.keys())))
    if year:
        all_months = [m for m in all_months if m.startswith(str(year))]

    # Build month records
    months = []
    for month in all_months:
        irr = irrigation.get(month, {"minutes": 0, "runs": 0, "estimated_gallons": 0.0})
        bill = water_bills.get(month)
        months.append(
            {
                "month": month,
                "irrigation_minutes": irr["minutes"],
                "irrigation_runs": irr["runs"],
                "estimated_gallons": irr.get("estimated_gallons", 0.0),
                "water_bill": bill,
            }
        )

    # Baseline is still informational context (surfaced in the report), even
    # though it no longer drives the irrigation cost figures below.
    baseline_avg, baseline_months = _compute_baseline(months)

    cpm_data = _compute_dynamic_cpm(config)
    cpm = cpm_data["cpm"]

    # Calculate irrigation cost from the authoritative config cost-per-minute
    total_irr_minutes = 0.0
    total_irr_cost = 0.0

    for m in months:
        if m["irrigation_minutes"] > 0:
            est_cost = round(m["irrigation_minutes"] * cpm, 2)
            m["is_baseline"] = False
            m["estimated_irrigation_cost"] = est_cost
            m["cost_per_minute"] = cpm
            total_irr_minutes += m["irrigation_minutes"]
            total_irr_cost += est_cost
        else:
            m["is_baseline"] = True
            m["estimated_irrigation_cost"] = None
            m["cost_per_minute"] = None

    avg_cost_per_min = cpm if total_irr_minutes > 0 else None
    total_gallons = round(sum(m.get("estimated_gallons", 0.0) for m in months), 1)

    return {
        "year": year or "all",
        "months": months,
        "baseline_avg": baseline_avg,
        "baseline_months": baseline_months,
        "total_irrigation_minutes": round(total_irr_minutes, 1),
        "total_estimated_gallons": total_gallons,
        "total_estimated_irrigation_cost": round(total_irr_cost, 2),
        "avg_cost_per_minute": avg_cost_per_min,
        "cpm_source": cpm_data["source"],
    }
