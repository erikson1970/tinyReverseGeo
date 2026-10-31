"""Package the portable LLM skill and NumPy-only runtime; database stays external."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
NAME = 'tinygeo-track-context'


def package_skill(output):
    skill = ROOT/'skills'/NAME
    payload = {f'{NAME}/SKILL.md': (skill/'SKILL.md').read_bytes()}
    for path in sorted((ROOT/'src/tinygeo').glob('*.py')):
        payload[f'{NAME}/assets/tinygeo/{path.name}'] = path.read_bytes()
    payload[f'{NAME}/assets/LICENSE'] = (ROOT/'LICENSE').read_bytes()
    manifest = {'format_version': 1, 'skill': NAME, 'python_min': '3.10',
                'runtime_dependencies': ['numpy>=1.21.6'], 'database_included': False,
                'database_usage': 'Gazetteer.from_file(user_supplied_path); no default database',
                'files_sha256': {name: sha256(value).hexdigest() for name, value in sorted(payload.items())}}
    payload[f'{NAME}/manifest.json'] = (json.dumps(manifest, sort_keys=True, indent=2)+'\n').encode()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(payload.items()):
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type, entry.external_attr = ZIP_DEFLATED, 0o100644 << 16
            archive.writestr(entry, value)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/tinygeo-track-context-skill.zip')
    args = parser.parse_args()
    output = package_skill(args.output)
    print('Skill + runtime:', output)
    print('Supply the external geographic dataset separately. Python 3.10+ and NumPy are required.')
    return output


if __name__ == '__main__': main()
