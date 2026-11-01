"""Offline unit checks for Phase 1 definitions and state extraction."""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aatc.traffic_state import APPROACH_EDGES, collect_state


class FakeEdge:
    def __init__(self):
        self.ids = {"north_in": ("n1",), "south_in": (), "east_in": ("e1", "e2"), "west_in": ()}

    def getLastStepVehicleIDs(self, edge_id):
        return self.ids[edge_id]

    def getLastStepVehicleNumber(self, edge_id):
        return len(self.ids.get(edge_id, ()))

    def getLastStepMeanSpeed(self, edge_id):
        return 0.0 if self.ids.get(edge_id) else -1.0

    def getLastStepOccupancy(self, edge_id):
        return 10.0 * len(self.ids.get(edge_id, ()))

    def getLastStepHaltingNumber(self, edge_id):
        return len(self.ids.get(edge_id, ()))

    def getWaitingTime(self, edge_id):
        return float(len(self.ids.get(edge_id, ())))


class FakeSimulation:
    def getTime(self):
        return 1.0

    def getArrivedNumber(self):
        return 2


class FakeTrafficLight:
    def getPhase(self, _):
        return 0

    def getPhaseDuration(self, _):
        return 30.0

    def getSpentDuration(self, _):
        return 1.0

    def getNextSwitch(self, _):
        return 30.0

    def getRedYellowGreenState(self, _):
        return "GGrr"


class FakeTraCI:
    edge = FakeEdge()
    simulation = FakeSimulation()
    trafficlight = FakeTrafficLight()


class Phase1InputTests(unittest.TestCase):
    def test_plain_xml_inputs_parse_and_define_four_approaches(self):
        nodes = ET.parse(ROOT / "simulation" / "nodes.nod.xml").getroot()
        edges = ET.parse(ROOT / "simulation" / "edges.edg.xml").getroot()
        connections = ET.parse(ROOT / "simulation" / "connections.con.xml").getroot()
        self.assertEqual(5, len(nodes.findall("node")))
        self.assertEqual(8, len(edges.findall("edge")))
        self.assertEqual(4, len(connections.findall("connection")))
        self.assertEqual(set(("north_in", "south_in", "east_in", "west_in")), set(APPROACH_EDGES.values()))

    def test_signal_program_uses_non_conflicting_phases_and_clearance(self):
        root = ET.parse(ROOT / "simulation" / "signals.tll.xml").getroot()
        phases = root.find("tlLogic").findall("phase")
        self.assertEqual([30, 3, 1, 30, 3, 1], [int(p.attrib["duration"]) for p in phases])
        self.assertEqual(["GGrr", "yyrr", "rrrr", "rrGG", "rryy", "rrrr"],
                         [p.attrib["state"] for p in phases])

    def test_state_reports_distinct_counts_and_vehicle_arrivals(self):
        previous_ids = dict((edge, set()) for edge in APPROACH_EDGES.values())
        state = collect_state(FakeTraCI, previous_ids)
        self.assertEqual(1, state["approaches"]["north"]["vehicle_count"])
        self.assertEqual(1, state["approaches"]["north"]["queue_length"])
        self.assertEqual(1.0, state["approaches"]["north"]["arrival_rate_veh_per_s"])
        self.assertEqual(2.0, state["approaches"]["east"]["arrival_rate_veh_per_s"])
        self.assertEqual(0.0, state["approaches"]["west"]["arrival_rate_veh_per_s"])
        self.assertEqual("GGrr", state["signal"]["current_state"])
        again = collect_state(FakeTraCI, previous_ids)
        self.assertEqual(0.0, again["approaches"]["north"]["arrival_rate_veh_per_s"])


if __name__ == "__main__":
    unittest.main()
