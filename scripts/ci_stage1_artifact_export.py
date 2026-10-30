"""Export existing native Stage1 bytes bound to this workflow run and attempt."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.ci_stage1_bundle import (
    CONFIG, PLATFORMS, PROFILE, RUNTIME, SCHEMA, SHARED, STAGE, MAX_FILE, MAX_TOTAL,
    SAFE_MODES, digest, host_platform, layout, regular_file, require as _require,
    valid_digest as _digest, validate_receipts, verify_native_header, workflow_lease,
)

TARGET = PLATFORMS["linux-x86_64"]["target"]  # legacy caller constant
OUTPUT_DIRECTORY = "build/stage1-native-export"
ARCHIVE_NAME = "stage1-native.tar.gz"


def _admit_runtime(root, runtime_path, target):
    # This verifier reads existing bytes/source/config; it never provisions.
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive
    checked, provenance = _verified_test_runtime_archive(
        runtime_path, threads=True, runtime_root=root / "pcc/runtime",
    )
    _require(checked == runtime_path and provenance["target_triple"] == target,
             "runtime provenance target differs")
    _require(all(row["runtime_build_config"] == CONFIG for row in provenance["members"]),
             "runtime provenance must be threaded atomic")


class _HashingReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size):
        data = self.stream.read(size)
        self.digest.update(data)
        return data


def _file_snapshot(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)


def _require_opened_identity(path_info, fd_info, name, platform_id):
    # Windows lstat adds execute bits for executable filename extensions, while
    # fstat has no filename and does not. Keep each mode in its own channel;
    # bind the opened regular file to the pathname by actual file identity.
    _require(stat.S_ISREG(fd_info.st_mode)
             and (fd_info.st_dev, fd_info.st_ino, fd_info.st_size, fd_info.st_mtime_ns)
             == (path_info.st_dev, path_info.st_ino, path_info.st_size, path_info.st_mtime_ns)
             and (platform_id == "windows-x86_64" or fd_info.st_mode == path_info.st_mode),
             "export opened file differs from checked path: " + name)


def export_bundle(root, admitted_archive=None, admitted_source_sha256=None, commit=None,
                  *, platform_id=None, lease=None):
    """Package original receipts and exact bytes, without provisioning/execution.

    Stage1 binds a clean checkout digest; the runtime verifier independently
    rechecks current runtime/codegen source. The receiving marker runner also
    recomputes the entire checkout digest before tests. No Git subprocess here.
    """
    root = Path(root).resolve(strict=True)
    platform_id = host_platform() if platform_id is None else platform_id
    lease = workflow_lease() if lease is None else lease
    commit = lease["sha"] if commit is None else commit
    paths, allowed = layout(platform_id)
    target = PLATFORMS[platform_id]["target"]
    destination = root / OUTPUT_DIRECTORY
    _require(not destination.exists() and not destination.is_symlink(), "refuse an existing export directory")
    _require(_digest(commit, 40) and lease.get("sha") == commit, "invalid admitted source commit/workflow lease")
    runtime_path = root / RUNTIME
    compiler_path = root / paths["compiler"]
    if admitted_archive is not None:
        _require(Path(admitted_archive) == runtime_path, "runtime path differs from admission")
    stage_path = root / STAGE
    regular_file(stage_path, root)
    stage_bytes = stage_path.read_bytes()
    stage = json.loads(stage_bytes)
    source = stage["source"]
    if admitted_source_sha256 is None:
        admitted_source_sha256 = source.get("source_sha256")
    _require(_digest(admitted_source_sha256), "invalid admitted source digest")
    _require(source.get("commit") == commit and source.get("clean_commit") is True
             and source.get("source_sha256") == admitted_source_sha256,
             "source differs from the admitted clean checkout")
    _require(stage.get("phase") == "stage1" and stage.get("status") == "PASS", "export requires a PASS Stage1 receipt")
    _require(stage["compiler"]["path"] == str(compiler_path), "compiler path differs from Stage1 producer")
    _require(stage["runtime"]["path"] == str(runtime_path)
             and stage["runtime"]["target"] == target and stage["runtime"]["config"] == CONFIG,
             "runtime identity differs from admitted configuration")
    expected = {paths["compiler"]: stage["compiler"]["sha256"],
                STAGE: hashlib.sha256(stage_bytes).hexdigest()}
    expected.update({RUNTIME + suffix: stage["runtime"]["hashes"][suffix]
                     for suffix in ("", ".provenance.json", ".capi_syms")})
    if paths["shared_receipt"]:
        shared_path = root / paths["shared_receipt"]
        regular_file(shared_path, root)
        shared_bytes = shared_path.read_bytes()
        shared = json.loads(shared_bytes)
        expected[paths["shared_receipt"]] = hashlib.sha256(shared_bytes).hexdigest()
        canary = shared["stages"][0]["canary"]
        suffix = ".exe" if platform_id == "windows-x86_64" else ""
        for name, key in (("canary.py", "source_sha256"), ("canary1" + suffix, "executable_sha256"), ("canary1-run.log", "output_sha256")):
            expected[SHARED + name] = canary[key]
    members = [(root / name, expected.get(name)) for name in sorted(allowed)]
    files = {}
    snapshots = {}
    for path, expected in members:
        name = path.relative_to(root).as_posix()
        info = regular_file(path, root)
        _require(info.st_size <= MAX_FILE, "export file exceeds admission")
        modes = SAFE_MODES | ({0o666, 0o777} if platform_id == "windows-x86_64" else set())
        _require(stat.S_IMODE(info.st_mode) in modes, "unsafe export file mode")
        _require(info.st_size > 0 or expected is None, "empty producer-bound file: " + name)
        with path.open("rb") as stream:
            fd_info = os.fstat(stream.fileno())
            _require_opened_identity(info, fd_info, name, platform_id)
            if expected is None:
                expected = hashlib.file_digest(stream, "sha256").hexdigest()
            _require(_file_snapshot(os.fstat(stream.fileno())) == _file_snapshot(fd_info)
                     and _file_snapshot(regular_file(path, root)) == _file_snapshot(info),
                     "export input changed during admission: " + name)
        _require(_digest(expected), "invalid export hash: " + name)
        if path == compiler_path and platform_id != "windows-x86_64":
            _require(info.st_mode & 0o111, "Stage1 compiler is not executable")
        files[name] = {"sha256": expected, "size": info.st_size,
                       "mode": stat.S_IMODE(info.st_mode), "source_path": str(path)}
        snapshots[name] = (_file_snapshot(info), _file_snapshot(fd_info))
    manifest = {
        "schema": SCHEMA, "platform": platform_id, "producer": lease, "paths": paths,
        "claim": "Stage1 identity only; not five-GC, prior, wheel or fixed-point qualification",
        "source": {"commit": commit, "source_sha256": admitted_source_sha256, "clean_commit": True},
        "target": target, "runtime_config": CONFIG, "files": files,
    }
    _require(sum(row["size"] for row in files.values()) <= MAX_TOTAL, "export size exceeds admission")
    validate_receipts(root, manifest)
    verify_native_header(compiler_path, platform_id)
    if paths["shared_receipt"]:
        verify_native_header(root / (SHARED + "canary1" + (".exe" if platform_id == "windows-x86_64" else "")), platform_id)
    _admit_runtime(root, runtime_path, target)
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="stage1-export.", dir=root / "build") as temporary:
        work = Path(temporary)
        with tarfile.open(str(work / ARCHIVE_NAME), "w|gz", compresslevel=1, format=tarfile.USTAR_FORMAT) as archive:
            for path, _expected in members:
                name = path.relative_to(root).as_posix()
                path_info = regular_file(path, root)
                _require(_file_snapshot(path_info) == snapshots[name][0],
                         "export path changed before reading: " + name)
                with path.open("rb") as stream:
                    info = os.fstat(stream.fileno())
                    _require_opened_identity(path_info, info, name, platform_id)
                    _require(_file_snapshot(info) == snapshots[name][1],
                             "export input changed before reading: " + name)
                    header = tarfile.TarInfo(name)
                    header.size = info.st_size
                    header.mode = files[name]["mode"]
                    reader = _HashingReader(stream)
                    archive.addfile(header, reader)
                    _require(not stream.read(1) and reader.digest.hexdigest() == files[name]["sha256"],
                             "export input hash or size mismatch: " + name)
                    _require(_file_snapshot(os.fstat(stream.fileno())) == snapshots[name][1]
                             and _file_snapshot(regular_file(path, root)) == snapshots[name][0],
                             "export input changed while reading: " + name)
            header = tarfile.TarInfo("manifest.json")
            header.size = len(manifest_bytes)
            header.mode = 0o644
            archive.addfile(header, io.BytesIO(manifest_bytes))
        (work / "manifest.json").write_bytes(manifest_bytes)
        _require(not destination.exists(), "export directory appeared during packing")
        work.rename(destination)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=tuple(PLATFORMS))
    parser.add_argument("--runtime-archive", type=Path)
    parser.add_argument("--source-sha256")
    args = parser.parse_args(argv)
    selected = args.platform or host_platform()
    _require(selected == host_platform(), "export platform differs from native host")
    manifest = export_bundle(ROOT, args.runtime_archive, args.source_sha256, os.environ["GITHUB_SHA"],
                             platform_id=selected)
    print(json.dumps({"directory": OUTPUT_DIRECTORY, "claim": manifest["claim"],
                      "input_bytes": sum(row["size"] for row in manifest["files"].values()),
                      "archive_bytes": (ROOT / OUTPUT_DIRECTORY / ARCHIVE_NAME).stat().st_size}))


if __name__ == "__main__":
    main()
