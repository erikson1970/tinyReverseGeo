"""Spherical nearest-reference lookup with validated, vectorized inputs."""

from dataclasses import asdict, dataclass
from functools import lru_cache
from numbers import Integral, Real

import numpy as np

from .data import load_dataset, load_default_dataset

EARTH_RADIUS_KM = 6371.0088
FEATURE_TYPES = frozenset({
    "city", "town", "landmark", "mountain", "lake", "island",
    "remote_settlement", "other", "ocean", "sea", "desert",
    "mountain_range", "physical_region",
})
SETTLEMENTS = frozenset({"city", "town", "remote_settlement"})
REGIONS = frozenset({"ocean", "sea", "desert", "mountain_range", "physical_region"})
# Bound pairwise temporary arrays instead of allocating query_count × all_records.
_MAX_SCAN_PAIRS = 262_144


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
    population: int | None = None
    radius_km: float = 0.0
    feature_id: str | None = None
    radius_method: str = "unspecified"


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
    """Spherical NumPy scans over cached reference vectors and feature subsets.

    Radii define fuzzy proximity circles, not authoritative boundaries. Matches
    are unique by feature_id (source_id for legacy points). Batches use bounded
    chunks. Equal-distance ordering of zero-radius points is unspecified.
    """

    def __init__(self, records, *, dataset_id="custom", synthetic=False, metadata=None):
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
        if any(isinstance(r.radius_km, bool) or not isinstance(r.radius_km, Real) for r in self.records):
            raise ValueError("radius_km must be a finite numeric distance")
        self._radii = np.array([r.radius_km for r in self.records], dtype=float)
        if not np.isfinite(self._radii).all() or (self._radii < 0).any() or (self._radii > np.pi * EARTH_RADIUS_KM).any():
            raise ValueError("radius_km must be finite and within [0, half the Earth circumference]")
        identities = [r.feature_id if r.feature_id is not None else r.source_id for r in self.records]
        if any(not isinstance(identity, str) or not identity for identity in identities):
            raise ValueError("feature_id must be a nonempty string when supplied")
        self._feature_keys = tuple(identities)
        _, self._feature_codes = np.unique(identities, return_inverse=True)
        self.feature_count = len(set(identities))
        feature_metadata = {}
        for identity, record in zip(identities, self.records):
            signature = (record.name, record.feature_type, record.country_code, record.region)
            if identity in feature_metadata and feature_metadata[identity] != signature:
                raise ValueError("circles sharing a feature_id must share name, type and administrative labels")
            feature_metadata[identity] = signature
        self._subsets = {FEATURE_TYPES: (np.arange(len(self.records)), self._vectors)}
        self.dataset_id = dataset_id
        self.synthetic = synthetic
        self.metadata = dict(metadata or {})

    @classmethod
    def _from_data(cls, data):
        metadata = {k: v for k, v in data.items() if k != "records"}
        metadata["record_count"] = len(data["records"])
        gazetteer = cls((Reference(**r) for r in data["records"]),
                   dataset_id=data["dataset_id"], synthetic=data["kind"] == "synthetic",
                   metadata=metadata)
        if "feature_count" in metadata and metadata["feature_count"] != gazetteer.feature_count:
            raise ValueError("dataset feature_count does not match its records")
        gazetteer.metadata["feature_count"] = gazetteer.feature_count
        return gazetteer

    @classmethod
    def from_file(cls, path):
        """Load an external JSON/gzip gazetteer produced by the deployer."""
        return cls._from_data(load_dataset(path))

    def _subset(self, types):
        key = frozenset(types)
        if key not in self._subsets:
            ids = np.array([i for i, r in enumerate(self.records)
                            if r.feature_type in key], dtype=int)
            self._subsets[key] = (ids, self._vectors[ids])
        return self._subsets[key]

    def _query(self, vectors, types, k, distance_mode="area"):
        ids, references = self._subset(types)
        if len(ids) == 0:
            return np.empty((len(vectors), 0), dtype=int)
        codes = self._feature_codes[ids]
        unique_count = len(np.unique(codes))
        grouped = unique_count != len(ids)
        fuzzy = distance_mode == "area" and bool(self._radii[ids].any())
        count = min(k, unique_count)
        result = np.empty((len(vectors), count), dtype=int)
        chunk_size = max(1, min(256, _MAX_SCAN_PAIRS // len(ids)))
        for start in range(0, len(vectors), chunk_size):
            queries = vectors[start:start + chunk_size]
            # Direct differences preserve close-point ordering better than 2-2*dot.
            delta = queries[:, None, :] - references[None, :, :]
            squared = np.einsum("ijk,ijk->ij", delta, delta)
            del delta
            if fuzzy or grouped:
                center_km = 2 * EARTH_RADIUS_KM * np.arcsin(np.clip(np.sqrt(squared) / 2, 0, 1))
                area_km = np.maximum(0, center_km - self._radii[ids]) if fuzzy else center_km
                # A full sort is deliberately simple at ~10,000 rows and <10 Hz.
                # Center distance breaks ties between overlapping coverage zones.
                order = np.lexsort((np.broadcast_to(ids, squared.shape), squared, area_km), axis=1)
                if grouped:
                    nearest = np.empty((len(queries), count), dtype=int)
                    for row, ordered in enumerate(order):
                        _, first = np.unique(codes[ordered], return_index=True)
                        nearest[row] = ordered[np.sort(first)[:count]]
                else:
                    nearest = order[:, :count]
            elif count == 1:
                nearest = np.argmin(squared, axis=1)[:, None]
            else:
                nearest = np.argpartition(squared, count - 1, axis=1)[:, :count]
                distances = np.take_along_axis(squared, nearest, axis=1)
                order = np.argsort(distances, axis=1)
                nearest = np.take_along_axis(nearest, order, axis=1)
            result[start:start + len(queries)] = ids[nearest]
        return result

    def locate(self, lat, lon, *, altitude=None, k=1, feature_types=None, distance_mode="area"):
        """Locate one point; optional altitude is validated but ignored."""
        point = [lat, lon] if altitude is None else [lat, lon, altitude]
        return self.locate_many([point], k=k, feature_types=feature_types, distance_mode=distance_mode)[0]

    def locate_many(self, positions, *, k=1, feature_types=None, distance_mode="area"):
        """Return ordered dictionaries for an N×2 or N×3 array/sequence.

        Coordinates must be finite and in the closed WGS84 degree ranges.
        Longitude is not wrapped. An invalid row rejects the entire batch.
        k is capped at the number of eligible unique features; empty filters return
        no selected neighbors. Class-specific neighbors are always unfiltered.
        Area mode ranks max(0, center_distance - radius), then center distance.
        Center mode ignores radii for ranking. Both return center and area distance.
        """
        if isinstance(k, bool) or not isinstance(k, Integral) or k < 1:
            raise ValueError("k must be a positive integer")
        if distance_mode not in {"area", "center"}:
            raise ValueError("distance_mode must be 'area' or 'center'")
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
        selected = self._query(vectors, types, k, distance_mode)
        any_ids = selected[:, :1] if types == FEATURE_TYPES else self._query(vectors, FEATURE_TYPES, 1, distance_mode)
        settlements = self._query(vectors, SETTLEMENTS, 1, distance_mode)
        landmarks = self._query(vectors, {"landmark"}, 1, distance_mode)
        regions = self._query(vectors, REGIONS, k, distance_mode)

        def neighbors(point, ids):
            distances, bearings = _geometry(point, self._coordinates[ids])
            return [dict(asdict(self.records[int(idx)]), feature_id=self._feature_keys[int(idx)],
                         distance_km=float(dist), distance_to_area_km=max(0.0, float(dist) - float(self._radii[int(idx)])),
                         within_radius=bool(self._radii[int(idx)] > 0 and dist <= self._radii[int(idx)]),
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
                if nearest["radius_km"] > 0:
                    gap = nearest["distance_to_area_km"]
                    confidence = "estimated_coverage" if nearest["within_radius"] else (
                        "nearby_reference" if gap <= 25 else "distant_reference")
                    text = (f"Near {name} (within its estimated proximity zone)." if nearest["within_radius"]
                            else f"Near {name} (approximately {gap:.1f} km from its estimated proximity zone).")
            if self.synthetic:
                text = "Synthetic demo data. " + text
            results.append({
                "query": query, "altitude_ignored": len(point) == 3,
                "dataset_id": self.dataset_id, "synthetic": self.synthetic, "distance_mode": distance_mode,
                "nearest": nearest, "neighbors": matches,
                "nearest_any": neighbors(point, any_ids[row])[0],
                "nearest_settlement": next(iter(neighbors(point, settlements[row])), None),
                "nearest_landmark": next(iter(neighbors(point, landmarks[row])), None),
                "nearest_region": next(iter(neighbors(point, regions[row])), None),
                "nearby_regions": neighbors(point, regions[row]),
                "description": text, "confidence": confidence,
            })
        return results


@lru_cache(maxsize=1)
def _default_gazetteer():
    return Gazetteer._from_data(load_default_dataset())


def dataset_info():
    """Return metadata for the bundled database, without the reference records."""
    from copy import deepcopy
    return deepcopy(_default_gazetteer().metadata)


def locate(lat, lon, *, altitude=None, k=1, feature_types=None, distance_mode="area"):
    """Locate with the cached bundled dataset; inspect dataset_info for provenance."""
    return _default_gazetteer().locate(lat, lon, altitude=altitude, k=k, feature_types=feature_types, distance_mode=distance_mode)


def locate_many(positions, *, k=1, feature_types=None, distance_mode="area"):
    """Batch locate with the cached bundled dataset."""
    return _default_gazetteer().locate_many(positions, k=k, feature_types=feature_types, distance_mode=distance_mode)
