"""Fertilizer recommendation engine - Bermuda grass seasonal calendar.

Maps current month, soil temperature, and treatment history to actionable
fertilizer and lawn-care recommendations.
"""

from datetime import date, datetime

from lawnops.db.connection import get_db

# ── Bermuda Grass Seasonal Calendar ─────────────────────────────────────
# Each phase defines: month range, soil temp range, recommended actions,
# and product categories with suggested N-P-K focus.

BERMUDA_CALENDAR = [
    {
        "phase": "Dormant",
        "months": (12, 1, 2),
        "soil_temp": (None, 55),
        "summary": "Bermuda is dormant. No fertilizer needed.",
        "actions": [
            "Do NOT fertilize - grass cannot absorb nutrients while dormant",
            "Good time for a soil test to plan spring inputs",
            "Address bare spots with winter overseeding (ryegrass) if desired",
            "Plan spring pre-emergent timing - watch soil temps in late Feb",
        ],
        "products": [],
    },
    {
        "phase": "Green-Up",
        "months": (3, 4),
        "soil_temp": (55, 65),
        "summary": "Bermuda is waking up. Focus on pre-emergent and light feeding.",
        "actions": [
            "Apply pre-emergent before soil hits 55\u00b0F for 3+ consecutive days",
            "Wait for 50%+ green-up before any nitrogen application",
            "If greening: light nitrogen (0.5 lb N/1000 sq ft) to boost recovery",
            "Post-emergent spot-spray broadleaf weeds on warm days (60\u00b0F+)",
        ],
        "products": [
            {"category": "pre-emergent", "priority": "high", "note": "Apply before crabgrass germination"},
            {
                "category": "fertilizer",
                "npk_focus": "starter or balanced",
                "priority": "medium",
                "note": "Light N only after 50% green-up (0.5 lb N/1000 sq ft)",
            },
        ],
    },
    {
        "phase": "Active Growth",
        "months": (5, 6),
        "soil_temp": (65, 80),
        "summary": "Bermuda is actively growing. Prime time for full nitrogen feeding.",
        "actions": [
            "Apply 1 lb N per 1000 sq ft - Bermuda's peak uptake period",
            "Post-emergent herbicide for any breakthrough weeds",
            "Second pre-emergent application if using split-app strategy",
            "Begin regular mowing at 1\u20131.5 inches for thick turf",
        ],
        "products": [
            {
                "category": "fertilizer",
                "npk_focus": "high-N (e.g. 24-0-11, 18-0-9)",
                "priority": "high",
                "note": "Full rate nitrogen - Bermuda is hungry now",
            },
            {"category": "post-emergent", "priority": "medium", "note": "Spot-treat weeds before summer heat"},
            {"category": "insecticide", "priority": "low", "note": "Preventive grub control if history of damage"},
        ],
    },
    {
        "phase": "Peak Summer",
        "months": (7, 8),
        "soil_temp": (80, None),
        "summary": "Bermuda thrives in heat. Maintain nitrogen, watch for stress.",
        "actions": [
            "Apply 0.5\u20131 lb N per 1000 sq ft to sustain color and density",
            "Monitor for chinch bugs, armyworms, and grubs",
            "Irrigate deeply but infrequently (1\u20131.5 inches/week total)",
            "Raise mowing height slightly if drought-stressed",
            "Avoid herbicide applications above 90\u00b0F air temp",
        ],
        "products": [
            {
                "category": "fertilizer",
                "npk_focus": "balanced or high-N (e.g. 24-0-11)",
                "priority": "high",
                "note": "Sustain summer growth - reduce rate if drought-stressed",
            },
            {
                "category": "insecticide",
                "priority": "medium",
                "note": "Treat grubs/armyworms if signs of damage appear",
            },
        ],
    },
    {
        "phase": "Fall Prep",
        "months": (9, 10),
        "soil_temp": (65, 80),
        "summary": "Bermuda slowing down. Shift to potassium for winter hardiness.",
        "actions": [
            "Switch to potassium-heavy fertilizer (e.g. 0-0-7 or low-N/high-K)",
            "Potassium strengthens cell walls for freeze resistance",
            "Last chance for post-emergent weed control before dormancy",
            "Reduce mowing frequency as growth slows",
            "NO heavy nitrogen - forces tender growth vulnerable to frost",
        ],
        "products": [
            {
                "category": "fertilizer",
                "npk_focus": "high-K, low-N (e.g. 0-0-7, 5-0-20)",
                "priority": "high",
                "note": "Potassium for winter hardiness - avoid heavy nitrogen",
            },
            {"category": "post-emergent", "priority": "low", "note": "Last spray window before Bermuda goes dormant"},
        ],
    },
    {
        "phase": "Pre-Dormancy",
        "months": (11,),
        "soil_temp": (55, 65),
        "summary": "Bermuda entering dormancy. Stop fertilizing.",
        "actions": [
            "Do NOT apply nitrogen - Bermuda cannot use it and it may leach",
            "Final mow at normal height before dormancy",
            "Clean up leaves to prevent smothering",
            "Plan soil test for late winter",
        ],
        "products": [],
    },
]


def _get_phase(month, soil_temp):
    """Determine current seasonal phase based on month and soil temperature.

    Uses month as primary selector, then refines with soil temp if it
    suggests an earlier or later phase than the calendar month implies.
    """
    # Find phase by month
    for phase in BERMUDA_CALENDAR:
        if month in phase["months"]:
            return phase

    # Fallback (shouldn't happen with complete calendar)
    return BERMUDA_CALENDAR[0]


def _get_last_treatments(config, categories=None, limit=5):
    """Get recent treatments, optionally filtered by product category keywords.

    Returns list of dicts with date, product, area keys.
    """
    conn = get_db(config)
    rows = conn.execute("""
        SELECT date, product, treatment_area
        FROM treatments
        ORDER BY date DESC
        LIMIT 20
    """).fetchall()
    conn.close()

    results = []
    for r in rows:
        if categories:
            product_lower = r["product"].lower()
            if not any(cat.lower() in product_lower for cat in categories):
                continue
        results.append(
            {
                "date": r["date"],
                "product": r["product"],
                "area": r["treatment_area"],
            }
        )
        if len(results) >= limit:
            break

    return results


def _get_inventory_status(config, product_categories):
    """Check product inventory for relevant categories.

    Returns list of dicts with name, category, qty, unit keys.
    """
    conn = get_db(config)
    rows = conn.execute("""
        SELECT name, category, qty_on_hand, unit
        FROM products
        ORDER BY category, name
    """).fetchall()
    conn.close()

    results = []
    for r in rows:
        cat = (r["category"] or "").lower()
        name = r["name"].lower()
        for pc in product_categories:
            pc_lower = pc.lower()
            if pc_lower in cat or pc_lower in name:
                results.append(
                    {
                        "name": r["name"],
                        "category": r["category"],
                        "qty": r["qty_on_hand"],
                        "unit": r["unit"],
                    }
                )
                break

    return results


def _days_since_last(treatments):
    """Calculate days since the most recent treatment in the list."""
    if not treatments:
        return None
    last_date = treatments[0]["date"]
    try:
        last = datetime.strptime(last_date, "%Y-%m-%d").date()
        return (date.today() - last).days
    except (ValueError, TypeError):
        return None


def get_recommendation(config, daily_data):
    """Generate fertilizer recommendation based on current conditions.

    Args:
        config: Loaded config dict.
        daily_data: Aggregated daily weather data from weather module.

    Returns:
        dict with keys: phase, summary, actions, products, soil_temp,
        month, grass_type, last_fertilizer, last_pre_emergent,
        inventory, days_since_fertilizer, days_since_pre_emergent.
    """
    today = date.today()
    month = today.month
    today_str = today.isoformat()

    grass_type = config.get("location", {}).get("grass_type", "bermuda")

    # Get current soil temp from most recent historical day
    historical = [d for d in daily_data if d["date"] <= today_str]
    soil_temp = historical[-1]["soil_avg"] if historical else None
    soil_max = historical[-1]["soil_max"] if historical else None

    # Determine seasonal phase
    phase = _get_phase(month, soil_temp)

    # Check treatment history
    fert_keywords = [
        "fertilizer",
        "fert",
        "18-0-9",
        "24-0-11",
        "0-0-7",
        "nitrogen",
        "potassium",
        "weed & feed",
        "weed and feed",
        "milorganite",
        "lesco",
    ]
    pre_em_keywords = ["pre-emergent", "prodiamine", "barricade", "dimension", "dithiopyr"]

    last_fertilizer = _get_last_treatments(config, fert_keywords, limit=3)
    last_pre_emergent = _get_last_treatments(config, pre_em_keywords, limit=2)

    days_since_fert = _days_since_last(last_fertilizer)
    days_since_pre = _days_since_last(last_pre_emergent)

    # Check inventory for recommended product categories
    product_cats = [p["category"] for p in phase["products"]]
    inventory = _get_inventory_status(config, product_cats) if product_cats else []

    # Build context-aware notes
    context_notes = []

    if soil_temp is not None:
        lo, hi = phase["soil_temp"]
        if lo is not None and soil_temp < lo:
            context_notes.append(
                f"Soil temp ({soil_temp:.1f}\u00b0F) is below typical range for "
                f"this phase ({lo}\u00b0F+) \u2014 conditions may be running behind schedule"
            )
        elif hi is not None and soil_temp > hi:
            context_notes.append(
                f"Soil temp ({soil_temp:.1f}\u00b0F) is above typical range for "
                f"this phase (\u2264{hi}\u00b0F) \u2014 conditions may be ahead of schedule"
            )

    if days_since_fert is not None:
        if days_since_fert < 21:
            context_notes.append(
                f"Last fertilizer was {days_since_fert} days ago "
                f"({last_fertilizer[0]['product']}) \u2014 too soon to reapply"
            )
        elif days_since_fert > 45 and month in (5, 6, 7, 8):
            context_notes.append(
                f"Last fertilizer was {days_since_fert} days ago \u2014 Bermuda may be due for another application"
            )

    # Inventory warnings
    for p in phase["products"]:
        cat = p["category"]
        matching = [
            i for i in inventory if cat.lower() in (i["category"] or "").lower() or cat.lower() in i["name"].lower()
        ]
        in_stock = [i for i in matching if i["qty"] > 0]
        if matching and not in_stock:
            context_notes.append(f"No {cat} products in stock \u2014 purchase needed before application")

    return {
        "phase": phase["phase"],
        "summary": phase["summary"],
        "actions": phase["actions"],
        "products": phase["products"],
        "soil_temp": soil_temp,
        "soil_max": soil_max,
        "month": month,
        "month_name": today.strftime("%B"),
        "grass_type": grass_type,
        "last_fertilizer": last_fertilizer,
        "last_pre_emergent": last_pre_emergent,
        "days_since_fertilizer": days_since_fert,
        "days_since_pre_emergent": days_since_pre,
        "inventory": inventory,
        "context_notes": context_notes,
    }
