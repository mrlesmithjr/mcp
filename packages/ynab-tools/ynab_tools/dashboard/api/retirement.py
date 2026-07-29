"""Retirement dashboard API endpoint."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from ynab_tools.db import get_connection
from ynab_tools.reports.retirement import (
    _check_staleness,
    _get_account_balances,
    _get_annual_breakdown,
    _get_limits,
    _get_projection_milestones,
    _get_retirement_accounts,
    _get_user_age,
    _project_balance,
)

router = APIRouter()

ACCOUNT_LABELS = {
    "401k": "401(k)",
    "roth_ira": "Roth IRA",
    "trad_ira": "Traditional IRA",
    "taxable": "Taxable Brokerage",
}

# Fidelity savings benchmarks: (age, salary_multiplier)
FIDELITY_TARGETS = [
    (30, 1.0),
    (35, 2.0),
    (40, 3.0),
    (45, 4.0),
    (50, 6.0),
    (55, 7.0),
    (60, 8.0),
    (67, 10.0),
]


def _interpolate_target(age: int) -> float:
    if age <= FIDELITY_TARGETS[0][0]:
        return FIDELITY_TARGETS[0][1]
    if age >= FIDELITY_TARGETS[-1][0]:
        return FIDELITY_TARGETS[-1][1]
    for i in range(len(FIDELITY_TARGETS) - 1):
        a0, m0 = FIDELITY_TARGETS[i]
        a1, m1 = FIDELITY_TARGETS[i + 1]
        if a0 <= age <= a1:
            return m0 + (m1 - m0) * (age - a0) / (a1 - a0)
    return FIDELITY_TARGETS[-1][1]


@router.get("/retirement")
def get_retirement() -> dict[str, Any]:
    accounts = _get_retirement_accounts()
    current_age = _get_user_age()

    if not accounts:
        return {
            "configured": False,
            "current_age": current_age,
            "accounts": [],
            "total_invested": 0.0,
            "readiness": None,
            "contributions": None,
            "projection": [],
            "return_rate_pct": 7.0,
            "social_security": {"configured": False, "statement_date": None, "scenarios": [], "combined": None},
        }

    conn = get_connection()
    try:
        current_year = datetime.now().year
        current_month = datetime.now().month

        balances = _get_account_balances(conn, accounts)
        total_invested = sum(balances.values())

        stale_results = _check_staleness(conn, accounts)
        stale_map: dict[str, tuple[float, str]] = {name: (days, last_date) for name, days, last_date in stale_results}

        account_list = []
        for key, name in accounts.items():
            if name in stale_map:
                days_val, last_updated = stale_map[name]
                is_stale, days_stale = True, int(days_val)
            else:
                is_stale, days_stale = False, None
                row = conn.execute(
                    "SELECT MAX(date) AS d FROM transactions WHERE deleted = 0 AND account_name = ?",
                    (name,),
                ).fetchone()
                last_updated = row["d"] if row else None

            account_list.append(
                {
                    "type": key,
                    "label": ACCOUNT_LABELS.get(key, key),
                    "name": name,
                    "balance": balances[key],
                    "stale": is_stale,
                    "days_stale": days_stale,
                    "last_updated": last_updated,
                }
            )

        # Readiness
        gross_salary_raw = os.environ.get("YNAB_GROSS_SALARY", "").strip()
        gross_salary = float(gross_salary_raw) if gross_salary_raw else None
        readiness = None
        if gross_salary and gross_salary > 0 and current_age is not None:
            current_multiplier = total_invested / gross_salary
            target_mult = _interpolate_target(current_age)
            target_amount = gross_salary * target_mult
            gap = total_invested - target_amount
            ratio = current_multiplier / target_mult if target_mult else 1.0
            status = "ahead" if ratio >= 1.1 else ("behind" if ratio < 0.9 else "on_track")

            min_age = max(current_age - 5, 25)
            targets = [
                {"age": age, "multiplier": mult, "amount": round(gross_salary * mult)}
                for age, mult in FIDELITY_TARGETS
                if age >= min_age
            ]
            readiness = {
                "multiplier": round(current_multiplier, 2),
                "gross_salary": gross_salary,
                "current_target_multiplier": round(target_mult, 1),
                "current_target_amount": round(target_amount),
                "status": status,
                "gap": round(gap),
                "targets": targets,
            }

        # Employer match estimate
        match_pct_raw = os.environ.get("YNAB_EMPLOYER_MATCH_PCT", "").strip()
        match_pct = float(match_pct_raw) / 100.0 if match_pct_raw else 0.0
        gross_ote_raw = os.environ.get("YNAB_GROSS_OTE", "").strip()
        gross_ote = float(gross_ote_raw) if gross_ote_raw else (gross_salary or 0.0)
        employer_match_annual = round(gross_ote * match_pct) if match_pct > 0 else 0

        # Contributions
        contributions = None
        if accounts:
            breakdowns: dict[str, dict[str, float]] = {
                key: _get_annual_breakdown(conn, name, current_year) for key, name in accounts.items()
            }

            use_prior = current_month < 6 and "401k" in accounts
            if use_prior:
                c401 = _get_annual_breakdown(conn, accounts["401k"], current_year - 1)
                croth = (
                    _get_annual_breakdown(conn, accounts.get("roth_ira", ""), current_year - 1)
                    if "roth_ira" in accounts
                    else {}
                )
                annualize = 1.0
            else:
                c401 = breakdowns.get("401k", {})
                croth = breakdowns.get("roth_ira", {})
                annualize = 12.0 / current_month

            # Fix 2: Roth IRA "Purchase" transactions are investment buys of already-contributed
            # cash, not new contributions. Use max(0, ...) to exclude them and avoid double-counting
            # with the paired ROTH CONVERSION inflow.
            annual_pace = (
                round(
                    (
                        c401.get("contribution", 0)
                        + c401.get("match", 0)
                        + c401.get("conversion", 0)
                        + max(0.0, croth.get("contribution", 0))
                        + abs(croth.get("conversion", 0))
                    )
                    * annualize
                )
                + employer_match_annual
            )

            limits = _get_limits()
            catch_up = current_age is not None and current_age >= 50
            emp_limit = limits["401k_employee"] + (limits["401k_catchup"] if catch_up else 0)
            ira_limit = limits["ira"] + (limits["ira_catchup"] if catch_up else 0)

            k401 = breakdowns.get("401k", {})
            roth = breakdowns.get("roth_ira", {})
            emp_contrib = k401.get("contribution", 0)
            match_contrib = k401.get("match", 0)
            mega = k401.get("conversion", 0)
            # Fix 1: include prorated employer match estimate in total 401k limit tracking
            ytd_match_est = round(employer_match_annual * current_month / 12) if employer_match_annual > 0 else 0
            total_401k = emp_contrib + match_contrib + mega + ytd_match_est
            # Roth conversions don't consume the annual IRA contribution limit; only direct contributions do
            ira_contrib = abs(roth.get("contribution", 0))

            contributions = {
                "year": current_year,
                "limit_year": limits["year"],
                "catch_up_eligible": catch_up,
                "by_account": breakdowns,
                "employer_match_annual": employer_match_annual,
                "limits": {
                    "employee_401k": {
                        "limit": limits["401k_employee"],
                        "catch_up": limits["401k_catchup"],
                        "effective_limit": emp_limit,
                        "contributed": emp_contrib,
                        "pct": round(emp_contrib / emp_limit * 100) if emp_limit else 0,
                    },
                    "total_401k": {
                        "limit": limits["401k_total"],
                        "contributed": total_401k,
                        "pct": round(total_401k / limits["401k_total"] * 100) if limits["401k_total"] else 0,
                    },
                    "ira": {
                        "limit": limits["ira"],
                        "catch_up": limits["ira_catchup"],
                        "effective_limit": ira_limit,
                        "contributed": ira_contrib,
                        "pct": round(ira_contrib / ira_limit * 100) if ira_limit else 0,
                    },
                },
                "annual_pace": annual_pace,
                "pace_basis_year": current_year - 1 if use_prior else current_year,
            }

        # Projection - extend to age 80 or 30 years, whichever is more
        proj_years = max(30, (80 - current_age + 1) if current_age else 30)
        annual_contrib_est = contributions["annual_pace"] if contributions else 0
        retirement_rate_raw = os.environ.get("YNAB_RETIREMENT_RATE", "").strip()
        growth_rate = float(retirement_rate_raw) / 100.0 if retirement_rate_raw else 0.07
        raw_proj = _project_balance(
            total_invested,
            annual_contrib_est,
            proj_years,
            growth_rate=growth_rate,
            current_age=current_age,
        )
        milestone_map = {m["age"]: m["label"] for m in _get_projection_milestones()}
        # Prepend current position so the chart starts at "now"
        current_point = {
            "year": current_year,
            "age": current_age,
            "balance": round(total_invested),
            "milestone": milestone_map.get(current_age) if current_age is not None else None,
        }
        projection = [current_point] + [
            {
                "year": p["year"],
                "age": p.get("age"),
                "balance": round(p["balance"]),
                "milestone": milestone_map.get(p["age"]) if p.get("age") is not None else None,
            }
            for p in raw_proj
        ]

        # Social Security config (refs #220)
        def _ss_float(key: str) -> float | None:
            raw = os.environ.get(key, "").strip()
            try:
                return float(raw) if raw else None
            except ValueError:
                return None

        def _ss_int(key: str) -> int | None:
            raw = os.environ.get(key, "").strip()
            try:
                return int(raw) if raw else None
            except ValueError:
                return None

        ss_fra_benefit = _ss_float("YNAB_SS_FRA_BENEFIT")
        ss_fra_age = _ss_int("YNAB_SS_FRA_AGE")
        ss_delayed_benefit = _ss_float("YNAB_SS_DELAYED_BENEFIT")
        ss_delayed_age = _ss_int("YNAB_SS_DELAYED_AGE")
        ss_early_benefit = _ss_float("YNAB_SS_EARLY_BENEFIT")
        ss_early_age = _ss_int("YNAB_SS_EARLY_AGE")
        ss_statement_date = os.environ.get("YNAB_SS_STATEMENT_DATE", "").strip() or None

        ss_configured = ss_fra_benefit is not None and ss_fra_age is not None

        # Build SS scenarios list for the frontend
        ss_scenarios = []
        if ss_early_benefit is not None and ss_early_age is not None:
            ss_scenarios.append({"label": "Early", "age": ss_early_age, "monthly_benefit": ss_early_benefit})
        if ss_fra_benefit is not None and ss_fra_age is not None:
            ss_scenarios.append({"label": "Full Retirement Age", "age": ss_fra_age, "monthly_benefit": ss_fra_benefit})
        if ss_delayed_benefit is not None and ss_delayed_age is not None:
            ss_scenarios.append({"label": "Delayed", "age": ss_delayed_age, "monthly_benefit": ss_delayed_benefit})

        # Combined income at FRA: find projected 401k monthly drawdown at FRA age
        # Standard rule-of-thumb: 4% annual withdrawal rate, divided by 12
        def _projected_monthly_at_age(target_age: int | None) -> float | None:
            if target_age is None:
                return None
            point = next((p for p in projection if p.get("age") == target_age), None)
            if point is None:
                return None
            return round(point["balance"] * 0.04 / 12)

        ss_combined = None
        if ss_configured and ss_fra_age is not None and ss_fra_benefit is not None:
            fra_portfolio_monthly = _projected_monthly_at_age(ss_fra_age)
            combined_fra = (fra_portfolio_monthly + ss_fra_benefit) if fra_portfolio_monthly is not None else None
            delayed_portfolio_monthly = _projected_monthly_at_age(ss_delayed_age) if ss_delayed_age else None
            combined_delayed = (
                (delayed_portfolio_monthly + ss_delayed_benefit)
                if (delayed_portfolio_monthly is not None and ss_delayed_benefit is not None)
                else None
            )
            ss_combined = {
                "fra_portfolio_monthly": fra_portfolio_monthly,
                "fra_ss_monthly": ss_fra_benefit,
                "fra_total_monthly": combined_fra,
                "delayed_portfolio_monthly": delayed_portfolio_monthly,
                "delayed_ss_monthly": ss_delayed_benefit,
                "delayed_total_monthly": combined_delayed,
            }

        social_security = {
            "configured": ss_configured,
            "statement_date": ss_statement_date,
            "scenarios": ss_scenarios,
            "combined": ss_combined,
        }

        return {
            "configured": True,
            "current_age": current_age,
            "accounts": account_list,
            "total_invested": total_invested,
            "readiness": readiness,
            "contributions": contributions,
            "projection": projection,
            "return_rate_pct": round(growth_rate * 100, 2),
            "social_security": social_security,
        }

    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"Database not ready. Run 'ynab sync' first. ({exc})") from exc
    finally:
        conn.close()
