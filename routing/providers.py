"""Driving routes from OpenRouteService when a key is set, otherwise from the public OSRM server."""
import logging
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344


class RoutingError(Exception):
    pass


@dataclass
class Route:
    """A provider-independent route: road distance plus the path as GeoJSON [lng, lat] pairs."""
    provider: str
    distance_miles: float
    coordinates: list


def get_route(start, finish):
    """Route between two (lat, lng) points with one call; OSRM is the fallback if OpenRouteService fails."""
    if settings.ORS_API_KEY:
        try:
            return _openrouteservice(start, finish)
        except (requests.RequestException, KeyError, IndexError) as exc:
            logger.warning("OpenRouteService failed, falling back to OSRM: %s", exc)
    try:
        return _osrm(start, finish)
    except (requests.RequestException, KeyError, IndexError) as exc:
        raise RoutingError(f"The routing service is unavailable ({exc}).") from exc


def _openrouteservice(start, finish):
    response = requests.post(
        "https://api.openrouteservice.org/v2/directions/driving-hgv/geojson",
        json={"coordinates": [[start[1], start[0]], [finish[1], finish[0]]]},
        headers={"Authorization": settings.ORS_API_KEY},
        timeout=settings.ROUTING_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    feature = response.json()["features"][0]
    return Route(
        provider="openrouteservice",
        distance_miles=feature["properties"]["summary"]["distance"] / METERS_PER_MILE,
        coordinates=feature["geometry"]["coordinates"],
    )


def _osrm(start, finish):
    points = f"{start[1]},{start[0]};{finish[1]},{finish[0]}"
    response = requests.get(
        f"{settings.OSRM_URL}/route/v1/driving/{points}",
        params={"overview": "full", "geometries": "geojson"},
        timeout=settings.ROUTING_TIMEOUT_SECONDS,
    )
    data = response.json()
    if data.get("code") != "Ok":
        raise RoutingError(f"No route found: {data.get('message') or data.get('code')}.")
    route = data["routes"][0]
    return Route(
        provider="osrm",
        distance_miles=route["distance"] / METERS_PER_MILE,
        coordinates=route["geometry"]["coordinates"],
    )
