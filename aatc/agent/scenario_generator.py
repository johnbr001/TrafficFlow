"""Create deterministic, simulation-only traffic demand profiles."""
import xml.etree.ElementTree as ET
from pathlib import Path


SCENARIOS = {
    "north_peak": {"north": 1000, "south": 250, "east": 250, "west": 250},
    "east_peak": {"north": 250, "south": 250, "east": 1000, "west": 250},
    "high_uniform": {"north": 600, "south": 600, "east": 600, "west": 600},
}


def generate_traffic_scenario(scenario, output_file, source_file=None):
    if scenario not in SCENARIOS:
        raise ValueError("scenario must be one of {}".format(", ".join(sorted(SCENARIOS))))
    root = Path(__file__).resolve().parents[2]
    source = Path(source_file) if source_file else root / "simulation" / "demand.rou.xml"
    output = Path(output_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.parse(source)
    flows = {flow.attrib["id"].replace("flow_", ""): flow for flow in tree.getroot().findall("flow")}
    for direction, volume in SCENARIOS[scenario].items():
        if direction not in flows:
            raise ValueError("Base route file does not contain flow for {}".format(direction))
        flows[direction].set("vehsPerHour", str(volume))
    tree.write(str(output), encoding="UTF-8", xml_declaration=True)
    return {"scenario": scenario, "vehicles_per_hour": dict(SCENARIOS[scenario]),
            "route_file": str(output.resolve())}
