"""Configurable reward from observed SUMO measurements and safety outcomes."""

DEFAULT_REWARD_CONFIG = {
    "queue_weight": 1.0,
    "waiting_weight": 0.1,
    "throughput_weight": 0.5,
    "invalid_action_penalty": 0.25,
    "switch_penalty": 0.05,
    "queue_normalizer": 40.0,
    "waiting_normalizer": 300.0,
}


def calculate_reward(mean_queue, mean_waiting, completed, decision, config=None):
    settings = dict(DEFAULT_REWARD_CONFIG)
    if config:
        settings.update(config)
    if settings["queue_normalizer"] <= 0 or settings["waiting_normalizer"] <= 0:
        raise ValueError("Reward normalizers must be positive")
    queue_cost = settings["queue_weight"] * min(2.0, mean_queue / settings["queue_normalizer"])
    waiting_cost = settings["waiting_weight"] * min(2.0, mean_waiting / settings["waiting_normalizer"])
    throughput_reward = settings["throughput_weight"] * completed
    invalid_cost = settings["invalid_action_penalty"] if not decision.get("accepted", True) else 0.0
    switching_cost = (settings["switch_penalty"]
                      if decision.get("reason") == "yellow_clearance_started" else 0.0)
    components = {
        "queue_cost": -queue_cost,
        "waiting_cost": -waiting_cost,
        "throughput_reward": throughput_reward,
        "invalid_action_cost": -invalid_cost,
        "switching_cost": -switching_cost,
    }
    return sum(components.values()), components
