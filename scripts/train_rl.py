"""Train PPO on the SUMO traffic signal environment."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aatc.rl.train import train


def main():
    parser = argparse.ArgumentParser(description="Train the AATSC PPO signal controller")
    parser.add_argument("--timesteps", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--duration", type=int, default=900)
    parser.add_argument("--profile", choices=("uniform", "imbalanced"), default="uniform")
    parser.add_argument("--decision-interval", type=int)
    parser.add_argument("--learning-rate", type=float)
    parser.add_argument("--gamma", type=float)
    parser.add_argument("--model", help="output model path (without .zip suffix)")
    parser.add_argument("--log-dir", help="TensorBoard log directory")
    args = parser.parse_args()
    try:
        metadata = train(args.timesteps, args.seed, args.duration, args.profile,
                         args.decision_interval, args.model, args.log_dir,
                         args.learning_rate, args.gamma)
    except (RuntimeError, OSError, ValueError) as exc:
        print("PPO training failed: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
