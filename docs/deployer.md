# Build an application deployment

`tools/deploy.py` fetches source snapshots, selects a requested number of reference
points, compresses the database, and packages the shared runtime for an application.
All source fetching and SciPy/geopy work happens on the build machine. The result
uses only **Python 3.10+, NumPy >=1.21.6, and the standard library**, fully offline.

## First build

From the repository root:

```sh
.venv/bin/python -m pip install -e '.[builder]'
.venv/bin/python tools/deploy.py --target pythonista --points 10000 --layout external
```

The default fetches GeoNames `cities500.zip`, `admin1CodesASCII.txt`, and the source
readme. Downloads are cached under ignored `data_sources/geonames/`. These public
bulk downloads need no GeoNames API username. The output is
`dist/tinygeo-pythonista-external-10000/`:

| File | Purpose |
|---|---|
| `tinygeo.zip` | Complete deployable bundle, including smoke test and attribution |
| `dataset.json.gz` | Standalone dataset for inspection or `Gazetteer.from_file` |
| `source-lock.json` | Checksums, URLs, and acquisition metadata for input snapshots |
| `manifest.json` | Target/profile, runtime file hashes, dataset ID and count |
| `build-report.json` | Selection parameters, feature/country counts, coverage and size diagnostics |

Extract `tinygeo.zip` into your application's script folder. For Pythonista, copy
that extracted folder into its script library and run `smoke_test.py`. Use
`from tinygeo import locate, locate_many, dataset_info` in other scripts beside it.
Device location recording and track prediction are separate planned app features;
this tool packages the geographic lookup library.

**Default-source limitation:** `cities500` primarily supplies settlements. It does
not supply worldwide mountain/landmark coverage. The report marks settlement-only
builds explicitly. A large point count cannot invent feature classes missing from
the source inputs. The development checkout retains its tiny synthetic fixture;
real data is placed in deployment artifacts, without silently replacing that fixture.

## Targets, storage, and optimization

```sh
# One Python file containing runtime plus gzip/Base64 data:
.venv/bin/python tools/deploy.py --target pythonista --points 5000 --layout embedded

# General Python application, external compressed data:
.venv/bin/python tools/deploy.py --target python --points 20000 --layout external
```

`external` creates a `tinygeo/` package whose database is a local gzip-compressed
UTF-8 JSON resource. The loader caches it on first use. This layout is recommended
for maintainability and independent data inspection.

`embedded` creates `tinygeo.py`; copying that one file is enough for runtime use.
The data, attribution, and source metadata travel with it. The full ZIP also
contains the manifest, report, licenses, and smoke test. Base64 increases the
uncompressed module size compared with a separate gzip file; the report measures
both database and runtime payload sizes. Both layouts use generated copies of
the same runtime source, without a separately maintained implementation.

The Pythonista profile limits pairwise scan chunks to 65,536 query/reference pairs;
the general Python profile uses 262,144. This adjusts temporary memory usage without
changing nearest-neighbor semantics. Both target Python 3.10+ and retain NumPy as
the only external runtime dependency. Physical iOS performance remains to be tested.

`--coordinate-decimals` defaults to 5, preserving typical GeoNames coordinate
precision. Values 3–7 are accepted. Choosing 3 can move coordinates by tens of meters;
the report records the setting. gzip compression level 9, a zero gzip timestamp,
and fixed ZIP metadata keep packaging compact and reproducible. No serialized
SciPy index is included. Requested counts larger than the distinct candidate pool
fail explicitly rather than silently producing fewer or duplicated points.

## Broader or regional sources

```sh
# Worldwide natural and cultural features as well as settlements; much larger input:
.venv/bin/python tools/deploy.py --sources allCountries --points 10000 --target pythonista

# Regional application using complete country extracts:
.venv/bin/python tools/deploy.py --countries NZ AU --points 5000 --target python

# Combine global settlements with full feature coverage in selected countries:
.venv/bin/python tools/deploy.py --sources cities500 --countries NZ --points 10000

# Existing GeoNames-format local snapshots; no network calls:
.venv/bin/python tools/deploy.py --input /path/to/NZ.zip /path/to/AU.zip \
  --admin1 /path/to/admin1CodesASCII.txt --points 5000 --offline
```

Supported named extracts are `cities500`, `cities1000`, `cities5000`, `cities15000`,
and `allCountries`; two-letter country codes request their GeoNames extracts.
The all-country archive was about 403 MB at initial implementation and is streamed
while parsing. Country additions give broader feature types only in those countries;
the report should be assessed with that sampling bias in mind. Local `--input`
files must be actual GeoNames 19-column TSVs or ZIP snapshots of that format.

Selection is deterministic `spatial-strata-v1`:

1. Stream eligible populated places and a documented subset of natural/cultural
   feature codes. Exclude administrative areas and abandoned/historical settlements.
2. Retain a bounded candidate pool: four high-salience references per equal-area
   cell and broad feature group, plus a global high-salience pool of the requested
   size. Salience uses population, capital/region-seat status, and mountain elevation.
3. Merge repeated source IDs and coincident same-name aliases within a feature type.
4. Seed approximately 20% important settlements, then fill spatial strata toward
   70% settlements, 20% natural features, and 10% cultural landmarks. Missing groups
   give their unused budget to available candidates. Explicit curated additions
   are pinned and included within the requested total, not appended afterward.
5. Prefer less occupied cells and references far from existing selections, capping
   the advantage of remoteness at 1,000 km. SciPy `cKDTree` computes these build-time
   coverage distances. Fill remaining slots deterministically, then export records.

This is an auditable starting heuristic, not a validated global recognizability
ranking or a guarantee that every small island is represented. Source feature
whitelists live in `tools/geodb.py`. Coverage diagnostics include area-uniform
global samples (including oceans) and retained settlement-candidate samples;
the latter are not a population-weighted independent validation dataset. All
descriptions retain the runtime's qualified nearest-reference wording.

Country and administrative-region labels are exported only for settlements
(`city`, `town`, `remote_settlement`). Physical features and landmarks have null
`country_code`/`region`; their report entries use the `not_applicable` country
bucket. Raw source snapshots retain their original metadata. Countries and
administrative areas are excluded as standalone area-coverage entities; the
planned circle model covers physical regions, with no separate "Iceland Region".

## Cached, pinned, and offline rebuilds

```sh
.venv/bin/python tools/deploy.py --target pythonista --points 10000 --layout external \
  --lock dist/tinygeo-pythonista-external-10000/source-lock.json \
  --offline --output dist/rebuilt
```

Cached source content is stored by SHA-256, and verified before reuse. A source
lock pins its exact input checksums and acquisition metadata; old cache snapshots
survive a later `--refresh`. Offline mode fails if a required snapshot is absent.
If the original snapshot has disappeared upstream, preserve/restore its cache to
rebuild it; a changed download never silently satisfies an old lock.

The lock pins **sources**. Pass the same point count, coordinate precision, target,
and layout to reproduce a build. The manifest/report record those settings, tool
versions and builder-source hashes. Reproducibility assumes the same code and
toolchain as well as the same sources/settings. Local locks also require the same
`--input`/`--admin1` snapshots; paths may move as long as basenames and bytes match.

`--refresh` explicitly acquires current upstream snapshots and cannot be combined
with `--offline` or `--lock`. Build outputs contain record attribution, source IDs,
license links, and checksums; source downloads and local credentials are not shipped.

## Optional geopy integration for curated places

geopy supplies clients for online providers. Bulk GeoNames extracts supply the main
database. The first geopy adapter uses the GeoNames provider so added data retains
the same provenance and CC BY 4.0 attribution as the bulk records. Other providers
would need their own adapters, caching, and license handling.

Create a small `places.json` list (maximum 50 entries):

```json
[
  {"query": "Eiffel Tower", "country": "FR", "feature_type": "landmark"}
]
```

Then set `GEONAMES_USERNAME` in your local shell to your enabled GeoNames account
and run:

```sh
.venv/bin/python tools/deploy.py --points 10000 --places places.json
```

The builder uses HTTPS, at most one query per second, and caches responses by
content hash. It performs no automatic retries. Include an optional `geonames_id`
to require a specific result ID; `country` and `feature_type`, when supplied, are
also checked. Unsupported feature codes or missing results stop the build. Review
resolved names/IDs in the dataset before relying on ambiguous queries. Cached
responses support `--offline`; with a source lock, pass the same `--places` file.
The username is read only on the build machine and is not stored in artifacts.

The cache/identity behavior is tested with mocked provider responses. Live geopy
queries require your account and have not been exercised in the initial build.

## Use the generated data in the notebook

From `notebooks/`:

```python
from tinygeo import Gazetteer
db = Gazetteer.from_file("../dist/tinygeo-pythonista-external-10000/dataset.json.gz")
db.locate(42.36, -71.06, k=3)
```

See the [GeoNames export specification](https://download.geonames.org/export/dump/readme.txt)
and [geopy provider documentation](https://geopy.readthedocs.io/en/stable/) for source
formats, attribution, account setup, and service behavior.

## Initial build verification

The 2026-10-31 `cities500` snapshot produced exactly **10,000 references** across
245 source country codes, selected as 4,932 cities, 5,021 towns, and 47 small
settlements. Dataset ID: `geonames-f508da16eb0e0c1e7292`.

| Measured artifact | Bytes |
|---|---:|
| Database JSON | 1,655,363 |
| Database gzip | 305,907 |
| External-layout ZIP | 319,164 |
| Embedded `tinygeo.py` alone | 431,102 |
| Embedded-layout ZIP | 329,682 |

The external ZIP rebuilt byte for byte from its source lock. Both layouts ran
their offline smoke tests on desktop Python 3.10 / NumPy 1.21.6 with network
creation blocked and neither SciPy nor geopy installed. The embedded module also
ran after copying only `tinygeo.py` to a separate folder. These sizes will change
with source snapshots, counts, configuration, or code.

On **2026-10-31**, the user reported that lookup code also ran on their iPhone in
Pythonista, appearing instantaneous after removing `%timeit`. This confirms basic
device execution. Exact device/software versions, measured latency, and a complete
device release test matrix have not been recorded.

A separate real `AQ` country-extract build exercised natural features: 250 selected
points included 217 mountains, 2 islands, 4 other features, and 27 small settlements.
Curated geopy behavior is covered by mocked/cache tests, without a live account.

## Fuzzy circles and Natural Earth physical features

```sh
.venv/bin/python tools/deploy.py --natural-earth --points 10000 --target pythonista \
  --output dist/tinygeo-physical-pythonista-external-10000
```

`--natural-earth` fetches and pins three small public-domain 10m datasets from
Natural Earth's physical labels collection: land regions, marine areas, and
elevation points. Its polygons are sampled on a 5° grid plus an interior anchor.
Circles overlap and provide approximate nearby context. Island/continent/geoarea
label polygons are excluded so country-like regions such as Iceland are not
introduced. Mountain points use a declared 5 km radius assumption.

By default up to 35% of stored rows are reserved for these physical sources.
`--physical-fraction` and `--physical-spacing` (1–15°) are adjustable. `--points`
still specifies the exact number of stored records; `feature_count` reports the
number of logical features after grouping repeated support circles. Selection can
thin a region's samples, so the circles do not guarantee complete area coverage.
Natural Earth boundaries are cartographic approximations, not authoritative areas.

New builds estimate settlement radii as `sqrt(population / (pi * density))`, clamped
to 0.5–40 km; unknown population gets 1 km. The default **1,000 people/km² is an
assumption**, not measured urban density. `--radius-min`, `--radius-max`, and
`--radius-fallback` configure the limits. `--point-radii` makes settlement radii zero.
Regional assumptions can be supplied with `--density-config densities.json`:

```json
{
  "default": 1000,
  "countries": {"XX": 1500},
  "regions": {"XX/Example Region": 2500},
  "source_note": "Illustrative assumptions; replace with calibrated values"
}
```

Keys use source country codes and administrative region **display names** before
export. Overrides prefer region, then country, then default. All assumptions are
stored in selection metadata. Source country/admin labels are still exported only
on settlements. No GHSL raster processing or extra runtime dependency is required.

Runtime schema 2 adds `radius_km`, `feature_id`, and `radius_method`, and remains
able to read old schema-1 point files. Area ranking uses `max(0, center-radius)`
with center distance as tie-breaker. Repeated circles for one feature produce one
neighbor. Results keep `distance_km` as center distance and expose
`distance_to_area_km`, `within_radius`, `nearest_region`, and `nearby_regions`.
Use `distance_mode="center"` to ignore radii for ranking.

Each new deployment includes `benchmark_deployed.py`, a plain-Python timing script
that benchmarks the bundled real dataset on desktop or Pythonista without IPython
magics. The [decision log](decisions.md) records accuracy and simplicity choices.

Initial fuzzy checkpoint: 10,000 stored circles represent 7,683 logical features,
including 3,790 cities, 2,678 towns, 32 remote settlements, 459 mountain references,
126 desert circles, 300 mountain-range circles, 1,345 ocean circles, 567 sea circles,
4 lake circles, and 699 other physical-region circles. The gzip database is 328,076
bytes. Both external and embedded layouts were exercised under Python 3.10 with
only NumPy, with network and build-module imports disabled. A locked external
rebuild matched byte for byte. Sizes and IDs describe this initial checkpoint and
will evolve with later builds; manifests are authoritative for a particular artifact.
