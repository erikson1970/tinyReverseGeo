"""Stable, append-only reference context for ordered tracks; no inferred visits."""

from copy import deepcopy
from datetime import datetime
import math
from numbers import Integral, Real

import numpy as np

from .lookup import _default_gazetteer, _geometry, _positions


def _timestamp_seconds(value):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime timestamps must include a timezone")
        result = value.timestamp()
    elif isinstance(value, Real) and not isinstance(value, bool):
        result = float(value)
    else:
        raise ValueError("timestamps must be finite epoch seconds or timezone-aware datetimes")
    if not math.isfinite(result):
        raise ValueError("timestamps must be finite")
    return result


def _track_point(position):
    try:
        return _positions([position])[0]
    except (TypeError, ValueError):
        return None


def _track_context(result, settlement_distance, region_distance):
    settlement, region = result['nearest_settlement'], result['nearest_region']
    if settlement and settlement['distance_to_area_km'] <= settlement_distance:
        return settlement, 'nearby_settlement'
    if region and region['distance_to_area_km'] <= region_distance:
        return region, 'nearby_region'
    return result['nearest_any'], 'distant_reference'


class TrackTracker:
    """Incremental hysteresis shared by batch summaries and live acquisition.

    A transition starts at its confirmation sample, never backdates saved labels.
    Gaps reset dwell. Distances gate displacement from the active event's first fix,
    not accumulated jitter. Times are optional but must be supplied consistently.
    """

    def __init__(self, gazetteer=None, *, min_distance_km=25, min_dwell_samples=3,
                 min_dwell_seconds=0, settlement_distance_km=50, region_distance_km=100,
                 max_gap_seconds=120, retain_samples=False, invalid='gap'):
        values = (min_distance_km, min_dwell_seconds, settlement_distance_km,
                  region_distance_km, max_gap_seconds)
        if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("track distance/time thresholds must be finite nonnegative numbers")
        if isinstance(min_dwell_samples, bool) or not isinstance(min_dwell_samples, Integral) or min_dwell_samples < 1:
            raise ValueError("min_dwell_samples must be a positive integer")
        if invalid not in {'gap', 'raise'}:
            raise ValueError("invalid must be 'gap' or 'raise'")
        self.gazetteer = gazetteer if gazetteer is not None else _default_gazetteer()
        self.min_distance_km, self.min_dwell_samples = min_distance_km, min_dwell_samples
        self.min_dwell_seconds = min_dwell_seconds
        self.settlement_distance_km, self.region_distance_km = settlement_distance_km, region_distance_km
        self.max_gap_seconds, self.retain_samples, self.invalid = max_gap_seconds, retain_samples, invalid
        self.events, self.samples = [], []
        self._active = self._anchor = self._pending = None
        self._index, self._last_seconds, self._last_timestamp, self._timestamped = -1, None, None, None

    def _gap(self, index, timestamp, reason, start_index=None, start_time=None):
        if (reason == 'invalid_position' and self.events and self.events[-1]['type'] == 'gap'
                and self.events[-1]['reason'] == reason):
            self.events[-1].update(end_index=index, end_time=timestamp)
        else:
            self.events.append({'type': 'gap', 'reason': reason,
                                'start_index': index if start_index is None else start_index,
                                'end_index': index, 'start_time': timestamp if start_index is None else start_time,
                                'end_time': timestamp})
        self._active = self._anchor = self._pending = None

    def update(self, position, timestamp=None, *, _result=None):
        """Append one fix or explicit gap; returns a stable sample annotation."""
        seconds = _timestamp_seconds(timestamp) if timestamp is not None else None
        if seconds is None and self.min_dwell_seconds > 0:
            raise ValueError("min_dwell_seconds requires timestamps")
        if self._timestamped is not None and (seconds is not None) != self._timestamped:
            raise ValueError("supply timestamps for all samples or none")
        if seconds is not None and self._last_seconds is not None and seconds < self._last_seconds:
            raise ValueError("track timestamps must be nondecreasing")
        point = _track_point(position)
        if point is None and self.invalid == 'raise':
            raise ValueError("invalid track position")
        index = self._index+1
        if (seconds is not None and self._last_seconds is not None
                and seconds-self._last_seconds > self.max_gap_seconds):
            self._gap(index, timestamp, 'time_gap', self._index, self._last_timestamp)
        self._index, self._timestamped = index, seconds is not None
        self._last_seconds, self._last_timestamp = seconds, timestamp
        if point is None:
            self._gap(index, timestamp, 'invalid_position')
            annotation = {'index': index, 'timestamp': timestamp, 'valid': False,
                          'context': None, 'event_index': len(self.events)-1}
        else:
            result = _result if _result is not None else self.gazetteer.locate(*point[:2])
            candidate, kind = _track_context(result, self.settlement_distance_km, self.region_distance_km)
            identity = candidate['feature_id']
            change = self._active is None
            if not change and identity != self._active['feature']['feature_id']:
                if self._pending and self._pending['identity'] == identity:
                    self._pending['count'] += 1
                else:
                    self._pending = {'identity': identity, 'count': 1, 'seconds': seconds}
                elapsed = seconds-self._pending['seconds'] if seconds is not None else None
                displacement = float(_geometry(point, np.asarray([self._anchor]))[0][0])
                change = (self._pending['count'] >= self.min_dwell_samples
                          and displacement >= self.min_distance_km
                          and (self.min_dwell_seconds == 0 or elapsed is not None and elapsed >= self.min_dwell_seconds))
            elif not change:
                self._pending = None
            if change:
                self._active = {'type': 'reference', 'context_kind': kind,
                                'feature': {k: candidate[k] for k in ('feature_id', 'name', 'feature_type', 'country_code', 'region')},
                                'start_index': index, 'end_index': index, 'start_time': timestamp,
                                'end_time': timestamp, 'sample_count': 0, 'closest_observed': None}
                self.events.append(self._active)
                self._anchor, self._pending = point[:2].copy(), None
            match = (candidate if identity == self._active['feature']['feature_id']
                     else self.gazetteer._feature_match(point, self._active['feature']['feature_id']))
            self._active.update(end_index=index, end_time=timestamp, sample_count=self._active['sample_count']+1)
            closest = self._active['closest_observed']
            if closest is None or (match['distance_to_area_km'], match['distance_km']) < (closest['reference']['distance_to_area_km'], closest['reference']['distance_km']):
                self._active['closest_observed'] = {'index': index, 'timestamp': timestamp,
                    'query': {'lat': float(point[0]), 'lon': float(point[1])}, 'reference': match}
            annotation = {'index': index, 'timestamp': timestamp, 'valid': True,
                          'query': {**result['query'], **({'altitude': float(point[2])} if len(point) == 3 else {})},
                          'context': match, 'raw_context': candidate,
                          'context_kind': self._active['context_kind'], 'event_index': len(self.events)-1}
        if self.retain_samples:
            self.samples.append(deepcopy(annotation))
        return deepcopy(annotation)

    def snapshot(self, mode='events'):
        """Copy event and/or sample output; closest approach does not imply a visit."""
        if mode not in {'events', 'samples', 'both'}:
            raise ValueError("mode must be 'events', 'samples', or 'both'")
        if mode == 'events':
            return deepcopy(self.events)
        if not self.retain_samples:
            raise ValueError("construct with retain_samples=True to return sample history")
        if mode == 'samples':
            return deepcopy(self.samples)
        return deepcopy({'events': self.events, 'samples': self.samples})

    def break_track(self, reason='acquisition_gap'):
        """Reset continuity without inventing another fix or timestamp.

        The gap is anchored at the last known sample; the next update starts a new
        reference segment. Live acquisition can attach its wall-clock time in its
        own journal when an accurate fix timestamp is unavailable.
        """
        if not isinstance(reason, str) or not reason:
            raise ValueError("gap reason must be a nonempty string")
        if self._index < 0:
            self.events.append({'type': 'gap', 'reason': reason, 'start_index': None,
                                'end_index': None, 'start_time': None, 'end_time': None})
            return
        self._gap(self._index, self._last_timestamp, reason)


def describe_track(positions, timestamps=None, *, gazetteer=None, mode='events', **thresholds):
    """Batch reference segments with stable original sample indices/timestamps.

    Invalid coordinates become explicit gaps (invalid='raise' rejects them).
    Missing times stay None; min_dwell_seconds > 0 needs supplied timestamps.
    Time gaps bracket their two known samples. Transitions begin when confirmed.
    """
    positions = list(positions)
    times = [None]*len(positions) if timestamps is None else list(timestamps)
    if len(times) != len(positions):
        raise ValueError("timestamps and positions must have the same length")
    tracker = TrackTracker(gazetteer, retain_samples=True, **thresholds)
    points = [_track_point(p) for p in positions]
    valid = [i for i, p in enumerate(points) if p is not None]
    results = tracker.gazetteer.locate_many([points[i][:2] for i in valid]) if valid else []
    by_index = dict(zip(valid, results))
    for i, position in enumerate(positions):
        tracker.update(position, times[i], _result=by_index.get(i))
    return tracker.snapshot(mode)
