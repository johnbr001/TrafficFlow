"""Search timing plans through reproducible SUMO experiments."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aatc.optimization.simulation_optimizer import optimize


def main():
    parser = argparse.ArgumentParser(description="Optimize fixed-time signal green splits in SUMO")
    parser.add_argument("--duration", type=int, default=900)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--profile", choices=("uniform", "imbalanced"), default="uniform")
    parser.add_argument("--objective", choices=("balanced", "waiting_time", "queue", "travel_time", "throughput"))
    parser.add_argument("--green-values", help="comma-separated candidate green seconds; default comes from config")
    parser.add_argument("--output", help="directory for candidate runs and ranking reports")
    args = parser.parse_args()
    try:
        greens = ([int(value.strip()) for value in args.green_values.split(",")]
                  if args.green_values else None)
        report = optimize(args.duration, args.seed, args.profile, args.objective, greens, args.output)
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print("Signal optimization failed: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps({"experiment_id": report["experiment_id"],
                      "strategy_count": report["strategy_count"],
                      "objective": report["objective"],
                      "best_strategy": report["best_strategy"],
                      "report": str((Path(args.output) if args.output else
                                     Path(__file__).resolve().parents[1] / "experiments" / "results" /
                                     "optimization_seed_{}".format(args.seed)) / "optimization_report.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
