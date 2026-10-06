from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .names import normalize
from .places import resolve_place, search_places
from .planner import PlanningError, plan_fuel_stops
from .providers import Route
from .stations import Station, StationOnRoute


def on_route(mile, price, name=None):
    station = Station(name or f'S{mile}', name or f'S{mile}', '', 'Town', 'ST', price, 0.0, 0.0)
    return StationOnRoute(station, mile, 0.0)


def plan(stations, total_miles, window=50):
    return plan_fuel_stops(stations, total_miles, range_miles=500, mpg=10, first_stop_window_miles=window)


def summary(stops):
    return [(s.stop.mile, round(s.gallons, 2)) for s in stops]


class PlannerTests(SimpleTestCase):
    def test_worked_example(self):
        stations = [on_route(0, 3.50), on_route(200, 3.00), on_route(450, 4.00), on_route(600, 3.20), on_route(900, 3.90)]
        stops = plan(stations, 1000)
        # $3.50 -> just enough to reach $3.00; nothing cheaper ahead -> fill; at mile 600 just enough to finish.
        self.assertEqual(summary(stops), [(0, 20), (200, 50), (600, 30)])
        self.assertAlmostEqual(sum(s.cost for s in stops), 70 + 150 + 96)

    def test_first_stop_is_cheapest_in_window(self):
        stations = [on_route(5, 4.00), on_route(40, 3.00), on_route(60, 2.00)]
        stops = plan(stations, 300, window=50)
        self.assertEqual(stops[0].stop.mile, 40)

    def test_goes_to_nearest_cheaper_station_not_cheapest(self):
        stations = [on_route(0, 4.00), on_route(100, 3.50), on_route(450, 3.00)]
        stops = plan(stations, 900)
        self.assertEqual(summary(stops)[:2], [(0, 10), (100, 35)])

    def test_fills_up_then_tops_up_at_next_best(self):
        stations = [on_route(0, 1.50), on_route(400, 2.50), on_route(460, 5.00), on_route(800, 5.00)]
        stops = plan(stations, 1200)
        # Full tank at $1.50, then 40 gallons at $2.50 on arrival with 10 left.
        self.assertEqual(summary(stops)[:2], [(0, 50), (400, 40)])

    def test_short_trip_buys_only_what_it_needs(self):
        stops = plan([on_route(10, 3.00)], 200)
        self.assertEqual(summary(stops), [(10, 19)])

    def test_cheapest_station_in_same_town_is_used(self):
        stations = [on_route(0, 4.00), on_route(100, 3.80, 'pricier'), on_route(100, 3.10, 'cheaper')]
        stops = plan(stations, 600)
        self.assertEqual(stops[1].stop.station.name, 'cheaper')

    def test_no_station_near_start(self):
        with self.assertRaisesMessage(PlanningError, 'first 50 miles'):
            plan([on_route(80, 3.00)], 400)

    def test_gap_longer_than_range(self):
        with self.assertRaisesMessage(PlanningError, "can't be completed"):
            plan([on_route(0, 3.00), on_route(700, 3.00)], 1000)


class NameTests(SimpleTestCase):
    def test_spelling_variants_match(self):
        self.assertEqual(normalize('St. Paul'), normalize('Saint Paul'))
        self.assertEqual(normalize('Ft Worth'), normalize('Fort Worth'))
        self.assertEqual(normalize('Mc Calla'), normalize('McCalla'))
        self.assertEqual(normalize('Cañon City'), normalize('Canon City'))


class PlaceTests(SimpleTestCase):
    def test_resolve(self):
        self.assertEqual(resolve_place('Nashville, TN').label, 'Nashville, TN')
        self.assertEqual(resolve_place('saint louis, mo').state, 'MO')
        self.assertIsNone(resolve_place('Portland'))  # The state is required.
        self.assertIsNone(resolve_place('Atlantis, ZZ'))

    def test_search_suggestions_resolve_to_themselves(self):
        results = search_places('portland')
        self.assertIn('Portland, OR', [p.label for p in results])
        for place in results:
            self.assertEqual(resolve_place(place.label), place)

    def test_search_filters_by_state(self):
        self.assertTrue(all(p.state.startswith('M') for p in search_places('springfield, m')))


class RouteViewTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def fake_route(self):
        # A straight line Chicago, IL -> St. Louis, MO; real stations near it are used.
        chicago, st_louis = resolve_place('Chicago, IL'), resolve_place('St. Louis, MO')
        steps = 200
        coordinates = [[chicago.lng + (st_louis.lng - chicago.lng) * i / steps,
                        chicago.lat + (st_louis.lat - chicago.lat) * i / steps] for i in range(steps + 1)]
        return Route('test', 260.0, coordinates)

    def test_route_returns_plan(self):
        with patch('routing.views.get_route', return_value=self.fake_route()) as get_route:
            response = self.client.get('/api/route', {'start': 'Chicago, IL', 'finish': 'St. Louis, MO'})
        self.assertEqual(response.status_code, 200)
        get_route.assert_called_once()
        data = response.json()
        self.assertGreaterEqual(len(data['fuel_stops']), 1)
        self.assertAlmostEqual(data['total_fuel_cost'], sum(s['cost'] for s in data['fuel_stops']), places=1)
        self.assertTrue(all(s['miles_off_route'] <= 10 for s in data['stations_on_route']))

    def test_repeat_request_is_served_from_cache(self):
        with patch('routing.views.get_route', return_value=self.fake_route()) as get_route:
            first = self.client.get('/api/route', {'start': 'Chicago, IL', 'finish': 'St. Louis, MO'})
            # Same places, different spelling: still a cache hit.
            second = self.client.get('/api/route', {'start': 'chicago, il', 'finish': 'Saint Louis, MO'})
        get_route.assert_called_once()
        self.assertEqual((first['X-Cache'], second['X-Cache']), ('MISS', 'HIT'))
        self.assertEqual(first.content, second.content)

    def test_unknown_city(self):
        response = self.client.get('/api/route', {'start': 'Nowhere, ZZ', 'finish': 'St. Louis, MO'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Unknown US city', response.json()['error'])

    def test_missing_params(self):
        self.assertEqual(self.client.get('/api/route').status_code, 400)

    def test_cities(self):
        response = self.client.get('/api/cities', {'q': 'des moi'})
        self.assertIn('Des Moines, IA', [r['label'] for r in response.json()['results']])
