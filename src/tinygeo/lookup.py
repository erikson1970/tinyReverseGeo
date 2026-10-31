"""Spherical nearest-reference lookup with validated, vectorized inputs."""

from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib.resources import files
import json
from numbers import Integral

import numpy as np
from scipy.spatial import cKDTree

EARTH_RADIUS_KM = 6371.0088
FEATURE_TYPES = frozenset({
    "city", "town", "landmark", "mountain", "lake", "island",
    "remote_settlement", "other",
})
SETTLEMENTS = frozenset({"city", "town", "remote_settlement"})


@dataclass(frozen=True)
class Reference:
    """A named point; administrative fields describe this reference only."""

    source_id: str
    name: str
    lat: float
    lon: float
    feature_type: str
    country_code: str | None = None
    region: str | None = None


def _positions(positions):
    try:
        points = np.asarray(positions, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("positions must be a numeric N×2 or N×3 array") from exc
    if points.ndim != 2 or points.shape[1] not in (2, 3):
        raise ValueError("positions must have shape (N, 2) or (N, 3)")
    if not np.isfinite(points).all():
        raise ValueError("all coordinates, including optional altitude, must be finite")
    if (np.abs(points[:, 0]) > 90).any():
        raise ValueError("latitude must be within [-90, 90] degrees")
    if (np.abs(points[:, 1]) > 180).any():
        raise ValueError("longitude must be within [-180, 180] degrees; no wrapping is applied")
    return points


def _xyz(points):
    lat, lon = np.deg2rad(points[:, :2]).T
    return np.column_stack((np.cos(lat) * np.cos(lon),
                            np.cos(lat) * np.sin(lon), np.sin(lat)))


def _geometry(query, targets):
    """Return great-circle distance and initial query-to-reference bearing."""
    lat1, lon1 = np.deg2rad(query[:2])
    lat2, lon2 = np.deg2rad(targets).T
    delta = lon2 - lon1
    hav = (np.sin((lat2 - lat1) / 2) ** 2
           + np.cos(lat1) * np.cos(lat2) * np.sin(delta / 2) ** 2)
    distance = 2 * EARTH_RADIUS_KM * np.arctan2(
        np.sqrt(np.clip(hav, 0, 1)), np.sqrt(np.clip(1 - hav, 0, 1)))
    y = np.sin(delta) * np.cos(lat2)
    x = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(delta)
    bearing = np.mod(np.rad2deg(np.arctan2(y, x)), 360)
    # Coincident, antipodal and exact-pole bearings have no unique azimuth.
    undefined = (np.hypot(x, y) < 1e-14) | (abs(np.cos(lat1)) < 1e-14)
    return distance, [None if bad else float(b) for bad, b in zip(undefined, bearing)]


class Gazetteer:
    """Reusable spherical indexes over a collection of reference points.

    Indexes are built once for each requested feature subset. Equal-distance
    neighbor ordering is unspecified. No lookup establishes area containment.
    """

    def __init__(self, records, *, dataset_id="custom", synthetic=False):
        self.records = tuple(records)
        if not self.records:
            raise ValueError("a gazetteer must contain at least one reference")
        if any(not r.name or not r.source_id for r in self.records):
            raise ValueError("each reference needs a name and source_id")
        if len({r.source_id for r in self.records}) != len(self.records):
            raise ValueError("source_id values must be unique")
        if any(r.feature_type not in FEATURE_TYPES for r in self.records):
            raise ValueError("unsupported reference feature_type")
        self._coordinates = _positions([[r.lat, r.lon] for r in self.records])
        self._vectors = _xyz(self._coordinates)
        self._indexes = {}
        self.dataset_id = dataset_id
        self.synthetic = synthetic

    def _index(self, types):
        key = frozenset(types)
        if key not in self._indexes:
            ids = np.array([i for i, r in enumerate(self.records)
                            if r.feature_type in key], dtype=int)
            self._indexes[key] = (ids, cKDTree(self._vectors[ids]) if len(ids) else None)
        return self._indexes[key]

    def _query(self, vectors, types, k):
        ids, tree = self._index(types)
        if tree is None:
            return np.empty((len(vectors), 0), dtype=int)
        count = min(k, len(ids))
        _, indices = tree.query(vectors, k=count)
        return ids[np.asarray(indices).reshape(len(vectors), count)]

    def locate(self, lat, lon, *, altitude=None, k=1, feature_types=None):
        """Locate one point; optional altitude is validated but ignored."""
        point = [lat, lon] if altitude is None else [lat, lon, altitude]
        return self.locate_many([point], k=k, feature_types=feature_types)[0]

    def locate_many(self, positions, *, k=1, feature_types=None):
        """Return ordered dictionaries for an N×2 or N×3 array/sequence.

        Coordinates must be finite and in the closed WGS84 degree ranges.
        Longitude is not wrapped. An invalid row rejects the entire batch.
        k is capped at the number of eligible records; empty filters return
        no selected neighbors. Class-specific neighbors are always unfiltered.
        """
        if isinstance(k, bool) or not isinstance(k, Integral) or k < 1:
            raise ValueError("k must be a positive integer")
        if feature_types is None:
            types = FEATURE_TYPES
        elif isinstance(feature_types, str):
            types = frozenset({feature_types})
        else:
            types = frozenset(feature_types)
        if not types <= FEATURE_TYPES:
            raise ValueError(f"unknown feature types: {sorted(types - FEATURE_TYPES)}")
        points = _positions(positions)
        if len(points) == 0:
            return []
        vectors = _xyz(points)
        selected = self._query(vectors, types, k)
        any_ids = self._query(vectors, FEATURE_TYPES, 1)
        settlements = self._query(vectors, SETTLEMENTS, 1)
        landmarks = self._query(vectors, {"landmark"}, 1)

        def neighbors(point, ids):
            distances, bearings = _geometry(point, self._coordinates[ids])
            return [dict(asdict(self.records[int(idx)]), distance_km=float(dist),
                         bearing_deg=bearing)
                    for idx, dist, bearing in zip(ids, distances, bearings)]

        results = []
        for row, point in enumerate(points):
            matches = neighbors(point, selected[row])
            nearest = matches[0] if matches else None
            query = {"lat": float(point[0]), "lon": float(point[1])}
            if len(point) == 3:
                query["altitude"] = float(point[2])
            text = "No reference matches the requested feature types."
            confidence = "no_reference"
            if nearest:
                name, distance = nearest["name"], nearest["distance_km"]
                confidence = "nearby_reference" if distance <= 25 else "distant_reference"
                text = f"Nearest known reference point: {name} (approximately {distance:.1f} km away)."
            if self.synthetic:
                text = "Synthetic demo data. " + text
            results.append({
                "query": query, "altitude_ignored": len(point) == 3,
                "dataset_id": self.dataset_id, "synthetic": self.synthetic,
                "nearest": nearest, "neighbors": matches,
                "nearest_any": neighbors(point, any_ids[row])[0],
                "nearest_settlement": next(iter(neighbors(point, settlements[row])), None),
                "nearest_landmark": next(iter(neighbors(point, landmarks[row])), None),
                "description": text, "confidence": confidence,
            })
        return results


@lru_cache(maxsize=1)
def _default_gazetteer():
    data = json.loads(files("tinygeo").joinpath("resources/demo.json").read_text(encoding="utf-8"))
    return Gazetteer((Reference(**r) for r in data["records"]),
                     dataset_id=data["dataset_id"], synthetic=data["kind"] == "synthetic")


def locate(lat, lon, *, altitude=None, k=1, feature_types=None):
    """Locate with the cached bundled dataset (currently a synthetic demo)."""
    return _default_gazetteer().locate(lat, lon, altitude=altitude, k=k, feature_types=feature_types)


def locate_many(positions, *, k=1, feature_types=None):
    """Batch locate with the cached bundled dataset (currently synthetic)."""
    return _default_gazetteer().locate_many(positions, k=k, feature_types=feature_types)
