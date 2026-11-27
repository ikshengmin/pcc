"""Exact diagnostic-only Stage1 function map transport, not a new gate."""

import hashlib
import importlib
import json
import os
from pathlib import Path
import struct

import pytest

from pcc.backend import elf_x86_64 as elf
from pcc.backend import owned_elf_link
from scripts import bootstrap_platform, ci_stage1_bundle as bundle
from tests.python.test_elf_x86_64 import _exit_42_object

TARGET = "x86_64-unknown-linux-gnu"
FOR = "PCC_OWNED_ELF_FUNCTION_MAP_FOR"
FLAG = "PCC_BOOTSTRAP_STAGE1_FUNCTION_MAP"
LEASE = {"repository": "owner/pcc", "workflow": ".github/workflows/pcc1-package-parity.yml",
         "run_id": 123, "run_attempt": 4, "sha": "a" * 40}
SOURCE = {"commit": LEASE["sha"], "source_sha256": "b" * 64, "clean_commit": True}


def _owner():
    return importlib.import_module("scripts.ci_stage1_function_map")


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    return path


def _fixtures(tmp_path):
    # Real final owned ELF and actual post-relocation function map, with an
    # equal-address function alias to prove the transport does not guess.
    start = _exit_42_object()
    original = start.symbols
    alias = elf.ElfSymbol("answer_alias", 1, 14, 6, elf.STB_GLOBAL, elf.STT_FUNC)
    obj = elf.ElfObject(start.sections, original + (alias,), start.machine)
    source = tmp_path / "input.pco"
    source.write_bytes(elf.emit_relocatable(obj))
    image = tmp_path / "pcc1"
    mapping = tmp_path / "pcc1.functions.jsonl"
    owned_elf_link.link_inputs(target=TARGET, output=str(image), objects=[str(source)],
                              function_map_path=str(mapping))
    return image, mapping


def _rows(mapping):
    return [json.loads(line) for line in mapping.read_text().splitlines()]


def _write_rows(mapping, rows):
    mapping.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_actual_map_preserves_alias_and_physical_ranges(tmp_path):
    image, mapping = _fixtures(tmp_path)
    result = _owner().validate_function_map(image, mapping)
    header, *rows = _rows(mapping)
    assert result["map_sha256"] == _digest(mapping)
    assert result["map_bytes"] == mapping.stat().st_size
    assert result["image_sha256"] == _digest(image)
    assert result["image_bytes"] == image.stat().st_size
    assert result["function_count"] == header["function_count"] == len(rows)
    assert result["overlapping_rows"] >= 1
    names = {r["name"]: r for r in rows}
    assert names["answer"]["start"] == names["answer_alias"]["start"]
    assert names["answer"]["end"] == names["answer_alias"]["end"]
    assert names["answer"]["symbol_index"] != names["answer_alias"]["symbol_index"]


@pytest.mark.parametrize("damage", ["image", "header-sha", "header-size", "header-count", "header-target",
    "row-bool", "row-range", "row-file-offset", "row-binding", "row-origin", "row-duplicate-key",
    "row-duplicate-id", "row-order", "row-truncated", "row-extra-key", "image-machine", "map-symlink"])
def test_map_rejects_malformed_or_mismatched_actual_data(tmp_path, damage):
    image, mapping = _fixtures(tmp_path)
    rows = _rows(mapping)
    if damage == "image":
        with image.open("ab") as stream: stream.write(b"x")
    elif damage == "image-machine":
        data = bytearray(image.read_bytes()); struct.pack_into("<H", data, 18, elf.EM_AARCH64)
        image.write_bytes(data); rows[0]["image_sha256"] = _digest(image); _write_rows(mapping, rows)
    elif damage == "map-symlink":
        target = mapping.with_name("realmap"); mapping.rename(target); mapping.symlink_to(target)
    elif damage == "row-duplicate-key":
        lines = mapping.read_text().splitlines(); lines[1] = lines[1][:-1] + ',"name":"duplicate"}'
        mapping.write_text("\n".join(lines) + "\n")
    elif damage == "row-truncated":
        mapping.write_bytes(mapping.read_bytes()[:-1])
    else:
        if damage == "header-sha": rows[0]["image_sha256"] = "0" * 64
        elif damage == "header-size": rows[0]["image_bytes"] += 1
        elif damage == "header-count": rows[0]["function_count"] += 1
        elif damage == "header-target": rows[0]["target"] = "aarch64-unknown-linux-gnu"
        elif damage == "row-bool": rows[1]["start"] = True
        elif damage == "row-range": rows[1]["end"] = rows[1]["start"]
        elif damage == "row-file-offset": rows[1]["file_offset"] += 1
        elif damage == "row-binding": rows[1]["binding"] = 3
        elif damage == "row-origin": rows[1]["origin_sha256"] = ""
        elif damage == "row-duplicate-id": rows.insert(2, dict(rows[1])); rows[0]["function_count"] += 1
        elif damage == "row-order": rows[1], rows[-1] = rows[-1], rows[1]
        else: rows[1]["unexpected"] = 1
        _write_rows(mapping, rows)
    with pytest.raises((ValueError, OSError)):
        _owner().validate_function_map(image, mapping)


def test_map_limits_are_fail_closed(tmp_path, monkeypatch):
    image, mapping = _fixtures(tmp_path)
    owner = _owner()
    monkeypatch.setattr(owner, "MAX_MAP_BYTES", mapping.stat().st_size - 1)
    with pytest.raises(ValueError, match="size"):
        owner.validate_function_map(image, mapping)


def _receipt_fixture(tmp_path):
    owner = _owner()
    image, mapping = _fixtures(tmp_path)
    checked = owner.validate_function_map(image, mapping)
    paths, allowed = bundle.layout("linux-x86_64")
    compiler_path = "/producer/checkout/" + paths["compiler"]
    runtime_path = "/producer/checkout/" + bundle.RUNTIME
    runtime_hashes = {"": "c" * 64, ".provenance.json": "d" * 64, ".capi_syms": "e" * 64}
    stage = {"phase": "stage1", "status": "PASS", "returncode": 0, "source": SOURCE,
             "compiler": {"path": compiler_path, "sha256": checked["image_sha256"]},
             "runtime": {"path": runtime_path, "target": TARGET, "config": bundle.CONFIG, "hashes": runtime_hashes}}
    canary = {"source_sha256": "1" * 64, "executable_sha256": "2" * 64,
              "output_sha256": "3" * 64, "compile": {"seconds": 0.25}, "run": {"seconds": 0.1}}
    shared = {"schema": "pcc.platform-bootstrap.v1", "target": TARGET, "source": SOURCE,
              "compiler_path": compiler_path,
              "runtime": {"path": runtime_path, "sha256": runtime_hashes[""],
                          "provenance_sha256": runtime_hashes[".provenance.json"],
                          "capi_inventory_sha256": runtime_hashes[".capi_syms"]},
              "stages": [{"stage": 1, "seconds": 1.0, "sha256": checked["image_sha256"], "canary": canary,
                          "function_map": {"status": "PASS", "path": compiler_path + ".functions.jsonl", **checked}}]}
    stage_path = _json(tmp_path / "stage.json", stage)
    shared_path = _json(tmp_path / "shared.json", shared)
    files = {name: {"sha256": "4" * 64, "size": 1, "source_path": "/producer/checkout/" + name,
                    "mode": 0o644} for name in allowed}
    files[paths["compiler"]].update(sha256=checked["image_sha256"], size=checked["image_bytes"], mode=0o755)
    files[bundle.STAGE].update(sha256=_digest(stage_path), size=stage_path.stat().st_size)
    files[paths["shared_receipt"]].update(sha256=_digest(shared_path), size=shared_path.stat().st_size)
    for suffix, digest in runtime_hashes.items(): files[bundle.RUNTIME + suffix]["sha256"] = digest
    for name, key in (("canary.py", "source_sha256"), ("canary1", "executable_sha256"), ("canary1-run.log", "output_sha256")):
        files[bundle.SHARED + name]["sha256"] = canary[key]
    manifest = {"schema": bundle.SCHEMA, "platform": "linux-x86_64", "producer": LEASE,
                "paths": paths, "source": SOURCE, "target": TARGET, "runtime_config": bundle.CONFIG, "files": files}
    manifest_path = _json(tmp_path / "bundle.json", manifest)
    return image, mapping, stage_path, shared_path, manifest_path


def test_transport_requires_exact_success_and_receipts(tmp_path):
    owner = _owner()
    image, mapping, stage, shared, manifest = _receipt_fixture(tmp_path)
    binding = owner.make_binding(image, mapping, stage, shared, manifest, LEASE)
    receipt = _json(tmp_path / "binding.json", binding)
    # Detached/relocated paths are admitted only by exact producer identities.
    assert owner.validate_transport(image, mapping, receipt, stage, shared, manifest, LEASE) == binding
    assert binding["compiler_sha256"] == _digest(image)
    assert binding["map_sha256"] == _digest(mapping)
    assert binding["stage_receipt_sha256"] == _digest(stage)
    assert binding["shared_receipt_sha256"] == _digest(shared)
    assert binding["bundle_manifest_sha256"] == _digest(manifest)


@pytest.mark.parametrize("damage", ["failed-stage", "failed-return", "missing-canary", "reused", "unqualified-map",
    "wrong-source", "wrong-map-hash", "wrong-compiler", "wrong-lease"])
def test_transport_rejects_invalid_success_or_producer_binding(tmp_path, damage):
    owner = _owner()
    image, mapping, stage_path, shared_path, manifest_path = _receipt_fixture(tmp_path)
    stage, shared, manifest = [json.loads(p.read_text()) for p in (stage_path, shared_path, manifest_path)]
    lease = dict(LEASE)
    if damage == "failed-stage": stage["status"] = "FAIL"
    elif damage == "failed-return": stage["returncode"] = 1
    elif damage == "missing-canary": del shared["stages"][0]["canary"]
    elif damage == "reused": shared["stages"][0]["reused"] = True
    elif damage == "unqualified-map": shared["stages"][0]["function_map"]["status"] = "UNQUALIFIED"
    elif damage == "wrong-source": stage["source"]["source_sha256"] = "0" * 64
    elif damage == "wrong-map-hash": shared["stages"][0]["function_map"]["map_sha256"] = "0" * 64
    elif damage == "wrong-compiler": stage["compiler"]["sha256"] = "0" * 64
    else: lease["run_attempt"] += 1
    _json(stage_path, stage); _json(shared_path, shared)
    for p, name in ((stage_path, bundle.STAGE), (shared_path, bundle.SHARED + "receipt.json")):
        manifest["files"][name].update(sha256=_digest(p), size=p.stat().st_size)
    _json(manifest_path, manifest)
    with pytest.raises(ValueError):
        owner.make_binding(image, mapping, stage_path, shared_path, manifest_path, lease)


def test_stage_environment_does_not_propagate_map_path(tmp_path):
    environment = {FOR: "/stale/path", FLAG: "1", "other": "unchanged"}
    output = tmp_path / "pcc1"
    off = bootstrap_platform._stage_compiler_environment(environment, tmp_path, 1, "0")
    assert FOR not in off and FLAG not in off and off["other"] == "unchanged"
    on = bootstrap_platform._stage_compiler_environment(environment, tmp_path, 1, "0", stage1_function_map_output=output)
    assert on[FOR] == str(output) and FLAG not in on
    later = bootstrap_platform._stage_compiler_environment(environment, tmp_path, 2, "0", stage1_function_map_output=output)
    assert FOR not in later and FLAG not in later
    assert environment[FOR] == "/stale/path" and environment[FLAG] == "1"


def test_export_existing_is_compact_and_preserves_actual_map_bytes(tmp_path):
    owner = _owner()
    image, mapping, stage_path, shared_path, manifest_path = _receipt_fixture(tmp_path)
    stage, shared, manifest = [json.loads(p.read_text()) for p in (stage_path, shared_path, manifest_path)]
    paths, _ = bundle.layout("linux-x86_64")
    real_image = tmp_path / paths["compiler"]
    real_image.parent.mkdir(parents=True)
    image.rename(real_image)
    real_mapping = Path(str(real_image) + ".functions.jsonl")
    mapping.rename(real_mapping)
    stage["compiler"]["path"] = shared["compiler_path"] = str(real_image)
    shared["stages"][0]["function_map"]["path"] = str(real_mapping)
    stage["runtime"]["path"] = shared["runtime"]["path"] = str(tmp_path / bundle.RUNTIME)
    actual_stage = _json(tmp_path / bundle.STAGE, stage)
    actual_shared = _json(tmp_path / paths["shared_receipt"], shared)
    for name, row in manifest["files"].items(): row["source_path"] = str(tmp_path / name)
    for path, name in ((actual_stage, bundle.STAGE), (actual_shared, paths["shared_receipt"])):
        manifest["files"][name].update(sha256=_digest(path), size=path.stat().st_size)
    actual_manifest = _json(tmp_path / owner.BUNDLE_MANIFEST, manifest)
    binding = owner.export_existing(tmp_path, LEASE)
    directory = tmp_path / owner.OUTPUT_DIR
    assert sorted(p.name for p in directory.iterdir()) == ["function-map.jsonl", "receipt.json"]
    assert (directory / "function-map.jsonl").read_bytes() == real_mapping.read_bytes()
    assert owner.validate_transport(real_image, directory / "function-map.jsonl", directory / "receipt.json",
                                    actual_stage, actual_shared, actual_manifest, LEASE) == binding
    with pytest.raises(ValueError, match="already exists"):
        owner.export_existing(tmp_path, LEASE)


def test_transport_binding_rejects_changed_detached_receipt(tmp_path):
    owner = _owner()
    image, mapping, stage, shared, manifest = _receipt_fixture(tmp_path)
    binding = owner.make_binding(image, mapping, stage, shared, manifest, LEASE)
    binding["map_sha256"] = "0" * 64
    receipt = _json(tmp_path / "binding.json", binding)
    with pytest.raises(ValueError, match="binding differs"):
        owner.validate_transport(image, mapping, receipt, stage, shared, manifest, LEASE)


def test_workflow_marker_and_independent_exact_artifact_boundary():
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/pcc1-package-parity.yml").read_text()
    marker = "[stage1-x64-function-map]"
    assert text.count(marker) == 2
    assert 'PCC_OWNED_ELF_FUNCTION_MAP_FOR: ""' in text
    assert "github.event_name == 'push' && matrix.platform == 'linux-x86_64'" in text
    stage = text.split("      - name: Validate optional exact Stage1 x64 function map\n", 1)[1]
    validate, tail = stage.split("      - name: Preserve optional exact Stage1 x64 function map\n", 1)
    upload = tail.split("      - name:", 1)[0]
    assert "steps.stage1_bundle.outcome == 'success'" in validate and marker in validate
    assert "continue-on-error: true" in validate and "continue-on-error: true" in upload
    assert "steps.stage1_function_map.outcome == 'success'" in upload
    assert [line.strip() for line in upload.splitlines() if line.startswith("            build/")] == [
        "build/stage1-function-map-export/function-map.jsonl", "build/stage1-function-map-export/receipt.json",
    ]
    generic = text.split("      - name: Preserve platform qualification evidence\n", 1)[1].split("      - name:", 1)[0]
    assert "build/platform-qualification/**/*.jsonl" in generic
    assert "!build/platform-qualification/shared/pcc1.functions.jsonl" in generic
    assert "!build/platform-qualification/shared/pcc1.tmp.functions.jsonl" in generic
