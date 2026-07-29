"""Seasonal maintenance checklists - pre-built task templates for seasonal home maintenance."""

# Each checklist is a list of task definitions that can be loaded into the
# tasks table. Tasks already in the DB are skipped (matched by name).

SPRING = {
    "name": "Spring",
    "description": "Spring startup and maintenance (March-May)",
    "tasks": [
        {
            "name": "HVAC Filter Change",
            "category": "hvac",
            "interval_days": 90,
            "notes": "Replace all filters. 4-inch MERV 13 for 3-zone system.",
        },
        {
            "name": "Smoke/CO Detector Test",
            "category": "safety",
            "interval_days": 180,
            "notes": "Test all detectors, replace batteries if needed.",
        },
        {
            "name": "Gutter Cleaning",
            "category": "gutters",
            "interval_days": 180,
            "notes": "Your gutter service. Clear pollen, seed pods, winter debris before summer storms.",
        },
        {
            "name": "Perimeter Pest Spray",
            "category": "pest",
            "interval_days": 30,
            "notes": "Cyzmic CS. Full house perimeter.",
        },
        {
            "name": "Interior Pest Spray",
            "category": "pest",
            "interval_days": 30,
            "notes": "Cyzmic CS. Baseboards, window frames, door frames, garage entry.",
        },
        {
            "name": "Exterior Inspection - Spring",
            "category": "exterior",
            "interval_days": 365,
            "notes": "Walk perimeter: check foundation cracks, siding damage, caulking, roof from ground level.",
        },
        {
            "name": "Pressure Washer Maintenance",
            "category": "exterior",
            "interval_days": 365,
            "notes": "Check oil, filters, connections before spring use.",
        },
        {
            "name": "Check Weatherstripping",
            "category": "interior",
            "interval_days": 365,
            "notes": "Inspect all exterior doors. Replace if worn or compressed.",
        },
    ],
}

SUMMER = {
    "name": "Summer",
    "description": "Summer maintenance (June-August)",
    "tasks": [
        {
            "name": "HVAC Drain Line Flush",
            "category": "hvac",
            "interval_days": 180,
            "notes": "Pour vinegar down condensate drain to prevent clogs. High humidity season.",
        },
        {
            "name": "Check Attic Ventilation",
            "category": "hvac",
            "interval_days": 365,
            "notes": "Verify attic vents clear of debris/pests. Check for moisture/mold.",
        },
        {
            "name": "Inspect Exterior Faucets",
            "category": "plumbing",
            "interval_days": 365,
            "notes": "Check for leaks and drips at all exterior hose bibs.",
        },
    ],
}

FALL = {
    "name": "Fall",
    "description": "Fall prep and winterization (September-November)",
    "tasks": [
        {
            "name": "Gutter Cleaning",
            "category": "gutters",
            "interval_days": 180,
            "notes": "Your gutter service. Clear leaves after fall drop. Before winter rain/ice.",
        },
        {
            "name": "Exterior Caulking Check",
            "category": "exterior",
            "interval_days": 365,
            "notes": "Re-caulk gaps around windows, doors, vents, pipes before winter.",
        },
        {
            "name": "Winterize Exterior Faucets",
            "category": "plumbing",
            "interval_days": 365,
            "notes": "Install hose bib covers. Disconnect and drain hoses.",
        },
        {
            "name": "HVAC Annual Maintenance",
            "category": "hvac",
            "interval_days": 365,
            "notes": "Your HVAC service. Schedule before heating season.",
        },
        {
            "name": "Fireplace/Chimney Inspection",
            "category": "safety",
            "interval_days": 365,
            "notes": "Inspect before first use of season. Clean if needed.",
        },
    ],
}

WINTER = {
    "name": "Winter",
    "description": "Winter maintenance (December-February)",
    "tasks": [
        {
            "name": "Check Pipe Insulation",
            "category": "plumbing",
            "interval_days": 365,
            "notes": "Verify insulation on exposed pipes in crawlspace/garage. Freeze prevention.",
        },
        {
            "name": "Test Sump Pump",
            "category": "plumbing",
            "interval_days": 180,
            "notes": "Pour water into pit to verify operation. Check discharge line.",
        },
        {
            "name": "Smoke/CO Detector Battery Replace",
            "category": "safety",
            "interval_days": 365,
            "notes": "Annual battery replacement for all detectors.",
        },
        {
            "name": "Review Home Insurance",
            "category": "interior",
            "interval_days": 365,
            "notes": "Annual policy review. Update coverage for improvements (pool, generator, etc.).",
        },
    ],
}

ALL_CHECKLISTS = {
    "spring": SPRING,
    "summer": SUMMER,
    "fall": FALL,
    "winter": WINTER,
}


def get_checklist(season):
    """Get a seasonal checklist by name."""
    season = season.lower()
    if season not in ALL_CHECKLISTS:
        raise RuntimeError(f"Unknown season '{season}'. Valid: {', '.join(ALL_CHECKLISTS.keys())}")
    return ALL_CHECKLISTS[season]


def load_checklist(config, season):
    """Load a seasonal checklist into the tasks database.

    Skips tasks that already exist (matched by name).
    Returns dict with added/skipped counts.
    """
    from homeops.db.connection import get_db

    checklist = get_checklist(season)
    conn = get_db(config)
    added = []
    skipped = []

    for task in checklist["tasks"]:
        existing = conn.execute(
            "SELECT id FROM tasks WHERE name = ?",
            (task["name"],),
        ).fetchone()

        if existing:
            skipped.append(task["name"])
            continue

        conn.execute(
            "INSERT INTO tasks (name, category, interval_days, notes) VALUES (?, ?, ?, ?)",
            (task["name"], task["category"], task["interval_days"], task.get("notes")),
        )
        added.append(task["name"])

    conn.commit()
    conn.close()

    return {
        "season": checklist["name"],
        "description": checklist["description"],
        "added": added,
        "skipped": skipped,
        "added_count": len(added),
        "skipped_count": len(skipped),
    }
