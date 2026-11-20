import csv
import io
import os
import tempfile
import zipfile

from transit_hub.gtfs_static import load_network


def hms(seconds):
    return "%02d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def _csv(header, rows):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def build_zip(path, routes, stops, trips, transfers=(), start="20260101", end="20261231", days=(1,) * 7):
    """Write a minimal GTFS zip.

    routes: {route_id: (short_name, long_name, color)}
    stops: {parent_id: [platform_id, ...]}
    trips: [(route_id, trip_id, [(stop_id, arrival_seconds, departure_seconds), ...]), ...]
    transfers: [(from_parent, to_parent, seconds), ...]
    """
    files = {
        "routes.txt": _csv(
            ["route_id", "route_short_name", "route_long_name", "route_color", "route_text_color", "route_sort_order"],
            [(rid, short, long_, color, "FFFFFF", i) for i, (rid, (short, long_, color)) in enumerate(routes.items())],
        ),
        "calendar.txt": _csv(
            ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"],
            [("Weekday", *days, start, end)],
        ),
        "calendar_dates.txt": _csv(["service_id", "date", "exception_type"], []),
    }
    stop_rows = []
    for index, (parent, platforms) in enumerate(stops.items()):
        lat, lon = 40.70 + 0.01 * index, -74.0
        stop_rows.append((parent, "Station " + parent, lat, lon, 1, ""))
        for platform in platforms:
            stop_rows.append((platform, "Station " + parent, lat, lon, "", parent))
    files["stops.txt"] = _csv(["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"], stop_rows)
    files["trips.txt"] = _csv(
        ["route_id", "trip_id", "service_id", "trip_headsign", "direction_id", "shape_id"],
        [(rid, tid, "Weekday", "toward " + times[-1][0], 0, "") for rid, tid, times in trips],
    )
    stop_time_rows = []
    for _rid, tid, times in trips:
        for sequence, (stop, arrive, depart) in enumerate(times, 1):
            stop_time_rows.append((tid, stop, hms(arrive), hms(depart), sequence))
    files["stop_times.txt"] = _csv(["trip_id", "stop_id", "arrival_time", "departure_time", "stop_sequence"], stop_time_rows)
    files["transfers.txt"] = _csv(
        ["from_stop_id", "to_stop_id", "transfer_type", "min_transfer_time"],
        [(a, b, 2, seconds) for a, b, seconds in transfers],
    )
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)


def make_network(routes, stops, trips, transfers=(), **kwargs):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "feed.zip")
        build_zip(path, routes, stops, trips, transfers, **kwargs)
        return load_network(path, street_walk_m=0)


def encode_varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def varint_field(number, value):
    return encode_varint(number << 3) + encode_varint(value)


def bytes_field(number, payload):
    if isinstance(payload, str):
        payload = payload.encode()
    return encode_varint(number << 3 | 2) + encode_varint(len(payload)) + payload
