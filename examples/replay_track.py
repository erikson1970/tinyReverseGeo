"""Replay an idealized flight leg and inspect conditional future reference context."""

import argparse
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if (ROOT/'src/tinygeo').is_dir(): sys.path.insert(0, str(ROOT/'src'))
from tinygeo import Gazetteer, describe_track, estimate_motion, project_motion


def flight_leg(lat=42.36, lon=-71.06, course=55, speed_m_s=250, duration=600, interval=10):
    """Synthetic constant-motion fixes, not a real route/flight plan."""
    lat_r, lon_r, bearing = np.deg2rad([lat, lon, course])
    east = np.array([-np.sin(lon_r), np.cos(lon_r), 0])
    north = np.array([-np.sin(lat_r)*np.cos(lon_r), -np.sin(lat_r)*np.sin(lon_r), np.cos(lat_r)])
    tangent = (north*np.cos(bearing)+east*np.sin(bearing))*speed_m_s/6371008.8
    seed = {'status': 'moving', 'position': {'lat': lat, 'lon': lon},
            'tangent_per_second': tangent.tolist(), 'timestamp': 1793448000.}
    truth = project_motion(seed, range(0, duration+1, interval), include_context=False)
    return [(p['lat'], p['lon']) for p in truth], [p['timestamp'] for p in truth]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path,
                        default=ROOT/'dist/tinygeo-staggered-pythonista-external-10000/dataset.json.gz')
    args = parser.parse_args()
    db = Gazetteer.from_file(args.dataset)
    positions, times = flight_leg()
    events = describe_track(positions, times, gazetteer=db)
    print('SYNTHETIC constant-motion flight leg; no real flight plan.')
    for event in events:
        if event['type'] == 'gap': print(event); continue
        print(f"Samples {event['start_index']}–{event['end_index']}: {event['feature']['name']}; "
              f"closest observed zone distance {event['closest_observed']['reference']['distance_to_area_km']:.1f} km")
    motion = estimate_motion(positions, times)
    print(f"Motion: {motion['status']}; {motion['speed_m_s']:.1f} m/s; {motion['course_deg']:.1f}°")
    for forecast in project_motion(motion, gazetteer=db):
        print(f"+{forecast['horizon_seconds']/60:.0f} min: {forecast['lat']:.3f}, {forecast['lon']:.3f} — "
              + forecast['context']['description'])
    print('Extrapolations assume unchanged motion; no assured route or arrival.')


if __name__ == '__main__': main()
