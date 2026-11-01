"""Bounded LLM-guided search over PPO signal phase timing constraints."""
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from aatc.agent.llm_provider import ProviderError
from aatc.agent.meta_provider import MetaProvider
from .rl_workflow import train_city_controller, evaluate_city_controller


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_provider():
    from aatc.agent.traffic_agent import _load_agent_environment
    _load_agent_environment(PROJECT_ROOT)
    settings = json.loads((PROJECT_ROOT / "config" / "agent.json").read_text(encoding="utf-8"))
    if os.environ.get("AGENT_PROVIDER", settings.get("provider", "meta")).strip().lower() != "meta":
        return None, "Meta provider is not selected"
    provider = MetaProvider.from_environment(settings)
    if provider is None:
        return None, "Meta API credentials or META_MODEL are not configured"
    return provider, None


def _proposal(provider, iteration, history, current):
    allowed_intervals = [5, 10, 15]
    allowed_min = [8, 10, 15]
    allowed_max = [35, 45, 60]
    if provider:
        messages = [
            {"role": "system", "content": (
                "You are advising a bounded SUMO traffic-signal experiment. Propose the next candidate "
                "by changing only PPO decision interval and minimum/maximum green constraints. The SUMO "
                "phase clearance program is immutable. Keep demand, date, seed, and network identical for "
                "paired comparison. Prefer a different legal setting informed by measured outcomes. "
                "Return only one JSON object with keys reason, decision_interval_s, min_green_s, max_green_s. "
                "Allowed decision interval values: 5, 10, 15. Allowed minimum green: 8, 10, 15. "
                "Allowed maximum green: 35, 45, 60, and it must exceed minimum green. State concise textual reasoning.")},
            {"role": "user", "content": json.dumps({"iteration": iteration,
                "previous_results": history[-5:], "last_settings": current}, ensure_ascii=False)},
        ]
        try:
            message = provider.complete(messages, [])
            content = message.get("content", "")
            if isinstance(content, list):
                content = "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
            match = re.search(r"\{.*\}", str(content), flags=re.DOTALL)
            data = json.loads(match.group(0)) if match else {}
            interval = int(data.get("decision_interval_s"))
            min_green = int(data.get("min_green_s"))
            max_green = int(data.get("max_green_s"))
            if interval not in allowed_intervals or min_green not in allowed_min or max_green not in allowed_max or max_green <= min_green:
                raise ValueError("The proposed timing settings are outside the permitted search range")
            reason = str(data.get("reason", "The model selected a constrained follow-up candidate."))[:1200]
            return {"decision_interval_s": interval, "min_green_s": min_green,
                    "max_green_s": max_green, "reasoning": reason, "reasoning_provider": "meta"}
        except (ProviderError, ValueError, TypeError, json.JSONDecodeError) as exc:
            fallback_reason = "Meta reasoning unavailable ({}); using the next predefined safe timing candidate.".format(exc)
    else:
        fallback_reason = "Meta reasoning unavailable ({}); using the next predefined safe timing candidate.".format(
            "provider is not configured")

    candidates = [(10, 10, 45), (5, 15, 45), (15, 8, 35), (10, 15, 60), (5, 8, 60)]
    settings = candidates[(iteration - 1) % len(candidates)]
    if not history:
        reason = ("Start with a 10-second decision interval and a 45-second green cap. This tests whether "
                  "less frequent policy changes and shorter maximum green reduce delay without changing demand. ")
    else:
        previous = history[-1]
        values = previous.get("adaptive", {})
        reason = ("Follow candidate {} (queue {}, wait {} sec, completed {}). Change to a {}-second "
                  "decision interval and {}–{} second green bounds to test a different responsiveness/hold-time "
                  "balance against the identical route demand. ").format(
                      previous.get("iteration"), values.get("mean_total_queue_length", "n/a"),
                      values.get("mean_total_waiting_time_s", "n/a"), values.get("completed_vehicles", "n/a"),
                      settings[0], settings[1], settings[2])
    reason += fallback_reason
    return {"decision_interval_s": settings[0], "min_green_s": settings[1],
            "max_green_s": settings[2], "reasoning": reason, "reasoning_provider": "local_fallback"}


def _beats_reference(baseline, adaptive):
    """Require lower queue, wait and travel time without fewer completed trips."""
    pairs = (("mean_total_queue_length", "mean_total_queue_length"),
             ("mean_total_waiting_time_s", "mean_total_waiting_time_s"),
             ("average_travel_time_s", "average_travel_time_s"))
    try:
        better = all(float(adaptive[key]) < float(baseline[key]) for key, _ in pairs)
        completed_ok = int(adaptive["completed_vehicles"]) >= int(baseline["completed_vehicles"])
        return better and completed_ok
    except (KeyError, TypeError, ValueError):
        return False


def _objective_score(baseline, adaptive):
    """Comparable lower-is-better score; all components are normalized to baseline."""
    try:
        queue = float(adaptive["mean_total_queue_length"]) / max(float(baseline["mean_total_queue_length"]), 0.1)
        waiting = float(adaptive["mean_total_waiting_time_s"]) / max(float(baseline["mean_total_waiting_time_s"]), 1.0)
        travel = float(adaptive["average_travel_time_s"]) / max(float(baseline["average_travel_time_s"]), 1.0)
        completion = float(adaptive["completed_vehicles"]) / max(float(baseline["completed_vehicles"]), 1.0)
        return round((queue + waiting + travel + (1.0 / max(completion, 0.01))) / 4.0, 6)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return float("inf")


def search_better_city_plan(city="Seoul", target_datetime=None, duration=900, seed=42,
                            radius_km=1.5, base_density_veh_per_hour_km=8.0,
                            budget_seconds=600, max_iterations=5, training_timesteps=2048,
                            progress_callback=None):
    """Train/evaluate candidates until strict improvement or the bounded budget is spent.

    A single candidate's train/evaluate call is not interruptible. The wall-clock budget is
    checked between complete candidates, so a final candidate can finish after the budget.
    """
    budget_seconds = max(60, min(int(budget_seconds), 3600))
    max_iterations = max(1, min(int(max_iterations), 12))
    target_datetime = target_datetime or datetime.now().replace(second=0, microsecond=0)
    if isinstance(target_datetime, str):
        target_datetime = datetime.fromisoformat(target_datetime)
    provider, provider_error = _load_provider()
    started = time.monotonic()
    history, best = [], None
    current = {"decision_interval_s": 5, "min_green_s": 10, "max_green_s": 60}
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    search_dir = PROJECT_ROOT / "experiments" / "results" / "city_runs" / (
        "SEARCH_{}_{}_{}".format(str(city).upper(), int(seed), run_id))
    search_dir.mkdir(parents=True, exist_ok=True)
    stop_reason = "maximum iterations reached"

    for iteration in range(1, max_iterations + 1):
        if iteration > 1 and time.monotonic() - started >= budget_seconds:
            stop_reason = "time budget reached between candidate runs"
            break
        proposal = _proposal(provider, iteration, history, current)
        current = {key: proposal[key] for key in ("decision_interval_s", "min_green_s", "max_green_s")}
        item = {"iteration": iteration, **proposal, "status": "running"}
        if progress_callback:
            progress_callback({"event": "candidate_start", "iteration": iteration,
                               "reasoning": proposal["reasoning"], "settings": current,
                               "elapsed_seconds": round(time.monotonic() - started)})
        model_stem = search_dir / "candidate_{}_ppo".format(iteration)
        try:
            trained = train_city_controller(city=city, target_datetime=target_datetime,
                duration=duration, seed=seed, radius_km=radius_km,
                timesteps=training_timesteps, base_density_veh_per_hour_km=base_density_veh_per_hour_km,
                output=str(model_stem), **{key: current[key] for key in current})
            evaluated = evaluate_city_controller(trained["model_path"], city=city,
                target_datetime=target_datetime, duration=duration, seed=seed, radius_km=radius_km,
                base_density_veh_per_hour_km=base_density_veh_per_hour_km,
                **{key: current[key] for key in current})
            comparison = evaluated["comparison"]
            baseline, adaptive = comparison.get("baseline", {}), comparison.get("ppo", {})
            item.update({"status": "complete", "model_path": trained["model_path"],
                         "comparison_path": evaluated["comparison_path"],
                         "experiment_dir": evaluated["experiment_dir"],
                         "baseline": {key: baseline.get(key) for key in (
                             "mean_total_queue_length", "mean_total_waiting_time_s",
                             "average_travel_time_s", "completed_vehicles")},
                         "adaptive": {key: adaptive.get(key) for key in (
                             "mean_total_queue_length", "mean_total_waiting_time_s",
                             "average_travel_time_s", "completed_vehicles")},
                         "beats_reference": _beats_reference(baseline, adaptive),
                         "objective_score": _objective_score(baseline, adaptive),
                         "elapsed_seconds": round(time.monotonic() - started)})
            history.append(item)
            if best is None or item["objective_score"] < best.get("objective_score", float("inf")):
                best = item
            if progress_callback:
                progress_callback({"event": "candidate_complete", **item})
            if item["beats_reference"]:
                stop_reason = "candidate beat fixed-time reference on queue, waiting, travel time, and completed trips"
                break
        except (OSError, RuntimeError, ValueError) as exc:
            item.update({"status": "failed", "error": str(exc),
                         "elapsed_seconds": round(time.monotonic() - started)})
            history.append(item)
            if progress_callback:
                progress_callback({"event": "candidate_failed", **item})
            stop_reason = "candidate failed; see iteration details"
                
    result = {"search_type": "llm_guided_bounded_city_ppo_search", "city": city,
              "target_datetime": target_datetime.isoformat(), "duration_s": int(duration),
              "seed": int(seed), "radius_km": float(radius_km),
              "base_density_veh_per_hour_km": float(base_density_veh_per_hour_km),
              "budget_seconds": budget_seconds, "elapsed_seconds": round(time.monotonic() - started),
              "max_iterations": max_iterations, "completed_iterations": len(history),
              "reasoning_provider": ("meta" if any(item.get("reasoning_provider") == "meta"
                                                       for item in history) else "local_fallback"),
              "reasoning_setup_note": provider_error, "stop_reason": stop_reason,
              "success": bool(best and best.get("beats_reference")), "best_candidate": best,
              "iterations": history}
    path = search_dir / "search_report.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["report_path"] = str(path.resolve())
    return result
