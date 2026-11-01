"""Create a decision-maker report from a completed SUMO/PPO comparison."""
import json
import os
import csv
from pathlib import Path

from aatc.agent.meta_provider import MetaProvider
from aatc.agent.llm_provider import ProviderError


def _load_agent_environment(root):
    allowed = {"MODEL_API_KEY", "META_API_KEY", "META_MODEL", "AGENT_PROVIDER"}
    env_path = Path(root) / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key in allowed and value and not os.environ.get(key):
            os.environ[key] = value


def _number(data, key):
    value = data.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _change(before, after, lower_is_better=True):
    if before is None or after is None or before == 0:
        return "not available"
    pct = (after - before) / abs(before) * 100
    label = "higher" if pct > 0 else "lower"
    if not lower_is_better:
        label = "higher" if pct > 0 else "lower"
    return "{:+.2f} ({:.1f}% {})".format(after - before, abs(pct), label)


def _facts(report):
    baseline, adaptive = report.get("baseline") or {}, report.get("ppo") or {}
    scenario_data = (baseline.get("scenario") or {})
    keys = ("mean_total_queue_length", "mean_total_waiting_time_s", "average_travel_time_s",
            "completed_vehicles", "departed_vehicles", "network_throughput_vehicles_per_hour")
    return {"scenario": {"city": report.get("city"), "region_code": report.get("region_code"),
                          "target_datetime": report.get("target_datetime"),
                          "duration_s": report.get("duration_s"), "seed": report.get("seed"),
                          "radius_km": report.get("radius_km"), "signal_count": report.get("signal_count"),
                          "requested_scenario": report.get("scenario_description"),
                          "demand_note": report.get("demand_assumption_note") or baseline.get("demand_assumption_note"),
                          "traffic_profile": baseline.get("traffic_profile"),
                          "day_type": baseline.get("day_type"),
                          "prompt_demand_multiplier": report.get("prompt_demand_multiplier", 1.0),
                          "prompt_demand_assumption": report.get("prompt_demand_assumption"),
                          "calendar_demand_multiplier": scenario_data.get("combined_relative_demand_multiplier",
                                                                           baseline.get("relative_demand_multiplier")),
                          "configured_insertion_density": baseline.get("base_insertion_density_veh_per_hour_km"),
                          "effective_insertion_density": baseline.get("effective_insertion_density_veh_per_hour_km"),
                          "active_events": scenario_data.get("active_events", []),
                          "applied_event_ids": scenario_data.get("applied_event_ids", []),
                          "controller": report.get("controller")},
            "before_fixed_time": {key: _number(baseline, key) for key in keys},
            "after_adaptive_rl": {key: _number(adaptive, key) for key in keys},
            "iterative_search": [{"iteration": item.get("iteration"),
                                  "reasoning": item.get("reasoning"),
                                  "settings": {key: item.get(key) for key in
                                               ("decision_interval_s", "min_green_s", "max_green_s")},
                                  "adaptive_metrics": item.get("adaptive"),
                                  "beats_reference": item.get("beats_reference"),
                                  "status": item.get("status")}
                                 for item in (report.get("iterative_search") or {}).get("iterations", [])],
            "interpretation": report.get("interpretation")}


def _relative_change(before, after, label):
    try:
        before, after = float(before), float(after)
    except (TypeError, ValueError):
        return "{}: unavailable".format(label)
    if before == 0:
        return "{}: {} (reference was zero)".format(label, _fmt(after))
    delta = (after - before) / abs(before) * 100.0
    direction = "higher" if delta > 0 else "lower" if delta < 0 else "unchanged"
    return "{} {:.1f}% {}".format(label, abs(delta), direction)


def _iteration_reasoning(item):
    reason = str(item.get("reasoning", "")).strip()
    if item.get("reasoning_provider") == "meta":
        return reason or "Meta returned no explanation."
    if reason.startswith("Textual fallback:"):
        return "Predefined fallback sequence; see search_report.json for the raw fallback message."
    marker = " Meta reasoning unavailable ("
    if marker in reason:
        reason = reason.split(marker, 1)[0].strip()
    return reason or "Predefined fallback candidate."


def _iteration_interpretation(item):
    baseline, adaptive = item.get("baseline", {}), item.get("adaptive", {})
    measures = [
        _relative_change(baseline.get("mean_total_queue_length"),
                         adaptive.get("mean_total_queue_length"), "queue"),
        _relative_change(baseline.get("mean_total_waiting_time_s"),
                         adaptive.get("mean_total_waiting_time_s"), "waiting"),
        _relative_change(baseline.get("average_travel_time_s"),
                         adaptive.get("average_travel_time_s"), "trip time"),
    ]
    try:
        completed_delta = int(adaptive["completed_vehicles"]) - int(baseline["completed_vehicles"])
        measures.append("completed trips {:+d}".format(completed_delta))
    except (KeyError, TypeError, ValueError):
        completed_delta = None
        measures.append("completed trips unavailable")
    if item.get("beats_reference"):
        assessment = "Meets the full improvement rule."
    else:
        try:
            queue_up = float(adaptive["mean_total_queue_length"]) > float(baseline["mean_total_queue_length"])
            wait_up = float(adaptive["mean_total_waiting_time_s"]) > float(baseline["mean_total_waiting_time_s"])
            trip_up = float(adaptive["average_travel_time_s"]) > float(baseline["average_travel_time_s"])
        except (KeyError, TypeError, ValueError):
            queue_up = wait_up = trip_up = False
        if queue_up or wait_up:
            assessment = "Reject for this scenario: queue or waiting regressed."
        elif trip_up or (completed_delta is not None and completed_delta < 0):
            assessment = "Promising congestion result, but trip time or completed trips regressed."
        else:
            assessment = "Did not satisfy the full improvement rule."
    return "{} **{}**".format("; ".join(measures), assessment)


def _signal_comparison(report):
    baseline_path = Path((report.get("baseline") or {}).get("state_csv", ""))
    adaptive_path = Path(report.get("ppo_signal_state_csv", ""))
    if not baseline_path.is_file() or not adaptive_path.is_file():
        return []

    def averages(path, signal_id):
        prefix = "tls_" + str(signal_id).replace("-", "_").replace(":", "_")
        values = {"queue": [], "waiting": []}
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                for metric, suffix in (("queue", "_queue_length"), ("waiting", "_waiting_time_s")):
                    try:
                        values[metric].append(float(row[prefix + suffix]))
                    except (KeyError, TypeError, ValueError):
                        pass
        return {key: sum(items) / len(items) if items else None for key, items in values.items()}

    rows = []
    for index, signal_id in enumerate(report.get("signal_ids", []), 1):
        before, after = averages(baseline_path, signal_id), averages(adaptive_path, signal_id)
        q0, q1 = before["queue"], after["queue"]
        if q0 is None or q1 is None:
            continue
        delta = q1 - q0
        status = "Queue lower" if delta < -0.01 else "Queue higher" if delta > 0.01 else "About the same"
        rows.append({"signal": "S{}".format(index), "id": str(signal_id),
                     "queue_before": q0, "queue_after": q1, "queue_delta": delta,
                     "wait_before": before["waiting"], "wait_after": after["waiting"],
                     "status": status})
    return rows


def _local_markdown(report):
    facts = _facts(report)
    scenario = facts["scenario"]
    before, after = facts["before_fixed_time"], facts["after_adaptive_rl"]
    q0, q1 = before["mean_total_queue_length"], after["mean_total_queue_length"]
    w0, w1 = before["mean_total_waiting_time_s"], after["mean_total_waiting_time_s"]
    travel0, travel1 = before["average_travel_time_s"], after["average_travel_time_s"]
    done0, done1 = before["completed_vehicles"], after["completed_vehicles"]
    improves = ((q0 is None or q1 is None or q1 <= q0) and
                (w0 is None or w1 is None or w1 <= w0) and
                (travel0 is None or travel1 is None or travel1 <= travel0) and
                (done0 is None or done1 is None or done1 >= done0))
    if improves:
        recommendation = ("In this run, the adaptive candidate improved or matched queue, waiting, trip time, and "
                         "completed trips. It is a promising simulation result, not an operational approval; "
                         "repeat it across more demand levels and random seeds before considering adoption.")
    elif (q0 is not None and q1 is not None and q1 < q0 and
          w0 is not None and w1 is not None and w1 < w0):
        recommendation = ("Retain the fixed-time reference for now. The adaptive candidate reduced mean queue "
            "from {} to {} vehicles; the change was {}. Mean waiting fell from {} to {} seconds; the change "
            "was {}. However, average trip time changed from {} to {} seconds; the change was {}, and "
            "completed trips changed from {} to {}. It therefore did "
            "not meet the full improvement rule: lower queue, waiting, and trip time, with at least as many "
            "completed trips. Tune and re-evaluate before selecting it.").format(
                _fmt(q0), _fmt(q1), _change(q0, q1), _fmt(w0), _fmt(w1), _change(w0, w1),
                _fmt(travel0), _fmt(travel1), _change(travel0, travel1), _fmt(done0), _fmt(done1))
    else:
        recommendation = ("Retain the fixed-time reference for this scenario. At least one key measure—queue, "
                          "waiting, trip time, or completed trips—was worse or unavailable under the adaptive "
                          "candidate. Review the measure table and junction results, then tune and repeat the run.")
    note = scenario.get("demand_note") or "Demand calibration details are not present in the comparison record."
    baseline_data = report.get("baseline") or {}
    scenario_data = baseline_data.get("scenario") or {}
    event_names = [event.get("name") for event in scenario_data.get("active_events", []) if event.get("name")]
    if event_names and not scenario_data.get("applied_event_ids"):
        event_summary = ("Calendar metadata lists {} for this date range, but no event-specific demand multiplier "
                         "was applied at the simulated date and time.".format(", ".join(event_names)))
    elif scenario_data.get("applied_event_ids"):
        event_summary = "Event-specific demand IDs applied: {}.".format(", ".join(scenario_data["applied_event_ids"]))
    else:
        event_summary = "No event-specific traffic adjustment was applied."
    demand_summary = ("Profile: {} on a {}. Prompt assumption: {} (×{:.2f}). Calendar multiplier: ×{}. "
        "Configured insertion density: {} vehicles/hour/km; effective density after calendar scaling: {} vehicles/hour/km. {} "
        "These are simulation assumptions, not observed traffic counts.").format(
            str(baseline_data.get("traffic_profile", "not recorded")).replace("_", " "),
            scenario.get("day_type", baseline_data.get("day_type", "day type not recorded")),
            report.get("prompt_demand_assumption", "Standard overall demand"),
            float(report.get("prompt_demand_multiplier", 1.0)),
            _fmt(_number(scenario, "calendar_demand_multiplier")),
            _fmt(_number(scenario, "configured_insertion_density")),
            _fmt(_number(scenario, "effective_insertion_density")), event_summary)
    signals = _signal_comparison(report)
    signal_rows = ["| {} | `{}` | {:.2f} → {:.2f} | {:.1f} → {:.1f} | {} |".format(
        row["signal"], row["id"], row["queue_before"], row["queue_after"],
        row["wait_before"] or 0.0, row["wait_after"] or 0.0, row["status"])
        for row in signals]
    worst_signal = max(signals, key=lambda row: row["queue_delta"]) if signals else None
    best_signal = min(signals, key=lambda row: row["queue_delta"]) if signals else None
    if q0 is not None and q1 is not None and q1 < q0 and w0 is not None and w1 is not None and w1 < w0:
        plain_summary = ("The adaptive controller reduced the network's average queue and waiting in this run. "
                         "However, the overall decision rule also checks trip time and completed trips. "
                         "Review those measures before treating the candidate as better.")
    elif q0 is not None and q1 is not None and (q1 > q0 or (w0 is not None and w1 is not None and w1 > w0)):
        plain_summary = ("The adaptive controller increased queue or waiting compared with the fixed-time "
                         "reference. For this scenario, the fixed-time plan remains the safer choice to carry forward.")
    else:
        plain_summary = ("The results are mixed or incomplete. The candidate does not establish a clear "
                         "network-wide benefit; use the signal-level results and repeat the comparison.")
    if worst_signal and worst_signal["queue_delta"] > 0.01:
        signal_highlight = "Largest queue increase: {} ({:.2f} to {:.2f} vehicles).".format(
            worst_signal["signal"], worst_signal["queue_before"], worst_signal["queue_after"])
    else:
        signal_highlight = "No signal showed a material increase in mean queue in the saved measurements."
    if best_signal and best_signal["queue_delta"] < -0.01:
        signal_highlight += " Largest decrease: {} ({:.2f} to {:.2f} vehicles).".format(
            best_signal["signal"], best_signal["queue_before"], best_signal["queue_after"])
    search = report.get("iterative_search") or {}
    search_rows = []
    reasoning_modes = {item.get("reasoning_provider") for item in search.get("iterations", [])}
    if search and "meta" not in reasoning_modes:
        search_note = ("Meta did not supply per-step reasoning for this run. The candidates used the bounded "
                       "fallback sequence; each interpretation below is calculated from the SUMO measurements.")
    else:
        search_note = "Meta proposed the candidates; each interpretation below is calculated from the SUMO measurements."
    for item in search.get("iterations", []):
        if item.get("status") != "complete":
            search_rows.append("| {} | {} | Failed | {} |".format(
                item.get("iteration", "—"), item.get("reasoning_provider", "—"),
                str(item.get("error", "candidate failed")).replace("|", "/")))
            continue
        settings = "interval {}s, green {}–{}s".format(item.get("decision_interval_s"),
                                                        item.get("min_green_s"), item.get("max_green_s"))
        reasoning = _iteration_reasoning(item).replace("|", "/").replace("\n", " ")
        interpretation = _iteration_interpretation(item).replace("|", "/").replace("\n", " ")
        search_rows.append("| {} | {} | {} | {} |".format(
            item.get("iteration"), settings,
            "Yes" if item.get("beats_reference") else "No",
            "**Plan:** {}<br>**Measured:** {}".format(reasoning, interpretation)))
    search_section = ""
    if search:
        search_section = ("\n## Iterative search log\n\n"
            "**Search outcome:** {} · {} of {} candidate runs · {} seconds elapsed against a {}-second budget. "
            "The active SUMO candidate is allowed to finish after the budget expires.\n\n"
            "{} Each step lists the timing change, planner rationale when available, and a separate "
            "interpretation of measured queue, waiting, travel-time, and completed-trip changes.\n\n"
            "| Step | Changed plan constraints | Beat reference? | Plan rationale and measured interpretation |\n"
            "|---:|---|:---:|---|\n{}\n").format(
                "Better plan found" if search.get("success") else "No candidate met the full improvement rule",
                search.get("completed_iterations", 0), search.get("max_iterations", 0),
                search.get("elapsed_seconds", 0), search.get("budget_seconds", 0),
                search_note,
                "\n".join(search_rows) or "| — | — | — | — | No iterations recorded |")
    return """# Simulation Outcome Report: {city} Adaptive Signal Plan

**Scenario:** {target_datetime} · {duration} minutes · {signals} signals · seed {seed}  
**Area:** {radius} km network radius · **Result:** SUMO simulation, not a live traffic intervention

**Scenario requested:** {requested_scenario}

## Executive decision

{recommendation}

The evaluation compares the **before** plan (SUMO fixed-time reference) with the **after** plan (PPO adaptive controller) under the same generated route demand. The adaptive controller selects signal phases during simulation; this result is not a fixed daily timing schedule or an approved government plan.

## What the result means

{plain_summary}

**At a glance:** {signal_highlight}

## Scenario and demand assumptions

{demand_summary}

### How to read the measures

- **Mean queue:** the average number of stopped vehicles on roads entering the modeled signals, averaged across simulation time. Lower is generally better.
- **Mean waiting:** SUMO's average measured waiting-time total on the modeled incoming roads. It is a simulation measure, not a person-hours or citywide delay total. Lower is generally better.
- **Average trip time:** average time for vehicles that completed their route during this run. Lower is generally better; compare it alongside completed trips.
- **Network throughput:** completed trips divided by simulated hours. This is the primary city-scale PPO objective; it counts vehicles reaching destinations, not simply entering the map.
- **Completed trips:** vehicles that reached their destination before the 15-minute simulation ended. More is generally better, but trips still on the road at the end are not counted as completed.
- **Departed vehicles:** vehicles that entered the network during the run. This is not the same as completed trips.

## Before and after

| Measure | Before: fixed-time | After: adaptive RL | Change |
|---|---:|---:|---:|
| Mean queue | {q0} vehicles | {q1} vehicles | {qchange} |
| Mean waiting time | {w0} sec | {w1} sec | {wchange} |
| Average trip time | {t0} sec | {t1} sec | {tchange} |
| Network throughput | {flow0} vehicles/hour | {flow1} vehicles/hour | {flowchange} |
| Completed trips | {d0} | {d1} | {dchange} |
| Departed vehicles | {depart0} | {depart1} | — |

## What changed after applying RL

The simulation applied PPO (**Proximal Policy Optimization**, a reinforcement-learning method that learns signal choices through simulated trials) to {signals} signal controllers. The controller could reconsider its action every {decision_interval} seconds, subject to a {min_green}–{max_green} second green-time guard and SUMO's signal-clearance phases. Queue and delay changes above are measured outcomes; they do not explain by themselves why a junction changed. Engineering review is still needed before any real-world use.
{search_section}

## Results by signal

The identifiers below are SUMO/OSM signal IDs. The dashboard map places them geographically. A lower queue at one junction can coexist with worse performance elsewhere, so use this table with the network-wide totals above.

| Signal | Map/network ID | Mean queue: before → after (vehicles) | Mean waiting: before → after (sec) | Queue result |
|---|---|---:|---:|---|
{signal_rows}

## Assessment and next action

{recommendation}

Review junction-level queue changes in the dashboard map, then repeat the comparison across multiple seeds, time periods, and calibrated demand levels. Investigate any junction where the adaptive queue or waiting time increased before changing a plan.

## Scope and data quality

- Scenario: {day} at {target_datetime}; simulation duration {duration} minutes; seed {seed}.
- Study area: {radius} km radius, with {signals} signal controllers included in the SUMO subnetwork.
- Demand was generated with SUMO's random trip generator using a scenario density assumption. It is not an observed vehicle count or calibrated origin-destination matrix.
- {note}
- Results cover only the modeled {radius} km subnetwork and this single simulation run. They are not observed field outcomes.
- The fixed-time reference is generated for the SUMO network and should not be described as official signal timing unless independently verified.

## Traceability

Machine-readable comparison: `comparison.json` in this report’s experiment folder. The dashboard also provides the signal-by-signal table and OpenStreetMap queue-change map.
""".format(city=scenario.get("city") or "City", target_datetime=scenario.get("target_datetime") or "unknown time",
           duration=round(float(scenario.get("duration_s") or 0) / 60), signals=scenario.get("signal_count") or "unknown",
           decision_interval=report.get("decision_interval_s", "not recorded"),
           min_green=_fmt(_number(report, "min_green_s")), max_green=_fmt(_number(report, "max_green_s")),
           radius=scenario.get("radius_km") or "unknown", seed=scenario.get("seed") or "unknown",
           requested_scenario=scenario.get("requested_scenario") or "No additional scenario prompt was saved.",
           demand_summary=demand_summary,
           recommendation=recommendation, q0=_fmt(q0), q1=_fmt(q1), qchange=_change(q0, q1),
           plain_summary=plain_summary, signal_highlight=signal_highlight,
           signal_rows="\n".join(signal_rows) if signal_rows else "| No per-signal time series available | — | — | — | — |",
           w0=_fmt(w0), w1=_fmt(w1), wchange=_change(w0, w1), t0=_fmt(travel0), t1=_fmt(travel1),
           tchange=_change(travel0, travel1), d0=_fmt(done0), d1=_fmt(done1),
           flow0=_fmt(before["network_throughput_vehicles_per_hour"]),
           flow1=_fmt(after["network_throughput_vehicles_per_hour"]),
           flowchange=_relative_change(before["network_throughput_vehicles_per_hour"],
                                       after["network_throughput_vehicles_per_hour"], "throughput"),
           dchange="{:+.0f}".format(done1-done0) if done0 is not None and done1 is not None else "not available",
           depart0=_fmt(before["departed_vehicles"]), depart1=_fmt(after["departed_vehicles"]), note=note,
           day=(report.get("baseline") or {}).get("day_type", "selected day"),
           search_section=search_section)


def _fmt(value):
    if value is None:
        return "not available"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "{:.2f}".format(value) if isinstance(value, float) else str(value)


def _meta_narrative(report, root):
    _load_agent_environment(root)
    settings_path = Path(root) / "config" / "agent.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    # The narrative needs room for a complete decision explanation. Keep the
    # shorter general-agent token budget for chat and use a larger report budget.
    settings["max_tokens"] = max(int(settings.get("max_tokens", 800)), 1800)
    if os.environ.get("AGENT_PROVIDER", settings.get("provider", "meta")).strip().lower() != "meta":
        return None, "Local plain-language report template used."
    provider = MetaProvider.from_environment(settings)
    if provider is None:
        return None, "Local report template (Meta API credentials/model are not configured)."
    evidence = json.dumps(_facts(report), ensure_ascii=False, indent=2)
    messages = [
        {"role": "system", "content": (
            "Write in plain, direct language for a city-government decision-maker who is not a traffic-modeling specialist. "
            "Explain what was compared, what the measured changes mean, whether the adaptive candidate passed the "
            "improvement rule, and what should happen next. Briefly define PPO as a controller that learns signal "
            "choices through simulated trials. The JSON is the complete evidence. Never invent metrics, causes, "
            "events, benefits, or deployment claims. Distinguish assumptions from measured outcomes. If any key "
            "measure regressed, clearly say to retain the reference for this scenario. Do not repeat the numeric table; "
            "it is appended from source data. Return only Markdown with short headings and paragraphs.")},
        {"role": "user", "content": "Write a detailed but readable explanation with headings: Decision in brief, What was tested, What changed, What the numbers mean, and Recommended next steps. Simulation facts:\n" + evidence},
    ]
    try:
        response = provider.complete(messages, [])
        content = response.get("content")
        if isinstance(content, list):
            content = "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
        if not isinstance(content, str) or not content.strip() or response.get("tool_calls"):
            raise ProviderError("Meta API returned no report text")
        return content.strip(), "Narrative generated with Meta Model API; measured figures appended from SUMO data."
    except (ProviderError, OSError, ValueError) as exc:
        return None, "Local report template (Meta API unavailable: {}).".format(str(exc))


def _existing_meta_narrative(path):
    """Preserve a previously generated Meta explanation if a later refresh is offline."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    marker = "## AI-assisted executive narrative\n\n"
    if marker not in text:
        return None
    narrative = text.split(marker, 1)[1].split("\n\n> ", 1)[0].strip()
    return narrative or None


def generate_executive_report(report, comparison_path, root):
    """Write a Markdown report beside comparison.json and return its path/provider note."""
    path = Path(comparison_path).with_name("simulation_report.md")
    previous_narrative = _existing_meta_narrative(path)
    narrative, source_note = _meta_narrative(report, root)
    if not narrative and previous_narrative:
        narrative = previous_narrative
        source_note = "Preserved previously generated Meta narrative; detailed measurements refreshed from SUMO data."
    factual = _local_markdown(report)
    # The deterministic report always contains the exact measurements; Meta adds an optional narrative.
    if narrative:
        insertion = "## AI-assisted executive narrative\n\n{}\n\n> {}\n\n".format(narrative, source_note)
        factual = factual.replace("## Before and after", insertion + "## Before and after", 1)
    else:
        factual = factual.replace("## Before and after", "> {}\n\n## Before and after".format(source_note), 1)
    path.write_text(factual, encoding="utf-8")
    return {"path": str(path.resolve()), "provider_note": source_note}
