# Security Policy

## Sensitive Data This Tool Handles

ynab-tools has access to your complete YNAB budget: all transactions, account balances, category names, payees, and budget history. The YNAB personal access token stored in your config grants read and write access to your budget. Treat it like a password.

## Credential Storage

- Credentials are stored in `~/.config/ynab-tools/config.json` with `0o600` permissions (owner read/write only).
- The data directory (`~/.local/share/ynab-tools/`) is created with `0o700` permissions.
- Backup files containing payee data are stored in `~/.local/share/ynab-tools/backups/` with `0o700` permissions.
- **Never commit** `.env` or `config.json` to version control. Both are in `.gitignore`.
- Use `ynab configure` to set credentials interactively. It supports 1Password for secure token retrieval.

## MCP Server Write Access

When running as an MCP server (`ynab-mcp`), several tools write directly to your YNAB budget:

- `fund_category`, `fund_goals_apply`, `paycheck_funding_apply` - modify category budgets
- `add_transaction`, `update_transaction`, `delete_transaction`, `approve_transactions` - create and modify transactions
- `payee_fix` - bulk-renames payees across your budget
- `create_category`, `create_category_group`, `set_category_goal`, `clear_category_goal` - modify budget structure
- `reconcile_account` - creates adjustment transactions

Review tool calls before approving them. All write actions are logged to the local `audit_log` table.

## Recommendations

- Rotate your YNAB personal access token periodically via Account Settings → Developer Settings.
- Use the most restrictive token scope available from YNAB.
- Keep Python and dependencies updated: `pip install --upgrade ynab-tools`
- Do not expose `ynab-dashboard` on a public network (`--host 0.0.0.0`) unless you control access to the host. A non-loopback bind requires `DASHBOARD_PASSWORD`; the dashboard refuses to start without it. Put TLS in front of it if it leaves the machine, since HTTP Basic is only base64-encoded.
- Run `ynab sync --status` to verify that only expected data is present in the local database.

## Reporting Vulnerabilities

**Do not file public GitHub issues for security vulnerabilities.**

To report a security issue, open a [GitHub Security Advisory](https://github.com/mrlesmithjr/ynab-tools/security/advisories/new) or email the maintainer directly. Include a description of the issue, steps to reproduce, and the potential impact. You will receive a response within 72 hours.
