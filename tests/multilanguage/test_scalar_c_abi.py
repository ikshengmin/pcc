"""C++ calls Java/Go/Rust in ONE owned-linked process, through two Java routes.

This component test does not qualify projects, native pcc1, callbacks from the
other frontends, shared handles/GC, exception propagation, or virtual threads.
The Java source gate and historical bytecode-compatibility experiment have
separate node IDs and receipts; class-file input cannot qualify source parsing.
"""

import builtins
import hashlib
import json
import platform
import struct
import subprocess
import sys

import pytest

from tests.multilanguage.owned_execution import (
    OWNERS,
    TARGET,
    compile_owned,
    original_inputs,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.pcc_gate(
        unavailable=None if sys.platform == "linux" and platform.machine() == "x86_64"
        else "requires actual Linux x86-64 owned execution",
    ),
]


def forbid_external_owners(monkeypatch):
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        if name == "llvmlite" or name.startswith("llvmlite."):
            raise AssertionError("cross-language owned compile imported " + name)
        return original_import(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("cross-language compile/link spawned an external process")

    monkeypatch.setattr(builtins, "__import__", checked_import)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


def assert_same_process(java_frontend, tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import SHN_UNDEF, parse_relocatable

    inputs = original_inputs(java_frontend)
    with monkeypatch.context() as owned:
        forbid_external_owners(owned)
        if java_frontend == "source":
            from pcc.frontends import java
            from pcc.frontends.java import compiler as compatibility

            def no_bytecode_route(*args, **kwargs):
                raise AssertionError("Java source gate attempted the bytecode compatibility route")

            owned.setattr(java, "compile_class", no_bytecode_route)
            owned.setattr(compatibility, "compile_class", no_bytecode_route)
            owned.setattr(compatibility, "parse_class", no_bytecode_route)
        first = compile_owned(inputs)
        second = compile_owned(inputs)
    # Same original bytes/options, freshly compiled twice, with no compile cache.
    assert first == second
    assert first.owner == OWNERS[java_frontend]
    assert original_inputs(java_frontend) == inputs
    assert [language for language, _ in first.ir] == ["cpp", "java", "go", "rust"]
    assert [language for language, _ in first.objects] == ["cpp", "java", "go", "rust", "observer"]
    parsed = {language: parse_relocatable(data) for language, data in first.objects}
    cpp = parsed["cpp"]
    references = {
        cpp.symbols[relocation.symbol_index].name
        for section in cpp.sections for relocation in section.relocations
        if cpp.symbols[relocation.symbol_index].section_index == SHN_UNDEF
    }
    # Prove these are real unresolved inter-object calls, not folded results or
    # disconnected per-language executables that happen to print the same data.
    assert references == {symbol for _language, symbol in first.exports}
    for language, symbol in first.exports:
        owner = "rust" if language.startswith("rust") else language
        assert any(item.name == symbol and item.section_index != SHN_UNDEF
                   for item in parsed[owner].symbols)

    java = -2000000000 - 127
    # Both stack-passed i64 arguments contain bits above bit 31; truncating
    # either stack slot cannot accidentally satisfy the raw-result assertion.
    delta = 1 + 2 + 4 + 8 + 16 + 0x100000020 + 0x200000040
    expected = (java, -5000000000000 - delta, 0xF1234500 ^ 0x100,
                0xF123456789ABC000 + delta, ((java & 0xFFFFFFFF) ^ 0x100) - delta)
    expected_bytes = struct.pack("<iqIQq", *expected)
    binary = tmp_path / "four-frontends"
    binary.write_bytes(first.executable)
    binary.chmod(0o700)
    assert binary.read_bytes() == first.executable
    # Retain the exact emitted inputs even when native execution fails.
    for language, text in first.ir:
        (tmp_path / (language + ".ir")).write_text(text, encoding="utf-8")
    for language, data in first.objects:
        (tmp_path / (language + ".o")).write_bytes(data)
    java_filename = "Interop.java" if java_frontend == "source" else "Interop.class"
    (tmp_path / java_filename).write_bytes(inputs.java)
    observations = []
    for index in range(2):
        result = subprocess.run([str(binary)], capture_output=True, timeout=5)
        (tmp_path / f"observed-{index}.stdout.bin").write_bytes(result.stdout)
        (tmp_path / f"observed-{index}.stderr.bin").write_bytes(result.stderr)
        assert (result.returncode, result.stdout, result.stderr) == (0, expected_bytes, b"")
        assert struct.unpack("<iqIQq", result.stdout) == expected
        observations.append(result.stdout.hex())

    # Small durable artifacts live in pytest's explicitly selected external
    # basetemp, never in the repository. No whole-tree snapshot/cache is needed.
    receipt = {
        "owner": first.owner,
        "target": TARGET,
        "scope": "same-process scalar C ABI component gate",
        "java_frontend": java_frontend,
        "rustc_reference_executed": False,
        "fresh_builds_byte_identical": 2,
        "native_observations": observations,
        "returned_values": expected,
        "exports": dict(first.exports),
        "input_sha256": {name: hashlib.sha256(getattr(inputs, name)).hexdigest()
                         for name in ("cpp", "java", "go", "rust")},
        "object_sha256": {name: hashlib.sha256(data).hexdigest()
                          for name, data in first.objects},
        "executable_sha256": hashlib.sha256(first.executable).hexdigest(),
    }
    (tmp_path / "observation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    assert original_inputs(java_frontend) == inputs


def test_cpp_calls_java_source_go_rust_in_one_owned_process(tmp_path, monkeypatch):
    assert_same_process("source", tmp_path, monkeypatch)


def test_cpp_calls_java_bytecode_go_rust_in_one_owned_process(tmp_path, monkeypatch):
    assert_same_process("bytecode-compat", tmp_path, monkeypatch)
