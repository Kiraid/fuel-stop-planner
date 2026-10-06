"""Geocoded fuel stations, and finding the ones within a corridor around a route."""
import csv
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from django.conf import settings

EARTH_RADIUS_MILES = 3958.8
MILES_PER_DEGREE = EARTH_RADIUS_MILES * np.pi / 180


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    address: str
    city: str
    state: str
    price: float
    lat: float
    lng: float


@dataclass(frozen=True)
class StationOnRoute:
    station: Station
    mile: float  # How far along the route the station is.
    miles_off_route: float


@lru_cache(maxsize=1)
def load_stations():
    """All stations, plus their coordinates as an (n, 2) [lat, lng] array; loaded once per process."""
    with open(settings.STATIONS_FILE, newline="", encoding="utf-8") as f:
        stations = [
            Station(row["id"], row["name"], row["address"], row["city"], row["state"],
                    float(row["price"]), float(row["lat"]), float(row["lng"]))
            for row in csv.DictReader(f)
        ]
    return stations, np.array([(s.lat, s.lng) for s in stations])


def cumulative_miles(path):
    """Great-circle miles from the start of an (n, 2) [lat, lng] path to each of its points."""
    lat, lng = np.radians(path[:, 0]), np.radians(path[:, 1])
    a = np.sin(np.diff(lat) / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lng) / 2) ** 2
    return np.concatenate([[0.0], np.cumsum(2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a)))])


def stations_along_route(route, corridor_miles):
    """Stations within corridor_miles of the route, each tagged with its mile marker, sorted by mile."""
    stations, coords = load_stations()
    path = np.array(route.coordinates, dtype=float)[:, ::-1]  # GeoJSON [lng, lat] -> [lat, lng]
    miles = cumulative_miles(path)
    miles *= route.distance_miles / miles[-1]  # Scale to the provider's road distance.

    # A point every half mile is plenty to place stations within a corridor of several miles.
    keep = np.unique(np.searchsorted(miles, np.arange(0, miles[-1], 0.5)))
    path, miles = path[keep], miles[keep]

    # Only stations inside the route's bounding box, padded by the corridor, can be near it.
    pad_lat = corridor_miles / MILES_PER_DEGREE
    pad_lng = pad_lat / np.cos(np.radians(np.abs(path[:, 0]).max() + pad_lat))
    low, high = path.min(axis=0) - (pad_lat, pad_lng), path.max(axis=0) + (pad_lat, pad_lng)
    candidates = np.flatnonzero(np.all((coords >= low) & (coords <= high), axis=1))

    found = []
    # Chunked so the station-by-route-point distance matrix stays small.
    for chunk in np.array_split(candidates, max(1, len(candidates) // 500)):
        if not len(chunk):
            continue
        # Flat-earth distances are accurate at corridor scale, and far cheaper than haversine.
        dlat = (coords[chunk, None, 0] - path[None, :, 0]) * MILES_PER_DEGREE
        dlng = (coords[chunk, None, 1] - path[None, :, 1]) * MILES_PER_DEGREE * np.cos(np.radians(path[None, :, 0]))
        distance = np.hypot(dlat, dlng)
        nearest = distance.argmin(axis=1)
        off_route = distance[np.arange(len(chunk)), nearest]
        for index, point, off in zip(chunk, nearest, off_route):
            if off <= corridor_miles:
                found.append(StationOnRoute(stations[index], float(miles[point]), float(off)))
    return sorted(found, key=lambda s: s.mile)
