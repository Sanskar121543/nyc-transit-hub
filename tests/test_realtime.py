import unittest

from tests.support import bytes_field, make_network, varint_field
from transit_hub import realtime
from transit_hub.protobuf import DecodeError

BASE = 1_795_000_000


def stop_update(stop_id, arrival=None, departure=None, relationship=None):
    body = bytes_field(4, stop_id)
    if arrival is not None:
        body += bytes_field(2, varint_field(2, arrival))
    if departure is not None:
        body += bytes_field(3, varint_field(2, departure))
    if relationship is not None:
        body += varint_field(5, relationship)
    body += bytes_field(1001, bytes_field(1, "2") + bytes_field(2, "2"))
    return bytes_field(2, body)


def trip_entity(entity_id, trip_id, route_id, stops, canceled=False):
    descriptor = bytes_field(1, trip_id) + bytes_field(5, route_id) + bytes_field(1001, varint_field(3, 1))
    if canceled:
        descriptor += varint_field(4, 3)
    update = bytes_field(1, descriptor) + b"".join(stops)
    return bytes_field(2, bytes_field(1, entity_id) + bytes_field(3, update))


def feed(timestamp, *entities):
    header = bytes_field(1, "2.0") + varint_field(3, timestamp)
    return bytes_field(1, header) + b"".join(entities)


def alert_entity(entity_id, routes, kind, texts, periods=()):
    body = b""
    for start, end in periods:
        body += bytes_field(1, varint_field(1, start) + varint_field(2, end))
    for route in routes:
        body += bytes_field(5, bytes_field(2, route))
    translations = b"".join(bytes_field(1, bytes_field(1, text) + bytes_field(2, lang)) for lang, text in texts)
    body += bytes_field(10, translations)
    body += bytes_field(1001, varint_field(1, 1) + bytes_field(3, kind))
    return bytes_field(2, bytes_field(1, entity_id) + bytes_field(5, body))


def small_network():
    routes = {"X": ("X", "X Local", "EE352E")}
    stops = {"A": ["AN"], "B": ["BN"], "C": ["CN"]}
    trips = [("X", "X0", [("AN", 28800, 28800), ("BN", 29100, 29100), ("CN", 29400, 29400)])]
    return make_network(routes, stops, trips)


class ParseFeedTests(unittest.TestCase):
    def test_reads_header_timestamp_trips_and_stop_times(self):
        data = feed(
            BASE,
            trip_entity("e1", "T1", "X", [stop_update("AN", BASE + 60, BASE + 90), stop_update("BN", BASE + 200)]),
        )
        result = realtime.parse_feed(data)
        self.assertEqual(result.timestamp, BASE)
        self.assertEqual(len(result.trips), 1)
        trip = result.trips[0]
        self.assertEqual((trip.trip_id, trip.route_id, trip.canceled), ("T1", "X", False))
        self.assertEqual([s.stop_id for s in trip.stops], ["AN", "BN"])
        self.assertEqual((trip.stops[0].arrival, trip.stops[0].departure), (BASE + 60, BASE + 90))
        self.assertEqual((trip.stops[1].arrival, trip.stops[1].departure), (BASE + 200, BASE + 200))

    def test_canceled_trips_and_skipped_stops_are_flagged(self):
        data = feed(
            BASE,
            trip_entity("e1", "T1", "X", [stop_update("AN", BASE, BASE, relationship=1)], canceled=True),
        )
        trip = realtime.parse_feed(data).trips[0]
        self.assertTrue(trip.canceled)
        self.assertTrue(trip.stops[0].skipped)

    def test_entities_without_trip_updates_are_ignored(self):
        vehicle_only = bytes_field(2, bytes_field(1, "v1") + bytes_field(4, varint_field(1, 7)))
        self.assertEqual(realtime.parse_feed(feed(BASE, vehicle_only)).trips, [])

    def test_truncated_input_raises(self):
        data = feed(BASE, trip_entity("e1", "T1", "X", [stop_update("AN", BASE)]))
        with self.assertRaises(DecodeError):
            realtime.parse_feed(data[:-3])


class ParseAlertsTests(unittest.TestCase):
    def test_reads_routes_kind_periods_and_prefers_plain_english(self):
        data = feed(
            BASE,
            alert_entity(
                "a1",
                ["6", "7"],
                "Planned - Part Suspended",
                [("en-html", "<b>html</b>"), ("en", "No 6 trains between A and B")],
                periods=[(BASE - 100, BASE + 100)],
            ),
        )
        alert = realtime.parse_alerts(data)[0]
        self.assertEqual(alert.routes, frozenset({"6", "7"}))
        self.assertEqual(alert.kind, "Planned - Part Suspended")
        self.assertEqual(alert.header, "No 6 trains between A and B")
        self.assertEqual(alert.severity, "suspended")
        self.assertTrue(alert.active_at(BASE))
        self.assertFalse(alert.active_at(BASE + 101))

    def test_open_ended_period_and_missing_period(self):
        open_ended = realtime.Alert("a", frozenset({"A"}), "Delays", "x", ((BASE, 0),))
        self.assertTrue(open_ended.active_at(BASE + 10**6))
        self.assertTrue(realtime.Alert("b", frozenset({"A"}), "Delays", "x", ()).active_at(BASE))
        self.assertEqual(open_ended.severity, "disrupted")

    def test_alert_kinds_map_to_severity(self):
        make = lambda kind: realtime.Alert("a", frozenset(), kind, "", ())
        self.assertEqual(make("No Scheduled Service").severity, "suspended")
        self.assertEqual(make("Planned - Stops Skipped").severity, "disrupted")
        self.assertEqual(make("Station Notice").severity, "info")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.network = small_network()

    def test_live_hops_chain_consecutive_stops(self):
        trips = [
            realtime.TripUpdate(
                "T1",
                "X",
                False,
                [realtime.StopUpdate("AN", 100, 110), realtime.StopUpdate("BN", 200, 210), realtime.StopUpdate("CN", 300, 300)],
            )
        ]
        hops = realtime._live_hops(self.network, trips)
        self.assertEqual(hops[("AN", "BN", "X")], [(110, 200)])
        self.assertEqual(hops[("BN", "CN", "X")], [(210, 300)])

    def test_unknown_skipped_and_canceled_are_dropped(self):
        trips = [
            realtime.TripUpdate(
                "T1",
                "X",
                False,
                [
                    realtime.StopUpdate("AN", 100, 110),
                    realtime.StopUpdate("ZZN", 150, 150),
                    realtime.StopUpdate("BN", 200, 210, skipped=True),
                    realtime.StopUpdate("CN", 300, 300),
                ],
            ),
            realtime.TripUpdate("T2", "X", True, [realtime.StopUpdate("AN", 100, 110), realtime.StopUpdate("BN", 200, 200)]),
        ]
        hops = realtime._live_hops(self.network, trips)
        self.assertEqual(list(hops), [("AN", "CN", "X")])
        self.assertEqual(hops[("AN", "CN", "X")], [(110, 300)])

    def test_build_snapshot_records_coverage_window_and_new_edges(self):
        good = realtime.FeedResult(
            BASE,
            [realtime.TripUpdate("T1", "X", False, [realtime.StopUpdate("AN", 100, 110), realtime.StopUpdate("CN", 300, 300)])],
        )
        results = {"ACE": (good, None), "G": (None, "OSError: down")}
        snapshot = realtime.build_snapshot(self.network, results, [], BASE + 5)
        self.assertEqual(snapshot.as_of, BASE)
        self.assertEqual(snapshot.window_end, BASE + realtime.LIVE_WINDOW)
        self.assertIn("X", snapshot.covered)
        self.assertIn("A", snapshot.covered)
        self.assertNotIn("G", snapshot.covered)
        self.assertEqual(snapshot.ok_feeds, 1)
        self.assertFalse(snapshot.feeds["G"]["ok"])
        self.assertEqual([e.key for e in snapshot.extra_out["AN"]], [("AN", "CN", "X")])
        self.assertEqual(snapshot.age_at(BASE + 30), 30)

    def test_fetch_snapshot_survives_individual_feed_failures(self):
        feed_urls = [url for url, _ in realtime.FEEDS.values()]
        working = feed(BASE, trip_entity("e", "T1", "X", [stop_update("AN", BASE + 10, BASE + 20), stop_update("BN", BASE + 90)]))

        def fetch(url):
            if url == feed_urls[0]:
                return working
            raise OSError("connection refused")

        snapshot = realtime.fetch_snapshot(self.network, fetch, now=BASE + 3)
        self.assertEqual(snapshot.ok_feeds, 1)
        self.assertEqual(len(snapshot.feeds), len(realtime.FEEDS))
        self.assertEqual(snapshot.live[("AN", "BN", "X")], ([BASE + 20], [BASE + 90]))
        failed = [s for s in snapshot.feeds.values() if not s["ok"]]
        self.assertTrue(all("connection refused" in s["error"] for s in failed))

    def test_garbage_bytes_count_as_a_failed_feed(self):
        snapshot = realtime.fetch_snapshot(self.network, lambda url: b"<html>not protobuf", now=BASE)
        self.assertEqual(snapshot.ok_feeds, 0)
        self.assertEqual(snapshot.covered, frozenset())


if __name__ == "__main__":
    unittest.main()
