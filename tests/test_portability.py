"""Exercise a fresh runtime import without optional builder dependencies."""

from pathlib import Path
import subprocess
import sys


def test_fresh_import_and_lookup_without_scipy():
    source = str(Path(__file__).resolve().parents[1] / "src")
    script = f"""
import sys
sys.path.insert(0, {source!r})
sys.modules['scipy'] = None
from tinygeo import locate, locate_many
assert locate(0, 0)['nearest']['source_id'] == 'demo:origin'
assert len(locate_many([[0, 0], [0, 90]], k=3)) == 2
assert not any(name.startswith('scipy.') for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-c", script], check=True,
                   capture_output=True, text=True, timeout=30)
