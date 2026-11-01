"""Compare a saved joint PPO policy against SUMO's static baseline."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aatc.city_data.rl_workflow import evaluate_city_controller


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="trained model .zip path")
    parser.add_argument("--city", default="Seoul")
    parser.add_argument("--datetime", dest="target_datetime", default="2026-11-02T08:00")
    parser.add_argument("--duration", type=int, default=900)
    parser.add_argument("--radius-km", type=float, default=3.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--density", type=float, default=8.0)
    parser.add_argument("--output", help="result directory")
    args = parser.parse_args()
    try:
        result = evaluate_city_controller(args.model, args.city,
                                          datetime.fromisoformat(args.target_datetime),
                                          args.duration, args.seed, args.radius_km,
                                          args.density, args.output)
    except (RuntimeError, OSError, ValueError) as exc:
        print("City PPO evaluation failed: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
