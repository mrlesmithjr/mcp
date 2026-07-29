"""Centralized configuration: layered config from config.json, env vars, and .env fallback.

Resolution order (highest wins):
  1. Environment variables (YNAB_ACCESS_TOKEN, YNAB_PLAN_ID, etc.)
  2. ~/.config/ynab-tools/config.json
  3. .env file in project root (dev workflow fallback)
  4. Built-in defaults
"""

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Package root (where source code lives) and its repo-root parent (dev
# checkout only - used for the .env fallback below, which is intentionally
# not part of the installed package).
_PACKAGE_DIR = Path(__file__).parent
_PROJECT_ROOT = _PACKAGE_DIR.parent

# Static data files ship with the package. This must live inside the
# ynab_tools/ package tree (not a repo-root sibling) so setuptools bundles it
# into the built wheel via [tool.setuptools.package-data]; a sibling
# directory only resolves in editable/dev installs where the source tree is
# imported in place (refs #122).
DATA_DIR = _PACKAGE_DIR / "data"
RULES_FILE = DATA_DIR / "payee_rules.json"
CATEGORY_DEFS_FILE = DATA_DIR / "category_definitions.json"
CATEGORY_DEFS_EXAMPLE_FILE = DATA_DIR / "category_definitions.example.json"


def resolve_category_defs_file() -> Path:
    """Return the category-definitions file to read, user copy first.

    category_definitions.json describes which merchants belong in which
    category, so a populated one is a record of where its owner actually
    shops - it is user data, not shipped content, and is gitignored for the
    same reason payee_rules.json and category_classification.json are. Only
    the .example.json template ships; a user copies it and fills in their own
    payees. Falling back to the template keeps a fresh install working out of
    the box instead of failing on a missing file.
    """
    return CATEGORY_DEFS_FILE if CATEGORY_DEFS_FILE.exists() else CATEGORY_DEFS_EXAMPLE_FILE


# Config file location
CONFIG_DIR = Path.home() / ".config" / "ynab-tools"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Maps config.json keys to the environment variable names used throughout the codebase.
# Loading config.json populates os.environ via these mappings so all existing
# os.environ.get() calls in reports/ and importers/ continue to work unchanged.
_CONFIG_KEY_TO_ENV = {
    "access_token": "YNAB_ACCESS_TOKEN",
    "plan_id": "YNAB_PLAN_ID",
    # plan_name was removed: stored in config.json by the Admin panel as a display
    # label but never read by Python code, so exposing it as an env var adds confusion.
    "data_dir": "YNAB_DATA_DIR",
    "regular_pay": "YNAB_REGULAR_PAY",
    "bonus_threshold": "YNAB_BONUS_THRESHOLD",
    "paycheck_payees": "YNAB_PAYCHECK_PAYEES",
    "income_payees": "YNAB_INCOME_PAYEES",
    "retirement_401k": "YNAB_RETIREMENT_401K",
    "retirement_roth_ira": "YNAB_RETIREMENT_ROTH_IRA",
    "retirement_trad_ira": "YNAB_RETIREMENT_TRAD_IRA",
    "retirement_taxable": "YNAB_RETIREMENT_TAXABLE",
    "birth_year": "YNAB_BIRTH_YEAR",
    "retirement_milestones": "YNAB_RETIREMENT_MILESTONES",
    "gross_salary": "YNAB_GROSS_SALARY",
    "gross_ote": "YNAB_GROSS_OTE",
    "retirement_annual": "YNAB_RETIREMENT_ANNUAL",
    "retirement_rate": "YNAB_RETIREMENT_RATE",
    "retirement_max_age": "YNAB_RETIREMENT_MAX_AGE",
    "employer_match_pct": "YNAB_EMPLOYER_MATCH_PCT",
    "ratio_housing": "YNAB_RATIO_HOUSING",
    "ratio_auto": "YNAB_RATIO_AUTO",
    "ratio_debt": "YNAB_RATIO_DEBT",
    "protected_groups": "YNAB_PROTECTED_GROUPS",
    "protected_names": "YNAB_PROTECTED_NAMES",
    "bonus_funded_groups": "YNAB_BONUS_FUNDED_GROUPS",
    "bonus_funded_categories": "YNAB_BONUS_FUNDED_CATEGORIES",
    "excluded_groups": "YNAB_EXCLUDED_GROUPS",
    "excluded_categories": "YNAB_EXCLUDED_CATEGORIES",
    "fidelity_accounts": "YNAB_FIDELITY_ACCOUNTS",
    "subscription_categories": "YNAB_SUBSCRIPTION_CATEGORIES",
    "non_subscription_payees": "YNAB_NON_SUBSCRIPTION_PAYEES",
    "payee_prefix_overrides": "YNAB_PAYEE_PREFIX_OVERRIDES",
    "payee_name_overrides": "YNAB_PAYEE_NAME_OVERRIDES",
    "dashboard_sync_interval_minutes": "YNAB_DASHBOARD_SYNC_INTERVAL",
    "dashboard_password": "DASHBOARD_PASSWORD",
    "holding_category": "YNAB_HOLDING_CATEGORY",
    "savings_general_category": "YNAB_SAVINGS_GENERAL_CATEGORY",
    "savings_long_term_category": "YNAB_SAVINGS_LONG_TERM_CATEGORY",
    "emergency_fund_category": "YNAB_EMERGENCY_FUND_CATEGORY",
    "limit_401k_employee": "YNAB_LIMIT_401K_EMPLOYEE",
    "limit_401k_catchup": "YNAB_LIMIT_401K_CATCHUP",
    "limit_401k_total": "YNAB_LIMIT_401K_TOTAL",
    "limit_ira": "YNAB_LIMIT_IRA",
    "limit_ira_catchup": "YNAB_LIMIT_IRA_CATCHUP",
    "limit_year": "YNAB_LIMIT_YEAR",
    "retirement_contrib_payees": "YNAB_RETIREMENT_CONTRIB_PAYEES",
    "retirement_match_payees": "YNAB_RETIREMENT_MATCH_PAYEES",
    "retirement_fee_payees": "YNAB_RETIREMENT_FEE_PAYEES",
    "home_category_prefix": "YNAB_HOME_CATEGORY_PREFIX",
    "onepassword_item": "YNAB_1PASSWORD_ITEM",
    "ss_fra_benefit": "YNAB_SS_FRA_BENEFIT",
    "ss_fra_age": "YNAB_SS_FRA_AGE",
    "ss_delayed_benefit": "YNAB_SS_DELAYED_BENEFIT",
    "ss_delayed_age": "YNAB_SS_DELAYED_AGE",
    "ss_early_benefit": "YNAB_SS_EARLY_BENEFIT",
    "ss_early_age": "YNAB_SS_EARLY_AGE",
    "ss_statement_date": "YNAB_SS_STATEMENT_DATE",
}

_env_loaded = False


def load_env() -> None:
    """Load configuration into os.environ. Idempotent.

    Resolution order:
      1. ~/.config/ynab-tools/config.json  (primary - stable across installs)
      2. .env file in project root          (dev workflow fallback)
      3. Existing environment variables     (always win - set externally)
    """
    global _env_loaded
    if _env_loaded:
        return

    # Layer 1: config.json - use setdefault so existing env vars win
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                file_config = json.load(f)
            for config_key, env_var in _CONFIG_KEY_TO_ENV.items():
                value = file_config.get(config_key)
                if value is not None and str(value).strip():
                    os.environ.setdefault(env_var, str(value).strip())
            logger.info("Loaded config from %s", CONFIG_FILE)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load %s: %s", CONFIG_FILE, e)

    # Layer 2: .env file (dev fallback - only sets vars not already set)
    env_path = _PROJECT_ROOT / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())
        except OSError as e:
            logger.warning("Could not read .env file: %s", e)

    _env_loaded = True


def _get_data_dir() -> Path:
    """Resolve the user data directory (DB, backups). Created on first use."""
    load_env()
    custom = os.environ.get("YNAB_DATA_DIR")
    if custom:
        data_dir = Path(custom)
    else:
        data_dir = Path.home() / ".local/share/ynab-tools"
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return data_dir


# User data paths (DB and backups) - overridable via YNAB_DATA_DIR env var
_data_dir = _get_data_dir()
DB_PATH = _data_dir / "ynab.db"
BACKUPS_DIR = _data_dir / "backups"


def atomic_write_json(path: Path, data, *, dir_mode: int | None = None, file_mode: int | None = None) -> None:
    """Write JSON to `path` atomically.

    Writes to a temp file in the same directory, flushes + fsyncs it, then
    os.replace()s it onto the target path. A crash mid-write (Ctrl-C,
    OOM-kill, host reboot) only ever corrupts the abandoned temp file - the
    real file at `path` is untouched until the replace succeeds. Used for
    config.json (holds the YNAB access token) and best-effort payee
    backup/audit snapshots (issue #78).

    `file_mode` is applied to the temp file BEFORE it is written to and
    BEFORE the replace, not after: os.replace() is what makes the file
    visible at `path`, so by the time that happens the permissions must
    already be correct. Applying chmod only after replace() leaves the
    file world-readable at its final path (default umask, typically 0644)
    for the entire write+fsync window (refs #78 code review).

    On any exception during the write, the temp file is removed before
    re-raising so a partially-written, possibly-fsynced copy of the data
    (e.g. a live access token) never lingers on disk.

    `dir_mode`, when given, is also applied unconditionally to the
    containing directory (not just at creation time via mkdir's `mode`)
    so pre-existing directories created before this permission was
    enforced get secured too.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=dir_mode if dir_mode is not None else 0o777)
    if dir_mode is not None:
        path.parent.chmod(dir_mode)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        # os.open() with an explicit mode sets the file's permissions at the
        # instant it is created, before any content is written - there is no
        # window where it exists at a more permissive default mode.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, file_mode if file_mode is not None else 0o666)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def apply_config(file_config: dict) -> None:
    """Apply a config dict to os.environ. Called after saving config.json to make
    changes take effect in the running process without a restart."""
    for config_key, env_var in _CONFIG_KEY_TO_ENV.items():
        value = file_config.get(config_key)
        if value is not None and str(value).strip():
            os.environ[env_var] = str(value).strip()
        elif env_var in os.environ:
            del os.environ[env_var]


def require_credentials() -> tuple[str, str]:
    """Load env and return (token, plan_id), exiting on missing values."""
    load_env()
    token = os.environ.get("YNAB_ACCESS_TOKEN")
    plan_id = os.environ.get("YNAB_PLAN_ID") or os.environ.get("YNAB_BUDGET_ID")
    if not token or not plan_id:
        print(
            f"Error: YNAB_ACCESS_TOKEN and YNAB_PLAN_ID are required.\n"
            f"\n"
            f"Create {CONFIG_FILE}:\n"
            f"  mkdir -p {CONFIG_DIR}\n"
            f"  cat > {CONFIG_FILE} << 'EOF'\n"
            f"  {{\n"
            f'    "access_token": "your-ynab-personal-access-token",\n'
            f'    "plan_id": "your-budget-uuid"\n'
            f"  }}\n"
            f"  EOF\n"
            f"\n"
            f"Or set environment variables:\n"
            f"  export YNAB_ACCESS_TOKEN=your-token\n"
            f"  export YNAB_PLAN_ID=your-budget-uuid\n",
            file=sys.stderr,
        )
        sys.exit(1)
    return token, plan_id
