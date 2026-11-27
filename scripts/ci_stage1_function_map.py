"""Validate/export the existing Stage1 x64 link-owned function map; never build."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import ci_stage1_bundle as bundle

TARGET = "x86_64-unknown-linux-gnu"
MAP_SCHEMA = "pcc.owned-elf-function-map.v1"
BINDING_SCHEMA = "pcc.ci-stage1-function-map.v1"
SUFFIX = ".functions.jsonl"
OUTPUT_DIR = "build/stage1-function-map-export"
BUNDLE_MANIFEST = "build/stage1-native-export/manifest.json"
MAX_MAP_BYTES = 128 * 1024 * 1024
MAX_ROW_BYTES = 64 * 1024
MAX_FUNCTIONS = 500_000
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
_CHUNK = 1024 * 1024
_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
_PHDR = struct.Struct("<IIQQQQQQ")
_HEADER_KEYS = {"schema", "target", "image_sha256", "image_bytes", "function_count"}
_ROW_KEYS = {"start", "end", "file_offset", "object_index", "symbol_index", "section_index",
             "section_name", "binding", "name", "origin_label", "origin_sha256", "origin_offset"}
_SYNTHETIC = {
    "linker-synthesized:thread-pointer": {"pcc_linux_set_thread_pointer"},
    "linker-synthesized:thread-intrinsics": {"pcc_linux_rt_sigreturn", "pcc_linux_thread_spawn", "pcc_linux_thread_exit_free"},
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _snapshot(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _checked_open(path: Path, limit: int):
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode), "diagnostic input must be a regular non-symlink file")
    _require(0 < info.st_size <= limit, "diagnostic input size exceeds admission")
    stream = path.open("rb")
    try:
        _require(_snapshot(info) == _snapshot(os.fstat(stream.fileno())), "diagnostic input changed before read")
    except BaseException:
        stream.close()
        raise
    return stream, info


def _unchanged(path, stream, info):
    _require(_snapshot(info) == _snapshot(os.fstat(stream.fileno())) == _snapshot(path.lstat()),
             "diagnostic input changed during read")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON object key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("non-finite JSON constant: " + value)


def _json(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)


def _uint(value):
    return type(value) is int and 0 <= value < 1 << 64


def _text(value):
    return type(value) is str and bool(value) and "\0" not in value


def _digest(value):
    return bundle.valid_digest(value)


def _read_receipt(path):
    path = Path(path)
    stream, info = _checked_open(path, MAX_RECEIPT_BYTES)
    with stream:
        data = stream.read(info.st_size + 1)
        _require(len(data) == info.st_size, "receipt size changed")
        _unchanged(path, stream, info)
    value = _json(data)
    _require(type(value) is dict, "receipt must be a JSON object")
    return value, hashlib.sha256(data).hexdigest(), len(data)


def _image_identity(path):
    path = Path(path)
    stream, info = _checked_open(path, bundle.MAX_FILE)
    with stream:
        raw = stream.read(_HEADER.size)
        _require(len(raw) == _HEADER.size, "truncated ELF header")
        ident, kind, machine, version, entry, phoff, shoff, _flags, ehsize, phentsize, phnum, _shentsize, shnum, shstrndx = _HEADER.unpack(raw)
        _require(ident[:7] == b"\x7fELF\x02\x01\x01" and kind == 2 and machine == 62 and version == 1,
                 "function map image must be owned x64 ELF64 ET_EXEC")
        _require(shoff == shnum == shstrndx == 0 and ehsize == _HEADER.size and phoff == _HEADER.size
                 and phentsize == _PHDR.size and 1 <= phnum <= 4,
                 "function map image has unexpected final ELF topology")
        rx = []
        for _ in range(phnum):
            raw = stream.read(_PHDR.size)
            _require(len(raw) == _PHDR.size, "truncated ELF program headers")
            ptype, flags, offset, address, physical, filesz, memsz, align = _PHDR.unpack(raw)
            _require(ptype in (1, 7) and flags & ~7 == 0 and 0 < align and align & (align - 1) == 0
                     and filesz <= memsz and offset + filesz <= info.st_size
                     and address + memsz <= 1 << 64 and address == physical,
                     "invalid owned ELF program header")
            if ptype == 1 and flags & 1:
                _require(flags == 5 and filesz > 0 and offset % align == address % align,
                         "function map executable LOAD is not read-only file-backed code")
                rx.append((offset, address, filesz))
        _require(len(rx) == 1 and any(addr <= entry < addr + size for _, addr, size in rx),
                 "function map image has no unique executable entry segment")
        stream.seek(0)
        digest = hashlib.sha256()
        length = 0
        while chunk := stream.read(_CHUNK):
            digest.update(chunk); length += len(chunk)
        _require(length == info.st_size, "function map image size changed")
        _unchanged(path, stream, info)
    return digest.hexdigest(), info.st_size, rx


def validate_function_map(image_path, map_path):
    """Stream exact image/map identity and validate every physical symbol row."""
    image_sha, image_bytes, rx = _image_identity(image_path)
    path = Path(map_path)
    stream, info = _checked_open(path, MAX_MAP_BYTES)
    with stream:
        digest = hashlib.sha256()
        total = 0
        def next_row():
            nonlocal total
            line = stream.readline(MAX_ROW_BYTES + 1)
            if not line:
                return None
            _require(len(line) <= MAX_ROW_BYTES and line.endswith(b"\n"), "function map row size or terminator differs")
            total += len(line)
            _require(total <= MAX_MAP_BYTES, "function map size exceeds admission")
            digest.update(line)
            value = _json(line)
            _require(type(value) is dict, "function map row must be a JSON object")
            return value
        header = next_row()
        _require(header is not None and set(header) == _HEADER_KEYS and header["schema"] == MAP_SCHEMA
                 and header["target"] == TARGET and header["image_sha256"] == image_sha
                 and type(header["image_bytes"]) is int and header["image_bytes"] == image_bytes
                 and type(header["function_count"]) is int and 0 < header["function_count"] <= MAX_FUNCTIONS,
                 "function map header differs from actual ELF or limits")
        seen = set()
        seen_synthetic = set()
        prior = None
        max_end = 0
        overlaps = 0
        count = 0
        while (row := next_row()) is not None:
            count += 1
            _require(count <= header["function_count"] and set(row) == _ROW_KEYS, "function map count/schema differs")
            _require(all(_uint(row[key]) for key in ("start", "end", "file_offset", "object_index", "symbol_index",
                                                    "section_index", "binding", "origin_offset")), "invalid function map scalar")
            _require(row["start"] < row["end"] and row["symbol_index"] > 0 and row["section_index"] > 0
                     and row["binding"] in (0, 1, 2) and all(_text(row[key]) for key in ("name", "section_name", "origin_label")),
                     "invalid function map symbol")
            _require(any(addr <= row["start"] < row["end"] <= addr + size
                         and row["file_offset"] == off + row["start"] - addr for off, addr, size in rx),
                     "function map range does not map to actual executable LOAD bytes")
            owner = row["origin_label"]
            if row["origin_sha256"] == "":
                _require(owner in _SYNTHETIC and row["name"] in _SYNTHETIC[owner] and row["origin_offset"] == 0
                         and row["name"] not in seen_synthetic, "invalid synthesized function map provenance")
                seen_synthetic.add(row["name"])
            else:
                _require(owner not in _SYNTHETIC and _digest(row["origin_sha256"]), "invalid staged function map provenance")
            identity = (row["object_index"], row["symbol_index"])
            order = (row["start"], *identity)
            _require(identity not in seen and (prior is None or prior < order), "duplicate or unordered function map identity")
            seen.add(identity); prior = order
            # Preserve all overlaps. Never pick the first function for a PC.
            if row["start"] < max_end:
                overlaps += 1
            max_end = max(max_end, row["end"])
        _require(count == header["function_count"] and total == info.st_size, "function map count or size differs")
        _require(seen_synthetic == set().union(*_SYNTHETIC.values()), "function map lacks actual synthesized helper provenance")
        _unchanged(path, stream, info)
    # Recheck the image after the potentially long map scan, not just its name.
    observed_sha, observed_size, _ = _image_identity(image_path)
    _require((observed_sha, observed_size) == (image_sha, image_bytes), "function map image changed during scan")
    return {"schema": MAP_SCHEMA, "target": TARGET, "map_sha256": digest.hexdigest(), "map_bytes": total,
            "image_sha256": image_sha, "image_bytes": image_bytes, "function_count": count, "overlapping_rows": overlaps}


def stage_map_receipt(image_path, expected_image_sha256):
    mapping = Path(str(image_path) + SUFFIX)
    result = validate_function_map(image_path, mapping)
    _require(result["image_sha256"] == expected_image_sha256, "map differs from completed Stage1 identity")
    return {"status": "PASS", "path": str(mapping), **result}


def make_binding(image_path, map_path, stage_receipt_path, shared_receipt_path, bundle_manifest_path, lease):
    """Cross-bind original success receipts, workflow lease and actual bytes."""
    stage, stage_sha, stage_bytes = _read_receipt(stage_receipt_path)
    shared, shared_sha, shared_bytes = _read_receipt(shared_receipt_path)
    manifest, manifest_sha, _ = _read_receipt(bundle_manifest_path)
    paths, allowed = bundle.layout("linux-x86_64")
    try:
        _require(manifest["schema"] == bundle.SCHEMA and manifest["platform"] == "linux-x86_64"
                 and manifest["producer"] == lease and manifest["paths"] == paths and set(manifest["files"]) == allowed
                 and manifest["target"] == TARGET and manifest["runtime_config"] == bundle.CONFIG,
                 "Stage1 bundle/workflow lease differs")
        _require(type(lease) is dict and set(lease) == {"repository", "workflow", "run_id", "run_attempt", "sha"}
                 and _text(lease["repository"]) and lease["repository"].count("/") == 1
                 and all(lease["repository"].split("/")) and lease["workflow"] == ".github/workflows/pcc1-package-parity.yml"
                 and type(lease["run_id"]) is int and lease["run_id"] > 0 and type(lease["run_attempt"]) is int and lease["run_attempt"] > 0
                 and bundle.valid_digest(lease["sha"], 40), "invalid workflow lease")
        files = manifest["files"]
        source = manifest["source"]
        _require(source["commit"] == lease["sha"] and source["clean_commit"] is True and _digest(source["source_sha256"])
                 and all(stage["source"][k] == shared["source"][k] == source[k] for k in ("commit", "source_sha256", "clean_commit")),
                 "Stage1 source identity differs")
        _require(stage["phase"] == "stage1" and stage["status"] == "PASS" and type(stage["returncode"]) is int
                 and stage["returncode"] == 0 and shared["schema"] == "pcc.platform-bootstrap.v1" and shared["target"] == TARGET,
                 "function map requires an actual successful Stage1 receipt")
        _require(files[bundle.STAGE]["sha256"] == stage_sha and files[bundle.STAGE]["size"] == stage_bytes
                 and files[paths["shared_receipt"]]["sha256"] == shared_sha and files[paths["shared_receipt"]]["size"] == shared_bytes,
                 "original Stage1 receipt bytes differ from bundle manifest")
        stages = shared["stages"]
        _require(type(stages) is list and len(stages) == 1 and type(stages[0]["stage"]) is int
                 and stages[0]["stage"] == 1 and stages[0].get("reused", False) is False,
                 "function map requires fresh actual Stage1 producer")
        record = stages[0]
        _require(type(record["seconds"]) in (int, float) and record["seconds"] > 0, "Stage1 completion lacks real wall time")
        canary = record["canary"]
        _require(type(canary["compile"]) is dict and type(canary["run"]) is dict
                 and canary["compile"]["seconds"] > 0 and canary["run"]["seconds"] > 0,
                 "Stage1 native canary success absent")
        for name, key in (("canary.py", "source_sha256"), ("canary1", "executable_sha256"), ("canary1-run.log", "output_sha256")):
            _require(_digest(canary[key]) and files[bundle.SHARED + name]["sha256"] == canary[key], "Stage1 canary identity differs")
        compiler = stage["compiler"]
        compiler_file = files[paths["compiler"]]
        _require(compiler["path"] == shared["compiler_path"] == compiler_file["source_path"]
                 and compiler["sha256"] == record["sha256"] == compiler_file["sha256"] and _digest(compiler["sha256"]),
                 "Stage1 actual compiler identity differs")
        runtime = stage["runtime"]
        _require(runtime["target"] == TARGET and runtime["config"] == bundle.CONFIG
                 and runtime["path"] == shared["runtime"]["path"] == files[bundle.RUNTIME]["source_path"], "Stage1 runtime identity differs")
        for suffix, key in (("", "sha256"), (".provenance.json", "provenance_sha256"), (".capi_syms", "capi_inventory_sha256")):
            value = runtime["hashes"][suffix]
            _require(_digest(value) and value == shared["runtime"][key] == files[bundle.RUNTIME + suffix]["sha256"], "Stage1 runtime hash differs")
        checked = validate_function_map(image_path, map_path)
        _require(checked["image_sha256"] == compiler["sha256"] and checked["image_bytes"] == compiler_file["size"],
                 "actual ELF differs from successful Stage1 compiler receipt")
        _require(record["function_map"] == {"status": "PASS", "path": compiler["path"] + SUFFIX, **checked},
                 "actual function map differs from successful Stage1 map receipt")
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("invalid Stage1 producer receipt structure") from exc
    return {"schema": BINDING_SCHEMA, "producer": lease, "source": source, "target": TARGET,
            "compiler_producer_path": compiler["path"], "map_producer_path": compiler["path"] + SUFFIX,
            "compiler_sha256": checked["image_sha256"], "compiler_bytes": checked["image_bytes"],
            "map_sha256": checked["map_sha256"], "map_bytes": checked["map_bytes"],
            "function_count": checked["function_count"], "overlapping_rows": checked["overlapping_rows"],
            "stage_receipt_sha256": stage_sha, "shared_receipt_sha256": shared_sha, "bundle_manifest_sha256": manifest_sha}


def validate_transport(image_path, map_path, binding_path, stage_receipt_path, shared_receipt_path, bundle_manifest_path, lease):
    actual = make_binding(image_path, map_path, stage_receipt_path, shared_receipt_path, bundle_manifest_path, lease)
    expected, _, _ = _read_receipt(binding_path)
    _require(expected == actual, "transport binding differs from actual producer artifacts")
    return actual


def export_existing(root, lease):
    root = Path(root).resolve(strict=True)
    paths, _ = bundle.layout("linux-x86_64")
    image = root / paths["compiler"]
    mapping = Path(str(image) + SUFFIX)
    stage = root / bundle.STAGE
    shared = root / paths["shared_receipt"]
    manifest = root / BUNDLE_MANIFEST
    for path in (image, mapping, stage, shared, manifest):
        bundle.regular_file(path, root)
    binding = make_binding(image, mapping, stage, shared, manifest, lease)
    _require(binding["compiler_producer_path"] == str(image) and binding["map_producer_path"] == str(mapping),
             "function map export requires actual producer paths")
    # Existing Stage1 bundle admission remains authoritative for its products.
    manifest_data, manifest_sha, _ = _read_receipt(manifest)
    _require(manifest_sha == binding["bundle_manifest_sha256"], "Stage1 bundle manifest changed during export")
    bundle.validate_receipts(root, manifest_data)
    destination = root / OUTPUT_DIR
    _require(not os.path.lexists(destination), "function map export directory already exists")
    with tempfile.TemporaryDirectory(prefix="stage1-map.", dir=root / "build") as temporary:
        work = Path(temporary) / "out"
        work.mkdir()
        copied = work / "function-map.jsonl"
        stream, info = _checked_open(mapping, MAX_MAP_BYTES)
        with stream, copied.open("xb") as output:
            digest = hashlib.sha256(); total = 0
            while chunk := stream.read(_CHUNK):
                output.write(chunk); digest.update(chunk); total += len(chunk)
                _require(total <= MAX_MAP_BYTES, "function map copy exceeds size limit")
            _unchanged(mapping, stream, info)
        _require(digest.hexdigest() == binding["map_sha256"] and total == binding["map_bytes"], "function map changed during export")
        for receipt_path, key in ((stage, "stage_receipt_sha256"), (shared, "shared_receipt_sha256"),
                                  (manifest, "bundle_manifest_sha256")):
            _, observed, _ = _read_receipt(receipt_path)
            _require(observed == binding[key], "Stage1 receipt changed during map export")
        (work / "receipt.json").write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _require(not os.path.lexists(destination), "function map export destination appeared")
        os.replace(work, destination)
    return binding


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    _require(bundle.host_platform() == "linux-x86_64", "function map export only supports Linux x64")
    result = export_existing(ROOT, bundle.workflow_lease())
    print("Stage1 function map diagnostic accepted:", result["function_count"], "rows", result["map_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
