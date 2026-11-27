"""Marked program code inlines ``pcc_gc_store_root`` for values needing no count.

Every assignment and exit of a pcc-Python local is a ``pcc_gc_store_root``
call: 161 per handler request on the stepped thread, 47 instructions each on
average (docs/status.md, item 3), most of them clearing a slot that is
empty or holds a tagged or immortal value. CPython's equivalent is a few
inline instructions. In code carrying the owned GC short-path marker, the
AArch64 call site now reads the runtime's ``pcc_gc_store_root_plain`` word
(1 once the debug cache and the runtime-log fast state are both off) and,
when the slot is not NULL, returns for an unchanged value and writes the
slot directly when both the old and the new value are NULL or tagged
(low bit set): what the function itself does for them. Anything else, and
any state with the word 0, calls the function, whose return PC stays a
stack-map call record. The programs run against a counting stand-in.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm

TARGET = "arm64-apple-darwin"
OBJECT = "ptrtoint (ptr @object to i64)"
TAGGED = "17"

# (plain word, slot is NULL, old value, new value, calls, final slot value)
CASES = [
    (1, False, "0", "0", 0, "0"),
    (1, False, "0", TAGGED, 0, TAGGED),
    (1, False, TAGGED, "0", 0, "0"),
    (1, False, TAGGED, TAGGED, 0, TAGGED),
    (1, False, OBJECT, OBJECT, 0, OBJECT),
    (1, False, "0", OBJECT, 1, OBJECT),
    (1, False, OBJECT, "0", 1, "0"),
    (1, False, TAGGED, OBJECT, 1, OBJECT),
    (0, False, "0", "0", 1, "0"),
    (0, False, TAGGED, TAGGED, 1, TAGGED),
    (1, True, "0", TAGGED, 0, "0"),
]


def _client(marked: bool) -> str:
    lines = ['target triple = "' + TARGET + '"']
    if marked:
        lines.append("@__pcc_owned_gc_short_paths_v1 = internal constant i8 1")
    # A registered keeper root makes every call a stack-map call record.
    lines.extend([
        "@object = global [2 x i64] zeroinitializer, align 16",
        "@frame_map = internal constant i32 1",
        "declare void @pcc_gc_store_root(ptr, ptr)",
        "declare void @pcc_gc_frame_enter_lifo(ptr, ptr)",
        "declare void @pcc_gc_frame_leave_lifo(ptr)",
        "declare void @configure(i32)",
        "declare i64 @call_count()",
        "define i32 @main() {",
        "entry:",
        "  %slot = alloca ptr, align 8",
        "  %keep = alloca ptr, align 8",
        "  store ptr null, ptr %keep, align 8",
        "  call void @pcc_gc_frame_enter_lifo(ptr @frame_map, ptr %keep)",
    ])
    for i, (plain, null_slot, old, new, calls, final) in enumerate(CASES):
        slot = "null" if null_slot else "%slot"
        lines.extend([
            f"  call void @configure(i32 {plain})",
            f"  %old{i} = inttoptr i64 {old} to ptr",
            f"  store ptr %old{i}, ptr %slot, align 8",
            f"  %new{i} = inttoptr i64 {new} to ptr",
            f"  call void @pcc_gc_store_root(ptr {slot}, ptr %new{i})",
            f"  %count{i} = call i64 @call_count()",
            f"  %got{i} = load ptr, ptr %slot, align 8",
            f"  %want{i} = inttoptr i64 {final} to ptr",
            f"  %count_ok{i} = icmp eq i64 %count{i}, {calls if marked else 1}",
            f"  %value_ok{i} = icmp eq ptr %got{i}, %want{i}",
            f"  %ok{i} = and i1 %count_ok{i}, %value_ok{i}",
            f"  br i1 %ok{i}, label %next{i}, label %bad{i}",
            f"bad{i}:",
            "  call void @pcc_gc_frame_leave_lifo(ptr %keep)",
            f"  ret i32 {10 + i}",
            f"next{i}:",
        ])
    lines.extend(["  call void @pcc_gc_frame_leave_lifo(ptr %keep)", "  ret i32 0", "}"])
    return "\n".join(lines) + "\n"


SUPPORT = '''target triple = "arm64-apple-darwin"
@pcc_gc_store_root_plain = global i32 0, align 4
@calls = internal global i64 0
@pcc_thread_stop_requested = global i32 0, align 4
@pcc_gc_frame_registry_inactive = global i32 0, align 4
define void @pcc_gc_frame_enter_lifo(ptr %map, ptr %slot) {
entry:
  ret void
}
define void @pcc_gc_frame_leave_lifo(ptr %slot) {
entry:
  ret void
}
define void @configure(i32 %plain) {
entry:
  store i32 %plain, ptr @pcc_gc_store_root_plain, align 4
  store i64 0, ptr @calls, align 8
  ret void
}
define i64 @call_count() {
entry:
  %n = load i64, ptr @calls, align 8
  ret i64 %n
}
define void @pcc_gc_store_root(ptr %slot, ptr %value) {
entry:
  %n = load i64, ptr @calls, align 8
  %next = add i64 %n, 1
  store i64 %next, ptr @calls, align 8
  %null = icmp eq ptr %slot, null
  br i1 %null, label %done, label %write
write:
  store ptr %value, ptr %slot, align 8
  br label %done
done:
  ret void
}
'''


def test_marked_store_root_calls_carry_the_plain_store_fast_path():
    marked = emit_aarch64_darwin_asm(_client(True), optimize=True)
    body = marked.split("_main:", 1)[1]
    assert "_pcc_gc_store_root_plain@GOTPAGEOFF" in body
    assert body.count("bl _pcc_gc_store_root") == len(CASES)
    assert re.search(r"\bstr x1, \[x0\]", body), body
    plain = emit_aarch64_darwin_asm(_client(False), optimize=True)
    assert "_pcc_gc_store_root_plain" not in plain


@pytest.mark.parametrize("marked", (False, True))
# Native execution targets Darwin AArch64.
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
def test_store_root_short_path_runs_every_case(tmp_path, marked):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_aarch64_gc_short_calls import _check_return_pcs
    data = emit_owned_object(_client(marked), TARGET)
    _check_return_pcs(data)
    binary = tmp_path / "store_root"
    binary.write_bytes(link_executable([data, emit_owned_object(SUPPORT, TARGET)]))
    binary.chmod(0o755)
    run = subprocess.run([str(binary)], capture_output=True, timeout=10,
                         env=dict(os.environ, PATH="/nonexistent"))
    assert (run.returncode, run.stdout, run.stderr) == (0, b"", b""), marked


def test_typed_and_text_emission_agree():
    from pcc.backend.arm64_asm_driver import assemble_file
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    source = _client(True)
    expected = assemble_file(emit_aarch64_darwin_asm(source, optimize=True))
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(source),
                                                    optimize=True, structured_instructions=True)
    assert transport.assemble_sections() == expected
