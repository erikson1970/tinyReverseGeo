"""Foreground Pythonista track companion; also importable for desktop replay.

Run beside a deployed tinygeo package/module. History stays in track_history/.
Only main() imports iOS location/ui. No platform reverse-geocoding or map download.
"""

from collections import deque
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import json
import math
from numbers import Real
from pathlib import Path
import sys
import threading
import time
import traceback
from uuid import uuid4

import numpy as np
from tinygeo import TrackTracker, estimate_motion, project_motion

# Normal foreground operation; the debug launcher enables the diagnostic flags.
DIAGNOSTICS = False
ENABLE_MOTION = True
CAPTURE_NATIVE_FAULTS = False


def _safe_log_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Real):
        return float(value) if math.isfinite(value) else repr(value)
    if isinstance(value, dict):
        return {str(k): _safe_log_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_log_value(v) for v in value]
    return repr(value)[:4000]


class TrackSession:
    """Provider-independent foreground acquisition, persistence, and forecasts."""

    def __init__(self, provider, directory, *, gazetteer=None, clock=time.time,
                 max_accuracy_m=200, max_fix_age_seconds=30, track_options=None, motion_options=None,
                 diagnostics=None, motion_enabled=True):
        self.provider, self.directory, self.clock = provider, Path(directory), clock
        if any(not isinstance(v, Real) or isinstance(v, bool) or not math.isfinite(v) or v <= 0
               for v in (max_accuracy_m, max_fix_age_seconds)):
            raise ValueError('accuracy and fix-age limits must be positive finite numbers')
        self.max_accuracy_m, self.max_fix_age_seconds = max_accuracy_m, max_fix_age_seconds
        self.track_options = dict(track_options or {})
        self.motion_options = dict(motion_options or {})
        self.tracker = TrackTracker(gazetteer, **self.track_options)
        self.db = self.tracker.gazetteer
        self.running, self.path, self._journal = False, None, None
        self.recent = deque(maxlen=512)
        self.last_seen = self.last_fix = self.current = self.annotation = None
        self.motion, self.forecasts = estimate_motion([], []), []
        self.status, self._last_rejection = 'Stopped', None
        self.diagnostics = DIAGNOSTICS if diagnostics is None else diagnostics
        self.motion_enabled = motion_enabled
        self._sequence, self._phase, self.last_error = 0, 'created', None

    def diagnostic(self, stage, **details):
        """Flush checkpoints; logging failures must not create an app failure."""
        self._phase = stage
        self._sequence += 1
        if not self.diagnostics or self._journal is None or self._journal.closed:
            return
        try:
            self._write({'type': 'diagnostic', 'stage': stage, 'recorded_at': self.clock(),
                         'sequence': self._sequence, 'thread_id': threading.get_ident(),
                         'recent_count': len(self.recent), 'details': _safe_log_value(details)})
        except Exception as exc:
            print('TinyGeo diagnostic write failed:', repr(exc))

    def capture_error(self, stage, exc):
        """Persist Python tracebacks even after poll cleanup closed the journal."""
        record = {'type': 'error', 'stage': stage, 'recorded_at': self.clock(),
                  'exception_type': type(exc).__name__, 'message': str(exc),
                  'traceback': ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                  'last_checkpoint': self._phase, 'thread_id': threading.get_ident()}
        self.last_error = record
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory/'errors.log').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n')
                stream.flush()
        except Exception as logging_error:
            print('TinyGeo error-log write failed:', repr(logging_error))
        try:
            if self._journal is not None and not self._journal.closed:
                self._write(record)
            elif self.path is not None:
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n')
                    stream.flush()
        except Exception as logging_error:
            print('TinyGeo journal error write failed:', repr(logging_error))
        print(record['traceback'])

    def _write(self, entry):
        self._journal.write(json.dumps(entry, ensure_ascii=False, allow_nan=False)+'\n')
        self._journal.flush()

    def start(self):
        if self.running:
            return
        try:
            self.provider.start_updates()
            self.directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.fromtimestamp(self.clock(), timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            self.path = self.directory/f'track-{stamp}-{uuid4().hex[:8]}.jsonl'
            self._journal = self.path.open('x', encoding='utf-8')
            self.tracker = TrackTracker(self.db, **self.track_options)
            self.recent.clear()
            self.last_seen = self.last_fix = self.current = self.annotation = None
            self._last_rejection = None
            self.last_error = None
            self.motion, self.forecasts = estimate_motion([], []), []
            self._write({'type': 'metadata', 'format_version': 1, 'created_at': self.clock(),
                         'dataset_id': self.db.dataset_id, 'synthetic': self.db.synthetic,
                         'track_options': self.track_options, 'motion_options': self.motion_options,
                         'python': sys.version, 'numpy': np.__version__, 'numpy_error_policy': np.geterr(),
                         'runtime_file': getattr(sys.modules.get('tinygeo'), '__file__', None),
                         'diagnostics': self.diagnostics, 'motion_enabled': self.motion_enabled,
                         'diagnostic_version': 2,
                         'ui_scheduler': getattr(self, 'ui_scheduler', 'controller-only'),
                         'main_state': getattr(self, 'main_state', 'controller-only'),
                         'native_fault_capture_requested': CAPTURE_NATIVE_FAULTS})
            self.diagnostic('start.ready')
            self.running, self.status = True, 'Waiting for location permission and a fresh fix'
        except Exception as exc:
            self.capture_error('start', exc)
            if self._journal is not None:
                self._journal.close()
                self._journal = None
            try:
                self.provider.stop_updates()
            except Exception as cleanup_error:
                self.capture_error('start.cleanup', cleanup_error)
            raise

    def stop(self):
        if not self.running:
            return
        self.running = False
        try:
            self.diagnostic('stop.provider.begin')
            self.provider.stop_updates()
            self.diagnostic('stop.provider.end')
        finally:
            try:
                self._write({'type': 'summary', 'recorded_at': self.clock(),
                             'events': self.tracker.snapshot()})
                self.diagnostic('stop.journal.close')
            finally:
                self._journal.close()
                self._journal = None
                self.status = 'Stopped; history saved'
                self.forecasts = []

    def _reject(self, reason):
        self.diagnostic('fix.rejected', reason=reason)
        self.status = reason
        if reason != self._last_rejection:
            self.tracker.break_track(reason)
            self._write({'type': 'gap', 'reason': reason, 'recorded_at': self.clock()})
            self._last_rejection = reason
        self.recent.clear()
        self.motion, self.forecasts = estimate_motion([], []), []

    def _refresh_motion(self):
        if not self.motion_enabled:
            self.motion, self.forecasts = estimate_motion([], []), []
            self.diagnostic('motion.disabled')
            return
        self.diagnostic('motion.estimate.begin', timestamps=[f['timestamp'] for f in self.recent])
        self.motion = estimate_motion([f['position'] for f in self.recent],
                                      [f['timestamp'] for f in self.recent],
                                      now=self.clock(), **self.motion_options)
        self.diagnostic('motion.estimate.end', motion=self.motion)
        self.diagnostic('motion.project.begin')
        self.forecasts = project_motion(self.motion, gazetteer=self.db)
        self.diagnostic('motion.project.end', forecast_count=len(self.forecasts))

    def poll(self):
        """Consume at most one new fix; stale/duplicate/out-of-order fixes never append."""
        if not self.running:
            return False
        try:
            self.diagnostic('location.get.begin')
            fix = self.provider.get_location()
            self.diagnostic('location.get.end', raw_fix=fix)
            if fix is None:
                self._reject('No fix: waiting for permission or a location update')
                return False
            if not isinstance(fix, dict):
                self._reject('Malformed location fix')
                return False
            stamp = fix.get('timestamp')
            if not isinstance(stamp, Real) or isinstance(stamp, bool) or not math.isfinite(stamp):
                self._reject('Invalid fix timestamp')
                return False
            stamp = float(stamp)
            if self.last_seen is not None and stamp <= self.last_seen:
                self.diagnostic('fix.duplicate_or_old', timestamp=stamp, previous=self.last_seen)
                self._refresh_motion()
                if self.motion['status'] == 'stale': self._reject('Latest fix is stale; forecasts paused')
                return False
            now = self.clock()
            if not -2 <= now-stamp <= self.max_fix_age_seconds:
                self._reject('Stale or future location fix')
                return False
            position = [fix.get('latitude'), fix.get('longitude')]
            if any(not isinstance(v, Real) or isinstance(v, bool) or not math.isfinite(v) for v in position):
                self._reject('Invalid coordinates')
                return False
            position = [float(v) for v in position]
            if abs(position[0]) > 90 or abs(position[1]) > 180:
                self._reject('Coordinates out of range')
                return False
            accuracy = fix.get('horizontal_accuracy', fix.get('accuracy'))
            if accuracy is not None:
                if (not isinstance(accuracy, Real) or isinstance(accuracy, bool) or not math.isfinite(accuracy)
                        or accuracy < 0 or accuracy > self.max_accuracy_m):
                    self._reject('Poor or invalid reported location accuracy')
                    return False
                accuracy = float(accuracy)
            normalized = {'timestamp': stamp, 'latitude': position[0], 'longitude': position[1],
                          'horizontal_accuracy': accuracy}
            altitude = fix.get('altitude')
            if isinstance(altitude, Real) and not isinstance(altitude, bool) and math.isfinite(altitude):
                normalized['altitude'] = float(altitude)
            self.last_seen = stamp
            self.diagnostic('track.update.begin')
            self.annotation = self.tracker.update(position, stamp)
            self.diagnostic('track.update.end')
            self.diagnostic('lookup.begin')
            self.current = self.db.locate(*position, k=3)
            self.diagnostic('lookup.end')
            self.last_fix, self._last_rejection = normalized, None
            self.recent.append({'position': position, 'timestamp': stamp})
            self._refresh_motion()
            self._write({'type': 'fix', 'fix': normalized, 'event_index': self.annotation['event_index'],
                         'feature_id': self.annotation['context']['feature_id'],
                         'context_name': self.annotation['context']['name'],
                         'context_kind': self.annotation['context_kind']})
            self.status = 'Recording in foreground'
            self.diagnostic('poll.end')
            return True
        except Exception as exc:
            self.capture_error(self._phase, exc)
            try:
                self.stop()
            except Exception as cleanup_error:
                self.capture_error('poll.cleanup', cleanup_error)
            raise


def history_entries(path):
    """Stream a journal, tolerating only an incomplete final line after a crash."""
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if not line.endswith('\n'):
                break
            yield json.loads(line)


def replay_history(path, *, gazetteer=None):
    entries = iter(history_entries(path))
    metadata = next(entries, None)
    if not metadata or metadata.get('type') != 'metadata' or metadata.get('format_version') != 1:
        raise ValueError('unsupported track journal')
    tracker = TrackTracker(gazetteer, **metadata['track_options'])
    if metadata['dataset_id'] != tracker.gazetteer.dataset_id:
        raise ValueError('replay needs the same dataset_id used by the recording')
    for entry in entries:
        if entry['type'] == 'fix':
            fix = entry['fix']
            tracker.update([fix['latitude'], fix['longitude']], fix['timestamp'])
        elif entry['type'] == 'gap':
            tracker.break_track(entry['reason'])
    return tracker.snapshot()


def export_csv(journal, destination):
    """Export accepted fixes and stable labels to a local CSV file."""
    metadata = None
    with Path(destination).open('w', newline='', encoding='utf-8') as stream:
        fields = ['timestamp', 'latitude', 'longitude', 'altitude', 'horizontal_accuracy',
                  'context_name', 'context_kind', 'event_index', 'dataset_id']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for entry in history_entries(journal):
            if entry['type'] == 'metadata': metadata = entry
            if entry['type'] != 'fix': continue
            row = {key: entry.get(key) for key in ('context_name', 'context_kind', 'event_index')}
            row.update(entry['fix'], dataset_id=metadata['dataset_id'])
            writer.writerow(row)
    return Path(destination)


def _display(session):
    lines = [session.status, 'Foreground recording; keep Pythonista open.']
    if session.db.synthetic: lines.append('SYNTHETIC DEMO DATA: install a real deployment for useful context.')
    if session.last_fix:
        fix = session.last_fix
        stamp = datetime.fromtimestamp(fix['timestamp'], timezone.utc).strftime('%H:%M:%S UTC')
        lines += ['', f"{fix['latitude']:.5f}, {fix['longitude']:.5f}",
                  f"Fix {stamp} · age {max(0, session.clock()-fix['timestamp']):.0f}s",
                  session.current['description']]
        for match in session.current['neighbors']:
            label = ', '.join(v for v in (match['name'], match['region'], match['country_code']) if v)
            bearing = '—' if match['bearing_deg'] is None else f"{match['bearing_deg']:.0f}°"
            lines.append(f"{label}: center {match['distance_km']:.1f} km {bearing}; zone {match['distance_to_area_km']:.1f} km")
    motion = session.motion
    lines += ['', 'Motion: '+motion['status'].replace('_', ' ')]
    if motion['speed_m_s'] is not None:
        course = '—' if motion['course_deg'] is None else f"{motion['course_deg']:.0f}°"
        lines.append(f"{motion['speed_m_s']*3.6:.1f} km/h · course {course}")
    lines += ['', 'Extrapolations assuming unchanged motion:']
    for f in session.forecasts:
        time_label = datetime.fromtimestamp(f['timestamp'], timezone.utc).strftime('%H:%M UTC')
        lines.append(f"+{f['horizon_seconds']/60:.0f} min ({time_label}): {f['lat']:.3f}, {f['lon']:.3f}")
        lines.append(f"  {f['motion_status']} · {f['context']['description']}")
    if not session.forecasts: lines.append('Waiting for reliable recent motion; no forecasts.')
    lines += ['', 'Recent observed reference context (not inferred visits):']
    for event in session.tracker.events[-10:]:
        if event['type'] == 'gap': lines.append('  Gap: '+event['reason'])
        else:
            near = event['closest_observed']['reference']
            lines.append(f"  {event['feature']['name']}: closest observed {near['distance_to_area_km']:.1f} km from zone")
    if session.path: lines += ['', 'Local history: '+session.path.name]
    return '\n'.join(lines)


@contextmanager
def _native_fault_logging(directory):
    """Best-effort native stacks; an iOS SIGKILL cannot run a fault handler."""
    stream, owned = None, False
    try:
        if CAPTURE_NATIVE_FAULTS:
            import faulthandler
            directory.mkdir(parents=True, exist_ok=True)
            stream = (directory/'native-fault.log').open('a', encoding='utf-8')
            stream.write('\nDiagnostic launch: '+datetime.now(timezone.utc).isoformat()+'\n')
            if faulthandler.is_enabled():
                stream.write('An existing fault handler is enabled; leaving its destination unchanged.\n')
            else:
                faulthandler.enable(file=stream, all_threads=True)
                owned = True
                stream.write('Native fault capture enabled for this run.\n')
            stream.flush()
    except Exception as exc:
        print('Native fault capture unavailable:', repr(exc))
        if stream is not None:
            try:
                stream.write('Native fault capture unavailable: '+repr(exc)+'\n')
                stream.flush()
            except Exception:
                pass
    try:
        yield
    finally:
        if owned:
            faulthandler.disable()
        if stream is not None:
            stream.close()


def main():
    import location
    import ui

    class Companion(ui.View):
        def __init__(self):
            self.name, self.background_color = 'TinyGeo track', 'white'
            self.session = TrackSession(location, Path(__file__).resolve().parent/'track_history',
                                        diagnostics=DIAGNOSTICS, motion_enabled=ENABLE_MOTION)
            self.session.ui_scheduler = 'View.update'
            self.generation, self.closed = 0, False
            self._in_tick = False
            self.update_interval = 0.0
            self.buttons = []
            for title, action in [('Start', self.start), ('Stop', self.stop), ('Export CSV', self.export)]:
                button = ui.Button(title=title)
                button.action = action
                self.add_subview(button)
                self.buttons.append(button)
            self.text = ui.TextView()
            self.text.editable, self.text.font = False, ('Menlo', 12)
            self.text.background_color, self.text.text_color = 'white', '#172033'
            self.add_subview(self.text)
            self.render()

        def layout(self):
            width = (self.width-32)/3
            for i, button in enumerate(self.buttons): button.frame = (8+i*(width+8), 8, width, 44)
            self.text.frame = (8, 60, self.width-16, self.height-68)

        def render(self):
            try:
                self.session.diagnostic('ui.display.begin')
                text = _display(self.session)
                self.session.diagnostic('ui.display.end', character_count=len(text))
                self.session.diagnostic('ui.text.begin')
                self.text.text = text
                self.session.diagnostic('ui.text.end')
                self.buttons[0].enabled = not self.session.running
                self.buttons[1].enabled = self.session.running
                self.buttons[2].enabled = self.session.path is not None
                self.session.diagnostic('ui.render.end')
                return True
            except Exception as exc:
                self.fail('ui.render', exc)
                return False

        def fail(self, stage, exc):
            self.update_interval = 0.0
            self.session.capture_error(stage, exc)
            self.generation += 1
            try:
                self.session.stop()
            except Exception as cleanup_error:
                self.session.capture_error('ui.cleanup', cleanup_error)
            self.session.status = f'Stopped after {type(exc).__name__}: {exc}'
            # Do not call render again: the failure may be in its formatting.
            try:
                self.text.text = self.session.status+'\nSee track_history/errors.log and the JSONL journal.'
                self.buttons[0].enabled, self.buttons[1].enabled = True, False
                self.buttons[2].enabled = self.session.path is not None
            except Exception as display_error:
                self.session.capture_error('ui.error_display', display_error)

        def start(self, sender):
            if self.closed or self.session.running:
                return
            try:
                self.session.start()
                self.generation += 1
                self.session.diagnostic('ui.update.enable', interval_seconds=2.0)
                self.update_interval = 2.0
                self.tick(self.generation)
            except Exception as exc:
                self.fail('ui.start', exc)

        def update(self):
            """Use the view-owned periodic callback, without delayed closures."""
            if self.closed or not self.session.running:
                return
            self.session.diagnostic('ui.update.begin', generation=self.generation)
            self.tick(self.generation)
            self.session.diagnostic('ui.update.end')

        def tick(self, generation):
            if self.closed or generation != self.generation or not self.session.running: return
            if self._in_tick:
                self.session.diagnostic('ui.tick.skipped_busy')
                return
            self._in_tick = True
            try:
                self.session.diagnostic('ui.tick.begin', generation=generation)
                self.session.poll()
                self.session.diagnostic('ui.poll.end')
                self.render()
            except Exception as exc:
                self.fail('ui.tick', exc)
            finally:
                self._in_tick = False

        def stop(self, sender=None):
            self.update_interval = 0.0
            self.session.diagnostic('ui.update.disable')
            self.generation += 1
            try:
                self.session.stop()
            except Exception as exc:
                self.fail('ui.stop', exc)
            else:
                self.render()

        def export(self, sender):
            try:
                destination = self.session.path.with_suffix('.csv')
                export_csv(self.session.path, destination)
                self.session.status = 'Saved '+destination.name+' beside the local journal'
            except Exception as exc:
                self.session.capture_error('ui.export', exc)
                self.session.status = 'Export error: '+str(exc)
            self.render()

        def will_close(self):
            self.session.diagnostic('ui.will_close')
            self.closed = True
            self.stop()

    with _native_fault_logging(Path(__file__).resolve().parent/'track_history'):
        view = Companion()
        try:
            view.session.main_state = 'presenting'
            view.session.diagnostic('main.present.begin')
            view.present('fullscreen')
            view.session.main_state = 'waiting'
            view.session.diagnostic('main.wait.begin')
            view.wait_modal()
            view.session.main_state = 'wait-returned'
            view.session.diagnostic('main.wait.end')
        except Exception as exc:
            view.session.capture_error('main.lifecycle', exc)
            raise
        finally:
            view.update_interval = 0.0
            view.session.diagnostic('main.cleanup')
            try:
                view.session.stop()
            except Exception as cleanup_error:
                view.session.capture_error('main.cleanup', cleanup_error)


if __name__ == '__main__':
    main()
