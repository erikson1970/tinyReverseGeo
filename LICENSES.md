# Data provenance and licensing

The current `src/tinygeo/resources/demo.json` contains six invented names and
coordinates created solely as numerical test/demo references. They are not
GeoNames records and do not represent real settlements or natural features.
No external geographic dataset is bundled at this milestone.

Before shipping real geographic data, the builder must record each source URL,
access date, snapshot/checksum, applicable license, and required attribution.
GeoNames is the intended primary source; its exact downloaded sources and license
terms still need verification when that milestone starts.

The project's source code is licensed under Apache License 2.0; see [LICENSE](LICENSE).
External geographic data will retain its own source-specific license and attribution.
