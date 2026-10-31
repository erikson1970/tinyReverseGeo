"""Build-only Natural Earth point references and overlapping proximity circles."""

from collections import Counter
from io import BytesIO
import math
from zipfile import ZipFile

import numpy as np

from tools.geodb import Candidate, fetch_source

BASE_URL = "https://naturalearth.s3.amazonaws.com/10m_physical/"
THEMES = ("geography_regions_polys", "geography_marine_polys", "geography_regions_elevation_points")
ATTRIBUTION = "Contains Natural Earth physical-feature data (https://www.naturalearthdata.com/), public domain; sampled into approximate proximity circles."
LAND_TYPES = {"Desert": "desert", "Range/mtn": "mountain_range", "Lake": "lake",
              **{name: "physical_region" for name in ("Plateau", "Plain", "Basin", "Valley",
                  "Tundra", "Wetlands", "Gorge", "Foothills", "Depression", "Coast",
                  "Peninsula", "Pen/cape", "Delta", "Isthmus")}}
SEA_TYPES = {"ocean": "ocean", **{name: "sea" for name in
             ("sea", "gulf", "bay", "strait", "sound", "channel", "lagoon", "fjord", "inlet")}}


def distance_km(lat, lon, coordinates):
    coordinates = np.asarray(coordinates, dtype=float)
    lat1, lon1 = np.deg2rad([lat, lon])
    lon2, lat2 = np.deg2rad(coordinates[:, :2]).T
    h = np.sin((lat2-lat1)/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin((lon2-lon1)/2)**2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def read_candidates(path, theme, *, spacing=5.0):
    import shapefile
    from shapely import contains_xy, make_valid
    from shapely.geometry import shape

    name = "ne_10m_" + theme
    candidates, stats = [], Counter()
    longitude, latitude = np.meshgrid(np.arange(-180+spacing/2, 180, spacing),
                                      np.arange(-90+spacing/2, 90, spacing))
    grid = np.column_stack((longitude.ravel(), latitude.ravel()))
    with ZipFile(path) as archive:
        reader = shapefile.Reader(shp=BytesIO(archive.read(name+".shp")),
                                  shx=BytesIO(archive.read(name+".shx")),
                                  dbf=BytesIO(archive.read(name+".dbf")))
        for source_index, item in enumerate(reader.iterShapeRecords()):
            attributes = {key.lower(): value for key, value in item.record.as_dict().items()}
            category = attributes.get("featurecla")
            stats["source_features"] += 1
            if theme == THEMES[2]:
                kind = "mountain" if category == "mountain" else None
            elif theme == THEMES[1]:
                kind = SEA_TYPES.get(category)
            else:
                kind = LAND_TYPES.get(category)
            if kind is None:
                stats["excluded_" + str(category)] += 1
                continue
            label = (attributes.get("name") if theme == THEMES[1] else attributes.get("name_en")) or attributes.get("name")
            if not label:
                continue
            identity = "naturalearth:" + name + ":" + str(attributes["ne_id"])
            rank = float(attributes.get("scalerank") or 0)
            score = 30 - min(10, max(0, rank)) * 2
            if kind == "mountain":
                lon, lat = item.shape.points[0][:2]
                support = [("point", lon, lat, 5.0, "feature_type_assumption")]
            else:
                geometry = make_valid(shape(item.shape.__geo_interface__))
                if geometry.is_empty:
                    continue
                interior = geometry.representative_point()
                extent = float(np.max(distance_km(interior.y, interior.x, item.shape.points)))
                mask = contains_xy(geometry, grid[:, 0], grid[:, 1])
                support = [("anchor", interior.x, interior.y,
                            min(extent, float(np.max(distance_km(interior.y, interior.x,
                                [(interior.x+spacing/2, min(90, interior.y+spacing/2)),
                                 (interior.x-spacing/2, max(-90, interior.y-spacing/2))])))),
                            "polygon_sample_circle")]
                for index in np.flatnonzero(mask):
                    lon, lat = grid[index]
                    corners = [(lon+dx, max(-90, min(90, lat+dy)))
                               for dx in (-spacing/2, spacing/2) for dy in (-spacing/2, spacing/2)]
                    radius = min(extent, float(np.max(distance_km(lat, lon, corners))))
                    support.append((f"grid{index}", lon, lat, radius, "polygon_sample_circle"))
            for suffix, lon, lat, radius, method in support:
                record = {"source_id": identity + f":part{source_index}:" + suffix, "feature_id": identity,
                          "name": str(label), "lat": float(lat), "lon": float((lon+180) % 360-180),
                          "feature_type": kind, "radius_km": round(max(0, radius), 3),
                          "radius_method": method, "country_code": None, "region": None,
                          "population": None}
                candidates.append(Candidate(record, score + (1 if suffix in {"anchor", "point"} else 0)))
            stats["included_features"] += 1
    unique = {}
    for candidate in sorted(candidates, key=lambda c: c.source_id):
        record = candidate.record
        key = (record["feature_id"], round(record["lat"], 5), round(record["lon"], 5))
        if key not in unique or record["radius_km"] > unique[key].record["radius_km"]:
            unique[key] = candidate
    stats["redundant_support_records_removed"] = len(candidates) - len(unique)
    candidates = sorted(unique.values(), key=lambda c: c.source_id)
    stats["support_records"] = len(candidates)
    return candidates, dict(stats)


def load_candidates(cache, *, offline=False, refresh=False, locked=(), spacing=5.0):
    if not math.isfinite(spacing) or spacing < 1 or spacing > 15:
        raise ValueError("physical circle spacing must be within [1, 15] degrees")
    expected = {source["name"]: source for source in locked}
    names = ["ne_10m_" + theme + ".zip" for theme in THEMES]
    if locked and set(expected) != set(names):
        raise ValueError("Natural Earth source lock has an unexpected theme list")
    candidates, sources, reports = [], [], {}
    for theme, name in zip(THEMES, names):
        print("Physical source:", name, flush=True)
        path, info = fetch_source(name, cache, offline=offline, refresh=refresh,
                                  locked=expected.get(name), base_url=BASE_URL, license_id="Public-Domain")
        selected, report = read_candidates(path, theme, spacing=spacing)
        candidates.extend(selected)
        sources.append(info)
        reports[theme] = report
    return candidates, sources, reports
