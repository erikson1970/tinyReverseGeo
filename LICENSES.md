# Data provenance and licensing

The current `src/tinygeo/resources/demo.json` contains six invented names and
coordinates created solely as numerical test/demo references. They are not
GeoNames records and do not represent real settlements or natural features.
The development source package continues to use that fixture. Deployment artifacts
created with `tools/deploy.py` can contain real GeoNames records.

The [GeoNames export readme](https://download.geonames.org/export/dump/readme.txt)
specifies **CC BY 4.0**. Generated artifacts retain reference GeoNames IDs and
include attribution, source/license URLs, input SHA-256 hashes, acquisition times,
and changes made (selection/repackaging and coordinate rounding). GeoNames data is
provided as-is; nearest references do not establish administrative containment.

Initial source snapshots downloaded on **2026-10-31**:

| Source (under `https://download.geonames.org/export/dump/`) | SHA-256 |
|---|---|
| `cities500.zip` | `fc89c44c54a66fe05d1d0060209bb1dd5090ee1cc86f0bd92c198a606c81103e` |
| `admin1CodesASCII.txt` | `2e4867f5397acb056ea26377601b57af37cb4c81399770225018e9e439e91ad3` |
| `readme.txt` | `b1957379b6c1242c700c98ac9a8aa0a09f56c3c0a50ee72175527005f48ef2c5` |

The builder also supports other documented city/country/worldwide extracts and
cached GeoNames API responses obtained through optional build-time geopy. Every
deployment's `source-lock.json` is authoritative for its actual input snapshots.
geopy is a provider client; added providers would need their own license handling.

Required attribution used in generated builds:

> Contains GeoNames data (https://www.geonames.org/), CC BY 4.0; selected and repackaged by TinyGeo.

License: [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).

The notebook's optional Robinson basemap uses Natural Earth 110m land/coastline
layers, cached under ignored `data_sources/cartopy/`. Natural Earth declares these
map data [public domain](https://www.naturalearthdata.com/about/terms-of-use/).
They are plotting assets, separate from the GeoNames reference database.

The project's source code is licensed under Apache License 2.0; see [LICENSE](LICENSE).
External geographic data will retain its own source-specific license and attribution.
