"""One-time import of data from Obsidian Task List.md into the database."""

import os
import re
from datetime import datetime

from lawnops.db.connection import get_db


def import_from_obsidian(config):
    """Import treatment history, products, equipment, observations, and purchases
    from the Obsidian Task List.md.

    Returns a dict of {table_name: row_count} for each imported table.
    Raises RuntimeError if DB not initialized or task list not found.
    """
    conn = get_db(config)
    task_list_path = config.get("obsidian", {}).get("task_list", "")
    if not task_list_path or not os.path.exists(task_list_path):
        raise RuntimeError(f"Task List not found at {task_list_path}")

    with open(task_list_path) as f:
        content = f.read()

    counts = {"treatments": 0, "products": 0, "equipment": 0, "purchases": 0, "observations": 0, "seasonal_tasks": 0}

    # ── Treatment History ──
    treatment_section = re.search(r"## Treatment History\s*\n\|.*\n\|[-\s|]+\n((?:\|.*\n)*)", content)
    if treatment_section:
        for line in treatment_section.group(1).strip().split("\n"):
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 5:
                date, area, product, method, notes = cols[0], cols[1], cols[2], cols[3], cols[4]
                soil_temp = None
                temp_match = re.search(r"~?(\d+)°F", notes)
                if temp_match and "soil" in notes.lower():
                    soil_temp = float(temp_match.group(1))
                cost = None
                cost_match = re.search(r"\$(\d+\.?\d*)", notes)
                if cost_match:
                    cost = float(cost_match.group(1))
                conn.execute(
                    """
                    INSERT OR IGNORE INTO treatments (date, treatment_area, product, method, soil_temp_f, cost, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (date, area, product, method, soil_temp, cost, notes),
                )
                counts["treatments"] += 1

    # ── Product Inventory ──
    product_section = re.search(r"### Current Stock.*?\n\s*\n?\|.*\n\|[-\s|]+\n((?:\|.*\n)*)", content)
    if product_section:
        for line in product_section.group(1).strip().split("\n"):
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 5:
                name, qty_str, last_ordered, cost_str, source = cols[0], cols[1], cols[2], cols[3], cols[4]
                qty = 0
                qty_match = re.search(r"(\d+)", qty_str)
                if qty_match:
                    qty = float(qty_match.group(1))
                if "empty" in qty_str.lower() or "used" in qty_str.lower():
                    qty = 0
                cost = None
                cost_match = re.search(r"\$(\d+\.?\d*)", cost_str)
                if cost_match:
                    cost = float(cost_match.group(1))
                category = None
                name_lower = name.lower()
                if "pre-emergent" in name_lower or "prodiamine" in name_lower:
                    category = "pre-emergent"
                elif "weed" in name_lower and "feed" in name_lower:
                    category = "fertilizer"
                elif "fertilizer" in name_lower or "no phos" in name_lower:
                    category = "fertilizer"
                elif "winterizer" in name_lower:
                    category = "fertilizer"
                elif "roundup" in name_lower:
                    category = "herbicide"
                elif "weed" in name_lower:
                    category = "herbicide"
                elif "cyonara" in name_lower or "ant" in name_lower:
                    category = "insecticide"
                unit = "bag"
                if "oz" in name_lower or "concentrate" in name_lower:
                    unit = "bottle"
                elif "rtu" in name_lower:
                    unit = "bottle"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO products (name, category, qty_on_hand, unit, last_ordered, cost_each, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (name, category, qty, unit, last_ordered, cost, source),
                )
                counts["products"] += 1

    # ── Equipment ──
    equip_start = content.find("### Equipment Status")
    equip_end = content.find("### General Planning")
    equip_section = content[equip_start:equip_end] if equip_start >= 0 and equip_end > equip_start else ""

    equip_raw_lines = re.findall(r"- \[x\] (.+?)(?:\n|$)", equip_section)

    for raw_line in equip_raw_lines:
        if " – " in raw_line:
            name, details = raw_line.split(" – ", 1)
        else:
            name = raw_line
            details = raw_line

        purchase_date = None
        date_match = re.search(r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s*\d{4})", details)
        if date_match:
            try:
                purchase_date = datetime.strptime(date_match.group(1).replace(",", ""), "%b %d %Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
        if not purchase_date:
            date_match2 = re.search(r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})", details)
            if date_match2:
                try:
                    purchase_date = datetime.strptime(date_match2.group(1), "%b %Y").strftime("%Y-%m-01")
                except ValueError:
                    pass
        cost = None
        cost_match = re.search(r"\$(\d+(?:\.\d+)?)", details)
        if cost_match:
            cost = float(cost_match.group(1))
        source = None
        for store in ["Home Depot", "Amazon", "Lowe's", "Walmart", "Ace Hardware"]:
            if store.lower() in details.lower():
                source = store
                break
        clean_name = re.sub(r"\s*\(\$[\d,.]+.*?\)\s*$", "", name).strip()
        clean_name = re.sub(
            r"\s*\((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}.*?\)\s*$", "", clean_name
        ).strip()
        conn.execute(
            """
            INSERT OR IGNORE INTO equipment (name, purchase_date, cost, source, notes)
            VALUES (?, ?, ?, ?, ?)
        """,
            (clean_name, purchase_date, cost, source, raw_line.strip()),
        )
        counts["equipment"] += 1
        if purchase_date and cost:
            conn.execute(
                """
                INSERT OR IGNORE INTO purchases (date, item, category, cost, source)
                VALUES (?, ?, 'equipment', ?, ?)
            """,
                (purchase_date, clean_name, cost, source),
            )

    # ── Soil Temperature Tracker ──
    soil_section = re.search(r"## Soil Temperature Tracker\s*\n\|.*\n\|[-\s|]+\n((?:\|.*\n)*)", content)
    if soil_section:
        for line in soil_section.group(1).strip().split("\n"):
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 3:
                date = cols[0]
                temp_match = re.search(r"~?(\d+)", cols[1])
                soil_temp = float(temp_match.group(1)) if temp_match else None
                if soil_temp:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO daily_observations (date, soil_avg_f, source)
                        VALUES (?, ?, 'manual')
                    """,
                        (date, soil_temp),
                    )
                    counts["observations"] += 1

    # ── Weather Tracker ──
    weather_section = re.search(r"### Weather Tracker\s*\n\|.*\n\|[-\s|]+\n((?:\|.*\n)*)", content)
    if weather_section:
        for line in weather_section.group(1).strip().split("\n"):
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 4:
                date = cols[0]
                temp_match = re.search(r"(\d+)°F\s*/\s*(\d+)°F", cols[2])
                if temp_match:
                    air_max = float(temp_match.group(1))
                    air_min = float(temp_match.group(2))
                    conn.execute(
                        """
                        INSERT INTO daily_observations (date, air_max_f, air_min_f, source)
                        VALUES (?, ?, ?, 'manual')
                        ON CONFLICT(date) DO UPDATE SET
                            air_max_f=excluded.air_max_f, air_min_f=excluded.air_min_f
                    """,
                        (date, air_max, air_min),
                    )
                    counts["observations"] += 1

    # ── 2025 Spending ──
    spending_section = re.search(r"### 2025 Full-Year Product Spending\s*\n\|.*\n\|[-\s|]+\n((?:\|.*\n)*)", content)
    if spending_section:
        for line in spending_section.group(1).strip().split("\n"):
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 4 and "Total" not in cols[0]:
                item, date_str, cost_str, source = cols[0], cols[1], cols[2], cols[3]
                cost_match = re.search(r"~?\$?(\d+\.?\d*)", cost_str)
                cost = float(cost_match.group(1)) if cost_match else None
                date = None
                date_match = re.search(r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2})", date_str)
                if date_match:
                    try:
                        date = datetime.strptime(date_match.group(1) + " 2025", "%b %d %Y").strftime("%Y-%m-%d")
                    except ValueError:
                        pass
                category = "product"
                if "spreader" in item.lower() or "sprayer" in item.lower():
                    category = "equipment"
                if date and cost:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO purchases (date, item, category, cost, source)
                        VALUES (?, ?, ?, ?, ?)
                    """,
                        (date, item, category, cost, source),
                    )
                    counts["purchases"] += 1

    # ── Yearly Tasks ──
    yearly_section = re.search(r"### Yearly Tasks.*?\n\s*\n?\|.*\n\|[-\s|]+\n((?:\|.*\n)*)", content)
    if yearly_section:
        for line in yearly_section.group(1).strip().split("\n"):
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 3:
                season, task_name, timing = cols[0], cols[1], cols[2]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO seasonal_tasks (season, task_name, timing)
                    VALUES (?, ?, ?)
                """,
                    (season, task_name, timing),
                )
                counts["seasonal_tasks"] += 1

    conn.commit()
    conn.close()
    return counts
