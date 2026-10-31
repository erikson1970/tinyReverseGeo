"""Fetch/select GeoNames references and build an offline TinyGeo application bundle."""

import argparse
import ast
import base64
from collections import Counter
import gzip
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import platform
import sys
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tools.geodb import (ALGORITHM, ATTRIBUTION, BASE_URL, LICENSE_URL, Candidate, admin_regions,
                        canonical_json, collect_candidates, coverage_report, fetch_source,
                        local_source, select_candidates)
from tinygeo import Gazetteer
from tinygeo.lookup import SETTLEMENTS
from tools.radii import density_config, estimate_radius
from tools.launcher import wrap_launcher

PROFILES = {"pythonista": {"python_min": "3.10", "scan_pairs": 65_536},
            "python": {"python_min": "3.10", "scan_pairs": 262_144}}
SOURCES = ("cities500", "cities1000", "cities5000", "cities15000", "allCountries")


def compressed_json(value):
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode="wb", filename="", mtime=0, compresslevel=9) as out:
        out.write(canonical_json(value))
    return stream.getvalue()


def runtime_source(profile):
    """Specialize the shared runtime's memory budget without maintaining a fork."""
    tree = ast.parse((ROOT / "src/tinygeo/lookup.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_MAX_SCAN_PAIRS" for t in node.targets):
            node.value = ast.Constant(profile["scan_pairs"])
    return tree


def package_payload(data_gz, layout, profile):
    runtime = runtime_source(profile)
    data_code = (ROOT / "src/tinygeo/data.py").read_text(encoding="utf-8")
    extra = {name: (ROOT / f"src/tinygeo/{name}.py").read_text(encoding="utf-8")
             for name in ("track", "motion")}
    if layout == "external":
        return {"tinygeo/__init__.py": (ROOT / "src/tinygeo/__init__.py").read_bytes(),
                "tinygeo/lookup.py": (ast.unparse(runtime) + "\n").encode("utf-8"),
                "tinygeo/data.py": data_code.encode("utf-8"),
                **{f"tinygeo/{name}.py": code.encode("utf-8") for name, code in extra.items()},
                "tinygeo/resources/geodb.json.gz": data_gz}
    data_tree = ast.parse(data_code)
    replacement = ast.parse("def load_default_dataset():\n    return decode_dataset(base64.b64decode(_EMBEDDED_DATA))").body[0]
    data_tree.body = [replacement if isinstance(n, ast.FunctionDef) and n.name == "load_default_dataset" else n
                      for n in data_tree.body]
    runtime.body = [n for n in runtime.body
                    if not isinstance(n, ast.ImportFrom) or n.level != 1 or n.module != "data"]
    encoded = base64.b64encode(data_gz).decode("ascii")
    chunks = "\n".join(repr(encoded[i:i + 100]) for i in range(0, len(encoded), 100))
    data_attribution = json.loads(gzip.decompress(data_gz))["attribution"]
    header = (f'"""TinyGeo single-file runtime. {data_attribution} License: {LICENSE_URL}"""\n'
              f"import base64\n_EMBEDDED_DATA = (\n{chunks}\n)\n")
    public_api = ast.parse((ROOT / "src/tinygeo/__init__.py").read_text(encoding="utf-8"))
    exports = next(n for n in public_api.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets))
    extra_code = []
    for code in extra.values():
        tree = ast.parse(code)
        tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom) or not n.level]
        extra_code.append(ast.unparse(tree))
    module = header + "\n".join([ast.unparse(data_tree), ast.unparse(runtime), *extra_code, ast.unparse(exports)]) + "\n"
    return {"tinygeo.py": module.encode("utf-8")}


SMOKE = '''"""Offline smoke test for a generated TinyGeo build."""
import json
import tinygeo
print("TinyGeo runtime:", tinygeo.__file__)
from tinygeo import dataset_info, locate, locate_many, describe_track, estimate_motion, project_motion

info = dataset_info()
result = locate(42.36, -71.06, k=3)
nearest = result["nearest"]
assert nearest is not None
assert locate(nearest["lat"], nearest["lon"])["nearest"]["distance_km"] < 1e-7
assert len(locate_many([[0, 180], [90, 0], [-90, 0]])) == 3
assert result["synthetic"] is False
assert len(describe_track([[0, 0]]*3, [0, 10, 20])) == 1
motion = estimate_motion([[0, 0]]*3, [0, 10, 20])
assert motion["status"] == "stationary"
assert len(project_motion(motion)) == 3
print("Dataset:", info["dataset_id"], "| References:", info["record_count"])
print(result["description"])
print(json.dumps(result["neighbors"], indent=2, ensure_ascii=False))
print("PASS: offline runtime, package data, nearest point, batch, antimeridian and poles.")
'''


def write_bundle(output, dataset, report, lock, *, target, layout):
    profile = PROFILES[target]
    compressed = compressed_json(dataset)
    payload = package_payload(compressed, layout, profile)
    runtime_bytes = sum(len(value) for value in payload.values())
    report["sizes"] = {"dataset_json_bytes": len(canonical_json(dataset)),
                       "dataset_gzip_bytes": len(compressed), "runtime_payload_bytes": runtime_bytes,
                       "gzip_target_bytes": 1_000_000, "gzip_target_met": len(compressed) <= 1_000_000}
    payload["smoke_test.py"] = wrap_launcher(SMOKE)
    payload["benchmark_deployed.py"] = wrap_launcher((ROOT / "tools/benchmark_lookup.py").read_bytes())
    payload["pythonista_track.py"] = wrap_launcher((ROOT / "examples/pythonista_track.py").read_bytes())
    payload["TRACKING.md"] = (ROOT / "docs/tracking.md").read_bytes()
    instructions = (f"TinyGeo deployment: {target}, {layout}, {dataset['record_count']} references.\n\n"
                    "Requires Python >=3.10 and NumPy >=1.21.6; no SciPy/geopy or network at runtime.\n"
                    "Extract this ZIP into an application/script folder, preserving all paths.\n"
                    "In Pythonista copy the extracted folder into the script library.\n"
                    "Run smoke_test.py, then use: from tinygeo import locate, locate_many, dataset_info\n"
                    "For an embedded build, tinygeo.py alone contains runtime and compressed data.\n"
                    "For an external build, copy the entire tinygeo/ directory.\n\n"
                    "Nearby references are approximate context; their country/region is not query containment.\n"
                    "The source set and feature coverage are described in build-report.json.\n"
                    "Run pythonista_track.py for the foreground track prototype; see TRACKING.md.\n"
                    "The UI, battery use, backgrounding and screen-lock behavior need physical-device testing.\n\n"
                    + dataset["attribution"] + "\n" + LICENSE_URL + "\n")
    payload["README.txt"] = instructions.encode("utf-8")
    payload["LICENSE"] = (ROOT / "LICENSE").read_bytes()
    payload["DATA-LICENSE.txt"] = (dataset["attribution"] + "\n" + LICENSE_URL + "\n"
                                  + BASE_URL + "readme.txt\n").encode("utf-8")
    payload["source-lock.json"] = canonical_json(lock)
    payload["build-report.json"] = canonical_json(report)
    manifest = {"format_version": 1, "target": target, "layout": layout,
                "profile": profile, "runtime_dependencies": ["numpy>=1.21.6"],
                "dataset_id": dataset["dataset_id"], "record_count": dataset["record_count"],
                "feature_count": dataset.get("feature_count", dataset["record_count"]),
                "dataset_sha256": sha256(compressed).hexdigest(),
                "files_sha256": {name: sha256(content).hexdigest() for name, content in sorted(payload.items())}}
    payload["manifest.json"] = canonical_json(manifest)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / "tinygeo.zip"
    temporary = output / "tinygeo.zip.tmp"
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in sorted(payload.items()):
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content, compress_type=ZIP_DEFLATED, compresslevel=9)
        temporary.replace(archive_path)
    finally:
        temporary.unlink(missing_ok=True)
    for name in ("manifest.json", "build-report.json", "source-lock.json"):
        (output / name).write_bytes(payload[name])
    (output / "dataset.json.gz").write_bytes(compressed)
    if layout == "embedded":
        (output / "tinygeo.py").write_bytes(payload["tinygeo.py"])
    return archive_path, manifest


def deploy(args):
    if args.points < 1:
        raise ValueError("--points must be a positive integer")
    density = density_config(args.density_config)
    if not (all(math.isfinite(v) for v in (args.radius_min, args.radius_max, args.radius_fallback, args.physical_fraction))
            and 0 <= args.radius_min <= args.radius_max <= 20000
            and 0 <= args.radius_fallback <= args.radius_max and 0 <= args.physical_fraction <= 1):
        raise ValueError("invalid radius bounds/fallback or physical fraction")
    if args.refresh and (args.offline or args.lock):
        raise ValueError("--refresh cannot be combined with --offline or --lock")
    lock = json.loads(args.lock.read_text(encoding="utf-8")) if args.lock else None
    if lock is not None and (not isinstance(lock, dict) or lock.get("format_version") != 1
                             or not isinstance(lock.get("sources"), list)
                             or not isinstance(lock.get("curated_sources", []), list)):
        raise ValueError("unsupported source-lock format")
    if lock and bool(lock.get("curated_sources")) != bool(args.places):
        raise ValueError("pass the same --places file used by the source lock")
    if lock and args.natural_earth and not lock.get("physical_sources"):
        raise ValueError("the source lock has no Natural Earth inputs; build without --lock to add them")
    use_physical = args.natural_earth or bool(lock and lock.get("physical_sources"))
    try:
        from scipy.spatial import cKDTree
    except ImportError as exc:
        raise ValueError("install build dependencies first: python -m pip install -e '.[builder]'") from exc
    snapshots, paths = [], []
    regions_path = args.admin1
    if args.input:
        if args.sources or args.countries:
            raise ValueError("use either --input snapshots or downloadable --sources/--countries")
        for path in sorted(args.input):
            source, info = local_source(path)
            paths.append(source)
            snapshots.append(info)
        if regions_path:
            _, info = local_source(regions_path)
            info["role"] = "admin1"
            snapshots.append(info)
        if lock and snapshots != lock["sources"]:
            raise ValueError("local snapshots do not match the source lock")
    else:
        if args.admin1:
            raise ValueError("--admin1 is for local --input snapshots")
        if lock:
            if args.sources or args.countries:
                raise ValueError("a source lock supplies its own source list")
            names = [info["name"] for info in lock["sources"]]
            expected = {info["name"]: info for info in lock["sources"]}
        else:
            countries = args.countries or []
            if any(len(c) != 2 or not c.isascii() or not c.isalpha() for c in countries):
                raise ValueError("country extracts require two-letter country codes")
            names = sorted(set([s + ".zip" for s in (args.sources or ([] if countries else ["cities500"]))]
                               + [c.upper() + ".zip" for c in countries]))
            names += ["admin1CodesASCII.txt", "readme.txt"]
            expected = {}
        for name in names:
            # Only documented dump filenames are accepted from lock files.
            allowed = {s + ".zip" for s in SOURCES} | {"admin1CodesASCII.txt", "readme.txt"}
            country_zip = len(name) == 6 and name[:2].isupper() and name[:2].isascii() and name.endswith(".zip")
            if name not in allowed and not country_zip:
                raise ValueError(f"unsupported locked source: {name}")
            print(f"Source: {name}", flush=True)
            path, info = fetch_source(name, args.cache, offline=args.offline,
                                      refresh=args.refresh, locked=expected.get(name))
            snapshots.append(info)
            if name == "admin1CodesASCII.txt":
                regions_path = path
            elif name.endswith(".zip"):
                paths.append(path)
    print("Ingesting and selecting references...", flush=True)
    candidates, ingest = collect_candidates(paths, args.points, admin_regions(regions_path))
    curated, curated_sources = [], []
    if args.places:
        from tools.geopy_places import resolve_places
        curated, curated_sources = resolve_places(args.places, args.cache, offline=args.offline,
                                                  refresh=args.refresh, locked=lock.get("curated_sources", ()) if lock else ())
        by_id = {c.source_id: c for c in candidates}
        by_id.update({c.source_id: c for c in curated})
        candidates = sorted(by_id.values(), key=lambda c: c.source_id)
    physical_sources, physical_report, physical_selected = [], {}, []
    if use_physical:
        from tools.natural_earth import load_candidates
        physical, physical_sources, physical_report = load_candidates(
            args.cache.parent / "natural_earth", offline=args.offline, refresh=args.refresh,
            locked=lock.get("physical_sources", ()) if lock else (), spacing=args.physical_spacing,
            pattern=args.physical_grid, coarsening=args.ocean_coarsening,
            ocean_radius_max=args.ocean_radius_max, detail=candidates)
        budget = min(len(physical), int(args.points * args.physical_fraction), args.points-len({c.source_id for c in curated}))
        if budget > 0:
            physical_selected = select_candidates(physical, budget)
        candidates += physical_selected
    pins = [c.source_id for c in curated + physical_selected]
    selected = select_candidates(candidates, args.points, pinned=pins)
    records = [estimate_radius({**c.record, "lat": round(c.record["lat"], args.coordinate_decimals),
                "lon": round(c.record["lon"], args.coordinate_decimals)}, density,
                minimum=args.radius_min, maximum=args.radius_max, fallback=args.radius_fallback,
                point_radii=args.point_radii) for c in selected]
    for record in records:
        if record["feature_type"] not in SETTLEMENTS:
            record["country_code"] = None
            record["region"] = None
    # Validate the exact quantized representation that the deployed runtime will read.
    from tinygeo import Reference
    Gazetteer([Reference(**record) for record in records])
    selection = {"algorithm": ALGORITHM, "points": args.points,
                 "coordinate_decimals": args.coordinate_decimals, "salience_seed_fraction": .2,
                 "group_targets": {"settlement": .7, "natural": .2, "landmark": .1},
                 "coverage_advantage_cap_km": 1000,
                 "administrative_metadata": "settlements_only",
                 "settlement_radii": {"density": density, "minimum_km": args.radius_min,
                    "maximum_km": args.radius_max, "fallback_km": args.radius_fallback, "point_only": args.point_radii},
                 "physical": {"enabled": use_physical, "fraction": args.physical_fraction,
                              **physical_report.get("grid", {})}}
    identity = sha256(canonical_json({"records": records, "selection": selection})).hexdigest()
    feature_count = len({r.get("feature_id") or r["source_id"] for r in records})
    attribution = ATTRIBUTION
    if use_physical:
        from tools.natural_earth import ATTRIBUTION as physical_attribution
        attribution += " " + physical_attribution
    dataset = {"schema_version": 2, "dataset_id": "tinygeo-" + identity[:20],
               "kind": "geographic", "record_count": len(records), "records": records,
               "feature_count": feature_count, "selection": selection,
               "sources": snapshots + curated_sources + physical_sources,
               "attribution": attribution, "license": "CC-BY-4.0", "license_url": LICENSE_URL}
    new_lock = {"format_version": 1, "sources": snapshots, "curated_sources": curated_sources,
                "physical_sources": physical_sources}
    from importlib.metadata import version
    report = {"dataset_id": dataset["dataset_id"], "record_count": len(records),
              "feature_count": feature_count, "selection": selection, "ingestion": ingest,
              "physical_ingestion": physical_report,
              "pinned_places": len({c.source_id for c in curated}),
              "feature_counts": dict(sorted(Counter(r["feature_type"] for r in records).items())),
              "country_counts": dict(sorted(Counter(
                  (r["country_code"] or "unknown") if r["feature_type"] in SETTLEMENTS
                  else "not_applicable" for r in records).items())),
              "coverage": coverage_report([Candidate(r, c.score, c.capital) for r, c in zip(records, selected)], candidates),
              "coverage_notes": ["Global samples are area-uniform, including ocean.",
                                 "Settlement sample is from the retained candidate pool, not population-weighted.",
                                 "Candidate feature availability limits requested group allocations."],
              "builder_versions": {"python": platform.python_version(), "numpy": version("numpy"), "scipy": version("scipy")},
              "builder_source_sha256": {name: sha256((ROOT / name).read_bytes()).hexdigest()
                                         for name in ("tools/geodb.py", "tools/deploy.py", "tools/geopy_places.py", "tools/radii.py", "tools/natural_earth.py", "tools/launcher.py")}}
    if use_physical:
        report["builder_versions"].update({"shapely": version("shapely"), "pyshp": version("pyshp")})
    report["coverage_notes"].append("Coverage-distance diagnostics measure reference centers, not fuzzy circle membership.")
    if not any(r["feature_type"] not in {"city", "town", "remote_settlement"} for r in records):
        report["coverage_notes"].append("Settlement-only build: choose country/allCountries sources for natural and cultural features.")
    output = args.output or ROOT / f"dist/tinygeo-{args.target}-{args.layout}-{args.points}"
    archive, manifest = write_bundle(output, dataset, report, new_lock, target=args.target, layout=args.layout)
    print(f"Built {len(records):,} references: {archive}")
    print(f"Compressed database: {report['sizes']['dataset_gzip_bytes']:,} bytes; ZIP: {archive.stat().st_size:,} bytes")
    print(f"Dataset: {manifest['dataset_id']}; ZIP SHA-256: {sha256(archive.read_bytes()).hexdigest()}")
    print("Feature counts:", json.dumps(report["feature_counts"], ensure_ascii=False))
    return archive, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=PROFILES, default="pythonista")
    parser.add_argument("--layout", choices=("external", "embedded"), default="external")
    parser.add_argument("--points", type=int, default=10_000)
    parser.add_argument("--sources", nargs="+", choices=SOURCES)
    parser.add_argument("--countries", nargs="+")
    parser.add_argument("--input", type=Path, nargs="+", help="local GeoNames text/ZIP snapshots")
    parser.add_argument("--admin1", type=Path, help="local admin1CodesASCII.txt, with --input")
    parser.add_argument("--places", type=Path, help="small curated GeoNames query list resolved through geopy")
    parser.add_argument("--natural-earth", action="store_true", help="add Natural Earth physical references and coverage circles")
    parser.add_argument("--physical-fraction", type=float, default=.35, help="maximum fraction of rows reserved for physical sources")
    parser.add_argument("--physical-spacing", type=float, default=5.0, help="physical polygon sample spacing, degrees (1–15)")
    parser.add_argument("--physical-grid", choices=("staggered", "rectangular"), default="staggered")
    parser.add_argument("--ocean-coarsening", action=argparse.BooleanOptionalAction, default=None,
                        help="merge remote ocean circles (default on for new builds; off for older locks without a coastline)")
    parser.add_argument("--ocean-radius-max", type=float, default=1200.0, help="maximum merged ocean-circle radius, km (up to 3000)")
    parser.add_argument("--density-config", type=Path, help="JSON urban-density assumptions with country/region overrides")
    parser.add_argument("--radius-min", type=float, default=.5)
    parser.add_argument("--radius-max", type=float, default=40.0)
    parser.add_argument("--radius-fallback", type=float, default=1.0)
    parser.add_argument("--point-radii", action="store_true", help="zero settlement radii for point-only comparisons")
    parser.add_argument("--cache", type=Path, default=ROOT / "data_sources/geonames")
    parser.add_argument("--lock", type=Path, help="source-lock.json from an earlier build")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="explicitly fetch new upstream snapshots")
    parser.add_argument("--coordinate-decimals", type=int, choices=range(3, 8), default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        return deploy(args)
    except (ValueError, OSError, ImportError, BadZipFile) as exc:
        parser.exit(2, f"Build failed: {exc}\n")


if __name__ == "__main__":
    main()
