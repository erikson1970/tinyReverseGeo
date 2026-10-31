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


def test_physical_deployer_pins_sources_and_rebuilds(tmp_path, monkeypatch):
    from tools.deploy import main
    layers = {}
    for theme, rows, point in [(THEMES[0], [("Desert", "Desert", 1)], False),
                               (THEMES[1], [("ocean", "Ocean", 2)], False),
                               (THEMES[2], [("mountain", "Peak", 3)], True)]:
        path = tmp_path / ("ne_10m_" + theme + ".zip")
        make_layer(path, theme, rows, point=point)
        layers[path.name] = path

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
            "--point-radii", "--physical-fraction", "0.5", "--output", str(output)]
    archive, manifest = main(args)
    first = archive.read_bytes()
    db = Gazetteer.from_file(output / "dataset.json.gz")
    assert len(db.records) == 12
    assert db.feature_count < len(db.records)
    assert any(r.feature_type == "ocean" for r in db.records)
    assert all(r.radius_km == 0 for r in db.records if r.feature_type == "town")
    assert all(r.country_code is None for r in db.records if r.feature_type != "town")
    lock = json.loads((output / "source-lock.json").read_text())
    assert len(lock["physical_sources"]) == 3
    main([arg for arg in args if arg != "--natural-earth"] + ["--lock", str(output / "source-lock.json")])
    assert archive.read_bytes() == first
    assert manifest["feature_count"] == db.feature_count
