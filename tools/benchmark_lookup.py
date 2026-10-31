"""Measure complete lookups on synthetic, external, or bundled data."""

import argparse
import json
from pathlib import Path
import platform
import sys
from types import SimpleNamespace
from time import perf_counter

source = Path(__file__).resolve().parents[1] / "src"
if (source / "tinygeo").is_dir():
    sys.path.insert(0, str(source))

import numpy as np

from tinygeo import Gazetteer, Reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=10_000)
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--bundled", action="store_true", default=Path(__file__).name == "benchmark_deployed.py")
    parser.add_argument("--distance-mode", choices=("area", "center"), default="area")
    args = parser.parse_args()
    if min(args.records, args.queries, args.batch_size) < 1:
        parser.error("record and query counts must be positive")
    rng = np.random.default_rng(42)

    def positions(count):
        return np.column_stack((
            np.rad2deg(np.arcsin(rng.uniform(-1, 1, count))),
            rng.uniform(-180, 180, count),
        ))

    kinds = ("city", "town", "landmark", "mountain", "island")
    records = [Reference(str(i), f"Synthetic {i}", float(lat), float(lon),
                         kinds[i % len(kinds)])
               for i, (lat, lon) in enumerate(positions(args.records))] if not (args.dataset or args.bundled) else []
    queries = positions(args.queries)
    batch = positions(args.batch_size)
    start = perf_counter()
    if args.dataset:
        db = Gazetteer.from_file(args.dataset)
        count, identity = len(db.records), db.dataset_id
    elif args.bundled:
        from tinygeo import dataset_info, locate, locate_many
        info = dataset_info()
        db = SimpleNamespace(locate=locate, locate_many=locate_many)
        count, identity = info["record_count"], info["dataset_id"]
    else:
        db = Gazetteer(records, dataset_id="synthetic-benchmark", synthetic=True)
        count, identity = len(records), db.dataset_id
    db.locate(*queries[0], distance_mode=args.distance_mode)
    setup_ms = (perf_counter() - start) * 1000
    timings = []
    for point in queries:
        start = perf_counter()
        db.locate(*point, distance_mode=args.distance_mode)
        timings.append((perf_counter() - start) * 1000)
    start = perf_counter()
    db.locate_many(batch, distance_mode=args.distance_mode)
    batch_seconds = perf_counter() - start
    try:
        import resource
        peak_rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**2 if sys.platform == "darwin" else 1024)
    except (ImportError, AttributeError, OSError):
        peak_rss_mib = None
    print(json.dumps({
        "python": platform.python_version(), "numpy": np.__version__,
        "platform": platform.platform(), "records": count, "dataset_id": identity,
        "distance_mode": args.distance_mode,
        "single_queries": args.queries, "batch_size": args.batch_size,
        "construction_and_first_lookup_ms": setup_ms,
        "warm_single_median_ms": float(np.median(timings)),
        "warm_single_p95_ms": float(np.percentile(timings, 95)),
        "batch_seconds": batch_seconds,
        "process_peak_rss_mib": peak_rss_mib,
        "note": "Complete results including formatting; timings describe this machine and dataset.",
    }, indent=2))


if __name__ == "__main__":
    main()
