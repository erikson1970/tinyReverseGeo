import json
from types import SimpleNamespace

import pytest

pytest.importorskip("geopy")

from tools.geopy_places import resolve_places


def test_geopy_resolution_pinned_cache_and_identity(tmp_path, monkeypatch):
    calls = []
    raw = {"geonameId": 123, "name": "Example Monument", "lat": "20", "lng": "30",
           "countryCode": "FR", "adminName1": "Example Region", "fcl": "S", "fcode": "MONU"}

    class FakeGeoNames:
        def __init__(self, **kwargs):
            assert kwargs["scheme"] == "https"
            assert kwargs["username"] == "test-account"

        def geocode(self, query, **kwargs):
            calls.append((query, kwargs))
            return SimpleNamespace(raw=raw)

    monkeypatch.setenv("GEONAMES_USERNAME", "test-account")
    monkeypatch.setattr("geopy.geocoders.GeoNames", FakeGeoNames)
    monkeypatch.setattr("geopy.extra.rate_limiter.RateLimiter", lambda function, **kwargs: function)
    queries = tmp_path / "places.json"
    entries = [{"query": "Example Monument", "country": "fr", "geonames_id": 123,
                "feature_type": "landmark"}]
    queries.write_text(json.dumps(entries))
    cache = tmp_path / "cache"
    candidates, sources = resolve_places(queries, cache)
    assert len(calls) == 1 and calls[0][1]["country"] == "FR"
    assert candidates[0].source_id == "geonames:123"
    assert candidates[0].record["feature_type"] == "landmark"
    assert "test-account" not in json.dumps(sources)
    monkeypatch.delenv("GEONAMES_USERNAME")
    again, _ = resolve_places(queries, cache, offline=True, locked=sources)
    assert again == candidates and len(calls) == 1
    entries[0]["geonames_id"] = 999
    queries.write_text(json.dumps(entries))
    with pytest.raises(ValueError, match="expected GeoNames ID"):
        resolve_places(queries, cache, offline=True)
    cached = cache / sources[0]["sha256"] / sources[0]["name"]
    cached.write_text("corrupted")
    with pytest.raises(ValueError, match="checksum mismatch"):
        resolve_places(queries, cache, offline=True)


def test_missing_geopy_cache_and_limits(tmp_path):
    queries = tmp_path / "places.json"
    queries.write_text(json.dumps([{"query": "Example"}]))
    with pytest.raises(ValueError, match="not cached"):
        resolve_places(queries, tmp_path / "cache", offline=True)
    queries.write_text(json.dumps([{"query": "Example"}] * 51))
    with pytest.raises(ValueError, match="at most 50"):
        resolve_places(queries, tmp_path / "cache", offline=True)
