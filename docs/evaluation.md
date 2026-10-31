# Initial geographic comparison

Measured 2026-10-31 for `tinygeo-c5f36386a63bde416304`: 10,000 records, 7,832
logical features, including 6,500 settlement references. This is an initial
coverage comparison, not a validation of administrative containment or ground truth.
The [raw report](reports/evaluation-2026-10-31.json) records exact inputs, seed,
checksums, examples, and independent distance checks.

## Method

Compare **nearest settlement center** with `reverse_geocoder` 1.5.1. Ignore fuzzy
radii for ranking and exclude mountains/oceans from this comparison. Use seed
20261031 for 2,000 area-uniform global coordinates and 2,000 unweighted positions
sampled without replacement from the baseline's 144,563-entry city CSV. Settlement
samples are deliberately on baseline references, so its distance is zero there;
this is a coverage stress test, not a neutral accuracy score.

Inspection of the installed baseline's
[primary source](https://github.com/thampiman/reverse-geocoder/blob/master/reverse_geocoder/__init__.py)
shows its 1.5.1 default tree/query using raw latitude/longitude coordinates. A
second **spherical** tree over its identical CSV separates geometric differences
from source selection. Neither baseline result establishes country containment.
The raw report pins both module and CSV hashes, since upstream code can change.

An independent paired haversine calculation agrees with TinyGeo's reported
center distances to less than 1e-9 km in these samples.

## Observed center-distance coverage

Distances in km; global sampling includes open oceans and polar areas.

| Samples / database | Median | p95 | Maximum |
|---|---:|---:|---:|
| Global / TinyGeo | 477.9 | 2,376.6 | 4,124.8 |
| Global / native reverse_geocoder | 513.7 | 2,408.5 | 5,609.3 |
| Global / spherical search of baseline CSV | 495.4 | 2,119.6 | 3,231.3 |
| Baseline settlement positions / TinyGeo | 43.4 | 108.7 | 176.3 |
| Baseline settlement positions / either baseline search | 0.0 | 0.0 | 0.0 |

TinyGeo and the spherical baseline return reference locations within 1 km of one
another for 40.9% of global samples and 3.8% of sampled baseline settlements.
That low settlement identity agreement is expected from a much smaller selection;
it is not evidence that the omitted city coordinates are wrong. Names can also
change or transliterate differently between snapshots.

The global median is not a superiority score: TinyGeo includes some newer/smaller
remote places missing from the older baseline, while its compact selection omits
many local settlements. The global p95 and extreme cases expose the tradeoff.
One Antarctic sample is about 593 km from the baseline's McMurdo reference but
4,054 km from TinyGeo's retained Invercargill reference. Its physical-region
context is a separate result; a nearby region does not repair settlement coverage.
An Indonesian sampled settlement is 176 km from TinyGeo's retained settlement.

Native baseline geometry also produces large polar/dateline disagreements. The
spherical CSV comparison exposes those separately. Different reference country
codes in the report describe the two **references**, not border accuracy.

## Physical-context inspection

These example descriptions remain qualified as proximity-zone estimates:

| Query | Selected context |
|---|---|
| New York, London, Tokyo | Corresponding city proximity zone |
| Sahara (24°, 13°) | Murzuq Desert; Sahara also among regional neighbors |
| Siberia (67°, 110°) | Central Siberian Plateau |
| Australian Outback (−24°, 134°) | MacDonnell Ranges |
| Pacific (0°, −140°) | South Pacific Ocean |
| North Atlantic (52°, −30°) | North Atlantic Ocean |
| Antarctica (−82°, 0°) | Antarctic Plateau |
| South of Iceland (62°, −20°) | North Atlantic Ocean, with settlement context separately |
| Dateline (0°, 180°) | North Pacific Ocean |
| North Pole | Arctic Ocean |

These are inspected outputs, not independent feature-boundary truth. Broad circles
can overlap land/coasts. Natural Earth polygons are approximate label regions.
The [decision log](decisions.md) records the country-label policy and coarse
coast guard. Circle membership never establishes an administrative boundary.

## Reproduce and next tuning

```sh
.venv/bin/python -m pip install -e '.[builder,evaluation]'
.venv/bin/python tools/evaluate_dataset.py \
  --dataset dist/tinygeo-staggered-pythonista-external-10000/dataset.json.gz \
  --samples 2000 --seed 20261031 --output dist/staggered-evaluation.json
```

Evaluation does not fetch geographic data. Preserve the baseline CSV/module and
TinyGeo dataset hashes to repeat the exact comparison. `reverse_geocoder` is an
optional desktop tool and is not shipped with TinyGeo runtime bundles.
Data attribution: GeoNames, CC BY 4.0; Natural Earth physical context, public domain.

Next useful tuning: add licensed reference sources for Antarctic stations and
other sparse regions; compare settlement/physical budget choices; collect device
route examples; evaluate region context against independent geometry when available.
These results do not establish global recognizability or authoritative boundaries.
