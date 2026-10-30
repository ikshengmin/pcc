"""Atomic integer results use normal liveness without changing memory order."""
import platform
import re
import subprocess
import sys

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


@pytest.mark.parametrize("width", (8, 16, 32, 64))
@pytest.mark.parametrize("ordering", ("unordered", "monotonic", "acquire", "seq_cst"))
def test_atomic_loads_reach_assigned_registers(width, ordering, monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    text = f'''target triple = "arm64-apple-darwin"
define i{width} @probe(ptr %address) {{
entry:
  %first = load atomic i{width}, ptr %address {ordering}, align {width // 8}
  %second = load atomic i{width}, ptr %address {ordering}, align {width // 8}
  %result = add i{width} %first, %second
  ret i{width} %result
}}
'''
    assembly = emit_aarch64_darwin_asm(text, optimize=False)
    body = assembly.split("_probe:", 1)[1].split(".section", 1)[0]
    suffix = "b" if width == 8 else "h" if width == 16 else ""
    opcode = ("ldar" if ordering in ("acquire", "seq_cst") else "ldr") + suffix
    register = "x" if width == 64 else "w"
    assert len(re.findall(r"  " + opcode + r" " + register + r"[1-8], \[x0\]", body)) == 2
    assert not re.search(r"\b(?:str|stur)[bh]? w10,", body)
    assert not re.search(r"\b(?:str|stur) x10,", body)


def _pressure_program(width):
    # All 24 observations survive a call and a branch; the callee-saved pool
    # cannot hold them all. The mutation also forbids reloading the source in
    # place of preserving an earlier atomic observation.
    values = [(1 << (width - 1)) + index * 3 + 1 for index in range(24)]
    statements = []
    for index, value in enumerate(values):
        statements.extend((
            f"  store atomic i{width} {value}, ptr @cell release, align {width // 8}",
            f"  %value{index} = load atomic i{width}, ptr @cell acquire, align {width // 8}",
        ))
    statements.extend((
        "  %called = call i32 @mutate()",
        "  %ready = icmp eq i32 %called, 42",
        "  br i1 %ready, label %consume, label %bad",
        "consume:",
    ))
    previous = "%value0"
    for index in range(1, len(values)):
        result = "%sum" + str(index)
        statements.append(f"  {result} = add i{width} {previous}, %value{index}")
        previous = result
    expected = sum(values) % (1 << width)
    statements.extend((
        f"  %same = icmp eq i{width} {previous}, {expected}",
        f"  %current = load atomic i{width}, ptr @cell seq_cst, align {width // 8}",
        f"  %fresh = icmp eq i{width} %current, 0",
        "  %ok = and i1 %same, %fresh",
        "  br i1 %ok, label %good, label %bad",
        "good:", "  ret i32 0", "bad:", "  ret i32 73",
    ))
    return f'''target triple = "arm64-apple-darwin"
@cell = global i{width} 0, align {width // 8}
define i32 @mutate() {{
entry:
  store atomic i{width} 0, ptr @cell release, align {width // 8}
  ret i32 42
}}
define i32 @main() {{
entry:
''' + "\n".join(statements) + "\n}\n"


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("width", (8, 16, 32, 64))
@pytest.mark.parametrize("callee_saved", ("0", "1"))
def test_atomic_values_survive_calls_branches_and_register_pressure(tmp_path, monkeypatch, width, callee_saved):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", callee_saved)
    data = emit_owned_object(_pressure_program(width), "arm64-apple-darwin")
    output = tmp_path / "atomic-pressure"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    result = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
