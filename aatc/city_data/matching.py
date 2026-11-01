"""Coordinate normalization and conservative matching of API intersections to OSM."""
import math


DEFAULT_MATCH_DISTANCE_M = 80.0


def normalize_api_coordinates(x_value, y_value):
    """Normalize API integer coordinates to WGS84 lon/lat degrees.

    CrossRoadInfoService records in the Seoul feed use seven decimal places
    (for example, 1270545161 -> 127.0545161). Other documented feeds may use
    six-place integers, so select scale by magnitude and validate the result.
    """
    try:
        longitude, latitude = float(x_value), float(y_value)
    except (TypeError, ValueError):
        return None
    if abs(longitude) > 180:
        longitude /= 10_000_000.0 if abs(longitude) >= 1_000_000_000 else 1_000_000.0
    if abs(latitude) > 90:
        latitude /= 10_000_000.0 if abs(latitude) >= 100_000_000 else 1_000_000.0
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return None
    return longitude, latitude


def haversine_m(lon1, lat1, lon2, lat2):
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    angle = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    return 2.0 * radius * math.asin(min(1.0, math.sqrt(angle)))


def _first(record, *keys):
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def match_intersections(api_records, osm_nodes, max_distance_m=DEFAULT_MATCH_DISTANCE_M):
    """Match records to nearby OSM candidates; > threshold remains explicitly unmatched."""
    max_distance_m = float(max_distance_m)
    if max_distance_m <= 0:
        raise ValueError("max_distance_m must be positive")
    # A 0.0005 degree grid bounds the search even for city-scale OSM extracts.
    cell_degrees = 0.0005
    grid = {}
    for node in osm_nodes:
        try:
            lat, lon = float(node["latitude"]), float(node["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (int(math.floor(lat / cell_degrees)), int(math.floor(lon / cell_degrees)))
        grid.setdefault(key, []).append((str(node["osmid"]), lon, lat, node))
    span = max(2, int(math.ceil(max_distance_m / 45.0)) + 1)
    matches = []
    used_osm_nodes = {}
    for record in api_records:
        coords = normalize_api_coordinates(_first(record, "X_COORD", "x_coord"),
                                           _first(record, "Y_COORD", "y_coord"))
        normalized = dict(record)
        normalized["normalized_longitude"] = coords[0] if coords else None
        normalized["normalized_latitude"] = coords[1] if coords else None
        nearest = None
        if coords:
            lon, lat = coords
            row, col = int(math.floor(lat / cell_degrees)), int(math.floor(lon / cell_degrees))
            candidates = []
            for drow in range(-span, span + 1):
                for dcol in range(-span, span + 1):
                    candidates.extend(grid.get((row + drow, col + dcol), ()))
            if candidates:
                nearest = min(candidates, key=lambda item: haversine_m(lon, lat, item[1], item[2]))
        distance = haversine_m(coords[0], coords[1], nearest[1], nearest[2]) if coords and nearest else None
        accepted = distance is not None and distance <= max_distance_m
        osm_id = nearest[0] if accepted else None
        duplicate_of = used_osm_nodes.get(osm_id) if osm_id else None
        if duplicate_of:
            accepted = False
            osm_id = None
        confidence = ("high" if distance is not None and distance <= 30 else
                      "medium" if accepted else "low")
        if accepted:
            used_osm_nodes[osm_id] = _first(record, "INT_NO", "int_no")
        matches.append({
            "REGION_CD": _first(record, "REGION_CD", "region_cd"),
            "INT_NO": _first(record, "INT_NO", "int_no"),
            "INT_NM": _first(record, "INT_NM", "int_nm"),
            "api_coordinates_raw": {"X_COORD": _first(record, "X_COORD", "x_coord"),
                                    "Y_COORD": _first(record, "Y_COORD", "y_coord")},
            "longitude": coords[0] if coords else None,
            "latitude": coords[1] if coords else None,
            "coordinate_conversion": "magnitude-based e6/e7 integer coordinate scaling",
            "osm_node_id": osm_id,
            "nearest_osm_node_id": nearest[0] if nearest else None,
            "matching_distance_m": round(distance, 2) if distance is not None else None,
            "matching_confidence": confidence,
            "matched": bool(accepted),
            "unmatched_reason": ("invalid_coordinates" if coords is None else
                                 "duplicate_nearest_osm_node" if duplicate_of else
                                 "outside_distance_threshold" if not accepted else None),
            "duplicate_match_of_INT_NO": duplicate_of,
            "raw_record": normalized,
        })
    return matches
