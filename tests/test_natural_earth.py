from io import BytesIO
from hashlib import sha256
import json
from zipfile import ZipFile

import pytest

shapefile = pytest.importorskip("shapefile")
pytest.importorskip("shapely")
pytest.importorskip("scipy")

from tinygeo import Gazetteer, Reference
from tools.natural_earth import THEMES, read_candidates


def make_layer(path, theme, rows, point=False):
    streams = {extension: BytesIO() for extension in ("shp", "shx", "dbf")}
    writer = shapefile.Writer(**streams, shapeType=shapefile.POINT if point else shapefile.POLYGON)
    for name, kind in [("featurecla", "C"), ("name", "C"), ("name_en", "C"), ("ne_id", "N"), ("scalerank", "N")]:
        writer.field(name, kind, size=80 if kind == "C" else 16)
    for category, name, identity in rows:
        if point:
            writer.point(12, 20)
        else:
            writer.poly([[(-10, -5), (-10, 5), (10, 5), (10, -5), (-10, -5)]])
        writer.record(category, name, name, identity, 0)
    writer.close()
    with ZipFile(path, "w") as archive:
        for extension, stream in streams.items():
            archive.writestr("ne_10m_" + theme + "." + extension, stream.getvalue())


def test_physical_circles_group_and_exclude_country_like_islands(tmp_path):
    path = tmp_path / "regions.zip"
    make_layer(path, THEMES[0], [("Desert", "Test Desert", 1), ("Island", "Iceland", 2),
                                ("Continent", "Europe", 3)])
    candidates, stats = read_candidates(path, THEMES[0])
    assert stats["included_features"] == 1
    assert stats["excluded_Island"] == 1
    assert len(candidates) > 1
    assert len({c.record["feature_id"] for c in candidates}) == 1
    assert len({c.source_id for c in candidates}) == len(candidates)
    assert all(c.record["country_code"] is None and c.record["region"] is None for c in candidates)
    db = Gazetteer([Reference(**c.record) for c in candidates])
    result = db.locate(0, 0, k=10)
    assert len(result["neighbors"]) == 1
    assert result["nearest"]["name"] == "Test Desert"
    assert result["nearest"]["distance_to_area_km"] == 0


def test_marine_name_preserves_north_south_and_elevation_radius(tmp_path):
    path = tmp_path / "marine.zip"
    make_layer(path, THEMES[1], [("ocean", "North Atlantic Ocean", 10)])
    candidates, _ = read_candidates(path, THEMES[1])
    assert all(c.record["name"] == "North Atlantic Ocean" for c in candidates)
    point_path = tmp_path / "elevation.zip"
    make_layer(point_path, THEMES[2], [("mountain", "Peak", 11)], point=True)
    peaks, _ = read_candidates(point_path, THEMES[2])
    assert len(peaks) == 1
    assert peaks[0].record["radius_km"] == 5
    assert peaks[0].record["feature_type"] == "mountain"


def test_repeated_source_feature_parts_have_distinct_record_ids(tmp_path):
    path = tmp_path / "parts.zip"
    make_layer(path, THEMES[0], [("Desert", "Desert", 1), ("Desert", "Desert", 1)])
    candidates, _ = read_candidates(path, THEMES[0])
    assert len({c.source_id for c in candidates}) == len(candidates)
    assert len({(c.record['lat'],c.record['lon']) for c in candidates}) == len(candidates)
    db = Gazetteer([Reference(**c.record) for c in candidates])
    assert db.feature_count == 1


@pytest.mark.parametrize("coarsening", [False, True])
def test_physical_deployer_pins_sources_and_rebuilds(tmp_path, monkeypatch, coarsening):
    from tools.deploy import main
    layers = {}
    for theme, rows, point in [(THEMES[0], [("Desert", "Desert", 1)], False),
                               (THEMES[1], [("ocean", "Ocean", 2)], False),
                               (THEMES[2], [("mountain", "Peak", 3)], True)]:
        path = tmp_path / ("ne_10m_" + theme + ".zip")
        make_layer(path, theme, rows, point=point)
        layers[path.name] = path
    # The mask has no feature attributes; reuse just the test polygon geometry.
    mask = tmp_path / "ne_110m_land.zip"
    with ZipFile(layers["ne_10m_"+THEMES[0]+".zip"]) as source, ZipFile(mask, "w") as dest:
        for name in source.namelist():
            dest.writestr("ne_110m_land."+name.rsplit(".", 1)[1], source.read(name))
    layers[mask.name] = mask

    def source(name, cache, **kwargs):
        path = layers[name]
        info = {"name": name, "url": "https://example.test/" + name,
                "sha256": sha256(path.read_bytes()).hexdigest(), "license": "Public-Domain"}
        if kwargs.get("locked"):
            assert kwargs["locked"] == info
        return path, info

    monkeypatch.setattr("tools.natural_earth.fetch_source", source)
    cities = tmp_path / "cities.txt"
    cities.write_text("".join("\t".join([str(i), f"City {i}", "", "", str(30+i), str(20+i),
                       "P", "PPL", "XX", "", "01", "", "", "", "1000", "", "", "", "2026-10-31"]) + "\n"
                                  for i in range(20)))
    output = tmp_path / "build"
    args = ["--input", str(cities), "--points", "12", "--natural-earth", "--offline",
            "--point-radii", "--ocean-coarsening" if coarsening else "--no-ocean-coarsening",
            "--physical-fraction", "0.5", "--output", str(output)]
    archive, manifest = main(args)
    first = archive.read_bytes()
    db = Gazetteer.from_file(output / "dataset.json.gz")
    assert len(db.records) == 12
    assert db.feature_count < len(db.records)
    assert any(r.feature_type == "ocean" for r in db.records)
    assert all(r.radius_km == 0 for r in db.records if r.feature_type == "town")
    assert all(r.country_code is None for r in db.records if r.feature_type != "town")
    lock = json.loads((output / "source-lock.json").read_text())
    assert len(lock["physical_sources"]) == (4 if coarsening else 3)
    main([arg for arg in args if arg != "--natural-earth"] + ["--lock", str(output / "source-lock.json")])
    assert archive.read_bytes() == first
    assert manifest["feature_count"] == db.feature_count


def test_staggered_grid_wraps_and_covers_sphere():
    import numpy as np
    from scipy.spatial import cKDTree
    from tools.natural_earth import sample_grid, grid_radius, unit_vectors

    grid, dx, dy = sample_grid(5)
    assert grid.shape == (36*72, 2)
    assert len(np.unique(grid, axis=0)) == len(grid)
    assert grid[72, 0]-grid[0, 0] == dx/2
    assert grid[144, 0] == grid[0, 0]
    assert np.all((-180 <= grid[:, 0]) & (grid[:, 0] < 180))
    # Independent area-uniform samples, plus the seam and polar caps.
    rng = np.random.default_rng(64)
    points = np.column_stack((rng.uniform(-180, 180, 10000),
                              np.rad2deg(np.arcsin(rng.uniform(-1, 1, 10000)))))
    points = np.vstack((points, [[180, 0], [-180, 0], [90, 90], [-90, -90]]))
    chord, index = cKDTree(unit_vectors(grid)).query(unit_vectors(points))
    distance = 2*6371.0088*np.arcsin(np.minimum(1, chord/2))
    radii = np.array([grid_radius(lat, dx, dy) for _, lat in grid])
    assert np.all(distance <= radii[index]+1e-6)
    irregular, dx, dy = sample_grid(7)
    assert len(np.unique(irregular, axis=0)) == len(irregular)
    assert dx <= 7 and dy <= 7


def test_ocean_merges_preserve_original_disks_and_local_detail():
    import numpy as np
    from shapely.geometry import box
    from tools.geodb import Candidate
    from tools.natural_earth import coarsen_oceans, distance_km

    def circle(identity, lon, lat, radius=180, feature="ocean"):
        return Candidate({"source_id": identity, "feature_id": feature, "name": feature,
                          "feature_type": "ocean", "lon": lon, "lat": lat,
                          "radius_km": radius}, 20)

    original = [circle(f"p{i}", lon, lat) for i, (lon, lat) in enumerate(
                [(lon, lat) for lon in range(-25, 26, 5) for lat in range(-10, 11, 5)])]
    original += [circle("other", 0, 0, feature="another_ocean")]
    geometry = {"ocean": box(-80, -50, 80, 50), "another_ocean": box(-80, -50, 80, 50)}
    land = box(65, -10, 75, 10)
    coast = np.array([[65, -10], [65, 0], [65, 10]])
    # Protect an interesting place in the west, far from the simulated land mask.
    detail = [Candidate({"source_id": "peak", "lon": -25, "lat": 0,
                         "feature_type": "mountain", "radius_km": 5}, 30)]
    merged, stats = coarsen_oceans(original, geometry, land, coast, detail=detail)
    assert stats["removed_ocean_circles"] > 0
    assert stats["merged_groups"] > 0
    assert len(merged) < len(original)
    assert any(c.source_id == "other" for c in merged)
    protected = next(c for c in merged if c.record["lon"] == -25 and c.record["lat"] == 0)
    assert protected.record["radius_km"] == 180
    assert max(c.record["radius_km"] for c in merged) <= 1200
    for before in original:
        after = [c for c in merged if c.record["feature_id"] == before.record["feature_id"]]
        gaps = distance_km(before.record["lat"], before.record["lon"],
                           [[c.record["lon"], c.record["lat"]] for c in after])
        assert any(d + before.record["radius_km"] <= c.record["radius_km"]+1e-6
                   for c, d in zip(after, gaps))
    again, second_stats = coarsen_oceans(list(reversed(original)), geometry, land, coast, detail=detail)
    assert merged == again
    assert stats == second_stats


def test_old_source_lock_cannot_silently_fetch_coastline(tmp_path):
    from tools.natural_earth import load_candidates
    locked = [{"name": "ne_10m_"+theme+".zip"} for theme in THEMES]
    with pytest.raises(ValueError, match="land-mask source pinned"):
        load_candidates(tmp_path, locked=locked, coarsening=True)
