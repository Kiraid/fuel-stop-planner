"""Add lat/lng to each cleaned station: US Census Gazetteer places first, Nominatim for the rest."""
import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from routing.names import PLACE_TYPE, census_names, normalize  # noqa: E402

DATA = ROOT / "data"
STATIONS = DATA / "fuel-stations-clean.csv"
# Places (cities, towns, CDPs) take priority over county subdivisions (townships, New England towns).
CENSUS_FILES = [DATA / "2026_Gaz_place_national.txt", DATA / "2026_Gaz_cousubs_national.txt"]
OUTPUT = DATA / "fuel-stations-geocoded.csv"
CACHE = DATA / "nominatim-cache.json"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "fuel-path-assessment/1.0 (https://github.com/Kiraid)"

def load_places():
    """Index Census places by (normalized name, state), keeping the best-ranked entry for each key."""
    best = {}
    for file_rank, path in enumerate(CENSUS_FILES):
        is_subdivision = file_rank > 0
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="|"):
                if is_subdivision and row["FUNCSTAT"] == "N":
                    continue  # Nonfunctioning paper townships, often far from the community of the same name.
                point = (float(row["INTPTLAT"]), float(row["INTPTLONG"]))
                for name, is_alias in census_names(row["NAME"], is_subdivision):
                    # Prefer places over subdivisions, real names over aliases, incorporated places over CDPs.
                    rank = (file_rank, is_alias, row["FUNCSTAT"] == "S")
                    key = (normalize(name), row["USPS"])
                    if key not in best or rank < best[key][0]:
                        best[key] = (rank, {point})
                    elif rank == best[key][0]:
                        best[key][1].add(point)
    # Equally ranked places sharing a name (two Fairfield townships in NJ) can't be told apart; Nominatim decides.
    return {key: points.pop() for key, (rank, points) in best.items() if len(points) == 1}


def find_place(places, city, state):
    return places.get((normalize(city), state)) or places.get((normalize(PLACE_TYPE.sub("", city.lower())), state))


def fetch_json(url, attempts=3):
    """GET a JSON URL, retrying brief network drops."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.URLError:
            if attempt == attempts:
                raise
            time.sleep(5 * attempt)


def nominatim_lookup(city, state):
    """Return [lat, lng] for a city, trying a structured search before a free-text one."""
    searches = [
        {"city": city, "state": state, "country": "USA"},
        {"q": f"{city}, {state}, USA"},
    ]
    for search in searches:
        params = urllib.parse.urlencode({**search, "format": "jsonv2", "limit": 1, "countrycodes": "us"})
        results = fetch_json(f"{NOMINATIM_URL}?{params}")
        time.sleep(1)  # Nominatim's usage policy allows at most one request per second.
        if results:
            return [float(results[0]["lat"]), float(results[0]["lon"])]
    return None


def main():
    places = load_places()
    with STATIONS.open(newline="", encoding="utf-8") as f:
        stations = list(csv.DictReader(f))

    # Saved after every lookup so a rerun or an interrupted run never repeats a request.
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}

    missing = sorted({(s["city"], s["state"]) for s in stations if not find_place(places, s["city"], s["state"])})
    print(f"{len(missing)} cities not in the Census files, looking them up in Nominatim")
    for i, (city, state) in enumerate(missing, 1):
        key = f"{city}, {state}"
        if key not in cache:
            cache[key] = nominatim_lookup(city, state)
            CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
            print(f"  [{i}/{len(missing)}] {key} -> {cache[key]}")

    geocoded, unresolved = [], []
    for station in stations:
        place = find_place(places, station["city"], station["state"])
        if place:
            (lat, lng), source = place, "census"
        elif cache.get(f"{station['city']}, {station['state']}"):
            (lat, lng), source = cache[f"{station['city']}, {station['state']}"], "nominatim"
        else:
            unresolved.append(station)
            continue
        geocoded.append({**station, "lat": lat, "lng": lng, "source": source})

    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[*stations[0].keys(), "lat", "lng", "source"])
        writer.writeheader()
        writer.writerows(geocoded)

    from_nominatim = sum(1 for s in geocoded if s["source"] == "nominatim")
    print(f"Geocoded {len(geocoded)} of {len(stations)} stations "
          f"({len(geocoded) - from_nominatim} Census, {from_nominatim} Nominatim) -> {OUTPUT.name}")
    if unresolved:
        print(f"Could not geocode {len(unresolved)} stations:")
        for s in unresolved:
            print(f"  {s['id']} {s['name']} - {s['city']}, {s['state']}")


if __name__ == "__main__":
    main()
