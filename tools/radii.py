"""Declared build-time radius heuristics; no runtime density-grid dependency."""

import json
import math
from pathlib import Path

DEFAULT_DENSITY = {"default": 1000.0, "countries": {}, "regions": {},
                   "source_note": "Configurable urban-density assumption; not measured regional data."}


def density_config(path=None):
    config = dict(DEFAULT_DENSITY) if path is None else json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("density configuration must be a JSON object")
    config = {**DEFAULT_DENSITY, **config}
    if not isinstance(config["countries"], dict) or not isinstance(config["regions"], dict):
        raise ValueError("countries and regions density overrides must be objects")
    for value in [config["default"], *config["countries"].values(), *config["regions"].values()]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("population densities must be positive finite people/km²")
    return config


def estimate_radius(record, config, *, minimum=0.5, maximum=40.0, fallback=1.0, point_radii=False):
    result = dict(record)
    if result["feature_type"] not in {"city", "town", "remote_settlement"}:
        return result
    result["feature_id"] = result.get("feature_id") or result["source_id"]
    if point_radii:
        result.update(radius_km=0.0, radius_method="point")
    elif result.get("population", 0):
        region_key = f"{result.get('country_code') or ''}/{result.get('region') or ''}"
        density = config["regions"].get(region_key, config["countries"].get(result.get("country_code"), config["default"]))
        radius = math.sqrt(result["population"] / (math.pi * density))
        result.update(radius_km=round(min(maximum, max(minimum, radius)), 3), radius_method="population_density_assumption")
    else:
        result.update(radius_km=round(fallback, 3), radius_method="unknown_population_fallback")
    return result
