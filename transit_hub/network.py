import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Route:
    id: str
    short_name: str
    long_name: str
    color: str
    text_color: str
    sort_order: int


@dataclass
class Platform:
    id: str
    name: str
    lat: float
    lon: float
    station: str
    heading: str


@dataclass
class Station:
    id: str
    name: str
    lat: float
    lon: float
    platforms: list
    routes: set = field(default_factory=set)
    complex_id: str = ""


@dataclass
class Complex:
    id: str
    name: str
    stations: list
    platforms: list
    routes: list
    lat: float
    lon: float

    @property
    def label(self):
        return "%s (%s)" % (self.name, " ".join(self.routes))


@dataclass
class ServiceRule:
    days: tuple
    start: dt.date
    end: dt.date


class Edge:
    """One directed hop between two platforms on one route, with its timetable per service id."""

    __slots__ = ("u", "v", "route", "by_service", "headsign")

    def __init__(self, u, v, route, by_service, headsign=""):
        self.u = u
        self.v = v
        self.route = route
        self.by_service = by_service
        self.headsign = headsign

    @property
    def key(self):
        return (self.u, self.v, self.route)


def _normalize(text):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


class Network:
    def __init__(self):
        self.routes = {}
        self.platforms = {}
        self.stations = {}
        self.complexes = {}
        self.edges = {}
        self.out = {}
        self.walks = {}
        self.calendar = {}
        self.calendar_dates = {}
        self.shapes = {}
        self.feed_version = ""
        self.line_of = {}
        self.lines = {}
        self._services = {}

    def finalize_lines(self):
        """Group express variants (6X, 7X, FX) under their local line; keep shuttles separate."""
        self.line_of = {}
        for route_id in self.routes:
            base = route_id[:-1] if route_id.endswith("X") and route_id[:-1] in self.routes else route_id
            self.line_of[route_id] = base
        keys = sorted(set(self.line_of.values()), key=lambda k: (self.routes[k].sort_order, k))
        counts = Counter(self.routes[k].short_name for k in keys)
        self.lines = {
            key: self.routes[key].short_name if counts[self.routes[key].short_name] == 1 else key for key in keys
        }

    def line_key(self, route_id):
        return self.line_of.get(route_id, route_id)

    def line_label(self, key):
        return self.lines.get(key, key)

    def line_routes(self, key):
        return [route_id for route_id, base in self.line_of.items() if base == key] or [key]

    def services_on(self, day):
        cached = self._services.get(day)
        if cached is not None:
            return cached
        active = {
            service_id
            for service_id, rule in self.calendar.items()
            if rule.days[day.weekday()] and rule.start <= day <= rule.end
        }
        for service_id, kind in self.calendar_dates.get(day, ()):
            if kind == 1:
                active.add(service_id)
            elif kind == 2:
                active.discard(service_id)
        result = tuple(sorted(active))
        self._services[day] = result
        return result

    def out_edges(self, platform_id):
        return self.out.get(platform_id, ())

    def station_name(self, platform_id):
        platform = self.platforms.get(platform_id)
        return platform.name if platform else platform_id

    def find_complexes(self, text):
        """Complexes matching free text, best matches first."""
        query = _normalize(text)
        if not query:
            return []
        if text in self.complexes:
            return [self.complexes[text]]
        exact, prefix, contains = [], [], []
        for cx in self.complexes.values():
            names = [_normalize(self.stations[s].name) for s in cx.stations]
            label = _normalize(cx.label)
            if query == label or query in names:
                exact.append(cx)
            elif any(name.startswith(query) for name in names):
                prefix.append(cx)
            elif query in label:
                contains.append(cx)
        rank = lambda cx: (-len(cx.routes), cx.name)
        return sorted(exact, key=rank) + sorted(prefix, key=rank) + sorted(contains, key=rank)
