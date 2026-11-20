import argparse
import json
import os
import sys
import time

from .clock import format_clock, parse_clock
from .gtfs_static import STREET_RADIUS_M, ensure_static, load_network
from .router import Leg
from .server import serve
from .service import TransitService


def _load(args):
    started = time.perf_counter()
    path = ensure_static(args.data_dir)
    network = load_network(path, 0 if args.no_street_walking else STREET_RADIUS_M)
    print("timetable loaded in %.1f s: %d stations, %d platforms, %d edges"
          % (time.perf_counter() - started, len(network.complexes), len(network.platforms), len(network.edges)),
          file=sys.stderr)
    service = TransitService(network, live=not args.offline)
    if service.live:
        snapshot = service.refresh()
        print("live feeds: %d of %d reachable" % (snapshot.ok_feeds, len(snapshot.feeds)), file=sys.stderr)
    return network, service


def _resolve(network, text):
    matches = network.find_complexes(text)
    if not matches:
        sys.exit("no station matches %r" % text)
    if len(matches) == 1:
        return matches[0]
    exact = [m for m in matches if m.label.lower() == text.lower()]
    if len(exact) == 1:
        return exact[0]
    options = "\n  ".join("%s  [%s]" % (m.label, m.id) for m in matches[:8])
    sys.exit("%r is ambiguous, pass a more specific name or an id:\n  %s" % (text, options))


def _print_journey(network, journey, title):
    if journey is None:
        print("%s: no route found" % title)
        return
    print("%s: %d min, %d transfer%s, %d stops (%s)" % (
        title, round(journey.seconds / 60), journey.transfers, "" if journey.transfers == 1 else "s",
        journey.stops, journey.source))
    for step in journey.steps:
        if isinstance(step, Leg):
            line = network.line_label(network.line_key(step.route_id))
            first = network.platforms[step.platforms[0]]
            toward = " toward %s" % step.headsign if step.headsign else ""
            print("  %8s  %-4s %s to %s, %d stop%s%s, next train in %d min" % (
                format_clock(step.depart), line, first.name, network.station_name(step.platforms[-1]),
                step.stops, "" if step.stops == 1 else "s", toward, max(0, round(step.wait / 60))))
        else:
            kind = "walk outside" if step.street else "transfer"
            print("  %8s  %s %s to %s, %d min" % (
                "", kind, network.station_name(step.source), network.station_name(step.target),
                max(1, round(step.seconds / 60))))
    print("  %8s  arrive" % format_clock(journey.arrive))


def command_route(args):
    network, service = _load(args)
    origin = _resolve(network, args.origin)
    destination = _resolve(network, args.destination)
    closed = [line.upper() for line in args.closed]
    depart = parse_clock(args.at, time.time()) if args.at else None
    result = service.plan(origin.id, destination.id, depart, closed)
    if args.json:
        print(json.dumps(result, indent=2))
        return
    now = int(service.clock())
    snapshot = service.usable_snapshot(now)
    if snapshot is not None:
        print("built from a feed snapshot %d seconds old" % snapshot.age_at(now))
    else:
        print("no live data, using the published timetable")
    title = "%s to %s" % (origin.name, destination.name)
    base = service.router.plan(origin.id, destination.id, result["depart"], snapshot)
    _print_journey(network, base, title)
    if closed:
        changed = service.router.plan(origin.id, destination.id, result["depart"], snapshot, closed)
        print()
        _print_journey(network, changed, "same trip with %s closed" % ", ".join(closed))
        if result["delta_minutes"] is not None:
            print("difference: %+d min" % result["delta_minutes"])
    print("\nsearch took %.1f ms" % result["elapsed_ms"])


def command_stations(args):
    network, _ = _load(argparse.Namespace(data_dir=args.data_dir, offline=True, no_street_walking=True))
    needle = args.query.lower() if args.query else ""
    for complex_ in sorted(network.complexes.values(), key=lambda c: c.name):
        if needle in complex_.label.lower():
            print("%-10s %s" % (complex_.id, complex_.label))


def command_serve(args):
    network, service = _load(args)
    service.start()
    httpd = serve(service, args.host, args.port)
    print("NYC Transit Hub running at http://%s:%d  (Ctrl+C to stop)" % (args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        httpd.server_close()


def build_parser():
    parser = argparse.ArgumentParser(prog="transit_hub", description="Live NYC subway routing with a close-a-line what-if switch")
    parser.add_argument("--data-dir", default=os.path.join(os.getcwd(), "data"), help="where the timetable zip is cached")
    parser.add_argument("--offline", action="store_true", help="skip the realtime feeds and use the published timetable only")
    parser.add_argument("--no-street-walking", action="store_true", help="only allow transfers the MTA feed lists")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("serve", help="start the web app")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8000)
    run.set_defaults(handler=command_serve)

    route = commands.add_parser("route", help="plan one trip in the terminal")
    route.add_argument("origin")
    route.add_argument("destination")
    route.add_argument("--closed", nargs="*", default=[], metavar="LINE", help="lines to suspend, e.g. --closed 6 N")
    route.add_argument("--at", metavar="HH:MM", help="leave at a local time instead of now")
    route.add_argument("--json", action="store_true")
    route.set_defaults(handler=command_route)

    stations = commands.add_parser("stations", help="list station names and ids")
    stations.add_argument("query", nargs="?", default="")
    stations.set_defaults(handler=command_stations)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.handler(args)
