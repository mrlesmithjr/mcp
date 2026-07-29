"""Flight price tracking MCP server: exposes flightops operations as Claude tools."""

import json
import os

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("flightops")

# MCP tool annotations follow the workspace convention in the root CLAUDE.md.
# openWorldHint and destructiveHint spec defaults are True, so closed-world
# and non-destructive tools must set them False explicitly.

# Local SQLite reads: no network, no mutations.
_LOCAL_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

# Live external flight-price searches: read-only but reach Google Flights.
_EXTERNAL_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


@mcp.tool(annotations=_EXTERNAL_READ)
def search_one_way(
    origin: str,
    destination: str,
    date: str,
    passengers: int = 2,
    nonstop_only: bool = False,
    airline_filter: str | None = None,
    seat: str = "economy",
    max_results: int = 10,
) -> str:
    """Search one-way flights on a given date.

    Args:
        origin: Origin airport IATA code (e.g. ATL)
        destination: Destination airport IATA code (e.g. YVR)
        date: Travel date in YYYY-MM-DD format
        passengers: Number of passengers (default 2)
        nonstop_only: Return only nonstop flights
        airline_filter: Case-insensitive substring match on airline name
        seat: Cabin class: economy, premium-economy, business, first
        max_results: Maximum results to return (default 10)

    Returns JSON: {results: [{price_total, price_per_person, currency, airline,
                   stops, duration, departure, arrival}], price_level: str}
    """
    try:
        from flightops.amadeus_client import search_flights

        results, price_level = search_flights(
            origin,
            destination,
            date,
            passengers=passengers,
            max_results=max_results,
            airline_filter=airline_filter,
            nonstop_only=nonstop_only,
            seat=seat,
        )
        return json.dumps({"results": results, "price_level": price_level})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_EXTERNAL_READ)
def search_round_trip(
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: str,
    passengers: int = 2,
    nonstop_only: bool = False,
    airline_filter: str | None = None,
    seat: str = "economy",
    max_results: int = 10,
) -> str:
    """Search round trip flights. Prices are per-person for the full round trip.

    Google Flights returns the total for all passengers in round-trip mode; this tool
    normalizes to per-person so price_per_person * passengers = price_total.

    Args:
        origin: Departure airport IATA code
        destination: Destination airport IATA code
        outbound_date: Outbound travel date YYYY-MM-DD
        return_date: Return travel date YYYY-MM-DD
        passengers: Number of passengers (default 2)
        nonstop_only: Return only nonstop flights
        airline_filter: Case-insensitive airline name filter
        seat: Cabin class: economy, premium-economy, business, first
        max_results: Maximum results to return (default 10)

    Returns JSON: {results: [...], price_level: str}
    """
    try:
        from flightops.amadeus_client import search_roundtrip

        results, price_level = search_roundtrip(
            origin,
            destination,
            outbound_date,
            return_date,
            passengers=passengers,
            max_results=max_results,
            nonstop_only=nonstop_only,
            airline_filter=airline_filter,
            seat=seat,
        )
        return json.dumps({"results": results, "price_level": price_level})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_LOCAL_READ)
def list_routes() -> str:
    """List all active tracked routes with their target prices and latest snapshot.

    Returns JSON: {routes: [{id, origin, destination, travel_date, return_date,
                   passengers, target_price, preferred_airline, nonstop_only,
                   label, latest_snapshot: {price_per_person, airline, fetched_at} | null}]}
    """
    try:
        from flightops import db

        routes = db.list_routes(active_only=True)
        for r in routes:
            snap = db.get_latest_snapshot(r["id"])
            if snap:
                r["latest_snapshot"] = {
                    "price_per_person": snap["price_per_person"],
                    "price_total": snap["price_total"],
                    "airline": snap["airline"],
                    "price_level": snap["price_level"],
                    "fetched_at": snap["fetched_at"],
                }
            else:
                r["latest_snapshot"] = None
        return json.dumps({"routes": routes})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        openWorldHint=True,
    )
)
def poll_routes(route_id: int | None = None) -> str:
    """Fetch and store current best prices for tracked routes.

    Args:
        route_id: Poll only this route ID. If omitted, polls all active routes.

    Returns JSON: {polled: [{route_id, label, price_per_person, airline,
                   price_level, stored: bool}]}
    """
    try:
        from flightops import db
        from flightops.amadeus_client import search_flights

        if route_id is not None:
            route = db.get_route(route_id)
            if not route or not route.get("active"):
                return json.dumps({"error": f"Route {route_id} not found or inactive"})
            routes = [route]
        else:
            routes = db.list_routes(active_only=True)

        polled = []
        for r in routes:
            label = r.get("label") or f"{r['origin']}->{r['destination']}"
            try:
                return_date = r.get("return_date")
                if return_date:
                    from flightops.amadeus_client import search_roundtrip

                    results, price_level = search_roundtrip(
                        origin=r["origin"],
                        destination=r["destination"],
                        outbound_date=r["travel_date"],
                        return_date=return_date,
                        passengers=r["passengers"],
                        max_results=5,
                        airline_filter=r.get("preferred_airline"),
                        nonstop_only=bool(r.get("nonstop_only")),
                    )
                else:
                    results, price_level = search_flights(
                        origin=r["origin"],
                        destination=r["destination"],
                        travel_date=r["travel_date"],
                        passengers=r["passengers"],
                        max_results=5,
                        airline_filter=r.get("preferred_airline"),
                        nonstop_only=bool(r.get("nonstop_only")),
                    )
                if results:
                    best = results[0]
                    db.add_snapshot(
                        route_id=r["id"],
                        price_total=best["price_total"],
                        price_per_person=best["price_per_person"],
                        currency=best["currency"],
                        airline=best["airline"],
                        stops=best["stops"],
                        duration=best["duration"],
                        departure=best.get("departure"),
                        arrival=best.get("arrival"),
                        price_level=price_level,
                        raw=best.get("raw"),
                    )
                    polled.append(
                        {
                            "route_id": r["id"],
                            "label": label,
                            "price_per_person": best["price_per_person"],
                            "airline": best["airline"],
                            "price_level": price_level,
                            "stored": True,
                        }
                    )
                else:
                    polled.append({"route_id": r["id"], "label": label, "stored": False, "reason": "no results"})
            except Exception as exc:
                polled.append({"route_id": r["id"], "label": label, "stored": False, "reason": str(exc)})

        return json.dumps({"polled": polled})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_LOCAL_READ)
def get_price_history(route_id: int, limit: int = 20) -> str:
    """Get price snapshot history for a tracked route, newest first.

    Args:
        route_id: Route ID from list_routes
        limit: Max snapshots to return (default 20)

    Returns JSON: {route: {...}, snapshots: [...], stats: {min, max, avg, count}}
    """
    try:
        from flightops import db

        route = db.get_route(route_id)
        if not route:
            return json.dumps({"error": f"Route {route_id} not found"})
        snapshots = db.get_history(route_id, limit=limit, desc=True)
        stats = db.get_stats(route_id)
        return json.dumps({"route": route, "snapshots": snapshots, "stats": stats})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_LOCAL_READ)
def get_price_alerts() -> str:
    """Return routes where the latest snapshot price is at or below the target price.

    Returns JSON: {alerts: [{route, snapshot, savings_pp, savings_pct}],
                   no_target: [route_ids with no target set],
                   no_data: [route_ids with no snapshot yet]}
    """
    try:
        from flightops import db

        routes = db.list_routes(active_only=True)
        alerts = []
        no_target = []
        no_data = []

        for r in routes:
            if not r.get("target_price"):
                no_target.append(r["id"])
                continue
            snap = db.get_latest_snapshot(r["id"])
            if not snap:
                no_data.append(r["id"])
                continue
            if snap["price_per_person"] <= r["target_price"]:
                savings_pp = r["target_price"] - snap["price_per_person"]
                savings_pct = round(savings_pp / r["target_price"] * 100, 1)
                alerts.append(
                    {
                        "route": r,
                        "snapshot": snap,
                        "savings_pp": round(savings_pp, 2),
                        "savings_pct": savings_pct,
                    }
                )

        return json.dumps({"alerts": alerts, "no_target": no_target, "no_data": no_data})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        openWorldHint=False,
    )
)
def add_route(
    origin: str,
    destination: str,
    date: str,
    passengers: int = 2,
    target_price: float | None = None,
    label: str | None = None,
    nonstop_only: bool = False,
    preferred_airline: str | None = None,
    return_date: str | None = None,
) -> str:
    """Add a new route to track for price polling.

    Args:
        origin: Origin airport IATA code
        destination: Destination airport IATA code
        date: Travel date YYYY-MM-DD
        passengers: Number of passengers (default 2)
        target_price: Alert threshold price per person
        label: Human-readable label (e.g. 'Anniversary outbound')
        nonstop_only: Only track nonstop flights
        preferred_airline: Filter to a specific airline
        return_date: Return date for round trip tracking

    Returns JSON: {route_id: int, message: str}
    """
    try:
        from flightops import db

        route_id = db.add_route(
            origin=origin.upper(),
            destination=destination.upper(),
            travel_date=date,
            return_date=return_date,
            passengers=passengers,
            target_price=target_price,
            preferred_airline=preferred_airline,
            nonstop_only=nonstop_only,
            label=label,
        )
        return json.dumps({"route_id": route_id, "message": f"Route {route_id} added"})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_LOCAL_READ)
def get_search_stats() -> str:
    """Return Google Flights search log counts: total, this month, by type, and recent searches."""
    try:
        from flightops import db

        return json.dumps(db.get_search_counts())
    except Exception as e:
        return json.dumps({"error": str(e)})


def main():
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
