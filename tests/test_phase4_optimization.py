"""Phase 4 strategy generation, scoring, and SUMO isolation checks."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aatc.sumo_runtime import load_sumo_environment
from aatc.optimization.strategy_evaluator import evaluate_strategies, score_metrics
from aatc.optimization.strategy_generator import generate_strategies

load_sumo_environment()
SUMO_AVAILABLE = bool(os.environ.get("SUMO_HOME") and
                      (Path(os.environ["SUMO_HOME"]) / "tools").is_dir())


class StrategyOptimizationTests(unittest.TestCase):
    def test_grid_is_unique_and_within_signal_safety_limits(self):
        strategies = generate_strategies((20, 30), yellow=3, all_red=1)
        self.assertEqual(4, len(strategies))
        self.assertEqual(4, len(set(item["strategy_id"] for item in strategies)))
        with self.assertRaises(ValueError):
            generate_strategies((5, 30))

    def test_objectives_rank_measured_metrics_in_expected_direction(self):
        low_wait = {"mean_total_waiting_time_s": 10, "mean_total_queue_length": 2,
                    "average_travel_time_s": 20, "completed_vehicles": 50}
        high_wait = dict(low_wait, mean_total_waiting_time_s=40)
        self.assertLess(score_metrics(low_wait, "waiting_time"), score_metrics(high_wait, "waiting_time"))
        self.assertLess(score_metrics(low_wait, "balanced"), score_metrics(high_wait, "balanced"))
        more_throughput = dict(low_wait, completed_vehicles=60)
        self.assertLess(score_metrics(more_throughput, "throughput"), score_metrics(low_wait, "throughput"))

    @unittest.skipUnless(SUMO_AVAILABLE, "requires an installed SUMO toolchain")
    def test_candidates_run_in_isolated_sumo_scenarios_with_same_seed(self):
        strategies = generate_strategies((20, 25), yellow=3, all_red=1)
        output = ROOT / "experiments" / "results" / "phase4_test_seed31"
        report = evaluate_strategies(strategies, duration=30, seed=31,
                                     traffic_profile="uniform", objective="waiting_time",
                                     output=output)
        self.assertEqual(4, report["strategy_count"])
        self.assertEqual(31, report["seed"])
        self.assertTrue((output / "optimization_report.json").is_file())
        self.assertTrue((output / "optimization_ranking.csv").is_file())
        configs = [Path(item["simulation_config"]) for item in report["ranked_results"]]
        self.assertEqual(4, len(set(configs)))
        self.assertTrue(all(path.is_file() for path in configs))
        self.assertTrue(all(item["sample_count"] == 30 for item in report["ranked_results"]))
        self.assertTrue(all(item["seed"] == 31 and item["traffic_profile"] == "uniform"
                            for item in report["ranked_results"]))
        self.assertEqual(4, len(set(item["route_file"] for item in report["ranked_results"])))


if __name__ == "__main__":
    unittest.main()
