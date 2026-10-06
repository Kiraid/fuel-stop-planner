"""Clean the raw fuel price CSV: drop Canadian rows and merge duplicate stations."""
import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
RAW = DATA / "fuel-prices-for-be-assessment.csv"
CLEAN = DATA / "fuel-stations-clean.csv"

CANADIAN_PROVINCES = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}


def main():
    with RAW.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    stations = {}
    canadian = 0
    for row in rows:
        state = row["State"].strip().upper()
        if state in CANADIAN_PROVINCES:
            canadian += 1
            continue

        station_id = row["OPIS Truckstop ID"].strip()
        price = float(row["Retail Price"])
        existing = stations.get(station_id)
        # Repeated IDs are the same stop at the same address, so keep its cheapest price.
        if existing is None or price < existing["price"]:
            stations[station_id] = {
                "id": station_id,
                "name": row["Truckstop Name"].strip(),
                "address": row["Address"].strip(),
                "city": row["City"].strip(),
                "state": state,
                "price": price,
            }

    with CLEAN.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "name", "address", "city", "state", "price"])
        writer.writeheader()
        writer.writerows(sorted(stations.values(), key=lambda s: int(s["id"])))

    print(f"Read {len(rows)} rows")
    print(f"Dropped {canadian} Canadian rows")
    print(f"Merged duplicates: {len(rows) - canadian - len(stations)} rows removed")
    print(f"Wrote {len(stations)} stations to {CLEAN.name}")


if __name__ == "__main__":
    main()
