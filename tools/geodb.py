"""GeoNames snapshots, streaming ingestion, and deterministic spatial selection."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import heapq
import io
import json
import math
from pathlib import Path
import shutil
import unicodedata
from urllib.request import Request, urlopen
from zipfile import ZipFile

import numpy as np

BASE_URL = "https://download.geonames.org/export/dump/"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = "Contains GeoNames data (https://www.geonames.org/), CC BY 4.0; selected and repackaged by TinyGeo."
ALGORITHM = "spatial-strata-v1"


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def file_hash(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_source(name, cache, *, offline=False, refresh=False, locked=None,
                 base_url=BASE_URL, license_id="CC-BY-4.0"):
    """Cache content-addressed snapshots and verify checksums on every reuse."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    pointer = cache / (name + ".source.json")
    info = locked
    if info is None and pointer.is_file() and not refresh:
        info = json.loads(pointer.read_text(encoding="utf-8"))
    if info is not None:
        if info.get("name") != name or info.get("url") != base_url + name:
            raise ValueError(f"invalid source metadata for {name}")
        digest = info.get("sha256", "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"invalid source checksum for {name}")
        path = cache / digest / name
        if path.is_file():
            if file_hash(path) != digest:
                raise ValueError(f"cached source checksum mismatch: {name}")
            return path, info
    if offline:
        raise ValueError(f"source not cached: {name}; run once without --offline")
    request = Request(base_url + name, headers={"User-Agent": "TinyGeo-builder/0.1"})
    temporary = cache / (name + ".download")
    try:
        with urlopen(request, timeout=60) as response, temporary.open("wb") as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
            modified = response.headers.get("Last-Modified")
        digest = file_hash(temporary)
        if locked is not None and digest != locked["sha256"]:
            raise ValueError(f"upstream {name} changed; restore the locked cached snapshot")
        path = cache / digest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    if locked is None:
        info = {"name": name, "url": base_url + name, "sha256": digest,
                "bytes": path.stat().st_size, "last_modified": modified,
                "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                "license": license_id, "kind": "download"}
        pointer.write_bytes(canonical_json(info))
    return path, info


def local_source(path):
    path = Path(path)
    return path, {"name": path.name, "sha256": file_hash(path),
                  "bytes": path.stat().st_size, "kind": "local_geonames",
                  "license": "CC-BY-4.0"}


def text_lines(path):
    """Stream the primary export member without extracting archive paths."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            expected = path.stem + ".txt"
            names = [n for n in archive.namelist() if n == expected]
            if not names:
                names = [n for n in archive.namelist() if n.endswith(".txt")
                         and not n.lower().endswith("readme.txt")]
            if len(names) != 1:
                raise ValueError(f"cannot identify primary GeoNames member in {path.name}")
            with archive.open(names[0]) as stream:
                yield from io.TextIOWrapper(stream, encoding="utf-8-sig")
    else:
        with path.open(encoding="utf-8-sig") as stream:
            yield from stream


def admin_regions(path):
    if path is None:
        return {}
    return {fields[0]: fields[1] for line in text_lines(path)
            if (fields := line.rstrip("\r\n").split("\t")) and len(fields) >= 2
            and not fields[0].startswith("#")}


def feature_type(feature_class, code, population):
    if feature_class == "P" and code in {"PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLC", "PPLG", "PPLS"}:
        return "city" if population >= 50_000 or code == "PPLC" else (
            "remote_settlement" if 0 < population < 500 else "town")
    if feature_class == "T" and code == "MTS":
        return "mountain_range"
    if feature_class == "T" and code == "DSRT":
        return "desert"
    if feature_class == "H" and code in {"OCN", "SEA"}:
        return "ocean" if code == "OCN" else "sea"
    if feature_class == "T" and code in {"MT", "PK", "PKS", "VLC"}:
        return "mountain"
    if feature_class == "T" and code in {"ISL", "ISLS", "ISLT", "ATOL"}:
        return "island"
    if feature_class == "H" and code in {"LK", "LKS", "LKN", "RSV"}:
        return "lake"
    if feature_class == "S" and code in {"MONU", "CSTL", "RUIN", "MUS", "TMB", "ARCH", "CH", "MSQE", "TMPL"}:
        return "landmark"
    if (feature_class == "T" and code in {"CAPE", "DSRT", "PLAT", "PEN", "VAL"}
            or feature_class == "L" and code in {"PRK", "RESN", "HSTS"}):
        return "landmark" if code == "HSTS" else "other"
    return None


@dataclass
class Candidate:
    record: dict
    score: float
    capital: bool = False

    @property
    def source_id(self):
        return self.record["source_id"]

    @property
    def group(self):
        kind = self.record["feature_type"]
        return "settlement" if kind in {"city", "town", "remote_settlement"} else (
            "landmark" if kind == "landmark" else "natural")


def parse_record(fields, regions):
    if len(fields) != 19:
        raise ValueError("GeoNames rows must have 19 tab-separated fields")
    identity, name = fields[0], fields[1].strip()
    if not identity.isdecimal() or not name:
        raise ValueError("invalid GeoNames ID or name")
    population = max(0, int(fields[14] or 0))
    kind = feature_type(fields[6], fields[7], population)
    if kind is None:
        return None
    lat, lon = float(fields[4]), float(fields[5])
    if not math.isfinite(lat) or not math.isfinite(lon) or abs(lat) > 90 or abs(lon) > 180:
        raise ValueError("invalid GeoNames coordinates")
    capital = fields[7] == "PPLC"
    score = math.log1p(population) + (20 if capital else 4 if fields[7] == "PPLA" else 0)
    if kind == "mountain":
        score += math.log1p(max(0, int(fields[15] or 0))) / 4
    record = {"source_id": "geonames:" + identity, "name": name, "lat": lat, "lon": lon,
              "feature_type": kind, "country_code": fields[8] or None,
              "region": regions.get(fields[8] + "." + fields[10]), "population": population or None}
    return Candidate(record, score, capital)


def rank(candidate):
    # Lexical source ID gives a stable tie-break independent of input row order.
    return (-candidate.score, candidate.source_id)


def grid_cell(record, bands):
    row = min(bands - 1, int((math.sin(math.radians(record["lat"])) + 1) * bands / 2))
    col = int(((record["lon"] + 180) % 360) / 360 * (2 * bands))
    return row, col


def collect_candidates(paths, count, regions=None):
    """Bound large-export memory with per-cell/type candidates plus a top-N pool."""
    regions = regions or {}
    bands = max(1, int(math.sqrt(count / 2)))
    buckets = defaultdict(dict)
    top, top_ids = [], set()
    stats = Counter()
    for path in paths:
        for number, line in enumerate(text_lines(path), 1):
            if not line.strip() or line.startswith("#"):
                continue
            stats["source_rows"] += 1
            try:
                candidate = parse_record(line.rstrip("\r\n").split("\t"), regions)
            except ValueError as exc:
                raise ValueError(f"{Path(path).name}:{number}: {exc}") from exc
            if candidate is None:
                continue
            stats["eligible_rows"] += 1
            key = (candidate.group, *grid_cell(candidate.record, bands))
            bucket = buckets[key]
            bucket[candidate.source_id] = candidate
            if len(bucket) > 4:
                worst = max(bucket.values(), key=rank)
                del bucket[worst.source_id]
            if candidate.source_id not in top_ids:
                # A min heap holds the globally highest salience candidates.
                entry = (candidate.score, candidate.source_id, candidate)
                if len(top) < count:
                    heapq.heappush(top, entry)
                    top_ids.add(candidate.source_id)
                elif entry[:2] > top[0][:2]:
                    removed = heapq.heapreplace(top, entry)
                    top_ids.remove(removed[1])
                    top_ids.add(candidate.source_id)
    combined = {c.source_id: c for bucket in buckets.values() for c in bucket.values()}
    combined.update({c.source_id: c for _, _, c in top})
    # Merge coincident aliases within a feature type, preserving cross-type context.
    unique = {}
    for c in sorted(combined.values(), key=rank):
        r = c.record
        alias_key = (unicodedata.normalize("NFKC", r["name"]).casefold(),
                     round(r["lat"], 4), round(r["lon"], 4), r["feature_type"])
        unique.setdefault(alias_key, c)
    stats["candidate_pool"] = len(unique)
    stats["coincident_aliases_removed"] = len(combined) - len(unique)
    return sorted(unique.values(), key=lambda c: c.source_id), dict(stats)


def vectors(records):
    lat, lon = np.deg2rad([[r["lat"], r["lon"]] for r in records]).T
    return np.column_stack((np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)))


def select_candidates(candidates, count, *, pinned=()):
    """Seed salience, then fill equal-area strata with capped coverage preference."""
    from scipy.spatial import cKDTree  # Builder-only; never copied into deployments.

    if count < 1 or len(candidates) < count:
        raise ValueError(f"requested {count} points but only {len(candidates)} distinct candidates are available")
    by_id = {c.source_id: c for c in candidates}
    if len(by_id) < count:
        raise ValueError(f"requested {count} points but only {len(by_id)} distinct source IDs are available")
    candidates = list(by_id.values())
    pins = sorted(set(pinned))
    if len(pins) > count:
        raise ValueError("pinned places exceed the requested database size")
    if any(identity not in by_id for identity in pins):
        raise ValueError("a pinned place is absent from the candidate pool")
    chosen = {identity: by_id[identity] for identity in pins}
    bands = max(1, int(math.sqrt(count / 2)))
    occupancy = Counter(grid_cell(c.record, bands) for c in chosen.values())

    def add(candidate):
        chosen[candidate.source_id] = candidate
        occupancy[grid_cell(candidate.record, bands)] += 1

    settlements = sorted((c for c in candidates if c.group == "settlement"), key=rank)
    seeded_settlements = sum(c.group == "settlement" for c in chosen.values())
    for c in settlements:
        if seeded_settlements >= count // 5 or len(chosen) >= count:
            break
        if c.source_id not in chosen:
            add(c)
            seeded_settlements += 1

    def fill(pool, amount):
        while amount > 0:
            heads = {}
            for c in sorted(pool, key=rank):
                if c.source_id not in chosen:
                    heads.setdefault(grid_cell(c.record, bands), c)
            if not heads:
                return
            options = list(heads.values())
            if chosen:
                tree = cKDTree(vectors([c.record for c in chosen.values()]))
                chord, _ = tree.query(vectors([c.record for c in options]))
                distance = 2 * 6371.0088 * np.arcsin(np.clip(chord / 2, 0, 1))
            else:
                distance = np.zeros(len(options))
            # Limit the advantage of extreme remoteness to 1,000 km.
            ordered = sorted(zip(options, distance), key=lambda pair: (
                occupancy[grid_cell(pair[0].record, bands)],
                -round(min(float(pair[1]), 1000) / 1000 + min(pair[0].score, 40) / 400, 8),
                pair[0].source_id))
            for c, _ in ordered[:amount]:
                add(c)
                amount -= 1

    for group, fraction in (("settlement", .7), ("natural", .2), ("landmark", .1)):
        target = int(count * fraction)
        already = sum(c.group == group for c in chosen.values())
        fill([c for c in candidates if c.group == group], max(0, min(target - already, count - len(chosen))))
    fill(candidates, count - len(chosen))
    return sorted(chosen.values(), key=lambda c: c.source_id)


def coverage_report(selected, candidates, *, sample_count=2000):
    from scipy.spatial import cKDTree

    tree = cKDTree(vectors([c.record for c in selected]))
    rng = np.random.default_rng(42)
    lat = np.arcsin(rng.uniform(-1, 1, sample_count))
    lon = rng.uniform(-math.pi, math.pi, sample_count)
    global_xyz = np.column_stack((np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)))

    def summary(xyz):
        chord, _ = tree.query(xyz)
        km = 2 * 6371.0088 * np.arcsin(np.clip(chord / 2, 0, 1))
        return {"samples": len(xyz), "median_km": round(float(np.median(km)), 3),
                "p95_km": round(float(np.percentile(km, 95)), 3), "max_km": round(float(np.max(km)), 3)}

    settlements = [c.record for c in candidates if c.group == "settlement"]
    result = {"global_area_uniform": summary(global_xyz)}
    if settlements:
        ids = rng.choice(len(settlements), min(sample_count, len(settlements)), replace=False)
        result["settlement_candidate_sample"] = summary(vectors([settlements[int(i)] for i in ids]))
    return result
