"""Gymnasium environment that controls a SUMO signal through TraCI."""
from pathlib import Path
import json

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
    _GYM_IMPORT_ERROR = None
except ImportError as exc:
    gym = None
    spaces = None
    _GYM_IMPORT_ERROR = exc

from ..network_generator import generate_network
from ..signal_safety_controller import SignalSafetyController
from ..sumo_runtime import configure_sumo_tools, find_sumo
from ..traffic_state import APPROACH_EDGES, collect_state
from .action_mapper import apply_action
from .observation_builder import OBSERVATION_SIZE, build_observation
from .reward_function import DEFAULT_REWARD_CONFIG, calculate_reward


_BaseEnv = gym.Env if gym is not None else object


class TrafficSignalEnv(_BaseEnv):
    """One isolated episode; one Gym action advances several SUMO seconds.

    Observation layout: 5 normalized metrics for each approach in N,S,E,W order;
    6-way one-hot SUMO signal phase; normalized elapsed green; normalized elapsed
    time in the current phase. Total length is 28 float32 values.
    """

    metadata = {"render_modes": []}

    def __init__(self, duration=900, seed=42, traffic_profile="uniform", gui=False,
                 decision_interval=None, min_green=None, max_green=None,
                 reward_config=None, normalization_limits=None):
        if gym is None:
            raise ImportError("Phase 3 requires Gymnasium. Install dependencies from requirements.txt.") from _GYM_IMPORT_ERROR
        super().__init__()
        if duration <= 0 or (decision_interval is not None and decision_interval <= 0):
            raise ValueError("duration and decision_interval must be positive")
        if traffic_profile not in ("uniform", "imbalanced"):
            raise ValueError("traffic_profile must be uniform or imbalanced")
        self.duration = int(duration)
        self.initial_seed = int(seed)
        self.traffic_profile = traffic_profile
        self.gui = bool(gui)
        self.root = Path(__file__).resolve().parents[2]
        with (self.root / "config" / "rl.json").open("r", encoding="utf-8") as handle:
            self.rl_config = json.load(handle)
        with (self.root / "config" / "reward_config.json").open("r", encoding="utf-8") as handle:
            file_reward_config = json.load(handle)
        self.decision_interval = int(decision_interval if decision_interval is not None
                                     else self.rl_config["decision_interval_s"])
        self.min_green = float(min_green if min_green is not None else self.rl_config["min_green_s"])
        self.max_green = float(max_green if max_green is not None else self.rl_config["max_green_s"])
        self.normalization_limits = normalization_limits or {
            "queue": self.rl_config["normalization"]["queue_vehicles"],
            "vehicles": self.rl_config["normalization"]["vehicles"],
            "speed": self.rl_config["normalization"]["speed_mps"],
            "waiting": self.rl_config["normalization"]["waiting_seconds"],
            "arrival_rate": self.rl_config["normalization"]["arrival_rate_vehicles_per_second"],
        }
        self.reward_config = dict(DEFAULT_REWARD_CONFIG)
        self.reward_config.update(file_reward_config)
        if reward_config:
            self.reward_config.update(reward_config)
        self.route_file = self.root / "simulation" / (
            "demand.rou.xml" if traffic_profile == "uniform" else "demand_imbalanced.rou.xml")
        self.traci, _ = configure_sumo_tools()
        self.binary = find_sumo(gui=self.gui)
        generate_network(self.root / "simulation")
        self.action_space = spaces.Discrete(5)
        self.observation_space = spaces.Box(low=0.0, high=1.0,
                                            shape=(OBSERVATION_SIZE,), dtype=np.float32)
        self.safety = None
        self._state = None
        self._previous_vehicle_ids = None
        self._sim_seed = self.initial_seed
        self._episode_samples = 0
        self._queue_sum = 0.0
        self._waiting_sum = 0.0
        self._completed = 0
        self._departed = 0
        self._safety_rejections = 0
        self._decision_index = 0
        self._departure_times = {}
        self._trip_time_sum = 0.0
        self._trip_count = 0

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.close()
        self._sim_seed = self.initial_seed if seed is None else int(seed)
        command = [self.binary, "-c", str(self.root / "simulation" / "aatc.sumocfg"),
                   "--seed", str(self._sim_seed), "--end", str(self.duration),
                   "--route-files", str(self.route_file), "--duration-log.disable", "true",
                   "--no-step-log", "true", "--no-warnings", "true"]
        self.traci.start(command)
        self.safety = SignalSafetyController(self.traci, min_green=self.min_green,
                                             max_green=self.max_green)
        self._previous_vehicle_ids = dict((edge, set()) for edge in APPROACH_EDGES.values())
        self._episode_samples = 0
        self._queue_sum = 0.0
        self._waiting_sum = 0.0
        self._completed = 0
        self._departed = 0
        self._safety_rejections = 0
        self._decision_index = 0
        self._departure_times = {}
        self._trip_time_sum = 0.0
        self._trip_count = 0
        self._state = collect_state(self.traci, self._previous_vehicle_ids)
        return build_observation(self._state, self.max_green, self.normalization_limits), self._info()

    def step(self, action):
        if self._state is None or not self.traci.isLoaded():
            raise RuntimeError("Call reset() before step()")
        self.safety.observe()
        decision = apply_action(action, self.safety)
        queue_samples = []
        waiting_samples = []
        completed_this_decision = 0
        last_state = self._state
        for _ in range(self.decision_interval):
            if (self.traci.simulation.getTime() >= self.duration
                    or self.traci.simulation.getMinExpectedNumber() <= 0):
                break
            self.traci.simulationStep()
            last_state = collect_state(self.traci, self._previous_vehicle_ids)
            self.safety.observe()
            queue_samples.append(float(last_state["total_queue_length"]))
            waiting_samples.append(float(last_state["total_waiting_time_s"]))
            completed_this_decision += int(last_state["completed_vehicles_this_step"])
            self._departed += int(self.traci.simulation.getDepartedNumber())
            now = float(last_state["simulation_time_s"])
            for vehicle_id in self.traci.simulation.getDepartedIDList():
                self._departure_times[vehicle_id] = now
            for vehicle_id in self.traci.simulation.getArrivedIDList():
                departure = self._departure_times.pop(vehicle_id, None)
                if departure is not None:
                    self._trip_time_sum += now - departure
                    self._trip_count += 1

        self._state = last_state
        self._decision_index += 1
        self._episode_samples += len(queue_samples)
        self._queue_sum += sum(queue_samples)
        self._waiting_sum += sum(waiting_samples)
        self._completed += completed_this_decision
        if not decision.get("accepted", True):
            self._safety_rejections += 1
        mean_queue = sum(queue_samples) / len(queue_samples) if queue_samples else 0.0
        mean_waiting = sum(waiting_samples) / len(waiting_samples) if waiting_samples else 0.0
        reward, reward_components = calculate_reward(
            mean_queue, mean_waiting, completed_this_decision, decision, self.reward_config)

        sim_time = self.traci.simulation.getTime()
        terminated = False
        truncated = sim_time >= self.duration or self.traci.simulation.getMinExpectedNumber() <= 0
        info = self._info()
        info.update({
            "decision": dict(decision),
            "reward_components": reward_components,
            "decision_index": self._decision_index,
            "simulated_seconds_this_step": len(queue_samples),
            "episode_metrics": self._episode_metrics(),
        })
        return (build_observation(self._state, self.max_green, self.normalization_limits),
                float(reward), terminated, truncated, info)

    def _episode_metrics(self):
        denominator = float(self._episode_samples) if self._episode_samples else 1.0
        return {
            "simulation_time_s": self.traci.simulation.getTime() if self.traci.isLoaded() else 0.0,
            "mean_total_queue_length": self._queue_sum / denominator,
            "mean_total_waiting_time_s": self._waiting_sum / denominator,
            "completed_vehicles": self._completed,
            "departed_vehicles": self._departed,
            "average_travel_time_s": (self._trip_time_sum / float(self._trip_count)
                                       if self._trip_count else None),
            "green_switches": self.safety.green_switches if self.safety else 0,
            "safety_rejections": self._safety_rejections,
        }

    def _info(self):
        if self._state is None:
            return {}
        return {
            "simulation_time_s": self._state["simulation_time_s"],
            "signal_phase": self._state["signal"]["current_phase"],
            "total_queue_length": self._state["total_queue_length"],
        }

    def close(self):
        if self.traci.isLoaded():
            self.traci.close()
        self._state = None
