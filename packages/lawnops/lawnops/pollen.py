"""Pollen count data from Atlanta Allergy & Asthma (NAB station)."""

import logging

import requests

logger = logging.getLogger(__name__)

_DEFAULT_URL = "https://www.atlantaallergy.com/pollen_counts"


def fetch_pollen(config):
    """Fetch today's pollen count from Atlanta Allergy & Asthma.

    Returns a dict with keys:
        date: str (YYYY-MM-DD)
        total_count: int
        category: str ("Low" | "Medium" | "High" | "Extremely High")
        trees: list[str]
        grasses: list[str]
        weeds: list[str]
        molds: list[str]

    Returns None if data unavailable (off-season, fetch error, parse error).
    """
    url = config.get("pollen", {}).get("source_url", _DEFAULT_URL)

    try:
        from bs4 import BeautifulSoup  # noqa: F401  # availability gate; parser imports its own
    except ImportError:
        logger.warning("beautifulsoup4 not installed - pollen data unavailable")
        return None

    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "lawnops/1.0 (personal lawn care tool)",
            },
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to fetch pollen data: %s", e)
        return None

    try:
        return _parse_pollen_page(resp.text)
    except Exception as e:
        logger.warning("Failed to parse pollen data: %s", e)
        return None


def _parse_pollen_page(html):
    """Parse pollen count page HTML from Atlanta Allergy & Asthma.

    Page structure:
    - div.widget-pollen-count-full contains everything
    - div.pollen-text has: "Total Pollen Count for MM/DD/YYYY: NNN"
    - div.gauge elements for Trees, Grass, Weeds (with contributors listed)
    - Mold Activity section has gauge-segments with Low/Moderate/High/Extremely High

    Returns dict or None.
    """
    import re

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # Find the pollen widget
    widget = soup.find("div", class_="widget-pollen-count-full")
    if not widget:
        return None

    pollen_text_div = widget.find("div", class_="pollen-text")
    if not pollen_text_div:
        return None

    text = pollen_text_div.get_text()

    # Extract date - format "MM/DD/YYYY" or "YYYY-MM-DD"
    date_str = None
    date_match = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if date_match:
        month, day, year = date_match.groups()
        date_str = f"{year}-{month}-{day}"
    else:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if date_match:
            date_str = date_match.group(1)

    if not date_str:
        return None

    # Extract total count - number after the date/colon
    total_count = None
    count_match = re.search(r"Count[^:]*:\s*(\d+)", text)
    if count_match:
        total_count = int(count_match.group(1))
    else:
        # Fallback: find standalone number in the pollen-text div
        for el in pollen_text_div.find_all(True):
            el_text = el.get_text(strip=True)
            if re.fullmatch(r"\d{1,5}", el_text):
                total_count = int(el_text)
                break

    if total_count is None:
        return None

    # Derive category from the total count
    category = _count_to_category(total_count)

    # Extract contributors from gauge divs
    gauges = widget.find_all("div", class_="gauge")
    trees = []
    grasses = []
    weeds = []
    molds = []

    for gauge in gauges:
        gauge_text = gauge.get_text()
        # Each gauge has: "Category (Top Contributors)\nTYPE1, TYPE2\xa0\nL=..."
        if re.search(r"Trees?\s*\(", gauge_text, re.IGNORECASE):
            trees = _extract_contributors(gauge_text)
        elif re.search(r"Grass", gauge_text, re.IGNORECASE):
            grasses = _extract_contributors(gauge_text)
        elif re.search(r"Weeds?\s*\(", gauge_text, re.IGNORECASE):
            weeds = _extract_contributors(gauge_text)
        elif re.search(r"Mold", gauge_text, re.IGNORECASE):
            molds = _extract_contributors(gauge_text)

    return {
        "date": date_str,
        "total_count": total_count,
        "category": category,
        "trees": trees,
        "grasses": grasses,
        "weeds": weeds,
        "molds": molds,
    }


def _extract_contributors(gauge_text):
    """Extract contributor names from a gauge div's text.

    Gauge text format: "Trees (Top Contributors)\nOAK, PINE, SWEET GUM\xa0\n\nL=0-14..."
    or: "Grass\nGRASS\xa0\n\nL=0-4..."
    """
    import re

    # Remove scale legend (L=..., M=..., H=..., E=...)
    cleaned = re.split(r"[LMHE]\s*=\s*\d", gauge_text)[0]
    # Split into lines and remove the first line (category header)
    lines = [line.strip().replace("\xa0", "") for line in cleaned.split("\n")]
    # First non-empty line is the header (e.g. "Trees (Top Contributors)"), skip it
    content_lines = []
    found_header = False
    for line in lines:
        if not line:
            continue
        if not found_header:
            found_header = True
            continue
        content_lines.append(line)

    if not content_lines:
        return []

    # Join remaining lines and split on commas
    contributor_text = ", ".join(content_lines)
    types = [t.strip().title() for t in contributor_text.split(",") if t.strip()]
    # Filter junk
    types = [t for t in types if t and len(t) < 40 and not re.fullmatch(r"\d+", t)]
    return types


def _count_to_category(count):
    """Map pollen count to category per Atlanta Allergy scale."""
    if count >= 1500:
        return "Extremely High"
    elif count >= 90:
        return "High"
    elif count >= 30:
        return "Medium"
    else:
        return "Low"


def pollen_spray_impact(count, config=None):
    """Assess pollen impact on spray operations.

    Returns a dict with keys:
        impact: str ("none" | "low" | "moderate" | "high")
        note: str
    """
    if count is None:
        return {"impact": "unknown", "note": "Pollen data unavailable"}

    pollen_cfg = (config or {}).get("pollen", {})
    impact_threshold = pollen_cfg.get("spray_impact_threshold", 500)
    delay_threshold = pollen_cfg.get("spray_delay_threshold", 1500)

    if count >= delay_threshold:
        return {
            "impact": "high",
            "note": (
                f"Extreme pollen ({count}) - heavy leaf coating reduces herbicide contact. "
                "Consider delaying spray 24-48h or spray early AM before pollen settles."
            ),
        }
    elif count >= impact_threshold:
        return {
            "impact": "moderate",
            "note": (f"Elevated pollen ({count}) - may reduce spray contact. Spray early AM for best results."),
        }
    elif count >= 90:
        return {
            "impact": "low",
            "note": f"Pollen present ({count}) - minimal impact on spray efficacy.",
        }
    else:
        return {
            "impact": "none",
            "note": f"Low pollen ({count}) - no impact on spray operations.",
        }


def get_pollen_trend_analysis(history):
    """Analyze pollen trend from historical data.

    Args:
        history: List of dicts with 'date' and 'total_count' keys, ordered by date.

    Returns dict with keys:
        avg: float
        direction: str ("rising" | "falling" | "stable")
        peak_date: str
        peak_count: int
    """
    if not history:
        return None

    counts = [h["total_count"] for h in history]
    avg = sum(counts) / len(counts)
    peak_idx = counts.index(max(counts))
    peak = history[peak_idx]

    # Trend: compare last 3 to first 3
    if len(counts) >= 6:
        recent = sum(counts[-3:]) / 3
        earlier = sum(counts[:3]) / 3
        diff = recent - earlier
        if diff > 50:
            direction = "rising"
        elif diff < -50:
            direction = "falling"
        else:
            direction = "stable"
    elif len(counts) >= 2:
        if counts[-1] > counts[0] * 1.2:
            direction = "rising"
        elif counts[-1] < counts[0] * 0.8:
            direction = "falling"
        else:
            direction = "stable"
    else:
        direction = "stable"

    return {
        "avg": round(avg, 0),
        "direction": direction,
        "peak_date": peak["date"],
        "peak_count": peak["total_count"],
    }
