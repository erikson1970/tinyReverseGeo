"""Inspect a mixed geographic build without notebook or GIS dependencies."""

import argparse
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
if (root / "src/tinygeo").is_dir():
    sys.path.insert(0, str(root / "src"))

from tinygeo import Gazetteer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=root / "dist/tinygeo-staggered-pythonista-external-10000/dataset.json.gz")
    parser.add_argument("--distance-mode", choices=("area", "center"), default="area")
    args = parser.parse_args()
    db = Gazetteer.from_file(args.dataset)
    print(f"{len(db.records):,} stored records / {db.feature_count:,} named features")
    for label, lat, lon in [("Sahara", 24, 13), ("North Atlantic", 52, -30),
                            ("Everest", 27.9881, 86.925), ("Boston", 42.36, -71.06),
                            ("South of Iceland", 62, -20)]:
        result = db.locate(lat, lon, k=3, distance_mode=args.distance_mode)
        print(f"\n{label}: {result['description']}")
        for item in result["neighbors"]:
            name = ", ".join(value for value in (item["name"], item.get("region"), item.get("country_code")) if value)
            print(f"  {name}: {item['feature_type']}; center {item['distance_km']:.1f} km, "
                  f"zone {item['distance_to_area_km']:.1f} km, radius {item['radius_km']:.1f} km")


if __name__ == "__main__":
    main()
