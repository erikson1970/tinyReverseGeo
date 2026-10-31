# Track history and conditional forecasts

The shared NumPy-only runtime now supports desktop replay and a foreground
Pythonista companion prototype. Its acquisition/controller is tested with a
simulated provider. **The new iOS UI has not been exercised on a physical device.**
The earlier successful iPhone test covered the point-lookup library, not this app.

## Pythonista quick start

For the current phone trial, use
`dist/tinygeo-staggered-pythonista-external-10000/tinygeo.zip` (dataset
`tinygeo-c5f36386a63bde416304`, 10,000 records / 7,832 features). This is the
recommended full-world external-data bundle; older point/physical builds and
small regional experiments remain in `dist/` as checkpoints.

Extract the **whole ZIP**, keeping `tinygeo/` beside `smoke_test.py` and
`pythonista_track.py`. Run `smoke_test.py` first, then `pythonista_track.py`.

Build/extract a current real-data deployment following the deployer guide, then
run `pythonista_track.py` beside its `tinygeo/` package or embedded `tinygeo.py`.
Tap **Start** and allow location access. The view shows the latest fix/time/age,
nearby references and bearings, observed context history, recent speed/course,
and extrapolated context 15 minutes, 1 hour, and 2 hours from the fix time.
Tap **Stop**, or close the view, to stop location updates and close the journal.

Keep Pythonista open in the foreground. Continuous background/screen-lock
recording is not promised. Long pauses create gaps on resume. Device tests still
need to cover permission denial, app interruption, locking/unlocking, rotation,
update cadence, lookup latency, battery use, and cleanup after an error.

Accepted fixes and stable labels are saved in local `track_history/*.jsonl` files
beside the launcher. **Export CSV** writes a CSV beside that journal, including
while recording. No sharing or upload happens. Each session has a new file;
start/stop does not append a new session to an old track. Every accepted line is
flushed; a crash can still lose the final in-flight write. Replay ignores an
incomplete final line, while malformed complete lines raise an error.

The launcher uses the documented Pythonista `location.start_updates()`,
`get_location()`, and `stop_updates()` API. Coordinates and timestamp are required.
Reported accuracy is checked when available; absent optional accuracy information
is shown by a null journal value. Device speed/course are not assumed available.
Polling defaults to two seconds. Duplicate and out-of-order fixes are ignored;
missing, malformed, stale/future, or poor-accuracy fixes break continuity and clear
motion history. Repeated unchanged rejected data does not flood the journal.

Sources: [Pythonista location API](https://omz-software.com/pythonista/docs-3.4/py3/ios/location.html),
[Pythonista UI API](https://omz-software.com/pythonista/docs-3.4/py3/ios/ui.html).

## Core API

```python
from tinygeo import Gazetteer, describe_track, estimate_motion, project_motion

db = Gazetteer.from_file("dataset.json.gz")
positions = [(42.36, -71.06), (42.37, -71.05), (42.38, -71.04)]
times = [1000, 1010, 1020]  # UTC epoch seconds, not elapsed time in a live app
summary = describe_track(positions, times, gazetteer=db, mode="both",
                         min_distance_km=25, min_dwell_samples=3)
motion = estimate_motion(positions, times)
forecasts = project_motion(motion, gazetteer=db)
```

`describe_track` batches valid fixes through the gazetteer. `mode="events"`
(default) returns reference/gap segments; `"samples"` returns ordered annotations;
`"both"` returns a dictionary with both lists. Input indices and supplied timestamps
are preserved. Timestamps may be finite epoch seconds or timezone-aware datetimes;
naive datetimes and decreasing time are rejected. When omitted, times remain None.
An empty track returns empty output. Coordinates may include ignored altitude.
Invalid coordinates become gap records; `invalid="raise"` rejects them instead.

Context prefers a settlement within 50 km of its proximity zone, then a physical
region within 100 km, otherwise a qualified distant reference. These are
configurable context-selection thresholds, not verified containment. The raw
nearest context and the retained stable label are both present on each sample.
A new label must persist for `min_dwell_samples` (default 3), optional
`min_dwell_seconds` (default 0), and displacement from the active segment's first
fix of at least `min_distance_km` (default 25). Accumulated jitter does not count
as travel. Time dwell requires supplied timestamps.

Transitions start at their **confirmation sample**, so already saved annotations
never change. Gap rows reset hysteresis. Timestamp gaps exceeding
`max_gap_seconds` (default 120) are explicit records bracketing their two known
samples. Reference segments contain inclusive original sample ranges, timestamps,
sample counts, feature identity, and the closest **observed sample** to that feature.
That closest approach is not interpolated passage through, or evidence of a visit.
Overlapping area circles use the same unique-feature semantics as point lookup.

`TrackTracker(db, ...)` provides the same algorithm incrementally through
`update(position, timestamp)`. `snapshot()` copies current events; use
`retain_samples=True` to retain full annotations and `snapshot("both")` to read
both. Live acquisition uses event-only retention and a bounded recent motion
window. `break_track(reason)` resets continuity without inventing a fix/time;
its gap is anchored at the last known sample (or null indices before any fix).

## Motion assumptions

`estimate_motion` fits unit-sphere coordinates against time over the latest
contiguous valid 60-second window and projects the velocity onto the latest fix's
tangent plane. This handles longitude wrapping and polar travel without local
longitude division. Course is undefined at an exact pole; the tangent still
supports projection. At least three samples spanning ten seconds are required.
The latest position remains the forecast origin; the fit smooths velocity.

Returned statuses are `moving`, `stationary`, `insufficient_history`, `stale`, or
`unreliable`. Defaults: stationary below 0.8 m/s, reject step speeds above 400 m/s,
fit RMS residual above 100 m, or gaps above 120 seconds. `now=` enables the default
30-second age check; without it, replay evaluates age at the latest supplied fix.
Motion timestamps must be strictly increasing. A sharp turn can suppress a forecast
until enough consistent new motion enters the window. These thresholds are
configurable application assumptions, not calibrated sensor uncertainty estimates.

`project_motion` returns three forecasts by default (900, 3600, 7200 seconds),
with target timestamps, coordinates, motion status, assumption, and nearby context.
It advances along a great circle at constant ground speed and initial direction.
It does **not** model roads, flight plans, future turns, wind, or acceleration.
Stationary forecasts explicitly retain the latest point. Stale, unreliable, and
insufficient-history estimates produce no forecasts. Two-hour extrapolations can
be very wrong when motion changes; no assured arrivals or routes are implied.

## Desktop journal replay/export

The companion module imports on desktop without Pythonista modules:

```python
from pythonista_track import replay_history, export_csv

events = replay_history("track_history/track-....jsonl", gazetteer=db)
export_csv("track_history/track-....jsonl", "track.csv")
```

Replay requires the same `dataset_id` and recorded track thresholds. A journal's
metadata, fixes, explicit acquisition gaps, and final event summary make that
comparison inspectable. The database remains the existing compressed JSON format;
JSON Lines is sufficient for append-only track capture and is portable to
Pythonista. A separate pure-Python database package is unnecessary at this scale.
