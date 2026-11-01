"""Command-line entry point for the Phase 1 SUMO run."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aatc.runner import run


def main():
    parser = argparse.ArgumentParser(description="Run the AATSC Phase 1 SUMO intersection")
    parser.add_argument("--duration", type=int, default=900, help="simulation duration in seconds")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--gui", action="store_true", help="use sumo-gui")
    parser.add_argument("--output", help="output directory for CSV and JSON results")
    parser.add_argument("--config", help="path to a SUMO configuration file")
    parser.add_argument("--controller", choices=("fixed_time", "rule_based"), default="fixed_time")
    parser.add_argument("--profile", choices=("uniform", "imbalanced"), default="uniform")
    args = parser.parse_args()
    try:
        result = run(args.duration, args.seed, args.gui, args.output, args.config,
                     args.controller, args.profile)
    except (RuntimeError, OSError, ValueError) as exc:
        print("Simulation could not run: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
