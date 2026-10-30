"""Private operand copies export owners without temporary address leases."""
import os
from pathlib import Path
import re
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime

ROOT = Path(__file__).resolve().parents[2]


def test_shared_valueclass_destination_keeps_locked_copy(tmp_path):
    from tests.python.test_shared_call_binding import _emit

    text = _emit('''import pcc
@pcc.valueclass
class Record:
    value: list
record = Record([])
def probe():
    global record
    local = Record([1])
    record = local
''')
    (tmp_path / "shared_destination.ll").write_text(text)
    copies = [line for line in text.splitlines()
              if "call " in line and "@pcc_gc_root_copy" in line
              and "value.copy.global" in line]
    assert copies
    assert all("@pcc_gc_root_copy_lease(" in line for line in copies)


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("borrowed", (0, 1))
def test_private_copy_reloads_after_lock_wait_without_address_transaction(backend, borrowed):
    from tests.python.test_foreign_address_leases import _functions
    from tests.python.test_runtime_entry_handoff import HandoffModel

    model = HandoffModel(backend)
    _functions(ROOT / "pcc/runtime/py/py_obj.py", {
        "pcc_gc_root_copy_local", "pcc_gc_root_copy_owned_prepare_locked",
    }, model.ns)
    source = model.object(1000)
    model.object(2000)
    destination = model.empty()
    model.on_lock = lambda: model.store(source, 0, 2000)
    assert model.ns["pcc_gc_root_copy_local"](destination, source, borrowed) == 0
    assert model.load(destination, 0) == 2000
    assert model.references == {1000: 1, 2000: 2}
    assert model.active() == 0
    assert ("retain_publish", destination, 1000) not in model.events
    assert ("borrowed_reload", source) in model.events if borrowed else ("reload", source) in model.events
    assert ("reload", source) not in model.events if borrowed else ("borrowed_reload", source) not in model.events


@pytest.mark.parametrize("backend", (3, 4))
def test_private_borrowed_copy_heals_forwarding_without_source_reference_transfer(backend):
    from tests.python.test_foreign_address_leases import _functions
    from tests.python.test_runtime_entry_handoff import HandoffModel

    model = HandoffModel(backend)
    source = model.object(1000)
    model.object(2000)
    destination = model.empty()
    model.store("pcc_gc_read_barrier_enabled", 0, 1)
    model.ns.update(
        _gc_forwarding_population=lambda: 1,
        _gc_relocation_candidate=lambda value: int(value == 1000),
        _gc_backend4_should_check_slot=lambda slot: 1,
        pcc_gc_backend4_slot_needs_resolve=lambda value: int(value == 1000),
        pcc_gc_note_load=lambda: None,
        pcc_gc_note_relocation_read=lambda value: 2000 if value == 1000 else value,
    )
    def forbidden_owning_resolver(*args):
        raise AssertionError("borrowed alias used the owning-slot resolver")
    model.ns["pcc_gc_resolve_root_slot_unlocked"] = forbidden_owning_resolver
    _functions(ROOT / "pcc/runtime/py/py_obj.py", {
        "pcc_gc_root_copy_local", "pcc_gc_load_borrowed_ptr",
    }, model.ns)
    assert model.ns["pcc_gc_root_copy_local"](destination, source, 1) == 0
    assert model.load(source, 0) == model.load(destination, 0) == 2000
    assert model.references == {1000: 1, 2000: 2}
    assert model.active() == 0


def test_private_and_shared_sources_select_distinct_copy_protocols(tmp_path):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "root_sources.py"
    output = tmp_path / "root_sources.ll"
    source.write_text('''shared = [1]
def local(value, key, consume):
    return consume(value[key])
def global_source(key, consume):
    return consume(shared[key])
''')
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   emit_llvm_only=True, ir_scaffold_mode="on")
    text = output.read_text()
    local = re.search(r"^define [^\n]*@user_root_sources_local\([^\n]*\{.*?^}", text, re.M | re.S)
    shared = re.search(r"^define [^\n]*@user_root_sources_global_source\([^\n]*\{.*?^}", text, re.M | re.S)
    assert local is not None and shared is not None
    assert "@pcc_gc_root_copy_local(" in local.group()
    assert "@pcc_gc_root_copy_borrowed_lease(" not in local.group()
    assert "@pcc_gc_root_copy_lease(" in shared.group()
    # The actual raw-address consumer still protects its operands.
    assert "@pcc_gc_foreign_lease_acquire(" in local.group()


def test_private_copy_native_owners_and_mode_transitions(
    tmp_path, threaded_pcc_runtime_archive,
):
    binary = tmp_path / "private_root_copy"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/private_root_copy.c", binary,
        runtime_archive=Path(threaded_pcc_runtime_archive),
    )
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="0"))
    assert (run.returncode, run.stdout, run.stderr) == (0, "PRIVATE_ROOT_COPY_OK\n", "")


PROGRAM = '''import gc
import weakref
events = []
shared = None
class Item:
    def __del__(self):
        events.append("drop")
        gc.collect()
class Key:
    def __index__(self):
        global shared
        shared = None
        gc.collect()
        return 0
class BadKey:
    def __index__(self):
        gc.collect()
        raise ValueError("index")
def identity(value):
    return value
def at(value, key, consume):
    return consume(value[key])
def at_global(key, consume):
    return consume(shared[key])
def main():
    global shared
    item = Item()
    reference = weakref.ref(item)
    value = [item]
    del item
    assert at(value, Key(), identity) is reference()
    shared = value
    assert at_global(Key(), identity) is reference()
    try:
        at(value, BadKey(), identity)
    except ValueError as error:
        assert str(error) == "index"
    else:
        raise AssertionError("missing exception")
    assert reference() is value[0]
    del value
    gc.collect()
    assert reference() is None
    assert events == ["drop"]
    print("PRIVATE_ROOT_CALLBACKS_OK")
main()
'''


def test_private_copy_native_callbacks_exceptions_and_finalizers(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / "private_copy_callbacks.py"
    binary = tmp_path / "private_copy_callbacks"
    source.write_text(PROGRAM)
    python_program_compiler(str(source), str(binary), backend="self",
                            libpython_mode="off", ir_scaffold_mode="on",
                            runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        run = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert (run.returncode, run.stdout, run.stderr) == (
            0, "PRIVATE_ROOT_CALLBACKS_OK\n", "",
        ), backend


BORROWED_MOVEMENT_IR = r'''
@private_copy_borrowed_map = internal constant i32 -1
declare i64 @pcc_gc_root_copy_local(ptr, ptr, i64)

define i64 @test_private_copy_borrowed_movement() {
entry:
  %roots = alloca [3 x ptr]
  %owner = getelementptr [3 x ptr], ptr %roots, i64 0, i64 0
  %destination = getelementptr [3 x ptr], ptr %roots, i64 0, i64 1
  %unused = getelementptr [3 x ptr], ptr %roots, i64 0, i64 2
  %borrowed = alloca ptr
  store ptr null, ptr %owner
  store ptr null, ptr %destination
  store ptr null, ptr %unused
  store ptr null, ptr %borrowed
  call void @pcc_gc_frame_enter_lifo(ptr @handoff_map, ptr %roots)
  call void @pcc_gc_frame_enter_lifo(ptr @private_copy_borrowed_map, ptr %borrowed)
  %backend = call i64 @pcc_gc_backend()
  call void @handoff_new_young(ptr %owner, i64 %backend)
  %old = load ptr, ptr %owner
  %old_bits = ptrtoint ptr %old to i64
  store ptr %old, ptr %borrowed
  %moved = call ptr @lease_try_move(ptr %owner, i64 %backend)
  %moved_bits = ptrtoint ptr %moved to i64
  %nonnull = icmp ne ptr %moved, null
  %different = icmp ne i64 %moved_bits, %old_bits
  %did_move = and i1 %nonnull, %different
  call void @handoff_require(i1 %did_move)
  %healed_owner = call ptr @pcc_gc_load_ptr(ptr null, ptr %owner)
  %owner_ok = icmp eq ptr %healed_owner, %moved
  call void @handoff_require(i1 %owner_ok)
  ; The collector may eagerly rewrite this registered borrowed root. The
  ; production-body model separately forces the lazy forwarding branch.
  %refs_before = load i64, ptr %healed_owner
  %leases_before = call i64 @handoff_active_count()
  %status = call i64 @pcc_gc_root_copy_local(ptr %destination, ptr %borrowed, i64 1)
  %success = icmp eq i64 %status, 0
  call void @handoff_require(i1 %success)
  %alias_after = load ptr, ptr %borrowed
  %copied = load ptr, ptr %destination
  %alias_ok = icmp eq ptr %alias_after, %healed_owner
  %copy_ok = icmp eq ptr %copied, %healed_owner
  %pointers_ok = and i1 %alias_ok, %copy_ok
  call void @handoff_require(i1 %pointers_ok)
  %refs_after = load i64, ptr %copied
  %expected_refs = add i64 %refs_before, 1
  %refs_ok = icmp eq i64 %refs_after, %expected_refs
  call void @handoff_require(i1 %refs_ok)
  %leases_after = call i64 @handoff_active_count()
  %no_lease = icmp eq i64 %leases_before, %leases_after
  call void @handoff_require(i1 %no_lease)
  store ptr null, ptr %borrowed
  call void @pcc_gc_frame_leave_lifo(ptr %borrowed)
  call void @pcc_gc_store_root(ptr %destination, ptr null)
  call void @pcc_gc_store_root(ptr %owner, ptr null)
  call void @pcc_gc_frame_leave_lifo(ptr %roots)
  ret i64 0
}
'''


def test_private_copy_native_borrowed_forwarding_gc3_gc4(
    tmp_path, threaded_pcc_runtime_archive,
):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_threaded_root_slot_handoff import HANDOFF_IR

    target = host_target_triple()
    helper = tmp_path / "borrowed_movement.o"
    helper.write_bytes(emit_owned_object(
        'target triple = "' + target + '"\n'
        '@__pcc_owned_gc_short_paths_v1 = internal constant i8 1\n'
        + HANDOFF_IR + BORROWED_MOVEMENT_IR,
        target,
    ))
    source = tmp_path / "borrowed_movement.py"
    source.write_text('''from pcc.extern import extern, c_int64
probe = extern("test_private_copy_borrowed_movement", (), c_int64)
def main():
    assert probe() == 0
    print("PRIVATE_COPY_MOVEMENT_OK")
main()
''')
    binary = tmp_path / "borrowed_movement"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(threaded_pcc_runtime_archive),
                   object_inputs=(str(helper),))
    for backend in (3, 4):
        run = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                     PCC_GC_MINOR_ALLOC_MAX="4096"),
        )
        assert (run.returncode, run.stdout, run.stderr) == (
            0, "PRIVATE_COPY_MOVEMENT_OK\n", "",
        ), backend
