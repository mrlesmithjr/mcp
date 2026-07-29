"""Tests for report modules (budget, spending, debt, transactions, funding)."""

from datetime import datetime, timedelta

from ynab_tools.db import get_connection, init_db


def _setup_test_db(tmp_path, months_of_history=0):
    """Create a test DB with sample data.

    If months_of_history > 0, also creates historical budget_categories rows
    for spending stats testing.
    """
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    month = datetime.now().strftime("%Y-%m-01")
    now_iso = datetime.now().isoformat()

    # Budget month
    conn.execute(
        """
        INSERT INTO budget_months (month, income, budgeted, activity, to_be_budgeted, age_of_money, last_synced_at)
        VALUES (?, 5000, 4500, -4200, 800, 30, ?)
    """,
        (month, now_iso),
    )

    # Historical months for spending stats
    if months_of_history > 0:
        for i in range(1, months_of_history + 1):
            hist_date = datetime.now() - timedelta(days=31 * i)
            hist_month = hist_date.strftime("%Y-%m-01")
            conn.execute(
                """
                INSERT OR IGNORE INTO budget_months
                (month, income, budgeted, activity, to_be_budgeted, age_of_money, last_synced_at)
                VALUES (?, 5000, 4500, -4200, 800, 30, ?)
            """,
                (hist_month, now_iso),
            )

    # Budget categories
    categories = [
        ("cat1", month, "Bills", "Groceries", 0, 0, 900, -850, 50, "MF", None, 0),
        ("cat2", month, "Bills", "Dining Out", 0, 0, 300, -350, -50, "MF", None, 0),
        ("cat3", month, "Bills", "Gas", 0, 0, 200, -100, 100, "MF", None, 0),
        ("cat4", month, "Savings", "Emergency Fund", 0, 0, 500, 0, 5000, "TB", 15000, 200),
        ("cat5", month, "Bills", "Rent", 0, 0, 1500, -1500, 0, "MF", None, 0),
    ]
    for c in categories:
        conn.execute(
            """
            INSERT INTO budget_categories
            (id, budget_month, category_group_id, name, hidden, deleted,
             budgeted, activity, balance, goal_type, goal_target, goal_under_funded)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            c,
        )

    # Accounts
    conn.execute("""
        INSERT INTO accounts (id, name, type, on_budget, closed, deleted, balance)
        VALUES ('acct1', 'Checking', 'checking', 1, 0, 0, 5000)
    """)
    conn.execute("""
        INSERT INTO accounts (id, name, type, on_budget, closed, deleted, balance)
        VALUES ('acct2', 'Auto Loan', 'autoLoan', 1, 0, 0, -15000)
    """)
    conn.execute("""
        INSERT INTO accounts (id, name, type, on_budget, closed, deleted, balance)
        VALUES ('acct3', 'Visa', 'creditCard', 1, 0, 0, -250)
    """)

    # Transactions
    today = datetime.now().strftime("%Y-%m-%d")
    txns = [
        (
            "t1",
            today,
            -50.00,
            None,
            "cleared",
            1,
            "acct1",
            "Checking",
            None,
            "Kroger",
            None,
            "Groceries",
            None,
            None,
            None,
            0,
        ),
        (
            "t2",
            today,
            -800.00,
            None,
            "cleared",
            1,
            "acct1",
            "Checking",
            None,
            "Big Purchase",
            None,
            "Rent",
            None,
            None,
            None,
            0,
        ),
        (
            "t3",
            today,
            5000.00,
            None,
            "cleared",
            1,
            "acct1",
            "Checking",
            None,
            "Employer",
            None,
            "Income: Paychecks",
            None,
            None,
            None,
            0,
        ),
        (
            "t4",
            today,
            500.00,
            "monthly",
            "cleared",
            1,
            "acct2",
            "Auto Loan",
            None,
            "Payment",
            None,
            None,
            None,
            None,
            None,
            0,
        ),
    ]
    for t in txns:
        conn.execute(
            """
            INSERT INTO transactions
            (id, date, amount, memo, cleared, approved, account_id, account_name,
             payee_id, payee_name, category_id, category_name, transfer_account_id,
             import_payee_name, import_payee_name_original, deleted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            t,
        )

    # Historical category data for spending stats
    if months_of_history > 0:
        # Groceries: consistent spending ~$800-900
        grocery_amounts = [-820, -870, -830, -910, -850, -840, -890, -860, -880, -835, -865, -845]
        # Dining Out: moderate variance ~$250-400
        dining_amounts = [-280, -350, -310, -390, -270, -360, -320, -340, -290, -380, -300, -330]
        # Gas: high variance (includes a $500 road trip month)
        gas_amounts = [-100, -120, -500, -90, -110, -80, -450, -95, -130, -85, -105, -115]

        for i in range(1, months_of_history + 1):
            hist_date = datetime.now() - timedelta(days=31 * i)
            hist_month = hist_date.strftime("%Y-%m-01")
            idx = (i - 1) % len(grocery_amounts)

            hist_cats = [
                (
                    f"cat1-{i}",
                    hist_month,
                    "Bills",
                    "Groceries",
                    0,
                    0,
                    900,
                    grocery_amounts[idx],
                    900 + grocery_amounts[idx],
                    "MF",
                    None,
                    0,
                ),
                (
                    f"cat2-{i}",
                    hist_month,
                    "Bills",
                    "Dining Out",
                    0,
                    0,
                    300,
                    dining_amounts[idx],
                    300 + dining_amounts[idx],
                    "MF",
                    None,
                    0,
                ),
                (
                    f"cat3-{i}",
                    hist_month,
                    "Bills",
                    "Gas",
                    0,
                    0,
                    200,
                    gas_amounts[idx],
                    200 + gas_amounts[idx],
                    "MF",
                    None,
                    0,
                ),
            ]
            for c in hist_cats:
                conn.execute(
                    """
                    INSERT INTO budget_categories
                    (id, budget_month, category_group_id, name, hidden, deleted,
                     budgeted, activity, balance, goal_type, goal_target, goal_under_funded)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    c,
                )

    conn.commit()
    return conn, db_path


class TestBudgetReport:
    def test_budget_check_runs(self, tmp_path, capsys):
        conn, db_path = _setup_test_db(tmp_path)
        conn.close()

        from ynab_tools.reports.budget import _get_categories, _get_rta

        # Test internal functions with the test DB
        conn = get_connection(db_path)
        month = datetime.now().strftime("%Y-%m-01")

        rta = _get_rta(conn, 3)
        assert len(rta) >= 1
        assert rta[0]["rta"] == 800

        cats = _get_categories(conn, month)
        assert len(cats) == 5

        overspent = [c for c in cats if (c["balance"] or 0) < 0]
        assert len(overspent) == 1
        assert overspent[0]["name"] == "Dining Out"
        conn.close()


class TestSpendingReport:
    def test_spending_by_category(self, tmp_path):
        conn, db_path = _setup_test_db(tmp_path)

        from ynab_tools.reports.spending import _get_spending_by_category

        month_start = datetime.now().strftime("%Y-%m-01")
        spending = _get_spending_by_category(conn, month_start)
        assert len(spending) > 0

        # Should have expense and income categories
        expenses = [s for s in spending if s["total"] < 0]
        income = [s for s in spending if s["total"] > 0]
        assert len(expenses) > 0
        assert len(income) > 0
        conn.close()

    def test_budget_targets(self, tmp_path):
        conn, db_path = _setup_test_db(tmp_path)

        from ynab_tools.reports.spending import _get_budget_targets

        month = datetime.now().strftime("%Y-%m-01")
        targets = _get_budget_targets(conn, month)
        assert targets["Groceries"] == 900
        assert targets["Dining Out"] == 300
        conn.close()


class TestDebtReport:
    def test_debt_accounts(self, tmp_path):
        conn, db_path = _setup_test_db(tmp_path)

        from ynab_tools.reports.debt import _get_credit_cards_with_balance, _get_debt_accounts

        debts = _get_debt_accounts(conn)
        assert len(debts) == 1
        assert debts[0]["name"] == "Auto Loan"
        assert debts[0]["balance"] == -15000

        ccs = _get_credit_cards_with_balance(conn)
        assert len(ccs) == 1
        assert ccs[0]["name"] == "Visa"
        conn.close()


class TestTransactionReports:
    def test_sinking_funds(self, tmp_path):
        conn, db_path = _setup_test_db(tmp_path)

        from ynab_tools.reports.transactions import _format_goal_type

        assert _format_goal_type("TB") == "Target Balance"
        assert _format_goal_type("MF") == "Monthly Funding"
        assert _format_goal_type("NEED") == "Needed for Spending"
        conn.close()


class TestFundingStats:
    """Tests for funding module: spending stats, recommendations, and helpers."""

    def test_spending_stats_empty(self):
        from ynab_tools.stats import spending_stats

        result = spending_stats([])
        assert result["avg"] == 0
        assert result["trimmed_avg"] == 0
        assert result["months"] == 0

    def test_spending_stats_single_month(self):
        from ynab_tools.stats import spending_stats

        result = spending_stats([500.0])
        assert result["avg"] == 500.0
        assert result["trimmed_avg"] == 500.0
        assert result["months"] == 1
        assert result["cv"] == 0  # no std dev with 1 data point

    def test_spending_stats_consistent(self):
        """Consistent spending should have low CV."""
        from ynab_tools.stats import spending_stats

        amounts = [850, 870, 830, 860, 840, 880, 850, 865, 835, 855, 845, 875]
        result = spending_stats(amounts)
        assert result["months"] == 12
        assert result["cv"] < 0.1  # very low variance
        # Trimmed avg should be close to mean
        assert abs(result["trimmed_avg"] - result["avg"]) < 20

    def test_spending_stats_with_outlier(self):
        """Trimmed average should reduce impact of outliers."""
        from ynab_tools.stats import spending_stats

        # Normal months ~$100, one spike at $500
        amounts = [100, 110, 500, 90, 105, 95, 100, 115, 98, 102, 108, 97]
        result = spending_stats(amounts)
        # Trimmed avg drops the $500 spike and lowest, should be much lower than raw avg
        assert result["trimmed_avg"] < result["avg"]
        assert result["trimmed_avg"] < 120  # should be close to typical ~$100

    def test_spending_stats_high_variance(self):
        """Erratic spending should have high CV."""
        from ynab_tools.stats import spending_stats

        amounts = [100, 120, 500, 90, 110, 80, 450, 95, 130, 85, 105, 115]
        result = spending_stats(amounts)
        assert result["cv"] > 0.5  # high variance

    def test_recommend_insufficient_data(self):
        from ynab_tools.stats import recommend_target, spending_stats

        result = spending_stats([100, 200])
        amount, note = recommend_target(result)
        assert amount is None
        assert note == "insufficient data"

    def test_recommend_high_variance(self):
        from ynab_tools.stats import recommend_target, spending_stats

        amounts = [100, 120, 500, 90, 110, 80, 450, 95, 130, 85, 105, 115]
        result = spending_stats(amounts)
        amount, note = recommend_target(result)
        assert amount is None
        assert note == "high variance"

    def test_recommend_consistent_spending(self):
        from ynab_tools.stats import recommend_target, spending_stats

        amounts = [850, 870, 830, 860, 840, 880, 850, 865, 835, 855, 845, 875]
        result = spending_stats(amounts)
        amount, note = recommend_target(result)
        assert amount is not None
        assert note == ""
        # Should be rounded up to nearest $5
        assert amount % 5 == 0
        # Should be close to the average (~855)
        assert 850 <= amount <= 870

    def test_round_up_to(self):
        from ynab_tools.stats import round_up_5

        assert round_up_5(851) == 855
        assert round_up_5(855) == 855
        assert round_up_5(856) == 860
        assert round_up_5(100) == 100
        assert round_up_5(101) == 105
        assert round_up_5(0) == 0

    def test_dollars_to_milliunits(self):
        from ynab_tools.reports.funding import _dollars_to_milliunits

        assert _dollars_to_milliunits(100.00) == 100000
        assert _dollars_to_milliunits(0.50) == 500
        assert _dollars_to_milliunits(99.99) == 99990
        assert _dollars_to_milliunits(0) == 0

    def test_month_str_normalization(self):
        from ynab_tools.reports.funding import _month_str

        assert _month_str("2026-03") == "2026-03-01"
        assert _month_str("2026-03-01") == "2026-03-01"
        # None returns current month
        result = _month_str(None)
        assert result.endswith("-01")

    def test_find_category(self, tmp_path):
        conn, db_path = _setup_test_db(tmp_path)
        from ynab_tools.reports.funding import _find_category

        month = datetime.now().strftime("%Y-%m-01")

        # Exact match
        matches = _find_category(conn, "Groceries", month)
        assert len(matches) == 1
        assert matches[0]["name"] == "Groceries"

        # Partial match
        matches = _find_category(conn, "din", month)
        assert len(matches) == 1
        assert matches[0]["name"] == "Dining Out"

        # No match
        matches = _find_category(conn, "nonexistent", month)
        assert len(matches) == 0
        conn.close()

    def test_get_monthly_spending(self, tmp_path):
        conn, db_path = _setup_test_db(tmp_path, months_of_history=6)
        from ynab_tools.reports.funding import _get_monthly_spending

        amounts = _get_monthly_spending(conn, "Groceries", months=12)
        assert len(amounts) == 6  # we created 6 months of history
        assert all(a > 0 for a in amounts)  # should be positive (abs of negative activity)
        conn.close()

    def test_get_monthly_spending_no_history(self, tmp_path):
        conn, db_path = _setup_test_db(tmp_path, months_of_history=0)
        from ynab_tools.reports.funding import _get_monthly_spending

        amounts = _get_monthly_spending(conn, "Groceries", months=12)
        assert len(amounts) == 0  # current month excluded, no history
        conn.close()

    def test_sync_age_display(self, tmp_path):
        conn, db_path = _setup_test_db(tmp_path)
        from ynab_tools.reports.funding import _last_synced

        month = datetime.now().strftime("%Y-%m-01")

        synced = _last_synced(conn, month)
        assert synced is not None

        # Non-existent month
        synced = _last_synced(conn, "2020-01-01")
        assert synced is None
        conn.close()


class TestIraContributionLimits:
    """Regression tests for 2026 IRA contribution limits (issue #217)."""

    def test_2026_ira_base_limit(self):
        """IRA base limit for 2026 is $7,500 (not 2025's $7,000)."""
        from ynab_tools.reports.retirement import _LIMITS_DEFAULTS

        assert _LIMITS_DEFAULTS["ira"] == 7_500

    def test_2026_ira_catchup_limit(self):
        """IRA catch-up limit for 2026 is $1,100 (not 2025's $1,000)."""
        from ynab_tools.reports.retirement import _LIMITS_DEFAULTS

        assert _LIMITS_DEFAULTS["ira_catchup"] == 1_100

    def test_2026_ira_over_50_combined_limit(self):
        """Combined IRA limit for age 50+ in 2026 is $8,600."""
        from ynab_tools.reports.retirement import _LIMITS_DEFAULTS

        combined = _LIMITS_DEFAULTS["ira"] + _LIMITS_DEFAULTS["ira_catchup"]
        assert combined == 8_600

    def test_get_limits_returns_2026_values(self, monkeypatch):
        """_get_limits() defaults to 2026 IRA values when no env vars override."""
        # Clear any env overrides that could interfere
        for var in ("YNAB_LIMIT_IRA", "YNAB_LIMIT_IRA_CATCHUP", "YNAB_LIMIT_YEAR"):
            monkeypatch.delenv(var, raising=False)

        from ynab_tools.reports.retirement import _get_limits

        limits = _get_limits()
        assert limits["ira"] == 7_500
        assert limits["ira_catchup"] == 1_100
        assert limits["ira"] + limits["ira_catchup"] == 8_600

    def test_age_49_uses_base_ira_limit(self, monkeypatch):
        """Age 49 should use the base IRA limit with no catch-up."""
        import datetime

        birth_year = datetime.date.today().year - 49
        monkeypatch.setenv("YNAB_BIRTH_YEAR", str(birth_year))
        for var in ("YNAB_LIMIT_IRA", "YNAB_LIMIT_IRA_CATCHUP"):
            monkeypatch.delenv(var, raising=False)

        from ynab_tools.reports.retirement import _get_limits, _get_user_age

        age = _get_user_age()
        assert age == 49
        limits = _get_limits()
        ira_limit = limits["ira"] + (limits["ira_catchup"] if age >= 50 else 0)
        assert ira_limit == 7_500

    def test_age_50_uses_catchup_ira_limit(self, monkeypatch):
        """Age 50 should add catch-up to the base IRA limit."""
        import datetime

        birth_year = datetime.date.today().year - 50
        monkeypatch.setenv("YNAB_BIRTH_YEAR", str(birth_year))
        for var in ("YNAB_LIMIT_IRA", "YNAB_LIMIT_IRA_CATCHUP"):
            monkeypatch.delenv(var, raising=False)

        from ynab_tools.reports.retirement import _get_limits, _get_user_age

        age = _get_user_age()
        assert age == 50
        limits = _get_limits()
        ira_limit = limits["ira"] + (limits["ira_catchup"] if age >= 50 else 0)
        assert ira_limit == 8_600

    def test_env_var_override_ira_limit(self, monkeypatch):
        """YNAB_LIMIT_IRA env var overrides the default IRA limit."""
        monkeypatch.setenv("YNAB_LIMIT_IRA", "9000")
        monkeypatch.delenv("YNAB_LIMIT_IRA_CATCHUP", raising=False)

        from ynab_tools.reports.retirement import _get_limits

        limits = _get_limits()
        assert limits["ira"] == 9000

    def test_env_var_override_401k_employee_limit(self, monkeypatch):
        """YNAB_LIMIT_401K_EMPLOYEE env var overrides the 401(k) employee limit. refs #106"""
        monkeypatch.setenv("YNAB_LIMIT_401K_EMPLOYEE", "25000")
        for var in ("YNAB_LIMIT_401K_CATCHUP", "YNAB_LIMIT_401K_TOTAL", "YNAB_LIMIT_IRA"):
            monkeypatch.delenv(var, raising=False)

        from ynab_tools.reports.retirement import _get_limits

        limits = _get_limits()
        assert limits["401k_employee"] == 25000

    def test_env_var_override_year(self, monkeypatch):
        """YNAB_LIMIT_YEAR env var overrides the limit year field. refs #106"""
        monkeypatch.setenv("YNAB_LIMIT_YEAR", "2027")
        for var in (
            "YNAB_LIMIT_401K_EMPLOYEE",
            "YNAB_LIMIT_401K_CATCHUP",
            "YNAB_LIMIT_401K_TOTAL",
            "YNAB_LIMIT_IRA",
            "YNAB_LIMIT_IRA_CATCHUP",
        ):
            monkeypatch.delenv(var, raising=False)

        from ynab_tools.reports.retirement import _get_limits

        limits = _get_limits()
        assert limits["year"] == 2027

    def test_invalid_env_var_falls_back_to_default(self, monkeypatch):
        """Invalid env var value (e.g. '23,500') falls back silently to the default. refs #106"""
        monkeypatch.setenv("YNAB_LIMIT_IRA", "23,500")  # commas are not valid int
        monkeypatch.delenv("YNAB_LIMIT_IRA_CATCHUP", raising=False)

        from ynab_tools.reports.retirement import _LIMITS_DEFAULTS, _get_limits

        limits = _get_limits()
        assert limits["ira"] == _LIMITS_DEFAULTS["ira"]

    def test_all_overrides_independent(self, monkeypatch):
        """Each limit key can be overridden independently without affecting the others. refs #106"""
        monkeypatch.setenv("YNAB_LIMIT_IRA", "8000")
        monkeypatch.setenv("YNAB_LIMIT_IRA_CATCHUP", "1500")
        monkeypatch.setenv("YNAB_LIMIT_401K_EMPLOYEE", "24000")
        monkeypatch.delenv("YNAB_LIMIT_401K_CATCHUP", raising=False)
        monkeypatch.delenv("YNAB_LIMIT_401K_TOTAL", raising=False)
        monkeypatch.delenv("YNAB_LIMIT_YEAR", raising=False)

        from ynab_tools.reports.retirement import _LIMITS_DEFAULTS, _get_limits

        limits = _get_limits()
        assert limits["ira"] == 8000
        assert limits["ira_catchup"] == 1500
        assert limits["401k_employee"] == 24000
        # Non-overridden keys keep defaults
        assert limits["401k_catchup"] == _LIMITS_DEFAULTS["401k_catchup"]
        assert limits["401k_total"] == _LIMITS_DEFAULTS["401k_total"]
