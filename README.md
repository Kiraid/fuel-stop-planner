# Fuel Path

A Django API that takes a start and finish location in the USA and returns the driving route, the most cost-effective places to refuel along it, and the total fuel cost.

**Vehicle assumptions:** 500-mile range, 10 miles per gallon (so a 50-gallon tank).

## Setup

Requires Python 3.12+ (developed on Python 3.14 in WSL Ubuntu) and Django 6.1.1.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # optional; every setting has a default
python manage.py runserver 8001
```

Then open http://127.0.0.1:8001/ for the map page, or call the API directly.

```bash
python manage.py test routing
```

### Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `ORS_API_KEY` | empty | [OpenRouteService](https://openrouteservice.org) key. When set, routes use its truck profile (`driving-hgv`). When empty, or if OpenRouteService fails, the public [OSRM](https://project-osrm.org) server is used (car profile). |
| `CORRIDOR_MILES` | `10` | How far off the route a station may be and still count as on it |
| `FIRST_STOP_WINDOW_MILES` | `50` | The first stop is the cheapest station within this many miles of the start (capped at 50) |
| `OSRM_URL` | `https://router.project-osrm.org` | OSRM server to use |
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` | dev key, `true` | Standard Django settings |

The two providers can return slightly different routes, because the truck profile avoids roads closed to trucks, so the fuel stops can differ too. Every response says which provider was used.

## API

### `GET /api/route?start=<City, ST>&finish=<City, ST>`

```
GET /api/route?start=Portland, OR&finish=Des Moines, IA
```

| Field | Meaning |
|---|---|
| `start`, `finish` | Resolved cities with coordinates |
| `routing_provider` | `openrouteservice` or `osrm` |
| `distance_miles` | Road distance |
| `total_fuel_cost`, `total_gallons` | Fuel bought on the trip |
| `fuel_stops` | Each stop: station, address, price, mile marker along the route, miles off route, gallons bought, cost |
| `stations_on_route` | Every station within the corridor (drawn on the map) |
| `route` | The route as a GeoJSON `LineString` |
| `assumptions` | Range, mpg, tank size, corridor and first-stop window in effect |

Errors return `{"error": "..."}`:
- `400`: a missing or unknown city
- `422`: no station near the start, or a gap longer than the range
- `502`: the routing service failed

Each request makes **one** routing call. The only exception is when OpenRouteService fails and the request is retried on OSRM.

**Caching.** Successful results are kept in memory (Django's local-memory cache) until the server restarts, keyed by the two resolved places. A repeat request makes no routing call and returns in about 30 ms instead of a few seconds. Different spellings of the same city share one entry (`portland, or` = `Portland, OR`). The `X-Cache` response header shows `MISS` or `HIT`.

### `GET /api/cities?q=<text>`

Autocomplete for the inputs, served from the local Census places file, with no external calls. `q=port` suggests cities starting with "port"; `q=springfield, m` narrows to states starting with M. Every suggestion's label resolves back to exactly that place.

### `GET /`

The map page: pick two cities and see the route, every station in the corridor (grey dots), the fuel stops (numbered) and the cost breakdown.

## How it works

1. **Data preparation (offline, once).** The provided fuel price list is cleaned and every station is given coordinates. The result is committed, so the API never geocodes stations at request time.
2. **Start and finish.** "City, ST" is looked up in the local Census places file. A name shared by several places in one state resolves to the incorporated one, then the largest by land area.
3. **Routing (one external call).** The routing provider returns the road distance and the route line. Both providers are converted to the same internal shape.
4. **Stations along the route.** The route is sampled every half mile. Stations within `CORRIDOR_MILES` of it are kept and tagged with their mile marker (how far along the route they are). numpy computes the distances, after a bounding-box filter, so this takes milliseconds.
5. **Fuel stops.** The vehicle starts with just enough fuel to reach its first stop; that fuel isn't counted in the cost. Then:
   - **First stop:** the cheapest station within `FIRST_STOP_WINDOW_MILES` of the start.
   - **At each stop:**
     - If a cheaper station is within 500 miles, buy just enough to reach the **nearest** cheaper one.
     - Otherwise, if the destination is within 500 miles, buy just enough to finish.
     - Otherwise, fill the tank (50 gallons) and drive to the cheapest station within range.

   Fuel left in the tank carries over, so after filling up at a cheap station, the next stop only tops up. Going to the *nearest* cheaper station, not the cheapest one, avoids buying extra fuel at the current, higher price.

## Data preparation

### 1. Cleaning: `scripts/clean_stations.py`

Input: `data/fuel-prices-for-be-assessment.csv` (8,151 rows, as provided).

| Step | Rows |
|---|---|
| Raw rows | 8,151 |
| Canadian rows dropped (AB, BC, MB, NB, NS, ON, QC, SK, YT) | −620 |
| Duplicate rows merged | −905 |
| **Stations** | **6,626** |

Duplicates share the same OPIS ID, address, city and state and differ only by price (one stop is listed six times, at $3.27–$3.43). Each stop keeps its **lowest** price.

Output: `data/fuel-stations-clean.csv`.

### 2. Geocoding: `scripts/geocode_stations.py`

The dataset has no coordinates, and its address field describes highway interchanges (`I-44, EXIT 283 & US-69`) rather than street addresses. Free geocoders cannot resolve that format, so each station is placed at the **center of its city**:

1. **[US Census Gazetteer files](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html)** (2026, public domain), matched on city + state:
   - **Places:** incorporated cities, towns and villages, plus Census Designated Places (named unincorporated communities).
   - **County Subdivisions:** townships and New England towns, such as Mahwah NJ and Sturbridge MA. Statistical areas (CCDs, precincts) and nonfunctioning townships are skipped, because their center can be far from any town of that name.

   Names are normalized before matching:
   - `St. Paul` / `St Paul` / `Saint Paul`, `Ft Worth` / `Fort Worth`, `Mc Calla` / `McCalla`, `S Coffeyville` / `South Coffeyville` and `Cañon City` / `Canon City` all match.
   - Census type suffixes are dropped (`Abbeville city` → `Abbeville`).
   - Consolidated city-counties are reachable by their short name (`Nashville-Davidson metropolitan government (balance)` → `Nashville`).

   When a name appears more than once in a state, places beat subdivisions, and incorporated places beat CDPs. That settles 200 of the 207 such cases among station cities. The remaining 7 are true ties (two Fairfield townships in New Jersey), and Nominatim picks the one it ranks higher.
2. **[Nominatim](https://nominatim.openstreetmap.org/)** (OpenStreetMap) for the cities the Census files don't cover, mostly unincorporated truck-stop communities such as Breezewood PA and Ruther Glen VA. Requests are limited to one per second, as Nominatim's usage policy requires, and cached in `data/nominatim-cache.json`, so rerunning the script makes no requests.

| Source | Stations |
|---|---|
| Census | 6,407 |
| Nominatim | 219 |
| Not found | 0 |

**Validation.** The stations were first geocoded with the SimpleMaps US Cities database, which can't be redistributed in a public repo, so it was replaced. Comparing the two independent results: 6,589 of 6,626 stations (99.4%) land within 5 miles of each other. The 9 cities that differ by 10+ miles were checked by hand against each station's highway and exit. In most of them the Census pick is the correct one, such as Midway FL on I-10 exit 192, where SimpleMaps had chosen a different Midway. Three stations are known to be misplaced: two where Nominatim chose the wrong same-named town (Wilmington IL, Newburg PA), and Taylor MO, which is only in the Census files as a distant township.

Output: `data/fuel-stations-geocoded.csv` (stations plus `lat`, `lng`, `source`).

**Why city-level, not exact locations.** A truck stop is usually a few miles from its town's center. That error is small next to the 500-mile range and the route corridor, and it barely moves the cost. Placing stations exactly would need a business-name search. In a test of 20 random stations, Nominatim found only 3 by name; the rest returned nothing or a different place. Google's Places API does find them, but it is a paid service. A free improvement would be to match each station's highway and exit number (`I-44, EXIT 283`) against OpenStreetMap's interchange data.

**Known limitation.** Stations in a town where two highways cross share one point, so a station on the other highway may appear to be on the route when reaching it actually takes a short detour.

### Rerunning the data preparation

The scripts use only the Python standard library.

```bash
python3 scripts/clean_stations.py
python3 scripts/geocode_stations.py
```

## Project layout

```
config/     Django project settings and URLs
routing/    the app
  places.py     city lookup and autocomplete (Census places)
  providers.py  OpenRouteService / OSRM, normalized to one Route shape
  stations.py   loads the geocoded stations; finds the ones along a route
  planner.py    the fuel-stop algorithm
  names.py      city-name normalization (shared with the geocoding script)
  views.py      the API and the map page
  templates/    the map page (Leaflet + OpenStreetMap tiles)
data/       input CSV, Census Gazetteer files, cleaned and geocoded stations, Nominatim cache
scripts/    one-off data preparation scripts
```

## Data sources and attribution

- Fuel prices: provided with the assessment.
- City coordinates: [US Census Bureau Gazetteer Files](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html), 2026 Places and County Subdivisions (public domain).
- Geocoding fallback: © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, via Nominatim.
