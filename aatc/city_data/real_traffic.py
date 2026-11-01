"""Import and analyze measured Seoul Smart Intersection traffic-volume files."""
import csv
import io
import json
import re
import statistics
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEOUL_TRAFFIC_ROOT = PROJECT_ROOT / "data" / "cities" / "seoul" / "traffic"
VOLUME_SOURCE = "Seoul T-Data Smart Intersection traffic-volume CSV"
VOLUME_SOURCE_URL = "https://t-data.seoul.go.kr/dataprovide/trafficdatalist.do?categoryLargeList=CATE_N1_10&detailSearchYn=Y"
LINK_SPEED_API_URL = "https://t-data.seoul.go.kr/apig/apiman-gateway/tapi/TopisIccStTimesLinkTrfSectionStats/1.0"


def estimate_speed_based_demand(rows, assumed_trip_distance_km=3.0):
    """Estimate a randomTrips insertion density from hourly speed observations.

    This is an explicit planning proxy, not observed volume. It uses road-class
    priors and a Greenshields speed-density curve to estimate flow per lane, then
    divides by assumed mean trip distance to obtain SUMO's lane-km insertion rate.
    """
    if float(assumed_trip_distance_km) <= 0:
        raise ValueError("assumed_trip_distance_km must be positive")
    priors = (
        (("expressway", "motorway", "도시고속", "고속도로"), 80.0, 1900.0),
        (("main arterial", "주간선", "간선도로"), 60.0, 1600.0),
        (("secondary arterial", "보조간선"), 50.0, 1300.0),
        (("collector", "집산"), 40.0, 1000.0),
    )
    link_flows = []
    speeds = []
    for row in rows:
        try:
            speed = float(row.get("avgSpd", row.get("average_speed_kmh")))
        except (TypeError, ValueError):
            continue
        if speed <= 0:
            continue
        road_class = str(row.get("roadDivNm") or row.get("road_type") or "").casefold()
        free_flow, capacity = 35.0, 800.0
        for labels, class_free_flow, class_capacity in priors:
            if any(label.casefold() in road_class for label in labels):
                free_flow, capacity = class_free_flow, class_capacity
                break
        ratio = min(0.95, max(0.05, speed / free_flow))
        estimated_flow = 4.0 * capacity * ratio * (1.0 - ratio)
        link_flows.append(estimated_flow)
        speeds.append(speed)
    if not link_flows:
        return None
    flow = float(statistics.median(link_flows))
    insertion_density = flow / float(assumed_trip_distance_km)
    return {
        "method": "Greenshields speed-density proxy with road-class priors",
        "records_used": len(link_flows),
        "median_observed_speed_kmh": round(float(statistics.median(speeds)), 2),
        "estimated_flow_vph_per_lane": round(flow, 1),
        "assumed_mean_trip_distance_km": float(assumed_trip_distance_km),
        "sumo_insertion_density_vph_per_lane_km": round(insertion_density, 1),
        "road_class_priors": "80/60/50/40/35 km/h free-flow speeds and 1900/1600/1300/1000/800 veh/h/lane capacities",
        "limitations": [
            "Estimated flow, not a measured vehicle count.",
            "Uses Seoul-wide speed records as a proxy for the selected OSM subnetwork.",
            "The assumed mean trip distance and road-class capacities materially affect the result."
        ]
    }


class _NoRedirectHandler(HTTPRedirectHandler):
    """Do not risk forwarding the API key through an HTTP redirect."""
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def load_tdata_api_key():
    """Load the T-Data key from process env or the ignored project .env."""
    import os
    value = os.environ.get("SEOUL_TDATA_API_KEY")
    if value:
        return value.strip().strip('"').strip("'")
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() == "SEOUL_TDATA_API_KEY":
                return value.strip().strip('"').strip("'") or None
    return None


def fetch_tdata_daily_link_speeds(stnd_date, api_key, page_size=1000000, timeout_seconds=90):
    """Fetch one requested date of hourly link speeds over HTTPS.

    Dataset 1015 does not expose a time or area query parameter, so each API
    response includes the available Seoul-wide rows for the date. Callers can
    filter the returned records to a selected hour. No response is written here.
    """
    if isinstance(stnd_date, datetime):
        day = stnd_date.date()
    elif isinstance(stnd_date, date):
        day = stnd_date
    else:
        try:
            day = date.fromisoformat(str(stnd_date))
        except ValueError as exc:
            raise ValueError("Date must use YYYY-MM-DD format") from exc
    if not api_key:
        raise ValueError("SEOUL_TDATA_API_KEY is missing from .env and the process environment")
    if int(page_size) < 1 or int(page_size) > 1000000:
        raise ValueError("page_size must be between 1 and 1000000")

    opener = build_opener(_NoRedirectHandler)
    rows = []
    start_row = 1
    while True:
        query = urlencode({"startRow": start_row, "stndDt": day.strftime("%Y%m%d"),
                           "apikey": api_key, "rowCnt": int(page_size)})
        request = Request(LINK_SPEED_API_URL + "?" + query,
                          headers={"Accept": "application/json", "User-Agent": "TrafficFlowPrototype/1.0"})
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                if not response.geturl().lower().startswith("https://"):
                    raise ValueError("T-Data redirected the request away from HTTPS; response was not read")
                page = json.loads(response.read().decode("utf-8-sig"))
        except HTTPError as exc:
            raise ValueError("T-Data returned HTTP {} for {}".format(exc.code, day.isoformat())) from None
        except (URLError, TimeoutError, OSError):
            raise ValueError("HTTPS request to T-Data failed for {}".format(day.isoformat())) from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("T-Data returned an invalid JSON response for {}".format(day.isoformat())) from None
        if not isinstance(page, list):
            raise ValueError("T-Data returned an unexpected response format for {}".format(day.isoformat()))
        rows.extend(page)
        if len(page) < int(page_size):
            return rows
        start_row += len(page)


def _decode_csv(source):
    if isinstance(source, bytes):
        for encoding in ("utf-8-sig", "cp949", "euc-kr"):
            try:
                return source.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("Could not decode CSV as UTF-8 or Korean CP949")
    return str(source)


def _headers(row):
    return {str(key).strip().lstrip("\ufeff").upper(): value
            for key, value in row.items() if key is not None}


def _number(value):
    if value is None:
        return 0.0
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "N/A", "null", "None"):
        return 0.0
    try:
        return max(0.0, float(text))
    except ValueError:
        return 0.0


def _hour(value):
    text = str(value or "").strip().replace(":", "")
    if not text:
        return None
    try:
        return max(0, min(23, int(text[:2])))
    except ValueError:
        return None


def parse_smart_intersection_volume_csv(source, year=None):
    """Normalize supported hourly or daily T-Data count exports.

    Output values remain counts reported by the detectors. Daily-format rows are
    expanded into 24 hourly bins. This does not synthesize values for missing bins.
    """
    text = _decode_csv(source)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("The selected file has no CSV header row")
    available = {str(name).strip().lstrip("\ufeff").upper() for name in reader.fieldnames}
    rows = []
    if {"CROSS_ID", "STND_DT", "STND_TM"}.issubset(available):
        for raw in reader:
            row = _headers(raw)
            day = re.sub(r"\D", "", str(row.get("STND_DT", "")))[:8]
            if len(day) != 8 or (year is not None and not day.startswith(str(int(year)))):
                continue
            hour = _hour(row.get("STND_TM"))
            if hour is None:
                continue
            turns = {name.lower(): _number(row.get(column)) for name, column in (
                ("left", "LTURN_VOL"), ("through", "STURN_VOL"),
                ("right", "RTURN_VOL"), ("uturn", "UTURN_VOL"))}
            if not any(turns.values()):
                continue
            rows.append({"date": "{}-{}-{}".format(day[:4], day[4:6], day[6:8]),
                         "hour": hour, "intersection_id": str(row.get("CROSS_ID", "")).strip(),
                         "intersection_name": str(row.get("CROSS_NM", "")).strip(),
                         "direction_code": str(row.get("DIR_CD", "")).strip(),
                         "direction_name": str(row.get("DIR_NM", "")).strip(),
                         "left_turn_vehicles": turns["left"],
                         "through_vehicles": turns["through"],
                         "right_turn_vehicles": turns["right"],
                         "uturn_vehicles": turns["uturn"],
                         "total_vehicles": sum(turns.values()),
                         "source_period": "hourly_count"})
    elif {"CROSS_ID", "STND_DT", "TURN_TYPE_CD"}.issubset(available):
        hour_columns = {hour: "TURN_CNT{}".format(hour) for hour in range(24)}
        if not set(hour_columns.values()).issubset(available):
            raise ValueError("Daily traffic file is missing one or more TURN_CNT0..TURN_CNT23 columns")
        movements = {"L": "left_turn_vehicles", "S": "through_vehicles",
                     "R": "right_turn_vehicles", "U": "uturn_vehicles"}
        for raw in reader:
            row = _headers(raw)
            day = re.sub(r"\D", "", str(row.get("STND_DT", "")))[:8]
            if len(day) != 8 or (year is not None and not day.startswith(str(int(year)))):
                continue
            movement = str(row.get("TURN_TYPE_CD", "")).strip().upper()
            target = movements.get(movement)
            if not target:
                continue
            for hour, column in hour_columns.items():
                count = _number(row.get(column))
                if count <= 0:
                    continue
                values = {name: 0.0 for name in movements.values()}
                values[target] = count
                rows.append({"date": "{}-{}-{}".format(day[:4], day[4:6], day[6:8]),
                             "hour": hour, "intersection_id": str(row.get("CROSS_ID", "")).strip(),
                             "intersection_name": str(row.get("CROSS_NM", "")).strip(),
                             "direction_code": str(row.get("DIR_CD", "")).strip(),
                             "direction_name": str(row.get("DIR_NM", "")).strip(),
                             "left_turn_vehicles": values["left_turn_vehicles"],
                             "through_vehicles": values["through_vehicles"],
                             "right_turn_vehicles": values["right_turn_vehicles"],
                             "uturn_vehicles": values["uturn_vehicles"],
                             "total_vehicles": count, "source_period": "daily_count_expanded_hourly"})
    else:
        raise ValueError("Unsupported T-Data volume CSV. Expected hourly CROSS_ID/STND_DT/STND_TM turn counts or daily TURN_CNT0..23 rows.")
    if not rows:
        raise ValueError("No measured count rows{} were found in the CSV".format(
            " for {}".format(year) if year else ""))
    return rows


def _name_key(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z\uac00-\ud7a3]", "", value)


def analyze_smart_intersection_volumes(rows, government_records=None):
    """Summarize measured counts and conservatively match official names."""
    intersections = defaultdict(lambda: {"name": "", "total": 0.0, "samples": 0,
                                          "hours": defaultdict(float), "directions": defaultdict(float)})
    hourly = defaultdict(float)
    daily = defaultdict(float)
    for row in rows:
        key = row["intersection_id"] or row["intersection_name"]
        group = intersections[key]
        group["name"] = row["intersection_name"] or group["name"]
        count = float(row["total_vehicles"])
        group["total"] += count
        group["samples"] += 1
        group["hours"][int(row["hour"])] += count
        group["directions"][row["direction_name"] or row["direction_code"]] += count
        hourly[int(row["hour"])] += count
        daily[row["date"]] += count

    official_by_name = {}
    for record in government_records or []:
        name = record.get("INT_NM") or record.get("int_nm")
        identifier = record.get("INT_NO") or record.get("int_no")
        normalized = _name_key(name)
        if normalized and identifier:
            official_by_name.setdefault(normalized, []).append(str(identifier))

    ranked = []
    for intersection_id, item in intersections.items():
        hours = item["hours"]
        peak_hour = max(hours, key=hours.get) if hours else None
        matches = official_by_name.get(_name_key(item["name"]), [])
        ranked.append({"intersection_id": intersection_id, "intersection_name": item["name"],
                       "measured_vehicle_count": round(item["total"], 2),
                       "observed_hourly_samples": item["samples"],
                       "mean_vehicles_per_observation": round(item["total"] / item["samples"], 2)
                       if item["samples"] else 0,
                       "peak_observed_hour": peak_hour,
                       "peak_hour_vehicle_count": round(hours.get(peak_hour, 0.0), 2) if peak_hour is not None else 0,
                       "direction_counts": {key: round(value, 2) for key, value in item["directions"].items()},
                       "government_intersection_ids_exact_name_match": matches,
                       "government_name_match_status": ("one_exact_name_match" if len(matches) == 1 else
                                                        "ambiguous_exact_name_match" if matches else "unmatched"),
                       "priority_label": "highest measured-volume site; congestion requires speed/queue evidence"})
    ranked.sort(key=lambda row: (row["peak_hour_vehicle_count"], row["measured_vehicle_count"]), reverse=True)
    peak_hour = max(hourly, key=hourly.get)
    return {"source": VOLUME_SOURCE, "source_url": VOLUME_SOURCE_URL,
            "records_are_measured_counts": True,
            "counts_are_not_speed_or_congestion_labels": True,
            "row_count": len(rows), "intersection_count": len(intersections),
            "date_start": min(row["date"] for row in rows),
            "date_end": max(row["date"] for row in rows),
            "total_vehicle_observations": round(sum(daily.values()), 2),
            "highest_aggregate_hour": peak_hour,
            "hourly_vehicle_observations": {str(hour): round(hourly.get(hour, 0.0), 2)
                                             for hour in range(24)},
            "daily_vehicle_observations": {key: round(value, 2) for key, value in sorted(daily.items())},
            "highest_volume_intersections": ranked[:100],
            "priority_note": "High measured volumes are prioritized for review, but volume alone does not establish congestion. Use observed speed or queue data for a congestion label."}


def ingest_smart_intersection_volume(source, year=None, city="Seoul", output_root=None,
                                     government_records=None):
    """Save normalized measured counts and their analysis to a versioned local folder."""
    rows = parse_smart_intersection_volume_csv(source, year=year)
    summary = analyze_smart_intersection_volumes(rows, government_records)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(output_root) if output_root else SEOUL_TRAFFIC_ROOT
    output_dir = root / "volume_imports" / ("{}_{}".format(year or "all_years", stamp))
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "normalized_hourly_counts.csv"
    fields = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    summary.update({"city": city, "requested_year": int(year) if year is not None else None,
                    "retrieved_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    "normalized_csv": str(csv_path.resolve()),
                    "source_data_license_note": "Check the source file's current terms; T-Data dataset 20004 is listed as Seoul Metropolitan Government attribution (BY)."})
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"directory": str(output_dir.resolve()), "csv_path": str(csv_path.resolve()),
            "summary_path": str(summary_path.resolve()), "summary": summary}
