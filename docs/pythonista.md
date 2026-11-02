# Pythonista setup and smoke test

For real GeoNames application builds with configurable point count and embedded
or external data, use the [deployer guide](deployer.md). The instructions below
refer to the original six-point synthetic demo bundle.

The shared lookup runtime targets **Python 3.10+ with NumPy >=1.21.6**. SciPy is
used only by optional desktop database-building tools; it is not needed on iOS.
Pythonista's official [platform page](https://omz-software.com/pythonista/) documents
Python 3.10 and bundled NumPy. Confirm the versions printed on your device.

The runtime is tested on desktop Python 3.10 with NumPy 1.21.6 and Python 3.13
with NumPy 2.5.4. On **2026-10-31**, the user confirmed basic lookup execution on an
iPhone running Pythonista and described it as seemingly instantaneous without
`%timeit`. Exact device/software versions and measured timings were not supplied;
the full device benchmark and release test matrix remain pending. The bundled
six-point database is synthetic. Use the real-data deployer for useful coverage
and the new foreground companion prototype described in [tracking.md](tracking.md).

Later on 2026-10-31, the user supplied a complete `PASS` output for the current
real-data smoke test on Pythonista: dataset `tinygeo-c5f36386a63bde416304`, 10,000
records. The printed runtime path identified the newly extracted bundle, confirming
recovery from the older nested-package import. Fuzzy lookup, batch/polar/dateline
checks, basic track summaries, and stationary projections completed successfully.
The smoke test does not request location access. Live UI/GPS behavior, exact
Pythonista/Python/NumPy versions, and device timing/battery measurements remain
unverified.

On 2026-11-02 the view-owned update loop subsequently passed a short foreground
trial: 57 fixes over approximately 117 seconds, successful moving/stationary
forecasts, and a clean Stop. Device metadata identified Python 3.10.4 and NumPy
1.22.3. Deployment now uses that scheduler with routine diagnostics/native fault
capture off, while retaining local history and Python error logs. See the
[device results and remaining checks](tracking.md). Longer recording, screen lock,
background behavior, battery use, and broad device coverage remain unverified.

## Make a portable bundle on desktop

From the repository root:

```sh
python3 tools/build_pythonista.py
```

This uses only the standard library and writes `dist/tinygeo-pythonista-demo.zip`.
It includes the `tinygeo/` package and its local resources, `pythonista_smoke.py`,
`benchmark_lookup.py`, this guide, licensing, and a SHA-256 manifest. No NumPy or SciPy binary is included;
the runtime uses Pythonista's own NumPy. The ZIP has deterministic entry metadata
and can be rebuilt without network access.

## Run on iPhone/iPad

1. Transfer the ZIP through Files, AirDrop, or another file-transfer method, extract
   it, and copy the extracted folder into Pythonista's script library. Preserve
   the directory structure: `tinygeo/` must sit beside `pythonista_smoke.py`.
2. Open `pythonista_smoke.py` in Pythonista and run it. It prints interpreter/NumPy
   versions and checks imports, packaged data, single/batch lookups, filters,
   distances, bearings, poles, and the antimeridian. Expect a final `PASS` line.
3. Create other scripts in the same folder and use `from tinygeo import locate,
   locate_many`. Do not install the desktop notebook/development extras on iOS.
4. To measure on-device speed, run `benchmark_lookup.py`. Its defaults generate
   10,000 synthetic references and report single-lookup and batch timings. Save
   that output together with the versions printed by the smoke test.

Alternatively copy `src/tinygeo/` and `examples/pythonista_smoke.py` into a single
folder yourself. No pip installation or network is required to run the scripts.

The smoke test reports whether Pythonista's `location` module is available but
does not start updates or request location access. Current real-data bundles also
include `pythonista_track.py` with local history and 15-minute/1-hour/2-hour
extrapolations. That new UI still needs a physical-device trial; background and
screen-lock recording are not promised. See [tracking.md](tracking.md).

## Why no runtime tree?

A NumPy scan ranks cached 3D unit-sphere reference vectors by squared chord
distance. This preserves spherical nearest-neighbor ordering across the dateline
and poles. It scans all eligible records for each query; batches use bounded
chunks. This keeps deployment small and simple for approximately 10,000 references
and device updates below 10 Hz. On-device timing remains to be measured.

SciPy's `cKDTree` is available through the desktop `builder` extra for
coverage-aware record selection; the exported database contains portable records,
not a serialized tree.
