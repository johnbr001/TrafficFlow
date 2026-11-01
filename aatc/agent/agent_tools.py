"""Allowlisted, validated Python tools exposed to the optional traffic agent."""
import csv
import json
import os
import re
import time
import uuid
from pathlib import Path

from ..optimization.simulation_optimizer import optimize
from ..runner import compare_controllers, run
from .scenario_generator import generate_traffic_scenario
from ..city_data.regions import get_regions


def _object_schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


TOOL_SPECS = [
    {"name": "simulate_city_scenario", "description": "Run an OSM-backed SUMO subnetwork for a Korean city and date/time. Uses cached OSM, Korean holiday/local event profiles, and explicitly assumption-based generated demand; returns measured SUMO outcomes and the signal IDs in the simulated radius.",
     "parameters": _object_schema({
         "city": {"type": "string", "enum": [row["city"] for row in get_regions()]},
         "target_datetime": {"type": "string", "description": "Local ISO date/time, for example 2026-11-02T08:00"},
         "duration": {"type": "integer", "minimum": 60, "maximum": 1800},
         "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
         "radius_km": {"type": "string", "enum": ["1.0", "1.5", "2.0", "3.0"]},
     }, required=("city", "target_datetime"))},
    {"name": "evaluate_city_policy", "description": "Compare a previously trained city PPO policy with SUMO's static OSM-derived signal baseline on the same generated date/time demand. This is a simulated prototype result, not an official or field-validated signal plan.",
     "parameters": _object_schema({
         "model_name": {"type": "string", "description": "Model filename stem in aatc/rl/models; letters, digits, underscores, and hyphens only."},
         "city": {"type": "string", "enum": [row["city"] for row in get_regions()]},
         "target_datetime": {"type": "string", "description": "Local ISO date/time"},
         "duration": {"type": "integer", "minimum": 60, "maximum": 1800},
         "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
         "radius_km": {"type": "string", "enum": ["1.0", "1.5", "2.0", "3.0"]},
     }, required=("model_name", "city", "target_datetime"))},
    {"name": "run_simulation", "description": "Run an isolated SUMO simulation and return measured traffic metrics.",
     "parameters": _object_schema({
         "duration": {"type": "integer", "minimum": 30, "maximum": 1800},
         "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
         "traffic_profile": {"type": "string", "enum": ["uniform", "imbalanced"]},
         "controller": {"type": "string", "enum": ["fixed_time", "rule_based"]},
     })},
    {"name": "compare_controllers", "description": "Run fixed-time and rule-based SUMO simulations with identical seed and demand.",
     "parameters": _object_schema({
         "duration": {"type": "integer", "minimum": 30, "maximum": 900},
         "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
         "traffic_profile": {"type": "string", "enum": ["uniform", "imbalanced"]},
     })},
    {"name": "compare_ppo_with_fixed", "description": "Evaluate a saved PPO model and a fixed-time baseline in fresh SUMO runs with identical seed and demand.",
     "parameters": _object_schema({
         "model_name": {"type": "string", "description": "Model filename stem in aatc/rl/models; letters, digits, underscores, and hyphens only."},
         "duration": {"type": "integer", "minimum": 30, "maximum": 900},
         "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
         "traffic_profile": {"type": "string", "enum": ["uniform", "imbalanced"]},
     }, required=("model_name",))},
    {"name": "generate_traffic_scenario", "description": "Generate deterministic synthetic directional demand, then run it in an isolated SUMO scenario and return measured outcomes.",
     "parameters": _object_schema({
         "scenario": {"type": "string", "enum": ["north_peak", "east_peak", "high_uniform"]},
         "duration": {"type": "integer", "minimum": 30, "maximum": 1800},
         "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
     }, required=("scenario",))},
    {"name": "optimize_signal", "description": "Evaluate a bounded green-time grid in isolated SUMO experiments and return measured rankings.",
     "parameters": _object_schema({
         "duration": {"type": "integer", "minimum": 30, "maximum": 900},
         "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
         "traffic_profile": {"type": "string", "enum": ["uniform", "imbalanced"]},
         "objective": {"type": "string", "enum": ["balanced", "waiting_time", "queue", "travel_time", "throughput"]},
         "green_values": {"type": "array", "items": {"type": "integer", "enum": [20, 30, 40, 50]},
                          "minItems": 1, "maxItems": 4},
     })},
    {"name": "predict_traffic", "description": "Project queue values from a saved SUMO traffic_state.csv using a transparent short linear trend.",
     "parameters": _object_schema({
         "experiment_dir": {"type": "string", "description": "Relative directory under experiments/results; omit to use the latest agent run."},
         "window_steps": {"type": "integer", "minimum": 3, "maximum": 120},
         "horizon_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
     })},
    {"name": "inspect_experiment", "description": "Read measured metrics from a saved SUMO experiment summary JSON.",
     "parameters": _object_schema({
         "experiment_dir": {"type": "string", "description": "Relative directory under experiments/results."},
     }, required=("experiment_dir",))},
    {"name": "explain_last_transition", "description": "Read the last recorded signal phase transition and associated controller/safety log fields.",
     "parameters": _object_schema({
         "experiment_dir": {"type": "string", "description": "Relative run directory under experiments/results."},
     }, required=("experiment_dir",))},
]


class TrafficAgentTools:
    def __init__(self, results_root=None):
        self.root = Path(__file__).resolve().parents[2]
        self.results_root = Path(results_root).resolve() if results_root else (self.root / "experiments" / "results").resolve()
        self._functions = {
            "simulate_city_scenario": self._simulate_city_scenario,
            "evaluate_city_policy": self._evaluate_city_policy,
            "run_simulation": self._run_simulation,
            "compare_controllers": self._compare_controllers,
            "compare_ppo_with_fixed": self._compare_ppo_with_fixed,
            "generate_traffic_scenario": self._generate_traffic_scenario,
            "optimize_signal": self._optimize_signal,
            "predict_traffic": self._predict_traffic,
            "inspect_experiment": self._inspect_experiment,
            "explain_last_transition": self._explain_last_transition,
        }
        self.specs = {spec["name"]: spec for spec in TOOL_SPECS}

    def provider_tools(self):
        return [{"type": "function", "function": {"name": spec["name"],
                 "description": spec["description"], "parameters": spec["parameters"]}}
                for spec in TOOL_SPECS]

    def execute(self, name, arguments):
        if name not in self._functions:
            return {"error": "Unknown tool"}
        if not isinstance(arguments, dict):
            return {"error": "Tool arguments must be a JSON object"}
        try:
            self._validate(self.specs[name]["parameters"], arguments)
            return self._functions[name](**arguments)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, json.JSONDecodeError) as exc:
            return {"error": str(exc)}

    @classmethod
    def _validate(cls, schema, value):
        properties = schema.get("properties", {})
        extras = set(value) - set(properties)
        if extras and schema.get("additionalProperties") is False:
            raise ValueError("Unexpected argument(s): {}".format(", ".join(sorted(extras))))
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError("Missing required argument: {}".format(key))
        for name, item in value.items():
            definition = properties[name]
            kind = definition.get("type")
            if kind == "integer":
                if isinstance(item, bool) or not isinstance(item, int):
                    raise ValueError("{} must be an integer".format(name))
                if item < definition.get("minimum", item) or item > definition.get("maximum", item):
                    raise ValueError("{} is outside the allowed range".format(name))
                if "enum" in definition and item not in definition["enum"]:
                    raise ValueError("{} contains an unsupported value".format(name))
            elif kind == "string":
                if not isinstance(item, str):
                    raise ValueError("{} must be a string".format(name))
                if "enum" in definition and item not in definition["enum"]:
                    raise ValueError("{} is not an allowed value".format(name))
            elif kind == "array":
                if not isinstance(item, list):
                    raise ValueError("{} must be an array".format(name))
                if len(item) < definition.get("minItems", 0) or len(item) > definition.get("maxItems", len(item)):
                    raise ValueError("{} has an invalid number of items".format(name))
                child_schema = definition["items"]
                for child in item:
                    cls._validate({"type": "object", "properties": {"item": child_schema},
                                   "additionalProperties": False}, {"item": child})

    def _new_output(self, prefix):
        name = "{}_{}_{}".format(prefix, int(time.time()), uuid.uuid4().hex[:8])
        directory = self.results_root / "agent_runs" / name
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _relative_experiment_dir(self, value):
        if value is None:
            files = list((self.results_root / "agent_runs").rglob("traffic_state.csv"))
            if not files:
                raise ValueError("No saved agent SUMO run is available yet")
            candidate = max(files, key=lambda path: path.stat().st_mtime).parent
        else:
            relative = Path(value)
            if relative.is_absolute():
                raise ValueError("experiment_dir must be relative to experiments/results")
            candidate = (self.results_root / relative).resolve()
        try:
            candidate.relative_to(self.results_root)
        except ValueError:
            raise ValueError("experiment_dir must stay inside experiments/results") from None
        if not candidate.is_dir():
            raise ValueError("Experiment directory was not found")
        return candidate

    def _return_path(self, path):
        return str(Path(path).resolve().relative_to(self.results_root)).replace(os.sep, "/")

    @staticmethod
    def _metrics(summary):
        fields = ("run_id", "controller", "seed", "traffic_profile", "requested_duration_s",
                  "actual_duration_s", "mean_total_queue_length", "mean_total_waiting_time_s",
                  "average_travel_time_s", "completed_vehicles", "departed_vehicles", "green_switches")
        return {field: summary.get(field) for field in fields}

    def _run_simulation(self, duration=180, seed=42, traffic_profile="uniform", controller="fixed_time"):
        output = self._new_output("simulation")
        summary = run(duration, seed, output=output, controller=controller,
                      traffic_profile=traffic_profile)
        return {"experiment_dir": self._return_path(output), "metrics": self._metrics(summary)}

    def _simulate_city_scenario(self, city="Seoul", target_datetime=None, duration=300, seed=42, radius_km="3.0"):
        if target_datetime is None:
            raise ValueError("target_datetime is required")
        from ..city_data.simulation import run_city_simulation
        result = run_city_simulation(city, target_datetime, duration, seed, float(radius_km))
        return {"experiment_dir": self._return_path(result["experiment_dir"]),
                "scenario": result["scenario"], "metrics": result["summary"],
                "signal_ids": result["summary"].get("signal_ids", []),
                "limitation": "Generated traffic demand is assumption-based. Signal phases are SUMO's static OSM-derived baseline, not official government timing."}

    def _evaluate_city_policy(self, model_name, city="Seoul", target_datetime=None, duration=900, seed=42, radius_km="3.0"):
        if target_datetime is None:
            raise ValueError("target_datetime is required")
        name = model_name[:-4] if model_name.endswith(".zip") else model_name
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name):
            raise ValueError("model_name must be a model filename stem")
        model_path = self.root / "aatc" / "rl" / "models" / (name + ".zip")
        if not model_path.is_file():
            raise ValueError("City PPO model was not found under aatc/rl/models")
        from ..city_data.rl_workflow import evaluate_city_controller
        result = evaluate_city_controller(model_path, city, target_datetime, duration, seed,
                                          float(radius_km))
        comparison = result["comparison"]
        return {"experiment_dir": self._return_path(result["experiment_dir"]),
                "baseline": {key: comparison["baseline"].get(key) for key in (
                    "mean_total_queue_length", "mean_total_waiting_time_s", "average_travel_time_s",
                    "completed_vehicles", "departed_vehicles")},
                "ppo": comparison["ppo"], "signal_ids": comparison["signal_ids"],
                "limitations": comparison["interpretation"]}

    def _compare_controllers(self, duration=180, seed=42, traffic_profile="uniform"):
        output = self._new_output("comparison")
        report = compare_controllers(duration, seed, output, traffic_profile=traffic_profile)
        return {"experiment_dir": self._return_path(output), "comparison": report}

    def _generate_traffic_scenario(self, scenario, duration=180, seed=42):
        output = self._new_output("scenario")
        scenario_info = generate_traffic_scenario(scenario, output / "scenario" / "demand.rou.xml")
        summary = run(duration=duration, seed=seed, output=output / "results",
                      controller="fixed_time", traffic_profile=scenario,
                      route_file=scenario_info["route_file"])
        return {"experiment_dir": self._return_path(output), "scenario": scenario_info,
                "metrics": self._metrics(summary)}

    def _compare_ppo_with_fixed(self, model_name, duration=180, seed=42, traffic_profile="uniform"):
        name = model_name[:-4] if model_name.endswith(".zip") else model_name
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name):
            raise ValueError("model_name must be a model filename stem")
        model_path = self.root / "aatc" / "rl" / "models" / (name + ".zip")
        if not model_path.is_file():
            raise ValueError("PPO model was not found under aatc/rl/models")
        output = self._new_output("ppo_comparison")
        from ..rl.evaluate import evaluate
        ppo_result = evaluate(model_path, episodes=1, seed=seed, duration=duration,
                              traffic_profile=traffic_profile, output=output / "ppo_evaluation.json")
        fixed_summary = run(duration=duration, seed=seed, output=output / "fixed_time",
                            controller="fixed_time", traffic_profile=traffic_profile)
        result = {"experiment_dir": self._return_path(output), "model_name": name,
                  "seed": seed, "traffic_profile": traffic_profile,
                  "fixed_time": self._metrics(fixed_summary),
                  "ppo": ppo_result.get("average_metrics", {})}
        return result

    def _optimize_signal(self, duration=180, seed=42, traffic_profile="uniform",
                         objective="balanced", green_values=None):
        output = self._new_output("optimization")
        greens = green_values if green_values is not None else [20, 30]
        report = optimize(duration, seed, traffic_profile, objective, greens, output)
        compact = [{"strategy": row["signal_plan"], "score": row["objective_score"],
                    "mean_queue": row["mean_total_queue_length"],
                    "mean_waiting_time_s": row["mean_total_waiting_time_s"],
                    "average_travel_time_s": row["average_travel_time_s"],
                    "completed_vehicles": row["completed_vehicles"]}
                   for row in report["ranked_results"]]
        return {"experiment_dir": self._return_path(output), "objective": objective,
                "best_strategy": report["best_strategy"], "ranked_results": compact}

    def _predict_traffic(self, experiment_dir=None, window_steps=30, horizon_seconds=60):
        directory = self._relative_experiment_dir(experiment_dir)
        csv_path = directory / "traffic_state.csv"
        if not csv_path.is_file():
            raise ValueError("No traffic_state.csv exists in the selected experiment directory")
        with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise ValueError("The selected experiment has no traffic samples")
        recent = rows[-window_steps:]
        forecast = {}
        for direction in ("north", "south", "east", "west"):
            column = "{}_queue_length".format(direction)
            if column not in recent[-1]:
                raise ValueError("Queue data is missing for {}".format(direction))
            ys = [float(row[column]) for row in recent]
            xs = [float(row["simulation_time_s"]) for row in recent]
            mean_x = sum(xs) / len(xs)
            mean_y = sum(ys) / len(ys)
            variance = sum((x - mean_x) ** 2 for x in xs)
            slope = (sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / variance
                     if variance else 0.0)
            forecast[direction] = {
                "latest_queue": ys[-1],
                "trend_vehicles_per_second": slope,
                "projected_queue": max(0.0, ys[-1] + slope * horizon_seconds),
            }
        return {"source": "SUMO traffic_state.csv", "experiment_dir": self._return_path(directory),
                "samples_used": len(recent), "horizon_seconds": horizon_seconds,
                "method": "linear trend fit to recent queue samples",
                "warning": "This is a short extrapolation, not a validated traffic forecasting model.",
                "approaches": forecast}

    def _inspect_experiment(self, experiment_dir):
        directory = self._relative_experiment_dir(experiment_dir)
        summary_path = directory / "summary.json"
        if summary_path.is_file():
            with summary_path.open("r", encoding="utf-8") as handle:
                summary = json.load(handle)
            return {"experiment_dir": self._return_path(directory), "type": "simulation",
                    "metrics": self._metrics(summary), "signal_plan": summary.get("signal_plan")}
        comparison_path = directory / "comparison.json"
        if comparison_path.is_file():
            with comparison_path.open("r", encoding="utf-8") as handle:
                comparison = json.load(handle)
            return {"experiment_dir": self._return_path(directory), "type": "controller_comparison",
                    "comparison": comparison}
        optimization_path = directory / "optimization_report.json"
        if optimization_path.is_file():
            with optimization_path.open("r", encoding="utf-8") as handle:
                report = json.load(handle)
            return {"experiment_dir": self._return_path(directory), "type": "optimization",
                    "objective": report.get("objective"), "best_strategy": report.get("best_strategy"),
                    "ranked_results": [{"signal_plan": item.get("signal_plan"),
                                        "objective_score": item.get("objective_score"),
                                        "mean_total_queue_length": item.get("mean_total_queue_length"),
                                        "mean_total_waiting_time_s": item.get("mean_total_waiting_time_s"),
                                        "completed_vehicles": item.get("completed_vehicles")}
                                       for item in report.get("ranked_results", [])]}
        raise ValueError("No recognized SUMO experiment report exists in the selected directory")

    def _explain_last_transition(self, experiment_dir):
        directory = self._relative_experiment_dir(experiment_dir)
        csv_path = directory / "traffic_state.csv"
        with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        transition = None
        for previous, current in zip(rows, rows[1:]):
            if previous.get("signal_phase") != current.get("signal_phase"):
                transition = {
                    "time_s": current.get("simulation_time_s"),
                    "from_phase": previous.get("signal_phase"),
                    "to_phase": current.get("signal_phase"),
                    "signal_state": current.get("signal_state"),
                    "controller_request": current.get("controller_request"),
                    "safety_accepted": current.get("safety_accepted"),
                    "safety_reason": current.get("safety_reason"),
                }
        if transition is None:
            raise ValueError("No recorded signal transition exists in this experiment")
        return {"source": "SUMO traffic_state.csv", "experiment_dir": self._return_path(directory),
                "last_transition": transition}
