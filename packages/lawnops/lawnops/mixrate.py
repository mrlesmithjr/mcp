"""Mix rate calculator - concentrate amounts for spray products."""

import math

from lawnops.coverage import _fuzzy_match_product


def calculate_mix(product_name, tank_gallons, config, rate_type=None):
    """Calculate spray mix rate for a product.

    Args:
        product_name: Product name (fuzzy matched against product_rates).
        tank_gallons: Tank capacity in gallons.
        config: Full config dict.
        rate_type: Rate type key (e.g. "southern", "northern"). Defaults to "southern".

    Returns a dict with: product, rate_type, tank_gallons, oz_per_gallon,
        concentrate_oz, coverage_per_tank, yard_sqft, tanks_for_yard, notes.

    Raises RuntimeError if product is not spray type or not found.
    """
    product_rates = config.get("product_rates", {})
    matched_name, product_cfg = _fuzzy_match_product(product_name, product_rates)

    if product_cfg.get("type") != "spray":
        raise RuntimeError(
            f"'{matched_name}' is a {product_cfg.get('type', 'unknown')} product, "
            f"not a spray product. Use 'lawnops coverage' instead."
        )

    rates = product_cfg.get("concentrate_oz_per_gallon", {})
    if not rates:
        raise RuntimeError(f"No concentrate_oz_per_gallon defined for '{matched_name}'.")

    if rate_type is None:
        rate_type = "southern"

    if rate_type not in rates:
        available = ", ".join(rates.keys())
        raise RuntimeError(f"Rate type '{rate_type}' not found for '{matched_name}'. Available: {available}")

    oz_per_gallon = rates[rate_type]
    concentrate_oz = oz_per_gallon * tank_gallons

    coverage_per_gal = product_cfg.get("coverage_sqft_per_gallon", 0)
    coverage_per_tank = int(coverage_per_gal * tank_gallons)

    yard_sqft = config.get("location", {}).get("yard_sqft", 0)
    if yard_sqft > 0 and coverage_per_tank > 0:
        tanks_for_yard = math.ceil(yard_sqft / coverage_per_tank)
    else:
        tanks_for_yard = 0

    return {
        "product": matched_name,
        "rate_type": rate_type,
        "tank_gallons": tank_gallons,
        "oz_per_gallon": oz_per_gallon,
        "concentrate_oz": concentrate_oz,
        "coverage_per_tank": coverage_per_tank,
        "yard_sqft": yard_sqft,
        "tanks_for_yard": tanks_for_yard,
        "notes": product_cfg.get("notes", ""),
    }
