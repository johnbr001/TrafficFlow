"""Cache-aware city data sync joining OSM roads with official signal records."""
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .crossroad_api import CrossRoadInfoClient
from .matching import match_intersections
from .regions import resolve_region
from .osm_city import acquire_osm_city


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CITY_ROOT = PROJECT_ROOT / "data" / "cities"
PHASE_KEYS = ["{}_RING_{}_PHASE_CONF_CD".format(ring, phase)
              for ring in ("A", "B") for phase in range(1, 9)]
MOVEMENTS = {"S": "straight", "L": "left_turn", "P": "pedestrian"}


def parse_phase_code(value):
    """Parse only the documented direction + entry/exit angle fields; retain unknown codes."""
    raw = str(value or "").strip()
    match = re.fullmatch(r"(.)(\d{3})(\d{3})", raw)
    if not match:
        return {"raw_code": raw or None, "valid_format": False, "direction_code": None,
                "movement": None, "entry_angle_code": None, "exit_angle_code": None}
    direction, entry, exit_angle = match.groups()
    return {"raw_code": raw, "valid_format": True, "direction_code": direction,
            "movement": MOVEMENTS.get(direction, "undocumented"),
            "entry_angle_code": entry, "exit_angle_code": exit_angle}


def _keyed(value, *keys):
    for key in keys:
        if value.get(key) not in (None, ""):
            return str(value[key])
    return ""


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def _load_json(path, fallback):
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return fallback


def _phase_plan(detail):
    result = dict(detail)
    result["phase_codes_parsed"] = {key: parse_phase_code(detail.get(key))
                                    for key in PHASE_KEYS if detail.get(key)}
    result["MAP_NO_note"] = "Official MAP_NO identifier; time-of-day ranges are not defined by the API guide."
    return result


def _osm_intersection_nodes(intersections_path):
    try:
        payload = _load_json(intersections_path, {})
        features = payload.get("features", [])
        rows = []
        for feature in features:
            properties = feature.get("properties", {})
            coordinates = feature.get("geometry", {}).get("coordinates", [])
            if len(coordinates) >= 2:
                rows.append({"osmid": properties.get("osmid", feature.get("id")),
                             "longitude": coordinates[0], "latitude": coordinates[1],
                             "approach_count": properties.get("approach_count"),
                             "traffic_signal_osm": properties.get("traffic_signal_osm", False)})
        return rows
    except (AttributeError, TypeError):
        return []


def sync_city_data(city="Seoul", refresh_osm=False, refresh_signals=False,
                   city_root=None, matching_threshold_m=80.0):
    """Update cached OSM and government data; neither source is redownloaded by default."""
    region = resolve_region(city)
    root = Path(city_root) if city_root else DEFAULT_CITY_ROOT
    city_key = region["city"].casefold().replace(" ", "_")
    city_dir = root / city_key
    gov_dir = city_dir / "government"
    gov_dir.mkdir(parents=True, exist_ok=True)
    osm_result = None
    warnings = []
    try:
        osm_result = acquire_osm_city(region["code"], output_root=root, refresh=refresh_osm)
    except (RuntimeError, OSError, ValueError, ImportError) as exc:
        warnings.append("OSM data: {}".format(exc))
    osm_metadata = osm_result.get("metadata", {}) if osm_result else _load_json(city_dir / "osm" / "metadata.json", {})

    list_path = gov_dir / "crossroads.json"
    details_path = gov_dir / "signal_details.json"
    api_cached = list_path.is_file() and details_path.is_file() and not refresh_signals
    if api_cached:
        crossroads = [item for item in _load_json(list_path, [])
                      if isinstance(item, dict) and _keyed(item, "INT_NO", "int_no")]
        details = [item for item in _load_json(details_path, [])
                   if isinstance(item, dict) and _keyed(item, "INT_NO", "int_no")]
        api_cached = bool(crossroads or details)
    if api_cached:
        api_retrieved_at = _load_json(gov_dir / "metadata.json", {}).get("retrieved_at_utc")
    else:
        try:
            client = CrossRoadInfoClient()
            crossroads, details = client.fetch_city(region["code"])
            crossroads = [item for item in crossroads if _keyed(item, "INT_NO", "int_no")]
            details = [item for item in details if _keyed(item, "INT_NO", "int_no")]
            api_retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            _write_json(list_path, crossroads)
            _write_json(details_path, details)
            if crossroads or details:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                snapshot_dir = gov_dir / "versions" / stamp
                _write_json(snapshot_dir / "crossroads.json", crossroads)
                _write_json(snapshot_dir / "signal_details.json", details)
                _write_json(snapshot_dir / "snapshot.json", {"retrieved_at_utc": api_retrieved_at,
                            "region_code": region["code"], "crossroad_count": len(crossroads),
                            "signal_detail_count": len(details), "source": "CrossRoadInfoService"})
            if not crossroads:
                warnings.append("Government API returned no {} intersection records; no official signal plan was inferred".format(region["city"]))
        except (RuntimeError, OSError, ValueError) as exc:
            warnings.append("Government signal data: {}".format(exc))
            crossroads = [item for item in _load_json(list_path, [])
                          if isinstance(item, dict) and _keyed(item, "INT_NO", "int_no")]
            details = [item for item in _load_json(details_path, [])
                       if isinstance(item, dict) and _keyed(item, "INT_NO", "int_no")]
            api_retrieved_at = _load_json(gov_dir / "metadata.json", {}).get("retrieved_at_utc")

    details_by_id = {}
    for detail in details:
        identifier = _keyed(detail, "INT_NO", "int_no")
        if identifier:
            details_by_id.setdefault(identifier, []).append(_phase_plan(detail))
    enriched = []
    for record in crossroads:
        identifier = _keyed(record, "INT_NO", "int_no")
        enriched.append({"intersection": record, "signal_plans": details_by_id.get(identifier, [])})
    _write_json(gov_dir / "signal_reference.json", enriched)

    osm_nodes = _osm_intersection_nodes(city_dir / "osm" / "intersections.geojson")
    if crossroads and osm_nodes:
        matches = match_intersections(crossroads, osm_nodes, matching_threshold_m)
        _write_json(city_dir / "intersection_matches.json", matches)
        unmatched = [item for item in matches if not item["matched"]]
        _write_json(city_dir / "unmatched_government_intersections.json", unmatched)
    else:
        matches = _load_json(city_dir / "intersection_matches.json", [])
        if crossroads and not osm_nodes:
            warnings.append("Intersection matching is pending because the OSM junction file is unavailable")

    files = [path for path in (city_dir / "osm" / "road_network.graphml",
                               city_dir / "osm" / "road_network.geojson",
                               city_dir / "osm" / "intersections.geojson",
                               city_dir / "osm" / "pois.geojson", list_path, details_path)
             if path.is_file()]
    hashes = {}
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[str(path.relative_to(city_dir)).replace("\\", "/")] = digest
    metadata = {
        "city": region["city"], "country": "KR", "region_code": region["code"],
        "osm_place": region["osm_place"], "api_source": "CrossRoadInfoService",
        "api_retrieved_at_utc": api_retrieved_at, "osm_metadata": osm_metadata,
        "api_records_cached": api_cached, "crossroad_count": len(crossroads),
        "signal_detail_count": len(details), "osm_junction_count": len(osm_nodes),
        "matched_intersection_count": sum(1 for item in matches if item.get("matched")),
        "unmatched_intersection_count": sum(1 for item in matches if not item.get("matched")),
        "matching_threshold_m": float(matching_threshold_m), "sha256": hashes,
        "warnings": warnings,
    }
    _write_json(gov_dir / "metadata.json", {"retrieved_at_utc": api_retrieved_at,
                 "region_code": region["code"], "crossroad_count": len(crossroads),
                 "signal_detail_count": len(details)})
    _write_json(city_dir / "manifest.json", metadata)
    from .ledger import record_city_data_version
    record_city_data_version(metadata)
    return {"city": region, "directory": str(city_dir.resolve()), "metadata": metadata,
            "cached_osm": bool(osm_result and osm_result.get("cached")), "cached_api": api_cached,
            "crossroads": crossroads, "signal_details": details, "matches": matches,
            "warnings": warnings}
