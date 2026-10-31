"""Measure complete lookups on synthetic data; not a geographic quality test."""

import argparse
import json
from pathlib import Path
import platform
import sys
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
               for i, (lat, lon) in enumerate(positions(args.records))]
    queries = positions(args.queries)
    batch = positions(args.batch_size)
    start = perf_counter()
    db = Gazetteer(records, dataset_id="synthetic-benchmark", synthetic=True)
    db.locate(*queries[0])
    setup_ms = (perf_counter() - start) * 1000
    timings = []
    for point in queries:
        start = perf_counter()
        db.locate(*point)
        timings.append((perf_counter() - start) * 1000)
    start = perf_counter()
    db.locate_many(batch)
    batch_seconds = perf_counter() - start
    print(json.dumps({
        "python": platform.python_version(), "numpy": np.__version__,
        "platform": platform.platform(), "records": args.records,
        "single_queries": args.queries, "batch_size": args.batch_size,
        "construction_and_first_lookup_ms": setup_ms,
        "warm_single_median_ms": float(np.median(timings)),
        "warm_single_p95_ms": float(np.percentile(timings, 95)),
        "batch_seconds": batch_seconds,
        "note": "Synthetic data, complete results including formatting; host timings, not iOS.",
    }, indent=2))


if __name__ == "__main__":
    main()
