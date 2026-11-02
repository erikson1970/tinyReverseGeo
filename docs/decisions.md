# Design decisions and checkpoints

This log records choices made during the autonomous development session on
2026-10-31. Each entry identifies the alternative to revisit if field results
suggest a different tradeoff. Git commits provide implementation checkpoints.

## D001 — Radii are fuzzy proximity zones

Accepted user direction: overlap is normal. Radius-aware lookup ranks
`max(0, spherical_center_distance - radius_km)`, breaking ties by center distance.
It returns one match per logical `feature_id`, with both center and area distances.
`distance_km` keeps its existing center-distance meaning. `distance_mode="center"`
provides the old ranking for comparisons. Radius zero preserves point behavior.
No circle membership verifies administrative containment.

Revisit: more complicated overlap handling or fitted urban footprints only if
actual examples show these simple proximity zones are misleading.

## D002 — Country context comes from settlements

Country and administrative labels attach only to city/town/remote-settlement
references. Do not generate country/state/province coverage features, including
an “Iceland Region”. Source metadata stays in cached source snapshots. Natural
Earth island/continent label polygons are excluded from the initial area adapter
to avoid introducing country-like coverage through island-country names.

Revisit: independently useful small-island point references remain allowed;
country-equivalent area features require an explicit change to this policy.

## D003 — Density assumptions are configurable, not observed statistics

Initial settlement radius uses `sqrt(population / (pi * assumed_density))`.
The default density is a declared **1,000 people/km² assumption**, not a measured
regional density. A JSON configuration may override it by source country/admin1
region, with a source note. Clamp the resulting radius to 0.5–40 km by default.
Unknown population uses a configurable 1 km fallback. Store the method and all
assumptions in the dataset. `--point-radii` restores zero settlement radii.

Revisit: tune defaults against field examples; replace assumptions with GHSL or
other density/footprint data when the gain warrants the input size and complexity.

## D004 — Physical areas use overlapping sampled circles

Use Natural Earth's public-domain physical-region/marine/elevation datasets for
an initial recognizable physical-feature collection. Sample area polygons on a
5° center grid with an additional interior representative point (now staggered;
see D006). Grid circles use
the cell's spherical half-diagonal; small polygons can use a smaller extent.
These are broad, approximate proximity zones, with coastal overlap allowed.
Shared feature IDs group all support circles for one physical feature.
Selection can thin support circles, so this does not guarantee full region
coverage. Mountain point references use a declared 5 km proximity assumption;
this also absorbs some source-coordinate uncertainty and is not a measured footprint.

Revisit: smaller grid spacing or simplified polygons if coastal errors matter.
The source polygons themselves are cartographic approximations.

## D005 — Count stored circles and logical features separately

Keep `--points` as the exact number of stored records, preserving the deployer
contract. Report `feature_count` separately because several records can support
one feature. A physical-feature build reserves at most 35% of the requested rows
for Natural Earth by default; the rest is selected from the other sources.

Revisit: expose a distinct logical-feature budget if users find stored-row budgets
less useful. Input feature availability can keep the physical allocation below
its requested fraction.

## D006 — Stagger rows and merge remote ocean circles

User direction: offset every other row by half a cell to improve coverage, then
grow circles in uninteresting ocean interiors. Use staggered latitude rows with
periodic longitude, retaining conservative spherical half-diagonal radii. Latitude
and longitude steps are reduced slightly when necessary to close the global grid.
This is a staggered rectangular grid, not an equal-area tessellation; polar rows
remain more densely sampled. Keep `--physical-grid rectangular` for comparison.

For oceans only, greedily consider centers with the most clearance first. Limit
growth to 1,200 km by default (`--ocean-radius-max`, capped at 3,000 km). Protect
coasts using a pinned Natural Earth 110m land mask, shoreline samples no more than
50 km apart, a 75 km sampling/safety allowance, and a further 10% clearance margin.
Other known feature centers and their largest radius add another growth limit.
Check concentric rings against the same source ocean polygon before merging.
Small islands missing from both the coarse land mask and the reference pool can
still be missed; these remain approximate context zones.

Remove a support circle only when spherical center distance plus its radius fits
inside the replacement circle, with a 200 m allowance for exported-coordinate
rounding. Preserve the feature ID. This preserves the input disks' modeled
coverage during merging; subsequent fixed-budget selection can still thin it.
Seas, land regions, and unmerged coastal circles retain their original radii.
The algorithm is deterministic and records before/after counts in the build report.

Backtrace: commit `903f62f` is the rectangular-grid baseline. Use
`--no-ocean-coarsening` to isolate the stagger effect. Older three-source locks
default to no coarsening because they lack the land-mask snapshot; explicit
coarsening with such a lock fails instead of fetching an unpinned input.

Revisit: adapt latitude spacing or use an equal-area mesh if polar redundancy
matters. Replace the coarse coast guard only if device/route examples justify
the added build complexity. Runtime remains a NumPy circle scan.

## D007 — Confirm track transitions without changing saved history

Share one incremental `TrackTracker` between batch replay and device capture.
Prefer nearby settlement context, then nearby physical-region context, then a
qualified distant reference. Require consecutive candidate samples, optional time
dwell, and displacement from the active segment's first fix. Start a new event
at the confirmation sample. Do not backdate its start or alter old annotations.
Use closest observed approach to the retained feature's best support circle;
neither a changing label nor a minimum sample distance proves a visit/passage.
Gaps reset pending changes; missing timestamps remain missing.

Revisit: retrospective backdating could improve edited trip reports, but must be
an explicitly separate replay/export option rather than altering live journals.
Thresholds default to 3 samples / 25 km; applications can lower the distance for
walking/driving. Time dwell requires real timestamps.

## D008 — Spherical motion, foreground capture, and JSON Lines history

Fit recent unit-sphere positions versus time, then project velocity onto the latest
fix's tangent. Extrapolate from that fix along a great circle at unchanged speed
and initial direction, for 900/3,600/7,200 seconds. Pole-safe vectors avoid a local
longitude singularity. Stationary estimates explicitly stay put; stale, noisy,
jumping, or insufficient histories yield no forecasts. A sharp turn can temporarily
invalidate the fit. This is conditional motion context, not route guidance.

Use a foreground Pythonista prototype with lazy iOS imports, provider-independent
acquisition, and Start/Stop cleanup. Accept only fresh, ordered, sufficiently
accurate fixes when accuracy is reported. Ignore duplicate/out-of-order timestamps.
Device UI/background/battery validation remains a physical-device task.

Choose append-only JSON Lines for local timestamped fixes, explicit acquisition
gaps, stable reference labels, dataset ID/settings, and final event summaries.
Flush each accepted entry. CSV exports accepted fixes; replay requires the same
dataset ID and thresholds. This needs no additional database package or compiled
extension. Incomplete final crash writes are ignored; malformed complete lines fail.
Bound the live motion window and retain events, rather than repeatedly reprocessing
an ever-growing location history.

Revisit: indexed SQLite storage for large multi-month histories or richer search,
only after confirming the target device's module support and a concrete need.

## D009 — Separate baseline geometry from dataset-selection gaps

Evaluate nearest settlement centers independently of fuzzy physical features.
Use `reverse_geocoder` 1.5.1 as requested, but inspection of the installed primary
source shows that version querying raw latitude/longitude coordinates. Add a
second spherical tree over its identical bundled CSV to distinguish geometry
artifacts (especially dateline/polar cases) from TinyGeo's smaller/different source
selection. Record package/module/data checksums and sampling seed.

The initial comparison exposes a real limitation: compact cities500-based
settlement coverage misses Antarctic research settlements present in the older
baseline, and dense settled regions have fewer local references. Keep those gaps
visible in the report rather than treating a baseline country label as containment
or inventing reference records. Physical-region context remains separately useful.

Revisit: add licensed source extracts for underrepresented regions/station types,
or adjust settlement/physical allocation after field examples. A 10,000-row budget
is an approximation, not exhaustive gazetteer coverage.

## D010 — Reallocate circle density according to regional complexity

User review identified more merge opportunities in open oceans and broad land
features such as Antarctica, alongside sparse Mediterranean sampling. Inspection
of the current 10,000-row build finds 156 Antarctic Plateau supports. The sampler
generates eight Mediterranean Sea supports and retains all eight; named sub-seas
also contribute context. This example calls for finer input sampling rather than
only changing the final selection budget.

Next optimization: compact circles within the same physical feature on land as
well as at sea, and allocate finer samples where narrow/complex shapes leave
modeled gaps. Preserve existing disk coverage when merging, retain distinct local
features, and measure coverage/spill before reallocating the freed row budget.
Land merging needs its own source-geometry guard; the current ocean coast guard
deliberately prevents growth over land. Country/admin coverage remains excluded.

Keep the current phone trial on a fixed artifact while these changes are designed.
The recommended trial bundle is `tinygeo-staggered-pythonista-external-10000`,
dataset `tinygeo-c5f36386a63bde416304`. This entry records the next optimization
work; it does not claim that land merging or adaptive refinement is implemented.

## D011 — Launchers select their adjacent runtime explicitly

The phone traceback ran a smoke script under `TinyGeo2` while importing an old
nested `tinygeo.tinygeo` package under `TinyGeo`. The outer import was a namespace
package, and the older inner package lacked the newer tracking exports. An added
dot in the import selected that older API rather than repairing bundle selection.

Generated smoke, tracking, and benchmark launchers now prepend their own folder,
clear cached `tinygeo` modules, invalidate import caches, and verify the loaded
module's file against the adjacent package/module. Both external and embedded
layouts use this bootstrap. A missing local runtime fails with an explicit layout
message; it cannot silently fall back to another installed release. The smoke test
prints the selected path before checking public API imports. Library imports in
ordinary applications remain unchanged; this reset belongs to standalone launchers.

Regression tests cover namespace nesting, another old package already in memory,
and stale code cached at the same path, for all three launchers and both layouts.
On 2026-10-31 the user reported the rebuilt smoke test passing on Pythonista with
the expected dataset `tinygeo-c5f36386a63bde416304` and 10,000 records. Its runtime
path identified the new extraction folder. This confirms the launcher recovery
for the reported device case; the live UI/GPS and other iOS behavior still need
field testing.

## D012 — Persist checkpoints for the live-device exit

The UI runs and records fixes on the phone, but continued capture returns to the
Home Screen without a visible traceback. Treat a native failure or OS termination
as a possibility, not an established diagnosis. Do not attribute this to zero
velocity without evidence: the motion code guards stationary normalization and
its stationary replay tests pass.

Add flushed JSONL checkpoints before/after location, lookup, motion, rendering,
and scheduling operations; record thread and runtime configuration. Persist Python
tracebacks independently of whether poll cleanup closed the journal. Protect UI
error rendering from recursion and preserve original failures during cleanup.
Attempt optional native-fault capture without replacing an existing handler;
forced OS termination can still leave no traceback. A motion on/off flag permits
an isolated comparison. Supply a standalone pasteable launcher with the prior
import bootstrap, leaving the phone's library/database and tested ZIP untouched.

Revisit once the device supplies its last checkpoints or a native fault report.
This is instrumentation and error containment, not a verified fix for the exit.

## D013 — Replace the delayed closure after the native fault report

On 2026-11-02 the device log supplied an actual segmentation-fault traceback
pointing to the self-rescheduling `ui.delay(lambda: self.tick(generation), 2.0)`
callback. Three complete lookup/render cycles precede the fault; motion remains
below its ten-second fitting window. Thus the observed stack/checkpoints implicate
callback dispatch, not a zero-velocity division. Earlier native memory corruption
cannot be ruled out from a Python stack alone.

Use Pythonista's view-owned `update` / `update_interval` API instead of delayed
closures. Retain immediate first acquisition, disable periodic updates on every
stop/close/error path, and prevent reentrant ticks or late Start actions on a
closed view. Keep diagnostic/native logging and identify the scheduler in session
metadata. This removes the observed callback mechanism without changing TinyGeo
lookup/motion mathematics, the dataset, or its wheel. Preserve the original logs
privately and provide a single pasteable script for the next device trial.

Backtrace: commit `660aed7` contains the instrumented delayed-loop source.
Desktop lifecycle/replay tests validate the replacement control flow; only a
device retry can establish whether it resolves this native crash.

## D014 — Promote the successful scheduler with quiet deployment defaults

The 2026-11-02 retry records 57 fixes across 116.785 seconds, 58 completed update
cycles, 54 three-horizon forecast sets, and a clean Stop. No error records or new
native faults appear. Motion was enabled throughout. Promote `View.update` to the
deployed companion while keeping the adjacent-runtime bootstrap and Python error
containment. Disable verbose checkpoints and native-fault capture by default;
continue saving fixes, event summaries, and exception tracebacks.

Generate normal and diagnostic launchers from the same maintained source with
explicit build-time flag specialization. Include scheduler/defaults in the
manifest and emit the normal script beside the ZIP for app-only phone updates.
Keep the standalone debug variant available. This promotion changes application
scripts and packaging, not core lookup/motion code or geographic data.

Two early estimates report about 1.23 and 0.87 m/s before becoming stationary.
GPS settling could contribute, but this log is not ground truth for motion
calibration. Leave thresholds unchanged pending more route/device evidence.
The short successful trial does not establish long-duration, background, screen
lock, battery, or all-device reliability. Preserve the raw logs privately.
