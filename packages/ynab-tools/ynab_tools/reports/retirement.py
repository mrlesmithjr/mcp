"""Retirement planning: balances, contributions, projections."""

import logging
import os
import sqlite3
from datetime import datetime

from ..config import load_env
from ..db import get_connection, init_db

logger = logging.getLogger(__name__)


def _get_retirement_accounts() -> dict[str, str]:
    """Load retirement account names from env vars, with empty defaults.

    Set these in .env:
      YNAB_RETIREMENT_401K=Your 401(k) Account Name
      YNAB_RETIREMENT_ROTH_IRA=Your Roth IRA Account Name
      YNAB_RETIREMENT_TRAD_IRA=Your Traditional IRA Account Name
      YNAB_RETIREMENT_TAXABLE=Your Taxable Brokerage Account Name
    """
    load_env()
    accounts = {}
    mapping = {
        "401k": "YNAB_RETIREMENT_401K",
        "roth_ira": "YNAB_RETIREMENT_ROTH_IRA",
        "trad_ira": "YNAB_RETIREMENT_TRAD_IRA",
        "taxable": "YNAB_RETIREMENT_TAXABLE",
    }
    for key, env_var in mapping.items():
        val = os.environ.get(env_var, "").strip()
        if val:
            accounts[key] = val
    return accounts


# Transaction type classification based on payee_name patterns
# These map YNAB payee names to contribution types
CONTRIB_PAYEES = {"Contribution", "Purchase"}
MATCH_PAYEES = {"Credit"}
DIVIDEND_PAYEES = {"Dividend", "Reinvestment"}
CONVERSION_PAYEES = {"Roth Conversion", "In Plan Roth"}
FEE_PAYEES = {"401k Fee", "Fee", "Plan Sponsor Fee", "Recordkeeping Fee", "Merrill Lynch 401k Fee"}
TRANSFER_PAYEES = {"Transfer"}
ADJUSTMENT_PAYEES = {"Reconciliation Balance Adjustment"}
ROTH_CONVERSION_PREFIX = "ROTH CONVERSION"

_LIMITS_DEFAULTS = {
    "year": 2026,
    "401k_employee": 23_500,
    "401k_catchup": 7_500,
    "401k_total": 70_000,
    "ira": 7_500,
    "ira_catchup": 1_100,
}


def _get_limits() -> dict:
    """Return contribution limits, overridable via env vars or config.json.

    Override any value with the corresponding env var (e.g. YNAB_LIMIT_IRA=7500)
    to adjust for a different tax year or non-US contribution vehicle structure.
    See docs/commands.md for the full list of env var names.
    """
    load_env()

    def _int(env_var: str, default: int) -> int:
        raw = os.environ.get(env_var, "").strip()
        try:
            return int(raw) if raw else default
        except ValueError:
            logger.warning("Invalid value for %s: %r, using default %d", env_var, raw, default)
            return default

    return {
        "year": _int("YNAB_LIMIT_YEAR", _LIMITS_DEFAULTS["year"]),
        "401k_employee": _int("YNAB_LIMIT_401K_EMPLOYEE", _LIMITS_DEFAULTS["401k_employee"]),
        "401k_catchup": _int("YNAB_LIMIT_401K_CATCHUP", _LIMITS_DEFAULTS["401k_catchup"]),
        "401k_total": _int("YNAB_LIMIT_401K_TOTAL", _LIMITS_DEFAULTS["401k_total"]),
        "ira": _int("YNAB_LIMIT_IRA", _LIMITS_DEFAULTS["ira"]),
        "ira_catchup": _int("YNAB_LIMIT_IRA_CATCHUP", _LIMITS_DEFAULTS["ira_catchup"]),
    }


def _get_user_age() -> int | None:
    """Load user's birth year from env to calculate age for projections.

    Set in .env: YNAB_BIRTH_YEAR=1972
    """
    load_env()
    birth_year = os.environ.get("YNAB_BIRTH_YEAR", "").strip()
    if birth_year and birth_year.isdigit():
        return datetime.now().year - int(birth_year)
    return None


def _get_projection_milestones() -> list[dict]:
    """Load custom projection milestones from env.

    Set in .env: YNAB_RETIREMENT_MILESTONES=67:Target retirement,60:Pension starts
    """
    load_env()
    raw = os.environ.get("YNAB_RETIREMENT_MILESTONES", "").strip()
    milestones = []
    if raw:
        for entry in raw.split(","):
            if ":" in entry:
                age_str, label = entry.split(":", 1)
                if age_str.strip().isdigit():
                    milestones.append({"age": int(age_str.strip()), "label": label.strip()})
    return milestones


def _get_payee_sets() -> tuple[set[str], set[str], set[str]]:
    """Return (contrib_payees, match_payees, fee_payees) from env vars or defaults.

    Override any set via the corresponding env var (comma-separated exact names):
      YNAB_RETIREMENT_CONTRIB_PAYEES=Contribution,Purchase
      YNAB_RETIREMENT_MATCH_PAYEES=Credit,Employer Match
      YNAB_RETIREMENT_FEE_PAYEES=401k Fee,Recordkeeping Fee
    """
    load_env()

    def _parse(env_var: str, default: set[str]) -> set[str]:
        raw = os.environ.get(env_var, "").strip()
        if raw:
            parsed = {p.strip() for p in raw.split(",") if p.strip()}
            return parsed if parsed else default
        return default

    return (
        _parse("YNAB_RETIREMENT_CONTRIB_PAYEES", CONTRIB_PAYEES),
        _parse("YNAB_RETIREMENT_MATCH_PAYEES", MATCH_PAYEES),
        _parse("YNAB_RETIREMENT_FEE_PAYEES", FEE_PAYEES),
    )


def _classify_transaction(
    payee: str | None,
    contrib_payees: set[str] | None = None,
    match_payees: set[str] | None = None,
    fee_payees: set[str] | None = None,
) -> str:
    """Classify a retirement transaction by its payee name.

    Pass pre-loaded payee sets to avoid re-reading env on every transaction.
    When parameters are None, falls back to the module-level constants.
    """
    if not payee:
        return "other"

    _contrib = contrib_payees if contrib_payees is not None else CONTRIB_PAYEES
    _match = match_payees if match_payees is not None else MATCH_PAYEES
    _fee = fee_payees if fee_payees is not None else FEE_PAYEES

    if payee in _contrib:
        return "contribution"
    if payee in _match:
        return "match"
    if payee in DIVIDEND_PAYEES:
        return "dividend"
    if payee in _fee:
        return "fee"
    if payee in TRANSFER_PAYEES:
        return "transfer"
    if payee in ADJUSTMENT_PAYEES:
        return "adjustment"
    if any(payee.startswith(p) for p in CONVERSION_PAYEES):
        return "conversion"
    if payee.startswith(ROTH_CONVERSION_PREFIX):
        return "conversion"
    return "other"


STALE_THRESHOLD_DAYS = 30


def _check_staleness(conn: sqlite3.Connection, accounts: dict[str, str]) -> list[tuple[str, float, str]]:
    """Check if retirement accounts have stale data.

    Returns list of (account_name, days_stale, last_date) for stale accounts.
    """
    stale = []
    for key, name in accounts.items():
        row = conn.execute(
            """
            SELECT MAX(date) AS last_date
            FROM transactions
            WHERE deleted = 0 AND account_name = ?
        """,
            (name,),
        ).fetchone()

        if not row or not row["last_date"]:
            # Account exists but no transactions - only warn if non-zero balance
            acct = conn.execute(
                "SELECT balance FROM accounts WHERE name = ? AND deleted = 0",
                (name,),
            ).fetchone()
            if acct and abs(acct["balance"] or 0) > 0.01:
                stale.append((name, 999, "never"))
            continue

        # Skip zero-balance accounts (e.g. Traditional IRA after Backdoor Roth)
        acct = conn.execute(
            "SELECT balance FROM accounts WHERE name = ? AND deleted = 0",
            (name,),
        ).fetchone()
        if acct and abs(acct["balance"] or 0) < 0.01:
            continue

        last_date = row["last_date"]
        days = (datetime.now() - datetime.strptime(last_date, "%Y-%m-%d")).days
        if days > STALE_THRESHOLD_DAYS:
            stale.append((name, days, last_date))

    return stale


def _get_account_balances(conn: sqlite3.Connection, accounts: dict[str, str]) -> dict[str, float]:
    """Get current balances for retirement accounts."""
    balances = {}
    for key, name in accounts.items():
        row = conn.execute(
            "SELECT balance FROM accounts WHERE name = ? AND deleted = 0",
            (name,),
        ).fetchone()
        balances[key] = row["balance"] if row else 0.0
    return balances


def _get_annual_breakdown(
    conn: sqlite3.Connection,
    account_name: str,
    year: int,
    contrib_payees: set[str] | None = None,
    match_payees: set[str] | None = None,
    fee_payees: set[str] | None = None,
) -> dict[str, float]:
    """Get transaction breakdown by type for an account in a given year."""
    rows = conn.execute(
        """
        SELECT payee_name, SUM(amount) AS total
        FROM transactions
        WHERE deleted = 0
          AND account_name = ?
          AND date >= ? AND date < ?
        GROUP BY payee_name
    """,
        (account_name, f"{year}-01-01", f"{year + 1}-01-01"),
    ).fetchall()

    breakdown = {
        "contribution": 0.0,
        "match": 0.0,
        "dividend": 0.0,
        "conversion": 0.0,
        "fee": 0.0,
        "transfer": 0.0,
        "adjustment": 0.0,
        "other": 0.0,
    }

    for row in rows:
        txn_type = _classify_transaction(
            row["payee_name"],
            contrib_payees=contrib_payees,
            match_payees=match_payees,
            fee_payees=fee_payees,
        )
        breakdown[txn_type] += row["total"]

    return breakdown


def _project_balance(
    current: float,
    annual_contribution: float,
    years: int,
    growth_rate: float = 0.07,
    current_age: int | None = None,
) -> list[dict]:
    """Project future balance with contributions and compound growth."""
    projections = []
    balance = current
    current_year = datetime.now().year

    for y in range(1, years + 1):
        # Growth on existing balance + half-year growth on new contributions
        balance = balance * (1 + growth_rate) + annual_contribution * (1 + growth_rate / 2)
        entry = {
            "year": current_year + y,
            "balance": balance,
        }
        if current_age is not None:
            entry["age"] = current_age + y
        projections.append(entry)

    return projections


def run_retirement(year: int | None = None, project: bool = False, debug: bool = False) -> None:
    """Show retirement account status, contributions, and optionally projections."""
    retirement_accounts = _get_retirement_accounts()
    if not retirement_accounts:
        print("No retirement accounts configured.")
        print("Set account names in .env:")
        print("  YNAB_RETIREMENT_401K=Your 401(k) Account Name")
        print("  YNAB_RETIREMENT_ROTH_IRA=Your Roth IRA Account Name")
        print("  YNAB_RETIREMENT_TRAD_IRA=Your Traditional IRA Account Name")
        print("  YNAB_RETIREMENT_TAXABLE=Your Taxable Brokerage Account Name")
        return

    conn = get_connection()
    try:
        init_db(conn)

        contrib_payees, match_payees, fee_payees = _get_payee_sets()
        current_year = datetime.now().year
        display_year = year or current_year
        balances = _get_account_balances(conn, retirement_accounts)
        total_invested = sum(balances.values())

        print("Retirement Summary")
        print("=" * 70)
        print()

        # Staleness check
        stale_accounts = _check_staleness(conn, retirement_accounts)
        if stale_accounts:
            print("WARNING: Stale account data detected!")
            for name, days, last_date in stale_accounts:
                display = name.split("–")[0].strip() if "–" in name else name
                print(f"  {display}: last updated {last_date} ({days:.0f} days ago)")
            print()
            print('  Update balances in YNAB, or use: ynab reconcile "account" amount')
            print()

        # Display names: use the key as a readable label
        display_names = {
            "401k": "401(k)",
            "roth_ira": "Roth IRA",
            "trad_ira": "Traditional IRA",
            "taxable": "Taxable Brokerage",
        }

        print("Current Balances:")
        print(f"  {'Account':<40} {'Balance':>14}")
        print(f"  {'-' * 40} {'-' * 14}")
        for key, name in retirement_accounts.items():
            bal = balances[key]
            if bal != 0:
                display = display_names.get(key, name)
                print(f"  {display:<40} ${bal:>13,.2f}")
        print(f"  {'-' * 40} {'-' * 14}")
        print(f"  {'Total Invested':<40} ${total_invested:>13,.2f}")
        print()

        # Annual contribution breakdown - only show columns for configured accounts
        col_keys = []
        col_labels = []
        for key in ["401k", "roth_ira", "taxable"]:
            if key in retirement_accounts:
                col_keys.append(key)
                col_labels.append(display_names.get(key, key))

        header = f"  {'Category':<30}" + "".join(f" {lbl:>12}" for lbl in col_labels)
        print(f"Contribution Breakdown ({display_year}):")
        print(header)
        print(f"  {'-' * 30}" + "".join(f" {'-' * 12}" for _ in col_keys))

        breakdowns = {}
        for key in col_keys:
            breakdowns[key] = _get_annual_breakdown(
                conn,
                retirement_accounts[key],
                display_year,
                contrib_payees=contrib_payees,
                match_payees=match_payees,
                fee_payees=fee_payees,
            )

        categories = [
            ("Employee Contributions", "contribution"),
            ("Employer Match", "match"),
            ("Roth Conversions", "conversion"),
            ("Dividends", "dividend"),
            ("Fees", "fee"),
        ]

        for label, cat_key in categories:
            vals = [breakdowns[k].get(cat_key, 0) for k in col_keys]
            if any(v != 0 for v in vals):
                line = f"  {label:<30}" + "".join(f" ${v:>11,.2f}" for v in vals)
                print(line)

        # Net change
        nets = [sum(breakdowns[k].values()) for k in col_keys]
        print(f"  {'-' * 30}" + "".join(f" {'-' * 12}" for _ in col_keys))
        net_line = f"  {'Net Change':<30}" + "".join(f" ${n:>11,.2f}" for n in nets)
        print(net_line)
        print()

        # Debug: per-transaction classification for troubleshooting
        if debug:
            print(f"Debug: Transaction Classification ({display_year})")
            for key in col_keys:
                account_name = retirement_accounts[key]
                display = display_names.get(key, account_name)
                print(f"  Account: {display}")
                rows = conn.execute(
                    """
                    SELECT COALESCE(t.payee_name, p.name) AS payee_name,
                           t.amount, t.date
                    FROM transactions t
                    LEFT JOIN payees p ON t.payee_id = p.id
                    WHERE t.deleted = 0
                      AND t.account_name = ?
                      AND t.date >= ? AND t.date < ?
                    ORDER BY t.date, t.payee_name
                    """,
                    (account_name, f"{display_year}-01-01", f"{display_year + 1}-01-01"),
                ).fetchall()
                if rows:
                    print(f"    {'Date':<12} {'Payee':<40} {'Amount':>12} {'Type'}")
                    print(f"    {'-' * 12} {'-' * 40} {'-' * 12} {'-' * 18}")
                    for row in rows:
                        payee = row["payee_name"] or "(none)"
                        txn_type = _classify_transaction(
                            payee,
                            contrib_payees=contrib_payees,
                            match_payees=match_payees,
                            fee_payees=fee_payees,
                        )
                        amount = float(row["amount"] or 0)
                        print(f"    {row['date']:<12} {payee:<40} ${amount:>11,.2f} {txn_type}")
                else:
                    print("    (no transactions)")
            print()

        # Contribution vs limits
        k401 = breakdowns.get("401k", {})
        roth = breakdowns.get("roth_ira", {})
        employee_contrib = k401.get("contribution", 0)
        employer_match = k401.get("match", 0)
        roth_conversion = k401.get("conversion", 0)
        # Conversions don't consume the IRA contribution limit; only direct contributions do
        ira_contrib = abs(roth.get("contribution", 0))

        current_age = _get_user_age()
        use_catchup = current_age is not None and current_age >= 50
        limits = _get_limits()

        employee_limit = limits["401k_employee"] + (limits["401k_catchup"] if use_catchup else 0)
        ira_limit = limits["ira"] + (limits["ira_catchup"] if use_catchup else 0)

        print(f"Progress Toward {limits['year']} Limits (US IRS):")
        pct_401 = (employee_contrib / employee_limit * 100) if employee_limit else 0
        pct_ira = (ira_contrib / ira_limit * 100) if ira_limit else 0

        print(f"  401(k) Employee:  ${employee_contrib:>10,.2f} / ${employee_limit:>10,.2f}  ({pct_401:.0f}%)")
        if employer_match > 0:
            print(f"  401(k) Employer:  ${employer_match:>10,.2f}")
        if roth_conversion > 0:
            print(f"  Mega Backdoor:    ${roth_conversion:>10,.2f}")
        total_401 = employee_contrib + employer_match + roth_conversion
        pct_total = (total_401 / limits["401k_total"] * 100) if limits["401k_total"] else 0
        print(f"  401(k) Total:     ${total_401:>10,.2f} / ${limits['401k_total']:>10,.2f}  ({pct_total:.0f}%)")
        print(f"  IRA (Backdoor):   ${ira_contrib:>10,.2f} / ${ira_limit:>10,.2f}  ({pct_ira:.0f}%)")

        # Projections
        if project:
            print()
            print("Projected Growth (7% annual return):")
            has_age = current_age is not None
            if has_age:
                print(f"  {'Year':<6} {'Age':>4} {'Balance':>16}")
                print(f"  {'-' * 6} {'-' * 4} {'-' * 16}")
            else:
                print(f"  {'Year':<6} {'Balance':>16}")
                print(f"  {'-' * 6} {'-' * 16}")

            # Estimate annual contribution
            months_elapsed = datetime.now().month
            contrib_year = display_year
            c401 = k401
            croth = roth

            if months_elapsed < 6 and "401k" in retirement_accounts:
                prior = current_year - 1
                c401 = _get_annual_breakdown(
                    conn,
                    retirement_accounts["401k"],
                    prior,
                    contrib_payees=contrib_payees,
                    match_payees=match_payees,
                    fee_payees=fee_payees,
                )
                croth = _get_annual_breakdown(
                    conn,
                    retirement_accounts.get("roth_ira", ""),
                    prior,
                    contrib_payees=contrib_payees,
                    match_payees=match_payees,
                    fee_payees=fee_payees,
                )
                contrib_year = prior
                annualize = 1.0
            else:
                annualize = 12.0 / months_elapsed

            match_pct_raw = os.environ.get("YNAB_EMPLOYER_MATCH_PCT", "").strip()
            match_pct = float(match_pct_raw) / 100.0 if match_pct_raw else 0.0
            gross_ote_raw = os.environ.get("YNAB_GROSS_OTE", "").strip()
            gross_salary_raw = os.environ.get("YNAB_GROSS_SALARY", "").strip()
            gross_ote = (
                float(gross_ote_raw) if gross_ote_raw else (float(gross_salary_raw) if gross_salary_raw else 0.0)
            )
            employer_match_annual = gross_ote * match_pct

            annual_contrib = (
                c401.get("contribution", 0)
                + c401.get("match", 0)
                + c401.get("conversion", 0)
                + max(0.0, croth.get("contribution", 0))
                + abs(croth.get("conversion", 0))
            ) * annualize + employer_match_annual

            max_age = int(os.environ.get("YNAB_RETIREMENT_MAX_AGE", "90"))
            proj_years = max(15, (max_age - current_age + 1) if current_age else 15)
            projections = _project_balance(total_invested, annual_contrib, proj_years, current_age=current_age)

            # Build milestone map from env config
            custom_milestones = _get_projection_milestones()
            milestone_labels = {m["age"]: m["label"] for m in custom_milestones}
            milestone_ages = set(milestone_labels.keys()) | {60, 62, 65, 67, 70}

            for p in projections:
                age = p.get("age")
                show = p["year"] == current_year + 1
                if age is not None and age in milestone_ages:
                    show = True
                no_age_milestones = {current_year + y for y in (1, 5, 10, 15)}
                if not has_age and p["year"] in no_age_milestones:
                    show = True
                if show:
                    marker = ""
                    if age is not None and age in milestone_labels:
                        marker = f"  << {milestone_labels[age]}"
                    if has_age:
                        print(f"  {p['year']:<6} {age:>4} ${p['balance']:>15,.0f}{marker}")
                    else:
                        print(f"  {p['year']:<6} ${p['balance']:>15,.0f}")

            print(f"\n  Assumed annual contributions: ${annual_contrib:>,.0f} (based on {contrib_year})")
            if employer_match_annual > 0:
                match_label = f"{match_pct * 100:.0f}% of OTE"
                print(f"  Includes employer match est.: ${employer_match_annual:>,.0f}/yr ({match_label})")
            print("  Assumed growth rate: 7% (nominal, pre-inflation)")
            if not has_age:
                print("  Set YNAB_BIRTH_YEAR in .env to show age-based milestones")

    finally:
        conn.close()
