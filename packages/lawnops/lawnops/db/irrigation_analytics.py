"""Irrigation analytics - pace tracking, budgeting, zone analysis, and ET recommendations.

Reuses helpers from water_usage.py for YNAB water bill and irrigation data access.
"""

from calendar import monthrange
from datetime import datetime, timedelta

from lawnops.db.connection import get_db
from lawnops.db.water_usage import _get_irrigation_monthly, _get_water_bills, get_water_usage_report


def _get_current_month_irrigation(config):
    """Get irrigation data for the current month from lawnops DB.

    Returns {minutes, runs, days_with_runs, daily: [{date, minutes, runs}]}.
    """
    conn = get_db(config)
    now = datetime.now()
    month_prefix = now.strftime("%Y-%m")

    try:
        # Daily breakdown
        rows = conn.execute(
            """
            SELECT date,
                   ROUND(SUM(duration_min), 1) as total_minutes,
                   COUNT(*) as run_count
            FROM irrigation_runs
            WHERE strftime('%Y-%m', date) = ?
            GROUP BY date
            ORDER BY date
        """,
            (month_prefix,),
        ).fetchall()

        daily = [{"date": r["date"], "minutes": r["total_minutes"] or 0, "runs": r["run_count"]} for r in rows]

        total_min = sum(d["minutes"] for d in daily)
        total_runs = sum(d["runs"] for d in daily)
        days_with = len(daily)

        return {
            "minutes": round(total_min, 1),
            "runs": total_runs,
            "days_with_runs": days_with,
            "daily": daily,
        }
    finally:
        conn.close()


def _get_zone_breakdown(config, year=None, month=None):
    """Get per-zone irrigation breakdown.

    Returns list of {zone_number, zone_name, total_minutes, run_count, avg_duration}.
    """
    conn = get_db(config)
    try:
        query = """
            SELECT zone_number, zone_name,
                   ROUND(SUM(duration_min), 1) as total_minutes,
                   COUNT(*) as run_count,
                   ROUND(AVG(duration_min), 1) as avg_duration,
                   ROUND(MIN(duration_min), 1) as min_duration,
                   ROUND(MAX(duration_min), 1) as max_duration
            FROM irrigation_runs
            WHERE 1=1
        """
        params = []
        if year and month:
            query += " AND strftime('%Y-%m', date) = ?"
            params.append(f"{year}-{month:02d}")
        elif year:
            query += " AND strftime('%Y', date) = ?"
            params.append(str(year))

        query += " GROUP BY zone_number, zone_name ORDER BY zone_number"

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _derive_schedule_params(config) -> dict | None:
    """Derive effective irrigation cycle period from the last 90 days of run history.

    Returns period_days, zone_count, avg_run_duration_min, and sample metadata,
    or None if insufficient data exists.
    """
    conn = get_db(config)
    now = datetime.now()
    cutoff = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    try:
        rows = conn.execute(
            """
            SELECT zone_number,
                   COUNT(*) as run_count,
                   ROUND(AVG(duration_min), 2) as avg_duration,
                   MIN(date) as first_date
            FROM irrigation_runs
            WHERE date >= ?
              AND duration_min IS NOT NULL
              AND duration_min > 1
            GROUP BY zone_number
        """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 2:
        return None
    total_zone_runs = sum(r["run_count"] for r in rows)
    if total_zone_runs < 10:
        return None
    zone_count = len(rows)
    try:
        first_dates = [datetime.strptime(r["first_date"], "%Y-%m-%d").date() for r in rows]
    except (ValueError, TypeError):
        return None
    window_days = max((now.date() - min(first_dates)).days, 1)
    period_days = window_days / (total_zone_runs / zone_count)
    period_days = max(1.0, min(14.0, round(period_days, 2)))
    avg_run_duration = round(sum(r["avg_duration"] * r["run_count"] for r in rows) / total_zone_runs, 2)
    return {
        "period_days": period_days,
        "zone_count": zone_count,
        "avg_run_duration_min": avg_run_duration,
        "data_days": window_days,
        "zone_runs_sampled": total_zone_runs,
    }


def _project_month(config, current_minutes: float, day_of_month: int, days_in_month: int) -> dict:
    """Project full-month irrigation minutes from current progress.

    Uses schedule-based model when 90-day run history is available; falls back
    to linear pace extrapolation. Always includes daily_rate for display.
    """
    # Always compute linear for fallback / display
    daily_rate = round(current_minutes / day_of_month, 2) if day_of_month > 0 else 0.0
    linear_projected = round(daily_rate * days_in_month, 1) if current_minutes > 0 else 0.0

    params = _derive_schedule_params(config)
    if params is not None:
        days_remaining = days_in_month - day_of_month
        remaining_runs = (days_remaining / params["period_days"]) * params["zone_count"]
        remaining_minutes = remaining_runs * params["avg_run_duration_min"]
        projected = round(current_minutes + remaining_minutes, 1)
        method = "schedule_based"
        reliability = "high" if day_of_month >= 7 else "medium"
        return {
            "projected_minutes": projected,
            "projection_method": method,
            "projection_reliability": reliability,
            "schedule_params": params,
            "daily_rate_minutes": daily_rate,
        }

    # Linear fallback
    reliability = "low" if day_of_month < 7 else "medium"
    return {
        "projected_minutes": linear_projected,
        "projection_method": "linear_pace",
        "projection_reliability": reliability,
        "schedule_params": None,
        "daily_rate_minutes": daily_rate,
    }


def _weighted_cpm_from_bills(config) -> dict:
    """Compute a weighted cost-per-minute from recent water bills.

    Diagnostic only - this value is never used directly for cost projections
    (see `_compute_dynamic_cpm`). Uses the three most recent qualifying months
    (sufficient irrigation data, not the current month) with weights
    0.5/0.3/0.2. Returns `cpm: None` when fewer than 2 qualifying months exist
    or the weighted result is an outlier (<=0 or > $1/min).
    """
    try:
        usage = get_water_usage_report(config)
        current_month = datetime.now().strftime("%Y-%m")
        qualifying = [
            m
            for m in usage["months"]
            if m.get("cost_per_minute") is not None
            and m["cost_per_minute"] > 0
            and m["irrigation_minutes"] > 30
            and m["month"] != current_month
        ]
        qualifying.sort(key=lambda m: m["month"], reverse=True)
        qualifying = qualifying[:3]
    except Exception as e:
        return {"cpm": None, "months_used": 0, "months": [], "warning": str(e)}

    if len(qualifying) < 2:
        return {"cpm": None, "months_used": 0, "months": [], "warning": None}

    weights = [0.5, 0.3, 0.2][: len(qualifying)]
    weight_sum = sum(weights)
    weighted_cpm = sum(w * m["cost_per_minute"] for w, m in zip(weights, qualifying)) / weight_sum
    weighted_cpm = round(weighted_cpm, 4)

    if weighted_cpm <= 0 or weighted_cpm > 1.0:
        return {"cpm": None, "months_used": 0, "months": [], "warning": f"weighted CPM outlier: {weighted_cpm}"}

    return {
        "cpm": weighted_cpm,
        "months_used": len(qualifying),
        "months": [m["month"] for m in qualifying],
        "warning": None,
    }


def _compute_dynamic_cpm(config) -> dict:
    """Return the authoritative cost-per-minute for irrigation cost projections.

    `hydrawise.budget.cost_per_minute` in config is authoritative whenever it
    is set to a positive value: it is used directly for `irrigation_budget`,
    `irrigation_pace`, and the ET projections, and `source` reads "config". The
    bill-derived weighted CPM (see `_weighted_cpm_from_bills`) is surfaced only
    as a diagnostic `observed_cpm_from_bills` field for comparison - it is
    never used to compute a cost projection.

    A configured value <= 0 (e.g. an accidental `0` or a negative number) is
    rejected rather than trusted: it would silently zero out or invert every
    cost projection downstream (budgets never trip, `irrigation check` never
    fires, YNAB's estimated bill drops the irrigation line). Rejection falls
    through to the same "no config value set" path below, with a `warning`
    explaining why, following the outlier-rejection convention already used
    by `_weighted_cpm_from_bills`.

    Falls back to the bill-derived computation (source "computed") when no
    valid config value is set, and to a hardcoded 0.12 default (source
    "default") when neither a valid config value nor enough billing history
    exists.
    """
    hydrawise_cfg = config.get("hydrawise", {})
    budget_cfg = hydrawise_cfg.get("budget", {})
    config_cpm = budget_cfg.get("cost_per_minute")

    bills = _weighted_cpm_from_bills(config)

    rejection_warning = None
    if config_cpm is not None and config_cpm <= 0:
        rejection_warning = f"configured cost_per_minute {config_cpm} is not positive; ignoring"
        config_cpm = None

    if config_cpm is not None:
        return {
            "cpm": config_cpm,
            "source": "config",
            "months_used": bills["months_used"],
            "months": bills["months"],
            "observed_cpm_from_bills": bills["cpm"],
            "warning": bills["warning"],
        }

    if bills["cpm"] is None:
        return {
            "cpm": 0.12,
            "source": "default",
            "months_used": 0,
            "months": [],
            "observed_cpm_from_bills": None,
            "warning": rejection_warning or bills["warning"],
        }

    return {
        "cpm": bills["cpm"],
        "source": "computed",
        "months_used": bills["months_used"],
        "months": bills["months"],
        "observed_cpm_from_bills": bills["cpm"],
        "warning": rejection_warning,
    }


def irrigation_pace(config):
    """Monthly pace tracker - current month irrigation vs historical averages.

    Returns structured dict with projection, comparison, and alert level.
    """
    now = datetime.now()
    current_month = now.strftime("%Y-%m")
    day_of_month = now.day
    days_in_month = monthrange(now.year, now.month)[1]
    pct_through = round(day_of_month / days_in_month * 100, 1)

    # Current month data
    current = _get_current_month_irrigation(config)

    # Project full month using schedule-based model with linear fallback
    projection = _project_month(config, current["minutes"], day_of_month, days_in_month)
    projected_minutes = projection["projected_minutes"]
    daily_rate = projection["daily_rate_minutes"]

    # Historical comparison - same month last year
    last_year = now.year - 1
    historical = _get_irrigation_monthly(config, last_year)
    last_year_key = f"{last_year}-{now.month:02d}"
    last_year_data = historical.get(last_year_key, {"minutes": 0, "runs": 0})

    # Historical averages - same month across all years
    all_history = _get_irrigation_monthly(config)
    same_month_history = {
        k: v for k, v in all_history.items() if k.endswith(f"-{now.month:02d}") and k != current_month
    }
    if same_month_history:
        historical_avg = round(sum(v["minutes"] for v in same_month_history.values()) / len(same_month_history), 1)
    else:
        historical_avg = None

    # Cost projection - use the config-authoritative cost-per-minute
    hydrawise_cfg = config.get("hydrawise", {})
    pace_cfg = hydrawise_cfg.get("pace", {})
    cpm_data = _compute_dynamic_cpm(config)
    cost_per_min = cpm_data["cpm"]
    elevated_cost = pace_cfg.get("elevated_cost", 120)
    high_cost = pace_cfg.get("high_cost", 200)

    projected_cost = round(projected_minutes * cost_per_min, 2)
    current_cost = round(current["minutes"] * cost_per_min, 2)

    # Alert level
    if projected_cost >= high_cost:
        alert = "high"
    elif projected_cost >= elevated_cost:
        alert = "elevated"
    else:
        alert = "on-track"

    return {
        "month": current_month,
        "month_name": now.strftime("%B %Y"),
        "day_of_month": day_of_month,
        "days_in_month": days_in_month,
        "pct_through_month": pct_through,
        "current_minutes": current["minutes"],
        "current_runs": current["runs"],
        "current_cost": current_cost,
        "daily_rate_minutes": round(daily_rate, 1),
        "projected_minutes": projected_minutes,
        "projected_cost": projected_cost,
        "projection_method": projection["projection_method"],
        "projection_reliability": projection["projection_reliability"],
        "cpm_source": cpm_data["source"],
        "observed_cpm_from_bills": cpm_data["observed_cpm_from_bills"],
        "last_year_minutes": last_year_data["minutes"],
        "last_year_runs": last_year_data["runs"],
        "historical_avg_minutes": historical_avg,
        "alert_level": alert,
        "thresholds": {
            "elevated_cost": elevated_cost,
            "high_cost": high_cost,
            "cost_per_minute": cost_per_min,
        },
    }


def irrigation_budget(config):
    """Budget tracker - actual vs configured limits with YNAB integration data.

    Returns structured dict with budget status, YNAB data, and recommendations.
    """
    now = datetime.now()
    current_month = now.strftime("%Y-%m")
    day_of_month = now.day
    days_in_month = monthrange(now.year, now.month)[1]

    hydrawise_cfg = config.get("hydrawise", {})
    budget_cfg = hydrawise_cfg.get("budget", {})
    alert_pct = budget_cfg.get("alert_pct", 80)
    monthly_minutes_limit = budget_cfg.get("monthly_minutes")
    monthly_dollars_limit = budget_cfg.get("monthly_dollars")

    # Current month data
    current = _get_current_month_irrigation(config)

    # Project full month using schedule-based model with linear fallback
    projection = _project_month(config, current["minutes"], day_of_month, days_in_month)
    projected_minutes = projection["projected_minutes"]

    # Use the config-authoritative cost-per-minute
    cpm_data = _compute_dynamic_cpm(config)
    cost_per_min = cpm_data["cpm"]

    projected_cost = round(projected_minutes * cost_per_min, 2)
    current_cost = round(current["minutes"] * cost_per_min, 2)

    # ET% target to stay within dollar budget
    et_pct_for_budget = None
    if monthly_dollars_limit is not None and projected_cost > monthly_dollars_limit and projected_cost > 0:
        raw = (monthly_dollars_limit / projected_cost) * 100
        et_pct_for_budget = max(0, min(100, round(raw)))

    # Budget tracking
    budget_set = monthly_minutes_limit is not None or monthly_dollars_limit is not None
    budget_status = "no_budget"
    pct_used = None
    remaining = None
    recommendations = []

    if monthly_minutes_limit is not None:
        pct_used = round(current["minutes"] / monthly_minutes_limit * 100, 1) if monthly_minutes_limit > 0 else 0
        remaining = round(monthly_minutes_limit - current["minutes"], 1)
        if pct_used >= 100:
            budget_status = "over"
        elif pct_used >= alert_pct:
            budget_status = "warning"
        else:
            budget_status = "ok"

        if budget_status in ("warning", "over"):
            # Calculate recommended ET reduction
            if projected_minutes > monthly_minutes_limit and monthly_minutes_limit > 0:
                reduction_pct = round((1 - monthly_minutes_limit / projected_minutes) * 100)
                recommendations.append(
                    f"Reduce ET% by ~{reduction_pct}% to stay within {monthly_minutes_limit} min budget"
                )

    elif monthly_dollars_limit is not None:
        pct_used = round(current_cost / monthly_dollars_limit * 100, 1) if monthly_dollars_limit > 0 else 0
        remaining = round(monthly_dollars_limit - current_cost, 2)
        if pct_used >= 100:
            budget_status = "over"
        elif pct_used >= alert_pct:
            budget_status = "warning"
        else:
            budget_status = "ok"

        if budget_status in ("warning", "over"):
            if projected_cost > monthly_dollars_limit and monthly_dollars_limit > 0:
                reduction_pct = round((1 - monthly_dollars_limit / projected_cost) * 100)
                recommendations.append(
                    f"Reduce ET% by ~{reduction_pct}% to stay within ${monthly_dollars_limit:.0f} budget"
                )

    # YNAB integration data - water bill and category balance
    ynab_integration = None
    try:
        water_bills = _get_water_bills(config)
        # Get most recent water bill for context
        last_bill_month = max(water_bills.keys()) if water_bills else None
        last_bill_amount = water_bills.get(last_bill_month) if last_bill_month else None

        # Estimated bill for current month
        from lawnops.db.water_usage import get_water_usage_report

        usage = get_water_usage_report(config)
        baseline = usage.get("baseline_avg")

        estimated_bill = None
        if baseline is not None:
            estimated_bill = round(baseline + projected_cost, 2)

        ynab_integration = {
            "estimated_water_bill": estimated_bill,
            "baseline_water_bill": baseline,
            "estimated_irrigation_cost": projected_cost,
            "last_bill_month": last_bill_month,
            "last_bill_amount": last_bill_amount,
            "category": config.get("ynab", {}).get("category", "Home: Yard & Outdoor Maintenance"),
            "memo": f"Est. irrigation: ${projected_cost:.2f} ({projected_minutes:.0f} min)",
        }
    except Exception:
        pass  # YNAB not available

    return {
        "month": current_month,
        "month_name": now.strftime("%B %Y"),
        "budget_set": budget_set,
        "budget_status": budget_status,
        "monthly_minutes_limit": monthly_minutes_limit,
        "monthly_dollars_limit": monthly_dollars_limit,
        "alert_pct": alert_pct,
        "current_minutes": current["minutes"],
        "current_cost": current_cost,
        "projected_minutes": projected_minutes,
        "projected_cost": projected_cost,
        "et_pct_for_budget": et_pct_for_budget,
        "projection_method": projection["projection_method"],
        "projection_reliability": projection["projection_reliability"],
        "cpm_source": cpm_data["source"],
        "observed_cpm_from_bills": cpm_data["observed_cpm_from_bills"],
        "pct_used": pct_used,
        "remaining": remaining,
        "cost_per_minute": cost_per_min,
        "recommendations": recommendations,
        "ynab_integration": ynab_integration,
    }


def zone_analysis(config, year=None, month=None):
    """Per-zone runtime breakdown with outlier detection.

    Groups by program (lawn vs beds), flags zones running significantly more
    than program average, identifies short-run anomalies (possible head issues).
    """
    hydrawise_cfg = config.get("hydrawise", {})
    programs_cfg = hydrawise_cfg.get("programs", {})
    zone_notes = hydrawise_cfg.get("zone_notes", {})

    zones = _get_zone_breakdown(config, year, month)
    if not zones:
        return {
            "year": year,
            "month": month,
            "programs": [],
            "zones": [],
            "flags": [],
        }

    # Build program membership lookup
    zone_to_program = {}
    for prog_name, prog_info in programs_cfg.items():
        for z in prog_info.get("zones", []):
            zone_to_program[z] = prog_name

    # Group zones by program
    program_zones = {}
    for z in zones:
        prog = zone_to_program.get(z["zone_number"], "unassigned")
        z["program"] = prog
        if z.get("zone_number") in zone_notes:
            z["note"] = zone_notes[z["zone_number"]]
        program_zones.setdefault(prog, []).append(z)

    # Calculate program averages and flag outliers
    programs = []
    flags = []
    for prog_name, prog_zones in program_zones.items():
        total_min = sum(z["total_minutes"] for z in prog_zones)
        avg_min = round(total_min / len(prog_zones), 1) if prog_zones else 0

        programs.append(
            {
                "name": prog_name,
                "zone_count": len(prog_zones),
                "total_minutes": round(total_min, 1),
                "avg_minutes_per_zone": avg_min,
            }
        )

        for z in prog_zones:
            # Flag zones running >50% above program average
            if avg_min > 0 and z["total_minutes"] > avg_min * 1.5:
                pct_over = round((z["total_minutes"] / avg_min - 1) * 100)
                flags.append(
                    {
                        "zone_number": z["zone_number"],
                        "zone_name": z["zone_name"],
                        "flag": "high_runtime",
                        "detail": f"{pct_over}% above {prog_name} avg ({z['total_minutes']:.0f} vs {avg_min:.0f} min)",
                    }
                )

            # Flag zones where >10% of runs are very short (< 2 min) - possible head issues
            # Skip sub-minute runs as those are normal sensor tests/cancellations
            if z["min_duration"] is not None and z["min_duration"] < 2 and z["run_count"] > 5:
                flags.append(
                    {
                        "zone_number": z["zone_number"],
                        "zone_name": z["zone_name"],
                        "flag": "short_runs",
                        "detail": f"Min run {z['min_duration']} min - possible head issue or sensor cutoff",
                    }
                )

    # Period description
    if year and month:
        period = f"{year}-{month:02d}"
    elif year:
        period = str(year)
    else:
        period = "all time"

    return {
        "period": period,
        "year": year,
        "month": month,
        "programs": programs,
        "zones": zones,
        "flags": flags,
    }


def et_recommendations(config, year=None):
    """Analyze cost-per-minute by month against ET percentages.

    Recommends reductions for expensive months. Advisory only - user must adjust
    in Hydrawise app.
    """
    from lawnops.db.water_usage import get_water_usage_report

    target_year = year or datetime.now().year - 1  # Default to last year for full data

    now_dt = datetime.now()
    now_day = now_dt.day
    now_days_in_month = monthrange(now_dt.year, now_dt.month)[1]
    current_irr = _get_current_month_irrigation(config)
    curr_projection = _project_month(config, current_irr["minutes"], now_day, now_days_in_month)
    projected_full_month_minutes = curr_projection["projected_minutes"]

    cpm_data = _compute_dynamic_cpm(config)

    usage = get_water_usage_report(config, target_year)
    months = usage["months"]
    baseline = usage.get("baseline_avg")
    avg_cpm = usage.get("avg_cost_per_minute")

    if not avg_cpm:
        return {
            "year": target_year,
            "baseline_avg": baseline,
            "avg_cost_per_minute": None,
            "months": [],
            "recommendations": [],
            "potential_savings": 0,
            "note": "Insufficient data - need water bills and irrigation history to calculate.",
        }

    recommendations = []
    total_potential_savings = 0

    month_analysis = []
    for m in months:
        cpm = m.get("cost_per_minute")
        irr_cost = m.get("estimated_irrigation_cost")
        irr_min = m.get("irrigation_minutes", 0)

        if cpm is None or irr_min < 30:
            continue  # Skip months with trivial irrigation (noise)

        # Determine if this month is expensive relative to average
        cpm_ratio = cpm / avg_cpm if avg_cpm > 0 else 1
        expense_level = "normal"
        if cpm_ratio >= 2.0:
            expense_level = "high"
        elif cpm_ratio >= 1.5:
            expense_level = "elevated"

        # Calculate potential savings if we could reduce to average cost
        potential_saving = 0
        suggested_reduction = None
        if expense_level in ("high", "elevated") and irr_cost:
            target_cost = round(irr_min * avg_cpm, 2)
            potential_saving = round(irr_cost - target_cost, 2)
            suggested_reduction = round((1 - avg_cpm / cpm) * 100)

            recommendations.append(
                {
                    "month": m["month"],
                    "current_cpm": cpm,
                    "avg_cpm": avg_cpm,
                    "expense_level": expense_level,
                    "suggested_et_reduction_pct": suggested_reduction,
                    "potential_monthly_savings": potential_saving,
                    "detail": (
                        f"{m['month']}: ${cpm:.4f}/min vs ${avg_cpm:.4f}/min avg - "
                        f"reduce ET by ~{suggested_reduction}% to save ~${potential_saving:.2f}/mo"
                    ),
                }
            )
            total_potential_savings += potential_saving

        month_analysis.append(
            {
                "month": m["month"],
                "irrigation_minutes": irr_min,
                "irrigation_cost": irr_cost,
                "cost_per_minute": cpm,
                "expense_level": expense_level,
                "cpm_ratio": round(cpm_ratio, 2),
            }
        )

    budget_based_recommendation = None
    budget_cfg = config.get("hydrawise", {}).get("budget", {})
    monthly_dollars_limit = budget_cfg.get("monthly_dollars")
    cpm = cpm_data["cpm"]

    if monthly_dollars_limit is not None and projected_full_month_minutes > 0 and cpm > 0:
        target_minutes = monthly_dollars_limit / cpm
        if projected_full_month_minutes > target_minutes:
            et_target_pct = max(0, min(100, round(target_minutes / projected_full_month_minutes * 100)))
            budget_based_recommendation = {
                "monthly_dollars_limit": monthly_dollars_limit,
                "cpm": cpm,
                "cpm_source": cpm_data["source"],
                "observed_cpm_from_bills": cpm_data["observed_cpm_from_bills"],
                "projected_full_month_minutes": projected_full_month_minutes,
                "projection_method": curr_projection["projection_method"],
                "projection_reliability": curr_projection["projection_reliability"],
                "target_minutes": round(target_minutes, 1),
                "et_target_pct": et_target_pct,
                "detail": (
                    f"Set ET% to ~{et_target_pct}% to stay within "
                    f"${monthly_dollars_limit:.0f}/month budget "
                    f"(projected {projected_full_month_minutes:.0f} min "
                    f"@ ${cpm:.4f}/min)"
                ),
            }

    return {
        "year": target_year,
        "baseline_avg": baseline,
        "avg_cost_per_minute": avg_cpm,
        "months": month_analysis,
        "recommendations": recommendations,
        "potential_savings": round(total_potential_savings, 2),
        "budget_based_recommendation": budget_based_recommendation,
        "cpm_source": cpm_data["source"],
        "note": "Advisory only - adjust ET% in Hydrawise app. Tiered water rates make high-usage months disproportionately expensive.",
    }


def evaluate_irrigation_check(config):
    """Flag irrigation budget/ET issues that qualify for a reminder (issue #146)."""
    budget = irrigation_budget(config)
    et = et_recommendations(config, year=datetime.now().year)

    budget_issue = budget if budget["budget_status"] in ("warning", "over") else None
    et_issue = et.get("recommendations") or []

    return {
        "qualifies": budget_issue is not None or bool(et_issue),
        "budget_issue": budget_issue,
        "et_issue": et_issue,
    }
