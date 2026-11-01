"""Supported Korean cities listed by the CrossRoadInfoService guide."""
import json
from pathlib import Path


REGION_CONFIG = Path(__file__).resolve().parents[2] / "config" / "korean_regions.json"


def get_regions():
    with REGION_CONFIG.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)["regions"]


def resolve_region(city_or_code):
    value = str(city_or_code).strip().casefold()
    for region in get_regions():
        candidates = (region["code"], region["city"], region["city_ko"])
        if any(value == candidate.casefold() for candidate in candidates):
            return dict(region)
    raise ValueError("City is not listed in the government traffic-signal API region catalog")
