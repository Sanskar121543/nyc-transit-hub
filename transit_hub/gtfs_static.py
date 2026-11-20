import csv
import datetime as dt
import io
import math
import os
import time
import urllib.request
import zipfile
from array import array
from collections import Counter

from .network import Complex, Edge, Network, Platform, Route, ServiceRule, Station

STATIC_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip"
DEFAULT_COLOR = "6D6E71"
MAX_SHAPES_PER_ROUTE = 4
STREET_RADIUS_M = 800
STREET_DETOUR = 1.3
STREET_SPEED = 1.4
HUB_SECONDS = 90


def ensure_static(data_dir, url=STATIC_URL, max_age_days=14):
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "gtfs_subway.zip")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_days * 86400:
        return path
    request = urllib.request.Request(url, headers={"User-Agent": "nyc-transit-hub"})
    partial = path + ".part"
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(partial, "wb") as out:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
    except OSError:
        if os.path.exists(path):
            return path
        raise
    os.replace(partial, path)
    return path


def _rows(archive, name):
    if name not in archive.namelist():
        return
    with archive.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))


def _date(text):
    return dt.date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def _seconds(text):
    hours, minutes, seconds = text.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def load_network(path, street_walk_m=STREET_RADIUS_M):
    network = Network()
    with zipfile.ZipFile(path) as archive:
        _load_routes(archive, network)
        _load_stops(archive, network)
        _load_calendar(archive, network)
        trips = _load_trips(archive)
        _load_stop_times(archive, network, trips)
        _load_transfers(archive, network)
        _load_shapes(archive, network, trips)
        for row in _rows(archive, "feed_info.txt"):
            network.feed_version = row.get("feed_version", "")
    _link_routes(network)
    _build_complexes(network)
    if street_walk_m:
        add_street_walks(network, street_walk_m)
    return network


def _load_routes(archive, network):
    for row in _rows(archive, "routes.txt"):
        route = Route(
            id=row["route_id"],
            short_name=row.get("route_short_name") or row["route_id"],
            long_name=row.get("route_long_name", ""),
            color=(row.get("route_color") or DEFAULT_COLOR).upper(),
            text_color=(row.get("route_text_color") or "FFFFFF").upper(),
            sort_order=int(row.get("route_sort_order") or 999),
        )
        network.routes[route.id] = route
    network.finalize_lines()


def _load_stops(archive, network):
    children = {}
    for row in _rows(archive, "stops.txt"):
        stop_id = row["stop_id"]
        lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
        parent = row.get("parent_station", "")
        if parent:
            heading = stop_id[-1] if stop_id[-1] in "NS" else ""
            network.platforms[stop_id] = Platform(stop_id, row["stop_name"], lat, lon, parent, heading)
            children.setdefault(parent, []).append(stop_id)
        elif row.get("location_type") == "1":
            network.stations[stop_id] = Station(stop_id, row["stop_name"], lat, lon, [])
    for station_id, platform_ids in children.items():
        station = network.stations.get(station_id)
        if station is not None:
            station.platforms = sorted(platform_ids)


def _load_calendar(archive, network):
    names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    for row in _rows(archive, "calendar.txt"):
        network.calendar[row["service_id"]] = ServiceRule(
            tuple(row[name] == "1" for name in names), _date(row["start_date"]), _date(row["end_date"])
        )
    for row in _rows(archive, "calendar_dates.txt"):
        network.calendar_dates.setdefault(_date(row["date"]), []).append(
            (row["service_id"], int(row["exception_type"]))
        )


def _load_trips(archive):
    trips = {}
    for row in _rows(archive, "trips.txt"):
        trips[row["trip_id"]] = (
            row["route_id"],
            row["service_id"],
            row.get("direction_id", ""),
            row.get("shape_id", ""),
            row.get("trip_headsign", ""),
        )
    return trips


def _load_stop_times(archive, network, trips):
    hops = {}
    headsigns = {}

    def flush(trip_id, stops):
        meta = trips.get(trip_id)
        if meta is None or len(stops) < 2:
            return
        route_id, service_id, headsign = meta[0], meta[1], meta[4]
        stops.sort(key=lambda stop: stop[3])
        for first, second in zip(stops, stops[1:]):
            depart, arrive = first[2], second[1]
            if arrive < depart:
                continue
            key = (first[0], second[0], route_id)
            per_service = hops.setdefault(key, {})
            counts = headsigns.setdefault(key, {})
            counts[headsign] = counts.get(headsign, 0) + 1
            bucket = per_service.get(service_id)
            if bucket is None:
                bucket = per_service[service_id] = ([], [])
            bucket[0].append(depart)
            bucket[1].append(arrive)

    with archive.open("stop_times.txt") as raw:
        reader = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        header = next(reader).strip().split(",")
        trip_i = header.index("trip_id")
        stop_i = header.index("stop_id")
        arr_i = header.index("arrival_time")
        dep_i = header.index("departure_time")
        seq_i = header.index("stop_sequence")
        current = None
        stops = []
        for line in reader:
            parts = line.rstrip("\r\n").split(",")
            trip_id = parts[trip_i]
            if trip_id != current:
                if current is not None:
                    flush(current, stops)
                current = trip_id
                stops = []
            arrival = parts[arr_i] or parts[dep_i]
            departure = parts[dep_i] or parts[arr_i]
            if not arrival:
                continue
            stops.append((parts[stop_i], _seconds(arrival), _seconds(departure), int(parts[seq_i])))
        if current is not None:
            flush(current, stops)

    for key, per_service in hops.items():
        by_service = {}
        for service_id, (departs, arrives) in per_service.items():
            pairs = sorted(zip(departs, arrives))
            by_service[service_id] = (
                array("i", [pair[0] for pair in pairs]),
                array("i", [pair[1] for pair in pairs]),
            )
        counts = headsigns[key]
        edge = Edge(key[0], key[1], key[2], by_service, max(counts, key=counts.get))
        network.edges[key] = edge
        network.out.setdefault(edge.u, []).append(edge)


def _link_routes(network):
    for edge in network.edges.values():
        for platform_id in (edge.u, edge.v):
            platform = network.platforms.get(platform_id)
            if platform is not None and platform.station in network.stations:
                network.stations[platform.station].routes.add(edge.route)


def _platforms_of(network, stop_id):
    station = network.stations.get(stop_id)
    if station is not None:
        return station.platforms
    return [stop_id] if stop_id in network.platforms else []


def _load_transfers(archive, network):
    for row in _rows(archive, "transfers.txt"):
        if row.get("transfer_type") == "3":
            continue
        seconds = int(row.get("min_transfer_time") or 0)
        sources = _platforms_of(network, row["from_stop_id"])
        targets = _platforms_of(network, row["to_stop_id"])
        for source in sources:
            for target in targets:
                if source != target:
                    network.walks.setdefault(source, []).append((target, seconds))


def _build_complexes(network):
    parent = {station_id: station_id for station_id in network.stations}

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for row_from, targets in _station_links(network):
        for target in targets:
            root_a, root_b = find(row_from), find(target)
            if root_a != root_b:
                parent[max(root_a, root_b)] = min(root_a, root_b)

    groups = {}
    for station_id in network.stations:
        groups.setdefault(find(station_id), []).append(station_id)

    for root, members in groups.items():
        members.sort(key=lambda s: (-len(network.stations[s].routes), s))
        names = []
        for member in members:
            name = network.stations[member].name
            if name not in names:
                names.append(name)
        routes = sorted(
            {route for member in members for route in network.stations[member].routes},
            key=lambda route: (network.routes[route].sort_order if route in network.routes else 999, route),
        )
        platforms = [p for member in members for p in network.stations[member].platforms]
        lat = sum(network.stations[m].lat for m in members) / len(members)
        lon = sum(network.stations[m].lon for m in members) / len(members)
        complex_id = "cx:" + min(members)
        network.complexes[complex_id] = Complex(
            complex_id, " / ".join(names[:2]), sorted(members), platforms, _display_routes(network, routes), lat, lon
        )
        for member in members:
            network.stations[member].complex_id = complex_id


def _display_routes(network, route_ids):
    shown = []
    for route_id in route_ids:
        label = network.line_label(network.line_key(route_id))
        if label not in shown:
            shown.append(label)
    return shown


def _station_links(network):
    seen = set()
    for source, targets in network.walks.items():
        a = network.platforms[source].station
        for target, _ in targets:
            b = network.platforms[target].station
            if a != b and (a, b) not in seen and a in network.stations and b in network.stations:
                seen.add((a, b))
                yield a, [b]


def _load_shapes(archive, network, trips):
    usage = Counter()
    for route_id, _service, direction, shape_id, _headsign in trips.values():
        if shape_id and direction in ("", "0"):
            usage[(route_id, shape_id)] += 1
    wanted = {}
    by_route = {}
    for (route_id, shape_id), count in usage.items():
        by_route.setdefault(route_id, []).append((count, shape_id))
    for route_id, items in by_route.items():
        items.sort(reverse=True)
        top = items[0][0]
        chosen = [shape for count, shape in items[:MAX_SHAPES_PER_ROUTE] if count * 10 >= top]
        for shape_id in chosen:
            wanted[shape_id] = route_id
    points = {}
    for row in _rows(archive, "shapes.txt"):
        shape_id = row["shape_id"]
        if shape_id in wanted:
            points.setdefault(shape_id, []).append(
                (int(row["shape_pt_sequence"]), float(row["shape_pt_lat"]), float(row["shape_pt_lon"]))
            )
    for shape_id, pts in points.items():
        pts.sort()
        line = [(round(lat, 5), round(lon, 5)) for _, lat, lon in pts[::3]]
        if line[-1] != (round(pts[-1][1], 5), round(pts[-1][2], 5)):
            line.append((round(pts[-1][1], 5), round(pts[-1][2], 5)))
        network.shapes.setdefault(wanted[shape_id], []).append(line)


def _meters(lat1, lon1, lat2, lon2):
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def add_street_walks(network, radius_m=STREET_RADIUS_M):
    """Link nearby station complexes through a street-level hub node per complex.

    Leaving a platform for the street and coming back down each cost HUB_SECONDS, so
    a street hop is never cheaper than the in-system transfers the feed already lists.
    """
    buckets = {}
    for complex_ in network.complexes.values():
        hub = "hub:" + complex_.id
        for platform in complex_.platforms:
            network.walks.setdefault(platform, []).append((hub, HUB_SECONDS))
            network.walks.setdefault(hub, []).append((platform, HUB_SECONDS))
        key = (int(complex_.lat / 0.01), int(complex_.lon / 0.012))
        buckets.setdefault(key, []).append(complex_)
    for (row, col), members in buckets.items():
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                for other in buckets.get((row + dr, col + dc), ()):
                    for here in members:
                        if here.id == other.id:
                            continue
                        meters = _meters(here.lat, here.lon, other.lat, other.lon)
                        if meters <= radius_m:
                            seconds = int(round(meters * STREET_DETOUR / STREET_SPEED))
                            network.walks["hub:" + here.id].append(("hub:" + other.id, seconds))
