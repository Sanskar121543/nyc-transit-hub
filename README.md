# NYC Transit Hub

Live subway routing for New York City, plus the question every rider asks when a line goes down: what happens to my trip if it's closed?

Pick two stations and the app plans the fastest trip from the MTA's real-time train predictions. Suspend any line and the same engine plans the trip again on the network without it, then shows the minutes the closure costs you.

Python standard library only. No API key, no database, no build step.

## Run it

Requires Python 3.9 or newer.

```bash
git clone https://github.com/Sanskar121543/nyc-transit-hub.git
cd nyc-transit-hub
python3 -m transit_hub serve
```

Open http://127.0.0.1:8000. The first start downloads the MTA's published timetable (about 6 MB) into `data/` and reuses it for two weeks.

```bash
python3 -m transit_hub route "Times Sq" "Hunts Point"             # plan one trip in the terminal
python3 -m transit_hub route "Times Sq" "Hunts Point" --closed 6  # the same trip with the 6 suspended
python3 -m transit_hub route "Grand Central" "Jay St" --at 17:30  # leave at a local time
python3 -m transit_hub stations hunts                             # find station names and ids
python3 -m transit_hub serve --offline                            # timetable only, no live feeds
```

Run the tests with `python3 -m unittest discover`.

## What it does

- Plans earliest-arrival trips between any two stations, with live train times where the feeds cover them and the published timetable everywhere else.
- Re-plans with any set of lines closed and reports how much longer the trip takes, or that the station is cut off.
- Uses the MTA's listed transfers, plus short street walks between nearby stations, such as the walk from Simpson St to Hunts Point Av when the 6 is down.
- Draws the whole network on a pannable, zoomable map and highlights the chosen route against the route you would have taken.
- Falls back to the timetable by itself when feeds are unreachable. The header pill says which mode you are in.

## How it works

```
GTFS timetable ──► Network graph ─┐
                                  ├─► Time-dependent Dijkstra ─► Journey ─► JSON API ─► Browser UI
GTFS-realtime feeds ─► Snapshot ──┘        (closed lines skipped)
```

**Static data.** `gtfs_static.py` loads the MTA's GTFS timetable into a graph of platforms. Every pair of consecutive stops on a line becomes an edge that holds a sorted array of departure and arrival times for each service day. Stations that share a complex (Times Sq-42 St and Port Authority, for instance) are merged with union-find so you can start or finish at any platform in one. Lines are grouped the way riders think of them: the 6X and 7X express runs appear under the 6 and 7, and the shuttles stay separate.

**Live data.** `realtime.py` fetches the eight subway GTFS-realtime feeds and the service-alert feed in parallel every 30 seconds. The MTA publishes them as protobuf, so `protobuf.py` is a small hand-written wire-format decoder. A feed that fails to download or parse only drops its own lines out of the live view.

**Search.** `router.py` is Dijkstra over states of (platform, line you are riding), keyed by arrival time, using a hand-written binary heap (`minheap.py`) and a hand-written binary search (`search.py`) to find the next departure on an edge.

- *Time-dependent edges.* The cost of an edge is not a constant: it depends on when you arrive. Riding is allowed to wait for the next train, and trains never overtake each other, so the earliest-arrival rule keeps Dijkstra correct.
- *Live overlay.* For the next 30 minutes, an edge in a covered feed uses live predictions instead of the schedule. If a covered line has no live trips at all, it is treated as unavailable for that window rather than trusting a timetable the feed contradicts.
- *Transfers.* Changing lines on one platform needs a 60 second margin. Walking between platforms uses the MTA's own transfer times, and street walks are modeled as hub nodes at 1.4 m/s with a 1.3 detour factor, capped at 800 m.
- *Closures.* A closed line is a set of route ids that the search skips. Nothing is rebuilt, so the comparison route costs one more search.
- *Service days.* Trips after midnight belong to the previous service day and times can exceed 24:00, so each query looks at the previous, current, and next service day. New York's DST rules are computed directly without a timezone database.

**Serving.** `server.py` is a `ThreadingHTTPServer` with a small JSON API (`/api/route`, `/api/stations`, `/api/lines`, `/api/map`, `/api/status`). The UI in `web/` is plain HTML, CSS, and JavaScript with an SVG map, and is built without `innerHTML`.

## Measured on real data

On the Fall 2026 timetable (444 station complexes, 992 platforms, 2,076 line segments, 29 routes), running in a small sandbox:

| | |
|---|---|
| Timetable load | about 1.5 s, once at start |
| Query time, random station pairs | median 11 ms, 95th percentile 31 ms |
| Search states settled per query | median about 1,300 |

Example: Times Sq to Hunts Point Av takes about 38 minutes from live predictions. With the 6 closed, the engine routes you 3 to 2 and then walks nine minutes from Simpson St, for about 4 minutes more.

## Testing

62 unit tests, about a second to run:

- Heap and binary search against `heapq` and `bisect` on random data.
- Router on a small synthetic network built into a real GTFS zip: transfers, the safety margin, walking transfers, closures, after-midnight service, and the two hour boarding limit.
- A randomized cross-check of the router against a brute-force solver on hundreds of generated queries.
- Live overlay: replacement, covered lines with no trains, live-only edges, and fallback when feeds fail.
- Protobuf and feed parsing, including truncated input and garbage bytes.
- The HTTP API on a real socket: validation errors, static files, and path traversal.

## Limitations

- The search finds the earliest arrival. It does not prefer fewer transfers when a trip with more changes arrives sooner.
- You can start at any platform of the origin station at no cost, and the walk from the street to the platform is not modeled.
- Train positions in the feed are not used, only predicted stop times.
- Alerts are shown for the lines on your route but do not change the plan. Only closures you toggle do.
- A trip never waits more than two hours for a train, so very late-night queries can come back with no route.
- The Staten Island Railway is not connected to the subway and cannot be routed.
- Street walking uses straight-line distance with a detour factor, not a street map.

## Data sources

- [MTA subway timetable (GTFS)](https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip)
- [MTA GTFS-realtime feeds](https://api.mta.info/#/subwayRealTimeFeeds) and service alerts

Not affiliated with the MTA.
