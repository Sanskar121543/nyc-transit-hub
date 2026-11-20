import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .network import Edge
from .protobuf import DecodeError, fields, signed

BASE_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/"
ALERTS_URL = BASE_URL + "camsys%2Fsubway-alerts"
LIVE_WINDOW = 30 * 60

FEEDS = {
    "1234567S": (BASE_URL + "nyct%2Fgtfs", ("1", "2", "3", "4", "5", "6", "6X", "7", "7X", "GS")),
    "ACE": (BASE_URL + "nyct%2Fgtfs-ace", ("A", "C", "E", "H")),
    "BDFM": (BASE_URL + "nyct%2Fgtfs-bdfm", ("B", "D", "F", "FX", "M", "FS")),
    "G": (BASE_URL + "nyct%2Fgtfs-g", ("G",)),
    "JZ": (BASE_URL + "nyct%2Fgtfs-jz", ("J", "Z")),
    "L": (BASE_URL + "nyct%2Fgtfs-l", ("L",)),
    "NQRW": (BASE_URL + "nyct%2Fgtfs-nqrw", ("N", "Q", "R", "W")),
    "SIR": (BASE_URL + "nyct%2Fgtfs-si", ("SI",)),
}

_SKIPPED = 1
_CANCELED = 3


@dataclass
class StopUpdate:
    stop_id: str
    arrival: int
    departure: int
    skipped: bool = False


@dataclass
class TripUpdate:
    trip_id: str
    route_id: str
    canceled: bool
    stops: list = field(default_factory=list)


@dataclass
class FeedResult:
    timestamp: int
    trips: list


@dataclass
class Alert:
    id: str
    routes: frozenset
    kind: str
    header: str
    periods: tuple

    @property
    def severity(self):
        kind = self.kind.lower()
        if "suspend" in kind or "no scheduled" in kind:
            return "suspended"
        if "delay" in kind or "reduced" in kind or "reroute" in kind or "skipped" in kind:
            return "disrupted"
        return "info"

    def active_at(self, when):
        if not self.periods:
            return True
        return any(start <= when and (end == 0 or when <= end) for start, end in self.periods)


@dataclass
class Snapshot:
    as_of: int
    fetched_at: int
    live: dict
    extra_out: dict
    covered: frozenset
    alerts: list
    feeds: dict

    @property
    def window_end(self):
        return self.as_of + LIVE_WINDOW

    def age_at(self, now):
        return max(0, int(now - self.as_of))

    @property
    def ok_feeds(self):
        return sum(1 for status in self.feeds.values() if status["ok"])


def _text(raw):
    return raw.decode("utf-8", "replace")


def _event_time(buf):
    for number, wire, value in fields(buf):
        if number == 2 and wire == 0:
            return signed(value)
    return 0


def _stop_update(buf):
    stop_id = ""
    arrival = departure = relationship = 0
    for number, wire, value in fields(buf):
        if number == 2 and wire == 2:
            arrival = _event_time(value)
        elif number == 3 and wire == 2:
            departure = _event_time(value)
        elif number == 4 and wire == 2:
            stop_id = _text(value)
        elif number == 5 and wire == 0:
            relationship = value
    return StopUpdate(stop_id, arrival or departure, departure or arrival, relationship == _SKIPPED)


def _trip_update(buf):
    trip_id = route_id = ""
    relationship = 0
    stops = []
    for number, wire, value in fields(buf):
        if number == 1 and wire == 2:
            for tn, tw, tv in fields(value):
                if tn == 1 and tw == 2:
                    trip_id = _text(tv)
                elif tn == 4 and tw == 0:
                    relationship = tv
                elif tn == 5 and tw == 2:
                    route_id = _text(tv)
        elif number == 2 and wire == 2:
            stops.append(_stop_update(value))
    return TripUpdate(trip_id, route_id, relationship == _CANCELED, stops)


def parse_feed(data):
    timestamp = 0
    trips = []
    for number, wire, value in fields(data):
        if number == 1 and wire == 2:
            for hn, hw, hv in fields(value):
                if hn == 3 and hw == 0:
                    timestamp = hv
        elif number == 2 and wire == 2:
            for en, ew, ev in fields(value):
                if en == 3 and ew == 2:
                    trips.append(_trip_update(ev))
    return FeedResult(timestamp, trips)


def _translated(buf):
    fallback = ""
    for number, wire, value in fields(buf):
        if number != 1 or wire != 2:
            continue
        text = language = ""
        for tn, tw, tv in fields(value):
            if tn == 1 and tw == 2:
                text = _text(tv)
            elif tn == 2 and tw == 2:
                language = _text(tv)
        if language == "en":
            return text
        fallback = fallback or text
    return fallback


def _alert(entity_id, buf):
    routes = set()
    periods = []
    kind = ""
    header = ""
    for number, wire, value in fields(buf):
        if number == 1 and wire == 2:
            start = end = 0
            for pn, pw, pv in fields(value):
                if pn == 1 and pw == 0:
                    start = pv
                elif pn == 2 and pw == 0:
                    end = pv
            periods.append((start, end))
        elif number == 5 and wire == 2:
            for en, ew, ev in fields(value):
                if en == 2 and ew == 2:
                    routes.add(_text(ev))
        elif number == 10 and wire == 2:
            header = _translated(value)
        elif number == 1001 and wire == 2:
            for mn, mw, mv in fields(value):
                if mn == 3 and mw == 2:
                    kind = _text(mv)
    return Alert(entity_id, frozenset(routes), kind, header, tuple(periods))


def parse_alerts(data):
    alerts = []
    for number, wire, value in fields(data):
        if number != 2 or wire != 2:
            continue
        entity_id = ""
        body = None
        for en, ew, ev in fields(value):
            if en == 1 and ew == 2:
                entity_id = _text(ev)
            elif en == 5 and ew == 2:
                body = ev
        if body is not None:
            alerts.append(_alert(entity_id, body))
    return alerts


def http_get(url, timeout=15):
    request = urllib.request.Request(url, headers={"User-Agent": "nyc-transit-hub"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _load(fetch, url, parse):
    try:
        return parse(fetch(url)), None
    except (OSError, DecodeError, ValueError) as error:
        return None, "%s: %s" % (type(error).__name__, error)


def _live_hops(network, trips):
    hops = {}
    for trip in trips:
        if trip.canceled or not trip.route_id:
            continue
        previous = None
        for stop in trip.stops:
            if stop.skipped or stop.stop_id not in network.platforms or not stop.arrival:
                continue
            if previous is not None and stop.arrival >= previous.departure:
                hops.setdefault((previous.stop_id, stop.stop_id, trip.route_id), []).append(
                    (previous.departure, stop.arrival)
                )
            previous = stop
    return hops


def build_snapshot(network, results, alerts, now):
    feeds = {}
    live = {}
    covered = set()
    stamps = []
    trips = []
    for name, (result, error) in results.items():
        if result is None:
            feeds[name] = {"ok": False, "error": error, "trips": 0, "timestamp": None}
            continue
        feeds[name] = {"ok": True, "error": None, "trips": len(result.trips), "timestamp": result.timestamp}
        covered.update(FEEDS[name][1])
        covered.update(trip.route_id for trip in result.trips if trip.route_id)
        if result.timestamp:
            stamps.append(result.timestamp)
        trips.extend(result.trips)

    extra_out = {}
    for key, hops in _live_hops(network, trips).items():
        hops.sort()
        edge_deps = [dep for dep, _ in hops]
        edge_arrs = [arr for _, arr in hops]
        live[key] = (edge_deps, edge_arrs)
        if key not in network.edges:
            extra_out.setdefault(key[0], []).append(Edge(key[0], key[1], key[2], {}))

    as_of = min(stamps) if stamps else now
    return Snapshot(as_of, now, live, extra_out, frozenset(covered), alerts, feeds)


def fetch_snapshot(network, fetch=http_get, now=None):
    now = int(now if now is not None else time.time())
    with ThreadPoolExecutor(max_workers=len(FEEDS) + 1) as pool:
        pending = {name: pool.submit(_load, fetch, url, parse_feed) for name, (url, _) in FEEDS.items()}
        pending_alerts = pool.submit(_load, fetch, ALERTS_URL, parse_alerts)
        results = {name: future.result() for name, future in pending.items()}
        alert_result, _ = pending_alerts.result()
    alerts = [a for a in (alert_result or []) if a.active_at(now) and a.routes]
    return build_snapshot(network, results, alerts, now)
