"""Import an official Seoul Smart Intersection volume CSV into the local cache."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aatc.city_data.real_traffic import ingest_smart_intersection_volume


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="T-Data hourly or daily Smart Intersection volume CSV")
    parser.add_argument("--year", type=int, default=2025,
                        help="keep only this data year (default: 2025)")
    parser.add_argument("--city", default="Seoul")
    args = parser.parse_args()
    csv_path = Path(args.csv)
    if not csv_path.is_file():
        parser.error("CSV file was not found: {}".format(csv_path))
    city_root = Path(__file__).resolve().parents[1] / "data" / "cities" / args.city.casefold().replace(" ", "_")
    government_path = city_root / "government" / "crossroads.json"
    government_records = []
    if government_path.is_file():
        with government_path.open("r", encoding="utf-8-sig") as handle:
            government_records = json.load(handle)
    try:
        result = ingest_smart_intersection_volume(csv_path.read_bytes(), args.year,
                                                  args.city,
                                                  output_root=city_root / "traffic",
                                                  government_records=government_records)
    except (OSError, ValueError) as exc:
        print("Traffic volume import failed: {}".format(exc), file=sys.stderr)
        return 2
    summary = result["summary"]
    print(json.dumps({"directory": result["directory"], "summary_path": result["summary_path"],
                      "row_count": summary["row_count"], "date_start": summary["date_start"],
                      "date_end": summary["date_end"],
                      "intersection_count": summary["intersection_count"],
                      "highest_volume_intersections": summary["highest_volume_intersections"][:20],
                      "priority_note": summary["priority_note"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
