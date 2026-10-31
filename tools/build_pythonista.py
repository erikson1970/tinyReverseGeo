"""Build a portable demo ZIP using only the standard library."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=root / "dist/tinygeo-pythonista-demo.zip")
    args = parser.parse_args()
    package = root / "src/tinygeo"
    payload = {
        "tinygeo/" + path.relative_to(package).as_posix(): path.read_bytes()
        for path in sorted(package.rglob("*"))
        if path.is_file() and path.suffix in {".py", ".json", ".gz", ".npz"}
    }
    for source, destination in (
        ("examples/pythonista_smoke.py", "pythonista_smoke.py"),
        ("tools/benchmark_lookup.py", "benchmark_lookup.py"),
        ("docs/pythonista.md", "README.md"),
        ("LICENSE", "LICENSE"), ("LICENSES.md", "LICENSES.md"),
    ):
        payload[destination] = (root / source).read_bytes()
    metadata = json.loads((package / "resources/demo.json").read_text(encoding="utf-8"))
    payload["manifest.json"] = (json.dumps({
        "format_version": 1, "dataset_id": metadata["dataset_id"],
        "synthetic": metadata["kind"] == "synthetic",
        "files_sha256": {name: sha256(data).hexdigest()
                         for name, data in sorted(payload.items())},
    }, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=ZIP_DEFLATED, compresslevel=9)
    print(f"Wrote {args.output} ({args.output.stat().st_size:,} bytes)")
    print(f"SHA-256: {sha256(args.output.read_bytes()).hexdigest()}")
    print("Synthetic demo data; physical Pythonista verification is pending.")


if __name__ == "__main__":
    main()
