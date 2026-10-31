"""Portable JSON/gzip dataset loading; no builder dependencies or network."""

import gzip
from importlib.resources import files
import json
from pathlib import Path


def decode_dataset(payload):
    if payload.startswith(b"\x1f\x8b"):
        payload = gzip.decompress(payload)
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") not in {1, 2}:
        raise ValueError("unsupported TinyGeo dataset schema")
    if not isinstance(data.get("records"), list) or not data["records"]:
        raise ValueError("dataset must contain a nonempty records list")
    if "record_count" in data and data["record_count"] != len(data["records"]):
        raise ValueError("dataset record_count does not match its records")
    if not isinstance(data.get("dataset_id"), str) or not data["dataset_id"]:
        raise ValueError("dataset must have a dataset_id")
    if data.get("kind") not in {"synthetic", "geographic"}:
        raise ValueError("dataset kind must be synthetic or geographic")
    return data


def load_dataset(path):
    """Read a versioned UTF-8 JSON dataset, optionally gzip-compressed."""
    return decode_dataset(Path(path).read_bytes())


def load_default_dataset():
    resource = files("tinygeo").joinpath("resources/geodb.json.gz")
    if not resource.is_file():
        resource = files("tinygeo").joinpath("resources/demo.json")
    return decode_dataset(resource.read_bytes())
