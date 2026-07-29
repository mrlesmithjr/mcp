"""Airline ancillary fee estimates for all-in price calculations.

Fees are per-person estimates based on publicly published rates as of 2026.
All figures assume economy class, domestic US or short-haul international,
pre-paid online (airport fees are higher). Ranges use the midpoint.

Sources: airline websites, DoT fee disclosures.
"""

# Fee structure per airline.
# carry_on:      cost per person (0 = included in base fare)
# checked_1:     first checked bag per person
# checked_2:     second checked bag per person
# seat_basic:    lowest assigned seat fee (0 = free assignment at checkin)
# notes:         caveats that affect real cost

AIRLINE_FEES: dict[str, dict] = {
    "WestJet": {
        "carry_on": 0,
        "checked_1": 38,
        "checked_2": 53,
        "seat_basic": 0,
        "notes": "Basic fare (Econo) includes carry-on. Checked bags extra on all but Plus/Business fares. When booked as a Delta codeshare on a transborder (US-Canada) itinerary, Delta's bag policy applies to the whole ticket: expect ~$55/bag for the transborder leg vs $38 standalone.",
    },
    "Frontier": {
        "carry_on": 59,
        "checked_1": 69,
        "checked_2": 79,
        "seat_basic": 20,
        "notes": "Personal item only included. Carry-on and checked bags both extra. Seat selection required to avoid random assignment. Bundle deals can reduce total; check at booking.",
    },
    "Delta": {
        "carry_on": 0,
        "checked_1": 45,
        "checked_2": 55,
        "seat_basic": 0,
        "notes": "Carry-on included. Basic Economy restricts carry-on to personal item only (verify fare class). First checked bag free with Delta SkyMiles Amex card. Multi-city bookings (e.g. ATL-YVR + LAX-ATL) are priced as a bundle on delta.com and are significantly cheaper than two separate one-ways. Book directly at delta.com as a multi-city trip.",
    },
    "American": {
        "carry_on": 0,
        "checked_1": 45,
        "checked_2": 55,
        "seat_basic": 0,
        "notes": "Carry-on included on main cabin and above. Basic Economy = personal item only. AAdvantage card holders get first checked bag free.",
    },
    "United": {
        "carry_on": 0,
        "checked_1": 45,
        "checked_2": 55,
        "seat_basic": 0,
        "notes": "Carry-on included on Economy and above. Basic Economy = personal item only. MileagePlus card holders get first checked bag free.",
    },
    "Air Canada": {
        "carry_on": 0,
        "checked_1": 35,
        "checked_2": 55,
        "seat_basic": 0,
        "notes": "Carry-on included. Checked bags extra on Basic fares. Prices in USD equivalent (actual charge in CAD).",
    },
    "Southwest": {
        "carry_on": 0,
        "checked_1": 0,
        "checked_2": 0,
        "seat_basic": 0,
        "notes": "Two checked bags always included. No seat selection fees; open seating.",
    },
    "Spirit": {
        "carry_on": 59,
        "checked_1": 69,
        "checked_2": 79,
        "seat_basic": 20,
        "notes": "Personal item only included. Similar fee structure to Frontier.",
    },
    "Alaska": {
        "carry_on": 0,
        "checked_1": 35,
        "checked_2": 45,
        "seat_basic": 0,
        "notes": "Carry-on included. Alaska card holders get first checked bag free.",
    },
}

# Default fee structure for unknown airlines
_UNKNOWN_FEES = {
    "carry_on": 0,
    "checked_1": 35,
    "checked_2": 45,
    "seat_basic": 0,
    "notes": "Unknown airline; using major-carrier estimates.",
}


def get_fees(airline: str | None) -> dict:
    """Return fee structure for an airline, matching by substring."""
    if not airline:
        return _UNKNOWN_FEES
    airline_lower = airline.lower()
    for name, fees in AIRLINE_FEES.items():
        if name.lower() in airline_lower or airline_lower in name.lower():
            return {**fees, "airline": name}
    return {**_UNKNOWN_FEES, "airline": airline}


def estimate_all_in(
    price_per_person: float,
    airline: str | None,
    passengers: int,
    checked_bags: int,
    include_carry_on: bool = True,
    include_seat: bool = False,
) -> dict:
    """Calculate estimated all-in cost per person and total.

    Returns dict with per_person, total, breakdown, and notes.
    """
    fees = get_fees(airline)

    extras_pp = 0
    breakdown = []

    if include_carry_on and fees["carry_on"] > 0:
        extras_pp += fees["carry_on"]
        breakdown.append(f"carry-on ${fees['carry_on']}")

    for i in range(checked_bags):
        bag_cost = fees["checked_1"] if i == 0 else fees["checked_2"]
        if bag_cost > 0:
            extras_pp += bag_cost
            breakdown.append(f"bag {i + 1} ${bag_cost}")

    if include_seat and fees["seat_basic"] > 0:
        extras_pp += fees["seat_basic"]
        breakdown.append(f"seat ${fees['seat_basic']}")

    all_in_pp = price_per_person + extras_pp
    all_in_total = all_in_pp * passengers

    return {
        "base_per_person": price_per_person,
        "extras_per_person": extras_pp,
        "all_in_per_person": all_in_pp,
        "all_in_total": all_in_total,
        "breakdown": breakdown,
        "notes": fees.get("notes", ""),
    }
