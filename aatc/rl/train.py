"""Train and save a Stable-Baselines3 PPO controller."""
import json
from pathlib import Path

from .traffic_signal_env import TrafficSignalEnv


def train(total_timesteps=None, seed=42, duration=900, traffic_profile="uniform",
          decision_interval=None, model_path=None, log_dir=None, learning_rate=None, gamma=None):
    try:
        from stable_baselines3 import PPO
        from stable_baselines3.common.callbacks import CheckpointCallback
    except ImportError as exc:
        raise RuntimeError("PPO dependencies are missing; install requirements.txt in a supported Python environment") from exc
    root = Path(__file__).resolve().parents[2]
    with (root / "config" / "rl.json").open("r", encoding="utf-8") as handle:
        rl_config = json.load(handle)
    total_timesteps = int(total_timesteps if total_timesteps is not None else rl_config["total_timesteps"])
    decision_interval = int(decision_interval if decision_interval is not None else rl_config["decision_interval_s"])
    learning_rate = float(learning_rate if learning_rate is not None else rl_config["learning_rate"])
    gamma = float(gamma if gamma is not None else rl_config["gamma"])
    if total_timesteps <= 0 or decision_interval <= 0:
        raise ValueError("total_timesteps and decision_interval must be positive")
    destination = Path(model_path) if model_path else root / "aatc" / "rl" / "models" / "ppo_traffic_seed_{}".format(seed)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tensorboard_dir = Path(log_dir) if log_dir else root / "experiments" / "results" / "ppo_training_seed_{}".format(seed)
    tensorboard_dir.mkdir(parents=True, exist_ok=True)
    env = TrafficSignalEnv(duration=duration, seed=seed, traffic_profile=traffic_profile,
                           decision_interval=decision_interval)
    checkpoint_freq = max(512, min(10000, total_timesteps // 10 or 1))
    callback = CheckpointCallback(save_freq=checkpoint_freq,
                                  save_path=str(destination.parent / "checkpoints"),
                                  name_prefix=destination.name)
    model = PPO("MlpPolicy", env, learning_rate=learning_rate, gamma=gamma,
                n_steps=512, batch_size=64, seed=seed, verbose=1,
                tensorboard_log=str(tensorboard_dir))
    try:
        model.learn(total_timesteps=total_timesteps, callback=callback,
                    tb_log_name=destination.name)
        model.save(str(destination))
    finally:
        env.close()
    metadata = {
        "algorithm": "PPO",
        "seed": seed,
        "total_timesteps": total_timesteps,
        "duration_s": duration,
        "traffic_profile": traffic_profile,
        "decision_interval_s": decision_interval,
        "learning_rate": learning_rate,
        "gamma": gamma,
        "model": str(destination.with_suffix(".zip").resolve()),
        "tensorboard_log_dir": str(tensorboard_dir.resolve()),
    }
    with destination.with_suffix(".metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
    return metadata
