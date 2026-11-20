import http.client
import json
import threading
import unittest

from tests.test_realtime import feed
from tests.test_router import at, cx, scenario
from transit_hub.server import serve
from transit_hub.service import TransitService


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class ServicePlanTests(unittest.TestCase):
    def setUp(self):
        self.net = scenario()
        self.clock = FakeClock(at(7, 55))
        self.service = TransitService(self.net, clock=self.clock, live=False)
        self.origin, self.target = cx(self.net, "A"), cx(self.net, "D")

    def test_plan_reports_both_journeys_and_the_difference(self):
        result = self.service.plan(self.origin, self.target, closed=["Y"])
        self.assertEqual(result["base"]["minutes"], 21)
        self.assertEqual(result["closed"]["minutes"], 25)
        self.assertEqual(result["delta_minutes"], 4)
        self.assertEqual(result["closed_lines"], ["Y"])
        self.assertEqual([s["line"] for s in result["base"]["steps"] if s["type"] == "ride"], ["X", "Y"])
        self.assertEqual(result["base"]["transfers"], 1)
        self.assertEqual(result["status"]["mode"], "schedule")

    def test_plan_without_closure_has_no_comparison(self):
        result = self.service.plan(self.origin, self.target)
        self.assertIsNone(result["closed"])
        self.assertIsNone(result["delta_minutes"])

    def test_unreachable_when_every_route_is_closed(self):
        result = self.service.plan(self.origin, self.target, closed=["Y", "Z"])
        self.assertIsNotNone(result["base"])
        self.assertIsNone(result["closed"])

    def test_bad_input_is_rejected(self):
        with self.assertRaises(KeyError):
            self.service.plan("cx:nope", self.target)
        with self.assertRaises(KeyError):
            self.service.plan(self.origin, self.target, closed=["QQ"])
        with self.assertRaises(ValueError):
            self.service.plan(self.origin, self.origin)

    def test_a_past_departure_time_is_clamped_to_now(self):
        result = self.service.plan(self.origin, self.target, depart=at(6, 0))
        self.assertEqual(result["depart"], at(7, 55))
        later = self.service.plan(self.origin, self.target, depart=at(8, 31))
        self.assertEqual(later["depart"], at(8, 31))

    def test_lines_and_stations_listings(self):
        keys = [line["key"] for line in self.service.lines()]
        self.assertEqual(keys, ["X", "Y", "Z", "W"])
        labels = {s["id"]: s["label"] for s in self.service.stations()}
        self.assertIn(self.origin, labels)
        self.assertEqual(len(set(labels.values())), len(labels))
        data = self.service.map_data()
        self.assertEqual({route["key"] for route in data["routes"]}, set(keys))


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.net = scenario()
        self.clock = FakeClock(at(8, 0))
        self.healthy = True

        def fetch(url):
            if not self.healthy:
                raise OSError("offline")
            return feed(self.clock.now - 10)

        self.service = TransitService(self.net, fetch=fetch, clock=self.clock, live=True)

    def test_fresh_snapshot_puts_the_service_in_live_mode(self):
        self.service.refresh()
        status = self.service.status()
        self.assertEqual(status["mode"], "live")
        self.assertEqual(status["age"], 10)

    def test_a_brief_outage_keeps_the_last_good_snapshot(self):
        first = self.service.refresh()
        self.healthy = False
        self.clock.now += 60
        self.assertIs(self.service.refresh(), first)
        self.assertEqual(self.service.status()["mode"], "live")

    def test_a_long_outage_drops_back_to_the_schedule(self):
        self.service.refresh()
        self.healthy = False
        self.clock.now += 400
        self.service.refresh()
        status = self.service.status()
        self.assertEqual(status["mode"], "schedule")
        self.assertTrue(all(not feed_status["ok"] for feed_status in status["feeds"].values()))

    def test_offline_mode_never_fetches(self):
        service = TransitService(self.net, fetch=lambda url: 1 / 0, clock=self.clock, live=False)
        self.assertIsNone(service.refresh())
        self.assertFalse(service.status()["live_enabled"])


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        net = scenario()
        cls.service = TransitService(net, clock=FakeClock(at(7, 55)), live=False)
        cls.origin, cls.target = cx(net, "A"), cx(net, "D")
        cls.httpd = serve(cls.service, "127.0.0.1", 0)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_route_endpoint(self):
        status, body = self.get("/api/route?from=%s&to=%s&closed=Y" % (self.origin, self.target))
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["delta_minutes"], 4)

    def test_route_endpoint_accepts_a_clock_time(self):
        status, body = self.get("/api/route?from=%s&to=%s&time=09:00" % (self.origin, self.target))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["depart"], at(9, 0))

    def test_bad_requests_return_400_with_a_message(self):
        for query in (
            "from=%s&to=%s" % (self.origin, self.origin),
            "from=%s&to=nope" % self.origin,
            "from=%s&to=%s&closed=ZZ" % (self.origin, self.target),
            "from=%s&to=%s&time=99:99" % (self.origin, self.target),
            "from=%s" % self.origin,
        ):
            status, body = self.get("/api/route?" + query)
            self.assertEqual(status, 400, query)
            self.assertIn("error", json.loads(body))

    def test_listings_and_status(self):
        for path in ("/api/stations", "/api/lines", "/api/map", "/api/status"):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            json.loads(body)

    def test_static_files_and_path_traversal(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"NYC Transit Hub", body)
        self.assertEqual(self.get("/app.js")[0], 200)
        self.assertEqual(self.get("/../transit_hub/router.py")[0], 404)
        self.assertEqual(self.get("/missing.html")[0], 404)
        self.assertEqual(self.get("/api/unknown")[0], 404)


if __name__ == "__main__":
    unittest.main()
