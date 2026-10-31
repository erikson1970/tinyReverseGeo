from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tinygeo import Gazetteer, Reference, TrackTracker, describe_track


@pytest.fixture
def towns():
    return Gazetteer([Reference('a', 'Alpha', 0, 0, 'town', country_code='AA'),
                      Reference('b', 'Beta', 0, 1, 'town', country_code='BB')])


def test_batch_and_stream_have_identical_stable_transitions(towns, monkeypatch):
    points = [[0, lon] for lon in [-.2, .49, .51, .49, .51, .7, .8]]
    times = list(range(100, 170, 10))
    calls = []
    original = towns.locate_many
    def counted(*args, **kwargs):
        calls.append(len(args[0]))
        return original(*args, **kwargs)
    monkeypatch.setattr(towns, 'locate_many', counted)
    batch = describe_track(points, times, gazetteer=towns, min_dwell_samples=2,
                           min_distance_km=0, mode='both')
    assert calls == [7]
    assert [(e['feature']['name'], e['start_index'], e['end_index']) for e in batch['events']] == [('Alpha', 0, 4), ('Beta', 5, 6)]
    assert batch['samples'][2]['context']['name'] == 'Alpha'
    assert batch['samples'][2]['raw_context']['name'] == 'Beta'
    assert batch['samples'][2]['context']['distance_km'] > 56
    stream = TrackTracker(towns, min_dwell_samples=2, min_distance_km=0, retain_samples=True)
    first = stream.update(points[0], times[0])
    for p, t in zip(points[1:], times[1:]): stream.update(p, t)
    assert first['index'] == 0  # Old annotations are not mutated by later updates.
    assert stream.snapshot('both') == batch
    assert batch['events'][1]['closest_observed']['index'] == 6


def test_distance_and_dwell_suppress_stationary_boundary_jitter(towns):
    points = [[0, lon] for lon in [.499, .501, .502, .501, .502, .501]]
    events = describe_track(points, gazetteer=towns, min_distance_km=5, min_dwell_samples=2)
    assert len(events) == 1
    assert events[0]['end_index'] == 5
    assert events[0]['start_time'] is None and events[0]['end_time'] is None
    times = [datetime(2026, 1, 1, tzinfo=timezone.utc)+timedelta(seconds=t) for t in (0, 1, 2, 3, 10, 11)]
    events = describe_track(points, times, gazetteer=towns, min_distance_km=0,
                            min_dwell_samples=2, min_dwell_seconds=8)
    assert len(events) == 2
    assert events[1]['start_index'] == 4
    assert events[1]['start_time'] == times[4]


def test_missing_positions_and_pauses_are_explicit_gaps(towns):
    points = [[0, 0], None, [float('nan'), 0], [0, 1], [0, 1]]
    events = describe_track(points, [0, 1, 2, 3, 300], gazetteer=towns)
    assert [e['type'] for e in events] == ['reference', 'gap', 'reference', 'gap', 'reference']
    assert events[1]['start_index'] == 1 and events[1]['end_index'] == 2
    assert events[3]['reason'] == 'time_gap'
    assert (events[3]['start_index'], events[3]['end_index']) == (3, 4)
    assert events[-1]['start_index'] == 4
    with pytest.raises(ValueError, match='invalid track position'):
        describe_track(points, gazetteer=towns, invalid='raise')


def test_dateline_and_grouped_region_context():
    records = [Reference('a', 'Ocean', 0, 179, 'ocean', radius_km=300, feature_id='o'),
               Reference('b', 'Ocean', 0, -179, 'ocean', radius_km=300, feature_id='o')]
    db = Gazetteer(records)
    result = describe_track([[0, 178], [0, 180], [0, -179]], gazetteer=db, mode='both')
    assert len(result['events']) == 1
    assert result['events'][0]['context_kind'] == 'nearby_region'
    assert result['events'][0]['closest_observed']['index'] == 2
    assert result['samples'][-1]['context']['source_id'] == 'b'


def test_empty_tracks_and_bad_options(towns):
    assert describe_track([], gazetteer=towns, mode='both') == {'events': [], 'samples': []}
    for kwargs in [{'min_distance_km': -1}, {'min_dwell_samples': True}, {'invalid': 'drop'}, {'min_dwell_seconds': np.inf}]:
        with pytest.raises(ValueError): TrackTracker(towns, **kwargs)
    with pytest.raises(ValueError, match='same length'): describe_track([[0, 0]], [], gazetteer=towns)
    with pytest.raises(ValueError, match='nondecreasing'): describe_track([[0, 0]]*2, [2, 1], gazetteer=towns)
    with pytest.raises(ValueError, match='timezone'): describe_track([[0, 0]], [datetime(2026, 1, 1)], gazetteer=towns)
    tracker = TrackTracker(towns)
    tracker.update([0, 0])
    with pytest.raises(ValueError, match='all samples'): tracker.update([0, 0], 10)
    with pytest.raises(ValueError, match='retain_samples'): tracker.snapshot('samples')
