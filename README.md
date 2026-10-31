# TinyGeo

Compact offline geographic context from latitude/longitude. See [PROJECT.md](PROJECT.md)
for the full project design and acceptance criteria.

**Status: first runtime milestone. The bundled six-point dataset is entirely
synthetic and is useful only for exercising the API. Real geographic coverage,
the reproducible GeoNames builder, track summaries, and benchmarks are pending.**
Every bundled lookup marks its dataset as synthetic and labels its description.

## Development

Python 3.11 or newer is required. The initial development environment uses 3.13.

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
python examples/demo.py
```

Installing dependencies needs network access or locally available wheels. Runtime
queries use only local package resources, NumPy, SciPy, and the standard library.

## Lookup API

```python
from tinygeo import locate, locate_many, Gazetteer, Reference

# These calls use SYNTHETIC demonstration references until a real database ships.
one = locate(0, -179.9, k=3)
batch = locate_many([(0, 0), (45, 90)], feature_types={"city", "town"})

# Applications can supply their own reference records now.
db = Gazetteer([
    Reference("example:1", "Example reference", 0, 0, "city"),
], dataset_id="my-example")
result = db.locate(0.1, 0.1)
```

Each result contains the query, selected `neighbors`, their `nearest` member,
unfiltered `nearest_any`, `nearest_settlement`, and `nearest_landmark`, plus a
qualified description, proximity classification, and dataset identity. Each
neighbor includes its reference metadata, `distance_km`, and `bearing_deg`.
Country and region fields describe the reference point, never query containment.

- Positions are numeric N×2 or N×3 arrays or rectangular Python sequences.
  The optional third column is altitude; it is preserved and ignored for matching.
  Single queries accept `altitude=`. All values must be finite.
- Latitude must be in [-90, 90], longitude in [-180, 180]. Longitudes are not
  automatically normalized. Any invalid sample rejects the entire batch.
  Empty batches must have an explicit `(0, 2)` or `(0, 3)` array shape.
- `k` is a positive integer, capped at the eligible record count. Feature filters
  select indexes before querying. Empty or unmatched filters return no selected
  neighbors; class-specific results remain available independently of the filter.
- Types: `city`, `town`, `remote_settlement`, `landmark`, `mountain`, `lake`,
  `island`, `other`. Settlement means the first three; landmark means exactly
  `landmark`, with natural features requested through their own types.
- Distances use a sphere of radius **6371.0088 km**, not an ellipsoidal geodesic.
  Bearings point from query to reference, clockwise from north. Undefined
  bearings at coincident/antipodal points or an exact query pole are `None`.
  Equal-distance neighbor ordering is unspecified.
- `nearby_reference` means within 25 km; otherwise `distant_reference`.
  This is a proximity label, not a geographic accuracy guarantee. No matching
  references produces `no_reference`. Ocean/country membership is not inferred.
- The default dataset loads once; feature-specific spherical cKDTree indexes are
  cached. Batch neighbor searches are vectorized; dictionary formatting currently
  runs per sample. Performance has not yet been benchmarked.

## Next milestones

1. GeoNames source/license verification, snapshot manifest, deterministic selection
   of approximately 10,000 useful references, and coverage diagnostics.
2. Measured compressed package resource, provenance, and real offline demo.
3. Track transitions with hysteresis and preserved sample indices/timestamps.
4. Geographic coverage evaluation, cold/warm benchmarks, and `reverse_geocoder`
   nearest-settlement comparisons.

## Git and attribution

Development uses local `main`, synchronized with
[`erikson1970/tinyReverseGeo`](https://github.com/erikson1970/tinyReverseGeo).
Deploy keys and downloaded source inputs are excluded from version control.
Source code is licensed under [Apache 2.0](LICENSE). See [LICENSES.md](LICENSES.md)
for data provenance and attribution.
