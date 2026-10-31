"""Optional, cached GeoNames queries through geopy for curated build-time additions."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

from tools.geodb import Candidate, canonical_json, feature_type


def resolve_places(path, cache, *, offline=False, refresh=False, locked=()):
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries or len(entries) > 50:
        raise ValueError("--places must contain a nonempty JSON list of at most 50 curated queries")
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    expected = {item["name"]: item for item in locked}
    candidates, sources = [], []
    geocode = None
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("query"), str) or not entry["query"].strip():
            raise ValueError(f"invalid curated query at index {index}")
        query = {"query": entry["query"], "country": entry.get("country")}
        if query["country"] is not None:
            country = query["country"]
            if not isinstance(country, str) or len(country) != 2 or not country.isascii() or not country.isalpha():
                raise ValueError("curated country must be a two-letter country code")
            query["country"] = country.upper()
        name = "geopy-" + sha256(canonical_json(query)).hexdigest()[:24] + ".json"
        pointer = cache / (name + ".source.json")
        info = expected.get(name)
        if info is None and locked:
            raise ValueError("curated queries differ from the source lock")
        if info is None and pointer.is_file() and not refresh:
            info = json.loads(pointer.read_text(encoding="utf-8"))
        if info is not None:
            digest = info.get("sha256", "")
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("invalid curated-source checksum")
            cached = cache / digest / name
        else:
            cached = None
        if cached is not None and cached.is_file():
            payload = cached.read_bytes()
            if sha256(payload).hexdigest() != info["sha256"]:
                raise ValueError("cached geopy response checksum mismatch")
            result = json.loads(payload)
            if any(result.get(key) != value for key, value in query.items()):
                raise ValueError("cached response does not match its curated query")
        else:
            if offline:
                raise ValueError(f"curated query {index} is not cached")
            if geocode is None:
                username = os.environ.get("GEONAMES_USERNAME")
                if not username:
                    raise ValueError("set GEONAMES_USERNAME to resolve curated places through geopy")
                from geopy.extra.rate_limiter import RateLimiter
                from geopy.geocoders import GeoNames
                geocoder = GeoNames(username=username, scheme="https", timeout=20,
                                    user_agent="TinyGeo-builder/0.1")
                geocode = RateLimiter(geocoder.geocode, min_delay_seconds=1,
                                     max_retries=0, swallow_exceptions=False)
            from geopy.exc import GeopyError
            try:
                location = geocode(query["query"], country=query["country"], exactly_one=True)
            except GeopyError as exc:
                raise ValueError(f"GeoNames query failed ({type(exc).__name__}); check account/service availability") from exc
            if location is None:
                raise ValueError(f"curated query {index} returned no result")
            result = dict(query, raw=location.raw)
            payload = canonical_json(result)
            digest = sha256(payload).hexdigest()
            if name in expected and digest != expected[name]["sha256"]:
                raise ValueError("geopy result changed; restore the locked cached response")
            cached = cache / digest / name
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(payload)
            if name not in expected:
                from importlib.metadata import version
                info = {"kind": "geopy_geonames", "name": name, "sha256": digest,
                        "url": "https://api.geonames.org/searchJSON", "license": "CC-BY-4.0",
                        "geopy_version": version("geopy"),
                        "retrieved_utc": datetime.now(timezone.utc).isoformat(), **query}
                pointer.write_bytes(canonical_json(info))
        raw = result["raw"]
        identity = str(raw.get("geonameId", ""))
        if not identity.isdecimal():
            raise ValueError(f"curated query {index} has no valid GeoNames ID")
        if "geonames_id" in entry and str(entry["geonames_id"]) != identity:
            raise ValueError(f"curated query {index} did not match its expected GeoNames ID")
        population = int(raw.get("population", 0) or 0)
        if query["country"] and raw.get("countryCode") != query["country"]:
            raise ValueError(f"curated query {index} returned a different country")
        kind = feature_type(raw.get("fcl"), raw.get("fcode"), population)
        if kind is None:
            raise ValueError(f"curated query {index} returned an unsupported feature class/code")
        if "feature_type" in entry and entry["feature_type"] != kind:
            raise ValueError(f"curated query {index} did not match its expected feature type")
        record = {"source_id": "geonames:" + identity, "name": raw["name"],
                  "lat": float(raw["lat"]), "lon": float(raw["lng"]),
                  "feature_type": kind, "country_code": raw.get("countryCode") or None,
                  "region": raw.get("adminName1") or None, "population": population or None}
        candidates.append(Candidate(record, 40, raw.get("fcode") == "PPLC"))
        sources.append(info)
    if locked and {source["name"] for source in sources} != set(expected):
        raise ValueError("curated queries differ from the source lock")
    return candidates, sources
