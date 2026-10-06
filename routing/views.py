import json

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .places import resolve_place, search_places
from .planner import PlanningError, plan_fuel_stops
from .providers import RoutingError, get_route
from .stations import stations_along_route


@require_GET
def index(request):
    return render(request, 'routing/index.html')


@require_GET
def cities(request):
    """Autocomplete for the start and finish inputs: ?q=port"""
    return JsonResponse({'results': [p.as_dict() for p in search_places(request.GET.get('q', ''))]})


@require_GET
def route(request):
    """Route plus fuel plan: ?start=Portland, OR&finish=Des Moines, IA"""
    texts = {field: request.GET.get(field, '').strip() for field in ('start', 'finish')}
    if not all(texts.values()):
        return _error("Both 'start' and 'finish' are required, e.g. ?start=Portland, OR&finish=Des Moines, IA", 400)
    places = {field: resolve_place(text) for field, text in texts.items()}
    for field, place in places.items():
        if place is None:
            return _error(f"Unknown US city for '{field}': '{texts[field]}'. Use 'City, ST', e.g. 'Portland, OR'.", 400)
    start, finish = places['start'], places['finish']

    # Results only depend on the two places, so repeat requests are served from memory.
    key = f'route:{start.lat},{start.lng}:{finish.lat},{finish.lng}'
    body = cache.get(key)
    hit = body is not None
    if not hit:
        try:
            body = json.dumps(_plan(start, finish))
        except RoutingError as exc:
            return _error(str(exc), 502)
        except PlanningError as exc:
            return _error(str(exc), 422)
        cache.set(key, body)
    response = HttpResponse(body, content_type='application/json')
    response['X-Cache'] = 'HIT' if hit else 'MISS'
    return response


def _plan(start, finish):
    road = get_route((start.lat, start.lng), (finish.lat, finish.lng))
    on_route = stations_along_route(road, settings.CORRIDOR_MILES)
    stops = plan_fuel_stops(
        on_route, road.distance_miles,
        range_miles=settings.VEHICLE_RANGE_MILES,
        mpg=settings.MILES_PER_GALLON,
        first_stop_window_miles=settings.FIRST_STOP_WINDOW_MILES,
    )
    return {
        'start': start.as_dict(),
        'finish': finish.as_dict(),
        'routing_provider': road.provider,
        'distance_miles': round(road.distance_miles, 1),
        'total_fuel_cost': round(sum(stop.cost for stop in stops), 2),
        'total_gallons': round(sum(stop.gallons for stop in stops), 2),
        'fuel_stops': [_stop_json(stop) for stop in stops],
        'assumptions': {
            'vehicle_range_miles': settings.VEHICLE_RANGE_MILES,
            'miles_per_gallon': settings.MILES_PER_GALLON,
            'tank_gallons': settings.VEHICLE_RANGE_MILES / settings.MILES_PER_GALLON,
            'corridor_miles': settings.CORRIDOR_MILES,
            'first_stop_window_miles': settings.FIRST_STOP_WINDOW_MILES,
            'starting_fuel': 'enough to reach the first stop; not counted in the cost',
        },
        'stations_on_route': [_station_json(s) for s in on_route],
        'route': {'type': 'LineString', 'coordinates': road.coordinates},
    }


def _station_json(on_route):
    s = on_route.station
    return {
        'id': s.id, 'name': s.name, 'address': s.address, 'city': s.city, 'state': s.state,
        'price_per_gallon': s.price, 'lat': s.lat, 'lng': s.lng,
        'mile': round(on_route.mile, 1), 'miles_off_route': round(on_route.miles_off_route, 1),
    }


def _stop_json(stop):
    return {**_station_json(stop.stop), 'gallons': round(stop.gallons, 2), 'cost': round(stop.cost, 2)}


def _error(message, status):
    return JsonResponse({'error': message}, status=status)
