"""LangGraph calculation stage for expected SUMO vehicle demand."""
from typing import Any, Dict, TypedDict

from langgraph.graph import END, START, StateGraph

from ..sumo_runtime import configure_sumo_tools


class DemandState(TypedDict, total=False):
    network_file: str
    base_insertion_density: float
    scenario_multiplier: float
    duration_s: int
    lane_km: float
    passenger_edge_count: int
    demand_result: Dict[str, Any]


def _measure_network_node(state):
    _, sumolib = configure_sumo_tools()
    network = sumolib.net.readNet(state["network_file"])
    lane_meters = 0.0
    edge_count = 0
    for edge in network.getEdges():
        if edge.allows("passenger"):
            lane_meters += float(edge.getLength()) * int(edge.getLaneNumber())
            edge_count += 1
    lane_km = lane_meters / 1000.0
    if lane_km <= 0:
        raise ValueError("The selected SUMO network has no passenger-accessible lane length")
    return {"lane_km": lane_km, "passenger_edge_count": edge_count}


def _estimate_vehicle_count_node(state):
    base_density = float(state["base_insertion_density"])
    scenario_multiplier = float(state["scenario_multiplier"])
    duration_s = int(state["duration_s"])
    effective_density = base_density * scenario_multiplier
    expected = effective_density * float(state["lane_km"]) * duration_s / 3600.0
    return {"demand_result": {
        "network_lane_km": round(float(state["lane_km"]), 3),
        "passenger_road_edges": int(state["passenger_edge_count"]),
        "base_insertion_density_vph_per_lane_km": round(base_density, 2),
        "scenario_multiplier": round(scenario_multiplier, 3),
        "effective_insertion_density_vph_per_lane_km": round(effective_density, 2),
        "duration_s": duration_s,
        "estimated_vehicle_count": max(0, int(round(expected))),
        "estimate_method": "effective insertion density x passenger lane-kilometers x duration / 3600",
        "estimate_note": "Expected trip departures before route validation and SUMO insertion constraints; not an observed vehicle count."
    }}


def _build_graph():
    builder = StateGraph(DemandState)
    builder.add_node("measure_network_lane_kilometers", _measure_network_node)
    builder.add_node("estimate_vehicle_count", _estimate_vehicle_count_node)
    builder.add_edge(START, "measure_network_lane_kilometers")
    builder.add_edge("measure_network_lane_kilometers", "estimate_vehicle_count")
    builder.add_edge("estimate_vehicle_count", END)
    return builder.compile()


_DEMAND_GRAPH = _build_graph()


def estimate_city_vehicle_demand(network_file, base_insertion_density,
                                 scenario_multiplier, duration_s):
    """Run the explicit network-measurement then vehicle-count calculation nodes."""
    state = _DEMAND_GRAPH.invoke({
        "network_file": str(network_file),
        "base_insertion_density": float(base_insertion_density),
        "scenario_multiplier": float(scenario_multiplier),
        "duration_s": int(duration_s),
    })
    return state["demand_result"]
