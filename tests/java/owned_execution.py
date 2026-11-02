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


def observe_scalar(compilation, directory, monkeypatch, arguments=()):
    """Observe exact primitive result bytes, with independent SysV arguments.

    This shim performs no Java arithmetic, comparison, extension or narrowing.
    ``arguments`` supplies already typed (Z/B/S/C/I/J/F/D, value) ABI lanes.
    F/D values and results are raw unsigned IEEE bits, never host conversions.
    Narrow argument high bits are not interpreted by this observer; the callee
    must normalize the declared low-width lane itself.
    """
    result_type = compilation.entry[1].rsplit(")", 1)[-1]
    width, register, memory, signed = {"Z": (1, "al", "BYTE", False),
                                       "B": (1, "al", "BYTE", True),
                                       "S": (2, "ax", "WORD", True),
                                       "C": (2, "ax", "WORD", False),
                                       "I": (4, "eax", "DWORD", True),
                                       "J": (8, "rax", "QWORD", True),
                                       "F": (4, "eax", "DWORD", False),
                                       "D": (8, "rax", "QWORD", False)}[result_type]
    assert compilation.entry[1] == "(" + "".join(kind for kind, _ in arguments) + ")" + result_type
    lines = [".intel_syntax noprefix", ".text", ".globl _start", "_start:", "  and rsp, -16"]
    # SysV has independent integer and SSE register banks. Overflow arguments
    # occupy ordered eight-byte stack slots regardless of which bank filled.
    locations, integer_count, floating_count, stack_count = [], 0, 0, 0
    for kind, _ in arguments:
        floating = kind in {"F", "D"}
        index, limit = (floating_count, 8) if floating else (integer_count, 6)
        if index < limit:
            locations.append(("fp" if floating else "gp", index))
        else:
            locations.append(("stack", stack_count))
            stack_count += 1
        if floating:
            floating_count += 1
        else:
            integer_count += 1
    stack_size = ((stack_count * 8 + 15) // 16) * 16
    if stack_size:
        lines.append("  sub rsp, " + str(stack_size))
    names = (("edi", "esi", "edx", "ecx", "r8d", "r9d"),
             ("rdi", "rsi", "rdx", "rcx", "r8", "r9"))
    for (kind, value), (bank, index) in zip(arguments, locations):
        assert kind in ("Z", "B", "S", "C", "I", "J", "F", "D") and (kind != "Z" or value in (0, 1))
        bits = 64 if kind in {"J", "D"} else 32
        immediate = hex(value & ((1 << bits) - 1))
        if bank == "fp":
            reg, move = ("rax", "movq") if kind == "D" else ("eax", "movd")
            lines += ["  mov " + reg + ", " + immediate, "  " + move + " xmm" + str(index) + ", " + reg]
        elif bank == "gp":
            lines.append("  mov " + names[kind == "J"][index] + ", " + immediate)
        else:
            offset = str(index * 8)
            if bits == 64:
                lines += ["  mov rax, " + immediate, "  mov QWORD PTR [rsp+" + offset + "], rax"]
            else:
                lines.append("  mov DWORD PTR [rsp+" + offset + "], " + immediate)
    lines.append("  call " + compilation.entry_symbol)
    if result_type in {"F", "D"}:
        lines.append("  movd eax, xmm0" if result_type == "F" else "  movq rax, xmm0")
    if stack_size:
        lines.append("  add rsp, " + str(stack_size))
    lines += ["  sub rsp, 16", "  mov " + memory + " PTR [rsp], " + register,
              "  mov eax, 1", "  mov edi, 1", "  mov rsi, rsp", "  mov edx, " + str(width),
              "  syscall", "  cmp rax, " + str(width), "  jne observer_fail",
              "  mov eax, 60", "  xor edi, edi", "  syscall", "observer_fail:",
              "  mov eax, 60", "  mov edi, 111", "  syscall"]
    obj, startup, executable = (directory / name for name in ("methods.o", "observer.s", "observed"))
    startup.write_text("\n".join(lines) + "\n")
    def forbidden(*args, **kwargs):
        raise AssertionError("Java scalar owned object/link invoked an external compiler")
    with monkeypatch.context() as denied:
        denied.setattr(subprocess, "Popen", forbidden)
        obj.write_bytes(compilation.emit_object())
        link_inputs(target=compilation.module.triple, output=str(executable),
                    objects=[str(obj)], assembly=[str(startup)])
    result = subprocess.run([str(executable)], timeout=5, capture_output=True)
    (directory / "observed.stdout.bin").write_bytes(result.stdout)
    (directory / "observed.stderr.bin").write_bytes(result.stderr)
    value = int.from_bytes(result.stdout, "little", signed=signed)
    receipt = {"execution_owner": "pcc-self-owned-ELF-x86_64-linux", "result_descriptor": result_type,
               "arguments": arguments, "returned_value": value, "result_width_bytes": width,
               "raw_stdout_hex": result.stdout.hex(), "raw_stderr_hex": result.stderr.hex(), "process_exit_status": result.returncode,
               "object_sha256": hashlib.sha256(obj.read_bytes()).hexdigest(),
               "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}
    (directory / "observation.json").write_text(json.dumps(receipt, indent=2))
    assert result.returncode == 0 and len(result.stdout) == width, (result.returncode, result.stderr)
    return value
