"""Unit checks for rule selection and deterministic signal safety."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aatc.rule_based_controller import RuleBasedController
from aatc.signal_safety_controller import SignalSafetyController
from aatc.rl.action_mapper import apply_action
from aatc.rl.observation_builder import OBSERVATION_SIZE, build_observation
from aatc.rl.reward_function import calculate_reward


class FakeTrafficLight:
    def __init__(self, phase=0, elapsed=0):
        self.phase = phase
        self.elapsed = elapsed
        self.durations = []
        self.next_switch = 30

    def getPhase(self, _):
        return self.phase

    def getSpentDuration(self, _):
        return self.elapsed

    def setPhase(self, _, phase):
        self.phase = phase
        self.elapsed = 0

    def setPhaseDuration(self, _, duration):
        self.durations.append(duration)

    def getNextSwitch(self, _):
        return self.next_switch


class FakeTraCI:
    def __init__(self, phase=0, elapsed=0):
        self.trafficlight = FakeTrafficLight(phase, elapsed)
        self.simulation = self
        self.now = 10

    def getTime(self):
        return self.now


class Phase2ControlTests(unittest.TestCase):
    def test_minimum_green_and_clearance_are_enforced(self):
        traci = FakeTraCI(phase=0, elapsed=5)
        safety = SignalSafetyController(traci, min_green=10, max_green=60)
        denied = safety.request_green("east_west")
        self.assertFalse(denied["accepted"])
        self.assertEqual("minimum_green_not_met", denied["reason"])
        traci.trafficlight.elapsed = 10
        accepted = safety.request_green("east_west")
        self.assertTrue(accepted["accepted"])
        self.assertEqual(1, traci.trafficlight.phase)
        during_yellow = safety.request_green("north_south")
        self.assertFalse(during_yellow["accepted"])
        self.assertEqual("clearance_in_progress", during_yellow["reason"])

    def test_illegal_phase_request_is_rejected(self):
        safety = SignalSafetyController(FakeTraCI(phase=0, elapsed=20))
        result = safety.request_green("protected_left_turn")
        self.assertFalse(result["accepted"])
        self.assertEqual("illegal_phase", result["reason"])

    def test_requests_are_rejected_during_yellow_and_all_red(self):
        for clearance_phase in (1, 2, 4, 5):
            safety = SignalSafetyController(FakeTraCI(phase=clearance_phase, elapsed=1))
            result = safety.request_green("east_west")
            self.assertFalse(result["accepted"])
            self.assertEqual("clearance_in_progress", result["reason"])
            self.assertTrue(safety.keep_current()["accepted"])

    def test_green_extension_is_limited_by_maximum_green(self):
        traci = FakeTraCI(phase=0, elapsed=10)
        safety = SignalSafetyController(traci, min_green=10, max_green=60)
        result = apply_action(3, safety)
        self.assertTrue(result["accepted"])
        self.assertEqual("green_extended_by_5s", result["reason"])
        self.assertEqual([25], traci.trafficlight.durations)
        traci.trafficlight.elapsed = 58
        traci.trafficlight.next_switch = 60
        traci.now = 58
        denied = safety.extend_green(5)
        self.assertFalse(denied["accepted"])
        self.assertEqual("maximum_green_would_be_exceeded", denied["reason"])

    def test_observation_and_reward_are_normalized_and_structured(self):
        state = {"signal": {"current_phase": 0, "phase_elapsed_s": 15}, "approaches": {}}
        for direction in ("north", "south", "east", "west"):
            state["approaches"][direction] = {
                "queue_length": 5, "vehicle_count": 10, "average_speed_mps": 6.945,
                "waiting_time_s": 30, "arrival_rate_veh_per_s": 0.5,
            }
        observation = build_observation(state)
        self.assertEqual((OBSERVATION_SIZE,), observation.shape)
        self.assertTrue((observation >= 0).all() and (observation <= 1).all())
        reward, components = calculate_reward(10, 30, 2, {"accepted": True, "reason": "keep_current"})
        self.assertAlmostEqual(0.74, reward)
        self.assertIn("queue_cost", components)

    def test_maximum_green_starts_yellow_clearance(self):
        traci = FakeTraCI(phase=0, elapsed=60)
        safety = SignalSafetyController(traci, min_green=10, max_green=60)
        safety.observe()
        self.assertEqual(1, traci.trafficlight.phase)

    def test_rule_controller_requests_green_for_longer_queue(self):
        state = {
            "signal": {"current_phase": 0, "phase_elapsed_s": 15},
            "approaches": {
                "north": {"queue_length": 1}, "south": {"queue_length": 1},
                "east": {"queue_length": 4}, "west": {"queue_length": 2},
            },
        }
        self.assertEqual("east_west", RuleBasedController(queue_threshold=2).choose(state))


if __name__ == "__main__":
    unittest.main()
