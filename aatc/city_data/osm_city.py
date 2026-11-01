"""Acquire and cache an OSM drivable graph, junctions, and traffic generators."""
import json
from datetime import datetime, timezone
from pathlib import Path

from .regions import resolve_region


POI_TAGS = {
    "landuse": ["industrial", "commercial", "retail"],
    "industrial": True,
    "amenity": ["university", "college", "hospital", "stadium", "bus_station",
                "place_of_worship", "convention_centre"],
    "leisure": ["stadium", "park", "sports_centre"],
    "tourism": ["attraction", "museum", "theme_park"],
    "shop": ["mall"],
    "railway": "station",
    "public_transport": "station",
    "building": ["industrial", "commercial", "stadium"],
}
POI_SCORES = {
    "stadium": 1.0, "transport_hub": 0.95, "industrial": 0.85, "shopping": 0.82,
    "university": 0.75, "hospital": 0.72, "tourism": 0.68, "sports": 0.65,
    "commercial": 0.60, "religious": 0.55, "park": 0.45, "other": 0.40,
}


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_geojson(path, frame):
    path.write_text(frame.to_json(drop_id=False), encoding="utf-8")


def _poi_category(tags):
    values = {str(value).casefold() for key, value in tags.items()
              if key in ("amenity", "landuse", "industrial", "leisure", "tourism", "shop",
                         "railway", "public_transport", "building")}
    if "stadium" in values:
        return "stadium"
    if values.intersection({"station", "bus_station"}):
        return "transport_hub"
    if values.intersection({"industrial", "factory"}):
        return "industrial"
    if values.intersection({"mall", "retail"}):
        return "shopping"
    if values.intersection({"university", "college"}):
        return "university"
    if "hospital" in values:
        return "hospital"
    if values.intersection({"attraction", "museum", "theme_park"}):
        return "tourism"
    if values.intersection({"sports_centre"}):
        return "sports"
    if "commercial" in values:
        return "commercial"
    if "place_of_worship" in values:
        return "religious"
    if "park" in values:
        return "park"
    return "other"


def _intersection_nodes(graph, nodes):
    rows = []
    for node_id, row in nodes.iterrows():
        node_text = str(node_id)
        adjacent = set(graph.successors(node_id)) | set(graph.predecessors(node_id))
        tags = row.to_dict()
        if len(adjacent) < 3 and tags.get("highway") != "traffic_signals":
            continue
        rows.append({"osmid": node_text, "latitude": float(row.geometry.y),
                     "longitude": float(row.geometry.x), "approach_count": len(adjacent),
                     "traffic_signal_osm": tags.get("highway") == "traffic_signals",
                     "connected_node_ids": [str(item) for item in sorted(adjacent, key=str)],
                     "ref": tags.get("ref"), "name": tags.get("name"),
                     "highway": tags.get("highway")})
    return rows


def acquire_osm_city(city, output_root=None, refresh=False):
    """Load cached OSM files or download and locally persist a city's drivables and POIs."""
    region = resolve_region(city)
    root = Path(output_root) if output_root else Path(__file__).resolve().parents[2] / "data" / "cities"
    city_key = region["city"].casefold().replace(" ", "_")
    city_dir = root / city_key
    osm_dir = city_dir / "osm"
    osm_dir.mkdir(parents=True, exist_ok=True)
    graph_path = osm_dir / "road_network.graphml"
    road_path = osm_dir / "road_network.geojson"
    intersections_path = osm_dir / "intersections.geojson"
    pois_path = osm_dir / "pois.geojson"
    metadata_path = osm_dir / "metadata.json"
    cached_files = (graph_path, road_path, intersections_path, pois_path, metadata_path)
    if all(path.is_file() for path in cached_files) and not refresh:
        with metadata_path.open("r", encoding="utf-8") as handle:
            metadata = json.load(handle)
        return {"city": region, "directory": str(city_dir.resolve()), "metadata": metadata,
                "cached": True, "files": {"graphml": str(graph_path), "roads": str(road_path),
                                             "intersections": str(intersections_path), "pois": str(pois_path)}}

    try:
        import osmnx as ox
    except ImportError as exc:
        raise RuntimeError("OSM downloads require OSMnx; install requirements.txt") from exc

    ox.settings.use_cache = True
    ox.settings.log_console = False
    ox.settings.timeout = 180
    # Seoul's municipal boundary is large for a local SUMO prototype. A configured
    # study-area place keeps OSM acquisition and subnetworks tractable while the
    # government API remains queried with the city-wide Seoul region code.
    place = region.get("osm_testbed_place") or region["osm_place"]
    graph = ox.graph_from_place(place, network_type="drive", simplify=True, retain_all=False)
    nodes, edges = ox.graph_to_gdfs(graph, nodes=True, edges=True)
    _write_geojson(road_path, edges)
    intersection_rows = _intersection_nodes(graph, nodes)
    intersections_geo = nodes.loc[[int(row["osmid"]) if str(row["osmid"]).lstrip("-").isdigit()
                                   else row["osmid"] for row in intersection_rows]].copy()
    if intersection_rows:
        intersections_geo["osmid"] = [row["osmid"] for row in intersection_rows]
        intersections_geo["approach_count"] = [row["approach_count"] for row in intersection_rows]
        intersections_geo["traffic_signal_osm"] = [row["traffic_signal_osm"] for row in intersection_rows]
        intersections_geo["connected_node_ids"] = [json.dumps(row["connected_node_ids"])
                                                     for row in intersection_rows]
    _write_geojson(intersections_path, intersections_geo)
    feature_frame = ox.features_from_place(place, tags=POI_TAGS)
    poi_rows = []
    if not feature_frame.empty:
        for osmid, row in feature_frame.iterrows():
            geom = row.geometry
            point = geom if geom.geom_type == "Point" else geom.representative_point()
            tags = row.drop(labels=["geometry"], errors="ignore").to_dict()
            category = _poi_category(tags)
            name = tags.get("name") or tags.get("name:en") or tags.get("brand") or "Unnamed OSM feature"
            poi_rows.append({"poi_id": str(osmid), "name": str(name), "category": category,
                             "importance_estimate": POI_SCORES[category],
                             "importance_basis": "configurable OSM category heuristic; not measured traffic",
                             "latitude": float(point.y), "longitude": float(point.x), **tags})
    import geopandas as gpd
    if poi_rows:
        poi_frame = gpd.GeoDataFrame(poi_rows, geometry=[
            (row.geometry if row.geometry.geom_type == "Point" else row.geometry.representative_point())
            for _, row in feature_frame.iterrows()], crs=feature_frame.crs)
    else:
        poi_frame = feature_frame.iloc[:0].copy()
    _write_geojson(pois_path, poi_frame)
    metadata = {
        "city": region["city"], "region_code": region["code"], "country": "KR",
        "place_query": place, "region_place": region["osm_place"],
        "retrieved_at_utc": _utc_now(),
        "network_type": "drive", "osm_graph_nodes": int(len(nodes)), "road_segments": int(len(edges)),
        "intersection_candidates": int(len(intersection_rows)), "potential_traffic_generators": int(len(poi_rows)),
        "poi_score_note": "Importance is a configurable category estimate, not observed traffic volume.",
        "coordinate_crs": str(graph.graph.get("crs", "EPSG:4326")),
        "osm_data_copyright": "© OpenStreetMap contributors, ODbL 1.0",
        "source": "OpenStreetMap via OSMnx / Overpass API",
    }
    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, ensure_ascii=False)
    ox.save_graphml(graph, filepath=graph_path)
    return {"city": region, "directory": str(city_dir.resolve()), "metadata": metadata,
            "cached": False, "files": {"graphml": str(graph_path), "roads": str(road_path),
                                         "intersections": str(intersections_path), "pois": str(pois_path)}}
