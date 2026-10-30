"""Integer atomic results retain side effects and aggregate escape behavior."""
import platform
import re
import subprocess
import sys

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


def _cas_program(width, field, success, shape="adjacent"):
    initial = (1 << (width - 1)) + 3
    desired = initial + 9
    expected = initial if success else initial + 1
    lines = [f"  %pair = cmpxchg ptr @cell, i{width} {expected}, i{width} {desired} acq_rel acquire"]
    if shape == "gap":
        lines.append("  fence seq_cst")
    if shape == "block":
        lines.extend(("  br label %extract", "extract:"))
    result_type = f"i{width}" if field == 0 else "i1"
    lines.append(f"  %observed = extractvalue {{i{width}, i1}} %pair, {field}")
    if shape == "both":
        lines.append(f"  %other = extractvalue {{i{width}, i1}} %pair, {1 - field}")
        other_type = "i1" if field == 0 else f"i{width}"
        other_expected = int(success) if field == 0 else initial
        lines.append(f"  %other_ok = icmp eq {other_type} %other, {other_expected}")
    lines.append("  %call = call i32 @noise()")
    projected = initial if field == 0 else int(success)
    stored = desired if success else initial
    lines.extend((
        f"  %same = icmp eq {result_type} %observed, {projected}",
        f"  %current = load atomic i{width}, ptr @cell acquire, align {width // 8}",
        f"  %stored = icmp eq i{width} %current, {stored}",
        "  %both = and i1 %same, %stored",
    ))
    check = "%both"
    if shape == "both":
        lines.append("  %all = and i1 %both, %other_ok")
        check = "%all"
    lines.extend((f"  br i1 {check}, label %good, label %bad", "good:",
                  "  ret i32 0", "bad:", "  ret i32 71"))
    return f'''target triple = "arm64-apple-darwin"
@cell = global i{width} {initial}, align {width // 8}
define i32 @noise() {{
entry:
  %v = load atomic i{width}, ptr @cell acquire, align {width // 8}
  %ok = icmp ne i{width} %v, 0
  %result = zext i1 %ok to i32
  ret i32 %result
}}
define i32 @main() {{
entry:
''' + "\n".join(lines) + "\n}\n"


@pytest.mark.parametrize("field", (0, 1))
def test_sole_cas_projection_avoids_pair_materialization(field):
    asm = emit_aarch64_darwin_asm(_cas_program(64, field, True), optimize=False)
    body = asm.split("_main:", 1)[1].split(".section", 1)[0]
    assert "  ldaxr " in body and "  stlxr " in body and "  clrex" in body
    assert "strb w14, [x15" not in body
    completion = body.split("_done:\n", 1)[1].split("  bl _noise", 1)[0]
    assert ("  cset " in completion) is (field == 1)


@pytest.mark.parametrize("shape", ("gap", "block", "both"))
def test_other_cas_consumers_keep_aggregate_storage(shape):
    asm = emit_aarch64_darwin_asm(_cas_program(64, 0, True, shape), optimize=False)
    assert "strb w14, [x15, #8]" in asm


def test_unused_integer_rmw_result_is_not_spilled(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    asm = emit_aarch64_darwin_asm('''target triple = "arm64-apple-darwin"
define void @unused(ptr %p) {
entry:
  %old = atomicrmw add ptr %p, i64 1 acq_rel
  ret void
}
''', optimize=False)
    body = asm.split("_unused:", 1)[1].split(".section", 1)[0]
    assert "ldaxr x11, [x0]" in body and "stlxr w13, x12, [x0]" in body
    assert not re.search(r"\b(?:str|stur) x11,", body)


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("width", (8, 16, 32, 64))
@pytest.mark.parametrize("field", (0, 1))
@pytest.mark.parametrize("success", (False, True))
def test_cas_result_and_memory_native(tmp_path, width, field, success):
    data = emit_owned_object(_cas_program(width, field, success), "arm64-apple-darwin")
    output = tmp_path / "cas-result"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("shape", ("gap", "block", "both"))
def test_cas_fallbacks_native(tmp_path, shape):
    data = emit_owned_object(_cas_program(64, 0, False, shape), "arm64-apple-darwin")
    output = tmp_path / "cas-fallback"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    assert subprocess.run([str(output)], timeout=10).returncode == 0


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("width", (8, 16, 32, 64))
def test_used_and_unused_rmw_results_keep_memory_effects(tmp_path, width):
    high_bit = 1 << (width - 1)
    text = f'''target triple = "arm64-apple-darwin"
@cell = global i{width} 17, align {width // 8}
define i32 @main() {{
entry:
  %unused = atomicrmw add ptr @cell, i{width} 3 acq_rel
  %prior = atomicrmw or ptr @cell, i{width} {high_bit} seq_cst
  %after = load atomic i{width}, ptr @cell acquire, align {width // 8}
  %old_ok = icmp eq i{width} %prior, 20
  %new_ok = icmp eq i{width} %after, {20 | high_bit}
  %ok = and i1 %old_ok, %new_ok
  br i1 %ok, label %good, label %bad
good:
  ret i32 0
bad:
  ret i32 74
}}
'''
    output = tmp_path / "rmw-results"
    output.write_bytes(link_executable([emit_owned_object(text, "arm64-apple-darwin")], entry="_main"))
    output.chmod(0o755)
    assert subprocess.run([str(output)], timeout=10).returncode == 0
