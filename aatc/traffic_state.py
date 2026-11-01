"""Read raw per-approach measurements from a live TraCI connection."""

APPROACH_EDGES = {
    "north": "north_in",
    "south": "south_in",
    "east": "east_in",
    "west": "west_in",
}


def collect_state(traci, previous_vehicle_ids, intersection_id="J1"):
    now = traci.simulation.getTime()
    approaches = {}
    for direction, edge_id in APPROACH_EDGES.items():
        vehicle_ids = set(traci.edge.getLastStepVehicleIDs(edge_id))
        count = len(vehicle_ids)
        speed = traci.edge.getLastStepMeanSpeed(edge_id)
        occupancy = traci.edge.getLastStepOccupancy(edge_id)
        halted = traci.edge.getLastStepHaltingNumber(edge_id)
        waiting = traci.edge.getWaitingTime(edge_id)
        arrivals = len(vehicle_ids - previous_vehicle_ids.get(edge_id, set()))
        approaches[direction] = {
            "edge_id": edge_id,
            "vehicle_count": count,
            "queue_length": halted,
            "average_speed_mps": speed,
            "waiting_time_s": waiting,
            "arrival_rate_veh_per_s": float(arrivals),
            "occupancy_percent": occupancy,
        }
        previous_vehicle_ids[edge_id] = vehicle_ids

    phase = traci.trafficlight.getPhase(intersection_id)
    phase_duration = traci.trafficlight.getPhaseDuration(intersection_id)
    spent = traci.trafficlight.getSpentDuration(intersection_id)
    next_switch = traci.trafficlight.getNextSwitch(intersection_id)
    vehicles_on_edges = sum(a["vehicle_count"] for a in approaches.values())
    return {
        "simulation_time_s": now,
        "intersection_id": intersection_id,
        "approaches": approaches,
        "signal": {
            "current_phase": phase,
            "current_state": traci.trafficlight.getRedYellowGreenState(intersection_id),
            "phase_duration_s": phase_duration,
            "phase_elapsed_s": spent,
            "phase_remaining_s": max(0.0, next_switch - now),
        },
        "total_approaching_vehicles": vehicles_on_edges,
        "vehicles_inside_intersection": traci.edge.getLastStepVehicleNumber(":J1_0")
        + traci.edge.getLastStepVehicleNumber(":J1_1")
        + traci.edge.getLastStepVehicleNumber(":J1_2")
        + traci.edge.getLastStepVehicleNumber(":J1_3"),
        "total_waiting_time_s": sum(a["waiting_time_s"] for a in approaches.values()),
        "total_queue_length": sum(a["queue_length"] for a in approaches.values()),
        "completed_vehicles_this_step": traci.simulation.getArrivedNumber(),
    }
