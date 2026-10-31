---
name: tinygeo-track-context
description: Add offline place context to timestamped observer routes, satellite ground tracks, and astronomical visibility results using a supplied TinyGeo database. Use existing astronomy or orbit tools for azimuth, elevation, and event geometry.
---

# TinyGeo track and sky-event context

Turn coordinate/time series into understandable places along a route. Combine
TinyGeo's offline geographic annotations with the sandbox's existing astronomy
and satellite tools. Keep the observer route, target ephemeris, and any ground
track or event footprint explicitly identified.

## Load the supplied database

The portable skill ZIP contains the shared TinyGeo runtime under `assets/tinygeo/`.
It requires Python 3.10+ and NumPy >=1.21.6. The user supplies the external
`dataset.json.gz` or uncompressed JSON separately. In a source checkout the runtime
is under `src/tinygeo/`; an already installed compatible runtime also works.
No SciPy, geopy, network, or database rebuild is needed for annotation.

Discover the actual extracted runtime and database paths. Do not assume a fixed
sandbox directory or install a similarly named package from an online registry.
If only this Markdown file and a database were supplied, the TinyGeo runtime is
also needed. Report a missing runtime/NumPy/database and request the missing
artifact; do not silently fall back to synthetic demo data or online geocoding.

Set `runtime_parent` to the directory containing `tinygeo/` and `database_path`
to the supplied data file, then use this bootstrap:

```python
from pathlib import Path
import hashlib
import sys

sys.path.insert(0, str(runtime_parent))  # portable bundle: skill_directory / "assets"
from tinygeo import Gazetteer, describe_track

database_path = Path(database_path)
db = Gazetteer.from_file(database_path)
if db.synthetic:
    raise ValueError("A geographic dataset is required; the supplied database is synthetic.")
provenance = {
    "dataset_id": db.dataset_id,
    "dataset_sha256": hashlib.sha256(database_path.read_bytes()).hexdigest(),
    "record_count": len(db.records),
    "feature_count": db.feature_count,
    "attribution": db.metadata.get("attribution"),
}
```

Use **this instance** for every lookup: `db.locate(...)`, `db.locate_many(...)`,
and `describe_track(..., gazetteer=db)`. Module-level `locate`, `dataset_info`,
and omitted `gazetteer` arguments use the default bundled database, which may
be absent or synthetic. The portable skill runtime intentionally includes no
reference database. Do not substitute a reference town's coordinates for the
observer's real position.

## Normalize the time series

Use the existing tools to obtain the requested observer route and target/event
series; adapt their real schemas rather than inventing tool names or signatures.
Maintain original sample indices and timestamps. For each observer sample retain:

- UTC instant (timezone-aware datetime or documented UTC epoch seconds),
- WGS84 **geodetic** latitude and east-positive longitude in degrees,
- observer height in metres, including its datum/source when known,
- original tool output, validity/gaps, and any interpolated-position flag.

Parse ISO timestamps into timezone-aware instants before passing them to
`describe_track`; retain the original strings for export. Check epoch seconds
versus milliseconds, and use astronomy-tool conversions for UTC/TAI/TT differences.

TinyGeo accepts latitude in [-90, 90] and longitude in [-180, 180]. If the input
uses 0–360° longitude, convert explicitly with `(lon + 180) % 360 - 180` while
retaining the original value. Confirm coordinate order: the API is **lat, lon**,
whereas many GIS/GeoJSON tools emit lon, lat. Validate finite coordinates first;
`locate_many` rejects its whole batch if any row is invalid. Split gaps/invalid
rows, annotate valid rows, and join results back to the original sample indices.

Keep observer height for the astronomy calculation: TinyGeo validates optional
altitude but **ignores it in lookup**. An aircraft at 10 km and a ground observer
at the same lat/lon get the same place context, but can have different sky geometry.
If observer height is missing, request it when material or state an explicit
approximation and its limits; do not silently replace it with a town's elevation.

## Add geographic context

For valid normalized `observer_rows`, this is the annotation pattern. Each row
uses `timestamp`, `lat_deg`, `lon_deg`, and optionally `height_m` plus astronomy
results such as `az_deg` and `el_deg` that were computed by the other tools:

```python
places = db.locate_many(
    [(r["lat_deg"], r["lon_deg"]) for r in observer_rows], k=3
)
annotated = [
    {**row, "place_context": place, "dataset_id": db.dataset_id}
    for row, place in zip(observer_rows, places)
]
```

Process large series in bounded chunks rather than retaining every full lookup
result at once. Cache by coordinates where useful. Annotate event start, peak,
and end positions separately, especially when the observer is moving.

Interpret the fields correctly:

- `nearest_settlement` supplies municipality context with its own country/admin
  labels. `nearest_region` and `nearby_regions` supply ocean/desert/range context;
  some returned references can be very distant, so inspect their distances.
- `distance_km` is surface distance to the selected support-circle **center**.
  `distance_to_area_km` is the fuzzy zone gap, `max(0, center_distance-radius_km)`.
  A zero gap is modeled proximity, not a verified physical or political boundary.
- `bearing_deg` points from the query toward a **surface reference center**. It is
  not the satellite/event azimuth. It can be None for undefined directions.
- Matches are grouped by `feature_id`. Several support circles can represent one
  named feature; do not count them as independent places or merge IDs just because
  names match. Country/admin labels belong to settlements, not the query point.

Describe results as “near [town, region, country]” with a surface distance, or
“near/over the estimated [ocean/physical-region] zone,” retaining useful nearby
settlement context separately. Do not infer the observer's country from the
nearest municipality. Sparse source coverage must remain visible in the wording.

For a condensed route, use `describe_track` with the supplied timestamps and
explicitly chosen `min_distance_km` / `min_dwell_samples`. Its default 25 km
transition threshold may be too coarse for a short route. Gaps reset context;
events start when changes are confirmed, and closest observed approach does not
prove a visit. Its label segments are not astronomical visibility windows.

## Join astronomy or satellite results

Compute topocentric sky geometry with the sandbox's existing, appropriate tools
at **each observer location and the same instant** as the target/event state.
TinyGeo supplies place names; it does not propagate orbits, transform sky frames,
calculate az/el, or determine eclipse/occultation circumstances.

For satellite states, identify frame, epoch/time scale, distance units, and
orbital-element/ephemeris validity. Convert frames with the existing tool's
supported transforms before forming a line of sight. Never subtract TEME/ECI
coordinates from ECEF/ITRS coordinates as if they shared a frame. A satellite's
lat/lon ground track alone is insufficient for az/el without its altitude or
3D state. Ground-track place labels describe the sub-satellite point, not the
places from which the satellite is visible.

If the tools already supply aligned Earth-fixed Cartesian positions for observer
and target, a Python/NumPy calculation can form their difference and rotate it
into the observer's local east/north/up frame using geodetic lat/lon. Then
`az = degrees(atan2(east, north)) % 360` and
`el = degrees(atan2(up, hypot(east, north)))` give geometric sky angles.
Use a supported ECEF/ENU transform (see the ESA reference below), consistent
length units and instants, and validate zenith/cardinal cases. A zero-length
line of sight has no direction. No orbit propagation is needed when valid 3D
states are already supplied; geodetic lat/lon/height states first need the
appropriate Earth-model conversion. This calculation does not supply optical
visibility, terrain, refraction, or event-specific circumstances.

For stars, planets, the Sun/Moon, or event directions, use the appropriate
astrometric/apparent position and observer-dependent transformation. For eclipse
or occultation visibility, use the event tool's local circumstances or footprint;
being near its centerline or having the Sun/Moon above the horizon is insufficient.

Normalize sky azimuth to degrees eastward from true north (N=0, E=90, S=180,
W=270), and elevation to degrees above the stated local horizon. Record whether
elevation is geometric or includes refraction. Do not confuse elevation angle
with observer height. Zenith azimuth can be undefined; preserve that rather than
inventing a direction. Terrain, local horizon masks, refraction, and an elevated
observer's horizon convention should come from the astronomy tool/model.
At a geographic pole, document the azimuth reference convention or leave it undefined.

Distinguish **above the chosen horizon** from **optically visible**. For an
unspecified geometric check, use an explicitly stated 0° local-horizontal
threshold with no terrain/refraction model. Optical satellite visibility also
needs illumination and observer darkness/brightness criteria; clouds, brightness,
and obstruction can remain unknown. An eclipsed satellite can be above the
horizon. Do not call a purely geometric result a guaranteed naked-eye sighting.
For an elevated observer's Earth-limb visibility, use that geometric criterion
explicitly rather than silently treating the 0° tangent plane as the physical limb.

Align target and observer times using the existing tools. Prefer evaluating a
moving observer at each event-search instant; fixed-observer rise/set routines
are not automatically valid for a moving route. Do not silently use nearest-time
joins or interpolate across gaps. When interpolation is justified, handle the
longitude seam/poles and interpolate height separately, reporting the method.
Refine horizon crossings/peaks with the astronomy tools when precision is needed.
If only coarse samples are available, report sampled-above-threshold intervals,
clipped endpoints, and cadence uncertainty; short passes may occur between samples.

## Deliver the answer

Give a compact UTC table with event/interval ID, time, observer place context,
original coordinates/height, **sky** azimuth/elevation, and visibility criterion
or status. For each window include start/end and peak time/elevation with place
context at those instants. Label sample-only peaks, interpolated positions,
open/clipped intervals, gaps, and unknown optical conditions. A useful sentence is:
“At [UTC], near [reference and surface distance], look [sky azimuth] at
[elevation] above [specified horizon]; [geometric/optical qualification].”

Include dataset ID (and hash in machine-readable output), orbit/ephemeris source
and epoch when relevant, time/height/frame conventions, cadence, and the assumptions
that materially affect the result. Preserve available GeoNames/Natural Earth
attribution. Export original sample IDs and computed geometry with the place
annotations so the result can be checked. If required astronomy tools or inputs
are absent, provide the geographic annotation that is possible and identify the
missing calculation; do not invent az/el or event visibility.

Primary references for the astronomy-tool adapter, when needed:
[Astropy satellite frames](https://docs.astropy.org/en/stable/coordinates/satellites.html),
[Astropy AltAz conventions](https://docs.astropy.org/en/stable/api/astropy.coordinates.AltAz.html),
[ESA ECEF/ENU transform](https://gssc.esa.int/navipedia/index.php/Transformations_between_ECEF_and_ENU_coordinates),
[Skyfield satellite observation and illumination](https://rhodesmill.org/skyfield/earth-satellites.html).
