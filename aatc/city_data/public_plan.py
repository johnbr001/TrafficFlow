"""Build a public-service-shaped JSON payload for simulated signal proposals.

The government API has no submission schema. This output mirrors its common
response envelope and intersection/detail field names, while keeping the SUMO
schedule in a clearly marked extension until a receiving agency defines a contract.
"""
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path


PHASE_KEYS = ["{}_RING_{}_PHASE_CONF_CD".format(ring, index)
              for ring in ("A", "B") for index in range(1, 9)]
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read_json(path, fallback):
    try:
        with Path(path).open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return fallback


def _field(row, signal_id, field):
    safe_id = str(signal_id).replace("-", "_").replace(":", "_")
    return row.get("tls_{}_{}".format(safe_id, field))


def _state_schedule(csv_path, signal_id):
    path = Path(csv_path)
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        samples = []
        for row in rows:
            state = _field(row, signal_id, "state")
            phase = _field(row, signal_id, "phase")
            time_value = row.get("simulation_time_s")
            if state is None or time_value in (None, ""):
                return []
            try:
                samples.append((float(time_value), int(float(phase)), str(state)))
            except (ValueError, TypeError):
                continue
    samples.sort(key=lambda item: item[0])
    if not samples:
        return []
    intervals = []
    for index, (time_s, phase, state) in enumerate(samples):
        end_s = samples[index + 1][0] if index + 1 < len(samples) else time_s + 1.0
        start_s = 0.0 if index == 0 and time_s <= 1.0 else time_s
        if intervals and intervals[-1]["phase_index"] == phase and intervals[-1]["state"] == state \
                and intervals[-1]["end_time_s"] == start_s:
            intervals[-1]["end_time_s"] = end_s
        else:
            intervals.append({"start_time_s": start_s, "end_time_s": end_s,
                              "phase_index": phase, "state": state,
                              "link_states": [{"link_index": n + 1, "state": char}
                                              for n, char in enumerate(state)]})
    return intervals


def build_public_signal_plan(report, report_path, city_dir, *, controller="ppo"):
    """Return a JSON-serializable response shaped like the public API's item envelope."""
    city_dir = Path(city_dir)
    matches = _read_json(city_dir / "intersection_matches.json", [])
    official = {str(item.get("INT_NO")): item for item in matches
                if item.get("matched") and item.get("INT_NO") is not None}
    crossroads = _read_json(city_dir / "government" / "crossroads.json", [])
    official_records = {str(item.get("INT_NO")): item for item in crossroads
                        if item.get("INT_NO") is not None}
    details = _read_json(city_dir / "government" / "signal_details.json", [])
    details_by_id = {}
    for detail in details:
        if detail.get("INT_NO") is not None:
            details_by_id.setdefault(str(detail["INT_NO"]), []).append(detail)

    run_path = Path(report_path)
    run_id = run_path.parent.name
    scenario = report.get("scenario") or {}
    baseline = report.get("baseline") or {}
    plan_rows, unmatched = [], []
    for signal_id in report.get("signal_ids", []):
        signal_id = str(signal_id)
        osm_ids = re.findall(r"(?:^|_)osm_(\d+)", signal_id)
        found = [item for item in official.values()
                 if item.get("osm_node_id") and str(item["osm_node_id"]) in osm_ids]
        if not found:
            unmatched.append(signal_id)
            continue
        if len(found) > 1:
            # A SUMO cluster can include several OSM junction IDs; include each
            # official intersection as a separate consumer item, with the same
            # observed cluster schedule and an explicit shared-controller note.
            pass
        intervals = _state_schedule(report.get("ppo_signal_state_csv", ""), signal_id)
        if not intervals:
            unmatched.append(signal_id)
            continue
        for match in found:
            int_no = str(match["INT_NO"])
            detail_rows = details_by_id.get(int_no) or [{}]
            detail = detail_rows[0]
            # Keep the response's known detail fields, but do not present the
            # official reference MAP_NO or phase codes as the proposal.
            item = {key: detail.get(key) for key in (
                "INT_NO", "INT_NM", "REGION_CD", "MAP_NO", *PHASE_KEYS)}
            item["INT_NO"] = int_no
            item["INT_NM"] = match.get("INT_NM") or detail.get("INT_NM")
            item["REGION_CD"] = match.get("REGION_CD") or detail.get("REGION_CD") or report.get("region_code")
            item["MAP_NO"] = None
            for phase_key in PHASE_KEYS:
                item[phase_key] = None
            item["INTERSECTION"] = official_records.get(int_no, match.get("raw_record", {}))
            item["OFFICIAL_REFERENCE"] = detail_rows if details_by_id.get(int_no) else None
            item["SIMULATION_PLAN"] = {
                    "plan_id": "SIM-{}-{}".format(run_id, int_no),
                    "plan_type": "simulated_adaptive_recommendation",
                    "is_official_plan": False,
                    "simulation_run_id": run_id,
                    "controller": report.get("controller", "PPO deterministic policy"),
                    "control_mode": "observed_second_by_second_schedule",
                    "schedule_time_basis": "seconds_from_simulation_start",
                    "schedule": intervals,
                    "sumo_traffic_light_id": signal_id,
                    "sumo_link_order_note": "link_index follows SUMO controlled-link order; official ring movement mapping was not available",
                    "official_phase_code_mapping": "not_provided",
            }
            plan_rows.append(item)

    payload = {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "SIMULATED PROPOSAL; NOT AN OFFICIAL TIMING PLAN"},
            "body": {
                "items": {"item": plan_rows},
                "numOfRows": len(plan_rows), "pageNo": 1, "totalCount": len(plan_rows),
                "extension": {
                    "format": "crossroad-signal-plan-proposal-v1",
                    "source_api_shape": "CrossRoadInfoService getCrossRoadInfoDetail item envelope",
                    "city": report.get("city", "unknown"),
                    "region_code": report.get("region_code"),
                    "target_datetime": report.get("target_datetime"),
                    "scenario_description": report.get("scenario_description"),
                    "traffic_profile": baseline.get("traffic_profile"),
                    "duration_s": report.get("duration_s"),
                    "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    "simulation_metrics": report.get("ppo", {}),
                    "unmatched_or_missing_schedule_signal_ids": unmatched,
                    "limitations": [
                        "No public submission API or write contract was provided; this payload is a prototype export.",
                        "SUMO link states cannot be translated to A_RING/B_RING phase configuration codes without the official movement mapping.",
                        "Schedule is a scenario-specific simulation replay, not a validated recurring daily program."
                    ]
                }
            }
        }
    }
    return payload


def find_city_run(city="seoul", run_id=None, results_dir=None):
    """Locate a saved city comparison without accepting arbitrary filesystem paths."""
    base = Path(results_dir) if results_dir else PROJECT_ROOT / "experiments" / "results" / "city_runs"
    if not base.is_dir():
        return None
    if run_id:
        if Path(run_id).name != run_id or run_id in (".", ".."):
            return None
        candidates = [base / run_id / "comparison.json"]
    else:
        candidates = sorted(base.glob("*/comparison.json"),
                            key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        report = _read_json(path, {})
        if report and str(report.get("city", "")).casefold() == city.casefold():
            return report, path
    return None
