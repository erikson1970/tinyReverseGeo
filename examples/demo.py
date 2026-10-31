"""Run after installing TinyGeo: python examples/demo.py."""

import json

from tinygeo import locate, locate_many

print("TinyGeo milestone 1: synthetic references, not real-world geographic context.")
print(json.dumps(locate(0, -179.9), indent=2, ensure_ascii=False))
print("\nBatch (third column is altitude, ignored for matching):")
for result in locate_many([(0, 0, 100), (89.95, 10, 0)]):
    print(result["description"])
