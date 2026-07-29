# Contributing to ynab-tools

Thanks for your interest in contributing! This project is a personal toolkit for YNAB budget management, but contributions are welcome.

## Getting Started

1. Fork the repository
2. Clone your fork and install in development mode:

```bash
pip install -e ".[dev]"
```

3. Create a feature branch from `main`

## Development

### Running Tests

```bash
pytest
```

### Code Style

- Python 3.11+ with type hints on all public functions
- Use `logging` for operational messages, `print()` for CLI output
- Parameterized SQL queries only (no string formatting)
- Use `pathlib.Path` for file paths

### Project Structure

- `ynab_tools/cli.py` - CLI entry point (argparse)
- `ynab_tools/client.py` - YNAB API wrapper
- `ynab_tools/config.py` - Configuration and credential loading
- `ynab_tools/db.py` - SQLite schema and queries
- `ynab_tools/sync.py` - Data sync engine
- `ynab_tools/payees/` - Payee management tools
- `ynab_tools/categories/` - Transaction categorization
- `ynab_tools/reports/` - Net worth and reporting
- `ynab_tools/importers/` - Brokerage CSV import

## Submitting Changes

1. Write tests for new functionality
2. Ensure all existing tests pass
3. Open a pull request with a clear description of the change

## Reporting Issues

Open an issue on GitHub with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your Python version and OS
