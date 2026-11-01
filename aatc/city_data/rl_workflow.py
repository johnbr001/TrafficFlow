"""Train and compare a joint PPO controller on a cached SUMO city scenario."""
import json
import shutil
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from .regions import resolve_region
from .simulation import run_city_simulation
from .rl_environment import CityTrafficSignalEnv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = PROJECT_ROOT / "aatc" / "rl" / "models"
RESULT_ROOT = PROJECT_ROOT / "experiments" / "results" / "city_runs"


def _build_reference_scenario(city, target_datetime, duration, seed, radius_km,
                              base_density_veh_per_hour_km, demand_estimate=None):
    # This produces the same SUMO demand inputs used for the fixed-time reference
    # and returns its saved config, route file, and measured baseline metrics.
    baseline = run_city_simulation(city, target_datetime, duration, seed, radius_km,
                                  base_density_veh_per_hour_km=base_density_veh_per_hour_km,
                                  demand_estimate=demand_estimate)
    summary_path = Path(baseline["experiment_dir"]) / "summary.json"
    with summary_path.open("r", encoding="utf-8") as handle:
        baseline_summary = json.load(handle)
    return baseline, baseline_summary, Path(baseline["experiment_dir"]) / "scenario.sumocfg"


def _make_training_inputs(sumocfg, route_command):
    """Keep random training routes separate from the paired fixed-time baseline."""
    training_dir = sumocfg.parent / "training_routes"
    training_dir.mkdir(parents=True, exist_ok=True)
    training_cfg = training_dir / "training.sumocfg"
    tree = ET.parse(str(sumocfg))
    root = tree.getroot()
    route_item = root.find(".//route-files")
    if route_item is None:
        raise ValueError("Scenario config has no route-files entry")
    route_item.set("value", str((training_dir / "demand.rou.xml").resolve()))
    tree.write(str(training_cfg), encoding="utf-8", xml_declaration=True)
    command = list(route_command or [])
    if "--route-file" not in command:
        raise ValueError("SUMO scenario does not provide its route-generation command")
    command[command.index("--route-file") + 1] = str((training_dir / "demand.rou.xml").resolve())
    return training_cfg, command


def train_city_controller(city="Seoul", target_datetime=None, duration=900, seed=42,
                          radius_km=3.0, timesteps=2048,
                          base_density_veh_per_hour_km=8.0, output=None,
                          decision_interval_s=5, min_green_s=10, max_green_s=60,
                          demand_estimate=None):
    if int(timesteps) <= 0:
        raise ValueError("timesteps must be positive")
    region = resolve_region(city)
    target_datetime = target_datetime or datetime.now().replace(second=0, microsecond=0)
    if isinstance(target_datetime, str):
        target_datetime = datetime.fromisoformat(target_datetime)
    baseline, baseline_summary, sumocfg = _build_reference_scenario(
        region["city"], target_datetime, duration, seed, radius_km,
        base_density_veh_per_hour_km, demand_estimate)
    training_cfg, randomized_route_command = _make_training_inputs(
        sumocfg, baseline_summary.get("route_generation_command"))
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    model_path = Path(output) if output else MODEL_ROOT / "city_{}_{}km_ppo_{}".format(
        region["code"], str(radius_km).replace(".", "p"), int(seed))
    model_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from stable_baselines3 import PPO
    except ImportError as exc:
        raise RuntimeError("City PPO requires Stable-Baselines3 and PyTorch from requirements.txt") from exc
    env = CityTrafficSignalEnv(training_cfg, duration=duration,
                               decision_interval=decision_interval_s,
                               min_green=min_green_s, max_green=max_green_s,
                               seed=seed, record_samples=False,
                               route_generation_command=randomized_route_command)
    try:
        model = PPO("MlpPolicy", env, seed=int(seed), verbose=0, n_steps=256,
                    batch_size=64, learning_rate=3e-4, gamma=0.99,
                    tensorboard_log=str(PROJECT_ROOT / "experiments" / "tensorboard" / "city"))
        model.learn(total_timesteps=int(timesteps), progress_bar=False)
        model.save(str(model_path))
        metadata = {"model_path": str(model_path.with_suffix(".zip").resolve()),
                    "city": region["city"], "region_code": region["code"],
                    "target_datetime": target_datetime.isoformat(), "duration_s": int(duration),
                    "seed": int(seed), "radius_km": float(radius_km),
                    "requested_training_timesteps": int(timesteps),
                    "signal_count": len(env.tls_ids), "signal_ids": list(env.tls_ids),
                    "algorithm": "Stable-Baselines3 PPO with joint MultiDiscrete action",
                    "optimization_objective": "Prioritize network throughput (completed trips per simulated hour); queue and waiting costs are secondary.",
                    "safety": "SUMO clearance phases plus minimum/maximum green guard",
                    "decision_interval_s": int(decision_interval_s),
                    "min_green_s": float(min_green_s), "max_green_s": float(max_green_s),
                    "demand_estimate": baseline_summary.get("demand_estimate") or demand_estimate,
                    "randomized_training_demand": True,
                    "training_note": "Randomized SUMO routes are generated on each PPO episode using the selected demand intensity; this reduces memorization of one route realization.",
                    "training_scenario_is_synthetic": True,
                    "demand_assumption_note": baseline_summary.get("demand_assumption_note"),
                    "baseline_experiment_dir": baseline["experiment_dir"],
                    "sumocfg": str(training_cfg.resolve())}
    finally:
        env.close()
    with model_path.with_suffix(".json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
    from .ledger import record_rl_model
    record_rl_model(metadata["model_path"], region["city"], metadata)
    return metadata


def evaluate_city_controller(model_path, city="Seoul", target_datetime=None,
                             duration=900, seed=42, radius_km=3.0,
                             base_density_veh_per_hour_km=8.0, output=None,
                             decision_interval_s=None, min_green_s=None, max_green_s=None,
                             demand_estimate=None):
    region = resolve_region(city)
    target_datetime = target_datetime or datetime.now().replace(second=0, microsecond=0)
    if isinstance(target_datetime, str):
        target_datetime = datetime.fromisoformat(target_datetime)
    baseline, baseline_summary, sumocfg = _build_reference_scenario(
        region["city"], target_datetime, duration, seed, radius_km,
        base_density_veh_per_hour_km, demand_estimate)
    try:
        from stable_baselines3 import PPO
    except ImportError as exc:
        raise RuntimeError("City PPO evaluation requires Stable-Baselines3 and PyTorch") from exc
    model_path = Path(model_path)
    metadata_path = model_path.with_suffix(".json")
    if metadata_path.is_file():
        with metadata_path.open("r", encoding="utf-8") as handle:
            model_metadata = json.load(handle)
        if model_metadata.get("city", "").casefold() != region["city"].casefold():
            raise ValueError("The selected policy was trained for a different city")
        if abs(float(model_metadata.get("radius_km", radius_km)) - float(radius_km)) > 1e-6:
            raise ValueError("Select the same subnetwork radius used to train this policy")
    policy = PPO.load(str(model_path))
    model_settings = {}
    if metadata_path.is_file():
        model_settings = json.loads(metadata_path.read_text(encoding="utf-8"))
    interval = int(decision_interval_s or model_settings.get("decision_interval_s", 5))
    min_green = float(min_green_s or model_settings.get("min_green_s", 10))
    max_green = float(max_green_s or model_settings.get("max_green_s", 60))
    env = CityTrafficSignalEnv(sumocfg, duration=duration, decision_interval=interval,
                               min_green=min_green, max_green=max_green,
                               seed=seed, record_samples=True)
    output_dir = Path(output) if output else RESULT_ROOT / (
        "CITY_RL_{}_{}_{}".format(region["code"], int(seed), datetime.now().strftime("%Y%m%dT%H%M%S")))
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        observation, _ = env.reset(seed=int(seed))
        terminated = truncated = False
        while not (terminated or truncated):
            action, _ = policy.predict(observation, deterministic=True)
            observation, _, terminated, truncated, _ = env.step(action)
        rl_metrics = env._episode_metrics()
        samples_path = env.save_samples(output_dir / "rl_signal_state.csv")
    finally:
        env.close()
    comparison = {"simulation_type": "osm_city_coordinated_rl_comparison",
                  "city": region["city"], "region_code": region["code"],
                  "target_datetime": target_datetime.isoformat(), "seed": int(seed),
                  "duration_s": int(duration), "radius_km": float(radius_km),
                  "route_file": str((Path(baseline["experiment_dir"]) / "demand.rou.xml").resolve()),
                  "baseline_experiment_dir": baseline["experiment_dir"],
                  "controller": "PPO deterministic policy",
                  "decision_interval_s": interval,
                  "min_green_s": min_green, "max_green_s": max_green,
                  "model_path": str(Path(model_path).resolve()),
                  "signal_count": rl_metrics["signal_count"],
                  "signal_ids": list(env.tls_ids),
                  "baseline": baseline_summary, "ppo": rl_metrics,
                  "demand_estimate": baseline_summary.get("demand_estimate") or demand_estimate,
                  "ppo_signal_state_csv": samples_path,
                  "demand_assumption_note": baseline_summary.get("demand_assumption_note"),
                  "interpretation": "Prototype simulation comparison on generated demand; not field-calibrated or an official signal plan."}
    comparison_path = output_dir / "comparison.json"
    with comparison_path.open("w", encoding="utf-8") as handle:
        json.dump(comparison, handle, ensure_ascii=False, indent=2)
    signal_rows = []
    import csv
    if samples_path:
        with Path(samples_path).open("r", newline="", encoding="utf-8") as handle:
            samples = list(csv.DictReader(handle))
    else:
        samples = []
    for signal_id in env.tls_ids:
        prefix = "tls_" + str(signal_id).replace("-", "_").replace(":", "_")
        rows = [row for row in samples if row.get(prefix + "_queue_length") not in (None, "")]
        if rows:
            signal_rows.append((signal_id, {
                "mean_queue_length": sum(float(row[prefix + "_queue_length"]) for row in rows) / len(rows),
                "mean_waiting_time_s": sum(float(row[prefix + "_waiting_time_s"]) for row in rows) / len(rows),
                "most_observed_phase": max(set(row[prefix + "_phase"] for row in rows),
                                            key=lambda phase: sum(1 for row in rows if row[prefix + "_phase"] == phase)),
                "recommendation": "adaptive joint PPO policy; phase is selected from the imported SUMO clearance program"}))
    from .ledger import record_experiment
    record_experiment("RL_" + output_dir.name, region["city"], "osm_city_coordinated_rl_comparison",
                      {"target_datetime": target_datetime.isoformat(), "seed": int(seed),
                       "duration_s": int(duration), "radius_km": float(radius_km)},
                      comparison, comparison_path, signal_rows)
    return {"experiment_dir": str(output_dir.resolve()), "comparison": comparison,
            "comparison_path": str(comparison_path.resolve())}
