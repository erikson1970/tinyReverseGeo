"""Recent spherical motion and explicitly conditional position extrapolations."""

from datetime import datetime, timedelta
import math
from numbers import Integral, Real

import numpy as np

from .lookup import EARTH_RADIUS_KM, _default_gazetteer, _positions, _xyz
from .track import _timestamp_seconds, _track_point


def estimate_motion(positions, timestamps, *, window_seconds=60, min_span_seconds=10,
                    min_samples=3, stationary_speed_m_s=.8, max_speed_m_s=400,
                    max_gap_seconds=120, max_age_seconds=30, max_residual_m=100, now=None):
    """Fit recent unit-sphere positions versus time, then project onto the tangent.

    Uses only the latest contiguous valid fixes. A sharp turn, implausible jump,
    stale fix, or poor fit suppresses forecasts. Thresholds are application choices,
    not GPS accuracy guarantees. Without now, age is measured at the latest fix.
    """
    settings = (window_seconds, min_span_seconds, stationary_speed_m_s, max_speed_m_s,
                max_gap_seconds, max_age_seconds, max_residual_m)
    if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) or v < 0 for v in settings):
        raise ValueError("motion thresholds must be finite nonnegative numbers")
    if (window_seconds <= 0 or max_gap_seconds <= 0 or max_speed_m_s <= 0
            or min_span_seconds > window_seconds or stationary_speed_m_s > max_speed_m_s):
        raise ValueError("inconsistent motion thresholds")
    if isinstance(min_samples, bool) or not isinstance(min_samples, Integral) or min_samples < 2:
        raise ValueError("min_samples must be an integer >= 2")
    points, times = list(positions), list(timestamps)
    if len(points) != len(times):
        raise ValueError("timestamps and positions must have the same length")
    seconds = np.array([_timestamp_seconds(t) for t in times], dtype=float)
    if len(seconds) > 1 and np.any(np.diff(seconds) <= 0):
        raise ValueError("motion timestamps must be strictly increasing")
    result = {'status': 'insufficient_history', 'reason': 'not_enough_recent_fixes',
              'position': None, 'timestamp': times[-1] if times else None,
              'time_seconds': float(seconds[-1]) if len(seconds) else None,
              'speed_m_s': None, 'course_deg': None, 'tangent_per_second': None,
              'sample_count': 0, 'span_seconds': 0., 'residual_m': None, 'age_seconds': None}
    if not points:
        return result
    valid = [_track_point(p) for p in points]
    if valid[-1] is None:
        result.update(status='unreliable', reason='invalid_latest_position')
        return result
    point = valid[-1]
    result['position'] = {'lat': float(point[0]), 'lon': float(point[1])}
    age = (_timestamp_seconds(now)-seconds[-1]) if now is not None else 0.
    result['age_seconds'] = float(age)
    if age < -2:
        result.update(status='unreliable', reason='future_fix_timestamp')
        return result
    if age > max_age_seconds:
        result.update(status='stale', reason='latest_fix_too_old')
        return result
    start = len(points)-1
    while start > 0:
        if (valid[start-1] is None or seconds[start]-seconds[start-1] > max_gap_seconds
                or seconds[-1]-seconds[start-1] > window_seconds):
            break
        start -= 1
    recent = np.array([p[:2] for p in valid[start:]])
    clock = seconds[start:]-seconds[-1]
    span = float(-clock[0])
    result.update(sample_count=len(recent), span_seconds=span)
    if len(recent) < min_samples or span < min_span_seconds or span <= 0:
        return result
    vectors = _xyz(recent)
    step_chord = np.linalg.norm(np.diff(vectors, axis=0), axis=1)
    step_speed = 2*EARTH_RADIUS_KM*1000*np.arcsin(np.minimum(1, step_chord/2))/np.diff(clock)
    if np.any(step_speed > max_speed_m_s):
        result.update(status='unreliable', reason='implausible_fix_jump')
        return result
    centered = clock-clock.mean()
    slope = np.sum(centered[:, None]*(vectors-vectors.mean(axis=0)), axis=0)/np.sum(centered**2)
    fitted = vectors.mean(axis=0) + centered[:, None]*slope
    fitted /= np.linalg.norm(fitted, axis=1)[:, None]
    residual = float(np.sqrt(np.mean(np.sum((vectors-fitted)**2, axis=1)))*EARTH_RADIUS_KM*1000)
    latest = vectors[-1]
    tangent = slope-np.dot(slope, latest)*latest
    speed = float(np.linalg.norm(tangent)*EARTH_RADIUS_KM*1000)
    result.update(speed_m_s=speed, residual_m=residual, tangent_per_second=tangent.tolist())
    if residual > max_residual_m or speed > max_speed_m_s:
        result.update(status='unreliable', reason='motion_fit_residual')
        return result
    if speed <= stationary_speed_m_s:
        result.update(status='stationary', reason='below_stationary_speed', speed_m_s=0.,
                      tangent_per_second=[0., 0., 0.])
        return result
    lat, lon = np.deg2rad(point[:2])
    east = np.array([-np.sin(lon), np.cos(lon), 0.])
    north = np.array([-np.sin(lat)*np.cos(lon), -np.sin(lat)*np.sin(lon), np.cos(lat)])
    course = float(np.rad2deg(np.arctan2(np.dot(tangent, east), np.dot(tangent, north))) % 360)
    result.update(status='moving', reason='recent_constant_motion_fit',
                  course_deg=course if abs(np.cos(lat)) > 1e-12 else None)
    return result


def project_motion(motion, horizons=(900, 3600, 7200), *, gazetteer=None, include_context=True):
    """Project 15 min / 1 h / 2 h by default; stale/unreliable inputs return [].

    Great-circle extrapolation from the latest fix, assuming unchanged ground
    speed and initial direction. It is not a route prediction or assured arrival.
    Stationary projections explicitly retain the latest position.
    """
    horizons = list(horizons)
    if any(isinstance(h, bool) or not isinstance(h, Real) or not math.isfinite(h) or h < 0 for h in horizons):
        raise ValueError("forecast horizons must be finite nonnegative seconds")
    if motion['status'] not in {'moving', 'stationary'}:
        return []
    point = _positions([[motion['position']['lat'], motion['position']['lon']]])
    center = _xyz(point)[0]
    tangent = np.array(motion['tangent_per_second'], dtype=float, copy=True)
    if tangent.shape != (3,) or not np.isfinite(tangent).all():
        raise ValueError("motion tangent must be a finite three-vector")
    tangent -= np.dot(tangent, center)*center
    angular_speed = float(np.linalg.norm(tangent))
    if motion['status'] == 'moving' and angular_speed <= 0:
        raise ValueError("moving motion needs a nonzero tangent")
    db = (gazetteer if gazetteer is not None else _default_gazetteer()) if include_context else None
    forecasts = []
    for horizon in horizons:
        angle = angular_speed*horizon if motion['status'] == 'moving' else 0.
        vector = center*np.cos(angle)
        if angular_speed:
            vector += tangent/angular_speed*np.sin(angle)
        lat = float(np.rad2deg(np.arctan2(vector[2], np.hypot(vector[0], vector[1]))))
        lon = float(np.rad2deg(np.arctan2(vector[1], vector[0])))
        timestamp = motion['timestamp']
        target_time = timestamp+timedelta(seconds=float(horizon)) if isinstance(timestamp, datetime) else float(timestamp)+horizon
        forecasts.append({'horizon_seconds': float(horizon), 'timestamp': target_time,
                          'lat': lat, 'lon': lon, 'motion_status': motion['status'],
                          'assumption': 'unchanged speed and initial direction along a great circle',
                          'context': db.locate(lat, lon, k=3) if db is not None else None})
    return forecasts
