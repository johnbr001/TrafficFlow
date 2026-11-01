"""Evaluate a saved PPO model in SUMO."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aatc.rl.evaluate import evaluate


def main():
    parser = argparse.ArgumentParser(description="Evaluate an AATSC PPO model")
    parser.add_argument("--model", required=True)
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--duration", type=int, default=900)
    parser.add_argument("--profile", choices=("uniform", "imbalanced"), default="uniform")
    parser.add_argument("--decision-interval", type=int)
    parser.add_argument("--output", help="result JSON path")
    args = parser.parse_args()
    try:
        result = evaluate(args.model, args.episodes, args.seed, args.duration,
                          args.profile, args.decision_interval, args.output)
    except (RuntimeError, OSError, ValueError) as exc:
        print("PPO evaluation failed: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
