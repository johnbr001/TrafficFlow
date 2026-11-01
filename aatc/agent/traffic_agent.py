"""Bounded tool-calling traffic assistant with an optional local fallback."""
import json
import os
from pathlib import Path

from .agent_tools import TrafficAgentTools
from .meta_provider import MetaProvider
from .llm_provider import ProviderError
from .workflow import TrafficAgentWorkflow


def _load_agent_environment(root):
    """Read only agent configuration keys from .env; do not inspect or log secrets."""
    allowed = {"MODEL_API_KEY", "META_API_KEY", "META_MODEL", "AGENT_PROVIDER"}
    path = Path(root) / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key not in allowed or os.environ.get(key):
            continue
        value = value.strip().strip("\"'")
        if value:
            os.environ[key] = value


class TrafficAgent:
    def __init__(self, provider=None, tools=None, settings=None):
        self.root = Path(__file__).resolve().parents[2]
        with (self.root / "config" / "agent.json").open("r", encoding="utf-8") as handle:
            self.settings = json.load(handle)
        if settings:
            self.settings.update(settings)
        _load_agent_environment(self.root)
        if os.environ.get("AGENT_PROVIDER"):
            self.settings["provider"] = os.environ["AGENT_PROVIDER"].strip().lower()
        self.tools = tools or TrafficAgentTools()
        self.provider = provider
        self.local_reason = None
        if self.provider is None and self.settings.get("provider") == "meta":
            self.provider = MetaProvider.from_environment(self.settings)
            if self.provider is None:
                self.local_reason = "Meta Model API credentials or model are not configured"
        self.workflow = TrafficAgentWorkflow(
            provider=self.provider,
            tools=self.tools,
            local_handler=self._ask_local,
            max_tool_rounds=self.settings.get("max_tool_rounds", 4),
        )

    def ask(self, question):
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        if self.provider is not None:
            try:
                return self.workflow.invoke(question, mode="model")
            except ProviderError as exc:
                if not self.settings.get("fallback_to_local", True):
                    return {"answer": "The configured AI provider is unavailable: {}".format(exc),
                            "provider": self.provider.name, "tool_steps": [], "evidence": []}
                return self.workflow.invoke(question, mode="local", provider_error=str(exc))
        return self.workflow.invoke(question, mode="local")

    def _ask_local(self, question, provider_error=None):
        lower = question.lower()
        profile = "imbalanced" if any(word in lower for word in ("imbalanced", "northbound", "north-south", "congested north")) else "uniform"
        steps = []

        def invoke(name, arguments):
            result = self.tools.execute(name, arguments)
            steps.append({"tool": name, "arguments": arguments, "result": result})
            return result

        if any(word in lower for word in ("ppo", "reinforcement learning", "rl controller")) and any(
                word in lower for word in ("compar", "versus", " vs ", "against", "evaluate")):
            model_name = "ppo_traffic_seed_42"
            for token in question.replace(".zip", "").split():
                if token.startswith("ppo_"):
                    model_name = token.strip("`.,:;!?()[]{}\"'")
                    break
            result = invoke("compare_ppo_with_fixed", {"model_name": model_name,
                                                         "duration": 180, "seed": 42,
                                                         "traffic_profile": profile})
            answer = self._format_ppo_comparison(result)
        elif any(word in lower for word in ("optimiz", "best signal", "best timing", "optimized")):
            result = invoke("optimize_signal", {"duration": 120, "seed": 42,
                                                   "traffic_profile": profile, "objective": "balanced",
                                                   "green_values": [20, 30]})
            answer = self._format_optimization(result)
        elif any(word in lower for word in ("scenario", "rush", "traffic spike", "traffic surge")):
            scenario = "east_peak" if "east" in lower else "high_uniform" if "uniform" in lower else "north_peak"
            result = invoke("generate_traffic_scenario", {"scenario": scenario,
                                                            "duration": 180, "seed": 42})
            answer = self._format_simulation(result)
        elif any(word in lower for word in ("compare", "comparison", "rule-based", "fixed-time")):
            result = invoke("compare_controllers", {"duration": 180, "seed": 42,
                                                       "traffic_profile": profile})
            answer = self._format_comparison(result)
        elif any(word in lower for word in ("predict", "forecast")):
            result = invoke("predict_traffic", {"window_steps": 30, "horizon_seconds": 60})
            if "error" in result and "No saved agent SUMO run" in result["error"]:
                run_result = invoke("run_simulation", {"duration": 180, "seed": 42,
                                                        "traffic_profile": profile, "controller": "fixed_time"})
                if "experiment_dir" in run_result:
                    result = invoke("predict_traffic", {"experiment_dir": run_result["experiment_dir"],
                                                         "window_steps": 30, "horizon_seconds": 60})
            answer = self._format_prediction(result)
        elif any(word in lower for word in ("last transition", "last signal change", "why did the signal")):
            directory = self._latest_agent_run()
            if directory is None:
                run_result = invoke("run_simulation", {"duration": 180, "seed": 42,
                                                        "traffic_profile": profile, "controller": "rule_based"})
                directory = run_result.get("experiment_dir")
            result = invoke("explain_last_transition", {"experiment_dir": directory}) if directory else {"error": "No recorded run was produced"}
            answer = self._format_transition(result)
        elif any(word in lower for word in ("analy", "traffic", "queue", "congestion", "situation")):
            controller = "rule_based" if "adaptive" in lower or "rule" in lower else "fixed_time"
            result = invoke("run_simulation", {"duration": 180, "seed": 42,
                                                "traffic_profile": profile, "controller": controller})
            answer = self._format_simulation(result)
        else:
            answer = ("I can run a SUMO traffic analysis, compare fixed-time and rule-based control, "
                      "optimize green splits, project queues from a saved run, or explain a recorded "
                      "signal transition. Ask for one of those and specify uniform or imbalanced demand.")
        response = {"answer": answer, "provider": "local_tools", "tool_steps": steps,
                    "evidence": [step["result"] for step in steps]}
        if provider_error:
            response["provider_fallback_reason"] = provider_error
        elif self.local_reason:
            response["provider_fallback_reason"] = self.local_reason
        return response

    def _latest_agent_run(self):
        base = self.tools.results_root / "agent_runs"
        if not base.is_dir():
            return None
        files = list(base.rglob("traffic_state.csv"))
        if not files:
            return None
        return self.tools._return_path(max(files, key=lambda path: path.stat().st_mtime).parent)

    @staticmethod
    def _format_simulation(result):
        metrics = result.get("metrics", {})
        if "error" in result:
            return "SUMO analysis failed: {}".format(result["error"])
        scenario = result.get("scenario", {})
        scenario_text = (" Generated {} demand at {}."
                         .format(scenario.get("scenario"), scenario.get("vehicles_per_hour"))
                         if scenario else "")
        return ("SUMO run {} ({}, seed {}, {}s): mean queue {:.2f}, mean waiting {:.2f}s, "
                "average completed-trip time {}, completed {} of {} departed vehicles. "
                "Saved under {}.{}".format(metrics.get("run_id"), metrics.get("traffic_profile"),
                metrics.get("seed"), metrics.get("actual_duration_s"),
                metrics.get("mean_total_queue_length", 0), metrics.get("mean_total_waiting_time_s", 0),
                metrics.get("average_travel_time_s"), metrics.get("completed_vehicles"),
                metrics.get("departed_vehicles"), result.get("experiment_dir"), scenario_text))

    @staticmethod
    def _format_comparison(result):
        if "error" in result:
            return "SUMO comparison failed: {}".format(result["error"])
        results = result.get("comparison", {}).get("results", [])
        values = "; ".join("{}: mean queue {:.2f}, mean waiting {:.2f}s, completed {}"
                            .format(row["controller"], row["mean_total_queue_length"],
                                    row["mean_total_waiting_time_s"], row["completed_vehicles"])
                            for row in results)
        return "Measured SUMO comparison ({}): {}. Artifacts: {}.".format(
            result.get("comparison", {}).get("traffic_profile"), values, result.get("experiment_dir"))

    @staticmethod
    def _format_optimization(result):
        if "error" in result:
            return "SUMO optimization failed: {}".format(result["error"])
        best = result.get("best_strategy", {})
        top = result.get("ranked_results", [{}])[0]
        return ("Best measured candidate: NS green {}s, EW green {}s; mean queue {:.2f}, "
                "mean waiting {:.2f}s, completed {} vehicles. {} candidates were run. Artifacts: {}."
                .format(best.get("green_ns_s"), best.get("green_ew_s"), top.get("mean_queue", 0),
                        top.get("mean_waiting_time_s", 0), top.get("completed_vehicles", 0),
                        len(result.get("ranked_results", [])), result.get("experiment_dir")))

    @staticmethod
    def _format_ppo_comparison(result):
        if "error" in result:
            return "PPO comparison failed: {}".format(result["error"])
        fixed = result.get("fixed_time", {})
        ppo = result.get("ppo", {})
        return ("Measured SUMO comparison for model {} ({}): fixed-time mean queue {}, PPO mean queue {}; "
                "fixed-time mean waiting {}s, PPO mean waiting {}s. Artifacts: {}."
                .format(result.get("model_name"), result.get("traffic_profile"),
                        fixed.get("mean_total_queue_length"), ppo.get("mean_total_queue_length"),
                        fixed.get("mean_total_waiting_time_s"), ppo.get("mean_total_waiting_time_s"),
                        result.get("experiment_dir")))

    @staticmethod
    def _format_prediction(result):
        if "error" in result:
            return "No forecast was produced: {}".format(result["error"])
        queues = result.get("approaches", {})
        formatted = ", ".join("{} {:.1f}".format(direction, values["projected_queue"])
                               for direction, values in queues.items())
        return ("A {}-second linear projection from {} recent SUMO samples estimates queues: {}. {}"
                .format(result.get("horizon_seconds"), result.get("samples_used"), formatted,
                        result.get("warning")))

    @staticmethod
    def _format_transition(result):
        transition = result.get("last_transition")
        if not transition:
            return "No transition explanation is available: {}".format(result.get("error", "no data"))
        return ("Recorded SUMO signal transition at {}s: phase {} to {} (state {}). Controller request: {}; "
                "safety accepted: {} ({})".format(transition.get("time_s"), transition.get("from_phase"),
                transition.get("to_phase"), transition.get("signal_state"),
                transition.get("controller_request"), transition.get("safety_accepted"),
                transition.get("safety_reason")))
