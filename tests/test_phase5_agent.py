"""Phase 5 provider, tool validation, local prediction, and SUMO agent checks."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aatc.sumo_runtime import load_sumo_environment
from aatc.agent.agent_tools import TrafficAgentTools
from aatc.agent.meta_provider import MetaProvider
from aatc.agent.scenario_generator import generate_traffic_scenario
from aatc.agent.traffic_agent import TrafficAgent

load_sumo_environment()
SUMO_AVAILABLE = bool(os.environ.get("SUMO_HOME") and
                      (Path(os.environ["SUMO_HOME"]) / "tools").is_dir())


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeProvider:
    name = "fake_meta"

    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "echo", "arguments": "{\"value\":\"recorded\"}"},
            }]}
        return {"role": "assistant", "content": "Used the tool result.", "tool_calls": []}


class FakeTools:
    def provider_tools(self):
        return []

    def execute(self, name, arguments):
        return {"tool": name, "value": arguments["value"]}


class Phase5AgentTests(unittest.TestCase):
    def test_meta_provider_parses_chat_completion_without_logging_credentials(self):
        observed = {}

        def opener(request, timeout):
            observed["url"] = request.full_url
            observed["authorization"] = request.headers.get("Authorization")
            observed["timeout"] = timeout
            return FakeResponse({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

        provider = MetaProvider("do-not-print-this", "test-model", opener=opener)
        message = provider.complete([{"role": "user", "content": "hello"}], [])
        self.assertEqual("ok", message["content"])
        self.assertTrue(observed["url"].endswith("/chat/completions"))
        self.assertEqual("Bearer do-not-print-this", observed["authorization"])

    def test_agent_executes_validated_tool_call_then_returns_model_reply(self):
        agent = TrafficAgent(provider=FakeProvider(), tools=FakeTools(), settings={"max_tool_rounds": 2})
        result = agent.ask("inspect current sample")
        self.assertEqual("Used the tool result.", result["answer"])
        self.assertEqual("fake_meta", result["provider"])
        self.assertEqual("recorded", result["tool_steps"][0]["result"]["value"])

    def test_tool_registry_rejects_unlisted_actions_and_invalid_arguments(self):
        tools = TrafficAgentTools()
        self.assertIn("error", tools.execute("set_signal", {"phase": 3}))
        self.assertIn("error", tools.execute("run_simulation", {"duration": 10}))
        self.assertIn("error", tools.execute("optimize_signal", {"green_values": [25]}))

    def test_linear_forecast_uses_saved_sumo_samples(self):
        with tempfile.TemporaryDirectory() as temp:
            results_root = Path(temp)
            run_dir = results_root / "agent_runs" / "forecast_case"
            run_dir.mkdir(parents=True)
            columns = ["simulation_time_s"] + ["{}_queue_length".format(d) for d in ("north", "south", "east", "west")]
            with (run_dir / "traffic_state.csv").open("w", encoding="utf-8", newline="") as handle:
                handle.write(",".join(columns) + "\n")
                for i in range(3):
                    handle.write(",".join([str(i)] + [str(i)] * 4) + "\n")
            result = TrafficAgentTools(results_root=results_root).execute(
                "predict_traffic", {"experiment_dir": "agent_runs/forecast_case",
                                    "window_steps": 3, "horizon_seconds": 10})
            self.assertAlmostEqual(12.0, result["approaches"]["north"]["projected_queue"])
            self.assertIn("linear trend", result["method"])

    def test_scenario_generator_writes_configured_synthetic_flows(self):
        with tempfile.TemporaryDirectory() as temp:
            result = generate_traffic_scenario("east_peak", Path(temp) / "routes.rou.xml")
            root = Path(result["route_file"])
            self.assertEqual(1000, result["vehicles_per_hour"]["east"])
            import xml.etree.ElementTree as ET
            flows = ET.parse(root).getroot().findall("flow")
            east = next(flow for flow in flows if flow.attrib["id"] == "flow_east")
            self.assertEqual("1000", east.attrib["vehsPerHour"])

    @unittest.skipUnless(SUMO_AVAILABLE, "requires SUMO")
    def test_local_tools_run_inspect_predict_and_explain_real_sumo_data(self):
        tools = TrafficAgentTools()
        result = tools.execute("run_simulation", {"duration": 75, "seed": 19,
                                                  "traffic_profile": "uniform", "controller": "rule_based"})
        self.assertIn("metrics", result)
        self.assertEqual(75.0, result["metrics"]["actual_duration_s"])
        experiment_dir = result["experiment_dir"]
        inspected = tools.execute("inspect_experiment", {"experiment_dir": experiment_dir})
        self.assertEqual(75.0, inspected["metrics"]["actual_duration_s"])
        prediction = tools.execute("predict_traffic", {"experiment_dir": experiment_dir,
                                                       "window_steps": 30, "horizon_seconds": 60})
        self.assertEqual("SUMO traffic_state.csv", prediction["source"])
        transition = tools.execute("explain_last_transition", {"experiment_dir": experiment_dir})
        self.assertIn("last_transition", transition)
        scenario = tools.execute("generate_traffic_scenario", {"scenario": "high_uniform",
                                                               "duration": 30, "seed": 19})
        self.assertEqual("high_uniform", scenario["scenario"]["scenario"])
        self.assertEqual("high_uniform", scenario["metrics"]["traffic_profile"])


if __name__ == "__main__":
    unittest.main()
