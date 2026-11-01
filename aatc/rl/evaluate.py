"""Evaluate a saved PPO model in fresh SUMO episodes."""
import json
from pathlib import Path

from .traffic_signal_env import TrafficSignalEnv


def evaluate(model_path, episodes=3, seed=42, duration=900, traffic_profile="uniform",
             decision_interval=None, output=None):
    try:
        from stable_baselines3 import PPO
    except ImportError as exc:
        raise RuntimeError("PPO dependencies are missing; install requirements.txt in a supported Python environment") from exc
    if episodes <= 0:
        raise ValueError("episodes must be positive")
    model_file = Path(model_path)
    if not model_file.is_file() and model_file.with_suffix(".zip").is_file():
        model_file = model_file.with_suffix(".zip")
    if not model_file.is_file():
        raise FileNotFoundError("PPO model not found: {}".format(model_file))
    env = TrafficSignalEnv(duration=duration, seed=seed, traffic_profile=traffic_profile,
                           decision_interval=decision_interval)
    episode_results = []
    try:
        model = PPO.load(str(model_file), env=env)
        for episode in range(episodes):
            observation, _ = env.reset(seed=seed + episode)
            total_reward = 0.0
            truncated = False
            final_info = {}
            while not truncated:
                action, _ = model.predict(observation, deterministic=True)
                observation, reward, terminated, truncated, final_info = env.step(action)
                total_reward += float(reward)
                if terminated:
                    break
            metrics = final_info.get("episode_metrics", {})
            episode_results.append({"episode": episode + 1, "seed": seed + episode,
                                    "total_reward": total_reward, **metrics})
    finally:
        env.close()
    metric_names = ("mean_total_queue_length", "mean_total_waiting_time_s", "completed_vehicles",
                    "average_travel_time_s", "departed_vehicles", "green_switches", "safety_rejections")
    averages = {}
    for name in metric_names:
        values = [item[name] for item in episode_results
                  if name in item and item[name] is not None]
        if values:
            averages[name] = sum(values) / float(len(values))
    result = {"model": str(model_file.resolve()), "episodes": episodes, "seed": seed,
              "traffic_profile": traffic_profile, "average_metrics": averages,
              "episode_results": episode_results}
    if output:
        output_file = Path(output)
    else:
        output_file = Path(__file__).resolve().parents[2] / "experiments" / "results" / "ppo_evaluation.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    result["output"] = str(output_file.resolve())
    return result
