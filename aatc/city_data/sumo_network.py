"""Generate a selected, connected Korean-city subnetwork from cached OSM data."""
import json
import math
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

from ..sumo_runtime import find_sumo_tool
from .matching import haversine_m
from .regions import resolve_region
from .sync import DEFAULT_CITY_ROOT


ROAD_PRIORITY = {"motorway": 13, "trunk": 11, "primary": 9, "secondary": 7,
                 "tertiary": 5, "residential": 3, "unclassified": 2, "service": 1}
DEFAULT_SPEED_KPH = {"motorway": 100, "trunk": 80, "primary": 60, "secondary": 50,
                     "tertiary": 40, "residential": 30, "unclassified": 30, "service": 20}


def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _speed_mps(tags, highway):
    value = _first(tags.get("maxspeed"))
    if value is not None:
        try:
            token = str(value).split()[0].replace(",", ".")
            speed = float(token)
            if "mph" in str(value).casefold():
                speed *= 1.609344
            if speed > 0:
                return round(speed / 3.6, 3)
        except (TypeError, ValueError):
            pass
    return DEFAULT_SPEED_KPH.get(highway, 30) / 3.6


def _lane_count(tags):
    raw = _first(tags.get("lanes"))
    if raw is not None:
        try:
            parsed = int(float(str(raw).split(";")[0]))
            if 1 <= parsed <= 12:
                return parsed
        except (TypeError, ValueError):
            pass
    highway = _first(tags.get("highway"))
    return 2 if highway in ("motorway", "trunk", "primary") else 1


def _edge_shape(edge, start, end):
    geometry = edge.get("geometry")
    coords = []
    if geometry is not None:
        try:
            coords = [(float(x), float(y)) for x, y in geometry.coords]
        except (AttributeError, TypeError, ValueError):
            coords = []
    if len(coords) < 2:
        coords = [start, end]
    else:
        if haversine_m(coords[0][0], coords[0][1], start[0], start[1]) > 5:
            coords.reverse()
        if haversine_m(coords[0][0], coords[0][1], start[0], start[1]) > 10:
            coords.insert(0, start)
        if haversine_m(coords[-1][0], coords[-1][1], end[0], end[1]) > 10:
            coords.append(end)
    return " ".join("{:.7f},{:.7f}".format(lon, lat) for lon, lat in coords)


def _center_for_graph(graph):
    lons = [float(data["x"]) for _, data in graph.nodes(data=True)]
    lats = [float(data["y"]) for _, data in graph.nodes(data=True)]
    return sum(lons) / len(lons), sum(lats) / len(lats)


def build_city_network(city="Seoul", radius_km=2.0, center_lat=None, center_lon=None,
                       city_root=None, output_root=None):
    """Write a reproducible SUMO subnetwork and OSM-to-SUMO mapping manifest.

    Existing OSM-tagged TLS locations are imported. SUMO-generated baseline phase
    durations are not treated as government-approved timings.
    """
    if not 0.3 <= float(radius_km) <= 15:
        raise ValueError("radius_km must be between 0.3 and 15")
    region = resolve_region(city)
    data_root = Path(city_root) if city_root else DEFAULT_CITY_ROOT
    key = region["city"].casefold().replace(" ", "_")
    osm_dir = data_root / key / "osm"
    graph_path = osm_dir / "road_network.graphml"
    if not graph_path.is_file():
        raise FileNotFoundError("Sync the city's OSM data first: {}".format(graph_path))
    try:
        import osmnx as ox
    except ImportError as exc:
        raise RuntimeError("City network generation requires OSMnx") from exc
    graph = ox.load_graphml(graph_path)
    if not graph.number_of_nodes():
        raise RuntimeError("The cached OSM graph is empty")
    center_lon, center_lat = ((float(center_lon), float(center_lat))
                              if center_lon is not None and center_lat is not None
                              else _center_for_graph(graph))
    # Find a nearby graph node in O(n) once; then retain nodes in the requested
    # radius. A radial cut is transparent and avoids silently changing the map.
    center_node = min(graph.nodes, key=lambda node: haversine_m(
        center_lon, center_lat, float(graph.nodes[node]["x"]), float(graph.nodes[node]["y"])))
    selected_nodes = {node for node, data in graph.nodes(data=True)
                      if haversine_m(center_lon, center_lat, float(data["x"]), float(data["y"]))
                      <= float(radius_km) * 1000.0}
    if center_node not in selected_nodes:
        selected_nodes.add(center_node)
    subgraph = graph.subgraph(selected_nodes).copy()
    if subgraph.number_of_nodes() < 30 or subgraph.number_of_edges() < 40:
        raise ValueError("Selected radius produced too few connected road elements; choose a larger radius")

    crosswalk_path = data_root / key / "intersection_matches.json"
    crosswalk = []
    if crosswalk_path.is_file():
        try:
            crosswalk = json.loads(crosswalk_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            crosswalk = []
    gov_by_osm = {str(row.get("osm_node_id")): row.get("INT_NO")
                  for row in crosswalk if row.get("matched") and row.get("osm_node_id")}

    output_base = Path(output_root) if output_root else data_root / key / "sumo" / (
        "radius_{:.1f}km_{:.5f}_{:.5f}".format(float(radius_km), center_lat, center_lon).replace(".", "p"))
    output_base.mkdir(parents=True, exist_ok=True)
    nodes_path = output_base / "nodes.nod.xml"
    edges_path = output_base / "edges.edg.xml"
    net_path = output_base / "network.net.xml"
    config_path = output_base / "scenario.sumocfg"
    route_path = output_base / "demand.rou.xml"
    id_map = {}
    node_root = ET.Element("nodes")
    for osmid, data in subgraph.nodes(data=True):
        osm_id = str(osmid)
        sumo_id = "osm_" + osm_id
        id_map[osm_id] = sumo_id
        tags = data
        government_signal = str(osmid) in gov_by_osm
        node_type = ("traffic_light" if _first(tags.get("highway")) == "traffic_signals"
                     or government_signal else "priority")
        attributes = {"id": sumo_id, "x": str(float(data["x"])), "y": str(float(data["y"])),
                      "type": node_type}
        if _first(tags.get("name")):
            attributes["name"] = str(_first(tags.get("name")))[:100]
        ET.SubElement(node_root, "node", attributes)
    edge_root = ET.Element("edges")
    edge_count = 0
    for u, v, edge_key, data in subgraph.edges(keys=True, data=True):
        highway = _first(data.get("highway")) or "unclassified"
        start = (float(subgraph.nodes[u]["x"]), float(subgraph.nodes[u]["y"]))
        end = (float(subgraph.nodes[v]["x"]), float(subgraph.nodes[v]["y"]))
        attributes = {"id": "osm_{}_{}_{}".format(u, v, edge_key), "from": id_map[str(u)],
                      "to": id_map[str(v)], "priority": str(ROAD_PRIORITY.get(highway, 2)),
                      "numLanes": str(_lane_count(data)), "speed": str(_speed_mps(data, highway)),
                      "shape": _edge_shape(data, start, end)}
        if _first(data.get("name")):
            attributes["name"] = str(_first(data.get("name")))[:100]
        ET.SubElement(edge_root, "edge", attributes)
        edge_count += 1
    ET.ElementTree(node_root).write(nodes_path, encoding="utf-8", xml_declaration=True)
    ET.ElementTree(edge_root).write(edges_path, encoding="utf-8", xml_declaration=True)
    command = [find_sumo_tool("netconvert"), "--node-files", str(nodes_path), "--edge-files", str(edges_path),
               "--output-file", str(net_path), "--proj.utm", "true", "--junctions.join", "true",
               "--no-turnarounds", "true", "--tls.default-type", "static"]
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True)
    if completed.returncode or not net_path.is_file():
        raise RuntimeError("SUMO netconvert failed (exit {}): {}".format(
            completed.returncode, completed.stdout[-3000:]))
    generated_net = ET.parse(str(net_path)).getroot()

    city_cfg = ET.Element("configuration")
    input_node = ET.SubElement(city_cfg, "input")
    ET.SubElement(input_node, "net-file", {"value": str(net_path.resolve())})
    ET.SubElement(input_node, "route-files", {"value": str(route_path.resolve())})
    time_node = ET.SubElement(city_cfg, "time")
    ET.SubElement(time_node, "begin", {"value": "0"})
    ET.SubElement(time_node, "end", {"value": "3600"})
    ET.ElementTree(city_cfg).write(config_path, encoding="utf-8", xml_declaration=True)

    tls_nodes = [str(node) for node, data in subgraph.nodes(data=True)
                 if _first(data.get("highway")) == "traffic_signals"]
    mapping = {"city": region["city"], "region_code": region["code"], "generated_network": str(net_path.resolve()),
               "nodes": [{"osm_node_id": str(node), "sumo_node_id": id_map[str(node)],
                          "osm_traffic_signal": _first(subgraph.nodes[node].get("highway")) == "traffic_signals",
                          "sumo_signalized": (_first(subgraph.nodes[node].get("highway")) == "traffic_signals"
                                              or str(node) in gov_by_osm),
                          "government_INT_NO": gov_by_osm.get(str(node))}
                         for node in subgraph.nodes],
               "edges": [{"osm_edge_id": "osm_{}_{}_{}".format(u, v, k), "from_osm_node": str(u),
                          "to_osm_node": str(v), "from_sumo_node": id_map[str(u)],
                          "to_sumo_node": id_map[str(v)]}
                         for u, v, k in subgraph.edges(keys=True)],
               "osm_tagged_signal_count": len(tls_nodes),
               "government_matched_signal_count": sum(
                   1 for node in subgraph.nodes if str(node) in gov_by_osm),
               "government_added_signal_count": sum(
                   1 for node in subgraph.nodes if str(node) in gov_by_osm
                   and _first(subgraph.nodes[node].get("highway")) != "traffic_signals"),
               "sumo_signal_count": len(generated_net.findall("tlLogic")),
               "osm_nodes_in_subnetwork": subgraph.number_of_nodes(), "osm_directed_edges": edge_count,
               "radius_km": float(radius_km), "center": {"latitude": center_lat, "longitude": center_lon},
               "signal_plan_note": "SUMO generates baseline phase programs at OSM-tagged and coordinate-matched government intersections; these are not official government timings."}
    mapping_path = output_base / "osm_sumo_mapping.json"
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"directory": str(output_base.resolve()), "network": str(net_path.resolve()),
            "sumocfg": str(config_path.resolve()), "mapping": str(mapping_path.resolve()),
            "metadata": mapping, "netconvert_command": command, "netconvert_output": completed.stdout}
