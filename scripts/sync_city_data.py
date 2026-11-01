"""Cache OSM roads/POIs and official Korean intersection/signal data locally."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aatc.city_data.events import annual_calendar
from aatc.city_data.sync import DEFAULT_CITY_ROOT, sync_city_data


def main():
    parser = argparse.ArgumentParser(description="Sync OSM and CrossRoadInfoService data for a supported Korean city")
    parser.add_argument("--city", default="Seoul", help="city name, Korean name, or supported API region code")
    parser.add_argument("--refresh-osm", action="store_true", help="download the road map and POIs again")
    parser.add_argument("--refresh-signals", action="store_true", help="fetch official intersection and phase records again")
    parser.add_argument("--match-distance-m", type=float, default=80.0,
                        help="maximum OSM/API coordinate matching distance (default: 80 m)")
    parser.add_argument("--calendar-year", type=int, help="also save the selected year's holiday/event calendar")
    parser.add_argument("--data-root", help="override data/cities storage directory")
    args = parser.parse_args()
    try:
        result = sync_city_data(args.city, args.refresh_osm, args.refresh_signals,
                                Path(args.data_root) if args.data_root else None,
                                args.match_distance_m)
        if args.calendar_year is not None:
            calendar = annual_calendar(result["city"]["city"], args.calendar_year)
            calendar_dir = Path(result["directory"]) / "events"
            calendar_dir.mkdir(parents=True, exist_ok=True)
            with (calendar_dir / "calendar_{}.json".format(args.calendar_year)).open("w", encoding="utf-8") as handle:
                json.dump(calendar, handle, ensure_ascii=False, indent=2)
        safe_result = {"city": result["city"], "directory": result["directory"],
                       "cached_osm": result["cached_osm"], "cached_api": result["cached_api"],
                       "metadata": result["metadata"], "warnings": result["warnings"]}
        print(json.dumps(safe_result, ensure_ascii=False, indent=2))
        return 0 if result["metadata"].get("osm_junction_count") or result["crossroads"] else 2
    except (OSError, RuntimeError, ValueError) as exc:
        print("City data sync failed: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
