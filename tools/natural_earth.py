"""Build-only Natural Earth point references and overlapping proximity circles."""

from collections import Counter, defaultdict
from io import BytesIO
import math
from zipfile import ZipFile

import numpy as np

from tools.geodb import Candidate, fetch_source

BASE_URL = "https://naturalearth.s3.amazonaws.com/10m_physical/"
THEMES = ("geography_regions_polys", "geography_marine_polys", "geography_regions_elevation_points")
LAND_MASK = "ne_110m_land.zip"
EARTH_KM = 6371.0088
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


def sample_grid(spacing=5.0, pattern="staggered"):
    """Periodic longitude rows; alternate rows shift by half a cell."""
    if not math.isfinite(spacing) or not 1 <= spacing <= 15:
        raise ValueError("physical circle spacing must be within [1, 15] degrees")
    if pattern not in {"staggered", "rectangular"}:
        raise ValueError("physical grid must be staggered or rectangular")
    rows, columns = math.ceil(180 / spacing), math.ceil(360 / spacing)
    dy, dx = 180 / rows, 360 / columns
    longitude, latitude = np.meshgrid(-180 + (np.arange(columns)+.5)*dx,
                                     -90 + (np.arange(rows)+.5)*dy)
    if pattern == "staggered":
        longitude[1::2] += dx/2
    return np.column_stack((((longitude.ravel()+180) % 360)-180, latitude.ravel())), dx, dy


def grid_radius(lat, dx, dy):
    # Spherical corner distances are conservative for the staggered covering too.
    # Keep that margin at high latitudes and near irregular polygon boundaries.
    return float(np.max(distance_km(lat, 0, [(x, np.clip(lat+y, -90, 90))
                                          for x in (-dx/2, dx/2) for y in (-dy/2, dy/2)])))


def read_candidates(path, theme, *, spacing=5.0, pattern="staggered", geometries=None):
    import shapefile
    from shapely import contains_xy, make_valid
    from shapely.geometry import shape

    name = "ne_10m_" + theme
    candidates, stats = [], Counter()
    grid, dx, dy = sample_grid(spacing, pattern)
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
                if geometries is not None and kind == "ocean":
                    geometries[identity] = geometry.union(geometries[identity]) if identity in geometries else geometry
                interior = geometry.representative_point()
                extent = float(np.max(distance_km(interior.y, interior.x, item.shape.points)))
                mask = contains_xy(geometry, grid[:, 0], grid[:, 1])
                support = [("anchor", interior.x, interior.y,
                            min(extent, grid_radius(interior.y, dx, dy)),
                            "polygon_sample_circle")]
                for index in np.flatnonzero(mask):
                    lon, lat = grid[index]
                    radius = min(extent, grid_radius(lat, dx, dy))
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


def unit_vectors(coordinates):
    """Convert (longitude, latitude) pairs to unit vectors."""
    lon, lat = np.deg2rad(np.asarray(coordinates, dtype=float)).T
    return np.column_stack((np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon), np.sin(lat)))


def circle_ring(lon, lat, radius, count=72):
    lon_r, lat_r = np.deg2rad([lon, lat])
    center = unit_vectors([[lon, lat]])[0]
    east = np.array([-np.sin(lon_r), np.cos(lon_r), 0])
    north = np.array([-np.sin(lat_r)*np.cos(lon_r), -np.sin(lat_r)*np.sin(lon_r), np.cos(lat_r)])
    angles = np.arange(count) * 2*np.pi/count
    tangent = np.cos(angles)[:, None]*north + np.sin(angles)[:, None]*east
    ring = center*np.cos(radius/EARTH_KM) + tangent*np.sin(radius/EARTH_KM)
    return np.column_stack((np.rad2deg(np.arctan2(ring[:, 1], ring[:, 0])),
                            np.rad2deg(np.arctan2(ring[:, 2], np.hypot(ring[:, 0], ring[:, 1])))))


def read_land_mask(path):
    """Densify a small coastline snapshot to <=50 km between samples."""
    import shapefile
    from shapely import make_valid
    from shapely.geometry import shape
    from shapely.ops import unary_union

    points, geometries = [], []
    with ZipFile(path) as archive:
        reader = shapefile.Reader(**{ext: BytesIO(archive.read("ne_110m_land."+ext))
                                     for ext in ("shp", "shx", "dbf")})
        for item in reader.iterShapes():
            geometries.append(make_valid(shape(item.__geo_interface__)))
            boundaries = list(item.parts) + [len(item.points)]
            for start, end in zip(boundaries, boundaries[1:]):
                ring = np.asarray(item.points[start:end])[:, :2]
                for a, b in zip(ring, np.roll(ring, -1, axis=0)):
                    # Short wrapped longitude interpolation avoids a dateline detour.
                    delta = np.array([((b[0]-a[0]+180) % 360)-180, b[1]-a[1]])
                    # Parallel/meridian path bound is conservative, including near poles.
                    bound = EARTH_KM * np.deg2rad(abs(delta[0])+abs(delta[1]))
                    steps = max(1, math.ceil(bound/50))
                    samples = a + np.arange(steps)[:, None]/steps * delta
                    samples[:, 0] = ((samples[:, 0]+180) % 360)-180
                    points.extend(samples)
    return unary_union(geometries), np.asarray(points)


def coarsen_oceans(candidates, geometries, land, coast_points, *, detail=(), maximum=1200.0):
    """Replace remote same-feature disks only when a larger disk contains them.

    Coast and other-feature clearances constrain growth. Original modeled coverage
    is preserved by a spherical disk-containment check; polygon/coast safeguards
    remain approximations, as are the source label polygons themselves.
    """
    from scipy.spatial import cKDTree
    from shapely import contains_xy

    if not math.isfinite(maximum) or not 0 < maximum <= 3000:
        raise ValueError("maximum ocean radius must be in (0, 3000] km")
    groups, output = defaultdict(list), []
    for c in candidates:
        if c.record["feature_type"] == "ocean":
            groups[c.record["feature_id"]].append(c)
        else:
            output.append(c)
    other = list(detail) + output
    coast_tree = cKDTree(unit_vectors(coast_points))
    detail_tree = cKDTree(unit_vectors([[c.record["lon"], c.record["lat"]] for c in other])) if other else None
    detail_radius = max((c.record.get("radius_km", 40.0) for c in other), default=0.0)
    stats = {"input_ocean_circles": sum(map(len, groups.values())), "merged_groups": 0,
             "maximum_radius_km": maximum, "coast_sample_spacing_max_km": 50,
             "coast_margin_km": 75, "detail_margin_km": 50,
             "containment_margin_km": .2}
    for identity, group in sorted(groups.items()):
        group.sort(key=lambda c: c.source_id)
        coordinates = np.array([[c.record["lon"], c.record["lat"]] for c in group])
        vectors = unit_vectors(coordinates)
        radii = np.array([c.record["radius_km"] for c in group])
        chord = coast_tree.query(vectors)[0]
        clearance = 2*EARTH_KM*np.arcsin(np.minimum(1, chord/2))-75  # 25 sampling + 50 margin
        if detail_tree is not None:
            chord = detail_tree.query(vectors)[0]
            clearance = np.minimum(clearance, 2*EARTH_KM*np.arcsin(np.minimum(1, chord/2))-detail_radius-50)
        limits = np.minimum(maximum, clearance*.9)
        limits[contains_xy(land, coordinates[:, 0], coordinates[:, 1])] = 0
        remaining = np.ones(len(group), dtype=bool)
        for index in sorted(range(len(group)), key=lambda i: (-limits[i], group[i].source_id)):
            if not remaining[index]:
                continue
            lon, lat = coordinates[index]
            limit = limits[index]
            if limit <= radii[index] or identity not in geometries:
                continue
            # Check several concentric rings to avoid swallowing polygon holes or narrow land.
            while limit > radii[index]:
                probes = np.vstack([circle_ring(lon, lat, limit*f) for f in (.25, .5, .75, 1)])
                if np.all(contains_xy(geometries[identity], probes[:, 0], probes[:, 1])):
                    break
                limit *= .8
            distances = distance_km(lat, lon, coordinates)
            contained = remaining & (distances + radii + .2 <= limit)
            if np.count_nonzero(contained) < 2 or not contained[index]:
                continue
            # Use only the radius needed to contain the selected original circles.
            radius = math.ceil((float(np.max((distances+radii)[contained]))+.2)*1000)/1000
            record = dict(group[index].record, radius_km=radius,
                          radius_method="remote_ocean_circle_merge")
            output.append(Candidate(record, group[index].score))
            remaining[contained] = False
            stats["merged_groups"] += 1
        output.extend(c for i, c in enumerate(group) if remaining[i])
    stats["output_ocean_circles"] = sum(c.record["feature_type"] == "ocean" for c in output)
    stats["removed_ocean_circles"] = stats["input_ocean_circles"]-stats["output_ocean_circles"]
    return sorted(output, key=lambda c: c.source_id), stats


def load_candidates(cache, *, offline=False, refresh=False, locked=(), spacing=5.0,
                    pattern="staggered", coarsening=None, ocean_radius_max=1200.0, detail=()):
    sample_grid(spacing, pattern)  # Validate before downloading.
    if not math.isfinite(ocean_radius_max) or not 0 < ocean_radius_max <= 3000:
        raise ValueError("maximum ocean radius must be in (0, 3000] km")
    expected = {source["name"]: source for source in locked}
    names = ["ne_10m_" + theme + ".zip" for theme in THEMES]
    if locked and set(expected) not in (set(names), set(names + [LAND_MASK])):
        raise ValueError("Natural Earth source lock has an unexpected theme list")
    if coarsening is None:
        coarsening = not locked or LAND_MASK in expected
    if coarsening and locked and LAND_MASK not in expected:
        raise ValueError("ocean coarsening requires a land-mask source pinned in the lock; build without --lock to add it")
    candidates, sources, reports, geometries = [], [], {}, {}
    for theme, name in zip(THEMES, names):
        print("Physical source:", name, flush=True)
        path, info = fetch_source(name, cache, offline=offline, refresh=refresh,
                                  locked=expected.get(name), base_url=BASE_URL, license_id="Public-Domain")
        selected, report = read_candidates(path, theme, spacing=spacing, pattern=pattern, geometries=geometries)
        candidates.extend(selected)
        sources.append(info)
        reports[theme] = report
    if coarsening or LAND_MASK in expected:
        path, info = fetch_source(LAND_MASK, cache, offline=offline, refresh=refresh,
                                  locked=expected.get(LAND_MASK),
                                  base_url="https://naturalearth.s3.amazonaws.com/110m_physical/",
                                  license_id="Public-Domain")
        sources.append(info)
        if coarsening:
            land, coast = read_land_mask(path)
            candidates, stats = coarsen_oceans(candidates, geometries, land, coast,
                                               detail=detail, maximum=ocean_radius_max)
            reports["ocean_coarsening"] = stats
    reports["grid"] = {"pattern": pattern, "spacing_degrees": spacing,
                       "ocean_coarsening": coarsening, "maximum_ocean_radius_km": ocean_radius_max}
    return candidates, sources, reports
