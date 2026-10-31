# Lookup timing notes

Measured 2026-10-31 on this development Mac (macOS 14.6.1, ARM64). These are
desktop measurements, not Pythonista/iOS results. Records are **synthetic** and
exercise runtime scale, not geographic usefulness. The shipping dataset still
contains only six synthetic demo records.

## Reproduce

```sh
.venv/bin/python tools/benchmark_lookup.py --records 10000 --queries 200 --batch-size 1000
```

The script uses seed 42, area-uniform spherical coordinates, and five equally
represented feature types. It measures 200 warmed, complete `locate` calls,
including class-specific neighbors, distances/bearings, and dictionary formatting.
It also times a 1,000-position `locate_many` call. Setup time includes constructing
the gazetteer and its first lookup; it excludes imports and synthetic input
generation. This is a quick timing experiment, not a controlled performance study.

## Observed results with 10,000 references

| Implementation | Python / NumPy | Setup + first lookup | Warm single median | Warm single p95 | Batch of 1,000 |
|---|---|---:|---:|---:|---:|
| Previous SciPy cKDTree runtime | 3.13.7 / 2.5.4 | 10.44 ms | 0.155 ms | 0.452 ms | 0.152 s |
| Current NumPy scan | 3.13.7 / 2.5.4 | 6.47 ms | 0.260 ms | 0.369 ms | 0.298 s |
| Current NumPy scan, minimum dependency environment | 3.10.16 / 1.21.6 | 6.86 ms | 0.313 ms | 0.977 ms | 0.436 s |

Measurements vary with machine load, interpreter, NumPy, and feature distribution.
The tree gives faster median queries and higher batch throughput in this run;
the scan supports the intended low update rate without a SciPy runtime dependency.
A 10 Hz loop has 100 ms between updates. The measured scan latency leaves ample
headroom on this Mac, but **physical iOS latency and battery use remain unmeasured**.

## Tradeoff

The runtime caches unit-sphere coordinates and feature subsets. Each query scans
eligible records, ranking by squared chord distance, then uses NumPy minimum or
partial selection for nearest-*k*. Great-circle distances and bearings are
computed only for selected references. Direct vector differences avoid losing
close-point distinctions through dot-product cancellation.

Batch scans bound the number of query/reference pairs processed at once instead
of allocating a full track-by-database matrix. Output dictionaries still scale
with sample count and requested neighbors. This is O(M) work per query; very large
offline batches will be slower than a tree. If measured use requires more speed
later, revisit that requirement with device data.

SciPy remains an optional **builder-only** dependency for coverage-aware
database selection, spacing checks, and deduplication. Deployed lookup code and
the exported point database do not depend on SciPy.

## Fuzzy mixed-feature checkpoint

The new radius/group path deliberately sorts all eligible records so overlap ties
and duplicate support circles have straightforward semantics. For mixed dataset
`tinygeo-1538bc4fb376b94dfc23` (10,000 rows / 7,683 logical features), desktop
Python 3.10.16 / NumPy 1.21.6 measured:

| Measurement | Observed |
|---|---:|
| Load and first lookup | 82.91 ms |
| Warm single median | 3.616 ms |
| Warm single p95 | 3.907 ms |
| Batch of 1,000 | 3.505 s |

This is slower than the zero-radius point path and remains suitable for the
intended desktop cadence below 10 Hz. It is not a phone measurement. The user
previously reported rapid iPhone execution with the earlier point build; benchmark
the new fuzzy build using its included `benchmark_deployed.py` to assess that
device separately. Reproduce on desktop with:

```sh
.venv/bin/python tools/benchmark_lookup.py \
  --dataset dist/tinygeo-physical-pythonista-external-10000/dataset.json.gz
```
