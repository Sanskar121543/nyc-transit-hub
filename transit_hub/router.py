import datetime as dt
from dataclasses import dataclass, field

from .clock import local_date, service_day_start
from .minheap import MinHeap
from .search import first_at_or_after

SAME_PLATFORM_BUFFER = 60
MAX_WAIT = 2 * 3600
MAX_OFFSET = 32 * 3600
INFINITY = float("inf")


@dataclass
class Leg:
    route_id: str
    platforms: list
    depart: int
    arrive: int
    wait: int
    live: bool
    headsign: str = ""

    @property
    def stops(self):
        return len(self.platforms) - 1


@dataclass
class Walk:
    source: str
    target: str
    seconds: int
    street: bool = False


@dataclass
class Journey:
    depart: int
    arrive: int
    steps: list
    settled: int = 0
    relaxed: int = 0
    closed: tuple = field(default_factory=tuple)

    @property
    def legs(self):
        return [step for step in self.steps if isinstance(step, Leg)]

    @property
    def seconds(self):
        return self.arrive - self.depart

    @property
    def transfers(self):
        return max(0, len(self.legs) - 1)

    @property
    def street_seconds(self):
        return sum(step.seconds for step in self.steps if isinstance(step, Walk) and step.street)

    @property
    def stops(self):
        return sum(leg.stops for leg in self.legs)

    @property
    def source(self):
        flags = {leg.live for leg in self.legs}
        if flags == {True}:
            return "live"
        if flags == {False}:
            return "schedule"
        return "mixed"


class Context:
    __slots__ = ("days", "snapshot", "live", "covered", "window_end", "extra_out", "closed")

    def __init__(self, network, depart, snapshot, closed_routes):
        today = local_date(depart)
        self.days = []
        for offset in (-1, 0, 1):
            day = today + dt.timedelta(days=offset)
            service_ids = network.services_on(day)
            if service_ids:
                self.days.append((service_day_start(day), service_ids))
        self.snapshot = snapshot
        if snapshot is not None and snapshot.covered:
            self.live = snapshot.live
            self.covered = snapshot.covered
            self.window_end = snapshot.window_end
            self.extra_out = snapshot.extra_out
        else:
            self.live = {}
            self.covered = frozenset()
            self.window_end = 0
            self.extra_out = {}
        self.closed = closed_routes


class Router:
    def __init__(self, network, same_platform_buffer=SAME_PLATFORM_BUFFER):
        self.network = network
        self.same_platform_buffer = same_platform_buffer

    def plan(self, origin, destination, depart, snapshot=None, closed_lines=()):
        """Earliest-arrival journey between two station complexes, or None if unreachable."""
        network = self.network
        if origin == destination:
            raise ValueError("origin and destination are the same station")
        starts = network.complexes[origin].platforms
        goals = set(network.complexes[destination].platforms)
        closed_routes = {route for line in closed_lines for route in network.line_routes(line)}
        context = Context(network, depart, snapshot, closed_routes)
        goal, parent, counters = self._search(starts, goals, depart, context)
        if goal is None:
            return None
        journey = self._journey(goal, parent, depart)
        journey.settled, journey.relaxed = counters
        journey.closed = tuple(sorted(closed_lines))
        return journey

    def _next_trip(self, edge, ready, context):
        trip = self._earliest_trip(edge, ready, context)
        if trip is not None and trip[0] - ready > MAX_WAIT:
            return None
        return trip

    def _earliest_trip(self, edge, ready, context):
        floor = ready
        live_best = None
        if edge.route in context.covered:
            entry = context.live.get(edge.key)
            if entry is not None:
                deps, arrs = entry
                i = first_at_or_after(deps, ready)
                if i < len(deps):
                    live_best = (deps[i], arrs[i], True)
                    if deps[i] <= context.window_end:
                        return live_best
            if ready < context.window_end:
                floor = context.window_end
        scheduled = None
        for start, service_ids in context.days:
            offset = floor - start
            if offset < -MAX_WAIT or offset > MAX_OFFSET:
                continue
            for service_id in service_ids:
                table = edge.by_service.get(service_id)
                if table is None:
                    continue
                deps, arrs = table
                i = first_at_or_after(deps, offset)
                if i < len(deps):
                    depart = start + deps[i]
                    if scheduled is None or depart < scheduled[0]:
                        scheduled = (depart, start + arrs[i], False)
        if live_best is None:
            return scheduled
        if scheduled is None or live_best[0] <= scheduled[0]:
            return live_best
        return scheduled

    def _search(self, starts, goals, depart, context):
        network = self.network
        buffer = self.same_platform_buffer
        closed = context.closed
        extra_out = context.extra_out
        heap = MinHeap()
        best = {}
        parent = {}
        for platform in starts:
            state = (platform, None)
            best[state] = depart
            parent[state] = None
            heap.push(depart, state)
        settled = relaxed = 0
        while heap:
            now, state = heap.pop()
            if now > best[state]:
                continue
            settled += 1
            platform, riding = state
            if platform in goals:
                return state, parent, (settled, relaxed)
            edges = network.out.get(platform, ())
            extra = extra_out.get(platform)
            if extra:
                edges = list(edges) + extra
            for edge in edges:
                route = edge.route
                if route in closed:
                    continue
                ready = now if riding is None or riding == route else now + buffer
                trip = self._next_trip(edge, ready, context)
                if trip is None:
                    continue
                relaxed += 1
                dep, arr, is_live = trip
                target = (edge.v, route)
                if arr < best.get(target, INFINITY):
                    best[target] = arr
                    parent[target] = (state, edge, dep, arr, is_live, now)
                    heap.push(arr, target)
            for neighbor, seconds in network.walks.get(platform, ()):
                relaxed += 1
                arrive = now + seconds
                target = (neighbor, None)
                if arrive < best.get(target, INFINITY):
                    best[target] = arrive
                    parent[target] = (state, None, now, arrive, False, now)
                    heap.push(arrive, target)
        return None, parent, (settled, relaxed)

    def _journey(self, goal, parent, depart):
        hops = []
        state = goal
        while parent[state] is not None:
            previous, edge, dep, arr, is_live, ready = parent[state]
            hops.append((previous, state, edge, dep, arr, is_live, ready))
            state = previous
        hops.reverse()

        steps = []
        leg = None
        for previous, state, edge, dep, arr, is_live, ready in hops:
            if edge is None:
                leg = None
                street = previous[0].startswith("hub:") or state[0].startswith("hub:")
                if steps and isinstance(steps[-1], Walk) and steps[-1].target == previous[0]:
                    last = steps[-1]
                    last.target = state[0]
                    last.seconds += arr - dep
                    last.street = last.street or street
                else:
                    steps.append(Walk(previous[0], state[0], arr - dep, street))
                continue
            if leg is not None and leg.route_id == edge.route and leg.platforms[-1] == edge.u:
                leg.platforms.append(edge.v)
                leg.arrive = arr
                leg.live = leg.live and is_live
                continue
            leg = Leg(edge.route, [edge.u, edge.v], dep, arr, dep - ready, is_live, edge.headsign)
            steps.append(leg)
        return Journey(depart, hops[-1][4], steps)
