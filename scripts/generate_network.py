"""Create simulation/intersection.net.xml using the installed SUMO netconvert."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aatc.network_generator import generate_network


if __name__ == "__main__":
    try:
        print("Generated {}".format(generate_network()))
    except RuntimeError as exc:
        print("Network generation failed: {}".format(exc), file=sys.stderr)
        raise SystemExit(2)
