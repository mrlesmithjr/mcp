-- YNAB Analysis Queries (SQLite)
-- Adapted from FinInsights PostgreSQL queries for local use.
--
-- Usage:
--   sqlite3 ynab.db < queries.sql              # Run all
--   sqlite3 ynab.db ".read queries.sql"         # Interactive
--   sqlite3 -header -column ynab.db "query"     # Single query

-- 1. Current Month Budget Status
SELECT
    category_group_name,
    name AS category,
    budgeted,
    activity AS spent,
    balance AS remaining,
    CASE
        WHEN budgeted != 0 THEN ROUND(ABS(activity) / budgeted * 100, 1)
        ELSE NULL
    END AS pct_spent
FROM budget_categories
WHERE budget_month = strftime('%Y-%m-01', 'now')
    AND budgeted != 0
    AND deleted = 0
ORDER BY
    CASE WHEN balance < 0 THEN 0 ELSE 1 END,
    balance ASC;

-- 2. Monthly Income vs Expense Summary (Last 12 Months)
SELECT
    strftime('%Y-%m', date) AS month,
    SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS total_income,
    SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END) AS total_expenses,
    SUM(amount) AS net_cash_flow,
    ROUND(
        SUM(amount) * 100.0 / NULLIF(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 0),
        1
    ) AS savings_rate_pct
FROM transactions
WHERE date >= date('now', '-12 months', 'start of month')
    AND deleted = 0
GROUP BY strftime('%Y-%m', date)
ORDER BY month DESC;

-- 3. Net Worth Breakdown
SELECT 'Total Assets (On Budget)' AS metric,
    SUM(CASE WHEN on_budget = 1 AND balance > 0 THEN balance ELSE 0 END) AS amount
FROM accounts WHERE deleted = 0 AND closed = 0
UNION ALL
SELECT 'Total Debt (On Budget)',
    SUM(CASE WHEN on_budget = 1 AND balance < 0 THEN balance ELSE 0 END)
FROM accounts WHERE deleted = 0 AND closed = 0
UNION ALL
SELECT 'Net Worth (On Budget)',
    SUM(CASE WHEN on_budget = 1 THEN balance ELSE 0 END)
FROM accounts WHERE deleted = 0 AND closed = 0
UNION ALL
SELECT 'Retirement Assets (Off Budget)',
    SUM(CASE WHEN on_budget = 0 AND type IN ('otherAsset') THEN balance ELSE 0 END)
FROM accounts WHERE deleted = 0 AND closed = 0
UNION ALL
SELECT 'Total Net Worth', SUM(balance)
FROM accounts WHERE deleted = 0 AND closed = 0;

-- 4. Emergency Fund Analysis
WITH monthly_expenses AS (
    SELECT AVG(monthly_total) AS avg_monthly_expenses
    FROM (
        SELECT strftime('%Y-%m', date) AS month,
            SUM(CASE WHEN amount < 0 THEN ABS(amount) ELSE 0 END) AS monthly_total
        FROM transactions
        WHERE date >= date('now', '-6 months')
            AND deleted = 0
        GROUP BY strftime('%Y-%m', date)
    )
),
liquid_assets AS (
    SELECT SUM(balance) AS total_liquid
    FROM accounts
    WHERE on_budget = 1 AND balance > 0
        AND deleted = 0 AND closed = 0
)
SELECT
    total_liquid AS liquid_assets,
    avg_monthly_expenses,
    ROUND(total_liquid / NULLIF(avg_monthly_expenses, 0), 1) AS months_covered
FROM liquid_assets, monthly_expenses;

-- 5. Recurring Expense Analysis
SELECT
    payee_name,
    COUNT(*) AS transaction_count,
    SUM(amount) AS total_spent,
    ROUND(AVG(amount), 2) AS avg_amount,
    MIN(date) AS first_transaction,
    MAX(date) AS last_transaction
FROM transactions
WHERE amount < 0
    AND date >= date('now', '-12 months')
    AND deleted = 0
GROUP BY payee_name
HAVING COUNT(*) >= 6
ORDER BY SUM(amount) ASC
LIMIT 30;

-- 6. Category Group Spending Trends (Last 6 Months)
SELECT
    category_group_name,
    COUNT(DISTINCT budget_month) AS months_tracked,
    SUM(budgeted) AS total_budgeted,
    SUM(activity) AS total_spent,
    ROUND(AVG(budgeted), 2) AS avg_monthly_budget,
    ROUND(AVG(activity), 2) AS avg_monthly_spent,
    ROUND(SUM(activity) * 100.0 / NULLIF(SUM(budgeted), 0), 1) AS pct_of_budget_used
FROM budget_categories
WHERE budget_month >= date('now', '-6 months', 'start of month')
    AND deleted = 0
    AND budgeted != 0
GROUP BY category_group_name
ORDER BY SUM(ABS(activity)) DESC;

-- 7. Large One-Time Expenses
SELECT date, payee_name, category_name, amount, memo
FROM transactions
WHERE amount < -500
    AND date >= date('now', '-6 months')
    AND deleted = 0
    AND payee_name NOT LIKE 'Transfer :%'
ORDER BY amount ASC
LIMIT 20;

-- 8. Account Balances Overview
SELECT
    name AS account_name,
    type AS account_type,
    on_budget,
    balance,
    cleared_balance,
    CASE WHEN balance < 0 THEN 'Debt' ELSE 'Asset' END AS account_category
FROM accounts
WHERE deleted = 0 AND closed = 0
ORDER BY on_budget DESC, balance DESC;

-- 9. Ready to Assign (RTA) - Last 3 Months
SELECT month, to_be_budgeted AS rta, income, budgeted
FROM budget_months
ORDER BY month DESC
LIMIT 3;

-- 10. Sinking Fund Health Check
SELECT
    name AS category,
    budget_month,
    budgeted,
    activity,
    balance,
    goal_type,
    goal_target,
    goal_target_month,
    goal_percentage_complete
FROM budget_categories
WHERE budget_month = strftime('%Y-%m-01', 'now')
    AND category_group_name = 'Sinking Funds'  -- Adjust to your YNAB category group name
    AND deleted = 0
ORDER BY balance ASC;

-- 11. Net Worth History (from snapshots)
SELECT snapshot_date, net_worth, total_assets, total_debt
FROM net_worth_snapshots
ORDER BY snapshot_date DESC
LIMIT 12;

-- 12. Split Transaction Details (subtransactions with category breakdowns)
SELECT t.date, t.payee_name, t.amount AS total,
       st.category_name, st.amount AS split_amount, st.memo
FROM transactions t
JOIN subtransactions st ON st.transaction_id = t.id
WHERE st.deleted = 0
ORDER BY t.date DESC
LIMIT 50;

-- 13. Spending by Category (includes split transaction detail)
-- Use this instead of just transactions to get accurate per-category totals
SELECT COALESCE(st.category_name, t.category_name) AS category,
       SUM(COALESCE(st.amount, t.amount)) AS total_spent,
       COUNT(*) AS txn_count
FROM transactions t
LEFT JOIN subtransactions st ON st.transaction_id = t.id AND st.deleted = 0
WHERE t.deleted = 0
    AND t.date >= strftime('%Y-%m-01', 'now')
    AND COALESCE(st.category_name, t.category_name) IS NOT NULL
    AND COALESCE(st.category_name, t.category_name) != 'Split (Multiple Categories...)'
GROUP BY category
ORDER BY total_spent ASC;
