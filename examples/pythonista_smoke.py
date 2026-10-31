"""Run in Pythonista to check the shared NumPy-only lookup runtime."""

from importlib.util import find_spec
from pathlib import Path
import sys

# Support both the repository examples/ directory and the extracted iOS bundle.
source = Path(__file__).resolve().parent.parent / "src"
if (source / "tinygeo").is_dir():
    sys.path.insert(0, str(source))

import numpy as np

from tinygeo import Gazetteer, Reference, locate, locate_many


print("Python:", sys.version.split()[0])
print("NumPy:", np.__version__)
print("Device location module available:", find_spec("location") is not None)
print("No device-location permission is requested by this smoke test.")

origin = locate(0, 0)
assert origin["nearest"]["distance_km"] < 1e-9
seam = locate(0, -179.9)
assert seam["nearest"]["source_id"] == "demo:dateline"
assert np.isclose(seam["nearest"]["distance_km"], 22.239016, atol=1e-5)
assert locate(90, 0)["nearest"]["bearing_deg"] is None
assert locate(0, 0, altitude=1000)["nearest"] == origin["nearest"]
assert len(locate_many([[0, 0], [0, 90]])) == 2
assert locate(0, 0, feature_types="landmark")["nearest"]["feature_type"] == "landmark"

custom = Gazetteer([
    Reference("test:east", "Synthetic East", 0, 90, "city"),
], dataset_id="smoke-test", synthetic=True)
assert np.isclose(custom.locate(0, 0)["nearest"]["bearing_deg"], 90)
print(seam["description"])
print("PASS: package data, distances, bearings, poles, antimeridian, altitude, batches, filters, custom data.")
print("Bundled data is synthetic; real geographic coverage and the tracking app are pending.")
