"""Build a normalized, documented float32 observation vector."""
import numpy as np

APPROACH_ORDER = ("north", "south", "east", "west")
APPROACH_FEATURES = ("queue_length", "vehicle_count", "average_speed", "waiting_time", "arrival_rate")
PHASE_COUNT = 6
OBSERVATION_SIZE = len(APPROACH_ORDER) * len(APPROACH_FEATURES) + PHASE_COUNT + 2


def build_observation(state, max_green=60.0, limits=None):
    limits = limits or {"queue": 40.0, "vehicles": 50.0, "speed": 13.89,
                        "waiting": 300.0, "arrival_rate": 1.0}
    if max_green <= 0 or any(limits[key] <= 0 for key in ("queue", "vehicles", "speed", "waiting", "arrival_rate")):
        raise ValueError("Observation normalization limits must be positive")
    values = []
    for direction in APPROACH_ORDER:
        approach = state["approaches"][direction]
        values.extend((
            min(1.0, max(0.0, approach["queue_length"] / limits["queue"])),
            min(1.0, max(0.0, approach["vehicle_count"] / limits["vehicles"])),
            min(1.0, max(0.0, approach["average_speed_mps"] / limits["speed"])),
            min(1.0, max(0.0, approach["waiting_time_s"] / limits["waiting"])),
            min(1.0, max(0.0, approach["arrival_rate_veh_per_s"] / limits["arrival_rate"])),
        ))
    phase = int(state["signal"]["current_phase"])
    values.extend(1.0 if index == phase else 0.0 for index in range(PHASE_COUNT))
    elapsed = max(0.0, state["signal"]["phase_elapsed_s"])
    is_green = phase in (0, 3)
    values.append(min(1.0, elapsed / max_green) if is_green else 0.0)
    values.append(min(1.0, elapsed / max_green))
    return np.asarray(values, dtype=np.float32)
