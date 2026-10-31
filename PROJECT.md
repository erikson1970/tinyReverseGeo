# TinyGeo — Compact Offline Geographic Context from Coordinates

## Project objective

Build a small, self-contained Python package that converts a latitude/longitude pair or a time series of coordinates into **useful, human-readable geographic context**, entirely offline at runtime.

The central idea is a carefully selected gazetteer of approximately **10,000 geographically representative reference locations** worldwide—not administrative polygons. The database should include cities, towns, notable landmarks, islands, mountains, and other named geographic features. For each query, return nearby reference points, distances and bearings, and a concise, appropriately qualified description.

This is **approximate geographic context**, **not authoritative reverse geocoding**. In particular, the country or state associated with the nearest feature must *not* be presented as the verified country/state containing an arbitrary query coordinate.

Target **Python >=3.10**, including Pythonista's documented interpreter, with
**NumPy as the only external runtime dependency**. Prioritize simple, accurate
lookups at device update rates (typically below 10 Hz), while retaining batch
support. A dependency-free Python fallback is optional. Desktop database-building
tools may use additional libraries such as SciPy; deployed artifacts must not
require them.

## Desired outcomes

1. `locate(lat, lon)` returns useful geographic context for one position.
2. `locate_many(positions)` efficiently handles N×2 or N×3 arrays (lat, lon, optional altitude), preserving order, and also accepts ordinary Python sequences.
3. `describe_track(positions, ...)` optionally compresses a trajectory to meaningful geographic transitions rather than duplicating nearly identical descriptions.
4. A reproducible, separately run **database builder** generates the shipping dataset from open geographic sources. No downloads or network operations at runtime.
5. A compact distribution: **target ≤ 1 MB compressed data** for roughly 10,000 reference locations, with actual size reported by the build process. This is a target rather than a hard constraint; do not sacrifice needed geographic coverage merely to meet it.
6. Evaluation scripts and tests measure geographic usefulness, numerical correctness, speed, and comparisons to `reverse_geocoder`.
7. Future release: a Pythonista/iOS companion uses this library and the device's reported location for current context, a history of nearby/passed references, and velocity-based projections at 15 minutes, 1 hour, and 2 hours. See the release TODO below; this is outside the first desktop milestone.
8. A desktop deployer builds for a processing target (`pythonista` or general
   `python`), accepting the requested reference count and embedded/external data
   layout. It fetches and pins source snapshots, selects/optimizes the database,
   and emits an offline runtime, source lock, attribution, and coverage/size report.

## Repository layout (suggested)

```text
tinygeo/
  PROJECT.md
  README.md
  pyproject.toml
  src/tinygeo/
    __init__.py
    lookup.py          # single and batch queries
    track.py           # trajectory summarization
    data.py            # compact embedded data OR package resource loader
  tools/
    build_geodb.py     # downloads/prepares source data; not used at runtime
    evaluate.py        # validation and benchmark suite
  data_sources/         # downloaded inputs; gitignored unless license permits
  tests/
    test_lookup.py
    test_track.py
    test_boundaries.py
  LICENSES.md           # source attributions, versions, and licenses
```

The initial implementation may be smaller; avoid unnecessary infrastructure.

## Deployment utility

The implemented entry point is `tools/deploy.py`; see [docs/deployer.md](docs/deployer.md).
For example:

```sh
python -m pip install -e '.[builder]'
python tools/deploy.py --target pythonista --points 10000 --layout embedded
```

The initial deployer supports cached GeoNames city/country/worldwide exports,
deterministic spatial-stratified selection with salience seeds, gzip JSON resources,
and single-file gzip/Base64 builds generated from the same runtime source. Target
profiles set batch memory budgets. SciPy helps selection/coverage only at build
time; optional geopy resolves small curated GeoNames query lists with response
caching. NumPy remains the only external deployed dependency. geopy is a client
for providers, so each added provider needs explicit data provenance/licensing.

Output includes `tinygeo.zip`, `dataset.json.gz`, `manifest.json`, `source-lock.json`,
and `build-report.json`; embedded builds also expose `tinygeo.py` directly. Rebuilds
can pin sources with `--lock ... --offline`. Defaults use `cities500` and therefore
provide settlement coverage; use broader extracts for natural/cultural features.
The development package's synthetic fixture stays separate from real deployment
data until a release dataset is selected. Selection-quality evaluation and physical
Pythonista validation remain required before declaring v1 complete.

## Database design

### Record schema

Each geographic point should contain:

- latitude, longitude (WGS84 decimal degrees)
- human-readable UTF-8 name
- feature type: e.g. `city`, `town`, `landmark`, `mountain`, `lake`, `island`, `remote_settlement`, `other`
- country code (ISO 3166-1 alpha-2 where known), attached only to settlements
- administrative region/state/province where available, attached only to settlements
- optional population or significance score
- source ID (e.g., GeoNames ID) for reproducibility and traceability
- optional `radius_km` (default zero), stable logical `feature_id`, and `radius_method`

Altitude is accepted in position inputs but **ignored for geographic nearest-neighbor matching** in v1; report this clearly. Do not accidentally treat altitude as longitude or vice versa.

### Country labels and future area coverage

Country and administrative labels belong to municipality/settlement references
(`city`, `town`, `remote_settlement`). New deployment exports set `country_code`
and administrative `region` to null for physical features and landmarks.
Original source snapshots retain their provenance metadata.

Do not create standalone country/state/province regions or coverage circles
representing countries. In particular, there should be no **"Iceland Region"**: country context
comes from the country metadata on nearby settlements. Area coverage is intended
for physical features such as oceans, seas, deserts, and mountain ranges.

The implemented representation adds optional `radius_km` (zero for a point)
and a shared `feature_id` for multiple coverage circles describing one physical
feature. Rank approximate area proximity using `max(0, center_distance - radius)`,
group results by feature ID, and retain both regional context and nearby settlement
references. Circle membership describes the modeled coverage, not administrative
containment. Overlaps are accepted. Settlement density-based radii are configurable
heuristics; Natural Earth physical-area samples provide overlapping circles with
shared feature IDs. See [docs/decisions.md](docs/decisions.md) for defaults and
alternatives. Dataset schema 2 adds these fields; runtime also reads schema 1.

### Sources and licensing

Start with **GeoNames** as the primary gazetteer. Investigate its downloadable population-filtered city files and feature classes, including appropriate regional/natural features. Supplement notable cultural or historical landmarks only when a freely distributable, appropriately licensed source is available. Maintain source URLs, access date, file versions/checksums if feasible, and required attribution. Respect source-specific licenses and attribution requirements (GeoNames is generally CC BY 4.0; check the exact sources used). Make the builder deterministic from a fixed, documented input snapshot and configuration.

Avoid hand-creating hundreds of famous sites. Use a small, auditable curated override list only to correct obvious gaps or prioritize especially recognizable places.

### Geographic selection: don't take the 10,000 largest cities

Optimize for **recognizability and spatial coverage**. Suggested rough allocation, tunable rather than hard-coded:

| Group | Approximate count | Selection goal |
|---|---:|---|
| Major metropolitan settlements | 2,000 | Familiarity, population, national/regional capitals |
| Smaller regional towns/settlements | 5,000 | Geographic coverage and local context |
| Natural geographic features | 1,500 | Mountains, lakes, islands, deserts, capes, etc. |
| Historic/cultural sites & landmarks | 1,000 | Recognizable named places |
| Remote/polar/island features | 500 | Fill sparse coverage gaps |
| **Total** | **10,000** | Tunable |

Implement a **reproducible selection algorithm** rather than relying only on population rank. A useful starting point:

1. Seed globally important settlements, capitals, and a modest set of highly significant named sites.
2. Assign candidate weights for population/significance, feature class, and whether a region is underserved.
3. Greedily add candidates that improve geographic coverage, e.g. weighted farthest-point sampling, with sensible spatial caps so extremely remote points do not consume most of the budget.
4. Ensure each continent, major island group, and remote populated region receives reasonable coverage; sample oceans/polar areas separately if meaningful features exist.
5. Deduplicate locations that coincide or are the same site under variant names; retain separate feature classes where useful.
6. Produce a diagnostic coverage report and map of the selected locations (optional visualization; Matplotlib is sufficient).

Builder-only dependencies are acceptable: use SciPy's `cKDTree` where useful for
candidate spacing, coverage scoring, or deduplication. Install these separately
with `pip install -e '.[builder]'`. Export only portable records, coordinates, and
metadata; do not serialize SciPy trees or require SciPy in deployed lookup code.

Avoid overconcentration in Europe, east Asia, and the northeastern U.S. while also avoiding overrepresentation of arbitrary nearly uninhabited points. Treat administrative boundary areas and coastlines as ambiguous unless true boundary data is added later.

### Packaging and compression

Try two approaches and measure both:

- **Preferred for maintainability:** ship a compressed binary resource (e.g. `.gz` or `.npz`) with the Python package.
- **Optional single-file distribution:** embed `gzip` + Base64 data into a Python module, decoded on first use.

A compact, versioned format with scaled integer coordinates (e.g. 1e-4 degrees), indexed/shared strings, feature-class IDs, and optional population/significance is appropriate. Measure actual compressed size; avoid making a complicated custom format without demonstrable value. Preserve Unicode names and attribution metadata. Decode/load **once**, not for every lookup. Runtime dependencies are limited to NumPy and the standard library; builder dependencies stay outside deployed bundles.

## Runtime lookup design

Cache 3D unit-sphere Cartesian coordinates, then scan them with NumPy to handle
the antimeridian and poles cleanly:

```python
lat_r = np.deg2rad(lat)
lon_r = np.deg2rad(lon)
xyz = np.column_stack((
    np.cos(lat_r) * np.cos(lon_r),
    np.cos(lat_r) * np.sin(lon_r),
    np.sin(lat_r),
))
```

Rank by squared chord distance computed from direct vector differences; this
preserves close-point distinctions better than subtracting a dot product from 1.
Chord ranking is equivalent to great-circle distance ordering. Each query scans
the eligible references (O(M)); at approximately 10,000 points and below 10 Hz,
measure this simple approach before considering additional runtime indexing.
Process batches in bounded chunks rather than allocating an N×M distance array
for the entire track. Convert selected results to great-circle distances in km
and calculate initial bearings (0–360 degrees clockwise from north). Use a
consistent mean Earth radius (e.g. 6371.0088 km); document that this is a spherical
approximation, not an ellipsoidal geodesic.

Support feature filters (e.g. settlement-only, landmark-only, or any) and nearest-*k*
results. Filter the candidate set before selecting neighbors; cache feature subsets.
Use NumPy minimum/partial selection for unique zero-radius points and a full sort
for circle/group queries, ordering by area distance then center distance. Do not
retrieve an insufficient fixed number of unfiltered neighbors and filter afterward.

Potential public API (refine if needed):

```python
from tinygeo import locate, locate_many, describe_track

one = locate(42.36, -71.06)
results = locate_many([(42.36, -71.06), (48.86, 2.35)])
events = describe_track(positions, min_distance_km=25)
```

Return ordinary Python dictionaries or documented lightweight dataclasses. An example result *shape* (not verified geographic values):

```python
{
    "query": {"lat": 42.36, "lon": -71.06},
    "nearest": {
        "name": "...", "feature_type": "city", "region": "...",
        "country_code": "US", "lat": 0.0, "lon": 0.0,
        "distance_km": 0.0, "bearing_deg": 0.0
    },
    "nearest_settlement": {"name": "...", "distance_km": 0.0},
    "nearest_landmark": None,
    "description": "Near ... (reference point approximately ... km away)",
    "confidence": "nearby_reference"
}
```

The `confidence` field describes **proximity to reference locations**, not verified administrative containment. Do not invent country/state for points at sea or near borders. For very remote positions, prefer wording like “approximately 620 km northwest of [place]” rather than “in [place].” Use distance-sensitive text thresholds that can be tuned and documented. Consider exposing both `nearest_any` and class-specific nearest neighbors.

### Ocean and regional descriptions

Ocean coverage is a **stretch goal**, not something the point database can reliably solve on its own. A nearest-island lookup does not establish which ocean, sea, or country contains a coordinate. For v1, use a qualified “nearest known reference point” description offshore. Later, optionally add coarse ocean/major-region polygons or another properly licensed classifier, explicitly keeping it separate from the point gazetteer. Never infer authoritative geographic boundaries from nearest-place country codes.

### Track summaries

`describe_track` should:

- accept ordered coordinates and optional timestamps; preserve original indices/timestamps;
- use batch lookup internally;
- return event/segment records with geographic labels and sample ranges;
- suppress redundant label changes or jitter near Voronoi boundaries (hysteresis or minimum dwell/distance threshold);
- allow both full-sample annotations and transition-only output;
- handle antimeridian crossings, stationary tracks, short tracks, and missing/invalid data predictably.

Do not invent timestamps when none were supplied.

## Validation and benchmark plan

Treat **`reverse_geocoder` as a comparison baseline, not ground truth**. It generally targets the nearest populated place, whereas this tool may intentionally choose a nearby geographic feature or a smaller, more geographically representative settlement. Evaluate the **nearest-settlement** mode against it separately.

1. **Numerical checks:** identical point ≈ 0 km; known great-circle cases; coordinates around ±180° longitude; poles; equator; coincident points; invalid latitude/longitude; longitude normalization policy.
2. **Reference checks:** major world cities; rural interiors; Sahara; Siberia; Australian Outback; remote islands; Antarctica; open ocean; borders/coasts. Ensure descriptions remain appropriately qualified.
3. **Comparison:** sample globally distributed coordinates and actual settlement coordinates; compare nearest settlement identity and great-circle distance to `reverse_geocoder`; explain disagreements due to database coverage or method rather than calling every disagreement a failure.
4. **Speed:** benchmark single lookups and batches of 1k, 10k, 100k samples, including cold-load vs warm-query timing, and peak memory usage.
5. **Coverage:** show distributions of nearest-reference distance for random global samples and for populated-region samples, separately. Test that data selection is not unduly biased by population alone.
6. **Reproducibility:** rebuilding the same dataset snapshot should produce matching records and a deterministic output hash where practical.

Optional: generate a map of selected locations and a geographic error/coverage heatmap using Matplotlib. Visual inspection is useful here.

## Acceptance criteria for v1

- Runtime operates fully offline on Python >=3.10, using only NumPy and the Python standard library. SciPy is allowed only in separately installed database-building tools.
- Reproducibly builds a gazetteer of approximately 10,000 distinct useful records with clear license/source attribution.
- Provides `locate` and `locate_many` with class-specific nearest references, distance and bearing; `describe_track` has basic transition suppression.
- Correct for antimeridian and polar test cases, with explicit validation rules for coordinates and altitude handling.
- Does not claim exact country/state containment from a nearest feature alone.
- Includes tests, a runnable demo, size measurements, performance metrics, and a summary of baseline comparisons to `reverse_geocoder`.
- Documents how to regenerate the database, and how to use the runtime in a disconnected environment.

## Future release TODO: Pythonista / iOS track companion

Deliver a versioned, importable Pythonista bundle that uses the shared TinyGeo
library and offline gazetteer on iPhone/iPad. This is planned work, not currently
supported functionality or an additional gate for desktop v1.

### Compatibility and packaging recommendation

Pythonista's official documentation, checked **2026-10-31**, advertises Python 3.10,
bundled NumPy, and an iOS `location` module. SciPy is not listed in the bundled
third-party modules, so the release does not depend on it. The core now targets
Python >=3.10 and NumPy >=1.21.6, with no SciPy runtime dependency. Confirm actual
versions/imports and resource loading on the target device before claiming
verified Pythonista support; desktop Python 3.10 tests alone are insufficient.

Use **one shared database and a NumPy lookup backend** across desktop and iOS:

- [ ] Build a versioned gzip-compressed UTF-8 JSON resource, readable with the
  standard library, from the same selected records and source manifest used by
  desktop TinyGeo. Preserve Unicode, source IDs, licenses, and attribution.
- [x] Replace runtime SciPy indexing with a shared NumPy nearest-*k* scan over
  cached unit-sphere vectors and filtered subsets, with bounded batch chunks.
- [x] Target Python 3.10 and retain matching semantics, validation, distances,
  bearings, and qualified descriptions in the shared library.
- [x] User-reported basic lookup execution on an actual iPhone/Pythonista
  (2026-10-31). The user described responses as seemingly instantaneous after
  removing the IPython `%timeit` statement; no measured device timing was supplied.
- [ ] Run the supplied smoke test and benchmark approximately 10,000 references
  on a physical Pythonista device. A standard-library-only fallback remains
  optional if a future target cannot import NumPy.
- [ ] Produce a downloadable ZIP containing the importable library, compressed
  data, launcher script, attribution, version/checksum manifest, and copy/run
  instructions. Require no package installation, downloads, or network at runtime.
  The deployer now produces real lookup-only bundles in both layouts; the device
  tracking launcher/UI and release verification remain pending.

### Device tracking and presentation

- [ ] Use `location.start_updates()` / `get_location()` for timestamped device
  fixes, and always call `stop_updates()` on exit. Handle denied permission,
  unavailable fixes, stale/duplicate timestamps, and poor accuracy when reported.
  Keep device acquisition separate from track processing so recorded fixes can
  be replayed on desktop.
- [ ] Show **where we are now** as current coordinates, fix time/age, and nearby
  named reference points with distances/bearings and qualified descriptions.
  Use TinyGeo's offline lookup rather than platform reverse-geocoding services.
- [ ] Persist a local track history of timestamped fixes and nearby/passed
  reference events, with original sample ranges, closest observed approach, and
  configurable proximity/dwell thresholds plus hysteresis. A changing nearest
  label alone must not imply a visit or passage through a place. Support local
  history reload and export (e.g. JSON/CSV); show gaps as gaps.
- [ ] Estimate recent ground speed and course from a configurable window of valid
  timestamped fixes, using spherical geometry and noise suppression; consider
  device speed/course only when available and valid. Distinguish stationary,
  insufficient-history, stale-fix, and unreliable-motion states.
- [ ] Project positions **15 minutes, 1 hour, and 2 hours** from the latest valid
  fix using constant ground speed and the estimated initial course along a great
  circle. Show each projected
  coordinate, target time, and nearby references through TinyGeo. Label these as
  extrapolations assuming unchanged motion, not route predictions or assured
  arrivals. Suppress unreliable moving forecasts; explicitly label stationary
  projections and recompute when motion changes. Handle poles and the antimeridian.
- [ ] Provide start/stop controls and a compact current/history/projection view.
  Begin with foreground recording; verify and document actual behavior during
  screen lock, backgrounding, interruption, and resume before promising continuous
  recording. Measure update cadence, battery cost, load time, memory, and lookup
  latency on a physical iOS device.
- [ ] Validate replay cases for straight motion, turns, stops/GPS jitter, missing
  fixes, stale or out-of-order data, and dateline crossings; check all three
  projection horizons analytically and smoke-test the released bundle on-device.

References: [Pythonista platform capabilities](https://omz-software.com/pythonista/),
[bundled third-party modules](https://omz-software.com/pythonista/docs-3.4/py3/ios/third-party-modules.html),
[device location API](https://omz-software.com/pythonista/docs-3.4/py3/ios/location.html).

## Suggested implementation order for Codex

1. Implement the minimal runtime on a tiny hand-checked test fixture; establish API and unit tests.
2. Implement the GeoNames ingester and deterministic 10,000-point selection pipeline; output maps/statistics.
3. Choose a compressed package format and connect it to the runtime; report actual on-disk and in-memory sizes.
4. Add track transition logic, edge-case tests, and benchmark scripts.
5. Compare to `reverse_geocoder` and generate a short evaluation report; tune selection and wording where results are weak.
6. Optionally implement the embedded Base64 single-file build as a release artifact.
7. Build and validate the Pythonista/iOS companion using the shared NumPy runtime described in the future release TODO.

Keep the implementation lean. Prefer working code, measurable geographic quality, and clear limitations to speculative features. If a requirement is ambiguous, choose a documented sensible default and proceed.
