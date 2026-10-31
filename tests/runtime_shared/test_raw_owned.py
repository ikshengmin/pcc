"""R0a real native storage, host-PCC compilation, owned object/link closure.

No host allocator/runtime, external compiler/assembler/linker, archived runtime,
model, or pcc1 claim. The C driver retains the exact void** signature. Current
C++ pointer-signature restrictions are not erased to fabricate C++ support.
Only Linux x86-64 execution is admitted here; other platform/GC/threaded cells
and production runtime-archive integration remain unqualified.
"""
from pathlib import Path
import ast
import json
import platform
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
TARGET = "x86_64-unknown-linux-gnu"


def _slice(source: Path, destination: Path, names: set[str]) -> None:
    """Keep exact source spans, declarations and globals, never rewritten bodies.

    Avoid pulling environment/exit/strerror/spawn semantics into this allocation
    gate. These slices are labeled compiler input, not whole-runtime proof.
    """
    text = source.read_text()
    tree = ast.parse(text)
    found = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert names <= found
    lines = text.splitlines(keepends=True)
    chunks = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name not in names:
            continue
        begin = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", ()))])
        chunks.append("".join(lines[begin - 1:node.end_lineno]))
    destination.write_text("\n\n".join(chunks) + "\n")


def _limit_address_space():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (64 * 1024 * 1024, 64 * 1024 * 1024))


@pytest.mark.pcc_gate(probe=lambda: sys.platform == "linux" and platform.machine() == "x86_64")
def test_raw_storage_real_owned_native_process(tmp_path, monkeypatch):
    assert sys.platform == "linux" and platform.machine() == "x86_64", (
        "this execution gate requires real Linux x86-64, not cross objects"
    )
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.driver.cli_core import cli_main
    from pcc.frontends.python.pipeline import compile_python

    runtime = ROOT / "pcc/runtime/py"
    # The unchanged allocator's duplicate-free diagnostics read one GC policy
    # word. Link its complete real definition-only provider, never a fake zero
    # symbol; no collector implementation or managed graph participates.
    inputs = [runtime / "freestanding_allocator.py",
              runtime / "freestanding_gc_state.py",
              runtime / "freestanding_shared_raw.py"]
    slices = {
        "freestanding_errno.py": {"pcc_errno_location", "pcc_errno_get", "pcc_errno_set"},
        "freestanding_c_linux_start.py": {"pcc_linux_initial_tls_setup", "pcc_linux_allocate_tls"},
        "freestanding_platform_process.py": {"pcc_platform_abort", "pcc_platform_kill"},
        "freestanding_platform_io.py": {"pcc_platform_write"},
    }
    for filename, names in slices.items():
        path = tmp_path / filename
        _slice(runtime / filename, path, names)
        inputs.append(path)
    inputs.append(FIXTURES / "raw_entry.py")
    objects = []
    ir_by_name = {}
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            pytest.fail("raw storage compilation tried to spawn an external owner")
        patch.setattr(subprocess, "Popen", forbidden)
        for source in inputs:
            ir_path = tmp_path / (source.stem + ".ll")
            compile_python(str(source), str(ir_path), emit_llvm_only=True,
                           libpython_mode="off", python_library=True,
                           backend="self", target_triple=TARGET)
            ir = ir_path.read_text()
            ir_by_name[source.stem] = ir
            data = emit_owned_object(ir, TARGET)
            (tmp_path / (source.stem + ".o")).write_bytes(data)
            objects.append(parse_relocatable(data))
        driver = tmp_path / "raw_buffer.o"
        assert cli_main([str(FIXTURES / "raw_buffer.c"), "--emit-obj", str(driver),
                         "--no-cache", "-O0", "--target=" + TARGET,
                         "--cpp-arg=-I" + str(ROOT / "pcc/runtime/include")]) == 0
        objects.append(parse_relocatable(driver.read_bytes()))
        # Component-test observer, not a Python arbitrary-extern admission.
        # TLS and buffer operations remain genuine linked pcc-Python/C bodies.
        startup = assemble_file(
            ".intel_syntax noprefix\n.text\n.globl _start\n_start:\n"
            "  mov rdi, rsp\n  sub rsp, 16\n"
            "  call pcc_linux_initial_tls_setup\n"
            "  test rax, rax\n  jne .Ltls_failed\n"
            "  call raw_test_entry\n"
            "  mov qword ptr [rsp], 0\n  mov dword ptr [rsp], eax\n"
            "  mov rax, 1\n  mov rdi, 1\n  mov rsi, rsp\n  mov rdx, 8\n  syscall\n"
            "  cmp rax, 8\n  jne .Lwrite_failed\n"
            "  mov edi, dword ptr [rsp]\n  mov rax, 60\n  syscall\n"
            ".Ltls_failed:\n  mov rdi, 71\n  mov rax, 60\n  syscall\n"
            ".Lwrite_failed:\n  mov rdi, 72\n  mov rax, 60\n  syscall\n"
        )
        objects.insert(0, startup)
        # Undefined symbols must be satisfied by these current source objects.
        # No archive and no stub support object enters this final link.
        executable = link_static_executable(objects, entry="_start")
    raw_ir = ir_by_name["freestanding_shared_raw"]
    import re
    for name, signature in {
        "pcc_rt_raw_alloc": ("i64", "i64", "i32", "ptr"),
        "pcc_rt_raw_resize": ("ptr", "i64", "i64", "i64", "ptr"),
        "pcc_rt_raw_free": ("ptr", "i64", "i64"),
    }.items():
        match = re.search(r"define(?:\s+external)?\s+i32\s+@" + name + r"\(([^)]*)\)", raw_ir)
        assert match, name
        assert tuple(argument.strip().split()[0] for argument in match[1].split(",")) == signature
    binary = tmp_path / "raw_owned"
    binary.write_bytes(executable)
    binary.chmod(0o700)
    result = subprocess.run([str(binary)], capture_output=True, timeout=10,
                            preexec_fn=_limit_address_space)
    (tmp_path / "native.stdout").write_bytes(result.stdout)
    (tmp_path / "native.stderr").write_bytes(result.stderr)
    (tmp_path / "native-result.json").write_text(json.dumps({
        "returncode": result.returncode, "stdout_hex": result.stdout.hex(),
        "stderr_hex": result.stderr.hex(),
    }, indent=2) + "\n")
    assert (result.returncode, result.stdout, result.stderr) == (0, b"\0" * 8, b""), (
        f"raw checkpoint/status={int.from_bytes(result.stdout, 'little')} "
        f"returncode={result.returncode} stderr={result.stderr!r}"
    )
    # A receipt beside the raw artifacts, never inside the source repository.
    (tmp_path / "ownership.json").write_text(json.dumps({
        "compiler": "host CPython/PCC", "execution": "native Linux x86-64",
        "startup": "owned assembly component-test observer; unchanged owned TLS bodies",
        "emitter": "PCC self", "linker": "PCC ELF", "archive": None,
        "allocator": "current unchanged freestanding_allocator.py",
        "runtime_scope": "raw allocation; no selected collector or managed payload",
        "threading": "single carrier; production atomic lock emitted, concurrency unrun",
        "address_space_limit_bytes": 64 * 1024 * 1024,
        "cpp": "unrun: exact void** signature not admitted by current frontend",
    }, indent=2) + "\n")


def test_memalign_admission_matches_owned_provider_and_rejects_mismatch():
    from pcc.frontends.python.pipeline_freestanding import (
        freestanding_allowed_external_symbols,
        validate_freestanding_ir,
    )
    from pcc.frontends.python.pipeline_modes import PyPipelineError

    source = (ROOT / "pcc/runtime/py/freestanding_allocator.py").read_text()
    provider = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == "pcc_memalign")
    assert [arg.annotation.id for arg in provider.args.args] == ["i64", "i64"]
    assert provider.returns.id == "c_ptr"
    assert ast.literal_eval(provider.decorator_list[0].args[0]) == "memalign"
    exact = 'allocate = extern("memalign", (c_int64, c_int64), c_ptr)'
    admitted = freestanding_allowed_external_symbols(exact)
    assert admitted == {"memalign"}
    ir = "define ptr @probe(i64 %a, i64 %n) {\n %p = call ptr @memalign(i64 %a, i64 %n)\n ret ptr %p\n}"
    validate_freestanding_ir(ir, admitted)
    for wrong in (
        exact.replace("c_int64, c_int64", "c_int32, c_int64"),
        exact.replace("c_int64, c_int64", "c_int64, c_uint64"),
        exact.replace(", c_ptr)", ", c_int64)"),
        exact.replace('"memalign"', '"not_memalign"'),
    ):
        denied = freestanding_allowed_external_symbols(wrong)
        assert "memalign" not in denied
        with pytest.raises(PyPipelineError, match="outside its verified closure"):
            validate_freestanding_ir(ir, denied)

    # Freestanding mode requires explicit export names for local helpers too.
    adapter = ast.parse((ROOT / "pcc/runtime/py/freestanding_shared_raw.py").read_text())
    for function in (node for node in adapter.body if isinstance(node, ast.FunctionDef)):
        assert any(isinstance(decorator, ast.Call)
                   and isinstance(decorator.func, ast.Name)
                   and decorator.func.id in ("c_abi_export", "c_abi_typed_export")
                   for decorator in function.decorator_list), function.name
