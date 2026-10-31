"""Memory-bearing C++/Rust component gate, separate from all scalar fixtures.

No GC handles, escaping references, aggregate-by-value, EH, shared scheduler,
whole-project or native-pcc1 qualification. Darwin results are cross-only.
"""

import hashlib
import json
import platform
import struct
import subprocess
import sys

import pytest

from tests.multilanguage.record_pointer_execution import (
    CPP_ENTRY,
    DARWIN_TARGET,
    LINUX_OBSERVER,
    LINUX_TARGET,
    OWNER,
    RUST_ENTRY,
    SNAPSHOT,
    compile_record_executable,
    compile_record_objects,
    original_record_inputs,
)
from tests.multilanguage.test_scalar_c_abi import forbid_external_owners


pytestmark = pytest.mark.integration
TAG = -123456789
WIDE = 0xFEDCBA9876543210
SIGNED = -72623859790382856
EXPECTED_RESULT = (WIDE + SIGNED * 3 + TAG * 5) & ((1 << 64) - 1)
EXPECTED_SNAPSHOT = struct.pack("<iQq", TAG, WIDE, SIGNED)
EXPECTED_OUTPUT = EXPECTED_SNAPSHOT * 2 + struct.pack("<Q", EXPECTED_RESULT)


def save_objects(directory, compiled, inputs):
    for name, data in (("caller.cpp", inputs.cpp), ("read_record.rs", inputs.rust),
                       ("cpp.o", compiled.cpp_object), ("rust.o", compiled.rust_object)):
        (directory / name).write_bytes(data)
    (directory / "cpp.ir").write_text(compiled.cpp_ir, encoding="utf-8")
    (directory / "rust.ir").write_text(compiled.rust_ir, encoding="utf-8")


@pytest.mark.pcc_gate(
    unavailable=None if sys.platform == "linux" and platform.machine() == "x86_64"
    else "requires actual Linux x86-64 owned execution",
)
def test_cpp_stack_record_calls_rust_readonly_reference_native(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import SHN_UNDEF, parse_relocatable

    inputs = original_record_inputs()
    with monkeypatch.context() as owned:
        forbid_external_owners(owned)
        first = compile_record_executable(inputs)
        second = compile_record_executable(inputs)
    assert first == second
    assert original_record_inputs() == inputs
    compiled = first.compiled
    save_objects(tmp_path, compiled, inputs)
    (tmp_path / "observer.o").write_bytes(first.observer_object)
    (tmp_path / "observer.s").write_text(LINUX_OBSERVER, encoding="utf-8")
    cpp = parse_relocatable(compiled.cpp_object)
    rust = parse_relocatable(compiled.rust_object)
    observer = parse_relocatable(first.observer_object)
    foreign = {
        cpp.symbols[relocation.symbol_index].name
        for section in cpp.sections for relocation in section.relocations
        if cpp.symbols[relocation.symbol_index].section_index == SHN_UNDEF
    }
    assert foreign == {RUST_ENTRY, SNAPSHOT}
    assert any(s.name == CPP_ENTRY and s.section_index != SHN_UNDEF for s in cpp.symbols)
    assert any(s.name == RUST_ENTRY and s.section_index != SHN_UNDEF for s in rust.symbols)
    assert any(s.name == SNAPSHOT and s.section_index != SHN_UNDEF for s in observer.symbols)
    assert not any(s.name == RUST_ENTRY and s.section_index != SHN_UNDEF for s in observer.symbols)

    binary = tmp_path / "owned-record-pointer"
    binary.write_bytes(first.executable)
    binary.chmod(0o700)
    runs = []
    for index in range(2):
        try:
            result = subprocess.run([str(binary)], capture_output=True, timeout=5)
        except subprocess.TimeoutExpired as error:
            (tmp_path / f"run-{index}.stdout.bin").write_bytes(error.stdout or b"")
            (tmp_path / f"run-{index}.stderr.bin").write_bytes(error.stderr or b"")
            (tmp_path / f"run-{index}.json").write_text(json.dumps({
                "status": "TIMEOUT", "returncode": None, "timeout_s": error.timeout,
            }) + "\n")
            raise
        (tmp_path / f"run-{index}.stdout.bin").write_bytes(result.stdout)
        (tmp_path / f"run-{index}.stderr.bin").write_bytes(result.stderr)
        run = {"returncode": result.returncode, "stdout_hex": result.stdout.hex(),
               "stderr_hex": result.stderr.hex()}
        (tmp_path / f"run-{index}.json").write_text(json.dumps(run, indent=2) + "\n")
        assert (result.returncode, result.stdout, result.stderr) == (0, EXPECTED_OUTPUT, b"")
        assert struct.unpack("<iQq", result.stdout[:20]) == (TAG, WIDE, SIGNED)
        assert result.stdout[:20] == result.stdout[20:40]
        assert struct.unpack("<Q", result.stdout[40:]) == (EXPECTED_RESULT,)
        runs.append(run)
    receipt = {
        "owner": OWNER, "target": LINUX_TARGET, "executed": True,
        "input_sha256": {"cpp": hashlib.sha256(inputs.cpp).hexdigest(),
                         "rust": hashlib.sha256(inputs.rust).hexdigest()},
        "field_offsets": {"i32_tag": 0, "u64_wide": 8, "i64_signed_value": 16},
        "pointer_alignment_checked": 8, "padding_bytes_read": False,
        "sizeof_or_tail_padding_qualified": False,
        "c_abi_undefined_relocations": sorted(foreign),
        "fresh_builds_byte_identical": 2, "native_runs": runs,
        "returned_u64": EXPECTED_RESULT, "rustc_reference_executed": False,
        "object_sha256": {"cpp": hashlib.sha256(compiled.cpp_object).hexdigest(),
                          "rust": hashlib.sha256(compiled.rust_object).hexdigest(),
                          "observer": hashlib.sha256(first.observer_object).hexdigest()},
        "executable_sha256": hashlib.sha256(first.executable).hexdigest(),
    }
    (tmp_path / "observation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    assert original_record_inputs() == inputs


def test_record_pointer_arm64_darwin_cross_objects_and_relocations(tmp_path, monkeypatch):
    from pcc.backend import macho_spec

    inputs = original_record_inputs()
    with monkeypatch.context() as owned:
        forbid_external_owners(owned)
        first = compile_record_objects(inputs, DARWIN_TARGET)
        second = compile_record_objects(inputs, DARWIN_TARGET)
    assert first == second
    assert original_record_inputs() == inputs
    save_objects(tmp_path, first, inputs)
    cpp = macho_spec.parse_object(first.cpp_object)
    rust = macho_spec.parse_object(first.rust_object)
    for obj in (cpp, rust):
        assert obj.header["cputype"] == macho_spec.CPU_TYPE_ARM64
        assert obj.header["filetype"] == macho_spec.MH_OBJECT
    symbols = cpp.symbols()
    branches = {
        symbols[relocation["r_symbolnum"]]["name"]
        for section in cpp.sections() for relocation in cpp.relocations(section)
        if relocation["r_extern"] and relocation["r_type"] == macho_spec.ARM64_RELOC_BRANCH26
        and symbols[relocation["r_symbolnum"]]["n_type"] & macho_spec.N_TYPE == macho_spec.N_UNDF
    }
    assert branches == {"_" + RUST_ENTRY, "_" + SNAPSHOT}
    assert any(s["name"] == "_" + RUST_ENTRY and s["n_sect"] != 0 for s in rust.symbols())
    (tmp_path / "cross-object.json").write_text(json.dumps({
        "owner": "host-python/pcc-cpp+rust/public-ir/pcc-self/macho-cross-object",
        "target": DARWIN_TARGET, "executed": False, "linked": False,
        "fresh_builds_byte_identical": 2, "external_branch26": sorted(branches),
        "remaining_test_observer_symbol": "_" + SNAPSHOT,
        "input_sha256": {"cpp": hashlib.sha256(inputs.cpp).hexdigest(),
                         "rust": hashlib.sha256(inputs.rust).hexdigest()},
        "object_sha256": {"cpp": hashlib.sha256(first.cpp_object).hexdigest(),
                          "rust": hashlib.sha256(first.rust_object).hexdigest()},
    }, indent=2) + "\n")
