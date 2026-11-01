"""Run the Phase 2 fixed-time versus rule-based comparison."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aatc.runner import compare_controllers


def main():
    parser = argparse.ArgumentParser(description="Compare fixed-time and rule-based SUMO control")
    parser.add_argument("--duration", type=int, default=900)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", help="comparison output directory")
    parser.add_argument("--config", help="SUMO configuration shared by both runs")
    parser.add_argument("--profile", choices=("uniform", "imbalanced"), default="uniform")
    args = parser.parse_args()
    try:
        result = compare_controllers(args.duration, args.seed, args.output, args.config, args.profile)
    except (RuntimeError, OSError, ValueError) as exc:
        print("Controller comparison failed: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
