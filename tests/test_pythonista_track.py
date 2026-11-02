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
    session = companion.TrackSession(provider, tmp_path/'history', gazetteer=db, clock=lambda:clock[0], diagnostics=True)
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
    fixes = [e for e in companion.history_entries(s.path) if e['type'] == 'fix']
    assert not s.poll()  # Same fix does not append a duplicate.
    assert [e for e in companion.history_entries(s.path) if e['type'] == 'fix'] == fixes
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


def test_diagnostics_capture_stationary_motion_and_original_exception(session):
    s, provider, clock = session
    s.start()
    for stamp in (100, 110, 120):
        clock[0] = stamp
        provider.fix = fix(stamp)
        s.poll()
    events = list(companion.history_entries(s.path))
    estimates = [e for e in events if e.get('stage') == 'motion.estimate.end']
    assert estimates[-1]['details']['motion']['speed_m_s'] == 0
    assert estimates[-1]['details']['motion']['status'] == 'stationary'
    assert any(e.get('stage') == 'motion.project.end' and e['details']['forecast_count'] == 3 for e in events)
    def fail(): raise ZeroDivisionError('simulated device failure')
    provider.get_location = fail
    with pytest.raises(ZeroDivisionError, match='simulated device failure'): s.poll()
    errors = [e for e in companion.history_entries(s.path) if e['type'] == 'error']
    assert errors[-1]['stage'] == 'location.get.begin'
    assert 'ZeroDivisionError' in errors[-1]['traceback']
    assert 'fail' in errors[-1]['traceback']
    persisted = [json.loads(line) for line in (s.directory/'errors.log').read_text().splitlines()]
    assert persisted[-1]['message'] == 'simulated device failure'
    assert s._journal is None and not s.running


def test_diagnostics_are_json_safe_and_motion_can_be_disabled(session):
    s, provider, clock = session
    s.motion_enabled = False
    s.start()
    provider.fix = fix(100, latitude=float('nan'))
    assert not s.poll()
    for stamp in (101, 111, 121):
        clock[0] = stamp
        provider.fix = fix(stamp)
        assert s.poll()
    assert s.forecasts == []
    events = list(companion.history_entries(s.path))
    assert any(e.get('stage') == 'motion.disabled' for e in events)
    assert not any(e.get('stage') == 'motion.estimate.begin' for e in events)
    raw = next(e for e in events if e.get('stage') == 'location.get.end')
    assert raw['details']['raw_fix']['latitude'] == 'nan'
    s.stop()


def test_cleanup_error_does_not_hide_the_original_failure(session):
    s, provider, _ = session
    s.start()
    def read_fail(): raise ValueError('original read failure')
    def stop_fail(): raise RuntimeError('cleanup failure')
    provider.get_location, provider.stop_updates = read_fail, stop_fail
    with pytest.raises(ValueError, match='original read failure'): s.poll()
    assert s._journal is None
    records = [e for e in companion.history_entries(s.path) if e['type'] == 'error']
    assert [e['message'] for e in records] == ['original read failure', 'cleanup failure']


@pytest.mark.parametrize('failure', ['formatting', 'text_update', 'update_enable'])
def test_ui_failure_is_persisted_and_stops_cleanly(session, monkeypatch, failure):
    import types
    s, provider, _ = session
    provider.fix = fix(100)
    views = []
    class View:
        def __init_subclass__(cls): pass
        @property
        def update_interval(self): return getattr(self, '_interval', 0)
        @update_interval.setter
        def update_interval(self, value):
            if failure == 'update_enable' and value > 0:
                raise RuntimeError('simulated periodic update exception')
            self._interval = value
        def add_subview(self, v): pass
        def present(self, *a): views.append(self)
        def wait_modal(self): self.start(None)
    class Button:
        def __init__(self, title): self.title = title
    class Text:
        @property
        def text(self): return getattr(self, '_text', '')
        @text.setter
        def text(self, value):
            if failure == 'text_update' and value.startswith('Recording in foreground'):
                raise RuntimeError('simulated native text setter exception')
            self._text = value
    original_display = companion._display
    def display(current):
        if failure == 'formatting' and current.last_fix:
            raise RuntimeError('simulated formatting exception')
        return original_display(current)
    monkeypatch.setitem(__import__('sys').modules, 'ui', types.SimpleNamespace(View=View, Button=Button, TextView=Text))
    monkeypatch.setitem(__import__('sys').modules, 'location', provider)
    monkeypatch.setattr(companion, 'TrackSession', lambda *a, **k: s)
    monkeypatch.setattr(companion, '_display', display)
    monkeypatch.setattr(companion, 'CAPTURE_NATIVE_FAULTS', False)
    companion.main()
    assert not s.running and provider.stops == 1
    assert 'Stopped after RuntimeError' in views[0].text.text
    assert views[0].buttons[0].enabled
    errors = [e for e in companion.history_entries(s.path) if e['type'] == 'error']
    assert errors[-1]['stage'] == ('ui.start' if failure == 'update_enable' else 'ui.render')
    assert 'simulated' in errors[-1]['traceback']
    assert (s.directory/'errors.log').exists()


@pytest.mark.parametrize('existing', [True, False])
def test_native_fault_stream_lifetime_and_existing_handler(tmp_path, monkeypatch, existing):
    import types
    state = {'enabled': existing, 'enable_calls': 0, 'disable_calls': 0, 'stream': None}
    def enable(*, file, all_threads):
        assert not file.closed and all_threads
        state.update(enabled=True, enable_calls=state['enable_calls']+1, stream=file)
    def disable():
        assert not state['stream'].closed
        state.update(enabled=False, disable_calls=state['disable_calls']+1)
    fake = types.SimpleNamespace(is_enabled=lambda: state['enabled'], enable=enable, disable=disable)
    monkeypatch.setitem(__import__('sys').modules, 'faulthandler', fake)
    monkeypatch.setattr(companion, 'CAPTURE_NATIVE_FAULTS', True)
    with companion._native_fault_logging(tmp_path):
        if not existing: assert state['stream'] is not None and not state['stream'].closed
    assert state['enable_calls'] == (0 if existing else 1)
    assert state['disable_calls'] == (0 if existing else 1)
    if not existing: assert state['stream'].closed
    assert state['enabled'] is existing


def test_view_owned_updates_stationary_forecasts_restart_and_close(session, monkeypatch):
    import types
    s, provider, clock = session
    provider.fix = fix(100)
    reads, saved = [], []
    def get_location():
        reads.append(provider.fix['timestamp'])
        return provider.fix
    provider.get_location = get_location
    class View:
        def add_subview(self, view): pass
        def present(self, *a): pass
        def wait_modal(self):
            assert self.update_interval == 0
            self.start(None)
            assert self.update_interval == 2.0
            self.start(None)  # A queued duplicate Start must not poll twice.
            assert len(reads) == 1
            for stamp in range(102, 132, 2):
                clock[0] = stamp; provider.fix = fix(stamp)
                self.update()
            assert len(reads) == 16
            assert s.motion['status'] == 'stationary' and len(s.forecasts) == 3
            rows = list(companion.history_entries(s.path))
            assert rows[0]['ui_scheduler'] == 'View.update'
            assert rows[0]['diagnostic_version'] == 2
            assert len([r for r in rows if r['type']=='fix']) == 16
            assert any(r.get('stage')=='ui.update.begin' for r in rows)
            assert not any(r.get('stage','').startswith('ui.timer.schedule') for r in rows)
            saved.append(s.path)
            old_generation = self.generation
            self.stop(None)
            assert self.update_interval == 0 and not s.running
            self.update()  # A late native update after Stop must not acquire a fix.
            assert len(reads) == 16
            clock[0] = 140; provider.fix = fix(140)
            self.start(None)
            assert s.path != saved[0] and len(reads) == 17
            self.tick(old_generation)
            assert len(reads) == 17
            # Simulate a reentrant update during acquisition: only one read is allowed.
            def reentrant_read():
                reads.append(142)
                self.update()
                return fix(142)
            provider.get_location = reentrant_read
            clock[0] = 142
            self.update()
            assert len(reads) == 18
            assert any(r.get('stage')=='ui.tick.skipped_busy' for r in companion.history_entries(s.path))
            self.will_close()
            assert self.update_interval == 0 and self.closed and not s.running
            self.update(); self.start(None)
            assert len(reads) == 18
    class Button:
        def __init__(self, title): self.title = title
    class Text: pass
    # No ui.delay exists in this stub; the former scheduling path cannot be used.
    monkeypatch.setitem(__import__('sys').modules, 'ui', types.SimpleNamespace(View=View, Button=Button, TextView=Text))
    monkeypatch.setitem(__import__('sys').modules, 'location', provider)
    monkeypatch.setattr(companion, 'TrackSession', lambda *a, **k: s)
    monkeypatch.setattr(companion, 'CAPTURE_NATIVE_FAULTS', False)
    companion.main()
    assert provider.starts == 2 and provider.stops == 2
    assert not [r for r in companion.history_entries(s.path) if r['type']=='error']


def test_normal_defaults_keep_fix_history_and_python_errors_without_checkpoints(session):
    old, provider, clock = session
    s = companion.TrackSession(provider, old.directory, gazetteer=old.db, clock=lambda:clock[0])
    assert not s.diagnostics and s.motion_enabled
    assert companion.DIAGNOSTICS is False and companion.CAPTURE_NATIVE_FAULTS is False
    s.start()
    for stamp in (100, 110, 120):
        clock[0] = stamp; provider.fix = fix(stamp)
        assert s.poll()
    rows = list(companion.history_entries(s.path))
    assert rows[0]['diagnostics'] is False
    assert len([r for r in rows if r['type']=='fix']) == 3
    assert not any(r['type']=='diagnostic' for r in rows)
    assert s.motion['status']=='stationary' and len(s.forecasts)==3
    def fail(): raise RuntimeError('normal-mode error logging')
    provider.get_location = fail
    with pytest.raises(RuntimeError): s.poll()
    assert any(r['type']=='error' for r in companion.history_entries(s.path))
    assert (s.directory/'errors.log').exists()
