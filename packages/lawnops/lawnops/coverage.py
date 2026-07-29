"""Coverage calculator - determine bags/product needed for yard area."""

import math


def _fuzzy_match_product(product_name, product_rates):
    """Fuzzy match a product name against product_rates keys.

    Returns (matched_key, product_config) or raises RuntimeError.
    """
    if not product_rates:
        raise RuntimeError("No product_rates configured. Add product_rates to config.yaml.")

    search = product_name.lower().strip()

    # Exact match first
    if search in product_rates:
        return search, product_rates[search]

    # Substring match
    matches = []
    for key in product_rates:
        if search in key or key in search:
            matches.append((key, product_rates[key]))

    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        names = ", ".join(m[0] for m in matches)
        raise RuntimeError(f"Ambiguous product match for '{product_name}': {names}")

    # Word overlap match
    search_words = set(search.split())
    for key in product_rates:
        key_words = set(key.lower().split())
        if search_words & key_words:
            matches.append((key, product_rates[key]))

    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        names = ", ".join(m[0] for m in matches)
        raise RuntimeError(f"Ambiguous product match for '{product_name}': {names}")

    available = ", ".join(product_rates.keys())
    raise RuntimeError(f"No product match for '{product_name}'. Available: {available}")


def calculate_coverage(product_name, config, sqft_override=None):
    """Calculate bags/product needed for yard coverage.

    Args:
        product_name: Product name (fuzzy matched against product_rates).
        config: Full config dict.
        sqft_override: Override yard sq ft from config.

    Returns a dict with: product, type, yard_sqft, bag_size_lbs,
        bag_coverage_sqft, bags_needed, surplus_sqft, notes.

    Raises RuntimeError for spray-type products or if product not found.
    """
    product_rates = config.get("product_rates", {})
    matched_name, product_cfg = _fuzzy_match_product(product_name, product_rates)

    yard_sqft = sqft_override or config.get("location", {}).get("yard_sqft")
    if not yard_sqft:
        raise RuntimeError("No yard_sqft configured. Set location.yard_sqft or use --sqft.")

    product_type = product_cfg.get("type", "granular")

    if product_type == "spray":
        # For spray products, calculate gallons needed
        coverage_per_gal = product_cfg.get("coverage_sqft_per_gallon", 0)
        if coverage_per_gal <= 0:
            raise RuntimeError(f"No coverage_sqft_per_gallon for '{matched_name}'.")
        gallons_needed = math.ceil(yard_sqft / coverage_per_gal)
        surplus_sqft = (gallons_needed * coverage_per_gal) - yard_sqft

        return {
            "product": matched_name,
            "type": product_type,
            "yard_sqft": yard_sqft,
            "unit": "gallon",
            "unit_coverage_sqft": coverage_per_gal,
            "units_needed": gallons_needed,
            "surplus_sqft": surplus_sqft,
            "notes": product_cfg.get("notes", ""),
        }

    # Granular products
    bag_coverage = product_cfg.get("bag_coverage_sqft", 0)
    bag_size = product_cfg.get("bag_size_lbs", 0)

    if bag_coverage <= 0:
        raise RuntimeError(f"No bag_coverage_sqft for '{matched_name}'.")

    bags_needed = math.ceil(yard_sqft / bag_coverage)
    surplus_sqft = (bags_needed * bag_coverage) - yard_sqft

    return {
        "product": matched_name,
        "type": product_type,
        "yard_sqft": yard_sqft,
        "unit": "bag",
        "bag_size_lbs": bag_size,
        "unit_coverage_sqft": bag_coverage,
        "units_needed": bags_needed,
        "surplus_sqft": surplus_sqft,
        "notes": product_cfg.get("notes", ""),
    }
