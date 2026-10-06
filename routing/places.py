"""US city lookup for the start and finish inputs, from the Census Gazetteer places file."""
import csv
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings

from .names import CONSOLIDATED, census_names, normalize


@dataclass(frozen=True)
class Place:
    name: str
    state: str
    lat: float
    lng: float

    @property
    def label(self):
        return f"{self.name}, {self.state}"

    def as_dict(self):
        return {"label": self.label, "lat": self.lat, "lng": self.lng}


@lru_cache(maxsize=1)
def _index():
    """Map (normalized name, state) to the best-ranked place with that name."""
    best = {}
    with open(settings.CENSUS_PLACES_FILE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="|"):
            names = census_names(row["NAME"])
            # Consolidated city-counties go by their short name ("Nashville", not "Nashville-Davidson ...").
            display = names[1][0] if CONSOLIDATED.search(row["NAME"]) and len(names) > 1 else names[0][0]
            place = Place(display, row["USPS"], float(row["INTPTLAT"]), float(row["INTPTLONG"]))
            # Incorporated places before CDPs, then larger land area as a stand-in for population.
            rank = (row["FUNCSTAT"] == "S", -int(row["ALAND"]))
            for name, _ in names:
                key = (normalize(name), row["USPS"])
                if key not in best or rank < best[key][0]:
                    best[key] = (rank, place)
    return best


@lru_cache(maxsize=1)
def _searchable():
    """Places whose own label resolves back to them, ordered by rank, so every suggestion is unambiguous."""
    entries = [(rank, key, place) for key, (rank, place) in _index().items()
               if key == (normalize(place.name), place.state)]
    return sorted(entries, key=lambda entry: entry[0])


def _split(text):
    city, _, state = text.partition(",")
    return normalize(city), state.strip().upper()


def resolve_place(text):
    """Return the Place for "City, ST", or None."""
    city, state = _split(text)
    entry = _index().get((city, state))
    return entry[1] if entry else None


def search_places(query, limit=8):
    """Autocomplete: places whose name starts with the query; "port, o" narrows to states starting with O."""
    city, state = _split(query)
    if len(city) < 2:
        return []
    matches = [(key[0] != city, rank, place) for rank, key, place in _searchable()
               if key[0].startswith(city) and key[1].startswith(state)]
    matches.sort(key=lambda match: match[:2])  # Exact name matches first, then rank.
    return [place for _, _, place in matches[:limit]]
