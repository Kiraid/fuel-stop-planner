"""Choose where to refuel along a route and how much to buy at each stop."""
from dataclasses import dataclass


class PlanningError(Exception):
    pass


@dataclass
class FuelStop:
    stop: object  # StationOnRoute
    gallons: float

    @property
    def cost(self):
        return self.gallons * self.stop.station.price


def plan_fuel_stops(stations, total_miles, *, range_miles, mpg, first_stop_window_miles):
    """Greedy cheapest-fuel plan over StationOnRoute items.

    The vehicle starts with just enough fuel to reach the first stop (not counted), which is the
    cheapest station in the first window. From each stop: if a cheaper station is in range, buy just
    enough to reach the nearest one; else if the destination is in range, buy just enough to finish;
    else fill the tank and go to the cheapest station in range.
    """
    tank = range_miles / mpg
    # Within one mile marker (stations in the same town), the cheapest comes first.
    stations = sorted(stations, key=lambda s: (s.mile, s.station.price))

    window = [s for s in stations if s.mile <= min(first_stop_window_miles, total_miles)]
    if not window:
        raise PlanningError(f"No fuel station within the first {first_stop_window_miles:g} miles of the route.")
    current = min(window, key=lambda s: s.station.price)
    fuel = 0.0

    stops = []
    while True:
        ahead = [s for s in stations if current.mile < s.mile <= current.mile + range_miles]
        cheaper = next((s for s in ahead if s.station.price < current.station.price), None)
        if cheaper:
            target, gallons = cheaper, (cheaper.mile - current.mile) / mpg - fuel
        elif total_miles - current.mile <= range_miles:
            target, gallons = None, (total_miles - current.mile) / mpg - fuel
        elif ahead:
            # Cheapest in range; on a price tie, the farthest one.
            target = min(ahead, key=lambda s: (s.station.price, -s.mile))
            gallons = tank - fuel
        else:
            raise PlanningError(
                f"No fuel station within {range_miles:g} miles after mile {current.mile:.0f}, "
                "so the route can't be completed."
            )

        gallons = max(gallons, 0.0)
        if gallons:
            stops.append(FuelStop(current, gallons))
        if target is None:
            return stops
        fuel = max(fuel + gallons - (target.mile - current.mile) / mpg, 0.0)
        current = target
