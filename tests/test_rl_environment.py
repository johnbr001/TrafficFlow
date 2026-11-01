"""SUMO-backed Gymnasium smoke tests; skipped when optional RL deps are absent."""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aatc.sumo_runtime import load_sumo_environment

load_sumo_environment()
SUMO_AVAILABLE = bool(os.environ.get("SUMO_HOME"))
RL_AVAILABLE = importlib.util.find_spec("gymnasium") is not None


@unittest.skipUnless(RL_AVAILABLE and SUMO_AVAILABLE, "requires Gymnasium and SUMO")
class TrafficSignalEnvironmentTests(unittest.TestCase):
    def test_reset_step_observation_action_and_truncation_contract(self):
        from aatc.rl.traffic_signal_env import TrafficSignalEnv

        env = TrafficSignalEnv(duration=15, decision_interval=5, seed=7)
        try:
            observation, info = env.reset(seed=7)
            self.assertEqual(env.observation_space.shape, observation.shape)
            self.assertTrue(env.observation_space.contains(observation))
            self.assertIn("simulation_time_s", info)
            observation, reward, terminated, truncated, info = env.step(0)
            self.assertEqual(env.observation_space.shape, observation.shape)
            self.assertTrue(env.action_space.contains(0))
            self.assertFalse(terminated)
            self.assertFalse(truncated)
            self.assertIn("reward_components", info)
        finally:
            env.close()
