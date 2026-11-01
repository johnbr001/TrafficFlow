"""Create isolated SUMO inputs for one signal-timing candidate."""
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from ..network_generator import generate_network


def build_scenario(strategy, traffic_profile, workspace):
    if traffic_profile not in ("uniform", "imbalanced"):
        raise ValueError("traffic_profile must be uniform or imbalanced")
    root = Path(__file__).resolve().parents[2]
    source = root / "simulation"
    target = Path(workspace)
    target.mkdir(parents=True, exist_ok=True)
    for filename in ("nodes.nod.xml", "edges.edg.xml", "connections.con.xml"):
        shutil.copyfile(source / filename, target / filename)
    route_name = "demand.rou.xml" if traffic_profile == "uniform" else "demand_imbalanced.rou.xml"
    shutil.copyfile(source / route_name, target / route_name)

    signal_tree = ET.parse(source / "signals.tll.xml")
    phases = signal_tree.getroot().find("tlLogic").findall("phase")
    if len(phases) != 6:
        raise ValueError("Expected the six-phase J1 signal program")
    durations = (strategy["green_ns_s"], strategy["yellow_s"], strategy["all_red_s"],
                 strategy["green_ew_s"], strategy["yellow_s"], strategy["all_red_s"])
    for phase, duration in zip(phases, durations):
        phase.set("duration", str(int(duration)))
    signal_path = target / "signals.tll.xml"
    signal_tree.write(str(signal_path), encoding="UTF-8", xml_declaration=True)

    generate_network(target)
    config = ET.Element("configuration")
    input_element = ET.SubElement(config, "input")
    ET.SubElement(input_element, "net-file", {"value": "intersection.net.xml"})
    ET.SubElement(input_element, "route-files", {"value": route_name})
    time_element = ET.SubElement(config, "time")
    ET.SubElement(time_element, "begin", {"value": "0"})
    ET.SubElement(time_element, "step-length", {"value": "1"})
    processing = ET.SubElement(config, "processing")
    ET.SubElement(processing, "time-to-teleport", {"value": "-1"})
    config_path = target / "scenario.sumocfg"
    ET.ElementTree(config).write(str(config_path), encoding="UTF-8", xml_declaration=True)
    with (target / "strategy.json").open("w", encoding="utf-8") as handle:
        json.dump(strategy, handle, indent=2)
    return config_path
