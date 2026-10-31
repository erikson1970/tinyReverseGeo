"""Compare center-ranked settlements with reverse_geocoder, a baseline, not truth."""

import argparse
import csv
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import numpy as np
from tinygeo import Gazetteer


def spherical_distance(query, target):
    """Independent haversine check (lat/lon degrees, vectorized paired rows)."""
    a, b = np.deg2rad(query), np.deg2rad(target)
    h = np.sin((b[:, 0]-a[:, 0])/2)**2 + np.cos(a[:, 0])*np.cos(b[:, 0])*np.sin((b[:, 1]-a[:, 1])/2)**2
    return 2*6371.0088*np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def distribution(values):
    values = np.asarray(values, dtype=float)
    return {label: float(value) for label, value in zip(('min', 'median', 'p90', 'p95', 'max'),
                                                       np.percentile(values, [0, 50, 90, 95, 100]))}


def name_key(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value).casefold() if c.isalnum())


def sphere_vectors(points):
    lat, lon = np.deg2rad(points).T
    return np.column_stack((np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon), np.sin(lat)))


def compare(db, points, baseline, baseline_tree):
    import reverse_geocoder as rg
    answers = db.locate_many(points, feature_types=('city', 'town', 'remote_settlement'), distance_mode='center')
    tiny = [a['nearest_settlement'] for a in answers]
    if any(a is None for a in tiny): raise ValueError('evaluation requires settlement references')
    other = rg.search([tuple(p) for p in points], mode=1, verbose=False)
    sphere_indices = baseline_tree.query(sphere_vectors(points))[1]
    spherical = [baseline[i] for i in sphere_indices]
    tiny_coordinates = np.array([[a['lat'], a['lon']] for a in tiny])
    other_coordinates = np.array([[float(a['lat']), float(a['lon'])] for a in other])
    spherical_coordinates = np.array([[float(a['lat']), float(a['lon'])] for a in spherical])
    tiny_distances = spherical_distance(points, tiny_coordinates)
    other_distances = spherical_distance(points, other_coordinates)
    spherical_distances = spherical_distance(points, spherical_coordinates)
    separation = spherical_distance(tiny_coordinates, other_coordinates)
    error = np.max(np.abs(tiny_distances-np.array([a['distance_km'] for a in tiny])))
    if error > 1e-6: raise AssertionError(f'distance check differs by {error} km')
    excess = tiny_distances-other_distances
    spherical_excess = tiny_distances-spherical_distances
    examples = []
    # Show both sparser-selection gaps and cases where newer/smaller TinyGeo places are closer.
    for index in list(np.argsort(spherical_excess)[-5:][::-1])+list(np.argsort(spherical_excess)[:5]):
        examples.append({'query': points[index].tolist(),
                         'tinygeo': {'name': tiny[index]['name'], 'country_code': tiny[index]['country_code'],
                                     'distance_km': float(tiny_distances[index])},
                         'baseline': {'name': other[index]['name'], 'country_code': other[index]['cc'],
                                      'distance_km': float(other_distances[index])},
                         'spherical_baseline': {'name': spherical[index]['name'], 'country_code': spherical[index]['cc'],
                                                'distance_km': float(spherical_distances[index])},
                         'excess_over_spherical_baseline_km': float(spherical_excess[index]),
                         'excess_center_distance_km': float(excess[index])})
    return {'count': len(points), 'tinygeo_center_distance_km': distribution(tiny_distances),
            'baseline_center_distance_km': distribution(other_distances),
            'spherical_baseline_center_distance_km': distribution(spherical_distances),
            'tinygeo_minus_spherical_baseline_distance_km': distribution(spherical_excess),
            'reference_locations_within_1km_of_spherical_baseline_fraction': float(np.mean(
                spherical_distance(tiny_coordinates, spherical_coordinates) <= 1)),
            'tinygeo_minus_baseline_distance_km': distribution(excess),
            'reference_locations_within_1km_fraction': float(np.mean(separation <= 1)),
            'same_normalized_name_and_country_fraction': float(np.mean([
                name_key(a['name']) == name_key(b['name']) and a['country_code'] == b['cc'] for a, b in zip(tiny, other)])),
            'different_reference_country_fraction': float(np.mean([a['country_code'] != b['cc'] for a, b in zip(tiny, other)])),
            'independent_distance_check_max_error_km': float(error), 'distance_disagreement_examples': examples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=2000, help='samples per global and baseline-settlement group')
    parser.add_argument('--seed', type=int, default=20261031)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/evaluation.json')
    args = parser.parse_args()
    if args.samples < 1: parser.error('--samples must be positive')
    try:
        import reverse_geocoder as rg
    except ImportError:
        parser.error("install the optional desktop evaluation extra: pip install -e '.[evaluation]'")
    db = Gazetteer.from_file(args.dataset)
    baseline_path = Path(rg.__file__).parent/'rg_cities1000.csv'
    with baseline_path.open(encoding='utf-8') as stream: baseline = list(csv.DictReader(stream))
    from scipy.spatial import cKDTree
    baseline_tree = cKDTree(sphere_vectors(np.array([[float(a['lat']), float(a['lon'])] for a in baseline])))
    rng = np.random.default_rng(args.seed)
    global_points = np.column_stack((np.rad2deg(np.arcsin(rng.uniform(-1, 1, args.samples))),
                                     rng.uniform(-180, 180, args.samples)))
    selected = rng.choice(len(baseline), min(args.samples, len(baseline)), replace=False)
    settlements = np.array([[float(baseline[i]['lat']), float(baseline[i]['lon'])] for i in selected])
    report = {'dataset_id': db.dataset_id, 'record_count': len(db.records), 'feature_count': db.feature_count,
              'dataset_sha256': sha256(args.dataset.read_bytes()).hexdigest(), 'seed': args.seed,
              'python': platform.python_version(), 'numpy': np.__version__,
              'baseline': {'name': 'reverse_geocoder', 'version': version('reverse_geocoder'),
                           'source': 'https://github.com/thampiman/reverse-geocoder',
                           'bundled_settlement_count': len(baseline),
                           'module_sha256': sha256(Path(rg.__file__).read_bytes()).hexdigest(),
                           'bundled_csv_sha256': sha256(baseline_path.read_bytes()).hexdigest()},
              'notes': ['Baseline is a comparison, not authoritative truth or country containment.',
                        'Both comparisons use nearest settlement CENTER distance; radii and physical features do not affect ranking.',
                        'Global sampling is area-uniform and includes oceans; settlement samples come from the older baseline CSV.',
                        'Settlement samples are unweighted and not independent population or visit-frequency truth.',
                        'Different source dates, selection budgets, place inclusion and name spellings explain disagreements.'],
              'global_area_uniform': compare(db, global_points, baseline, baseline_tree),
              'baseline_settlement_positions': compare(db, settlements, baseline, baseline_tree)}
    report['notes'].append('Installed reverse_geocoder 1.5.1 queries raw lat/lon degrees; a separate spherical tree over the identical CSV isolates geometry from source/selection differences.')
    cases = [('New York',40.7128,-74.006), ('London',51.5074,-.1278), ('Tokyo',35.6762,139.6503),
             ('Sahara',24,13), ('Siberia',67,110), ('Australian Outback',-24,134), ('Pacific',0,-140),
             ('North Atlantic',52,-30), ('Antarctica',-82,0), ('South of Iceland',62,-20),
             ('Dateline',0,180), ('North Pole',90,0)]
    report['context_examples'] = []
    for name, lat, lon in cases:
        result = db.locate(lat,lon,k=3)
        report['context_examples'].append({'case': name, 'query': result['query'], 'description': result['description'],
                                          'nearest_settlement': result['nearest_settlement'],
                                          'nearby_regions': result['nearby_regions']})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print('Evaluation:', args.output)
    for group in ('global_area_uniform','baseline_settlement_positions'):
        row=report[group]
        print(group, ': within 1 km of baseline reference', f"{row['reference_locations_within_1km_fraction']:.1%}",
              '; median center distance', f"{row['tinygeo_center_distance_km']['median']:.1f} km")
    return report


if __name__ == '__main__': main()
