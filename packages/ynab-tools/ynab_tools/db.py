"""SQLite database: schema, connection helper, and query functions."""

import contextlib
import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path

from .config import DB_PATH

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT,
    on_budget INTEGER,
    closed INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    balance REAL,
    cleared_balance REAL,
    uncleared_balance REAL,
    note TEXT,
    last_reconciled_at TEXT,
    direct_import_linked INTEGER DEFAULT 0,
    debt_interest_rates TEXT,
    debt_minimum_payments TEXT,
    debt_original_balance REAL,
    debt_escrow_amounts TEXT,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS budget_months (
    month TEXT PRIMARY KEY,
    income REAL,
    budgeted REAL,
    activity REAL,
    to_be_budgeted REAL,
    age_of_money INTEGER,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS budget_categories (
    id TEXT NOT NULL,
    budget_month TEXT NOT NULL,
    category_group_id TEXT,
    category_group_name TEXT,
    name TEXT NOT NULL,
    hidden INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    budgeted REAL,
    activity REAL,
    balance REAL,
    goal_type TEXT,
    goal_target REAL,
    goal_target_month TEXT,
    goal_cadence INTEGER,
    goal_cadence_frequency INTEGER,
    goal_months_to_budget INTEGER,
    goal_percentage_complete REAL,
    goal_under_funded REAL,
    goal_overall_funded REAL,
    goal_overall_left REAL,
    last_synced_at TEXT,
    PRIMARY KEY (id, budget_month)
);

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    amount REAL,
    memo TEXT,
    cleared TEXT,
    approved INTEGER,
    flag_color TEXT,
    flag_name TEXT,
    account_id TEXT,
    account_name TEXT,
    payee_id TEXT,
    payee_name TEXT,
    category_id TEXT,
    category_name TEXT,
    transfer_account_id TEXT,
    debt_transaction_type TEXT,
    import_id TEXT,
    import_payee_name TEXT,
    import_payee_name_original TEXT,
    matched_transaction_id TEXT,
    deleted INTEGER DEFAULT 0,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS subtransactions (
    id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    amount REAL,
    memo TEXT,
    payee_id TEXT,
    payee_name TEXT,
    category_id TEXT,
    category_name TEXT,
    transfer_account_id TEXT,
    deleted INTEGER DEFAULT 0,
    last_synced_at TEXT,
    FOREIGN KEY (transaction_id) REFERENCES transactions(id)
);

CREATE INDEX IF NOT EXISTS idx_subtransactions_transaction ON subtransactions(transaction_id);
CREATE INDEX IF NOT EXISTS idx_subtransactions_category ON subtransactions(category_name);

CREATE TABLE IF NOT EXISTS payees (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    transfer_account_id TEXT,
    deleted INTEGER DEFAULT 0,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    synced_at TEXT NOT NULL,
    accounts_count INTEGER,
    categories_count INTEGER,
    transactions_count INTEGER,
    months_count INTEGER,
    duration_seconds REAL
);

CREATE TABLE IF NOT EXISTS sync_state (
    endpoint TEXT PRIMARY KEY,
    server_knowledge INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS net_worth_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    total_assets REAL,
    total_debt REAL,
    net_worth REAL,
    on_budget_assets REAL,
    on_budget_debt REAL,
    off_budget_assets REAL,
    off_budget_debt REAL,
    account_details TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_payee ON transactions(payee_name);
CREATE INDEX IF NOT EXISTS idx_transactions_payee_id ON transactions(payee_id);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category_name);
CREATE INDEX IF NOT EXISTS idx_budget_categories_month ON budget_categories(budget_month);
CREATE INDEX IF NOT EXISTS idx_budget_categories_group ON budget_categories(category_group_name);
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON net_worth_snapshots(snapshot_date);

CREATE TABLE IF NOT EXISTS funding_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    category_id TEXT NOT NULL,
    category_name TEXT NOT NULL,
    category_group TEXT,
    budget_month TEXT NOT NULL,
    old_budgeted REAL,
    new_budgeted REAL,
    delta REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_funding_log_timestamp ON funding_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_funding_log_month ON funding_log(budget_month);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    entity_name TEXT,
    details TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);

CREATE TABLE IF NOT EXISTS planned_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_name TEXT NOT NULL,
    category_id TEXT,
    amount REAL NOT NULL,
    due_date TEXT NOT NULL,
    memo TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_planned_expenses_status ON planned_expenses(status);
CREATE INDEX IF NOT EXISTS idx_planned_expenses_due ON planned_expenses(due_date);

CREATE TABLE IF NOT EXISTS money_movements (
    id TEXT PRIMARY KEY,
    month TEXT NOT NULL,
    moved_at TEXT NOT NULL,
    note TEXT,
    money_movement_group_id TEXT,
    performed_by_user_id TEXT,
    from_category_id TEXT,
    from_category_name TEXT,
    to_category_id TEXT,
    to_category_name TEXT,
    amount_milliunits INTEGER NOT NULL,
    amount REAL NOT NULL,
    deleted INTEGER DEFAULT 0,
    last_synced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_money_movements_month ON money_movements(month);
CREATE INDEX IF NOT EXISTS idx_money_movements_from_cat ON money_movements(from_category_id);
CREATE INDEX IF NOT EXISTS idx_money_movements_to_cat ON money_movements(to_category_id);
CREATE INDEX IF NOT EXISTS idx_money_movements_moved_at ON money_movements(moved_at);
CREATE INDEX IF NOT EXISTS idx_money_movements_group ON money_movements(money_movement_group_id);
"""


MIGRATIONS = [
    # Add new account columns (debt details, reconciliation, notes)
    ("ALTER TABLE accounts ADD COLUMN note TEXT", "accounts", "note"),
    ("ALTER TABLE accounts ADD COLUMN last_reconciled_at TEXT", "accounts", "last_reconciled_at"),
    ("ALTER TABLE accounts ADD COLUMN direct_import_linked INTEGER DEFAULT 0", "accounts", "direct_import_linked"),
    ("ALTER TABLE accounts ADD COLUMN debt_interest_rates TEXT", "accounts", "debt_interest_rates"),
    ("ALTER TABLE accounts ADD COLUMN debt_minimum_payments TEXT", "accounts", "debt_minimum_payments"),
    ("ALTER TABLE accounts ADD COLUMN debt_original_balance REAL", "accounts", "debt_original_balance"),
    ("ALTER TABLE accounts ADD COLUMN debt_escrow_amounts TEXT", "accounts", "debt_escrow_amounts"),
    # Add goal recurrence fields (cadence, frequency, months remaining in current cycle)
    ("ALTER TABLE budget_categories ADD COLUMN goal_cadence INTEGER", "budget_categories", "goal_cadence"),
    (
        "ALTER TABLE budget_categories ADD COLUMN goal_cadence_frequency INTEGER",
        "budget_categories",
        "goal_cadence_frequency",
    ),
    (
        "ALTER TABLE budget_categories ADD COLUMN goal_months_to_budget INTEGER",
        "budget_categories",
        "goal_months_to_budget",
    ),
    # Add new transaction columns (flags, debt type, import tracking)
    ("ALTER TABLE transactions ADD COLUMN flag_color TEXT", "transactions", "flag_color"),
    ("ALTER TABLE transactions ADD COLUMN flag_name TEXT", "transactions", "flag_name"),
    ("ALTER TABLE transactions ADD COLUMN debt_transaction_type TEXT", "transactions", "debt_transaction_type"),
    ("ALTER TABLE transactions ADD COLUMN import_id TEXT", "transactions", "import_id"),
    ("ALTER TABLE transactions ADD COLUMN matched_transaction_id TEXT", "transactions", "matched_transaction_id"),
]


POST_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_transactions_flag ON transactions(flag_color)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_debt_type ON transactions(debt_transaction_type)",
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Add columns that don't exist yet (idempotent)."""
    for sql, table, column in MIGRATIONS:
        try:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in cols:
                conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # Column already exists or table doesn't exist yet

    for idx_sql in POST_MIGRATION_INDEXES:
        try:
            conn.execute(idx_sql)
        except sqlite3.OperationalError:
            pass

    # Remove stale subtransaction rows left by YNAB ID regeneration on split edits.
    # When YNAB edits a split transaction it issues new subtransaction IDs; old rows
    # with old IDs accumulate alongside new rows. This keeps only the most recently
    # synced batch (by last_synced_at) per transaction_id. Idempotent: transactions
    # with a single synced batch are untouched. Catches OperationalError in case the
    # table hasn't been created yet (e.g. schema not yet initialized).
    # Contract: relies on sync.py always writing datetime.now(UTC).isoformat() for
    # last_synced_at, making timestamps monotonically increasing across sync runs.
    try:
        conn.execute(
            """
            DELETE FROM subtransactions
            WHERE last_synced_at < (
                SELECT MAX(last_synced_at)
                FROM subtransactions s2
                WHERE s2.transaction_id = subtransactions.transaction_id
            )
            AND transaction_id IN (
                SELECT transaction_id FROM subtransactions
                GROUP BY transaction_id
                HAVING COUNT(DISTINCT last_synced_at) > 1
            )
            """
        )
    except sqlite3.OperationalError:
        pass

    # Backfill funding_log rows where category_group was not recorded at write time.
    # Idempotent: only touches NULL rows; resolves group from budget_categories by
    # matching category_id + budget_month.
    conn.execute(
        """
        UPDATE funding_log
        SET category_group = (
            SELECT category_group_name
            FROM budget_categories
            WHERE id = funding_log.category_id
              AND budget_month = funding_log.budget_month
            LIMIT 1
        )
        WHERE category_group IS NULL
        """
    )
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    """Initialize the database schema and run any pending migrations."""
    conn.executescript(SCHEMA_SQL)
    _run_migrations(conn)
    conn.commit()


def current_month() -> str:
    """Return the first day of the current month as YYYY-MM-01."""
    return datetime.now().strftime("%Y-%m-01")


def get_connection(db_path: Path | None = None, *, init: bool = True) -> sqlite3.Connection:
    """Get a SQLite connection with row_factory enabled.

    Calls init_db() by default so callers don't need to remember to run schema
    migrations. Pass init=False only for read-only connections or when managing
    init explicitly.
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    # sqlite creates the file honouring the process umask, which on a default
    # macOS setup yields 0644 - world-readable. This database holds the full
    # budget (balances, transactions, payees, net worth), so it gets the same
    # user-only treatment as config.json. The containing directory is already
    # 0700, so this is defence in depth rather than the only barrier.
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    conn.row_factory = sqlite3.Row
    if init:
        init_db(conn)
    return conn


def log_audit(
    conn: sqlite3.Connection,
    action: str,
    entity_type: str,
    entity_id: str | None,
    entity_name: str | None,
    details: str,
    source: str,
) -> None:
    """Record an action in the audit log.

    action: create-transaction, update-transaction, split-transaction,
            set-goal, clear-goal, create-category, categorize
    entity_type: transaction, category, goal
    details: human-readable description of what changed
    source: CLI command or module that triggered the action
    """
    conn.execute(
        """
        INSERT INTO audit_log (timestamp, action, entity_type, entity_id,
                               entity_name, details, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (
            datetime.now(UTC).isoformat(),
            action,
            entity_type,
            entity_id,
            entity_name,
            details,
            source,
        ),
    )


def find_account(conn: sqlite3.Connection, search: str) -> dict | None:
    """Find an account by partial name match. Handles parentheses in names like 401(k)."""
    search_clean = search.replace("(", "").replace(")", "")
    rows = conn.execute(
        """
        SELECT id, name, balance, type, on_budget, last_reconciled_at
        FROM accounts
        WHERE deleted = 0 AND closed = 0
          AND (LOWER(name) LIKE LOWER(?)
               OR LOWER(REPLACE(REPLACE(name, '(', ''), ')', '')) LIKE LOWER(?))
        ORDER BY name
        """,
        (f"%{search}%", f"%{search_clean}%"),
    ).fetchall()
    if not rows:
        return None
    if len(rows) == 1:
        return dict(rows[0])
    print(f"Multiple accounts match '{search}':")
    for r in rows:
        print(f"  {r['name']:<45} ${r['balance']:>12,.2f}")
    print("\nBe more specific.")
    return None


def get_mismatches(conn: sqlite3.Connection) -> list[dict]:
    """Find transactions where payee_name doesn't match import_payee_name_original."""
    rows = conn.execute("""
        SELECT date, amount, payee_name, import_payee_name_original,
               category_name, id
        FROM transactions
        WHERE deleted = 0
          AND import_payee_name_original IS NOT NULL
          AND import_payee_name_original != ''
          AND payee_name != import_payee_name_original
        ORDER BY date DESC
    """).fetchall()

    return [
        {
            "date": row["date"],
            "amount": row["amount"] or 0,
            "payee_name": row["payee_name"],
            "original_import": row["import_payee_name_original"],
            "category_name": row["category_name"],
            "ynab_transaction_id": row["id"],
        }
        for row in rows
    ]


def get_payee_ids_with_transactions(conn: sqlite3.Connection) -> set[str]:
    """Get the set of payee IDs that have at least one transaction."""
    rows = conn.execute("""
        SELECT DISTINCT payee_id
        FROM transactions
        WHERE deleted = 0 AND payee_id IS NOT NULL
    """).fetchall()
    return {row["payee_id"] for row in rows}
