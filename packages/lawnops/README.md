# LawnOps

Personal lawn care operations CLI built for my property in North Georgia (Zone 7b/8a). Handles soil temperature monitoring, pre-emergent timing advisories, application window planning, Hunter Hydrawise irrigation control, and SQLite-backed tracking for treatments, products, equipment, mowing, and spending.

This is a personal tool that I'm sharing publicly. It's tailored to my setup but the architecture is modular enough to adapt if you find it useful.

## What It Does

- **Weather Monitoring** - Soil temperature (6cm depth), air temp, precipitation, and wind from the [Open-Meteo API](https://open-meteo.com/) (free, no key required)
- **Pre-Emergent Advisory** - Tracks consecutive days above soil temp threshold → SAFE / WARNING / URGENT / LATE
- **Spray Window Assessment** - Evaluates next 48 hours for post-emergent conditions (temp, wind, rain) with mowing schedule awareness
- **Application Window Finder** - Scores next 7 days for spray, granular, or pre-emergent application with weather and mow-buffer analysis
- **Coverage & Mix Calculators** - How many bags for your yard, concentrate per tank load
- **Irrigation Control** - Full Hydrawise management via [pydrawise](https://github.com/dknowles2/pydrawise): status, run/stop zones, suspend/resume, watering history
- **Database Tracking** - SQLite for treatments, products, equipment, mowing visits, purchases, spending reports
- **Reorder Alerts** - Flags zero-stock products that have been used in past treatments
- **YNAB Import** - Pulls historical lawn spending from [ynab-tools](https://github.com/mrlesmithjr/mcp/tree/main/packages/ynab-tools) database with configurable payee mapping
- **Obsidian Import** - One-time import from an Obsidian vault Task List
- **Programmatic API** - Business logic is importable without CLI dependencies

## Setup

```bash
git clone https://github.com/mrlesmithjr/mcp.git
cd mcp/packages/lawnops
uv tool install --editable .
```

The SQLite database is created and initialized automatically on first use. To initialize it explicitly before first use, run `lawnops db init` (optional).

### Configure credentials

Run the setup wizard to configure Hydrawise irrigation credentials:

```bash
lawnops configure
```

The wizard detects 1Password (`op`) if installed and offers it as an input source. Only Hydrawise credentials are collected - all other config (location, thresholds, product rates) is preserved if you re-run configure.

Hydrawise credentials are only required for irrigation control. Weather advisories, soil temperature monitoring, and tracking features work without them.

### Configuration

Create a config file at `~/.config/lawnops/config.json`. Only `location` and `database` are required - everything else is optional.

**Minimal config** (weather, advisories, treatments, products, mowing):

```bash
mkdir -p ~/.config/lawnops
cat > ~/.config/lawnops/config.json << 'EOF'
{
  "location": {
    "name": "City, ST",
    "latitude": 0.0,
    "longitude": 0.0,
    "timezone": "America/New_York",
    "yard_sqft": 10000,
    "grass_type": "bermuda"
  },
  "database": {
    "path": "~/.local/share/lawnops/lawnops.db"
  }
}
EOF
```

This gets you soil temp monitoring, spray/pre-emergent advisories, treatment/product/mowing tracking, and spending reports.

**Add Hydrawise** (for irrigation control - optional):

```json
{
  "hydrawise": {
    "api_key": "your-api-key",
    "username": "your-email",
    "password": "your-password",
    "post_treatment_hold_hours": 24,
    "zone_notes": {
      "1": "Front yard",
      "2": "Flower beds"
    },
    "programs": {
      "lawn": {"zones": [1, 3, 4, 5]},
      "beds": {"zones": [2, 6]}
    }
  }
}
```

**Add YNAB integration** (for spending import and water usage correlation - optional):

```json
{
  "ynab": {
    "db_path": "~/.local/share/ynab-tools/ynab.db",
    "category": "Home: Yard & Outdoor Maintenance",
    "provider_aliases": {
      "green lawn co": "Green Lawn Co"
    },
    "water_payees": ["City Water Utility"]
  }
}
```

See `config.example.yaml` in the repo for the full settings reference including thresholds, product rates, pollen settings, mowing defaults, and Obsidian import paths.

### What Requires What

| Feature | Requires |
|---------|----------|
| Weather, advisories, spray windows | `location` only |
| Treatment/product/mowing tracking | `location` + `database` |
| Coverage & mix calculators | `product_rates` in config |
| Irrigation control | `hydrawise` credentials |
| YNAB spending import | `ynab` config + ynab-tools installed |
| Water usage correlation | `ynab` config (water_payees) |

## Usage

```bash
# Weather & advisories
lawnops now                          # Current soil temp + threshold status
lawnops trend                        # 14-day soil temp trend
lawnops advisory                     # Pre-emergent recommendation
lawnops spray                        # 48-hour spray window check (mow-buffer aware)

# Application planning
lawnops window spray                 # Best spray day this week (wind, rain, temp, mow buffer)
lawnops window granular              # Best granular application day
lawnops window pre-emergent          # Best day (bonuses for rain 24-48h after)

# Calculators
lawnops coverage "Bug B-gon"         # How many bags for your yard
lawnops coverage "Bug B-gon" --sqft 8000  # Override yard size
lawnops mix "Weed B-Gon" --tank 4    # Concentrate per tank, coverage
lawnops mix "Weed B-Gon" --tank 4 --rate northern  # Override rate type

# Irrigation (requires Hydrawise credentials in config.json)
lawnops irrigation status            # Controller + zone status
lawnops irr run 3 15                 # Run zone 3 for 15 min
lawnops irr runall 20                # All zones, 20 min each
lawnops irr runall 15 --zones 1 3 5  # Specific zones
lawnops irr stop                     # Stop all
lawnops irr suspend 48               # Suspend 48 hours
lawnops irr resume                   # Resume
lawnops irr history --days 14        # Watering history
lawnops irrigation export            # Export live config to YAML (read-only; zone advisory fields are ISE-blocked on Hydrawise)
lawnops irrigation export --output /path/to/file.yaml  # Custom output path
lawnops irrigation diff              # Compare live config vs desired-state YAML; exits non-zero on drift
lawnops irrigation diff --file /path/to/file.yaml  # Compare against a specific YAML file
lawnops irrigation apply             # DRY-RUN: show planned writes; nothing is written without --confirm
lawnops irrigation apply --confirm   # Execute program-level writes to live controller
lawnops irrigation apply --file /path/to/file.yaml --confirm  # Apply from a specific YAML file

# Database
lawnops db treatment add --date 2025-03-30 --area "Full lawn" \
  --product "Prodiamine 0-0-7" --method "Broadcast spreader" --cost 36.97
lawnops db treatment list --year 2025
lawnops db product list
lawnops db product add "Lesco 0-0-7" --category pre-emergent --qty 2 --cost 36.97 --source "Home Depot"
lawnops db product update "Prodiamine" --qty 1
lawnops db product alerts            # Show zero-stock products needing reorder
lawnops db purchase add --date 2025-03-15 --item "Prodiamine" \
  --category product --cost 36.97 --source "Home Depot"
lawnops db mowing add --date 2025-05-15 --cost 45.00
lawnops db mowing summary --year 2025
lawnops db equipment list
lawnops db report spend --year 2025
lawnops db report spend --year 2025 --category equipment
lawnops db sync-irrigation --days 30

# Deterministic checks (read-only reports; issue #146, Reminders creation removed in #39)
lawnops bermuda-check                # Soil temp vs Bermuda green-up threshold
lawnops irrigation check             # Irrigation budget/ET issue check

# Imports
lawnops db import-obsidian           # From Obsidian Task List.md
lawnops db import-ynab --preview     # Preview YNAB import
lawnops db import-ynab --year 2025   # Import specific year

# Auto-logging is on by default; disable with:
lawnops --no-log advisory

# Also works as a module
python -m lawnops advisory
```

## Configuration Reference

All settings live in `~/.config/lawnops/config.json`. A legacy `config.yaml` in the project root is used as a fallback if no config.json exists.

| Section | Purpose |
|---------|---------|
| `location` | Property coordinates, timezone, yard size (sq ft), grass type |
| `thresholds` | Soil/air temp thresholds for advisories (spray and granular) |
| `products` | Your pre-emergent and post-emergent products |
| `product_rates` | Coverage rates and mix ratios for calculators |
| `hydrawise` | API credentials, zone notes, post-treatment hold hours |
| `mowing` | Default provider, schedule day, no-mow buffer |
| `database` | DB path, auto-log toggle |
| `pollen` | Pollen source URL and spray impact thresholds |
| `ynab` | ynab-tools DB path, category, payee→provider mapping |
| `obsidian` | Path to Task List.md for import |

### Environment Variable Overrides

Environment variables take precedence over config.json values:

| Variable | Config Key |
|----------|-----------|
| `HYDRAWISE_API_KEY` | `hydrawise.api_key` |
| `HYDRAWISE_USERNAME` | `hydrawise.username` |
| `HYDRAWISE_PASSWORD` | `hydrawise.password` |
| `LAWNOPS_DB_PATH` | `database.path` |

## Programmatic API

Business logic is importable without CLI or terminal side effects:

```python
from lawnops import load_config, fetch_data, aggregate_daily, pre_emergent_advisory

config = load_config("/path/to/project")
data = fetch_data(config)
daily = aggregate_daily(data)
level, message = pre_emergent_advisory(daily, config)

from lawnops.db import list_treatments, get_spend_report, add_product, get_reorder_alerts
rows, year = list_treatments(config, year=2025)
alerts = get_reorder_alerts(config)

from lawnops.irrigation import get_status, run_zone
ctrl, sensors, programs = get_status()

from lawnops.coverage import calculate_coverage
from lawnops.mixrate import calculate_mix
from lawnops.window import find_application_window
```

## MCP Server

LawnOps includes a [Model Context Protocol](https://modelcontextprotocol.io/) server exposing 46 tools for use with Claude Code and other MCP-compatible clients. Full CRUD support - no CLI fallback needed for standard operations.

### Claude Code

```bash
claude mcp add -s user lawnops -- lawnops-mcp
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lawnops": {
      "command": "lawnops-mcp",
      "env": {
        "HYDRAWISE_API_KEY": "your-api-key",
        "HYDRAWISE_USERNAME": "your-email",
        "HYDRAWISE_PASSWORD": "your-password"
      }
    }
  }
}
```

### Weather & Planning

| Tool | Description |
|------|-------------|
| `soil_temp_now` | Current soil temp + threshold status |
| `soil_temp_trend` | Multi-day soil temp history and forecast |
| `pre_emergent_advisory` | Pre-emergent timing recommendation |
| `spray_advisory` | 48-hour spray window GO/NO-GO |
| `application_window` | Best day this week for spray/granular/pre-emergent |
| `fertilizer_recommendation` | Seasonal fertilizer advice |
| `pollen_now` | Current pollen levels |
| `pollen_trend` | Pollen trend over time |

### Treatments

| Tool | Description |
|------|-------------|
| `treatment_list` | Treatment history with IDs |
| `treatment_add` | Log a treatment |
| `treatment_delete` | Delete a treatment by ID |

### Products

| Tool | Description |
|------|-------------|
| `product_list` | Full inventory |
| `product_alerts` | Zero-stock reorder alerts |
| `product_add` | Add a product |
| `product_update` | Update qty/cost by partial name match |
| `product_delete` | Delete a product by partial name match |

### Mowing

| Tool | Description |
|------|-------------|
| `mowing_summary` | Season visits with IDs |
| `mowing_add` | Log a visit |
| `mowing_delete` | Delete a visit by ID |

### Purchases & Equipment

| Tool | Description |
|------|-------------|
| `spend_report` | YTD spending with IDs |
| `purchase_add` | Log a purchase |
| `purchase_delete` | Delete a purchase by ID |
| `equipment_list` | Equipment inventory with IDs |
| `equipment_add` | Add equipment |
| `equipment_delete` | Delete equipment by ID |

### Irrigation

| Tool | Description |
|------|-------------|
| `irrigation_status` | Controller status, zones, programs |
| `irrigation_history` | Recent watering runs |
| `irrigation_budget` | Monthly irrigation cost tracking |
| `irrigation_budget_update` | Update monthly budget or alert threshold |
| `irrigation_pace` | Current month pacing |
| `irrigation_program_list` | List controller programs and their zones |
| `irrigation_program_update` | Enable or disable a controller program |
| `irrigation_run_zone` | Run a specific zone |
| `irrigation_run_all` | Run all zones |
| `irrigation_stop` | Stop all running zones |
| `irrigation_suspend` | Suspend irrigation for N days |
| `irrigation_resume` | Resume suspended irrigation |
| `irrigation_zone_update` | Set fixed watering adjustment for a zone (advisory only; zone-level writes are ISE-blocked on Hydrawise) |
| `irrigation_export` | Export live controller config to local YAML (read-only; default: `~/.config/lawnops/irrigation_state.yaml`, never committed to the repo) |
| `irrigation_diff` | Compare live controller config against desired-state YAML; returns `has_drift` boolean, counts by category, and per-change details. `program_level` changes are actionable; `zone_level` changes are advisory (ISE-blocked on Hydrawise). Read-only. |
| `irrigation_apply` | Converge live controller to desired-state YAML for program-level changes. Default is dry-run (`confirm=False`): returns the plan with no writes. Set `confirm=True` to execute. Writes: `seasonal_adjustment_factors`, `period_days`, `start_times`, `predictive_watering_ids`, zone add/remove, `run_duration_min`. Always skipped: zone-level fields (ISE-blocked) and portal-only program fields (`name`, `day_pattern`, `program_type`, `ignore_rain_sensor`). Never calls run/stop/suspend/resume. |
| `zone_analysis` | Per-zone runtime analysis |

### Water & Efficiency

| Tool | Description |
|------|-------------|
| `water_usage_report` | Water usage and cost history |
| `et_recommendations` | ET% reduction recommendations |

### Calculators

| Tool | Description |
|------|-------------|
| `coverage_calculator` | Bags/units needed for yard |
| `mix_calculator` | Concentrate per tank load |

## Project Structure

```
lawnops/
├── __init__.py            # Version + top-level re-exports
├── __main__.py            # python -m lawnops
├── config.py              # Layered config loader (config.json → env vars)
├── weather.py             # Open-Meteo API + daily aggregation
├── advisory.py            # Pre-emergent and spray advisory logic (mow-buffer aware)
├── mowing.py              # Mowing schedule, no-mow buffer calculations
├── window.py              # Application window scoring (spray/granular/pre-emergent)
├── coverage.py            # Bag/coverage calculator
├── mixrate.py             # Spray concentrate mix rate calculator
├── irrigation.py          # Hydrawise (async internals, sync API)
├── irrigation_config.py   # Declarative config export: serialize live controller state to YAML
├── db/
│   ├── connection.py      # SQLite connection helpers
│   ├── schema.py          # Schema + init
│   ├── observations.py    # Daily weather logging
│   ├── treatments.py      # Treatment CRUD
│   ├── products.py        # Product inventory + add
│   ├── purchases.py       # Purchase tracking
│   ├── mowing.py          # Mowing visits
│   ├── equipment.py       # Equipment inventory
│   ├── irrigation_log.py  # Irrigation run logging + Hydrawise sync
│   ├── reports.py         # Spending reports
│   ├── alerts.py          # Reorder alerts (zero-stock detection)
│   ├── obsidian_import.py # Obsidian vault import
│   └── ynab_import.py     # YNAB spending import
└── cli/
    ├── main.py            # Argparse + command routing
    └── display.py         # Terminal formatting
```

## License

MIT
