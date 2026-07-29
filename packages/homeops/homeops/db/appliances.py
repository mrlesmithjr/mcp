"""Appliance lifecycle tracking."""

from datetime import date

from homeops.db.connection import get_db


def _parse_date(date_str):
    """Parse YYYY-MM-DD or YYYY-MM to a date object."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        pass
    # Try YYYY-MM format (assume 1st of month)
    try:
        parts = date_str.split("-")
        if len(parts) == 2:
            return date(int(parts[0]), int(parts[1]), 1)
    except (ValueError, IndexError):
        pass
    return None


def add_appliance(
    config,
    name,
    category,
    brand=None,
    model=None,
    purchased=None,
    warranty_end=None,
    expected_lifespan_years=None,
    replacement_cost=None,
    location=None,
    notes=None,
):
    """Add an appliance to the registry."""
    conn = get_db(config)
    conn.execute(
        "INSERT INTO appliances (name, category, brand, model, purchase_date, "
        "warranty_end, expected_lifespan_years, replacement_cost, location, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name,
            category,
            brand,
            model,
            purchased,
            warranty_end,
            expected_lifespan_years,
            replacement_cost,
            location,
            notes,
        ),
    )
    conn.commit()
    conn.close()

    return {"name": name, "category": category, "brand": brand}


def list_appliances(config, category=None):
    """List all appliances with age and warranty status."""
    conn = get_db(config)
    query = "SELECT * FROM appliances"
    params = []
    if category:
        query += " WHERE category = ?"
        params.append(category)
    query += " ORDER BY category, name"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    today = date.today()
    appliances = []
    for row in rows:
        a = dict(row)

        # Compute age
        if a.get("purchase_date"):
            purchased = _parse_date(a["purchase_date"])
            if purchased:
                age_days = (today - purchased).days
                a["age_years"] = round(age_days / 365.25, 1)
            else:
                a["age_years"] = None
        else:
            a["age_years"] = None

        # Warranty status
        if a.get("warranty_end"):
            warranty = _parse_date(a["warranty_end"])
            if warranty:
                a["warranty_active"] = warranty >= today
                a["warranty_days_left"] = (warranty - today).days if warranty >= today else 0
            else:
                a["warranty_active"] = None
                a["warranty_days_left"] = None
        else:
            a["warranty_active"] = None
            a["warranty_days_left"] = None

        # Remaining lifespan estimate
        if a.get("expected_lifespan_years") and a.get("age_years"):
            remaining = a["expected_lifespan_years"] - a["age_years"]
            a["remaining_lifespan_years"] = round(max(0, remaining), 1)
        else:
            a["remaining_lifespan_years"] = None

        appliances.append(a)

    return appliances


def get_expiring_warranties(config, months=12):
    """Get appliances with warranties expiring within N months."""
    appliances = list_appliances(config)
    max_days = months * 30
    return [a for a in appliances if a.get("warranty_active") and a.get("warranty_days_left", 999) <= max_days]


def get_aging_appliances(config, threshold_years=2):
    """Get appliances within N years of expected end of life."""
    appliances = list_appliances(config)
    return [
        a
        for a in appliances
        if a.get("remaining_lifespan_years") is not None and a["remaining_lifespan_years"] <= threshold_years
    ]
