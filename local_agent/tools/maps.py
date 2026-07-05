"""Maps tool: place search and directions.

Uses OpenStreetMap (Nominatim for geocoding, OSRM for routing) — free, no API
key, works on-device. Returns Google Maps links so results open in the Maps app
(pair with `open_app`). If GOOGLE_MAPS_API_KEY is set in config, that could be
wired in later; the free path needs no key.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from . import Tool, ToolContext

# Nominatim's usage policy requires a descriptive User-Agent and modest rates.
_UA = "local-agent/0.1 (personal on-device assistant)"


def _get_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def _geocode(query: str) -> Optional[dict]:
    params = urllib.parse.urlencode({"q": query, "format": "json", "limit": 1})
    data = _get_json(f"https://nominatim.openstreetmap.org/search?{params}")
    if not data:
        return None
    top = data[0]
    return {
        "name": top.get("display_name", query),
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
    }


def _maps_search_link(query: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(query)}"


def _maps_dir_link(origin: str, destination: str) -> str:
    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={urllib.parse.quote(origin)}"
        f"&destination={urllib.parse.quote(destination)}"
    )


def maps(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search a place, or get directions between two places.

    args: {"query": str}                         -> place search
          {"origin": str, "destination": str}    -> driving directions
    """
    query = str(args.get("query", "")).strip()
    origin = str(args.get("origin", "")).strip()
    destination = str(args.get("destination", "")).strip()

    try:
        if origin and destination:
            a = _geocode(origin)
            b = _geocode(destination)
            if not a or not b:
                return "couldn't locate origin and/or destination"
            coords = f"{a['lon']},{a['lat']};{b['lon']},{b['lat']}"
            route = _get_json(
                f"https://router.project-osrm.org/route/v1/driving/{coords}?overview=false"
            )
            r0 = (route.get("routes") or [{}])[0]
            km = r0.get("distance", 0) / 1000
            mins = r0.get("duration", 0) / 60
            link = _maps_dir_link(origin, destination)
            return (
                f"{origin} → {destination}: {km:.1f} km, ~{mins:.0f} min driving.\n"
                f"Open in Maps: {link}"
            )

        if query:
            loc = _geocode(query)
            if not loc:
                return f"no place found for '{query}'"
            link = _maps_search_link(query)
            return (
                f"{loc['name']}\ncoords: {loc['lat']:.5f},{loc['lon']:.5f}\n"
                f"Open in Maps: {link}"
            )
    except urllib.error.URLError as e:
        return f"maps lookup failed (network): {e}"
    except Exception as e:  # noqa: BLE001
        return f"maps error: {e}"

    return "error: provide 'query' (search) or both 'origin' and 'destination' (directions)"


TOOLS = [
    Tool(
        name="maps",
        tag="SAFE",
        fn=maps,
        optional=("query", "origin", "destination"),
        description="Search a place or get driving directions (OpenStreetMap; returns a Maps link).",
    ),
]
