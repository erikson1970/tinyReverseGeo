import math
import socket

import numpy as np
import pytest

from tinygeo import Gazetteer, Reference, locate, locate_many
from tinygeo.lookup import EARTH_RADIUS_KM
from tinygeo.lookup import _default_gazetteer


def ref(name, lat, lon, feature_type="city", **kwargs):
    return Reference(name, name, lat, lon, feature_type, **kwargs)


def test_identical_and_altitude_ignored():
    db = Gazetteer([ref("A", 12, 34)])
    result = db.locate(12, 34, altitude=10000)
    assert result["nearest"]["distance_km"] == pytest.approx(0, abs=1e-9)
    assert result["nearest"]["bearing_deg"] is None
    assert result["query"] == {"lat": 12, "lon": 34, "altitude": 10000}
    assert result["altitude_ignored"] is True


def test_quarter_circumference_and_bearing():
    db = Gazetteer([ref("East", 0, 90)])
    result = db.locate(0, 0)["nearest"]
    assert result["distance_km"] == pytest.approx(math.pi * EARTH_RADIUS_KM / 2)
    assert result["bearing_deg"] == pytest.approx(90)
    assert db.locate(0, 180)["nearest"]["bearing_deg"] == pytest.approx(270)


def test_antimeridian():
    db = Gazetteer([ref("Close", 0, 179.9), ref("Far", 0, -170)])
    result = db.locate(0, -179.9)["nearest"]
    assert result["name"] == "Close"
    assert result["distance_km"] == pytest.approx(EARTH_RADIUS_KM * math.radians(0.2))
    assert result["bearing_deg"] == pytest.approx(270)
    east = db.locate(0, 180)["nearest"]
    west = db.locate(0, -180)["nearest"]
    assert east["source_id"] == west["source_id"]
    assert east["distance_km"] == pytest.approx(west["distance_km"], abs=1e-9)
    assert east["bearing_deg"] == pytest.approx(west["bearing_deg"])


def test_poles_and_antipodes():
    db = Gazetteer([ref("North", 89.9, -170), ref("South", -89.9, 12)])
    for lat, expected in [(90, "North"), (-90, "South")]:
        result = db.locate(lat, 123)["nearest"]
        assert result["name"] == expected
        assert result["distance_km"] == pytest.approx(EARTH_RADIUS_KM * math.radians(0.1))
        assert result["bearing_deg"] is None
    antipode = Gazetteer([ref("Opposite", 0, 180)]).locate(0, 0)["nearest"]
    assert antipode["distance_km"] == pytest.approx(math.pi * EARTH_RADIUS_KM)
    assert antipode["bearing_deg"] is None


def test_filter_is_applied_before_query():
    db = Gazetteer([ref(str(i), 0, i / 100) for i in range(50)]
                   + [ref("Site", 0, 20, "landmark")])
    result = db.locate(0, 0, feature_types="landmark", k=100)
    assert [n["name"] for n in result["neighbors"]] == ["Site"]
    assert result["nearest_any"]["name"] == "0"
    assert result["nearest_settlement"]["name"] == "0"
    assert result["nearest_landmark"]["name"] == "Site"


def test_batch_order_shapes_and_sequences():
    positions = [[0, 90, 1], [0, 0, -10], [0, -90, 500]]
    for points in (positions, np.array(positions)):
        results = locate_many(points, k=2)
        assert len(results) == 3
        assert [r["query"]["lon"] for r in results] == [90, 0, -90]
        assert all(len(r["neighbors"]) == 2 for r in results)
        assert results == [locate(*p[:2], altitude=p[2], k=2) for p in positions]
    assert locate_many(np.empty((0, 2))) == []
    assert locate_many(np.empty((0, 3))) == []


@pytest.mark.parametrize("points", [
    [0, 0], [], [[0]], [[0, 0, 0, 0]], [[91, 0]], [[-91, 0]],
    [[0, 181]], [[0, -181]], [[np.nan, 0]], [[0, np.inf]],
    [[0, 0, np.nan]], [[0, 0], [0, 0, 1]], [["invalid", 0]],
])
def test_invalid_positions(points):
    with pytest.raises(ValueError):
        locate_many(points)


@pytest.mark.parametrize("k", [0, -1, True, 1.5])
def test_invalid_k(k):
    with pytest.raises(ValueError):
        locate(0, 0, k=k)


def test_empty_and_unknown_filters():
    result = locate(0, 0, feature_types=[])
    assert result["nearest"] is None
    assert result["neighbors"] == []
    assert result["confidence"] == "no_reference"
    assert result["nearest_any"] is not None
    with pytest.raises(ValueError, match="unknown feature"):
        locate(0, 0, feature_types="airport")


def test_missing_class_and_coincident_references():
    db = Gazetteer([ref("A", 0, 0, "island"), ref("B", 0, 0, "mountain")])
    result = db.locate(0, 0, k=10)
    assert len(result["neighbors"]) == 2
    assert result["nearest_landmark"] is None
    assert result["nearest_settlement"] is None
    assert all(r["distance_km"] == 0 for r in result["neighbors"])


def test_no_containment_claims():
    db = Gazetteer([ref("A", 0, 0, country_code="XX", region="Example")])
    result = db.locate(40, 50)
    assert result["confidence"] == "distant_reference"
    assert "country_code" not in result["query"]
    assert "XX" not in result["description"]
    assert "Example" not in result["description"]
    assert "nearest known reference point" in result["description"].lower()


def test_offline_and_explicit_demo(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", blocked)
    _default_gazetteer.cache_clear()
    result = locate(0, 0)
    assert result["synthetic"] is True
    assert result["dataset_id"] == "synthetic-demo-v1"
    assert result["description"].startswith("Synthetic demo data.")


def test_index_ranking_against_independent_haversine():
    rng = np.random.default_rng(42)
    coordinates = rng.uniform([-90, -180], [90, 180], size=(200, 2))
    db = Gazetteer([ref(str(i), *p) for i, p in enumerate(coordinates)])
    queries = rng.uniform([-90, -180], [90, 180], size=(30, 2))
    for query, result in zip(queries, db.locate_many(queries, k=5)):
        lat, lon = np.deg2rad(coordinates).T
        qlat, qlon = np.deg2rad(query)
        h = (np.sin((lat - qlat) / 2) ** 2
             + np.cos(lat) * np.cos(qlat) * np.sin((lon - qlon) / 2) ** 2)
        expected = np.argsort(h)[:5]
        assert [n["source_id"] for n in result["neighbors"]] == list(map(str, expected))


def test_invalid_gazetteer():
    with pytest.raises(ValueError):
        Gazetteer([])
    with pytest.raises(ValueError):
        Gazetteer([ref("A", 100, 0)])
    with pytest.raises(ValueError):
        Gazetteer([ref("A", 0, 0), ref("A", 0, 1)])
    with pytest.raises(ValueError):
        Gazetteer([ref("A", 0, 0, "unknown")])
