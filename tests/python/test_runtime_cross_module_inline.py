"""The owned runtime archive inlines small definitions across its modules.

Each runtime module was optimized alone, so a list append called the
container lock and unlock, ``py_err_occurred`` and the GC retain of other
modules through full calls (docs/status.md, item 3). Between its two
phases the build now gives each importing module internal copies of the
small external definitions it calls (``runtime_cross_module_inline``),
then inlines them; LLVM's (Thin)LTO imports definitions the same way.
These cases pin the selection (private references, size, constructs and
frame intrinsics refused), the declarations a copy brings and the call
retargeting (address-taken uses keep the original), then link two modules
that share a thread-local variable and run them before and after.
"""
from __future__ import annotations

import subprocess

import pytest

from pcc.frontends.python import runtime_cross_module_inline as xinline

TARGET = 'target triple = "arm64-apple-darwin"\n'

LOCKS = TARGET + '''@lock_state = thread_local global i64 0, align 8
@stripes = global [4 x i64] [i64 0, i64 0, i64 0, i64 0], align 8
@hidden_count = internal global i64 0
declare ptr @llvm.threadlocal.address.p0(ptr)
declare ptr @llvm.frameaddress.p0(i32)
define external void @take_lock(i64 %index) "no-builtins" {
entry:
  %state = call ptr @llvm.threadlocal.address.p0(ptr @lock_state)
  %depth = load i64, ptr %state, align 8
  %next = add i64 %depth, 1
  store i64 %next, ptr %state, align 8
  %slot = getelementptr i64, ptr @stripes, i64 %index
  store i64 1, ptr %slot, align 8
  ret void
}
define external void @drop_lock(i64 %index) {
entry:
  %state = call ptr @llvm.threadlocal.address.p0(ptr @lock_state)
  %depth = load i64, ptr %state, align 8
  %next = sub i64 %depth, 1
  store i64 %next, ptr %state, align 8
  %slot = getelementptr i64, ptr @stripes, i64 %index
  store i64 0, ptr %slot, align 8
  ret void
}
define external i64 @lock_depth() {
entry:
  %depth = load i64, ptr @lock_state, align 8
  ret i64 %depth
}
define external void @take_twice(i64 %index) {
entry:
  call void @take_lock(i64 %index)
  call void @take_lock(i64 %index)
  ret void
}
define external i64 @counts_privately() {
entry:
  %n = load i64, ptr @hidden_count, align 8
  ret i64 %n
}
define external i64 @frame_bits() {
entry:
  %f = call ptr @llvm.frameaddress.p0(i32 0)
  %i = ptrtoint ptr %f to i64
  ret i64 %i
}
define internal i64 @private_helper() {
entry:
  ret i64 3
}
'''

USER = TARGET + '''declare void @take_lock(i64)
declare void @drop_lock(i64)
declare i64 @lock_depth()
declare void @take_twice(i64)
@keep = global ptr @take_lock, align 8
define i32 @main() {
entry:
  call void @take_lock(i64 2)
  %inside = call i64 @lock_depth()
  call void @drop_lock(i64 2)
  call void @take_twice(i64 1)
  %twice = call i64 @lock_depth()
  call void @drop_lock(i64 1)
  call void @drop_lock(i64 1)
  %after = call i64 @lock_depth()
  %ok1 = icmp eq i64 %inside, 1
  %ok2 = icmp eq i64 %twice, 2
  %ok3 = icmp eq i64 %after, 0
  %a = and i1 %ok1, %ok2
  %b = and i1 %a, %ok3
  %status = select i1 %b, i32 0, i32 37
  ret i32 %status
}
'''


def _table():
    return xinline.cross_module_inline_table({"locks": LOCKS, "user": USER})


def test_small_external_definitions_without_private_references_are_selected():
    table = _table()
    assert {"take_lock", "drop_lock", "lock_depth", "take_twice", "main"} <= set(table)
    # A private global, the frame address and internal linkage keep their calls.
    assert "counts_privately" not in table
    assert "frame_bits" not in table
    assert "private_helper" not in table
    entry = table["take_lock"]
    assert entry["module"] == "locks"
    assert entry["define"] == "define internal void @take_lock.pcc.xinl(i64 %index) {"
    assert entry["declarations"] == {
        "llvm.threadlocal.address.p0": "declare ptr @llvm.threadlocal.address.p0(ptr)",
        "lock_state": "@lock_state = external thread_local global i64, align 8",
        "stripes": "@stripes = external global [4 x i64], align 8",
    }


def test_oversized_definitions_are_not_selected(monkeypatch):
    monkeypatch.setattr(xinline, "MAX_INSTRUCTIONS", 4)
    table = _table()
    assert "lock_depth" in table and "take_lock" not in table


def test_a_module_gets_copies_of_what_it_calls_and_their_callees():
    table = _table()
    text, copies = xinline.import_cross_module_definitions(USER, table, "user")
    assert copies == 4
    # Calls are retargeted; the address-taken use keeps the original.
    assert "call void @take_lock.pcc.xinl(i64 2)" in text
    assert "@keep = global ptr @take_lock, align 8" in text
    assert "declare void @take_lock(i64)" in text
    # take_twice's own calls reach the copy as well.
    assert text.count("define internal void @take_lock.pcc.xinl(i64 %index) {") == 1
    assert "define internal void @take_twice.pcc.xinl(i64 %index) {" in text
    assert text.count("@lock_state = external thread_local global i64, align 8") == 1
    assert text.count("declare ptr @llvm.threadlocal.address.p0(ptr)") == 1
    # The defining module imports nothing of its own.
    assert xinline.import_cross_module_definitions(LOCKS, table, "locks") == (LOCKS, 0)


def test_global_declarations_carry_aggregate_types_and_thread_locality():
    assert xinline._global_declaration(
        "s", "thread_local global { i64, [2 x ptr] } zeroinitializer, align 16"
    ) == "@s = external thread_local global { i64, [2 x ptr] }, align 16"
    assert xinline._global_declaration("c", "constant [3 x i8] c\"ab\\00\"") == \
        "@c = external constant [3 x i8]"
    assert xinline._global_declaration("w", "weak global i64 0") is None


@pytest.mark.parametrize("imported", (False, True))
# Native execution on Darwin arm64: the copies and the shared thread-local.
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
def test_imported_and_inlined_modules_link_and_run(tmp_path, imported):
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from pcc.backend.self_backend_verify import verify_parsed_module
    from pcc.ir.optimization.driver import optimize_ir

    user = USER
    if imported:
        user, copies = xinline.import_cross_module_definitions(USER, _table(), "user")
        assert copies == 4
        user = optimize_ir(user, xinline.IMPORT_PASSES)
        main = user[user.index("define i32 @main()"):]
        main = main[:main.index("\n}\n")]
        assert "call void @take_lock" not in main and "call i64 @lock_depth" not in main, main
        verify_parsed_module(parse_self_backend_module(user))
    binary = tmp_path / "xinline"
    binary.write_bytes(link_executable(
        [emit_owned_object(LOCKS, "arm64-apple-darwin"), emit_owned_object(user, "arm64-apple-darwin")],
        entry="_main",
    ))
    binary.chmod(0o755)
    ran = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, b"", b"")


def test_the_build_imports_only_into_ordinary_darwin_modules(tmp_path):
    from pcc.frontends.python import owned_runtime_build as build

    assert build.cross_module_inline_target("arm64-apple-darwin")
    assert not build.cross_module_inline_target("x86_64-unknown-linux-gnu")
    assert not build.cross_module_inline_target("aarch64-unknown-linux-gnu")
    py = tmp_path / "py"
    py.mkdir()
    (py / "plain.py").write_text("x = 1\n")
    (py / "bare.py").write_text('"""doc"""\n__pcc_freestanding__ = True\n')
    assert build._imports_cross_module_definitions(str(tmp_path), "plain")
    assert not build._imports_cross_module_definitions(str(tmp_path), "bare")
    (py / "py_runtime_log.py").write_text("x = 1\n")
    assert not build._imports_cross_module_definitions(str(tmp_path), "py_runtime_log")
    assert not build._imports_cross_module_definitions(str(tmp_path), "missing")


def test_importing_modules_outline_after_the_object_phase_import(tmp_path):
    """An importing module's slow paths are outlined after its imports are
    inlined: in the object phase's IMPORT_PASSES, or alone when nothing
    was imported. Others outline in the IR phase."""
    from pcc.frontends.python import owned_runtime_build as build

    py = tmp_path / "py"
    py.mkdir()
    (py / "plain.py").write_text("x = 1\n")
    (py / "bare.py").write_text('"""doc"""\n__pcc_freestanding__ = True\n')
    passes = "mem2reg,sroa,instcombine,outline-slow-paths,dce"
    assert build._defers_outlining(str(tmp_path), "plain", "arm64-apple-darwin", passes)
    assert not build._defers_outlining(str(tmp_path), "bare", "arm64-apple-darwin", passes)
    assert not build._defers_outlining(str(tmp_path), "plain", "aarch64-unknown-linux-gnu", passes)
    assert not build._defers_outlining(str(tmp_path), "plain", "arm64-apple-darwin",
                                       "mem2reg,sroa,dce")
    assert build._without_pass(passes, build.OUTLINE_PASS) == "mem2reg,sroa,instcombine,dce"
    names = xinline.IMPORT_PASSES.split(",")
    assert names.index("outline-slow-paths") == len(names) - 2 and names[-1] == "dce"
    assert names.index("outline-slow-paths") > max(
        index for index, name in enumerate(names) if name.startswith("inline"))
