"""Build a SUMO subnetwork around a selected point from cached OSM city data."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aatc.city_data.sumo_network import build_city_network


def main():
    parser = argparse.ArgumentParser(description="Convert a selected OSM city subnetwork into SUMO XML")
    parser.add_argument("--city", default="Seoul")
    parser.add_argument("--radius-km", type=float, default=2.0,
                        help="simulation radius; limit it to a manageable urban subnetwork")
    parser.add_argument("--center-lat", type=float)
    parser.add_argument("--center-lon", type=float)
    parser.add_argument("--data-root", help="override the local city-data root")
    parser.add_argument("--output", help="override generated SUMO scenario directory")
    args = parser.parse_args()
    try:
        result = build_city_network(args.city, args.radius_km, args.center_lat, args.center_lon,
                                    Path(args.data_root) if args.data_root else None,
                                    Path(args.output) if args.output else None)
    except (OSError, RuntimeError, ValueError) as exc:
        print("City network generation failed: {}".format(exc), file=sys.stderr)
        return 2
    summary = {key: result[key] for key in ("directory", "network", "sumocfg", "mapping")}
    summary["metadata"] = {key: result["metadata"][key] for key in (
        "city", "region_code", "osm_nodes_in_subnetwork", "osm_directed_edges",
        "osm_tagged_signal_count", "government_matched_signal_count",
        "government_added_signal_count", "sumo_signal_count", "radius_km")}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
