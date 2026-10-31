import gzip
import json

import pytest

from tinygeo import Gazetteer, dataset_info
from tinygeo.data import decode_dataset


@pytest.fixture
def dataset():
    return {"schema_version": 1, "dataset_id": "fixture", "kind": "geographic",
            "records": [{"source_id": "test:1", "name": "École", "lat": 10, "lon": 20,
                         "feature_type": "landmark", "population": None}]}


@pytest.mark.parametrize("compressed", [False, True])
def test_external_dataset_loading(tmp_path, dataset, compressed):
    payload = json.dumps(dataset, ensure_ascii=False).encode("utf-8")
    path = tmp_path / "data.bin"
    path.write_bytes(gzip.compress(payload) if compressed else payload)
    db = Gazetteer.from_file(path)
    assert db.locate(10, 20)["nearest"]["name"] == "École"
    assert db.dataset_id == "fixture"
    assert db.synthetic is False
    assert db.metadata["record_count"] == 1
    assert "records" not in db.metadata


@pytest.mark.parametrize("change", [
    {"schema_version": 2}, {"records": []}, {"dataset_id": ""},
    {"kind": "unknown"}, {"record_count": 999},
])
def test_invalid_dataset_schema(dataset, change):
    dataset.update(change)
    with pytest.raises(ValueError):
        decode_dataset(json.dumps(dataset).encode())


def test_metadata_is_not_mutable_through_dataset_info():
    info = dataset_info()
    info["dataset_id"] = "changed"
    assert dataset_info()["dataset_id"] == "synthetic-demo-v1"
    assert dataset_info()["record_count"] == 6
