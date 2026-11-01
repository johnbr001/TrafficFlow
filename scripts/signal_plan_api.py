"""Read-only HTTP API serving saved simulated signal-plan proposal JSON."""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aatc.city_data.public_plan import build_public_signal_plan, find_city_run


class SignalPlanHandler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = unquote(urlparse(self.path).path).strip("/")
        if path in ("health", "api/v1/health"):
            self._send_json(200, {"status": "ok", "service": "simulated-signal-plan-prototype"})
            return
        parts = path.split("/")
        # /api/v1/cities/{city}/signal-plans/{latest|run-id}
        if len(parts) != 6 or parts[:3] != ["api", "v1", "cities"] or parts[4] != "signal-plans":
            self._send_json(404, {"error": "not_found", "message": "See /api/v1/cities/{city}/signal-plans/latest"})
            return
        city, requested = parts[3], parts[5]
        run_id = None if requested == "latest" else requested
        found = find_city_run(city=city, run_id=run_id)
        if not found:
            self._send_json(404, {"error": "simulation_not_found", "city": city,
                                  "run_id": run_id})
            return
        report, report_path = found
        city_dir = ROOT / "data" / "cities" / city.casefold().replace(" ", "_")
        payload = build_public_signal_plan(report, report_path, city_dir)
        self._send_json(200, payload)

    def log_message(self, fmt, *args):
        print("{} - {}".format(self.address_string(), fmt % args))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address; use 0.0.0.0 only on a trusted network.")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), SignalPlanHandler)
    print("Signal-plan proposal API listening at http://{}:{}/api/v1/cities/seoul/signal-plans/latest".format(
        args.host, args.port))
    print("This prototype serves simulated proposals; it does not submit changes to a government system.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping signal-plan API.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
