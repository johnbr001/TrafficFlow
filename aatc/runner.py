"""Run the Phase 1 SUMO scenario and persist measurements."""
import csv
import json
import sys
from pathlib import Path

from .network_generator import generate_network
from .fixed_time_controller import FixedTimeController
from .rule_based_controller import RuleBasedController
from .signal_safety_controller import SignalSafetyController
from .sumo_runtime import configure_sumo_tools, find_sumo
from .traffic_state import APPROACH_EDGES, collect_state


def run(duration=900, seed=42, gui=False, output=None, config=None, controller="fixed_time",
        traffic_profile="uniform", signal_plan=None, route_file=None):
    if duration <= 0:
        raise ValueError("duration must be positive")
    if controller not in ("fixed_time", "rule_based"):
        raise ValueError("controller must be fixed_time or rule_based")
    route_files = {"uniform": "demand.rou.xml", "imbalanced": "demand_imbalanced.rou.xml"}
    if traffic_profile not in route_files and route_file is None:
        raise ValueError("traffic_profile must be uniform or imbalanced")
    if route_file is not None and not Path(route_file).is_file():
        raise ValueError("route_file does not exist: {}".format(route_file))
    traci, _ = configure_sumo_tools()
    binary = find_sumo(gui)
    root = Path(__file__).resolve().parent.parent
    if config:
        config_path = Path(config).resolve()
    else:
        generate_network(root / "simulation")
        config_path = root / "simulation" / "aatc.sumocfg"
    output_dir = Path(output) if output else root / "experiments" / "results" / "run_{}".format(seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "traffic_state.csv"
    summary_path = output_dir / "summary.json"
    selected_route_file = Path(route_file).resolve() if route_file else root / "simulation" / route_files[traffic_profile]
    command = [binary, "-c", str(config_path), "--seed", str(seed), "--end", str(duration),
               "--route-files", str(selected_route_file),
               "--duration-log.disable", "true", "--no-step-log", "true"]
    if not gui:
        command.extend(["--no-warnings", "true"])

    rows = []
    previous_vehicle_ids = dict((edge, set()) for edge in APPROACH_EDGES.values())
    phase_switches = 0
    green_switches = 0
    previous_green_phase = None
    previous_phase = None
    completed = 0
    departed = 0
    departure_times = {}
    completed_trip_times = []
    controller_impl = FixedTimeController() if controller == "fixed_time" else RuleBasedController()
    safety = SignalSafetyController(traci) if controller == "rule_based" else None
    try:
        traci.start(command)
        while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() < duration:
            traci.simulationStep()
            state = collect_state(traci, previous_vehicle_ids)
            requested = controller_impl.choose(state)
            safety_result = {"accepted": True, "reason": "fixed_schedule", "requested": None}
            if safety is not None:
                safety.observe()
                safety_result = dict(safety.last_decision)
                if requested is not None:
                    safety_result = safety.request_green(requested)
            phase = state["signal"]["current_phase"]
            if previous_phase is not None and phase != previous_phase:
                phase_switches += 1
            if phase in (0, 3):
                if previous_green_phase is not None and phase != previous_green_phase:
                    green_switches += 1
                previous_green_phase = phase
            previous_phase = phase
            completed += state["completed_vehicles_this_step"]
            departed += traci.simulation.getDepartedNumber()
            now = state["simulation_time_s"]
            for vehicle_id in traci.simulation.getDepartedIDList():
                departure_times[vehicle_id] = now
            for vehicle_id in traci.simulation.getArrivedIDList():
                departure = departure_times.pop(vehicle_id, None)
                if departure is not None:
                    completed_trip_times.append(now - departure)
            row = {
                "simulation_time_s": state["simulation_time_s"],
                "signal_phase": phase,
                "signal_state": state["signal"]["current_state"],
                "signal_phase_elapsed_s": state["signal"]["phase_elapsed_s"],
                "total_approaching_vehicles": state["total_approaching_vehicles"],
                "vehicles_inside_intersection": state["vehicles_inside_intersection"],
                "total_waiting_time_s": state["total_waiting_time_s"],
                "total_queue_length": state["total_queue_length"],
                "completed_vehicles_this_step": state["completed_vehicles_this_step"],
                "controller_request": requested or "",
                "safety_accepted": safety_result["accepted"],
                "safety_reason": safety_result["reason"],
            }
            for direction, values in state["approaches"].items():
                for metric, value in values.items():
                    if metric != "edge_id":
                        row["{}_{}".format(direction, metric)] = value
            rows.append(row)
        final_time = traci.simulation.getTime()
    finally:
        if traci.isLoaded():
            traci.close()

    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    mean_queue = sum(r["total_queue_length"] for r in rows) / float(len(rows)) if rows else 0.0
    mean_wait = sum(r["total_waiting_time_s"] for r in rows) / float(len(rows)) if rows else 0.0
    summary = {
        "run_id": "EXP_{}_SEED_{}".format(
            (signal_plan or {}).get("strategy_id", controller).upper(), seed),
        "simulation_config": str(config_path.resolve()),
        "controller": controller,
        "seed": seed,
        "traffic_profile": traffic_profile,
        "signal_plan": signal_plan,
        "route_file": str(selected_route_file.resolve()),
        "requested_duration_s": duration,
        "actual_duration_s": final_time,
        "sample_count": len(rows),
        "mean_total_queue_length": mean_queue,
        "mean_total_waiting_time_s": mean_wait,
        "average_travel_time_s": (sum(completed_trip_times) / float(len(completed_trip_times))
                                   if completed_trip_times else None),
        "travel_time_sample_count": len(completed_trip_times),
        "completed_vehicles": completed,
        "departed_vehicles": departed,
        "signal_phase_transitions": phase_switches,
        "green_switches": green_switches,
        "safety_rejections": sum(1 for row in rows if not row["safety_accepted"]),
        "control_parameters": (signal_plan if signal_plan else
                               ({"min_green_s": 10, "max_green_s": 60, "yellow_s": 3,
                                "all_red_s": 1, "queue_threshold_vehicles": 4}
                               if safety is not None else {"green_s": 30, "yellow_s": 3, "all_red_s": 1})),
        "state_csv": str(csv_path.resolve()),
        "command": command,
    }
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
    return summary


def compare_controllers(duration=900, seed=42, output=None, config=None, traffic_profile="uniform"):
    """Run isolated fixed-time and rule-based experiments with identical inputs."""
    root = Path(__file__).resolve().parent.parent
    comparison_dir = Path(output) if output else root / "experiments" / "results" / "comparison_seed_{}".format(seed)
    comparison_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name in ("fixed_time", "rule_based"):
        results.append(run(duration=duration, seed=seed, output=comparison_dir / name,
                           config=config, controller=name, traffic_profile=traffic_profile))
    metrics = ("mean_total_queue_length", "mean_total_waiting_time_s", "average_travel_time_s",
               "completed_vehicles", "departed_vehicles", "green_switches", "safety_rejections")
    comparison = {
        "comparison_id": "CMP_PHASE2_SEED_{}".format(seed),
        "duration_s": duration,
        "seed": seed,
        "traffic_profile": traffic_profile,
        "identical_simulation_config": str((Path(config) if config else root / "simulation" / "aatc.sumocfg").resolve()),
        "results": [{"controller": r["controller"], **dict((key, r[key]) for key in metrics)} for r in results],
    }
    with (comparison_dir / "comparison.json").open("w", encoding="utf-8") as handle:
        json.dump(comparison, handle, indent=2)
    with (comparison_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["controller"] + list(metrics))
        writer.writeheader()
        writer.writerows(comparison["results"])
    return comparison
