"""Exercise the portable handoff in isolation with a separately supplied database."""

from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile

from tools.package_llm_skill import NAME, package_skill


def test_skill_package_external_database_and_documented_annotation(tmp_path):
    first = package_skill(tmp_path/'first.zip')
    second = package_skill(tmp_path/'second.zip')
    assert first.read_bytes() == second.read_bytes()
    extracted = tmp_path/'sandbox'
    with ZipFile(first) as archive:
        archive.extractall(extracted)
    skill = extracted/NAME
    manifest = json.loads((skill/'manifest.json').read_text())
    assert manifest['database_included'] is False
    for name, digest in manifest['files_sha256'].items():
        assert sha256((extracted/name).read_bytes()).hexdigest() == digest
    assert not list((skill/'assets/tinygeo').rglob('*.json'))
    database = tmp_path/'supplied-database.json'
    database.write_text(json.dumps({'schema_version': 2, 'kind': 'geographic',
        'dataset_id': 'separate-test-data', 'record_count': 3, 'feature_count': 2,
        'attribution': 'Test fixture only', 'records': [
            {'source_id':'town', 'name':'Harbor Town', 'lat':0, 'lon':179.9,
             'feature_type':'town', 'country_code':'XX', 'region':'Test Region'},
            {'source_id':'west', 'feature_id':'ocean', 'name':'Test Ocean', 'lat':0, 'lon':179,
             'feature_type':'ocean', 'radius_km':400},
            {'source_id':'east', 'feature_id':'ocean', 'name':'Test Ocean', 'lat':0, 'lon':-179,
             'feature_type':'ocean', 'radius_km':400}]}))
    # Execute the actual published examples, not a second implementation of them.
    blocks = re.findall(r'```python\n(.*?)```', (skill/'SKILL.md').read_text(), flags=re.S)
    script = f'''
import sys,socket
from pathlib import Path
for name in ('scipy','geopy','shapely','shapefile','cartopy'):sys.modules[name]=None
def offline(*args,**kwargs): raise AssertionError('Network forbidden')
socket.socket=offline
runtime_parent=Path({str(skill/'assets')!r})
database_path=Path({str(database)!r})
observer_rows=[
 {{'timestamp':'2026-10-31T00:00:00Z','lat_deg':0.,'lon_deg':179.95,'height_m':10000.,'az_deg':37.6,'el_deg':12.3}},
 {{'timestamp':'2026-10-31T00:00:10Z','lat_deg':0.,'lon_deg':-179.95,'height_m':10001.,'az_deg':100.,'el_deg':-5.}}
]
'''
    script += '\n'.join(blocks)
    script += f'''
import tinygeo
assert Path(tinygeo.__file__).resolve().parent == runtime_parent/'tinygeo'
assert provenance['dataset_id']=='separate-test-data'
assert provenance['feature_count']==2
assert len(annotated)==2
for row,original in zip(annotated,observer_rows):
 for field,value in original.items(): assert row[field]==value
 assert row['place_context']['nearest_settlement']['name']=='Harbor Town'
 assert row['place_context']['nearest_region']['name']=='Test Ocean'
 assert len(row['place_context']['neighbors'])==2  # Circles group by feature ID.
assert annotated[0]['place_context']['nearest_settlement']['bearing_deg']!=annotated[0]['az_deg']
assert 5.5 < settlement_distance_km < 5.6
assert abs(settlement_bearing_deg-270) < 1e-8
assert 'Harbor Town' in settlement_summary and 'undefined' not in settlement_summary
assert place['nearest']['feature_type']=='ocean'  # Its distances must not replace the settlement's.
try: tinygeo.locate(0,0)
except FileNotFoundError: pass
else: raise AssertionError('The handoff must not silently include a default database')
'''
    subprocess.run([sys.executable, '-I', '-c', script], cwd=extracted,
                   capture_output=True, text=True, check=True, timeout=30)
