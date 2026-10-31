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
