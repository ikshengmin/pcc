"""Unchanged ZXing 3.5.4 Reed-Solomon package: strict owned gate plus JDK reference.

projects/zxing-3.5.4/PCC-README.md records the pin and selection. The owned
gate requires the complete original production package to compile; a compiler
rejection is a failing project test. JUnit is not supplied, so owned and
reference upstream-test execution remain explicitly UNRUN.
"""
import hashlib
import os
from pathlib import Path
import shutil

import pytest

from scripts.ci_source_frontier_support import load_source_lock, materialize_snapshot, verify_snapshot

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects/zxing-3.5.4"
LOCK_SHA256 = "807ce6918037fd2ea1c015b4fa42ce267120c99ec76657be627e6b4672708cdb"
REVISION = "f651b0a0375676e47144f73397dddff8868b0e4c"
PACKAGE = "com/google/zxing/common/reedsolomon/"
PRODUCTION = tuple("core/src/main/java/" + PACKAGE + name + ".java" for name in (
    "GenericGF", "GenericGFPoly", "ReedSolomonDecoder", "ReedSolomonEncoder", "ReedSolomonException"))
TESTS = tuple("core/src/test/java/" + PACKAGE + name + ".java" for name in (
    "GenericGFPolyTestCase", "ReedSolomonTestCase"))
# The package's only static method; selection exports it, every method is checked.
ENTRY = {"entry_class": "com.google.zxing.common.reedsolomon.GenericGF",
         "entry": "addOrSubtract", "descriptor": "(II)I"}


def load_inputs(directory=PROJECT):
    lock, files = load_source_lock(directory, LOCK_SHA256)
    if (lock["project"] != "zxing-core-reedsolomon" or lock["version"] != "3.5.4"
            or lock["revision"] != REVISION or lock["release_tag"] != "zxing-3.5.4"
            or tuple(lock["production_units"]) != PRODUCTION or tuple(lock["upstream_test_units"]) != TESTS
            or lock["complete_module_source_closure"] is not True
            or lock["complete_upstream_test_source_closure"] is not True
            or lock["dependencies"]["state"] != "MISSING_SOURCE_AND_TOOL_CLOSURE"):
        raise ValueError("complete original Reed-Solomon selection differs")
    if {name for name in files if name.endswith(".java")} != set(PRODUCTION + TESTS):
        raise ValueError("original Reed-Solomon Java closure differs")
    return lock, files


def original_sources(files, names):
    return {name: files[name].decode("utf-8") for name in names}


def test_lock_pins_the_complete_package_and_its_upstream_tests():
    lock, files = load_inputs()
    assert len(files) == 13 and lock["total_bytes"] == sum(map(len, files.values()))
    assert all(entry["url"].startswith("https://raw.githubusercontent.com/zxing/zxing/" + REVISION + "/")
               for entry in lock["files"])
    assert {entry["path"]: entry["role"] for entry in lock["files"]}["LICENSE"] == "license-notice"
    assert lock["upstream_test_classes"] == [
        "com.google.zxing.common.reedsolomon.GenericGFPolyTestCase",
        "com.google.zxing.common.reedsolomon.ReedSolomonTestCase"]
    # Package sources import only java.util; tests need the missing JUnit 4.
    for name in PRODUCTION:
        imports = [line for line in files[name].decode().splitlines() if line.startswith("import ")]
        assert all(line.startswith("import java.util.") for line in imports), name
    assert all(b"import org.junit." in files[name] for name in TESTS)
    assert lock["dependencies"]["junit"]["coordinate"] == "junit:junit:4.13.2"
    assert lock["dependencies"]["junit"]["binary_reference_closure_present"] is False


@pytest.mark.parametrize("name", PRODUCTION + TESTS + ("LICENSE",))
def test_changed_original_bytes_are_rejected(tmp_path, name):
    copy = tmp_path / "project"
    shutil.copytree(PROJECT, copy)
    target = copy / name
    data = bytearray(target.read_bytes())
    data[len(data) // 2] ^= 1
    target.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="checksum"):
        load_inputs(copy)


def test_materialized_snapshot_keeps_every_original_byte(tmp_path):
    _, files = load_inputs()
    materialize_snapshot(tmp_path / "source", files)
    verify_snapshot(tmp_path / "source", files)
    assert {path.relative_to(tmp_path / "source").as_posix() for path in (tmp_path / "source").rglob("*")
            if path.is_file()} == set(files)


@pytest.mark.integration
def test_original_reedsolomon_package_compiles_with_the_owned_java_frontend(monkeypatch):
    """Strict project gate: every original production unit, no rewrite."""
    import subprocess
    from pcc.frontends.java import compile_sources
    _, files = load_inputs()
    before = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("owned compile launched a tool"))
    compilation = compile_sources(original_sources(files, PRODUCTION), **ENTRY)
    assert compilation.emit_object().startswith(b"\x7fELF")
    assert {name: hashlib.sha256(data).hexdigest() for name, data in load_inputs()[1].items()} == before


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
                      "set PCC_JAVA_RAW_REFERENCE to an explicit Linux JDK 9+ java executable")
def test_original_reedsolomon_package_compiles_with_the_selected_jdk(tmp_path):
    """Reference only: javac --release 8 on the same originals; tests UNRUN."""
    from tests.java.test_source_raw_reference import JVM_LIMITS, record_process, tool_identity
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    home = java.parent.parent
    before = tool_identity(java)
    _, files = load_inputs()
    source = tmp_path / "source"
    materialize_snapshot(source, files)
    classes = tmp_path / "classes"
    classes.mkdir()
    result = record_process((str(home / "bin/javac"), *("-J" + option for option in JVM_LIMITS),
                             "--release", "8", "-encoding", "UTF-8", "-proc:none", "-d", str(classes),
                             *(str(source / name) for name in PRODUCTION)), tmp_path, "javac", 60)
    assert result[0] == 0, result
    assert {path.name for path in classes.rglob("*.class")} >= {
        "GenericGF.class", "GenericGFPoly.class", "ReedSolomonDecoder.class",
        "ReedSolomonEncoder.class", "ReedSolomonException.class"}
    verify_snapshot(source, files)
    assert tool_identity(java) == before
