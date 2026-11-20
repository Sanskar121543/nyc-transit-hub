import datetime as dt
import random
import unittest

from tests.support import make_network
from transit_hub.clock import service_day_start
from transit_hub.network import Edge
from transit_hub.realtime import Snapshot
from transit_hub.router import Leg, Router, Walk

DAY = dt.date(2026, 11, 20)
BASE = service_day_start(DAY)


def at(hours, minutes=0, seconds=0):
    return BASE + hours * 3600 + minutes * 60 + seconds


def cx(network, parent):
    return network.stations[parent].complex_id


def scenario(y_first=8 * 3600 + 12 * 60):
    routes = {name: (name, name + " line", "336699") for name in "XYZW"}
    stops = {p: [p + "N"] for p in "ABCDEF"}
    trips = []
    for k in range(12):
        start = 8 * 3600 + k * 600
        trips.append(("X", "X%d" % k, [("AN", start, start), ("BN", start + 300, start + 300), ("CN", start + 600, start + 600)]))
        late = y_first + k * 600
        trips.append(("Y", "Y%d" % k, [("CN", late, late), ("DN", late + 240, late + 240)]))
    for k in range(4):
        start = 8 * 3600 + k * 1800
        trips.append(("Z", "Z%d" % k, [("AN", start, start), ("DN", start + 1200, start + 1200)]))
    for k in range(6):
        start = 8 * 3600 + 15 * 60 + k * 1800
        trips.append(("W", "W%d" % k, [("EN", start, start), ("FN", start + 300, start + 300)]))
    transfers = [("B", "E", 120), ("E", "B", 120)]
    return make_network(routes, stops, trips, transfers)


def snapshot(live=None, covered=("X",), extra_out=None, as_of=None):
    stamp = as_of if as_of is not None else at(7, 55)
    return Snapshot(stamp, stamp, live or {}, extra_out or {}, frozenset(covered), [], {})


class EarliestArrivalTests(unittest.TestCase):
    def setUp(self):
        self.net = scenario()
        self.router = Router(self.net)

    def plan(self, origin, destination, depart, **kwargs):
        return self.router.plan(cx(self.net, origin), cx(self.net, destination), depart, **kwargs)

    def test_single_line_trip_boards_the_next_train(self):
        journey = self.plan("A", "C", at(7, 55))
        self.assertEqual(journey.arrive, at(8, 10))
        leg = journey.legs[0]
        self.assertEqual((leg.route_id, leg.stops, leg.wait), ("X", 2, 300))
        self.assertEqual(journey.transfers, 0)
        self.assertEqual(journey.source, "schedule")

    def test_transfer_beats_a_slower_direct_train(self):
        journey = self.plan("A", "D", at(7, 55))
        self.assertEqual([leg.route_id for leg in journey.legs], ["X", "Y"])
        self.assertEqual(journey.arrive, at(8, 16))
        self.assertEqual(journey.transfers, 1)
        self.assertEqual(journey.legs[1].wait, 120)

    def test_same_platform_change_needs_the_safety_margin(self):
        net = scenario(y_first=8 * 3600 + 10 * 60 + 30)
        origin, target = cx(net, "A"), cx(net, "D")
        with_margin = Router(net).plan(origin, target, at(7, 55))
        self.assertEqual([leg.route_id for leg in with_margin.legs], ["Z"])
        self.assertEqual(with_margin.arrive, at(8, 20))
        no_margin = Router(net, same_platform_buffer=0).plan(origin, target, at(7, 55))
        self.assertEqual([leg.route_id for leg in no_margin.legs], ["X", "Y"])
        self.assertEqual(no_margin.arrive, at(8, 14, 30))

    def test_walking_transfer_between_stations_uses_the_feed_time(self):
        journey = self.plan("A", "F", at(7, 55))
        kinds = [type(step).__name__ for step in journey.steps]
        self.assertEqual(kinds, ["Leg", "Walk", "Leg"])
        walk = journey.steps[1]
        self.assertEqual((walk.source, walk.target, walk.seconds, walk.street), ("BN", "EN", 120, False))
        self.assertEqual(journey.arrive, at(8, 20))

    def test_closing_a_line_reroutes_or_disconnects(self):
        rerouted = self.plan("A", "D", at(7, 55), closed_lines=("Y",))
        self.assertEqual([leg.route_id for leg in rerouted.legs], ["Z"])
        self.assertEqual(rerouted.arrive, at(8, 20))
        self.assertEqual(rerouted.closed, ("Y",))
        self.assertIsNone(self.plan("A", "D", at(7, 55), closed_lines=("Y", "Z")))
        self.assertIsNone(self.plan("A", "C", at(7, 55), closed_lines=("X",)))

    def test_closing_a_line_never_makes_the_trip_faster(self):
        for lines in (("X",), ("Y",), ("Z",), ("X", "Y")):
            for target in "BCD":
                base = self.plan("A", target, at(7, 55))
                closed = self.plan("A", target, at(7, 55), closed_lines=lines)
                if closed is not None:
                    self.assertGreaterEqual(closed.arrive, base.arrive)

    def test_origin_equal_to_destination_is_rejected(self):
        with self.assertRaises(ValueError):
            self.plan("A", "A", at(8))

    def test_no_route_when_the_next_train_is_more_than_two_hours_away(self):
        self.assertIsNone(self.plan("A", "C", at(23, 30)))

    def test_the_two_hour_wait_limit_is_a_hard_edge(self):
        self.assertEqual(self.plan("A", "C", at(6, 0, 1)).arrive, at(8, 10))
        self.assertIsNone(self.plan("A", "C", at(5, 59)))


class LiveOverlayTests(unittest.TestCase):
    def setUp(self):
        self.net = scenario()
        self.router = Router(self.net)
        self.origin, self.target = cx(self.net, "A"), cx(self.net, "C")

    def test_live_trips_replace_the_schedule_inside_the_window(self):
        live = {
            ("AN", "BN", "X"): ([at(8, 7)], [at(8, 12)]),
            ("BN", "CN", "X"): ([at(8, 12, 30)], [at(8, 17)]),
        }
        journey = self.router.plan(self.origin, self.target, at(7, 55), snapshot(live))
        self.assertEqual(journey.arrive, at(8, 17))
        self.assertEqual(journey.source, "live")

    def test_covered_line_with_no_live_trips_is_unusable_until_the_window_ends(self):
        journey = self.router.plan(self.origin, self.target, at(7, 55), snapshot())
        self.assertEqual(journey.arrive, at(8, 40))
        self.assertEqual(journey.source, "schedule")

    def test_uncovered_lines_keep_using_the_schedule(self):
        journey = self.router.plan(self.origin, self.target, at(7, 55), snapshot(covered=("Y",)))
        self.assertEqual(journey.arrive, at(8, 10))

    def test_failed_feeds_fall_back_to_the_schedule(self):
        for degraded in (None, snapshot(covered=())):
            journey = self.router.plan(self.origin, self.target, at(7, 55), degraded)
            self.assertEqual(journey.arrive, at(8, 10))

    def test_departures_beyond_the_window_use_the_schedule_again(self):
        journey = self.router.plan(self.origin, self.target, at(8, 40), snapshot())
        self.assertEqual(journey.arrive, at(8, 50))

    def test_live_only_edges_can_be_used(self):
        live = {("AN", "CN", "Q"): ([at(8, 2)], [at(8, 4)])}
        extra = {"AN": [Edge("AN", "CN", "Q", {})]}
        journey = self.router.plan(self.origin, self.target, at(7, 55), snapshot(live, ("X", "Q"), extra))
        self.assertEqual(journey.arrive, at(8, 4))
        self.assertEqual(journey.legs[0].route_id, "Q")

    def test_closing_a_line_also_removes_its_live_trips(self):
        live = {("AN", "CN", "Q"): ([at(8, 2)], [at(8, 4)])}
        extra = {"AN": [Edge("AN", "CN", "Q", {})]}
        journey = self.router.plan(
            self.origin, self.target, at(7, 55), snapshot(live, ("X", "Q"), extra), closed_lines=("Q",)
        )
        self.assertEqual(journey.arrive, at(8, 40))


class ServiceDayTests(unittest.TestCase):
    def setUp(self):
        routes = {"V": ("V", "V line", "00AA00")}
        stops = {"G": ["GN"], "H": ["HN"]}
        trips = [
            ("V", "late", [("GN", 24 * 3600 + 40 * 60, 24 * 3600 + 40 * 60), ("HN", 24 * 3600 + 50 * 60, 24 * 3600 + 50 * 60)]),
            ("V", "early", [("GN", 300, 300), ("HN", 900, 900)]),
        ]
        self.net = make_network(routes, stops, trips, start="20261120", end="20261121")
        self.router = Router(self.net)
        self.origin, self.target = cx(self.net, "G"), cx(self.net, "H")
        self.friday = service_day_start(dt.date(2026, 11, 20))
        self.saturday = service_day_start(dt.date(2026, 11, 21))

    def test_trip_after_midnight_belongs_to_the_previous_service_day(self):
        journey = self.router.plan(self.origin, self.target, self.saturday + 30 * 60)
        self.assertEqual(journey.legs[0].depart, self.friday + 24 * 3600 + 40 * 60)
        self.assertEqual(journey.arrive, self.friday + 24 * 3600 + 50 * 60)

    def test_late_evening_search_finds_the_first_train_of_the_next_service_day(self):
        journey = self.router.plan(self.origin, self.target, self.friday + 23 * 3600 + 50 * 60)
        self.assertEqual(journey.legs[0].depart, self.saturday + 300)
        self.assertEqual(journey.arrive, self.saturday + 900)


class RandomizedAgainstBruteForce(unittest.TestCase):
    """Compare the heap-based search with a plain relaxation-until-stable solver."""

    @staticmethod
    def build_case(rng):
        count = rng.randint(4, 7)
        parents = ["S%d" % i for i in range(count)]
        stops = {p: [p + "N"] for p in parents}
        routes, trips = {}, []
        for r in range(rng.randint(2, 4)):
            rid = "R%d" % r
            routes[rid] = (rid, rid, "112233")
            path = rng.sample(parents, rng.randint(2, count))
            headway = rng.choice([300, 420, 600, 900])
            first = 8 * 3600 + rng.randint(0, 600)
            hops = [rng.randint(60, 400) for _ in path[1:]]
            for k in range(rng.randint(8, 14)):
                moment = first + k * headway
                times = [(path[0] + "N", moment, moment)]
                for stop, hop in zip(path[1:], hops):
                    moment += hop
                    times.append((stop + "N", moment, moment))
                trips.append((rid, "%s_%d" % (rid, k), times))
        transfers = []
        for _ in range(rng.randint(0, 3)):
            a, b = rng.sample(parents, 2)
            seconds = rng.randint(60, 300)
            transfers += [(a, b, seconds), (b, a, seconds)]
        return routes, stops, trips, transfers

    @staticmethod
    def brute_force(net, trips, origin, target, depart, buffer):
        timetable = {}
        for rid, _tid, times in trips:
            for first, second in zip(times, times[1:]):
                timetable.setdefault((first[0], second[0], rid), []).append((first[2], second[1]))
        best = {(p, None): depart for p in net.complexes[origin].platforms}
        changed = True
        while changed:
            changed = False
            for (platform, riding), now in list(best.items()):
                for edge in net.out.get(platform, ()):
                    ready = now if riding is None or riding == edge.route else now + buffer
                    options = [pair for pair in timetable[edge.key] if pair[0] >= ready]
                    if not options:
                        continue
                    arrival = min(options)[1]
                    if arrival < best.get((edge.v, edge.route), float("inf")):
                        best[(edge.v, edge.route)] = arrival
                        changed = True
                for neighbor, seconds in net.walks.get(platform, ()):
                    if now + seconds < best.get((neighbor, None), float("inf")):
                        best[(neighbor, None)] = now + seconds
                        changed = True
        goals = set(net.complexes[target].platforms)
        arrivals = [t for (platform, _), t in best.items() if platform in goals]
        return min(arrivals) if arrivals else None

    def test_matches_brute_force_and_returns_consistent_journeys(self):
        rng = random.Random(11)
        compared = 0
        for _ in range(60):
            routes, stops, trips, transfers = self.build_case(rng)
            net = make_network(routes, stops, trips, transfers)
            router = Router(net)
            ids = list(net.complexes)
            for _ in range(6):
                if len(ids) < 2:
                    continue
                origin, target = rng.sample(ids, 2)
                depart = BASE + 8 * 3600 + rng.randint(-600, 3000)
                journey = router.plan(origin, target, depart)
                expected = self.brute_force(net, trips, origin, target, depart - BASE, router.same_platform_buffer)
                if expected is None:
                    self.assertIsNone(journey)
                    continue
                self.assertIsNotNone(journey)
                self.assertEqual(journey.arrive, BASE + expected)
                self.assertGreaterEqual(journey.depart, depart)
                self.check_consistency(net, journey)
                compared += 1
        self.assertGreater(compared, 100)

    def check_consistency(self, net, journey):
        clock = journey.depart
        for step in journey.steps:
            if isinstance(step, Leg):
                self.assertGreaterEqual(step.depart, clock)
                self.assertGreaterEqual(step.wait, 0)
                self.assertGreater(step.arrive, step.depart - 1)
                for first, second in zip(step.platforms, step.platforms[1:]):
                    self.assertIn((first, second, step.route_id), net.edges)
                clock = step.arrive
            else:
                self.assertIsInstance(step, Walk)
                clock += step.seconds
        self.assertLessEqual(clock, journey.arrive)


if __name__ == "__main__":
    unittest.main()
