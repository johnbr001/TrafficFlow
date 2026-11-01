"""Run a real-road SUMO scenario for a selected city subnetwork and time window."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aatc.city_data.simulation import run_city_simulation


def main():
    parser = argparse.ArgumentParser(description="Simulate a scenario over a cached OSM city subnetwork")
    parser.add_argument("--city", default="Seoul")
    parser.add_argument("--datetime", help="target local time, ISO format e.g. 2026-10-05T08:00")
    parser.add_argument("--duration", type=int, default=1800, help="simulated seconds")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--radius-km", type=float, default=1.5)
    parser.add_argument("--center-lat", type=float)
    parser.add_argument("--center-lon", type=float)
    parser.add_argument("--base-density", type=float, default=8.0,
                        help="assumed vehicles/hour/km before weekday/time/event multipliers; calibrate with counts")
    parser.add_argument("--output", help="optional result directory")
    args = parser.parse_args()
    try:
        result = run_city_simulation(args.city, args.datetime, args.duration, args.seed,
                                     args.radius_km, args.center_lat, args.center_lon,
                                     args.base_density, output=Path(args.output) if args.output else None)
    except (OSError, RuntimeError, ValueError) as exc:
        print("City SUMO simulation failed: {}".format(exc), file=sys.stderr)
        return 2
    summary = result["summary"]
    safe = {"experiment_dir": result["experiment_dir"], "network": result["network"],
            "scenario": {key: result["scenario"][key] for key in (
                "target_datetime", "day_type", "time_period", "combined_relative_demand_multiplier",
                "active_events", "assumption_note")},
            "signals": result["tls_count"],
            "metrics": {key: summary.get(key) for key in (
                "mean_total_queue_length", "mean_total_waiting_time_s", "average_travel_time_s",
                "completed_vehicles", "departed_vehicles", "actual_duration_s")},
            "demand_note": summary["demand_generation_note"],
            "signal_plan_note": summary["signal_plan_note"]}
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
