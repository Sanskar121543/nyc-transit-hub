import gzip
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .clock import parse_clock

WEB_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NYCTransitHub"

        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self._api(parsed)
            else:
                self._static(parsed.path)

        def _send(self, status, body, content_type, cache="no-store"):
            if "gzip" in self.headers.get("Accept-Encoding", "") and len(body) > 2048:
                body = gzip.compress(body, 5)
                encoding = "gzip"
            else:
                encoding = None
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status=200, cache="no-store"):
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json", cache)

        def _api(self, parsed):
            query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            try:
                if parsed.path == "/api/status":
                    self._json(service.status())
                elif parsed.path == "/api/stations":
                    self._json(service.stations(), cache="max-age=3600")
                elif parsed.path == "/api/lines":
                    self._json(service.lines())
                elif parsed.path == "/api/map":
                    self._json(service.map_data(), cache="max-age=3600")
                elif parsed.path == "/api/route":
                    self._route(query)
                else:
                    self._json({"error": "not found"}, 404)
            except KeyError as error:
                self._json({"error": str(error.args[0]) if error.args else "bad request"}, 400)
            except ValueError as error:
                self._json({"error": str(error)}, 400)

        def _route(self, query):
            origin, destination = query.get("from", ""), query.get("to", "")
            if not origin or not destination:
                raise ValueError("from and to are required")
            closed = [line for line in query.get("closed", "").split(",") if line]
            depart = None
            if query.get("time"):
                depart = parse_clock(query["time"], service.clock())
            elif query.get("at", "").isdigit():
                depart = int(query["at"])
            self._json(service.plan(origin, destination, depart, closed))

        def _static(self, path):
            name = "index.html" if path in ("", "/") else path.lstrip("/")
            full = os.path.realpath(os.path.join(WEB_ROOT, name))
            if not full.startswith(os.path.realpath(WEB_ROOT) + os.sep) or not os.path.isfile(full):
                self._send(404, b"not found", "text/plain")
                return
            content_type = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type.endswith("javascript"):
                content_type += "; charset=utf-8"
            with open(full, "rb") as handle:
                self._send(200, handle.read(), content_type, "no-cache")

    return Handler


def serve(service, host="127.0.0.1", port=8000):
    httpd = ThreadingHTTPServer((host, port), make_handler(service))
    httpd.daemon_threads = True
    return httpd
