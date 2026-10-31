import json
import math

import pytest

from tools.radii import density_config, estimate_radius


def test_population_density_assumptions_and_region_override(tmp_path):
    path = tmp_path / "density.json"
    path.write_text(json.dumps({"default": 1000, "countries": {"XX": 500},
                                "regions": {"XX/Region": 5000}, "source_note": "test assumption"}))
    config = density_config(path)
    record = {"source_id": "a", "feature_type": "town", "population": 100000,
              "country_code": "XX", "region": "Region"}
    result = estimate_radius(record, config)
    assert result["radius_km"] == round(math.sqrt(100000 / (math.pi*5000)), 3)
    assert result["radius_method"] == "population_density_assumption"
    assert "radius_km" not in record
    assert estimate_radius({**record, "region": None}, config)["radius_km"] > result["radius_km"]


def test_radius_bounds_fallback_and_point_mode():
    config = density_config()
    record = {"source_id": "a", "feature_type": "town", "population": 1}
    assert estimate_radius(record, config)["radius_km"] == .5
    assert estimate_radius({**record, "population": 1e9}, config)["radius_km"] == 40
    assert estimate_radius({**record, "population": None}, config)["radius_km"] == 1
    assert estimate_radius(record, config, point_radii=True)["radius_km"] == 0
    natural = {**record, "feature_type": "mountain"}
    assert estimate_radius(natural, config) == natural


@pytest.mark.parametrize("density", [0, -1, float("inf"), True, "unknown"])
def test_invalid_density(tmp_path, density):
    path = tmp_path / "density.json"
    path.write_text(json.dumps({"default": density}))
    with pytest.raises(ValueError, match="density|densities"):
        density_config(path)
