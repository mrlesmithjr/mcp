"""Flight search via fast-flights (Google Flights, no API key required)."""

import io
import re
import sys
from datetime import datetime


def _get_html_quiet(query) -> str:
    """Fetch Google Flights HTML, suppressing stdout/stderr (primp impersonation warning)."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = io.StringIO()
    try:
        from fast_flights.fetcher import fetch_flights_html

        return fetch_flights_html(query)
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _get_flights_quiet(query):
    """Call get_flights() suppressing stdout/stderr (v3 debug print, primp warning)."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = io.StringIO()
    try:
        from fast_flights import get_flights

        return get_flights(query)
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _parse_price_str(price_str: str) -> float | None:
    try:
        val = float(re.sub(r"[^\d.]", "", price_str))
        return val if val > 0 else None
    except ValueError:
        return None


def _parse_html_css(html: str, passengers: int) -> tuple[list[dict], str]:
    """Parse rendered HTML flight cards (v2-style CSS selectors).

    Returns (results, price_level). Gets 37+ results vs the JS parser's 4,
    correctly includes all airlines such as WestJet on international routes.
    """
    from selectolax.lexbor import LexborHTMLParser

    parser = LexborHTMLParser(html)
    raw = []

    for fl in parser.css('div[jsname="IWWDBc"], div[jsname="YdtKid"]'):
        for item in fl.css("ul.Rk10dc li"):
            name_node = item.css_first("div.sSHqwe.tPgKwe.ogfYpf span")
            name = name_node.text(strip=True) if name_node else ""

            dp_ar = item.css("span.mv1WYe div")
            departure = dp_ar[0].text(strip=True) if len(dp_ar) > 0 else ""
            arrival = dp_ar[1].text(strip=True) if len(dp_ar) > 1 else ""

            dur_node = item.css_first("li div.Ak5kof div")
            duration = dur_node.text(strip=True) if dur_node else ""

            stops_node = item.css_first(".BbR8Ec .ogfYpf")
            stops_text = stops_node.text(strip=True) if stops_node else ""
            try:
                stops = 0 if stops_text == "Nonstop" else int(stops_text.split(" ", 1)[0])
            except (ValueError, IndexError):
                stops = None

            price_node = item.css_first(".YMlIz.FpEdX")
            price_str = price_node.text(strip=True) if price_node else ""
            price_per_person = _parse_price_str(price_str)
            if price_per_person is None:
                continue

            raw.append(
                {
                    "price_total": float(price_per_person * passengers),
                    "price_per_person": float(price_per_person),
                    "currency": "USD",
                    "airline": name or None,
                    "stops": stops,
                    "duration": duration,
                    "departure": departure,
                    "arrival": arrival,
                    "raw": {"price_str": price_str},
                }
            )

    # Google renders each result twice; deduplicate on (airline, departure, price)
    seen: set = set()
    results = []
    for r in raw:
        key = (r["airline"], r["departure"], r["price_per_person"])
        if key not in seen:
            seen.add(key)
            results.append(r)

    level_node = parser.css_first("span.gOatQ")
    price_level_raw = level_node.text(strip=True).lower() if level_node else ""
    price_level = price_level_raw if price_level_raw in ("low", "typical", "high") else "unknown"

    return results, price_level


def _parse_js_data(html: str, passengers: int) -> list[dict]:
    """Parse v3-style embedded JS data object. Fallback when CSS parsing returns nothing."""
    try:
        from fast_flights.parser import parse

        raw = parse(html)
        results = []
        for flight in raw:
            r = _flight_to_result_v3(flight, passengers)
            if r:
                results.append(r)
        return results
    except Exception:
        return []


def _fmt_simpledatetime(sdt) -> str:
    """Format SimpleDatetime(date=(y,m,d), time=(h,min)) to '8:15 AM Sep 24' style."""
    try:
        dt = datetime(*sdt.date, *sdt.time)
        h = dt.strftime("%I").lstrip("0") or "12"
        return f"{h}:{dt.strftime('%M %p %b')} {dt.day}"
    except Exception:
        return ""


def _fmt_duration(minutes: int | None) -> str:
    if minutes is None:
        return ""
    h, m = divmod(int(minutes), 60)
    return f"{h} hr {m} min"


def _flight_to_result_v3(flight, passengers: int) -> dict | None:
    """Convert a v3 Flights object to our standard result dict."""
    try:
        price_per_person = int(flight.price)
        price_total = price_per_person * passengers
        segs = flight.flights
        stops = len(segs) - 1
        duration = _fmt_duration(sum(s.duration for s in segs))
        return {
            "price_total": float(price_total),
            "price_per_person": float(price_per_person),
            "currency": "USD",
            "airline": flight.airlines[0] if flight.airlines else None,
            "stops": stops,
            "duration": duration,
            "departure": _fmt_simpledatetime(segs[0].departure) if segs else "",
            "arrival": _fmt_simpledatetime(segs[-1].arrival) if segs else "",
            "raw": {"airlines": flight.airlines, "price": flight.price},
        }
    except Exception:
        return None


def _make_query(slices: list[dict], trip: str, passengers: int, seat: str):
    from fast_flights import FlightQuery, Passengers, create_query

    return create_query(
        flights=[FlightQuery(date=s["date"], from_airport=s["origin"], to_airport=s["destination"]) for s in slices],
        seat=seat,
        trip=trip,
        passengers=Passengers(adults=passengers),
    )


def search_flights(
    origin: str,
    destination: str,
    travel_date: str,
    passengers: int = 2,
    max_results: int = 10,
    airline_filter: str | None = None,
    nonstop_only: bool = False,
    seat: str = "economy",
) -> tuple[list[dict], str]:
    """Search one-way flights via Google Flights. Returns (results, price_level).

    Uses CSS parsing (v2-style) on the fetched HTML - returns 37+ results including
    all airlines. Falls back to v3 JS data parsing if CSS returns nothing.
    """
    query = _make_query(
        [{"origin": origin.upper(), "destination": destination.upper(), "date": travel_date}],
        "one-way",
        passengers,
        seat,
    )
    html = _get_html_quiet(query)

    raw_results, price_level = _parse_html_css(html, passengers)
    if not raw_results:
        raw_results = _parse_js_data(html, passengers)
        price_level = "unknown"

    from flightops.db import log_search

    log_search(origin, destination, "one_way", passengers, len(raw_results))

    results = []
    for r in raw_results:
        if nonstop_only and r["stops"] != 0:
            continue
        if airline_filter and airline_filter.lower() not in (r["airline"] or "").lower():
            continue
        results.append(r)
    results.sort(key=lambda r: r["price_total"])
    return results[:max_results], price_level


def search_roundtrip(
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: str,
    passengers: int = 2,
    max_results: int = 10,
    nonstop_only: bool = False,
    airline_filter: str | None = None,
    seat: str = "economy",
) -> tuple[list[dict], str]:
    """Search round-trip flights via Google Flights.

    Uses v3 JS data parsing (CSS parsing fails for round-trip; Google requires JS rendering).
    Google Flights returns total-for-all-passengers in round-trip mode; normalized to per-person.
    """
    query = _make_query(
        [
            {"origin": origin.upper(), "destination": destination.upper(), "date": outbound_date},
            {"origin": destination.upper(), "destination": origin.upper(), "date": return_date},
        ],
        "round-trip",
        passengers,
        seat,
    )
    raw = _get_flights_quiet(query)
    from flightops.db import log_search

    log_search(origin, destination, "round_trip", passengers, len(raw))

    results = []
    for flight in raw:
        r = _flight_to_result_v3(flight, passengers)
        if r is None:
            continue
        # Google Flights returns total-for-all-passengers in round-trip mode (confirmed
        # against live data: $1,161 for 3 pax = $387/pp). Divide to normalize to per-person.
        total = float(r["raw"]["price"])
        r["price_total"] = total
        r["price_per_person"] = round(total / passengers, 2)
        if nonstop_only and r["stops"] != 0:
            continue
        if airline_filter and airline_filter.lower() not in (r["airline"] or "").lower():
            continue
        results.append(r)
    results.sort(key=lambda r: r["price_total"])
    return results[:max_results], "unknown"


def search_multi_city(
    legs: list[dict], passengers: int = 2, max_results: int = 5, seat: str = "economy"
) -> tuple[list[dict], str]:
    """Multi-city bundled quotes require JS rendering and are not available.

    Use individual leg searches and check google.com/travel/flights for a bundled quote.
    """
    from flightops.db import log_search

    log_search(legs[0]["origin"], legs[-1]["destination"], "multi_city", passengers, 0)
    return [], "unknown"
