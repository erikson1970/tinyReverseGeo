from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tinygeo import estimate_motion, project_motion
from tinygeo.lookup import EARTH_RADIUS_KM, _geometry


def equatorial(speed=100, start_lon=0, count=7):
    times = np.arange(count)*10.
    lon = (start_lon + np.rad2deg(speed*times/(EARTH_RADIUS_KM*1000))+180)%360-180
    return np.column_stack((np.zeros(count), lon)), times


@pytest.mark.parametrize('start_lon', [0, 179.98, -179.98])
def test_speed_course_and_all_three_forecasts(start_lon):
    points, times = equatorial(start_lon=start_lon)
    motion = estimate_motion(points, times)
    assert motion['status'] == 'moving'
    assert motion['speed_m_s'] == pytest.approx(100, rel=1e-6)
    assert motion['course_deg'] == pytest.approx(90, abs=1e-8)
    forecasts = project_motion(motion, include_context=False)
    assert [f['horizon_seconds'] for f in forecasts] == [900, 3600, 7200]
    for horizon, forecast in zip((900, 3600, 7200), forecasts):
        expected_lon = (points[-1, 1]+np.rad2deg(100*horizon/(EARTH_RADIUS_KM*1000))+180)%360-180
        assert forecast['lat'] == pytest.approx(0, abs=1e-8)
        assert forecast['lon'] == pytest.approx(expected_lon, abs=1e-5)
        assert forecast['timestamp'] == 60+horizon
        assert forecast['context'] is None


def test_polar_forecast_has_no_arbitrary_compass_course():
    times = [0, 10, 20]
    positions = [[89.98, 0], [89.99, 0], [90, 0]]
    motion = estimate_motion(positions, times)
    assert motion['status'] == 'moving'
    assert motion['course_deg'] is None
    forecast = project_motion(motion, [10], include_context=False)[0]
    assert forecast['lat'] == pytest.approx(89.99, abs=1e-8)
    assert abs(forecast['lon']) == pytest.approx(180, abs=1e-8)


def test_stationary_noise_and_stale_forecasts():
    rng = np.random.default_rng(32)
    points = np.array([42., -71.])+rng.normal(0, .00001, (7, 2))
    times = list(range(0, 70, 10))
    motion = estimate_motion(points, times)
    assert motion['status'] == 'stationary'
    assert motion['speed_m_s'] == 0
    for f in project_motion(motion, include_context=False):
        assert _geometry(points[-1], np.array([[f['lat'], f['lon']]]))[0][0] < 1e-7
        assert f['motion_status'] == 'stationary'
    stale = estimate_motion(points, times, now=200)
    assert stale['status'] == 'stale'
    assert project_motion(stale) == []
    assert estimate_motion(points, times, now=0)['reason'] == 'future_fix_timestamp'


def test_gap_jump_and_turn_suppress_unreliable_movement():
    points, times = equatorial()
    assert estimate_motion([], [])['status'] == 'insufficient_history'
    assert estimate_motion(points[:1], times[:1])['status'] == 'insufficient_history'
    missing = points.tolist(); missing[-2] = None
    assert estimate_motion(missing, times)['sample_count'] == 1
    assert estimate_motion(points, [0, 10, 20, 30, 40, 50, 1000])['status'] == 'insufficient_history'
    jump = points.copy(); jump[-1] = [10, 10]
    unreliable = estimate_motion(jump, times)
    assert unreliable['reason'] == 'implausible_fix_jump'
    assert project_motion(unreliable) == []
    # A right-angle turn over the window disagrees with constant motion.
    turn = [[0, 0], [0, .01], [0, .02], [0, .03], [.01, .03], [.02, .03], [.03, .03]]
    assert estimate_motion(turn, times, max_residual_m=50)['reason'] == 'motion_fit_residual'
    invalid = points.tolist(); invalid[-1] = [91, 0]
    assert estimate_motion(invalid, times)['reason'] == 'invalid_latest_position'


def test_aware_time_roundtrip_and_context():
    points, times = equatorial()
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stamps = [origin+timedelta(seconds=float(t)) for t in times]
    motion = estimate_motion(points, stamps, now=stamps[-1])
    forecast = project_motion(motion, [900])[0]
    assert forecast['timestamp'] == stamps[-1]+timedelta(seconds=900)
    assert forecast['context']['synthetic'] is True
    assert 'unchanged' in forecast['assumption']


def test_bad_time_and_threshold_inputs():
    points, times = equatorial()
    for bad in ([0, 1, 2], [0, 10, 20, 30, 40, 50, 50], [0, 10, 20, 30, 40, 50, np.nan]):
        with pytest.raises(ValueError): estimate_motion(points, bad)
    for kwargs in [{'window_seconds': -1}, {'min_samples': 1}, {'min_span_seconds': 100}, {'max_speed_m_s': 0}]:
        with pytest.raises(ValueError): estimate_motion(points, times, **kwargs)
    with pytest.raises(ValueError): project_motion({'status': 'stale'}, [-1])
