"""Independent native observer: stdout contains the full returned i32 lane.

This test-only kernel-ABI shim is assembled/linked by PCC, not a C compiler.
It does not use the Java frontend's compare or integer-conversion lowering.
"""
import hashlib
import json
import struct
import subprocess

from pcc.backend.owned_elf_link import link_inputs


def observe_i32(compilation, directory, monkeypatch):
    assembly = (
        ".intel_syntax noprefix\n.text\n.globl _start\n_start:\n"
        "  and rsp, -16\n  call " + compilation.entry_symbol + "\n"
        "  sub rsp, 16\n  mov DWORD PTR [rsp], eax\n"
        "  mov eax, 1\n  mov edi, 1\n  mov rsi, rsp\n  mov edx, 4\n  syscall\n"
        "  cmp rax, 4\n  jne observer_fail\n  mov eax, 60\n  xor edi, edi\n  syscall\n"
        "observer_fail:\n  mov eax, 60\n  mov edi, 111\n  syscall\n"
    )
    obj = directory / "methods.o"
    startup = directory / "observer.s"
    executable = directory / "observed"
    startup.write_text(assembly, encoding="utf-8")

    def external_owner_forbidden(*args, **kwargs):
        raise AssertionError("Java AOT object/link attempted an external subprocess")

    with monkeypatch.context() as denied:
        denied.setattr(subprocess, "Popen", external_owner_forbidden)
        obj.write_bytes(compilation.emit_object())
        link_inputs(target=compilation.module.triple, output=str(executable),
                    objects=[str(obj)], assembly=[str(startup)])
    result = subprocess.run([str(executable)], timeout=5, capture_output=True)
    (directory / "observed.stdout.bin").write_bytes(result.stdout)
    assert result.returncode == 0 and len(result.stdout) == 4, (result.returncode, result.stderr)
    value = struct.unpack("<i", result.stdout)[0]
    receipt = {"execution_owner": "pcc-self-owned-ELF-x86_64-linux", "returned_i32": value,
               "raw_stdout_hex": result.stdout.hex(), "process_exit_status": result.returncode,
               "object_sha256": hashlib.sha256(obj.read_bytes()).hexdigest(),
               "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}
    (directory / "observation.json").write_text(json.dumps(receipt, indent=2))
    return value
