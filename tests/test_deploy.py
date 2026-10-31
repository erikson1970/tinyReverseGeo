from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

pytest.importorskip("scipy")

from tools import geodb
from tools.deploy import main


def row(identity, lat, lon, *, name=None, feature_class="P", code="PPL", population=1000):
    fields = [str(identity), name or f"Place {identity}", "", "", str(lat), str(lon),
              feature_class, code, "XX", "", "01", "", "", "", str(population), "", "", "", "2026-10-31"]
    return "\t".join(fields) + "\n"


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "fixture.txt"
    text = "".join(row(i, -70 + i * 4, -170 + i * 9, population=i * 1000) for i in range(30))
    text += row(100, 20, 30, name="Café Peak", feature_class="T", code="MT", population=0)
    text += row(101, -30, -40, feature_class="S", code="MONU", population=0)
    text += row(102, 0, 0, feature_class="A", code="ADM1", population=0)
    path.write_text(text, encoding="utf-8")
    return path


def test_ingest_and_selection(source, tmp_path):
    candidates, stats = geodb.collect_candidates([source], 12, {"XX.01": "Example Region"})
    assert stats["source_rows"] == 33
    assert stats["eligible_rows"] == 32
    assert {c.group for c in candidates} == {"settlement", "natural", "landmark"}
    selected = geodb.select_candidates(candidates, 12)
    assert len(selected) == 12
    assert all(c.record["region"] == "Example Region" for c in selected)
    assert any(c.record["feature_type"] == "mountain" for c in selected)
    assert any(c.record["feature_type"] == "landmark" for c in selected)
    assert geodb.select_candidates(list(reversed(candidates)), 12) == selected
    reverse = tmp_path / "reverse.txt"
    reverse.write_text("".join(reversed(source.read_text().splitlines(keepends=True))), encoding="utf-8")
    reversed_candidates, _ = geodb.collect_candidates([reverse], 12, {"XX.01": "Example Region"})
    assert geodb.select_candidates(reversed_candidates, 12) == selected


def test_pins_count_and_insufficient_candidates(source):
    candidates, _ = geodb.collect_candidates([source], 8)
    pinned = candidates[-1].source_id
    selected = geodb.select_candidates(candidates, 8, pinned=[pinned] * 20)
    assert len(selected) == 8 and pinned in {c.source_id for c in selected}
    with pytest.raises(ValueError, match="only"):
        geodb.select_candidates(candidates, 1000)
    with pytest.raises(ValueError, match="absent"):
        geodb.select_candidates(candidates, 8, pinned=["missing"])


def test_selection_retains_sparse_context(tmp_path):
    path = tmp_path / "coverage.txt"
    # Many large cities in one small cluster, plus smaller settlements worldwide.
    text = "".join(row(i, 40 + i / 10000, 10 + i / 10000, population=1_000_000) for i in range(100))
    text += "".join(row(200 + i, lat, lon, population=1000)
                    for i, (lat, lon) in enumerate([(-60, -120), (-30, 120), (0, -90), (30, 90), (65, -40)]))
    path.write_text(text)
    candidates, _ = geodb.collect_candidates([path], 10)
    selected = geodb.select_candidates(candidates, 10)
    assert sum(c.record["population"] == 1000 for c in selected) == 5


def test_zip_ingestion_and_bad_rows(source, tmp_path):
    archive = tmp_path / "fixture.zip"
    with ZipFile(archive, "w") as out:
        out.writestr("fixture.txt", source.read_bytes())
        out.writestr("readme.txt", "not a record")
    assert list(geodb.text_lines(archive)) == list(geodb.text_lines(source))
    bad = tmp_path / "bad.txt"
    bad.write_text("not a valid row\n")
    with pytest.raises(ValueError, match="bad.txt:1"):
        geodb.collect_candidates([bad], 1)


def test_source_cache_pinning_and_integrity(tmp_path, monkeypatch):
    responses = [b"first", b"second"]

    def download(request, timeout):
        stream = io.BytesIO(responses.pop(0))
        stream.headers = {"Last-Modified": "snapshot"}
        return stream

    monkeypatch.setattr(geodb, "urlopen", download)
    first, info = geodb.fetch_source("cities500.zip", tmp_path)
    assert first.read_bytes() == b"first"
    second, _ = geodb.fetch_source("cities500.zip", tmp_path, refresh=True)
    assert second.read_bytes() == b"second" and first != second
    restored, _ = geodb.fetch_source("cities500.zip", tmp_path, offline=True, locked=info)
    assert restored == first
    first.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum mismatch"):
        geodb.fetch_source("cities500.zip", tmp_path, offline=True, locked=info)
    with pytest.raises(ValueError, match="not cached"):
        geodb.fetch_source("missing.zip", tmp_path, offline=True)
    first.unlink()
    responses.append(b"changed upstream")
    with pytest.raises(ValueError, match="changed"):
        geodb.fetch_source("cities500.zip", tmp_path, locked=info)
    assert not (tmp_path / "cities500.zip.download").exists()


@pytest.mark.parametrize("layout,target", [("external", "pythonista"), ("embedded", "python")])
def test_end_to_end_offline_build(source, tmp_path, monkeypatch, layout, target):
    def blocked(*args, **kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(geodb, "urlopen", blocked)
    output = tmp_path / "output"
    arguments = ["--input", str(source), "--points", "12", "--layout", layout,
                 "--target", target, "--offline", "--output", str(output)]
    archive, manifest = main(arguments)
    original = archive.read_bytes()
    assert manifest["record_count"] == 12
    assert len(manifest["runtime_dependencies"]) == 1
    main(arguments + ["--lock", str(output / "source-lock.json")])
    assert archive.read_bytes() == original
    extracted = tmp_path / "extracted"
    with ZipFile(archive) as bundle:
        bundle.extractall(extracted)
        assert not any("geopy" in n or "scipy" in n or n.startswith("tools/") for n in bundle.namelist())
    for name, expected in manifest["files_sha256"].items():
        assert sha256((extracted / name).read_bytes()).hexdigest() == expected
    script = f"""
import sys
sys.path.insert(0, {str(extracted)!r})
sys.modules['scipy'] = None
sys.modules['geopy'] = None
from tinygeo import dataset_info, locate, Gazetteer
import tinygeo
assert set(tinygeo.__all__) == {{'Gazetteer', 'Reference', 'dataset_info', 'locate', 'locate_many'}}
info = dataset_info()
assert info['record_count'] == 12 and info['kind'] == 'geographic'
assert locate(20, 30)['nearest']['name'] == 'Café Peak'
assert not locate(20, 30)['synthetic']
db = Gazetteer.from_file({str(output / 'dataset.json.gz')!r})
assert db.dataset_id == info['dataset_id']
"""
    subprocess.run([sys.executable, "-I", "-c", script], check=True, capture_output=True, text=True)
    subprocess.run([sys.executable, str(extracted / "smoke_test.py")], cwd=extracted,
                   check=True, capture_output=True, text=True)
    if layout == "embedded":
        # Copy only the .py file into a new application folder.
        isolated = tmp_path / "single-file"
        isolated.mkdir()
        (isolated / "tinygeo.py").write_bytes((output / "tinygeo.py").read_bytes())
        subprocess.run([sys.executable, "-I", "-c",
                        f"import sys; sys.path.insert(0, {str(isolated)!r}); import tinygeo; assert tinygeo.dataset_info()['record_count'] == 12"], check=True)


def test_cli_rejects_invalid_configuration(source, tmp_path):
    for args in (["--points", "0"], ["--refresh", "--offline"],
                 ["--input", str(source), "--countries", "US"]):
        with pytest.raises(SystemExit) as error:
            main(args + ["--output", str(tmp_path / "output")])
        assert error.value.code == 2
