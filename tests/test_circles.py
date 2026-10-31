import math

import numpy as np
import pytest

from tinygeo import Gazetteer, Reference
from tinygeo.lookup import EARTH_RADIUS_KM


def point(identity, lon, *, radius=0, feature_id=None, name=None, kind="town"):
    return Reference(identity, name or identity, 0, lon, kind,
                     radius_km=radius, feature_id=feature_id)


def test_far_center_can_have_closest_coverage():
    db = Gazetteer([point("Nearby point", .1), point("Broad area", 5, radius=600, kind="desert")])
    result = db.locate(0, 0, k=2)
    assert result["nearest"]["name"] == "Broad area"
    assert result["nearest"]["distance_km"] > 500
    assert result["nearest"]["distance_to_area_km"] == 0
    assert result["nearest"]["within_radius"] is True
    assert result["confidence"] == "estimated_coverage"
    assert db.locate(0, 0, distance_mode="center")["nearest"]["name"] == "Nearby point"
    assert result["nearest_settlement"]["name"] == "Nearby point"
    assert result["nearest_region"]["name"] == "Broad area"


def test_overlaps_tie_by_center_and_keep_both_features():
    db = Gazetteer([point("Far center", 2, radius=1000), point("Near center", 1, radius=200)])
    result = db.locate(0, 0, k=2)
    assert [r["name"] for r in result["neighbors"]] == ["Near center", "Far center"]
    assert all(r["within_radius"] for r in result["neighbors"])


def test_unique_feature_search_does_not_underfill():
    circles = [point(f"ocean:{i}", i / 100, radius=500, name="Ocean", feature_id="ocean", kind="ocean")
               for i in range(50)]
    db = Gazetteer(circles + [point("A", 6), point("B", 7)])
    for mode in ("area", "center"):
        result = db.locate(0, 0, k=100, distance_mode=mode)
        assert len(result["neighbors"]) == 3
        assert len({r["feature_id"] for r in result["neighbors"]}) == 3
        assert len(result["nearby_regions"]) == 1
        assert db.feature_count == 3
    assert db.locate(0, 0, feature_types="town", k=3)["nearest"]["name"] == "A"


def test_same_name_different_features_are_not_merged():
    db = Gazetteer([point("a", 1, name="Springfield"), point("b", 2, name="Springfield")])
    assert len(db.locate(0, 0, k=2)["neighbors"]) == 2


def test_circle_edge_and_antimeridian():
    radius = EARTH_RADIUS_KM * math.radians(1)
    db = Gazetteer([Reference("seam", "Seam", 0, 179.5, "sea", radius_km=radius)])
    assert db.locate(0, -179.8)["nearest"]["distance_to_area_km"] == 0
    outside = db.locate(0, -179)["nearest"]
    assert outside["distance_to_area_km"] == pytest.approx(radius / 2)
    assert not outside["within_radius"]


@pytest.mark.parametrize("radius", [-1, float("nan"), float("inf"), 30000, True, "10", None])
def test_invalid_radius(radius):
    with pytest.raises(ValueError, match="radius_km"):
        Gazetteer([point("bad", 0, radius=radius)])


def test_conflicting_feature_metadata_rejected():
    with pytest.raises(ValueError, match="sharing a feature_id"):
        Gazetteer([point("a", 0, feature_id="same", name="One"),
                   point("b", 1, feature_id="same", name="Two")])
    with pytest.raises(ValueError, match="feature_id"):
        Gazetteer([point("bad", 0, feature_id="")])


def test_circle_batches_against_independent_reference(monkeypatch):
    monkeypatch.setattr("tinygeo.lookup._MAX_SCAN_PAIRS", 100)
    rng = np.random.default_rng(12)
    records = []
    for i in range(90):
        feature = f"feature:{i // 3}"
        records.append(Reference(str(i), feature, float(rng.uniform(-85, 85)),
                                 float(rng.uniform(-180, 180)), "physical_region",
                                 radius_km=float(rng.uniform(0, 3000)), feature_id=feature))
    db = Gazetteer(records)
    queries = rng.uniform([-80, -180], [80, 180], (7, 2))
    for mode in ("area", "center"):
        results = db.locate_many(queries, k=5, distance_mode=mode)
        for query, result in zip(queries, results):
            qlat, qlon = np.deg2rad(query)
            reference = []
            for record in records:
                lat, lon = np.deg2rad([record.lat, record.lon])
                h = np.sin((lat-qlat)/2)**2 + np.cos(lat)*np.cos(qlat)*np.sin((lon-qlon)/2)**2
                center = 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1, max(0, h))))
                score = max(0, center-record.radius_km) if mode == "area" else center
                reference.append((score, center, record.source_id, record.feature_id))
            expected, seen = [], set()
            for _, _, source_id, feature_id in sorted(reference):
                if feature_id not in seen:
                    seen.add(feature_id)
                    expected.append(source_id)
            assert [r["source_id"] for r in result["neighbors"]] == expected[:5]
            assert result == db.locate(*query, k=5, distance_mode=mode)


def test_invalid_distance_mode():
    with pytest.raises(ValueError, match="distance_mode"):
        Gazetteer([point("a", 0)]).locate(0, 0, distance_mode="rhumb")
