"""Bundle launchers must survive persistent imports and namespace-folder nesting."""

from pathlib import Path
import subprocess
import sys

import pytest

from tools.deploy import SMOKE, compressed_json, package_payload, PROFILES
from tools.launcher import wrap_launcher

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('layout', ['external', 'embedded'])
@pytest.mark.parametrize('cached', ['namespace', 'old_package', 'same_path_old_code'])
@pytest.mark.parametrize('entry', ['smoke_test.py', 'pythonista_track.py', 'benchmark_deployed.py'])
def test_launcher_selects_own_runtime_over_cached_copies(tmp_path, layout, cached, entry):
    # Match the user's outer folder named tinygeo, containing the actual package.
    bundle = tmp_path/'TinyGeo2'/'tinygeo'
    bundle.mkdir(parents=True)
    dataset = {'schema_version': 2, 'dataset_id': 'current-bundle', 'kind': 'geographic',
               'attribution': 'Numerical test fixture only',
               'record_count': 2, 'records': [
                   {'source_id': 'origin', 'name': 'Origin', 'lat': 0, 'lon': 0, 'feature_type': 'town'},
                   {'source_id': 'boston', 'name': 'Boston', 'lat': 42.36, 'lon': -71.06, 'feature_type': 'city'}]}
    for name, contents in package_payload(compressed_json(dataset), layout, PROFILES['pythonista']).items():
        path = bundle/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    source = SMOKE if entry == 'smoke_test.py' else (ROOT/('examples/pythonista_track.py' if entry == 'pythonista_track.py' else 'tools/benchmark_lookup.py')).read_bytes()
    script_path = bundle/entry
    script_path.write_bytes(wrap_launcher(source))
    expected = bundle/('tinygeo/__init__.py' if layout == 'external' else 'tinygeo.py')
    code = f'''
import sys,types,runpy
from pathlib import Path
sys.modules['scipy']=None
sys.modules['geopy']=None
old=types.ModuleType('tinygeo')
old.OLD_COPY_SENTINEL=True
old.__path__=['/old/TinyGeo/tinygeo']
old.__file__={str(expected)!r} if {cached!r}=='same_path_old_code' else ('/old/TinyGeo/tinygeo/__init__.py' if {cached!r}=='old_package' else None)
sys.modules['tinygeo']=old
child=types.ModuleType('tinygeo.tinygeo')
old.tinygeo=child
sys.modules['tinygeo.tinygeo']=child
sys.modules['tinygeo.lookup']=types.ModuleType('tinygeo.lookup')
result=runpy.run_path({str(script_path)!r},run_name='bundle_test')
import tinygeo
assert Path(tinygeo.__file__)==Path({str(expected)!r})
assert not hasattr(tinygeo,'OLD_COPY_SENTINEL')
assert 'tinygeo.tinygeo' not in sys.modules
assert tinygeo.dataset_info()['dataset_id']=='current-bundle'
assert callable(tinygeo.describe_track)
assert callable(tinygeo.estimate_motion)
assert callable(tinygeo.project_motion)
assert result['__doc__']  # Preserve benchmark help and launcher docstrings.
'''
    subprocess.run([sys.executable, '-I', '-c', code], cwd=tmp_path,
                   capture_output=True, text=True, check=True, timeout=30)


def test_missing_adjacent_runtime_does_not_fall_back_to_an_old_installation(tmp_path):
    path = tmp_path/'smoke_test.py'
    path.write_bytes(wrap_launcher('from tinygeo import dataset_info\n'))
    code = f'''
import sys,types,runpy
old=types.ModuleType('tinygeo')
old.dataset_info=lambda:{{'dataset_id':'old-installation'}}
sys.modules['tinygeo']=old
try:
 runpy.run_path({str(path)!r})
except ImportError as error:
 assert 'Extract the whole ZIP' in str(error)
 assert str({str(tmp_path)!r}) in str(error)
else:
 raise AssertionError('Must not use an unrelated runtime when bundle files are missing')
assert sys.modules['tinygeo'] is old
'''
    subprocess.run([sys.executable, '-I', '-c', code], capture_output=True,
                   text=True, check=True, timeout=30)


@pytest.mark.parametrize('debug', [True, False])
def test_tracker_variant_has_explicit_diagnostic_defaults(tmp_path, debug):
    from tools.launcher import tracker_launcher
    folder = tmp_path/'tinygeo'
    folder.mkdir()
    (folder/'__init__.py').write_text('')
    script = '''"""Variant test."""
DIAGNOSTICS = False
CAPTURE_NATIVE_FAULTS = False
ENABLE_MOTION = True
class Session:
    def __init__(self, diagnostics=None):
        self.diagnostics = DIAGNOSTICS if diagnostics is None else diagnostics
'''
    path = tmp_path/'pythonista_track.py'
    path.write_bytes(tracker_launcher(script, diagnostics=debug))
    code = f'''
import runpy
app = runpy.run_path({str(path)!r})
assert app['DIAGNOSTICS'] is {debug!r}
assert app['CAPTURE_NATIVE_FAULTS'] is {debug!r}
assert app['ENABLE_MOTION'] is True
assert app['Session']().diagnostics is {debug!r}
'''
    subprocess.run([sys.executable,'-I','-c',code],cwd=tmp_path,check=True,capture_output=True,text=True)
