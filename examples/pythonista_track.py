"""Foreground Pythonista track companion; also importable for desktop replay.

Run beside a deployed tinygeo package/module. History stays in track_history/.
Only main() imports iOS location/ui. No platform reverse-geocoding or map download.
"""

from collections import deque
import csv
from datetime import datetime, timezone
import json
import math
from numbers import Real
from pathlib import Path
import time
from uuid import uuid4

from tinygeo import TrackTracker, estimate_motion, project_motion


class TrackSession:
    """Provider-independent foreground acquisition, persistence, and forecasts."""

    def __init__(self, provider, directory, *, gazetteer=None, clock=time.time,
                 max_accuracy_m=200, max_fix_age_seconds=30, track_options=None, motion_options=None):
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
            self.motion, self.forecasts = estimate_motion([], []), []
            self._write({'type': 'metadata', 'format_version': 1, 'created_at': self.clock(),
                         'dataset_id': self.db.dataset_id, 'synthetic': self.db.synthetic,
                         'track_options': self.track_options, 'motion_options': self.motion_options})
            self.running, self.status = True, 'Waiting for location permission and a fresh fix'
        except Exception:
            if self._journal is not None:
                self._journal.close()
                self._journal = None
            self.provider.stop_updates()
            raise

    def stop(self):
        if not self.running:
            return
        self.running = False
        try:
            self.provider.stop_updates()
        finally:
            try:
                self._write({'type': 'summary', 'recorded_at': self.clock(),
                             'events': self.tracker.snapshot()})
            finally:
                self._journal.close()
                self._journal = None
                self.status = 'Stopped; history saved'
                self.forecasts = []

    def _reject(self, reason):
        self.status = reason
        if reason != self._last_rejection:
            self.tracker.break_track(reason)
            self._write({'type': 'gap', 'reason': reason, 'recorded_at': self.clock()})
            self._last_rejection = reason
        self.recent.clear()
        self.motion, self.forecasts = estimate_motion([], []), []

    def _refresh_motion(self):
        self.motion = estimate_motion([f['position'] for f in self.recent],
                                      [f['timestamp'] for f in self.recent],
                                      now=self.clock(), **self.motion_options)
        self.forecasts = project_motion(self.motion, gazetteer=self.db)

    def poll(self):
        """Consume at most one new fix; stale/duplicate/out-of-order fixes never append."""
        if not self.running:
            return False
        try:
            fix = self.provider.get_location()
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
            self.annotation = self.tracker.update(position, stamp)
            self.current = self.db.locate(*position, k=3)
            self.last_fix, self._last_rejection = normalized, None
            self.recent.append({'position': position, 'timestamp': stamp})
            self._refresh_motion()
            self._write({'type': 'fix', 'fix': normalized, 'event_index': self.annotation['event_index'],
                         'feature_id': self.annotation['context']['feature_id'],
                         'context_name': self.annotation['context']['name'],
                         'context_kind': self.annotation['context_kind']})
            self.status = 'Recording in foreground'
            return True
        except Exception:
            self.stop()
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


def main():
    import location
    import ui

    class Companion(ui.View):
        def __init__(self):
            self.name, self.background_color = 'TinyGeo track', 'white'
            self.session = TrackSession(location, Path(__file__).resolve().parent/'track_history')
            self.generation, self.closed = 0, False
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
            self.text.text = _display(self.session)
            self.buttons[0].enabled = not self.session.running
            self.buttons[1].enabled = self.session.running
            self.buttons[2].enabled = self.session.path is not None

        def start(self, sender):
            try:
                self.session.start()
                self.generation += 1
                self.tick(self.generation)
            except Exception as exc:
                self.session.status = 'Error: '+str(exc)
                self.render()

        def tick(self, generation):
            if self.closed or generation != self.generation or not self.session.running: return
            try:
                self.session.poll()
            except Exception as exc:
                self.session.status = 'Stopped after error: '+str(exc)
            self.render()
            if self.session.running: ui.delay(lambda: self.tick(generation), 2.0)

        def stop(self, sender=None):
            self.generation += 1
            try:
                self.session.stop()
            finally:
                self.render()

        def export(self, sender):
            try:
                destination = self.session.path.with_suffix('.csv')
                export_csv(self.session.path, destination)
                self.session.status = 'Saved '+destination.name+' beside the local journal'
            except Exception as exc:
                self.session.status = 'Export error: '+str(exc)
            self.render()

        def will_close(self):
            self.closed = True
            self.stop()

    view = Companion()
    try:
        view.present('fullscreen')
        view.wait_modal()
    finally:
        view.session.stop()


if __name__ == '__main__':
    main()
