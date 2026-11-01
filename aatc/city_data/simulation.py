"""SUMO scenario execution for a selected real-world OSM city subnetwork."""
import csv
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from ..sumo_runtime import configure_sumo_tools, find_sumo, load_sumo_environment
from .events import scenario_for_datetime
from .demand_workflow import estimate_city_vehicle_demand
from .regions import resolve_region
from .sumo_network import build_city_network
from .sync import DEFAULT_CITY_ROOT


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_ROOT = PROJECT_ROOT / "experiments" / "results" / "city_runs"


def _write_config(path, network_path, route_path, duration):
    config = ET.Element("configuration")
    inputs = ET.SubElement(config, "input")
    ET.SubElement(inputs, "net-file", {"value": str(Path(network_path).resolve())})
    ET.SubElement(inputs, "route-files", {"value": str(Path(route_path).resolve())})
    timing = ET.SubElement(config, "time")
    ET.SubElement(timing, "begin", {"value": "0"})
    ET.SubElement(timing, "end", {"value": str(int(duration))})
    ET.ElementTree(config).write(path, encoding="utf-8", xml_declaration=True)


def _sumo_incoming_edges(traci, tls_ids):
    by_tls = {}
    all_incoming = set()
    for tls_id in tls_ids:
        edges = set()
        try:
            groups = traci.trafficlight.getControlledLinks(tls_id)
            for group in groups:
                for link in group or ():
                    if not link or not link[0]:
                        continue
                    try:
                        edge_id = traci.lane.getEdgeID(link[0])
                    except Exception:
                        continue
                    if edge_id and not str(edge_id).startswith(":"):
                        edges.add(edge_id)
        except Exception:
            edges = set()
        by_tls[tls_id] = sorted(edges)
        all_incoming.update(edges)
    return by_tls, all_incoming


def _summary(run_id, city, scenario, seed, duration, density, tls_ids, rows, trip_times, output_dir):
    denominator = float(len(rows)) or 1.0
    queue_mean = sum(row["total_queue_length"] for row in rows) / denominator
    waiting_mean = sum(row["total_waiting_time_s"] for row in rows) / denominator
    return {"run_id": run_id, "simulation_type": "osm_city_subnetwork",
            "city": city, "seed": int(seed), "requested_duration_s": int(duration),
            "actual_duration_s": rows[-1]["simulation_time_s"] if rows else 0.0,
            "sample_count": len(rows), "controller": "sumo_osm_baseline",
            "traffic_profile": scenario["time_period"]["id"], "target_datetime": scenario["target_datetime"],
            "day_type": scenario["day_type"], "event_ids": [e.get("event_id") for e in scenario["active_events"]],
            "relative_demand_multiplier": scenario["combined_relative_demand_multiplier"],
            "base_insertion_density_veh_per_hour_km": float(density),
            "effective_insertion_density_veh_per_hour_km": round(float(density) *
                                                                   scenario["combined_relative_demand_multiplier"], 3),
            "demand_assumption_note": scenario["assumption_note"],
            "mean_total_queue_length": queue_mean, "mean_total_waiting_time_s": waiting_mean,
            "average_travel_time_s": sum(trip_times) / float(len(trip_times)) if trip_times else None,
            "completed_vehicles": rows[-1]["completed_vehicles"] if rows else 0,
            "departed_vehicles": rows[-1]["departed_vehicles"] if rows else 0,
            "network_throughput_vehicles_per_hour": ((rows[-1]["completed_vehicles"] * 3600.0 /
                                                        rows[-1]["simulation_time_s"])
                                                       if rows and rows[-1]["simulation_time_s"] > 0 else 0.0),
            "network_throughput_vehicles_per_hour": ((rows[-1]["completed_vehicles"] * 3600.0 /
                                                        rows[-1]["simulation_time_s"])
                                                       if rows and rows[-1]["simulation_time_s"] > 0 else 0.0),
            "signal_count": len(tls_ids), "signal_ids": tls_ids,
            "signal_plan_note": "Static SUMO phases are generated at OSM-tagged and coordinate-matched government intersections. They are not an official government timing plan.",
            "map_source": "OpenStreetMap contributors, ODbL 1.0",
            "state_csv": str((output_dir / "traffic_state.csv").resolve())}


def run_city_simulation(city="Seoul", target_datetime=None, duration=1800, seed=42,
                        radius_km=1.5, center_lat=None, center_lon=None,
                        base_density_veh_per_hour_km=8.0, city_root=None, output=None,
                        demand_estimate=None):
    """Generate an assumption-labeled OSM demand scenario and run it in SUMO."""
    if int(duration) < 60 or int(duration) > 86400:
        raise ValueError("duration must be between 60 and 86400 seconds")
    if float(base_density_veh_per_hour_km) <= 0:
        raise ValueError("base density must be positive")
    region = resolve_region(city)
    if target_datetime is None:
        target_datetime = datetime.now().replace(second=0, microsecond=0)
    elif isinstance(target_datetime, str):
        target_datetime = datetime.fromisoformat(target_datetime)
    scenario = scenario_for_datetime(region["city"], target_datetime)
    network = build_city_network(region["code"], radius_km, center_lat, center_lon, city_root)
    sumo_dir = Path(network["directory"])
    network_file = Path(network["network"])
    run_id = "CITY_{}_{}_{}_{}".format(region["code"], scenario["time_period"]["id"], int(seed), uuid.uuid4().hex[:8])
    output_dir = Path(output) if output else RESULTS_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    route_file = output_dir / "demand.rou.xml"
    config_path = output_dir / "scenario.sumocfg"
    load_sumo_environment()
    sumo_home = os.environ.get("SUMO_HOME")
    random_trips = Path(sumo_home) / "tools" / "randomTrips.py" if sumo_home else None
    if not random_trips or not random_trips.is_file():
        raise RuntimeError("SUMO_HOME/tools/randomTrips.py was not found")
    demand_calculation = estimate_city_vehicle_demand(
        network_file, base_density_veh_per_hour_km,
        scenario["combined_relative_demand_multiplier"], duration)
    density = demand_calculation["effective_insertion_density_vph_per_lane_km"]
    fringe_factor = 3.0 if scenario["time_period"]["profile"] in ("commute_inbound", "commute_outbound") else 1.0
    route_command = [sys.executable, str(random_trips), "--net-file", str(network_file),
                     "--route-file", str(route_file), "--begin", "0", "--end", str(int(duration)),
                     "--seed", str(int(seed)), "--insertion-density", str(density),
                     "--min-distance", "250", "--fringe-factor", str(fringe_factor),
                     "--validate", "--remove-loops", "--vehicle-class", "passenger",
                     "--prefix", "suwon"]
    route_result = subprocess.run(route_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  universal_newlines=True)
    if route_result.returncode or not route_file.is_file():
        raise RuntimeError("SUMO randomTrips failed (exit {}): {}".format(
            route_result.returncode, route_result.stdout[-3000:]))
    generated_route_count = 0
    for _, element in ET.iterparse(str(route_file), events=("end",)):
        if element.tag == "vehicle":
            generated_route_count += 1
        element.clear()
    demand_calculation["generated_route_count"] = generated_route_count
    _write_config(config_path, network_file, route_file, duration)

    traci, _ = configure_sumo_tools()
    tls_ids = []
    rows = []
    departed = completed = 0
    departures = {}
    trip_times = []
    final_time = 0.0
    command = [find_sumo(), "-c", str(config_path), "--seed", str(int(seed)),
               "--end", str(int(duration)), "--no-step-log", "true",
               "--duration-log.disable", "true", "--no-warnings", "true"]
    try:
        traci.start(command)
        tls_ids = sorted(traci.trafficlight.getIDList())
        incoming_by_tls, all_incoming = _sumo_incoming_edges(traci, tls_ids)
        all_road_edges = [edge for edge in traci.edge.getIDList() if not str(edge).startswith(":")]
        known_edges = set(all_road_edges)
        incoming_by_tls = {tls_id: [edge for edge in edges if edge in known_edges]
                           for tls_id, edges in incoming_by_tls.items()}
        all_incoming = {edge for edges in incoming_by_tls.values() for edge in edges}
        measured_edges = sorted(all_incoming if all_incoming else known_edges)
        import traci.constants as tc
        edge_variables = (tc.LAST_STEP_VEHICLE_HALTING_NUMBER, tc.VAR_WAITING_TIME,
                          tc.LAST_STEP_VEHICLE_NUMBER)
        for edge in measured_edges:
            traci.edge.subscribe(edge, edge_variables)
        fieldnames = ["simulation_time_s", "total_queue_length", "total_waiting_time_s",
                      "approaching_vehicle_count", "completed_vehicles", "departed_vehicles"]
        for tls_id in tls_ids:
            safe = "tls_" + str(tls_id).replace("-", "_").replace(":", "_")
            fieldnames.extend([safe + "_phase", safe + "_state", safe + "_queue_length", safe + "_waiting_time_s"])
        while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() < duration:
            traci.simulationStep()
            now = float(traci.simulation.getTime())
            active_edges = all_incoming if all_incoming else set(all_road_edges)
            queues, waits, vehicle_counts = {}, {}, {}
            for edge in active_edges:
                values = traci.edge.getSubscriptionResults(edge) or {}
                queues[edge] = int(values.get(tc.LAST_STEP_VEHICLE_HALTING_NUMBER, 0))
                waits[edge] = float(values.get(tc.VAR_WAITING_TIME, 0.0))
                vehicle_counts[edge] = int(values.get(tc.LAST_STEP_VEHICLE_NUMBER, 0))
            row = {"simulation_time_s": now, "total_queue_length": sum(queues.values()),
                   "total_waiting_time_s": sum(waits.values()),
                   "approaching_vehicle_count": sum(vehicle_counts.values()),
                   "completed_vehicles": completed, "departed_vehicles": departed}
            for tls_id in tls_ids:
                incoming = incoming_by_tls.get(tls_id, [])
                prefix = "tls_" + str(tls_id).replace("-", "_").replace(":", "_")
                row[prefix + "_phase"] = traci.trafficlight.getPhase(tls_id)
                row[prefix + "_state"] = traci.trafficlight.getRedYellowGreenState(tls_id)
                row[prefix + "_queue_length"] = sum(queues.get(edge, 0) for edge in incoming)
                row[prefix + "_waiting_time_s"] = sum(waits.get(edge, 0.0) for edge in incoming)
            departed += int(traci.simulation.getDepartedNumber())
            completed += int(traci.simulation.getArrivedNumber())
            for vehicle_id in traci.simulation.getDepartedIDList():
                departures[vehicle_id] = now
            for vehicle_id in traci.simulation.getArrivedIDList():
                departure_time = departures.pop(vehicle_id, None)
                if departure_time is not None:
                    trip_times.append(now - departure_time)
            row["completed_vehicles"], row["departed_vehicles"] = completed, departed
            rows.append(row)
        final_time = float(traci.simulation.getTime())
    finally:
        if traci.isLoaded():
            traci.close()

    with (output_dir / "traffic_state.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    summary = _summary(run_id, region["city"], scenario, seed, duration, base_density_veh_per_hour_km,
                       tls_ids, rows, trip_times, output_dir)
    summary["actual_duration_s"] = final_time
    summary["demand_estimate"] = dict(demand_estimate or {
        "method": "manual synthetic insertion-density assumption",
        "is_measured_volume": False})
    summary["demand_estimate"].update(demand_calculation)
    summary["scenario"] = scenario
    summary["sumo_network_dir"] = str(sumo_dir.resolve())
    summary["route_file"] = str(route_file.resolve())
    summary["route_generation_command"] = route_command
    summary["sumo_command"] = command
    summary["fringe_origin_destination_weight"] = fringe_factor
    summary["demand_generation_note"] = ("randomTrips.py over OSM roads; insertion density and fringe weighting are scenario assumptions, "
                                         "not observed traffic counts or POI-calibrated OD flows")
    with (output_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    from .ledger import record_experiment
    record_experiment(run_id, region["city"], "osm_city_subnetwork", scenario,
                      summary, output_dir / "summary.json")
    return {"experiment_dir": str(output_dir.resolve()), "summary": summary,
            "scenario": scenario, "tls_count": len(tls_ids), "network": str(network_file.resolve())}
