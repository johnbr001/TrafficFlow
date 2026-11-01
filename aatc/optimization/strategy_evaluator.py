"""Evaluate independent timing candidates and rank measured SUMO outcomes."""
import csv
import json
from pathlib import Path

from ..runner import run
from .scenario_builder import build_scenario


def score_metrics(metrics, objective="balanced", config=None):
    if objective not in ("balanced", "waiting_time", "queue", "travel_time", "throughput"):
        raise ValueError("Unknown optimization objective: {}".format(objective))
    normalizers = {"waiting_seconds": 300.0, "queue_vehicles": 30.0,
                   "travel_time_seconds": 300.0, "throughput_vehicles": 300.0}
    weights = {"waiting": 0.4, "queue": 0.3, "travel_time": 0.2, "throughput": 0.1}
    if config:
        normalizers.update(config.get("score_normalizers", {}))
        weights.update(config.get("balanced_weights", {}))
    if any(value <= 0 for value in normalizers.values()):
        raise ValueError("Score normalizers must be positive")
    if objective == "waiting_time":
        return float(metrics["mean_total_waiting_time_s"])
    if objective == "queue":
        return float(metrics["mean_total_queue_length"])
    travel = metrics.get("average_travel_time_s")
    if objective == "travel_time":
        return float(travel) if travel is not None else normalizers["travel_time_seconds"] * 2.0
    if objective == "throughput":
        return -float(metrics["completed_vehicles"])
    travel_cost = float(travel) / normalizers["travel_time_seconds"] if travel is not None else 2.0
    return (weights["waiting"] * float(metrics["mean_total_waiting_time_s"]) / normalizers["waiting_seconds"]
            + weights["queue"] * float(metrics["mean_total_queue_length"]) / normalizers["queue_vehicles"]
            + weights["travel_time"] * travel_cost
            - weights["throughput"] * float(metrics["completed_vehicles"]) / normalizers["throughput_vehicles"])


def evaluate_strategies(strategies, duration=900, seed=42, traffic_profile="uniform",
                        objective="balanced", output=None, score_config=None):
    if not strategies:
        raise ValueError("At least one strategy is required")
    if duration <= 0:
        raise ValueError("duration must be positive")
    root = Path(__file__).resolve().parents[2]
    output_dir = Path(output) if output else root / "experiments" / "results" / "optimization_seed_{}".format(seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    evaluations = []
    for strategy in strategies:
        strategy_id = strategy["strategy_id"]
        candidate_dir = output_dir / strategy_id
        scenario_dir = candidate_dir / "scenario"
        result_dir = candidate_dir / "results"
        config_path = build_scenario(strategy, traffic_profile, scenario_dir)
        summary = run(duration=duration, seed=seed, output=result_dir, config=config_path,
                      controller="fixed_time", traffic_profile=traffic_profile, signal_plan=strategy,
                      route_file=config_path.parent / ("demand.rou.xml" if traffic_profile == "uniform"
                                                       else "demand_imbalanced.rou.xml"))
        row = dict(summary)
        row["objective"] = objective
        row["objective_score"] = score_metrics(summary, objective, score_config)
        row["scenario_dir"] = str(scenario_dir.resolve())
        evaluations.append(row)
    evaluations.sort(key=lambda item: item["objective_score"])
    report = {
        "experiment_id": "OPT_SEED_{}_{}".format(seed, traffic_profile.upper()),
        "duration_s": duration,
        "seed": seed,
        "traffic_profile": traffic_profile,
        "objective": objective,
        "strategy_count": len(evaluations),
        "best_strategy": evaluations[0]["signal_plan"],
        "ranked_results": evaluations,
    }
    with (output_dir / "optimization_report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    fields = ["rank", "strategy_id", "green_ns_s", "green_ew_s", "yellow_s", "all_red_s",
              "objective_score", "mean_total_queue_length", "mean_total_waiting_time_s",
              "average_travel_time_s", "completed_vehicles", "departed_vehicles", "green_switches"]
    with (output_dir / "optimization_ranking.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rank, item in enumerate(evaluations, 1):
            plan = item["signal_plan"]
            writer.writerow({"rank": rank, "strategy_id": plan["strategy_id"],
                             "green_ns_s": plan["green_ns_s"], "green_ew_s": plan["green_ew_s"],
                             "yellow_s": plan["yellow_s"], "all_red_s": plan["all_red_s"],
                             "objective_score": item["objective_score"],
                             "mean_total_queue_length": item["mean_total_queue_length"],
                             "mean_total_waiting_time_s": item["mean_total_waiting_time_s"],
                             "average_travel_time_s": item["average_travel_time_s"],
                             "completed_vehicles": item["completed_vehicles"],
                             "departed_vehicles": item["departed_vehicles"],
                             "green_switches": item["green_switches"]})
    return report
