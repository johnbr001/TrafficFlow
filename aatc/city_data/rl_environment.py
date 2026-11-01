"""Joint multi-intersection Gymnasium environment for OSM city subnetworks."""
import json
import subprocess
from pathlib import Path

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError as exc:
    gym = None
    spaces = None
    _GYM_IMPORT_ERROR = exc
else:
    _GYM_IMPORT_ERROR = None

from ..rl.reward_function import DEFAULT_REWARD_CONFIG, calculate_reward
from ..sumo_runtime import configure_sumo_tools, find_sumo


class CityTrafficSignalEnv(gym.Env if gym is not None else object):
    """PPO environment where one joint action chooses hold/switch per traffic light.

    The graph includes every TLS in the provided SUMO subnetwork. Each switch request
    may only enter the next phase in the imported SUMO program, allowing its authored
    yellow/all-red clearance phases to execute normally.
    """

    metadata = {"render_modes": []}

    def __init__(self, sumocfg, duration=1800, decision_interval=5, seed=42,
                 min_green=10, max_green=60, reward_config=None, record_samples=False,
                 route_generation_command=None):
        if gym is None:
            raise ImportError("City RL requires Gymnasium; install requirements.txt") from _GYM_IMPORT_ERROR
        super().__init__()
        if int(duration) <= 0 or int(decision_interval) <= 0:
            raise ValueError("duration and decision_interval must be positive")
        if float(min_green) <= 0 or float(max_green) <= float(min_green):
            raise ValueError("green timing bounds are invalid")
        self.sumocfg = Path(sumocfg).resolve()
        if not self.sumocfg.is_file():
            raise FileNotFoundError("SUMO city scenario config not found: {}".format(self.sumocfg))
        self.duration = int(duration)
        self.decision_interval = int(decision_interval)
        self.initial_seed = int(seed)
        self.min_green = float(min_green)
        self.max_green = float(max_green)
        self.record_samples = bool(record_samples)
        self.route_generation_command = list(route_generation_command or [])
        self.reward_config = dict(DEFAULT_REWARD_CONFIG)
        # City-scale coordination prioritizes completed network trips. Keep its
        # objective separate from the small-intersection PPO reward configuration.
        config_path = Path(__file__).resolve().parents[2] / "config" / "city_reward_config.json"
        with config_path.open("r", encoding="utf-8") as handle:
            self.reward_config.update(json.load(handle))
        if reward_config:
            self.reward_config.update(reward_config)
        self.traci, _ = configure_sumo_tools()
        self.binary = find_sumo()
        self.tls_ids = self._read_tls_ids()
        if not self.tls_ids:
            raise ValueError("The selected city SUMO network has no controlled traffic lights")
        self.action_space = spaces.MultiDiscrete(np.full(len(self.tls_ids), 2, dtype=np.int64))
        observation_size = len(self.tls_ids) * 4 + 3
        self.observation_space = spaces.Box(low=0.0, high=2.0,
                                            shape=(observation_size,), dtype=np.float32)
        self._incoming = {}
        self._all_incoming = set()
        self._phase_states = {}
        self._phase_index = {}
        self._last_phase = {}
        self._green_since = {}
        self._edge_state = {}
        self._rows = []
        self._simulation_seed = self.initial_seed
        self._tot_queue = self._tot_wait = 0.0
        self._sample_count = self._completed = self._departed = 0
        self._trip_times = []
        self._depart_times = {}
        self._accepted_count = self._rejected_count = self._switch_count = 0
        self._done = False

    def _read_tls_ids(self):
        import xml.etree.ElementTree as ET
        net_file = None
        root = ET.parse(self.sumocfg).getroot()
        for item in root.findall(".//net-file"):
            net_file = item.attrib.get("value")
            break
        if not net_file:
            raise ValueError("City SUMO config does not declare a net-file")
        path = Path(net_file)
        if not path.is_absolute():
            path = self.sumocfg.parent / path
        network_root = ET.parse(path).getroot()
        return sorted({item.attrib["id"] for item in network_root.findall("tlLogic") if item.get("id")})

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.close()
        if seed is not None:
            self._simulation_seed = int(seed)
        elif self.route_generation_command:
            self._simulation_seed = int(self.np_random.integers(0, 2147483647))
        else:
            self._simulation_seed = self.initial_seed
        if self.route_generation_command:
            command = list(self.route_generation_command)
            if "--seed" in command:
                command[command.index("--seed") + 1] = str(self._simulation_seed)
            generated = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       universal_newlines=True)
            if generated.returncode:
                raise RuntimeError("Could not generate randomized city training demand: {}".format(
                    generated.stdout[-2000:]))
        command = [self.binary, "-c", str(self.sumocfg), "--seed", str(self._simulation_seed),
                   "--end", str(self.duration), "--no-step-log", "true",
                   "--duration-log.disable", "true", "--no-warnings", "true"]
        self.traci.start(command)
        active_tls = set(self.traci.trafficlight.getIDList())
        self.tls_ids = [tls for tls in self.tls_ids if tls in active_tls]
        if not self.tls_ids:
            raise RuntimeError("No traffic lights were loaded by SUMO for this network")
        self._incoming = {}
        self._all_incoming = set()
        self._phase_states = {}
        self._phase_index = {}
        self._last_phase = {}
        self._green_since = {}
        for tls_id in self.tls_ids:
            edges = set()
            for group in self.traci.trafficlight.getControlledLinks(tls_id):
                for link in group or ():
                    if not link or not link[0]:
                        continue
                    try:
                        edge_id = self.traci.lane.getEdgeID(link[0])
                    except Exception:
                        continue
                    if edge_id and not str(edge_id).startswith(":"):
                        edges.add(edge_id)
            self._incoming[tls_id] = sorted(edges)
            self._all_incoming.update(edges)
            logic_list = self.traci.trafficlight.getAllProgramLogics(tls_id)
            active_program = self.traci.trafficlight.getProgram(tls_id)
            logic = next((item for item in logic_list if item.programID == active_program),
                         logic_list[0] if logic_list else None)
            if logic is None or not logic.phases:
                raise RuntimeError("Traffic light {} has no SUMO signal program".format(tls_id))
            self._phase_states[tls_id] = [str(phase.state) for phase in logic.phases]
            phase_index = int(self.traci.trafficlight.getPhase(tls_id))
            self._phase_index[tls_id] = phase_index
            self._last_phase[tls_id] = phase_index
            state = self._phase_states[tls_id][phase_index]
            spent = float(self.traci.trafficlight.getSpentDuration(tls_id))
            self._green_since[tls_id] = -spent if self._is_green(state) else None
        self._edge_state = {}
        import traci.constants as tc
        self._edge_variables = (tc.LAST_STEP_VEHICLE_HALTING_NUMBER, tc.VAR_WAITING_TIME,
                                tc.LAST_STEP_VEHICLE_NUMBER, tc.LAST_STEP_MEAN_SPEED)
        known_edges = set(self.traci.edge.getIDList())
        self._all_incoming.intersection_update(known_edges)
        for edge in sorted(self._all_incoming):
            self.traci.edge.subscribe(edge, self._edge_variables)
        self._rows = []
        self._tot_queue = self._tot_wait = 0.0
        self._sample_count = self._completed = self._departed = 0
        self._trip_times, self._depart_times = [], {}
        self._accepted_count = self._rejected_count = self._switch_count = 0
        self._done = False
        return self._observation(), {"tls_ids": list(self.tls_ids), "seed": self._simulation_seed}

    @staticmethod
    def _is_green(state):
        return "G" in state or "g" in state

    def _edge_values(self, edge):
        import traci.constants as tc
        values = self.traci.edge.getSubscriptionResults(edge) or {}
        return {"queue": int(values.get(tc.LAST_STEP_VEHICLE_HALTING_NUMBER, 0)),
                "waiting": float(values.get(tc.VAR_WAITING_TIME, 0.0)),
                "vehicles": int(values.get(tc.LAST_STEP_VEHICLE_NUMBER, 0)),
                "speed": float(values.get(tc.LAST_STEP_MEAN_SPEED, 0.0))}

    def _current_green_elapsed(self, tls_id, now):
        phase = int(self.traci.trafficlight.getPhase(tls_id))
        state = self._phase_states[tls_id][phase]
        if phase != self._last_phase[tls_id]:
            self._last_phase[tls_id] = phase
            self._phase_index[tls_id] = phase
            self._green_since[tls_id] = now - float(self.traci.trafficlight.getSpentDuration(tls_id)) if self._is_green(state) else None
        if not self._is_green(state):
            return phase, state, 0.0
        if self._green_since[tls_id] is None:
            self._green_since[tls_id] = now - float(self.traci.trafficlight.getSpentDuration(tls_id))
        return phase, state, max(0.0, now - self._green_since[tls_id])

    def _apply_joint_action(self, actions):
        now = float(self.traci.simulation.getTime())
        actions = np.asarray(actions, dtype=np.int64).reshape(-1)
        if len(actions) != len(self.tls_ids):
            raise ValueError("Expected one hold/switch action for each signal")
        decisions = []
        for index, tls_id in enumerate(self.tls_ids):
            phase, state, green_elapsed = self._current_green_elapsed(tls_id, now)
            if not self._is_green(state):
                decisions.append({"tls_id": tls_id, "accepted": True, "reason": "SUMO_clearance_phase"})
                continue
            phase_states = self._phase_states[tls_id]
            next_phase = (phase + 1) % len(phase_states)
            next_state = phase_states[next_phase]
            # A control request may enter the next phase only when that phase is a
            # clearance-only state. SUMO then executes its configured clearance.
            next_is_clearance = not self._is_green(next_state)
            requested_switch = int(actions[index]) == 1
            forced_switch = green_elapsed >= self.max_green
            if forced_switch and next_is_clearance:
                self.traci.trafficlight.setPhase(tls_id, next_phase)
                self._switch_count += 1
                decisions.append({"tls_id": tls_id, "accepted": True, "reason": "maximum_green_forced_clearance"})
            elif requested_switch and green_elapsed < self.min_green:
                self._rejected_count += 1
                decisions.append({"tls_id": tls_id, "accepted": False, "reason": "minimum_green_guard"})
            elif requested_switch and next_is_clearance:
                self.traci.trafficlight.setPhase(tls_id, next_phase)
                self._switch_count += 1
                decisions.append({"tls_id": tls_id, "accepted": True, "reason": "safe_clearance_started"})
            elif requested_switch:
                decisions.append({"tls_id": tls_id, "accepted": False,
                                  "reason": "next_SUMO_phase_is_not_clearance"})
                self._rejected_count += 1
            else:
                extension = min(float(self.decision_interval), max(0.1, self.max_green - green_elapsed))
                self.traci.trafficlight.setPhaseDuration(tls_id, extension)
                decisions.append({"tls_id": tls_id, "accepted": True, "reason": "safe_green_extension"})
        self._accepted_count += sum(1 for item in decisions if item["accepted"])
        return decisions

    def _collect_sample(self, completed_total, departed_total):
        now = float(self.traci.simulation.getTime())
        edge_values = {edge: self._edge_values(edge) for edge in self._all_incoming}
        queue = sum(item["queue"] for item in edge_values.values())
        waiting = sum(item["waiting"] for item in edge_values.values())
        vehicle_count = sum(item["vehicles"] for item in edge_values.values())
        row = {"simulation_time_s": now, "total_queue_length": queue,
               "total_waiting_time_s": waiting, "approaching_vehicle_count": vehicle_count,
               "completed_vehicles": completed_total, "departed_vehicles": departed_total}
        for tls_id in self.tls_ids:
            prefix = "tls_" + str(tls_id).replace("-", "_").replace(":", "_")
            local = [edge_values[edge] for edge in self._incoming.get(tls_id, []) if edge in edge_values]
            row[prefix + "_queue_length"] = sum(item["queue"] for item in local)
            row[prefix + "_waiting_time_s"] = sum(item["waiting"] for item in local)
            row[prefix + "_phase"] = self.traci.trafficlight.getPhase(tls_id)
            row[prefix + "_state"] = self.traci.trafficlight.getRedYellowGreenState(tls_id)
        self._sample_count += 1
        self._tot_queue += queue
        self._tot_wait += waiting
        if self.record_samples:
            self._rows.append(row)
        return row

    def _observation(self):
        now = float(self.traci.simulation.getTime())
        edge_values = {edge: self._edge_values(edge) for edge in self._all_incoming}
        vector = []
        for tls_id in self.tls_ids:
            local = [edge_values[edge] for edge in self._incoming.get(tls_id, []) if edge in edge_values]
            queue = sum(item["queue"] for item in local)
            waiting = sum(item["waiting"] for item in local)
            vehicles = sum(item["vehicles"] for item in local)
            mean_speed = (sum(item["speed"] for item in local) / len(local)) if local else 0.0
            _, state, elapsed = self._current_green_elapsed(tls_id, now)
            vector.extend((min(2.0, queue / 40.0), min(2.0, waiting / 300.0),
                           min(2.0, vehicles / 50.0),
                           min(1.0, elapsed / self.max_green) if self._is_green(state) else 0.0))
        queues = sum(item["queue"] for item in edge_values.values())
        waits = sum(item["waiting"] for item in edge_values.values())
        completed_rate = min(1.0, float(self.traci.simulation.getArrivedNumber()) / max(1, len(self.tls_ids)))
        vector.extend((min(2.0, queues / max(40.0, 40.0 * len(self.tls_ids))),
                       min(2.0, waits / max(300.0, 300.0 * len(self.tls_ids))), completed_rate))
        return np.asarray(vector, dtype=np.float32)

    def step(self, action):
        if not self.traci.isLoaded():
            raise RuntimeError("Call reset() before step()")
        if self._done:
            raise RuntimeError("Episode is complete; call reset() before step()")
        decisions = self._apply_joint_action(action)
        queue_samples, wait_samples, completed = [], [], 0
        departed_total = 0
        for _ in range(self.decision_interval):
            if (self.traci.simulation.getTime() >= self.duration
                    or self.traci.simulation.getMinExpectedNumber() <= 0):
                break
            self.traci.simulationStep()
            now = float(self.traci.simulation.getTime())
            for tls_id in self.tls_ids:
                self._current_green_elapsed(tls_id, now)
            departed_total += int(self.traci.simulation.getDepartedNumber())
            completed += int(self.traci.simulation.getArrivedNumber())
            for vehicle_id in self.traci.simulation.getDepartedIDList():
                self._depart_times[vehicle_id] = now
            for vehicle_id in self.traci.simulation.getArrivedIDList():
                start = self._depart_times.pop(vehicle_id, None)
                if start is not None:
                    self._trip_times.append(now - start)
            row = self._collect_sample(self._completed + completed, self._departed + departed_total)
            queue_samples.append(float(row["total_queue_length"]))
            wait_samples.append(float(row["total_waiting_time_s"]))
        self._completed += completed
        self._departed += departed_total
        mean_queue = sum(queue_samples) / len(queue_samples) if queue_samples else 0.0
        mean_wait = sum(wait_samples) / len(wait_samples) if wait_samples else 0.0
        joint_decision = {"accepted": all(item["accepted"] for item in decisions),
                          "reason": "safe_clearance_started" if any(item["reason"] == "safe_clearance_started"
                                                                      for item in decisions) else "joint_action"}
        reward, components = calculate_reward(mean_queue, mean_wait, completed, joint_decision,
                                              self.reward_config)
        simulation_time = float(self.traci.simulation.getTime())
        truncated = simulation_time >= self.duration or self.traci.simulation.getMinExpectedNumber() <= 0
        self._done = bool(truncated)
        metrics = self._episode_metrics()
        info = {"decisions": decisions, "reward_components": components,
                "simulation_time_s": simulation_time, "episode_metrics": metrics}
        return self._observation(), float(reward), False, truncated, info

    def _episode_metrics(self):
        denominator = float(self._sample_count) or 1.0
        elapsed = float(self.traci.simulation.getTime()) if self.traci.isLoaded() else 0.0
        return {"simulation_time_s": float(self.traci.simulation.getTime()) if self.traci.isLoaded() else 0.0,
                "mean_total_queue_length": self._tot_queue / denominator,
                "mean_total_waiting_time_s": self._tot_wait / denominator,
                "completed_vehicles": self._completed, "departed_vehicles": self._departed,
                "network_throughput_vehicles_per_hour": self._completed * 3600.0 / elapsed if elapsed > 0 else 0.0,
                "average_travel_time_s": sum(self._trip_times) / float(len(self._trip_times)) if self._trip_times else None,
                "signal_count": len(self.tls_ids), "green_switches": self._switch_count,
                "safety_rejections": self._rejected_count,
                "accepted_signal_actions": self._accepted_count}

    def save_samples(self, path):
        if not self._rows:
            return None
        import csv
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self._rows[0]))
            writer.writeheader()
            writer.writerows(self._rows)
        return str(output.resolve())

    def close(self):
        if hasattr(self, "traci") and self.traci.isLoaded():
            self.traci.close()
