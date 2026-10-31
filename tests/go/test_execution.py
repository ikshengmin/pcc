"""Real host-developed/self-backed Linux ELF Go execution, not native pcc1."""

import builtins
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys

import pytest

from pcc.frontends.go import GoDiagnostic
from tests.go.owned_execution import (
    Execution, compile_owned, compile_reference, run, write_artifact,
)


FIXTURES = Path(__file__).parent / "fixtures"
CASES = [
    (("scalars.go",), -40499999856),
    (("control.go",), 21440),
    (("multi_main.go", "multi_helper.go"), 409),
]


def units(names):
    return tuple((name, (FIXTURES / name).read_bytes().decode("utf-8")) for name in names)


def forbid_external_compiler(monkeypatch):
    original_import = builtins.__import__
    def checked_import(name, *args, **kwargs):
        if name == "llvmlite" or name.startswith("llvmlite."):
            raise AssertionError("owned Go compile imported " + name)
        return original_import(name, *args, **kwargs)
    def no_subprocess(*args, **kwargs):
        raise AssertionError("owned Go compile attempted external process")
    monkeypatch.setattr(builtins, "__import__", checked_import)
    monkeypatch.setattr(subprocess, "run", no_subprocess)
    monkeypatch.setattr(subprocess, "Popen", no_subprocess)


def test_go_emits_deterministic_owned_elf_without_external_tools(monkeypatch):
    forbid_external_compiler(monkeypatch)
    source_units = (("empty.go", "package main\nfunc main() {}\n"),)
    first, second = compile_owned(source_units), compile_owned(source_units)
    assert first.owner == "host-python/pcc-go/pcc-self/pcc-elf"
    assert first == second
    assert first.object[:4] == first.executable[:4] == b"\x7fELF"


def test_go_failure_never_publishes_an_artifact(tmp_path):
    binary = tmp_path / "rejected"
    with pytest.raises(GoDiagnostic, match="unsupported builtin panic"):
        write_artifact(compile_owned((("bad.go", "package main\nfunc main(){panic(1)}"),)), binary)
    assert not binary.exists()


@pytest.mark.integration
@pytest.mark.parametrize("names,expected", CASES)
@pytest.mark.parametrize("observe_result", [False, True], ids=["original-main", "full-i64-observer"])
def test_go_unchanged_program_executes_owned_native_code(tmp_path, monkeypatch, names, expected, observe_result):
    assert sys.platform == "linux" and platform.machine() == "x86_64", "requires real Linux x86_64 execution"
    with monkeypatch.context() as owned:
        forbid_external_compiler(owned)
        artifact = compile_owned(units(names), observe_result=observe_result)
    binary = tmp_path / "owned"
    write_artifact(artifact, binary)
    assert binary.read_bytes() == artifact.executable
    output = struct.pack("<q", expected) if observe_result else b""
    assert run(binary) == Execution(0, output, b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
@pytest.mark.parametrize("names,expected", CASES)
def test_go_explicit_official_compiler_oracle_receives_identical_program(tmp_path, names, expected):
    del expected
    assert os.environ.get("PCC_GO_REFERENCE_CACHE"), "select a shared bounded oracle cache explicitly"
    original = units(names)
    reference = compile_reference(original, tmp_path / "reference", compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    owned = tmp_path / "owned"
    write_artifact(compile_owned(original), owned)
    assert run(owned) == run(reference) == Execution(0, b"", b"")
    for filename, text in original:
        assert (reference.parent / filename).read_bytes() == text.encode("utf-8")
