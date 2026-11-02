# Track history and conditional forecasts

The shared NumPy-only runtime now supports desktop replay and a foreground
Pythonista companion prototype. Its acquisition/controller is tested with a
simulated provider. **A short live iOS trial passed after replacing the delayed
callback loop with view-owned updates (see the device results below).**
On 2026-10-31 the user reported a successful Pythonista smoke test of the real
10,000-record dataset `tinygeo-c5f36386a63bde416304`, after the launcher import fix.
That test covered package/data loading, fuzzy lookup, batch/polar/dateline cases,
and basic track-summary/stationary-forecast assertions. On 2026-11-02 the revised
UI recorded 57 fixes over approximately 117 seconds, produced motion forecasts,
and stopped cleanly. Longer recordings, battery use, and background behavior
remain unverified. The smoke test's Boston result uses fixed coordinates.

## Pythonista quick start

For the current phone trial, use
`dist/tinygeo-staggered-pythonista-external-10000/tinygeo.zip` (dataset
`tinygeo-c5f36386a63bde416304`, 10,000 records / 7,832 features). This is the
recommended full-world external-data bundle; older point/physical builds and
small regional experiments remain in `dist/` as checkpoints.

Extract the **whole ZIP**, keeping `tinygeo/` beside `smoke_test.py` and
`pythonista_track.py`. Run `smoke_test.py` first, then `pythonista_track.py`.

The generated launchers select the runtime beside their own file, independently
of the working directory, and clear cached `tinygeo` imports before loading it.
This supports repeated runs in a persistent interpreter and side-by-side older
bundles. The smoke test prints the loaded runtime path. A missing adjacent runtime
produces an extraction/layout error instead of falling back to another installation.

If an older launcher imports `tinygeo.tinygeo`, or a traceback names a different
bundle folder, extract a current full ZIP into a fresh folder and use its scripts
and runtime together. Keep the normal `from tinygeo import ...` imports. The outer
extraction folder can contain the inner `tinygeo/` package; extra dotted imports
can select an old nested package and do not update its API. Preserve the filename
`tinygeo/__init__.py`, including the double underscores.

Build/extract a current real-data deployment following the deployer guide, then
run `pythonista_track.py` beside its `tinygeo/` package or embedded `tinygeo.py`.
Tap **Start** and allow location access. The view shows the latest fix/time/age,
nearby references and bearings, observed context history, recent speed/course,
and extrapolated context 15 minutes, 1 hour, and 2 hours from the fix time.
Tap **Stop**, or close the view, to stop location updates and close the journal.

Normal deployed scripts use `View.update` at two-second intervals with motion
enabled. `DIAGNOSTICS = False` and `CAPTURE_NATIVE_FAULTS = False` keep verbose
checkpoints and native signal handlers off. Fixes, stable reference labels, final
summaries, and Python exception tracebacks remain saved. The manifest's `companion`
section identifies these defaults. An app-only `pythonista_track.py` is also
written beside each build ZIP for copying into an existing installation.

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

## Device exit investigation (2026-10-31)

The user subsequently ran the foreground UI on iPhone. Start produces position
reports and an initial lookup; quick Start/Stop and local CSV/JSONL output work,
but continued recording returns to the iPhone Home Screen after a few updates.
No Python traceback was visible. The preserved editor state does not establish
whether the process survived or restored state after termination. The cause is
unknown; zero speed is explicitly handled and stationary replay tests pass.

For this investigation, replace only the phone's `pythonista_track.py` with the
contents of `dist/pythonista_track_debug.py`, in the existing extracted folder.
The standalone diagnostic script includes the adjacent-runtime import fix and
uses the existing library/database. No new ZIP or data transfer is required.
Its source is maintained in `examples/pythonista_track.py`; build the standalone
debug file with `tools.launcher.tracker_launcher(source, diagnostics=True)`.
The normal deployed variant uses the same source with diagnostics=False.

The diagnostic version writes flushed `diagnostic` checkpoints to each session's
JSONL journal around location reads, track/lookup calls, motion estimation and
projection, UI text rendering, and timer scheduling. Records include sequence,
thread ID, fix data, motion state, and Python/NumPy configuration. Replay and CSV
export ignore diagnostic/error rows. Python exceptions include full tracebacks
in the journal and `track_history/errors.log`, including failures after the
normal journal handle has closed. A render failure uses a simple error view
instead of recursively calling the failing renderer. Cleanup errors are logged
without hiding the original polling failure.

The script attempts optional native fault capture in `native-fault.log`, keeping
the stream open until its own handler is disabled. It leaves an existing handler's
destination unchanged and logs that fact. Availability depends on the interpreter;
a forced iOS kill cannot execute a Python handler. A log with no exception may
therefore still have useful last-started/last-completed checkpoints, but those
checkpoints do not prove the cause. See the
[Python fault-handler documentation](https://docs.python.org/3.10/library/faulthandler.html).

First reproduce with `ENABLE_MOTION = True`. If it exits, retain that journal and
try `ENABLE_MOTION = False` near the top of the diagnostic script. That keeps
capture, lookup, UI, and CSV working while bypassing the moving/stationary fit and
forecasts. Compare the last checkpoints and retain any `errors.log` or native
fault output. `DIAGNOSTICS = False` disables routine checkpoint volume after the
investigation; exception logging remains available.

### Native log finding and scheduler change (2026-11-02)

The supplied `native-fault.log` records a segmentation fault in the delayed
lambda at line 479 of the earlier diagnostic script. The JSONL journal contains
three complete capture/lookup/render cycles, followed by the last successful
`ui.timer.schedule.end`. The fault stack has no next `tick` frame/checkpoint.
This localizes the observed failure to the delayed-callback boundary, although
it does not prove the underlying native defect originated there.

All three motion estimates were `insufficient_history`; the third had three
samples spanning 7.393 seconds, below the ten-second fit threshold. No forecasts
were generated. The velocity fit and stationary normalization were therefore not
executing at this failure. Device metadata reports Python 3.10.4 and NumPy 1.22.3.
The source logs remain in ignored `dist/pythonista_debug/`; private fix coordinates
are not copied into repository documentation or tests.

The revised standalone script uses the documented `View.update()` callback with
`update_interval = 2.0`, replacing self-rescheduling `ui.delay` closures. Stop,
close, error handling, and final cleanup set the interval to zero. Reentrant ticks
and stale generations are ignored. Diagnostics remain enabled, and new metadata
identifies `diagnostic_version: 2` / `ui_scheduler: "View.update"`. Keep motion
enabled for the next comparison. Desktop tests cover repeated updates through
stationary forecasts, rapid Start/Stop, restart/close, and reentrant callbacks;
the device results below record the subsequent retry.

Copy the updated `dist/pythonista_track_debug.py` contents over the phone script;
the existing library, database, and ZIP do not need replacement.
Reference: [Pythonista View.update](https://omz-software.com/pythonista/docs-3.4/py3/ios/ui.html#ui.View.update).

### Successful retry and deployment defaults (2026-11-02)

The subsequent device journal reports `View.update`, diagnostic version 2, Python
3.10.4 / NumPy 1.22.3, and motion enabled. It contains 57 accepted fixes spanning
116.785 seconds, 58 complete update cycles, and a normal Stop/provider-stop/final
summary. There are no Python error records and no new native faults in this run.
The shared native log is append-only and still contains the earlier crash.

Motion results comprise four insufficient-history, two moving, and 52 stationary
estimates. Fifty-four cycles produced all three forecast horizons. Early moving
estimates were approximately 1.23 and 0.87 m/s while reported accuracy was settling;
these could reflect startup jitter or motion. This short trial does not establish
a new stationary threshold, so the deployment keeps the existing configurable
motion model and thresholds.

Promote the view-owned scheduler and error containment into both deployment
layouts. Keep verbose/native diagnostics available through the standalone debug
variant. Preserve the real dataset bytes and core wheel: the validated change is
in the application launcher. A longer field trial is still needed before claiming
continuous/background recording or battery performance. Private device coordinates
and raw journals are excluded from source control and distributed bundles.
