"""Real host-owned Linux PIDX boundary controls, without runtime provisioning.

C and explicitly raw-ABI Python share fixed post-frontend IR across emission
routes. Each indexed backend runs in a fresh pcc0 process; only the emitted
runtime-free programs execute. This does not qualify pcc1 or a managed runtime.
"""
from dataclasses import asdict
import builtins
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

import pytest

from pcc.backend.elf_x86_64 import (
    EM_X86_64,
    link_static_executable,
    parse_relocatable,
    parse_static_executable,
)
from pcc.backend.owned_elf_link import assemble
from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.target_objects import emit_x86_64_streamed_module
from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.frontends.python import pipeline


TARGET = "x86_64-unknown-linux-gnu"
ROOT = Path(__file__).resolve().parents[2]


def _linux_host():
    return sys.platform == "linux" and platform.machine() == "x86_64"


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def owned_host(monkeypatch):
    assert sys.implementation.name == "cpython"
    assert not os.environ.get("PCC_TEST_COMPILER"), "host pcc0 is required"
    original_import = builtins.__import__
    original_process = subprocess.Popen
    executables = set()

    def import_owned(name, *args, **kwargs):
        assert name != "llvmlite" and not name.startswith("llvmlite."), name
        return original_import(name, *args, **kwargs)

    def process_owned(command, *args, **kwargs):
        assert not kwargs.get("shell"), command
        assert isinstance(command, (tuple, list)), command
        assert (list(command[:4]) == [sys.executable, "-m", "pcc",
                                     "--pcc-self-backend-indexed-emit-worker"]
                or (len(command) == 1 and command[0] in executables)), command
        return original_process(command, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_owned)
    monkeypatch.setattr(subprocess, "Popen", process_owned)
    for key, value in {
        "PCC_NO_AUTO_PCC1": "1", "PCC_BACKEND": "self",
        "PCC_PYTHON_IR_PASSES": "off", "PCC_SELF_TARGET_PASSES": "none",
        "PCC_PRECISE_STACKMAP_TABLES": "1", "PYTHONHASHSEED": "0",
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("PCC_INDEXED_HANDOFF_REQUEST", "PCC_INDEXED_HANDOFF_SEAL_SHA256",
                "PCC_TARGET_TRIPLE", "PCC_TARGET_ARCH", "PCC_CODE_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    return executables


def _frontend_ir(kind, tmp_path):
    if kind == "c":
        source = """long bias = 7;
static long step(long value) { return value < 0 ? -value : value * 3; }
long boundary_answer(void) {
    long total = 0;
    for (long index = 0; index < 4; ++index) total += step(index);
    return total + bias + 17;
}
"""
        (tmp_path / "boundary.c").write_text(source)
        evaluator = CEvaluator(backend="self", target_triple=TARGET)
        units = evaluator.compile_translation_units(
            [TranslationUnit("boundary.c", "boundary.c", source)],
            use_system_cpp=False, use_compile_cache=False,
            include_dirs=[str(ROOT / "utils/fake_libc_include")],
        )
        assert len(units) == 1
        return units[0][1]
    source = """from pcc.extern import c_abi_export
__pcc_freestanding__ = True

@c_abi_export("raw_step")
def raw_step(value: i64) -> i64:
    if value < 0:
        return -value
    return value * 3 + 1

@c_abi_export("boundary_answer")
def boundary_answer() -> i64:
    return raw_step(7) + raw_step(-5) + 15
"""
    source_path = tmp_path / "raw_boundary.py"
    ir_path = tmp_path / "raw_boundary.ll"
    source_path.write_text(source)
    pipeline.compile_python(str(source_path), str(ir_path), emit_llvm_only=True,
                            libpython_mode="off", python_library=True,
                            target_triple=TARGET)
    return ir_path.read_text()


def _published_module(ir):
    module = parse_self_backend_module(ir)
    for function in module.functions:
        get_indexed_function_kernel(function)
    return module


def _emit_worker(sidecar, output, kind="PCO"):
    return subprocess.run(
        [sys.executable, "-m", "pcc", "--pcc-self-backend-indexed-emit-worker",
         str(sidecar), str(output), kind],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )


def _object_shape(data):
    obj = parse_relocatable(data)
    assert obj.machine == EM_X86_64
    return dict(machine=obj.machine, symbols=[asdict(symbol) for symbol in obj.symbols],
                sections=[dict(name=section.name, flags=section.flags, align=section.align,
                               size=section.size, sha256=_sha256(section.data),
                               relocations=[asdict(item) for item in section.relocations])
                          for section in obj.sections])


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=_linux_host)
@pytest.mark.parametrize("kind", ("c", "raw-python"))
def test_real_linux_boundary_bytes_and_native_execution(tmp_path, owned_host, kind):
    ir = _frontend_ir(kind, tmp_path)
    ir_path = tmp_path / "input.ll"
    ir_path.write_text(ir)
    sidecar = tmp_path / "input.pidx"
    encode_indexed_module_file(str(sidecar), _published_module(ir))
    sidecar_hash = _sha256(sidecar.read_bytes())
    unsplit = emit_x86_64_streamed_module(_published_module(ir)).encode_object()
    outputs = {"unsplit": unsplit}
    workers = []
    for mode in ("split", "split-repeat"):
        output = tmp_path / (mode + ".o")
        result = _emit_worker(sidecar, output)
        assert result.returncode == 0, result.stdout + result.stderr
        outputs[mode] = output.read_bytes()
        workers.append(dict(command=result.args, returncode=result.returncode,
                            stdout=result.stdout, stderr=result.stderr))
    assert _sha256(sidecar.read_bytes()) == sidecar_hash
    assert outputs["unsplit"] == outputs["split"] == outputs["split-repeat"]
    shapes = {mode: _object_shape(data) for mode, data in outputs.items()}
    assert shapes["unsplit"] == shapes["split"] == shapes["split-repeat"]
    obj = parse_relocatable(unsplit)
    assert "boundary_answer" in {symbol.name for symbol in obj.symbols}
    assert any(section.relocations for section in obj.sections)
    assert not {symbol.name for symbol in obj.symbols if symbol.section_index == 0 and symbol.name}
    startup = assemble(""".intel_syntax noprefix
.text
.globl _start
.type _start, @function
_start:
    call boundary_answer
    cmp rax, 42
    jne .Lfailed
    xor edi, edi
    jmp .Lexit
.Lfailed:
    mov edi, 1
.Lexit:
    mov eax, 60
    syscall
.size _start, .-_start
""", TARGET)
    runs = []
    for mode in ("unsplit", "split"):
        executable = tmp_path / (mode + ".exe")
        data = link_static_executable([startup, parse_relocatable(outputs[mode])])
        parse_static_executable(data)
        executable.write_bytes(data)
        executable.chmod(0o755)
        owned_host.add(str(executable))
        result = subprocess.run([str(executable)], capture_output=True, timeout=3)
        assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
        runs.append(dict(mode=mode, executable_sha256=_sha256(data),
                         returncode=result.returncode, stdout="", stderr=""))
    assert runs[0]["executable_sha256"] == runs[1]["executable_sha256"]
    (tmp_path / "boundary-receipt.json").write_text(json.dumps(dict(
        scope="host pcc0/self Linux PIDX boundary and runtime-free native execution",
        target=TARGET, frontend=kind, expected_raw_i64=42, source_ir_sha256=_sha256(ir.encode()),
        pidx_sha256=sidecar_hash, objects={mode: dict(sha256=_sha256(data), size=len(data),
                                                   shape=shapes[mode])
                                       for mode, data in outputs.items()},
        workers=workers, executions=runs,
    ), sort_keys=True) + "\n")


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=_linux_host)
@pytest.mark.parametrize(("target", "kind", "message"), (
    ("riscv64-unknown-linux-gnu", "PCO", "does not support target"),
    (TARGET, "UNKNOWN", "artifact kind must be ASM or PCO"),
))
def test_real_indexed_boundary_rejects_unsupported_modes(tmp_path, owned_host, target, kind, message):
    sidecar = tmp_path / "unsupported.pidx"
    encode_indexed_module_file(str(sidecar), _published_module(
        'target triple = "' + target + '"\ndefine i64 @answer() {\nentry:\n ret i64 42\n}\n'))
    output = tmp_path / "unsupported.o"
    result = _emit_worker(sidecar, output, kind)
    assert result.returncode == 1
    assert message in result.stderr
    assert not output.exists() and not output.with_suffix(".o.tmp").exists()
