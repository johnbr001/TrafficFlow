"""Top-level simulation-based timing optimizer."""
import json
from pathlib import Path

from .strategy_evaluator import evaluate_strategies
from .strategy_generator import generate_strategies


def optimize(duration=900, seed=42, traffic_profile="uniform", objective=None,
             green_values=None, output=None):
    config_path = Path(__file__).resolve().parents[2] / "config" / "optimization.json"
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    greens = green_values if green_values is not None else config["green_values_s"]
    chosen_objective = objective or config["objective"]
    strategies = generate_strategies(greens, config["yellow_duration_s"], config["all_red_duration_s"],
                                     config["min_green_s"], config["max_green_s"])
    return evaluate_strategies(strategies, duration, seed, traffic_profile,
                               chosen_objective, output, config)
