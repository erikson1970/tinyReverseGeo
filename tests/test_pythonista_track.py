import csv
import importlib.util
import json
from pathlib import Path

import pytest

from tinygeo import Gazetteer, Reference

spec = importlib.util.spec_from_file_location('pythonista_track', Path(__file__).resolve().parents[1]/'examples/pythonista_track.py')
companion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(companion)


class Provider:
    fix = None
    starts = stops = 0
    def start_updates(self): self.starts += 1
    def stop_updates(self): self.stops += 1
    def get_location(self): return self.fix


@pytest.fixture
def session(tmp_path):
    db = Gazetteer([Reference('a', 'Alpha, City', 0, 0, 'town')], dataset_id='test-data')
    clock = [100.]
    provider = Provider()
    session = companion.TrackSession(provider, tmp_path/'history', gazetteer=db, clock=lambda:clock[0])
    return session, provider, clock


def fix(stamp, latitude=0, longitude=0, **extra):
    return dict(timestamp=stamp, latitude=latitude, longitude=longitude, **extra)


def test_lifecycle_duplicate_stale_forecast_and_csv_replay(session):
    s, provider, clock = session
    assert not s.poll()
    s.start(); s.start()
    assert provider.starts == 1
    for stamp in (100, 110, 120):
        clock[0] = stamp
        provider.fix = fix(stamp, horizontal_accuracy=5, altitude=17)
        assert s.poll()
    assert s.motion['status'] == 'stationary' and len(s.forecasts) == 3
    lines = s.path.read_text().splitlines()
    assert not s.poll()  # Same fix does not append a duplicate.
    assert s.path.read_text().splitlines() == lines
    provider.fix = fix(110)  # Out-of-order provider data is ignored.
    assert not s.poll()
    clock[0] = 200
    assert not s.poll()
    assert s.forecasts == []
    assert s.tracker.events[-1]['type'] == 'gap'
    provider.fix = fix(200)
    assert s.poll()
    assert s.motion['status'] == 'insufficient_history'
    expected = s.tracker.snapshot()
    csv_path = s.path.with_suffix('.csv')
    companion.export_csv(s.path, csv_path)  # Also works while recording.
    with csv_path.open() as stream: rows = list(csv.DictReader(stream))
    assert len(rows) == 4
    assert rows[0]['context_name'] == 'Alpha, City'
    assert rows[0]['altitude'] == '17.0'
    assert rows[0]['dataset_id'] == 'test-data'
    s.stop(); s.stop()
    assert provider.stops == 1
    assert companion.replay_history(s.path, gazetteer=s.db) == expected
    assert 'Foreground' in companion._display(s)


@pytest.mark.parametrize('bad,reason', [(None,'No fix'), (fix(10),'Stale'), (fix(110),'future'),
                         (fix(100,latitude=91),'out of range'), (fix(100,longitude=float('nan')),'Invalid coordinates'),
                         (fix(float('nan')),'timestamp'), (fix(100,horizontal_accuracy=1000),'accuracy'),
                         ('garbled','Malformed')])
def test_rejected_fixes_are_gaps_and_never_saved_as_fixes(session, bad, reason):
    s, provider, clock = session
    s.start(); provider.fix = bad
    assert not s.poll()
    assert reason.lower() in s.status.lower()
    assert not any(e['type']=='fix' for e in companion.history_entries(s.path))
    assert s.forecasts == []
    s.stop()


def test_failed_provider_and_failed_start_always_stop_updates(session):
    s, provider, _ = session
    def fail(): raise RuntimeError('provider unavailable')
    provider.get_location = fail
    s.start()
    with pytest.raises(RuntimeError, match='provider unavailable'): s.poll()
    assert not s.running and provider.stops == 1
    assert s._journal is None
    provider.start_updates = fail
    with pytest.raises(RuntimeError): s.start()
    assert provider.stops == 2


def test_rejected_timestamp_does_not_poison_later_valid_fixes(session):
    s, provider, clock = session
    s.start()
    provider.fix = fix(1000000)
    assert not s.poll()
    provider.fix = fix(100, horizontal_accuracy=1000)
    assert not s.poll()
    provider.fix = fix(100, horizontal_accuracy=5)
    assert s.poll()
    assert s.last_seen == 100
    s.stop()


def test_restart_uses_new_journal_and_replay_checks_dataset(session):
    s, provider, _ = session
    s.start(); provider.fix = fix(100); s.poll(); s.stop()
    old = s.path
    s.start(); assert s.path != old
    assert not s.tracker.events
    assert s.last_seen is None
    s.stop()
    other = Gazetteer([Reference('other', 'Other', 0, 0, 'town')], dataset_id='different')
    with pytest.raises(ValueError, match='same dataset_id'): companion.replay_history(old, gazetteer=other)
    # A crashed, partially written final line is ignored; bad complete lines fail.
    with old.open('a') as stream: stream.write('{"type":')
    assert companion.replay_history(old, gazetteer=s.db)
    with old.open('a') as stream: stream.write('\n')
    with pytest.raises(json.JSONDecodeError): companion.replay_history(old, gazetteer=s.db)
