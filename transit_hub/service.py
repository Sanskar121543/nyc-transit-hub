import threading
import time

from .realtime import fetch_snapshot, http_get
from .router import Leg, Router

REFRESH_SECONDS = 30
MAX_SNAPSHOT_AGE = 300
MAX_STALE_KEEP = 120
SEVERITY_ORDER = {"suspended": 0, "disrupted": 1, "info": 2}


class TransitService:
    def __init__(self, network, fetch=http_get, clock=time.time, live=True):
        self.network = network
        self.router = Router(network)
        self.fetch = fetch
        self.clock = clock
        self.live = live
        self.snapshot = None
        self._stop = threading.Event()
        self._thread = None
        self._labels = self._station_labels()

    def refresh(self):
        if not self.live:
            return None
        now = self.clock()
        fresh = fetch_snapshot(self.network, self.fetch, now)
        previous = self.snapshot
        if fresh.ok_feeds == 0 and previous is not None and previous.ok_feeds and previous.age_at(now) < MAX_STALE_KEEP:
            return previous
        self.snapshot = fresh
        return fresh

    def start(self, interval=REFRESH_SECONDS):
        def loop():
            while not self._stop.wait(interval):
                try:
                    self.refresh()
                except Exception:
                    pass

        self._thread = threading.Thread(target=loop, name="live-refresh", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def usable_snapshot(self, now):
        snapshot = self.snapshot
        if snapshot is None or snapshot.ok_feeds == 0 or snapshot.age_at(now) > MAX_SNAPSHOT_AGE:
            return None
        return snapshot

    def status(self):
        now = int(self.clock())
        snapshot = self.snapshot
        usable = self.usable_snapshot(now)
        data = {
            "mode": "live" if usable is not None else "schedule",
            "live_enabled": self.live,
            "feed_version": self.network.feed_version,
            "now": now,
            "age": None,
            "feeds": {},
            "alerts": 0,
        }
        if snapshot is not None:
            data["age"] = snapshot.age_at(now)
            data["feeds"] = snapshot.feeds
            data["alerts"] = len(snapshot.alerts)
        return data

    def stations(self):
        items = []
        for complex_ in self.network.complexes.values():
            items.append(
                {
                    "id": complex_.id,
                    "name": complex_.name,
                    "label": self._labels[complex_.id],
                    "routes": complex_.routes,
                    "lat": complex_.lat,
                    "lon": complex_.lon,
                }
            )
        items.sort(key=lambda item: item["label"])
        return items

    def lines(self):
        now = int(self.clock())
        snapshot = self.usable_snapshot(now)
        worst = {}
        if snapshot is not None:
            for alert in snapshot.alerts:
                for route in alert.routes:
                    key = self.network.line_key(route)
                    rank = SEVERITY_ORDER[alert.severity]
                    if key not in worst or rank < SEVERITY_ORDER[worst[key]]:
                        worst[key] = alert.severity
        items = []
        for key, label in self.network.lines.items():
            route = self.network.routes[key]
            items.append(
                {
                    "key": key,
                    "label": label,
                    "name": route.long_name,
                    "color": route.color,
                    "text_color": route.text_color,
                    "alert": worst.get(key) if worst.get(key) != "info" else None,
                }
            )
        return items

    def map_data(self):
        network = self.network
        routes = []
        for key in network.lines:
            lines = []
            for route_id in network.line_routes(key):
                lines.extend(network.shapes.get(route_id, []))
            route = network.routes[key]
            routes.append({"key": key, "label": network.line_label(key), "color": route.color, "lines": lines})
        stations = [[c.id, round(c.lat, 5), round(c.lon, 5)] for c in network.complexes.values()]
        return {"routes": routes, "stations": stations}

    def plan(self, origin, destination, depart=None, closed=()):
        network = self.network
        if origin not in network.complexes or destination not in network.complexes:
            raise KeyError("unknown station")
        unknown = [line for line in closed if line not in network.lines]
        if unknown:
            raise KeyError("unknown line %s" % ", ".join(unknown))
        now = int(self.clock())
        depart = int(depart) if depart and int(depart) > now else now
        snapshot = self.usable_snapshot(now)
        started = time.perf_counter()
        base = self.router.plan(origin, destination, depart, snapshot)
        changed = self.router.plan(origin, destination, depart, snapshot, closed) if closed else None
        elapsed = (time.perf_counter() - started) * 1000
        result = {
            "origin": self._station_json(origin),
            "destination": self._station_json(destination),
            "depart": depart,
            "closed_lines": list(closed),
            "base": self._journey_json(base, snapshot, now),
            "closed": self._journey_json(changed, snapshot, now) if changed else None,
            "delta_minutes": None,
            "elapsed_ms": round(elapsed, 1),
            "status": self.status(),
        }
        if base is not None and changed is not None:
            result["delta_minutes"] = round((changed.arrive - base.arrive) / 60)
        return result

    def _station_json(self, complex_id):
        complex_ = self.network.complexes[complex_id]
        return {"id": complex_.id, "name": complex_.name, "lat": complex_.lat, "lon": complex_.lon}

    def _station_labels(self):
        counts = {}
        for complex_ in self.network.complexes.values():
            counts[complex_.label] = counts.get(complex_.label, 0) + 1
        return {
            complex_.id: complex_.label if counts[complex_.label] == 1 else "%s #%s" % (complex_.label, complex_.id[3:])
            for complex_ in self.network.complexes.values()
        }

    def _point(self, platform_id):
        platform = self.network.platforms.get(platform_id)
        if platform is None:
            station = self.network.complexes[platform_id[4:]]
            return [station.lat, station.lon]
        return [platform.lat, platform.lon]

    def _journey_json(self, journey, snapshot, now):
        if journey is None:
            return None
        network = self.network
        steps = []
        lines_used = []
        for step in journey.steps:
            if isinstance(step, Leg):
                line = network.line_key(step.route_id)
                route = network.routes.get(step.route_id) or network.routes.get(line)
                lines_used.append(line)
                first = network.platforms[step.platforms[0]]
                steps.append(
                    {
                        "type": "ride",
                        "line": network.line_label(line),
                        "line_key": line,
                        "route_id": step.route_id,
                        "color": route.color if route else "6D6E71",
                        "text_color": route.text_color if route else "FFFFFF",
                        "name": route.long_name if route else "",
                        "from": first.name,
                        "to": network.station_name(step.platforms[-1]),
                        "toward": step.headsign,
                        "stops": step.stops,
                        "depart": step.depart,
                        "arrive": step.arrive,
                        "wait": step.wait,
                        "live": step.live,
                        "path": [self._point(p) for p in step.platforms],
                    }
                )
            else:
                steps.append(
                    {
                        "type": "walk",
                        "street": step.street,
                        "from": network.station_name(step.source),
                        "to": network.station_name(step.target),
                        "seconds": step.seconds,
                        "path": [self._point(step.source), self._point(step.target)],
                    }
                )
        return {
            "depart": journey.depart,
            "arrive": journey.arrive,
            "minutes": round(journey.seconds / 60),
            "transfers": journey.transfers,
            "stops": journey.stops,
            "walk_minutes": round(journey.street_seconds / 60),
            "source": journey.source,
            "steps": steps,
            "alerts": self._alerts_for(lines_used, snapshot),
            "search": {"settled": journey.settled, "relaxed": journey.relaxed},
        }

    def _alerts_for(self, lines, snapshot):
        if snapshot is None:
            return []
        wanted = set(lines)
        found = {}
        for alert in snapshot.alerts:
            for route in alert.routes:
                line = self.network.line_key(route)
                if line in wanted and alert.header not in found:
                    found[alert.header] = {
                        "line": self.network.line_label(line),
                        "kind": alert.kind,
                        "severity": alert.severity,
                        "header": alert.header[:240],
                    }
        ranked = sorted(found.values(), key=lambda a: SEVERITY_ORDER[a["severity"]])
        return ranked[:4]
