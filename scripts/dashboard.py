"""Streamlit monitoring dashboard for completed and on-demand SUMO experiments."""
import json
import html
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd
import pydeck as pdk
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aatc.city_data.events import scenario_for_datetime
from aatc.city_data.executive_report import generate_executive_report
from aatc.city_data.public_plan import build_public_signal_plan
from aatc.city_data.sync import sync_city_data

st.set_page_config(page_title="Seoul Signal Plan", page_icon="🚦", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown("""
<style>
    .stApp { background: #f3f6fa; }
    [data-testid="stHeader"] { background: rgba(243,246,250,.92); }
    .block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1500px; }
    [data-testid="stMetric"] { background: white; border: 1px solid #e1e8f0; padding: 16px 18px; border-radius: 12px; }
    [data-testid="stMetricLabel"] p { color: #64748b; font-size: .82rem; }
    [data-testid="stMetricValue"] { color: #102238; }
    div[data-testid="stVerticalBlockBorderWrapper"] { background: white; border-radius: 14px; border-color: #e1e8f0; }
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] { gap: .55rem; }
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] > label {
        background: rgba(255,255,255,.72); border: 1px solid #dce5ef; border-radius: 12px;
        padding: .8rem .9rem; margin: 0; transition: background .15s ease, border-color .15s ease, transform .15s ease;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] > label:hover {
        background: #fff; border-color: #80b8d9; transform: translateX(2px);
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] > label:has(input:checked) {
        background: linear-gradient(110deg, #e7f5fb, #f4f9ff); border-color: #2785bb;
        box-shadow: inset 3px 0 0 #1684bd, 0 2px 8px rgba(20,82,120,.08);
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] input[type="radio"] { accent-color: #147eae; }
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] > label p {
        color: #243b53; font-weight: 650; font-size: .96rem; margin: 0;
    }
    .eyebrow { color: #127c71; font-size: .75rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
    .muted { color: #64748b; }
</style>
""", unsafe_allow_html=True)

def read_json(path):
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None

def fmt(value, precision=1, suffix=""):
    if value is None:
        return "—"
    try:
        return "{:,.{}f}{}".format(float(value), precision, suffix)
    except (TypeError, ValueError):
        return "—"

def top_header(kicker, title, subtitle):
    st.markdown('<div class="eyebrow">{}</div>'.format(kicker), unsafe_allow_html=True)
    st.title(title)
    st.caption(subtitle)

def _latest_seoul_signal_plan():
    runs = ROOT / "experiments" / "results" / "city_runs"
    if not runs.is_dir():
        return None, None
    candidates = sorted(runs.glob("*/comparison.json"), key=lambda path: path.stat().st_mtime,
                       reverse=True)
    for path in candidates:
        report = read_json(path)
        if isinstance(report, dict) and report.get("region_code") == "L01":
            return report, path
    return None, None

def _available_seoul_signal_plans():
    runs = ROOT / "experiments" / "results" / "city_runs"
    if not runs.is_dir():
        return []
    choices = []
    for path in runs.glob("*/comparison.json"):
        report = read_json(path)
        if not isinstance(report, dict) or report.get("region_code") != "L01":
            continue
        target = report.get("target_datetime", "unknown time")
        profile = (report.get("baseline") or {}).get("traffic_profile", "scenario")
        label = "{} · {} · {} signals · {}".format(
            target, profile.replace("_", " ").title(), report.get("signal_count", "?"), path.parent.name)
        choices.append({"label": label, "path": path, "report": report,
                        "modified": path.stat().st_mtime})
    return sorted(choices, key=lambda item: item["modified"], reverse=True)

def _signal_phase_summary(frame, signal_id):
    prefix = _signal_column_prefix(signal_id)
    phase_col, state_col = prefix + "_phase", prefix + "_state"
    if phase_col not in frame:
        return {"phase": "—", "queue": None, "waiting": None}
    green_frame = frame
    if state_col in frame:
        mask = frame[state_col].fillna("").astype(str).str.contains("[Gg]", regex=True)
        if mask.any():
            green_frame = frame[mask]
    phases = pd.to_numeric(green_frame[phase_col], errors="coerce").dropna()
    return {"phase": int(phases.mode().iloc[0]) if not phases.empty else "—",
            "queue": float(frame[prefix + "_queue_length"].mean())
            if prefix + "_queue_length" in frame else None,
            "waiting": float(frame[prefix + "_waiting_time_s"].mean())
            if prefix + "_waiting_time_s" in frame else None}

def _signal_column_prefix(signal_id):
    return "tls_" + str(signal_id).replace("-", "_").replace(":", "_")

def _signal_state_column(frame, signal_id, suffix):
    column = _signal_column_prefix(signal_id) + "_" + suffix
    return frame[column] if column in frame else pd.Series(index=frame.index, dtype=object)

def _cardinal_direction(dx, dy):
    if dx == 0 and dy == 0:
        return None
    angle = math.degrees(math.atan2(dy, dx)) % 360
    return ("East", "Northeast", "North", "Northwest", "West", "Southwest", "South", "Southeast")[
        int((angle + 22.5) // 45) % 8]

@st.cache_data(show_spinner=False)
def _signal_movement_labels(network_path, signal_id, link_count):
    """Resolve link indexes to incoming approach and turn labels where possible."""
    fallback = [{"link_index": i, "label": "Movement {}".format(i + 1),
                 "from_edge": "", "to_edge": "", "direction": ""}
                for i in range(link_count)]
    path = Path(network_path) if network_path else None
    if not path or not path.is_file():
        return fallback
    try:
        root = ET.parse(str(path)).getroot()
    except (OSError, ET.ParseError):
        return fallback
    edges = {}
    for edge in root.iter("edge"):
        lane = next(iter(edge.findall("lane")), None)
        points = []
        for point in (lane.get("shape", "") if lane is not None else "").split():
            try:
                x, y = point.split(",")[:2]
                points.append((float(x), float(y)))
            except (ValueError, IndexError):
                continue
        edges[edge.get("id")] = {"points": points}
    turn_names = {"s": "straight", "l": "left turn", "r": "right turn",
                  "t": "U-turn", "L": "partial left turn", "R": "partial right turn"}
    resolved = {}
    for connection in root.iter("connection"):
        if connection.get("tl") != str(signal_id) or connection.get("linkIndex") is None:
            continue
        try:
            index = int(connection.get("linkIndex"))
        except ValueError:
            continue
        if not 0 <= index < link_count:
            continue
        points = edges.get(connection.get("from"), {}).get("points", [])
        heading = (_cardinal_direction(points[-1][0] - points[-2][0],
                                       points[-1][1] - points[-2][1])
                   if len(points) >= 2 else None)
        direction = turn_names.get(connection.get("dir"), "movement")
        label = "{}bound - {}".format(heading, direction) if heading else direction.title()
        if index not in resolved or resolved[index]["label"] == "Movement" and label != "movement":
            resolved[index] = {"link_index": index, "label": label,
                               "from_edge": connection.get("from", ""),
                               "to_edge": connection.get("to", ""),
                               "direction": connection.get("dir", "")}
    for index, record in resolved.items():
        fallback[index] = record
    return fallback

def _signal_timeline_html(signal_id, baseline, adaptive, movement_labels=None):
    """Build a second-by-second state timeline, grouped into contiguous intervals."""
    time_column = "simulation_time_s"
    if time_column not in baseline or time_column not in adaptive:
        return None, None
    before = baseline[[time_column]].copy()
    after = adaptive[[time_column]].copy()
    before["state"] = _signal_state_column(baseline, signal_id, "state").fillna("").astype(str)
    before["phase"] = _signal_state_column(baseline, signal_id, "phase")
    after["state"] = _signal_state_column(adaptive, signal_id, "state").fillna("").astype(str)
    after["phase"] = _signal_state_column(adaptive, signal_id, "phase")
    before = before.drop_duplicates(time_column).sort_values(time_column)
    after = after.drop_duplicates(time_column).sort_values(time_column)
    if before.empty or after.empty:
        return None, None
    duration = max(float(before[time_column].max()), float(after[time_column].max())) + 1
    link_count = max(max((len(s) for s in before["state"]), default=0),
                     max((len(s) for s in after["state"]), default=0))
    if not link_count:
        return None, None

    colors = {"G": "#15803d", "g": "#86efac", "y": "#f59e0b", "Y": "#d97706",
              "r": "#dc2626", "R": "#991b1b"}
    width, left, top, row_height, group_gap = 1120, 260, 44, 24, 28
    plot_width = width - left - 24
    movement_labels = movement_labels or [
        {"label": "Movement {}".format(i + 1)} for i in range(link_count)]
    rows = []
    for group, frame, label in (("Current fixed-time reference", before, "Current"),
                                ("Proposed adaptive control", after, "Proposed")):
        rows.append(("heading", group, 0, ""))
        for link in range(link_count):
            rows.append((label, movement_labels[link]["label"], link, frame))
    height = top + len(rows) * row_height + group_gap * 2 + 36
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {} {}">'.format(width, height),
           '<rect width="100%" height="100%" fill="#ffffff"/>']
    cursor = top
    timeline_rows = []
    for row in rows:
        kind, label, link, frame = row
        if kind == "heading":
            svg.append('<text x="12" y="{}" font-family="Arial,sans-serif" font-size="15" font-weight="700" fill="#0f172a">{}</text>'.format(
                cursor + 15, html.escape(label)))
            cursor += row_height + group_gap
            continue
        svg.append('<text x="12" y="{}" font-family="Arial,sans-serif" font-size="12" fill="#334155">{}: {}</text>'.format(
            cursor + 15, html.escape(kind), html.escape(label)))
        samples = frame[[time_column, "state", "phase"]].to_dict("records")
        values = []
        for sample in samples:
            t = float(sample[time_column])
            state = str(sample["state"] or "")
            symbol = state[link] if link < len(state) else "?"
            values.append((t, symbol, sample["phase"]))
        runs = []
        for i, (start, symbol, phase) in enumerate(values):
            end = values[i + 1][0] if i + 1 < len(values) else duration
            if runs and runs[-1]["symbol"] == symbol and runs[-1]["phase"] == phase and abs(runs[-1]["end"] - start) < 1.01:
                runs[-1]["end"] = end
            else:
                runs.append({"start": start, "end": end, "symbol": symbol, "phase": phase})
        timeline_rows.append({"Controller": kind, "Movement": label, "Runs": runs})
        for run in runs:
            x = left + plot_width * run["start"] / duration
            rect_width = max(0.8, plot_width * (run["end"] - run["start"]) / duration)
            color = colors.get(run["symbol"], "#cbd5e1")
            tip = "{}; {} {}; {:.0f}–{:.0f} sec; phase {}".format(
                kind, label, run["symbol"], run["start"], run["end"], run["phase"])
            svg.append('<rect x="{:.2f}" y="{}" width="{:.2f}" height="16" rx="2" fill="{}"><title>{}</title></rect>'.format(
                x, cursor + 2, rect_width, color, html.escape(tip)))
            if rect_width > 15:
                svg.append('<text x="{:.2f}" y="{}" font-family="Arial,sans-serif" font-size="9" fill="{}">{}</text>'.format(
                    x + 3, cursor + 13, "#fff" if run["symbol"] in "GgYyRr" else "#334155",
                    html.escape(run["symbol"])))
        cursor += row_height

    axis_y = cursor + 4
    for minute in range(int(duration // 60) + 1):
        second = min(minute * 60, duration)
        x = left + plot_width * second / duration
        svg.append('<line x1="{:.2f}" y1="{}" x2="{:.2f}" y2="{}" stroke="#94a3b8" stroke-width="1"/>'.format(x, top, x, axis_y))
        svg.append('<text x="{:.2f}" y="{}" text-anchor="middle" font-family="Arial,sans-serif" font-size="10" fill="#475569">{} min</text>'.format(x, axis_y + 15, minute))
    svg.append('</svg>')
    legend = " &nbsp; ".join('<span style="color:{}">■</span> {}'.format(colors[s], label)
                            for s, label in (("G", "Protected green"), ("g", "Permissive green"),
                                             ("y", "Amber"), ("r", "Red")))
    markup = '<div style="font:12px Arial,sans-serif;overflow-x:auto">{}<div style="padding:4px 12px;color:#334155">{}</div></div>'.format("".join(svg), legend)
    return markup, timeline_rows

def _signal_change_points(report, city_dir):
    roads = read_json(city_dir / "osm" / "road_network.geojson")
    junctions = read_json(city_dir / "osm" / "intersections.geojson")
    baseline_path = Path((report.get("baseline") or {}).get("state_csv", ""))
    adaptive_path = Path(report.get("ppo_signal_state_csv", ""))
    if not roads or not junctions or not baseline_path.is_file() or not adaptive_path.is_file():
        return roads, []
    baseline_samples = pd.read_csv(baseline_path)
    adaptive_samples = pd.read_csv(adaptive_path)
    coordinates = {}
    for feature in junctions.get("features", []):
        properties = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        point = geometry.get("coordinates", [])
        osmid = str(properties.get("osmid", ""))
        if osmid and len(point) >= 2:
            coordinates[osmid] = (float(point[0]), float(point[1]))
    official_names = {}
    for match in read_json(city_dir / "intersection_matches.json") or []:
        if match.get("matched") and match.get("osm_node_id"):
            official_names[str(match["osm_node_id"])] = match.get("INT_NM")

    points = []
    for signal_id in report.get("signal_ids", []):
        node_ids = re.findall(r"osm_(\d+)", str(signal_id))
        found = [coordinates[node_id] for node_id in node_ids if node_id in coordinates]
        if not found:
            continue
        longitude = sum(point[0] for point in found) / len(found)
        latitude = sum(point[1] for point in found) / len(found)
        before = _signal_phase_summary(baseline_samples, signal_id)
        after = _signal_phase_summary(adaptive_samples, signal_id)
        if before["queue"] is None or after["queue"] is None:
            continue
        delta = after["queue"] - before["queue"]
        color = [22, 163, 74, 225] if delta < -0.05 else ([220, 38, 38, 225] if delta > 0.05 else [217, 119, 6, 225])
        labels = [official_names[node_id] for node_id in node_ids if official_names.get(node_id)]
        points.append({"position": [longitude, latitude],
                       "name": labels[0] if labels else str(signal_id),
                       "signal_id": str(signal_id),
                       "queue_before": round(before["queue"], 2),
                       "queue_after": round(after["queue"], 2),
                       "queue_change": round(delta, 2),
                       "wait_before": round(before["waiting"], 2) if before["waiting"] is not None else None,
                       "wait_after": round(after["waiting"], 2) if after["waiting"] is not None else None,
                       "weight": max(abs(delta), 0.1), "color": color,
                       "radius": 55 + min(abs(delta) * 12, 95)})
    return roads, points

def _previous_run_snapshot(report, path):
    ppo = report.get("ppo") or {}
    return {"target_datetime": report.get("target_datetime"),
            "scenario_description": report.get("scenario_description", ""),
            "comparison_path": str(path.resolve()),
            "queue": ppo.get("mean_total_queue_length"),
            "waiting": ppo.get("mean_total_waiting_time_s"),
            "completed": ppo.get("completed_vehicles"),
            "signal_count": report.get("signal_count")}

def render_signal_plan_result(report, report_path, city_dir):
    baseline = report.get("baseline") or {}
    optimized = report.get("ppo") or {}
    st.markdown("### Current plan and proposed plan")
    st.caption("{} · {} · {}-minute simulation".format(
        report.get("target_datetime", "Scenario date"),
        baseline.get("traffic_profile", "selected time period").replace("_", " ").title(),
        int(report.get("duration_s", 0)) // 60))
    st.caption("Current plan: fixed-time SUMO reference. Proposed plan: coordinated adaptive signal decisions.")
    demand_source = report.get("demand_estimate") or baseline.get("demand_estimate")
    if demand_source and demand_source.get("selected_method") == "speed_based_estimate":
        st.caption("Traffic demand used a speed-based estimate from {} T-Data links at {:02d}:00 on {}. This is a model-based guess, not observed vehicle volume.".format(
            demand_source.get("records_used", 0), int(demand_source.get("source_hour", 0)),
            demand_source.get("source_date", "unknown date")))
    if demand_source and demand_source.get("estimated_vehicle_count") is not None:
        st.info("Demand calculation: {:,} trips estimated for this network and duration; {:,} routes generated; {:,} vehicles actually departed in SUMO.".format(
            int(demand_source["estimated_vehicle_count"]),
            int(demand_source.get("generated_route_count", 0)),
            int(baseline.get("departed_vehicles", 0))))

    def improvement(base_value, new_value):
        try:
            base_value, new_value = float(base_value), float(new_value)
            if base_value <= 0:
                return None
            return (base_value - new_value) / base_value * 100.0
        except (TypeError, ValueError):
            return None

    queue_gain = improvement(baseline.get("mean_total_queue_length"),
                             optimized.get("mean_total_queue_length"))
    wait_gain = improvement(baseline.get("mean_total_waiting_time_s"),
                            optimized.get("mean_total_waiting_time_s"))
    travel_gain = improvement(baseline.get("average_travel_time_s"),
                              optimized.get("average_travel_time_s"))
    throughput_before = baseline.get("network_throughput_vehicles_per_hour")
    throughput_after = optimized.get("network_throughput_vehicles_per_hour")
    throughput_gain = ((float(throughput_after) - float(throughput_before)) / float(throughput_before) * 100.0
                       if throughput_before and throughput_after is not None else None)
    metrics = st.columns(6)
    metrics[0].metric("Network throughput", fmt(throughput_after, 1, " vehicles/hour"),
                      "{:+.1f}% vs fixed-time".format(throughput_gain) if throughput_gain is not None else None)
    st.caption("City PPO prioritizes completed trips per simulated hour. Queue, waiting time, and trip time remain secondary checks; throughput here is a SUMO simulation measure.")
    metrics[1].metric("Average queue", fmt(optimized.get("mean_total_queue_length"), 1, " vehicles"),
                      "{:+.1f}% vs fixed-time".format(queue_gain) if queue_gain is not None else None)
    metrics[2].metric("Average waiting", fmt(optimized.get("mean_total_waiting_time_s"), 1, " sec"),
                      "{:+.1f}% vs fixed-time".format(wait_gain) if wait_gain is not None else None)
    metrics[3].metric("Average trip time", fmt(optimized.get("average_travel_time_s"), 1, " sec"),
                      "{:+.1f}% vs fixed-time".format(travel_gain) if travel_gain is not None else None)
    metrics[4].metric("Vehicles completed", optimized.get("completed_vehicles", 0),
                      "fixed-time: {}".format(baseline.get("completed_vehicles", 0)))
    metrics[5].metric("Signals coordinated", report.get("signal_count", 0))

    try:
        selected_scenario = scenario_for_datetime(
            "Seoul", datetime.fromisoformat(report.get("target_datetime")))
        event_names = [str(event.get("name")) for event in selected_scenario.get("active_events", [])]
        event_note = " Calendar events: {}.".format(", ".join(event_names)) if event_names else ""
        calendar_factor = float(selected_scenario.get("combined_relative_demand_multiplier", 1.0))
        prompt_factor = float(report.get("prompt_demand_multiplier", 1.0))
        st.info("Reason: the joint controller selected safe signal phases to reduce simulated queues and waiting for the {} {} scenario. Demand assumptions: calendar ×{:.2f}, prompt ×{:.2f}, combined ×{:.2f}.{}".format(
            selected_scenario.get("day_type", "selected day").replace("_", " "),
            selected_scenario.get("time_period", {}).get("id", "selected period").replace("_", " "),
            calendar_factor, prompt_factor, calendar_factor * prompt_factor, event_note))
    except (TypeError, ValueError):
        pass

    def change_text(gain):
        if gain is None:
            return "not available"
        if abs(gain) < 0.05:
            return "about the same"
        return "{:.1f}% lower".format(gain) if gain > 0 else "{:.1f}% higher".format(abs(gain))

    st.info("Compared with fixed-time control, this run measured queue {}, waiting {}, and trip time {}. Completed vehicles: {} adaptive vs {} fixed-time. Results are from this simulation only.".format(
        change_text(queue_gain), change_text(wait_gain), change_text(travel_gain), optimized.get("completed_vehicles", 0),
        baseline.get("completed_vehicles", 0)))
    completed_gain = improvement(baseline.get("completed_vehicles"), optimized.get("completed_vehicles"))
    if (any(value is not None and value < 0 for value in (queue_gain, wait_gain, travel_gain)) or
            (completed_gain is not None and completed_gain < 0)):
        st.warning("The proposed plan did not outperform the current reference on every measure. Keep the current reference for this scenario unless further simulations confirm a benefit.")
    else:
        st.success("The proposed plan matched or improved the reported measures in this simulation. Confirm it across more scenarios before considering field use.")

    prompt = report.get("scenario_description")
    if prompt:
        st.markdown("**Requested scenario**")
        st.write(prompt)

    roads, change_points = _signal_change_points(report, city_dir)
    st.markdown("#### Queue-change map")
    st.caption("OSM roads with simulated queue changes at mapped signals. Green means fewer queued vehicles under the proposed plan; red means more. The heat layer shows the size of the change.")
    if roads and change_points:
        layers = [
            pdk.Layer("GeoJsonLayer", data=roads, stroked=True, filled=False,
                      get_line_color=[112, 128, 144, 150], get_line_width=1,
                      line_width_min_pixels=1, pickable=False),
            pdk.Layer("HeatmapLayer", data=change_points, get_position="position",
                      get_weight="weight", radius_pixels=70, intensity=1.0,
                      threshold=0.05, opacity=0.35),
            pdk.Layer("ScatterplotLayer", data=change_points, get_position="position",
                      get_radius="radius", radius_units="meters", get_fill_color="color",
                      get_line_color=[255, 255, 255, 240], line_width_min_pixels=1,
                      stroked=True, pickable=True),
        ]
        view = pdk.ViewState(latitude=37.5729, longitude=126.9794, zoom=13, pitch=0)
        tooltip = {"html": "<b>{name}</b><br/>Signal: {signal_id}<br/>Queue before: {queue_before}<br/>Queue after: {queue_after}<br/>Queue change: {queue_change}<br/>Wait before: {wait_before} sec<br/>Wait after: {wait_after} sec"}
        st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view, tooltip=tooltip),
                        use_container_width=True, height=500)
        legend_cols = st.columns(3)
        legend_cols[0].markdown("🟢 **Lower queue**")
        legend_cols[1].markdown("🔴 **Higher queue**")
        legend_cols[2].markdown("🟠 **About the same**")
        st.caption("OpenStreetMap contributors · ODbL · {} of {} simulated signals mapped to OSM junctions. Marker size and heat intensity reflect queue-change magnitude.".format(
            len(change_points), report.get("signal_count", len(change_points))))
    else:
        st.warning("The simulation result is available, but its signal IDs could not be matched to map coordinates. The numeric results and signal table remain available below.")

    baseline_path = Path(baseline.get("state_csv", ""))
    adaptive_path = Path(report.get("ppo_signal_state_csv", ""))
    if baseline_path.is_file() and adaptive_path.is_file():
        baseline_samples = pd.read_csv(baseline_path)
        adaptive_samples = pd.read_csv(adaptive_path)
        government_names = {}
        match_path = city_dir / "intersection_matches.json"
        for match in read_json(match_path) or []:
            if match.get("matched") and match.get("osm_node_id"):
                government_names[str(match["osm_node_id"])] = {
                    "name": match.get("INT_NM"), "id": match.get("INT_NO")}
        signal_ids = [str(signal_id) for signal_id in report.get("signal_ids", [])]
        signal_labels = {}
        for signal_id in signal_ids:
            node_ids = re.findall(r"osm_(\d+)", signal_id)
            names = [government_names[node_id]["name"] for node_id in node_ids
                     if node_id in government_names and government_names[node_id].get("name")]
            signal_labels[signal_id] = "{} — {}".format(names[0], signal_id) if names else signal_id

        if signal_ids:
            st.markdown("#### Red, amber and green timeline")
            st.info("One intersection has one controller and several vehicle movements. Each row below is one movement through that intersection, not a separate traffic light. At the same moment, one movement can be green while conflicting movements are red; this is normal and keeps crossing traffic apart.")
            st.caption("Compare the fixed-time reference with the adaptive controller. Labels use the approach direction and turn when SUMO's network file identifies them; otherwise they fall back to a movement number.")
            selected_signal = st.selectbox(
                "Intersection", signal_ids, format_func=lambda value: signal_labels.get(value, value),
                key="signal_timeline_" + report_path.parent.name)
            state_values = pd.concat([
                _signal_state_column(baseline_samples, selected_signal, "state"),
                _signal_state_column(adaptive_samples, selected_signal, "state")])
            link_count = max((len(str(value)) for value in state_values.dropna()), default=0)
            network_dir_value = baseline.get("sumo_network_dir")
            network_path = Path(network_dir_value) / "network.net.xml" if network_dir_value else None
            movement_labels = _signal_movement_labels(
                str(network_path) if network_path else "", selected_signal, link_count)
            timeline_html, timeline_rows = _signal_timeline_html(
                selected_signal, baseline_samples, adaptive_samples, movement_labels)
            if timeline_html:
                components.html(timeline_html, height=min(900, 150 + len(timeline_rows) * 25),
                                scrolling=True)
                st.caption("Read each row from left to right over time. Green means that movement may proceed; red means it must wait. Amber is a clearance interval. Hover over a bar for its exact time and state.")
                with st.expander("Show how SUMO movements map to these labels"):
                    movement_table = pd.DataFrame([{
                        "SUMO link number": item["link_index"] + 1,
                        "Movement through intersection": item["label"],
                        "Incoming road ID": item["from_edge"],
                        "Outgoing road ID": item["to_edge"],
                    } for item in movement_labels])
                    st.dataframe(movement_table, use_container_width=True, hide_index=True)
                    st.caption("SUMO link numbers are internal indexes. Direction and turn labels are inferred from the simulated road geometry and connection; confirm them against the map before operational use.")
                timeline_export = []
                for row in timeline_rows:
                    for interval in row["Runs"]:
                        timeline_export.append({
                            "Signal ID": selected_signal, "Controller": row["Controller"],
                            "Movement": row["Movement"], "Start (sec)": interval["start"],
                            "End (sec)": interval["end"], "State": interval["symbol"],
                            "Phase": interval["phase"]})
                st.download_button(
                    "Download this signal timeline (CSV)",
                    pd.DataFrame(timeline_export).to_csv(index=False).encode("utf-8-sig"),
                    file_name="signal_timeline_{}.csv".format(re.sub(r"[^A-Za-z0-9_-]+", "_", selected_signal)),
                    mime="text/csv", key="download_timeline_" + report_path.parent.name)
            else:
                st.warning("Second-by-second signal states are missing for this intersection, so its timeline cannot be drawn.")
        plan_rows = []
        for signal_id in report.get("signal_ids", []):
            reference = _signal_phase_summary(baseline_samples, signal_id)
            proposed = _signal_phase_summary(adaptive_samples, signal_id)
            official = government_names.get(str(signal_id), {})
            plan_rows.append({
                "Intersection": official.get("name") or str(signal_id),
                "Official ID": official.get("id") or "—",
                "Signal ID": str(signal_id),
                "Current reference phase": "Phase {}".format(reference["phase"]),
                "Proposed adaptive phase": "Phase {}".format(proposed["phase"]),
                "Queue: current → proposed": "{} → {}".format(
                    fmt(reference["queue"], 1), fmt(proposed["queue"], 1)),
                "Wait (sec): current → proposed": "{} → {}".format(
                    fmt(reference["waiting"], 1), fmt(proposed["waiting"], 1)),
            })
        if plan_rows:
            st.markdown("#### Signal-by-signal outcome summary")
            st.caption("This table summarizes average queue/wait and the most-observed phase. For the actual red/amber/green sequence over time, use the timeline above.")
            plan = pd.DataFrame(plan_rows).sort_values("Intersection")
            st.dataframe(plan, use_container_width=True, hide_index=True, height=420)
            st.download_button("Download signal plan (CSV)", plan.to_csv(index=False).encode("utf-8-sig"),
                               file_name="seoul_adaptive_signal_plan.csv", mime="text/csv")

    previous = report.get("previous_run_comparison")
    if previous:
        st.markdown("#### Compared with the previous simulation")
        previous_metrics = previous.get("adaptive", {})
        comparison_rows = [
            {"Measure": "Average queue (vehicles)",
             "Previous": round(float(previous_metrics["queue"]), 2) if previous_metrics.get("queue") is not None else None,
             "This run": round(float(optimized["mean_total_queue_length"]), 2)
             if optimized.get("mean_total_queue_length") is not None else None},
            {"Measure": "Average waiting (seconds)",
             "Previous": round(float(previous_metrics["waiting"]), 2) if previous_metrics.get("waiting") is not None else None,
             "This run": round(float(optimized["mean_total_waiting_time_s"]), 2)
             if optimized.get("mean_total_waiting_time_s") is not None else None},
            {"Measure": "Vehicles completed",
             "Previous": previous_metrics.get("completed"), "This run": optimized.get("completed_vehicles")},
        ]
        st.dataframe(pd.DataFrame(comparison_rows), use_container_width=True, hide_index=True)
        st.caption("Previous scenario: {} · {}. Current scenario: {} · {}. If the prompts or dates differ, this compares two scenarios as well as their plans; use the fixed-time comparison above to compare control methods under the same demand.".format(
            previous.get("target_datetime", "unknown"), previous.get("scenario_description", "no prompt saved"),
            report.get("target_datetime", "unknown"), report.get("scenario_description", "no prompt saved")))

    st.caption("This is an adaptive SUMO/PPO recommendation, not a fixed green-time schedule or an approved field timing plan. Comparison report: {}".format(report_path.parent.name))
    markdown_report_path = report_path.with_name("simulation_report.md")
    if markdown_report_path.is_file():
        st.markdown("#### Decision report")
        with st.expander("Read simulation before-and-after report", expanded=True):
            st.markdown(markdown_report_path.read_text(encoding="utf-8"), unsafe_allow_html=False)
        st.download_button("Download executive simulation report (Markdown)",
                           markdown_report_path.read_bytes(),
                           file_name="simulation_report.md", mime="text/markdown",
                           key="download_executive_simulation_report")
        refresh_key = "refresh_executive_report_{}".format(report_path.parent.name)
        if st.button("Refresh detailed explanation with Meta AI", key=refresh_key):
            with st.spinner("Generating a plain-language explanation from this simulation's saved results..."):
                result = generate_executive_report(report, report_path, ROOT)
                report["executive_report_path"] = result["path"]
                report["executive_report_provider"] = result["provider_note"]
                report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            st.success("Report refreshed. {}".format(result["provider_note"]))
            st.rerun()
    elif st.button("Generate decision report", key="generate_seoul_simulation_report"):
        with st.spinner("Preparing the before-and-after decision report..."):
            result = generate_executive_report(report, report_path, ROOT)
        st.success("Report created. {}".format(result["provider_note"]))
        st.rerun()
    st.download_button("Download simulation report", report_path.read_bytes(),
                       file_name="seoul_signal_plan_report.json", mime="application/json",
                       key="download_seoul_signal_plan_report")

    st.divider()
    st.markdown("### Proposed signal-plan API response")
    st.caption("This is the JSON response shape served by the prototype API for this simulation. Expand the tree to inspect intersections, plans, and timed signal states.")
    public_payload = build_public_signal_plan(report, report_path, city_dir)
    extension = public_payload.get("response", {}).get("body", {}).get("extension", {})
    st.caption("{} intersection record(s) included; {} signal(s) omitted because they lack a government ID match or saved schedule.".format(
        public_payload.get("response", {}).get("body", {}).get("totalCount", 0),
        len(extension.get("unmatched_or_missing_schedule_signal_ids", []))))
    response_key = "show_public_plan_response_" + report_path.parent.name
    if st.button("Hide JSON response" if st.session_state.get(response_key) else "Show JSON response",
                 key="toggle_" + response_key):
        st.session_state[response_key] = not st.session_state.get(response_key, False)
    if st.session_state.get(response_key):
        st.json(public_payload, expanded=False)
        st.download_button(
            "Download this API response (JSON)",
            json.dumps(public_payload, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name="signal_plan_proposal_{}.json".format(report_path.parent.name),
            mime="application/json", key="download_public_plan_" + report_path.parent.name)

def render_seoul_signal_results():
    top_header("SEOUL · JONGNO PILOT", "Simulation results",
               "Review the latest fixed-time versus adaptive simulation, map, signal details, and decision report.")
    city_dir = ROOT / "data" / "cities" / "seoul"
    road_map = city_dir / "osm" / "road_network.geojson"
    if not road_map.is_file():
        st.warning("The Seoul road map is not prepared yet. Open **New simulation** to prepare the test area.")
        return

    choices = _available_seoul_signal_plans()
    if choices:
        labels = [item["label"] for item in choices]
        selected_label = st.selectbox("Simulation to review", labels, index=0,
                                      key="selected_seoul_result")
        selected = next(item for item in choices if item["label"] == selected_label)
        render_signal_plan_result(selected["report"], selected["path"], city_dir)
    else:
        st.info("No Seoul simulation results are available yet. Open **New simulation** to configure and run the first scenario.")

def _prompt_demand_assumption(prompt):
    text = str(prompt or "").casefold()
    high_terms = ("heavy", "busy", "surge", "high traffic", "traffic jam", "congest",
                  "crowded", "increase", "rush", "festival", "event")
    low_terms = ("quiet", "light traffic", "low traffic", "reduced", "less traffic")
    negates_high = any(term in text for term in (
        "not heavy", "not congested", "no traffic increase", "don't increase",
        "do not increase", "without more traffic", "normal traffic"))
    if any(term in text for term in high_terms) and not negates_high:
        return 1.25, "Higher overall demand (+25%)"
    if any(term in text for term in low_terms):
        return 0.8, "Lower overall demand (-20%)"
    return 1.0, "Standard overall demand"

def _render_scenario_stepper(active_step):
    titles = [("Describe", "Traffic scenario"), ("Schedule", "Date and settings"),
              ("Review & run", "Assumptions and simulation")]
    columns = st.columns(3)
    for index, (title, detail) in enumerate(titles, 1):
        if index < active_step:
            columns[index - 1].success("✓ Step {} · {}\n\n{}".format(index, title, detail))
        elif index == active_step:
            columns[index - 1].info("**Step {} · {}**\n\n{}".format(index, title, detail))
        else:
            columns[index - 1].markdown("**Step {} · {}**\n\n{}".format(index, title, detail))
    st.progress(active_step / 3.0)

def _save_city_comparison(report_path, prompt, demand_factor, demand_label, previous_snapshot=None):
    report = read_json(report_path) or {}
    report["scenario_description"] = prompt.strip()
    report["prompt_demand_multiplier"] = demand_factor
    report["prompt_demand_assumption"] = demand_label
    if previous_snapshot:
        report["previous_run_comparison"] = previous_snapshot
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    generated = generate_executive_report(report, report_path, ROOT)
    report["executive_report_path"] = generated["path"]
    report["executive_report_provider"] = generated["provider_note"]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

def render_new_simulation():
    top_header("SEOUL · JONGNO PILOT", "New simulation",
               "Complete each step to create a matched SUMO comparison and adaptive signal recommendation.")
    city_dir = ROOT / "data" / "cities" / "seoul"
    if not (city_dir / "osm" / "road_network.geojson").is_file():
        st.warning("The Seoul road map is not prepared yet.")
        if st.button("Prepare Seoul road map", type="primary"):
            try:
                with st.spinner("Preparing Seoul road and signal data..."):
                    result = sync_city_data("L01")
                if result.get("warnings"):
                    for warning in result["warnings"]:
                        st.warning(warning)
                else:
                    st.success("Seoul test area is ready. Start at Step 1.")
                    st.rerun()
            except (OSError, RuntimeError, ValueError) as exc:
                st.error("Could not prepare the Seoul road map: {}".format(exc))
        return

    step = int(st.session_state.get("new_simulation_step", 1))
    _render_scenario_stepper(step)
    if step == 1:
        st.markdown("### Step 1 of 3 · Describe the traffic scenario")
        st.write("Tell the system what conditions you want to evaluate. The prompt guides the scenario label and network-wide demand assumption.")
        suggestions = [
            ("Morning commute", "Simulate heavy weekday morning commute traffic in Jongno.", "Morning commute"),
            ("Evening commute", "Compare the evening rush hour with the current signal plan.", "Evening commute"),
            ("Event traffic", "Simulate higher traffic around a busy event day.", "Midday"),
            ("Quiet period", "Check signal performance during a quiet night period.", "Night"),
        ]
        suggestion_cols = st.columns(4)
        for index, (label, text, suggested_period) in enumerate(suggestions):
            if suggestion_cols[index].button(label, key="wizard_scenario_suggestion_{}".format(index),
                                             use_container_width=True):
                st.session_state["plan_scenario_prompt"] = text
                st.session_state["scenario_prompt_saved"] = text
                st.session_state["plan_scenario_period"] = suggested_period
                st.rerun()
        if "plan_scenario_prompt" not in st.session_state:
            st.session_state["plan_scenario_prompt"] = st.session_state.get("scenario_prompt_saved", "")
        prompt = st.text_area("Describe what you want to test", key="plan_scenario_prompt",
                              max_chars=500, height=110,
                              placeholder="Example: Simulate heavy weekday morning traffic in Jongno.")
        # Widget-owned session state is discarded when Step 1 is hidden. Keep
        # the actual prompt separately so transitions to Steps 2 and 3 retain it.
        st.session_state["scenario_prompt_saved"] = prompt
        st.caption("Named junctions and turning movements are not independently calibrated yet. Demand changes are network-wide assumptions.")
        if st.button("Next: choose date and settings →", type="primary",
                     disabled=not prompt.strip(), key="wizard_to_schedule"):
            st.session_state["new_simulation_step"] = 2
            st.rerun()
        return

    prompt = st.session_state.get("scenario_prompt_saved", "").strip()
    if not prompt:
        st.session_state["new_simulation_step"] = 1
        st.rerun()

    if step == 2:
        st.markdown("### Step 2 of 3 · Choose date and simulation settings")
        st.markdown("**Scenario prompt**")
        st.info(prompt)
        period_names = ["Morning commute", "Midday", "Evening commute", "Night"]
        period_times = {"Morning commute": "08:00", "Midday": "12:00",
                        "Evening commute": "18:00", "Night": "22:00"}
        saved_inputs = st.session_state.get("wizard_saved_inputs", {})
        defaults = {"plan_scenario_date": date.today(),
                    "plan_scenario_period": "Morning commute",
                    "wizard_radius_km": 1.5,
                    "wizard_duration_minutes": 15,
                    "wizard_training_steps": 2048,
                    "wizard_retrain": False}
        for key, default in defaults.items():
            if key not in st.session_state:
                st.session_state[key] = saved_inputs.get(key, default)
        cols = st.columns(2)
        cols[0].date_input("Scenario date", key="plan_scenario_date")
        if st.session_state.get("plan_scenario_period") not in period_names:
            st.session_state["plan_scenario_period"] = "Morning commute"
        cols[1].selectbox("Time period", period_names, key="plan_scenario_period")
        with st.expander("Network and training settings", expanded=True):
            settings = st.columns(3)
            settings[0].selectbox("Study area", [1.0, 1.5, 3.0], index=1,
                                  format_func=lambda value: "{} km around Jongno".format(value),
                                  key="wizard_radius_km")
            settings[1].selectbox("Simulation length", [15, 30], index=0, key="wizard_duration_minutes")
            settings[2].selectbox("Policy training effort", [2048, 4096, 8192], index=0,
                                  key="wizard_training_steps")
            st.checkbox("Train a fresh policy", value=False, key="wizard_retrain")
        st.session_state["wizard_saved_inputs"] = {
            key: st.session_state[key] for key in defaults
        }
        target_date = st.session_state["plan_scenario_date"]
        period = st.session_state["plan_scenario_period"]
        scenario_time = datetime.strptime(period_times[period], "%H:%M").time()
        scenario = scenario_for_datetime("Seoul", datetime.combine(target_date, scenario_time))
        events = [event.get("name") for event in scenario.get("active_events", [])]
        st.caption("Calendar: {} · demand multiplier {:.2f}{}".format(
            scenario.get("day_type", "selected day"),
            float(scenario.get("combined_relative_demand_multiplier", 1.0)),
            " · events: " + ", ".join(events) if events else ""))
        nav = st.columns(2)
        if nav[0].button("← Back to scenario", key="wizard_back_to_prompt"):
            st.session_state["new_simulation_step"] = 1
            st.rerun()
        if nav[1].button("Next: review before running →", type="primary", key="wizard_to_review"):
            st.session_state["new_simulation_step"] = 3
            st.rerun()
        return

    st.markdown("### Step 3 of 3 · Review and run")
    saved_inputs = st.session_state.get("wizard_saved_inputs", {})
    target_date = saved_inputs.get("plan_scenario_date", date.today())
    period = saved_inputs.get("plan_scenario_period", "Morning commute")
    period_times = {"Morning commute": "08:00", "Midday": "12:00",
                    "Evening commute": "18:00", "Night": "22:00"}
    scenario_time = datetime.strptime(period_times[period], "%H:%M").time()
    target_datetime = datetime.combine(target_date, scenario_time)
    scenario = scenario_for_datetime("Seoul", target_datetime)
    demand_factor, demand_label = _prompt_demand_assumption(prompt)
    radius = float(saved_inputs.get("wizard_radius_km", 1.5))
    duration_minutes = int(saved_inputs.get("wizard_duration_minutes", 15))
    training_steps = int(saved_inputs.get("wizard_training_steps", 2048))
    retrain = bool(saved_inputs.get("wizard_retrain", False))
    active_events = [event.get("name") for event in scenario.get("active_events", [])]
    review_rows = [
        {"Review item": "Prompt", "Selected value": prompt},
        {"Review item": "Date and time", "Selected value": "{} at {} ({})".format(
            target_date.isoformat(), period_times[period], period)},
        {"Review item": "Study area", "Selected value": "{} km radius around Jongno".format(radius)},
        {"Review item": "Simulation length", "Selected value": "{} minutes".format(duration_minutes)},
        {"Review item": "Prompt demand assumption", "Selected value": demand_label},
        {"Review item": "Calendar events", "Selected value": ", ".join(active_events) if active_events else "None listed"},
    ]
    st.dataframe(pd.DataFrame(review_rows), use_container_width=True, hide_index=True)
    st.warning("SUMO will compare this scenario against a fixed-time reference. Results are simulated and not an approved field timing plan.")
    latest, latest_path = _latest_seoul_signal_plan()
    compare_previous = st.checkbox("Also compare with the previous simulation",
                                   value=bool(latest), disabled=not bool(latest),
                                   key="compare_previous_seoul_run")
    search_controls = st.columns(2)
    search_minutes = search_controls[0].selectbox("Search time budget", [5, 10, 15], index=1,
        format_func=lambda value: "{} minutes".format(value), key="wizard_search_budget")
    max_iterations = search_controls[1].selectbox("Maximum candidate plans", [2, 3, 5], index=1,
        key="wizard_search_iterations")
    st.caption("Search time is checked between full train/evaluate runs; an active SUMO run is allowed to finish.")
    navigation = st.columns(3)
    if navigation[0].button("← Back to settings", key="wizard_back_to_settings"):
        st.session_state["new_simulation_step"] = 2
        st.rerun()
    run_standard = navigation[1].button("Run one comparison", type="primary", use_container_width=True,
                                        key="wizard_run_comparison")
    run_search = navigation[2].button("Search for a better plan", use_container_width=True,
                                      key="wizard_run_iterative_search")

    if run_standard:
        from aatc.city_data.rl_workflow import evaluate_city_controller, train_city_controller
        seed = 42
        radius_label = str(radius).replace(".", "p")
        model_stem = ROOT / "aatc" / "rl" / "models" / "city_L01_{}km_ppo_{}_v2".format(radius_label, seed)
        if demand_factor != 1.0:
            model_stem = ROOT / "aatc" / "rl" / "models" / "city_L01_{}km_ppo_{}_v2_d{}".format(
                radius_label, seed, str(demand_factor).replace(".", "p"))
        model_path = model_stem.with_suffix(".zip")
        previous_snapshot = (_previous_run_snapshot(latest, latest_path)
                             if compare_previous and latest and latest_path else None)
        try:
            if retrain or not model_path.is_file():
                with st.spinner("Step 3 of 3: training the signal policy in SUMO..."):
                    trained = train_city_controller(city="Seoul", target_datetime=target_datetime,
                        duration=duration_minutes * 60, seed=seed, radius_km=radius,
                        timesteps=training_steps, base_density_veh_per_hour_km=8.0 * demand_factor,
                        output=str(model_stem))
                model_path = Path(trained["model_path"])
            with st.spinner("Step 3 of 3: comparing adaptive control with fixed-time control..."):
                evaluated = evaluate_city_controller(str(model_path), "Seoul", target_datetime,
                    duration_minutes * 60, seed, radius,
                    base_density_veh_per_hour_km=8.0 * demand_factor)
            report_path = Path(evaluated["comparison_path"])
            _save_city_comparison(report_path, prompt, demand_factor, demand_label, previous_snapshot)
            st.session_state["requested_seoul_page"] = "Simulation results"
            st.session_state["new_simulation_step"] = 1
            st.session_state["reset_new_simulation_form"] = True
            st.cache_data.clear()
            st.rerun()
        except (OSError, RuntimeError, ValueError) as exc:
            st.error("Could not create the signal plan: {}".format(exc))

    if run_search:
        from aatc.city_data.iterative_search import search_better_city_plan
        progress_box = st.status("Starting iterative plan search...", expanded=True)

        def show_iteration(event):
            if event["event"] == "candidate_start":
                progress_box.write("**Step {}:** {} Settings: decision interval {}s; green bounds {}–{}s.".format(
                    event["iteration"], event["reasoning"], event["settings"]["decision_interval_s"],
                    event["settings"]["min_green_s"], event["settings"]["max_green_s"]))
            elif event["event"] == "candidate_complete":
                values = event["adaptive"]
                progress_box.write("Measured queue {} · wait {} sec · trip {} sec · completed {}. {}".format(
                    fmt(values.get("mean_total_queue_length"), 2),
                    fmt(values.get("mean_total_waiting_time_s"), 2),
                    fmt(values.get("average_travel_time_s"), 2), values.get("completed_vehicles"),
                    "Better than reference." if event.get("beats_reference") else "Does not meet all improvement criteria."))

        try:
            with st.spinner("Step 3 of 3: training and comparing candidate plans..."):
                result = search_better_city_plan(city="Seoul", target_datetime=target_datetime,
                    duration=duration_minutes * 60, seed=42, radius_km=radius,
                    base_density_veh_per_hour_km=8.0 * demand_factor,
                    budget_seconds=search_minutes * 60, max_iterations=max_iterations,
                    training_timesteps=training_steps, progress_callback=show_iteration)
            progress_box.update(label=result["stop_reason"], state="complete", expanded=False)
            best = result.get("best_candidate")
            if best and best.get("comparison_path"):
                report_path = Path(best["comparison_path"])
                report = read_json(report_path) or {}
                report["scenario_description"] = prompt
                report["prompt_demand_multiplier"] = demand_factor
                report["prompt_demand_assumption"] = demand_label
                report["iterative_search"] = result
                report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                generated = generate_executive_report(report, report_path, ROOT)
                report["executive_report_path"] = generated["path"]
                report["executive_report_provider"] = generated["provider_note"]
                report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                if result.get("success"):
                    st.success("A candidate beat the reference on queue, waiting, trip time, and completed trips.")
                else:
                    st.warning("No candidate met every improvement criterion. The best-scoring candidate is available on the Results page; the fixed-time reference remains the recommendation.")
                st.session_state["requested_seoul_page"] = "Simulation results"
                st.session_state["new_simulation_step"] = 1
                st.session_state["reset_new_simulation_form"] = True
                st.cache_data.clear()
                st.rerun()
            else:
                st.error("No candidate completed. Review search_report.json for details.")
        except (OSError, RuntimeError, ValueError) as exc:
            progress_box.update(label="Search stopped with an error", state="error")
            st.error("Could not complete the iterative search: {}".format(exc))

def main():
    if st.session_state.pop("reset_new_simulation_form", False):
        st.session_state["new_simulation_step"] = 1
        st.session_state["plan_scenario_prompt"] = ""
        st.session_state["scenario_prompt_saved"] = ""
        st.session_state["plan_scenario_period"] = "Morning commute"
        st.session_state["plan_scenario_date"] = date.today()
        st.session_state["wizard_radius_km"] = 1.5
        st.session_state["wizard_duration_minutes"] = 15
        st.session_state["wizard_training_steps"] = 2048
        st.session_state["wizard_retrain"] = False
        st.session_state["wizard_saved_inputs"] = {
            "plan_scenario_date": date.today(), "plan_scenario_period": "Morning commute",
            "wizard_radius_km": 1.5, "wizard_duration_minutes": 15,
            "wizard_training_steps": 2048, "wizard_retrain": False}
    requested_page = st.session_state.pop("requested_seoul_page", None)
    if requested_page:
        st.session_state["seoul_signal_page"] = requested_page
    with st.sidebar:
        st.markdown("# 🚦 Seoul Signal Plan")
        st.caption("Jongno pilot · simulation recommendations")
        st.divider()
        page = st.radio("Page", ["Simulation results", "New simulation"],
                        label_visibility="collapsed", key="seoul_signal_page")
        st.caption("Review a completed run or start a separate simulation.")
    if page == "Simulation results":
        render_seoul_signal_results()
    else:
        render_new_simulation()
    st.markdown("<div class='muted' style='text-align:center;margin-top:3rem'>Seoul · simulated traffic signal recommendations</div>",
                unsafe_allow_html=True)

if __name__ == "__main__":
    main()
