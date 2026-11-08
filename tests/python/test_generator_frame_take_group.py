"""Rooted frame batches: source/IR, production-plan model, and native controls.

The model controls relocation at potential stop boundaries; it is not a
collector simulator or proof of a real pending-stop/native handshake.
Native admitted return=1 and reference semantics are separate evidence from
emitted call presence. No timing, stack-size or throughput assertion is made.
"""
import os
from pathlib import Path
import sys

import pytest

from pcc.ir.compat import ir
from tests.python.test_generator_frame_take_protocol import _plan_code
from tests.python.test_generator_moving_frame_take import _function, _native, _run
from tests.python.test_generator_operand_root_groups import (
    Host, I64, PTR, _parsed_function, body_text, call_count,
    source_generator_group_ir,
)
from tests.python.test_runtime_getter_roots import _GetterMemory, _Object, _Slots, abi
from tests.python.root_slot_contract import RootSlotContract


def test_group_entry_rejects_other_policies_before_any_native_frame_or_poll():
    _, wrapper = _function("py_list_native_entry.py", "py_gen_frame_take_group")
    setup = wrapper.index("pcc_thread_no_park_enter()")
    for gate in ("pcc_gc_config_initialized", "pcc_gc_backend_selected",
                 "pcc_gc_backend4_no_refcounts", "pcc_log_init_state",
                 "pcc_diagnostics_runtime_log_fast_state", "pcc_gc_refcount_provenance_probe"):
        assert wrapper.index(gate) < setup
    assert "pcc_gc_backend()" not in wrapper
    assert setup < wrapper.index("store_ptr(inputs, 0, lst)")
    assert wrapper.index("pcc_gc_native_root_bind_range(inputs, -1)") < wrapper.index("pcc_thread_no_park_exit()")
    assert wrapper.index("pcc_thread_no_park_exit()") < wrapper.index("taken: int = pcc_list_body_")
    assert wrapper.index("pcc_gc_native_frame_leave(frame)") < wrapper.rindex("return taken")
    _, body = _function("py_list.py", "_native_body_py_gen_frame_take_group")
    lock, unlock = body.index("pcc_py_gc_minor_graph_lock()"), body.index("pcc_py_gc_minor_graph_unlock()")
    assert lock < body.index("pcc_gc_backend_selected")
    assert body.index("while offset < count:") < body.index("if valid != 0:")
    assert body.index("if valid != 0:") < body.index("pcc_gc_store_ptr_plan_init")
    assert body.index("store_ptr(outputs,") < body.index("pcc_gc_store_ptr_plan_commit_locked") < unlock
    assert unlock < body.index("pcc_gc_retain_plan_finish") < body.index("pcc_gc_store_ptr_plan_finish")
    assert "return " not in body[lock:unlock]
    assert "pcc_gc_pin(" not in body and "pcc_gc_native_root_unbind" not in body


def assert_group_restore_contract(blocks, globals_, start="gen.dispatch", target="gen.select", frame_offset=8,
                                  *, promoted_owners=False):
    """Every success/fallback path restores the same physical cells in order.

    Raw emitter checks stay memory-exact. Only the actual optimized consumer
    opts into the separately proved SSA ownership representation.
    """
    roots = RootSlotContract(blocks, globals_)
    by_name = {block.name: block for block in blocks}
    expected, batches, flags = {}, {}, {}
    promoted = None
    if promoted_owners:
        from tests.python.promoted_restore_contract import PromotedRestoreOwners
        promoted = PromotedRestoreOwners(roots)
    authoritative = None
    for block, position, ins in roots.rows:
        if roots.called(ins, "py_gen_frame_take") and ins.data[0].startswith("gen.operand.restore"):
            index = int(roots.args(ins)[1])
            following = block.instructions[position + 1]
            assert following.kind == "store" and following.data[1] == ins.data[0]
            output = roots.slot(following.data[3])
            roots.require_owning(output)
            assert index not in expected
            expected[index] = output
            following_flags = block.instructions[position + 2:position + 3]
            flag = following_flags[0] if following_flags else None
            if flag is not None and flag.kind == "store" and flag.data[0].describe() == "i1":
                assert flag.data[1] == "1"
                flags[index] = roots.slot(flag.data[3])
            else:
                assert promoted is not None, "each scalar restore needs its immediate ownership store"
                flags[index] = None
        if roots.called(ins, "py_gen_frame_take_group"):
            receiver, first, count, output = roots.args(ins)
            first, count = int(first), int(count)
            assert 2 <= count <= 16 and block.name not in batches
            base, offset = roots.slot(output)
            run = tuple((first + cell, (base, offset + cell * 8)) for cell in range(count))
            for _, cell in run:
                roots.require_owning(cell)
            batches[block.name] = run
            # Receiver is a fresh load from the authoritative borrowed root.
            load = next(row for row in block.instructions[:position]
                        if row.kind == "load" and row.data[0] == receiver)
            receiver_root = roots.slot(load.data[3])
            assert receiver_root[1] == frame_offset
            if authoritative is None:
                authoritative = receiver_root
            assert receiver_root == authoritative
            term = block.terminator
            check = next(row for row in block.instructions
                         if row.kind == "icmp" and row.data[1] == term.data[0])
            assert check.data[0] == "ne" and check.data[3:] == (ins.data[0], "0")
    for run in batches.values():
        assert all(expected[index] == output for index, output in run)
    grouped = {index for run in batches.values() for index, _ in run}
    assert all(flag is not None or index in grouped for index, flag in flags.items()), (
        "only a grouped restore can use a promoted join flag"
    )
    pending, seen, results = [(start, ())], set(), set()
    while pending:
        name, history = pending.pop()
        if (name, history) in seen:
            continue
        seen.add((name, history))
        if name == target:
            results.add(history)
            continue
        block = by_name[name]
        for position, ins in enumerate(block.instructions):
            if roots.called(ins, "py_gen_frame_take") and ins.data[0].startswith("gen.operand.restore"):
                index = int(roots.args(ins)[1])
                history += ((index, expected[index]),)
                receiver = roots.args(ins)[0]
                reload = block.instructions[position - 1]
                assert reload.kind == "load" and reload.data[0] == receiver, "each fallback must freshly reload"
                receiver_root = roots.slot(reload.data[3])
                assert receiver_root[1] == frame_offset
                if authoritative is None:
                    authoritative = receiver_root
                assert receiver_root == authoritative
        term = block.terminator
        if name in batches:
            assert term.kind == "br_cond"
            fast, slow = term.data[1:]
            assert fast.startswith("gen.operand.group.owned") and slow.startswith("gen.operand.scalar")
            fast_stores = by_name[fast].instructions
            memory_flags = [flags[index] for index, _ in batches[name] if flags[index] is not None]
            assert len(fast_stores) == len(memory_flags)
            assert all(row.kind == "store" and row.data[0].describe() == "i1"
                       and row.data[1] == "1" for row in fast_stores)
            assert [roots.slot(row.data[3]) for row in fast_stores] == memory_flags
            if promoted is not None:
                promoted.check_join(fast, slow, [output for index, output in batches[name]
                                                 if flags[index] is None])
            pending.extend(((fast, history + batches[name]), (slow, history)))
        else:
            assert term.kind == "br", "restore chain must not escape or loop before selection"
            pending.append((term.data[0], history))
    assert results == {tuple(sorted(expected.items()))}
    assert by_name[target].terminator.kind == "switch"
    if promoted is not None:
        promoted.assert_phi_predecessors()
    roots.assert_frame_exits()
    return batches


@pytest.mark.parametrize("count", (0, 1, 2, 15, 16, 17, 31, 32))
def test_actual_operand_emitters_use_registered_groups_and_exact_boundaries(count):
    from pcc.frontends.python.codegen import generator_lowering as generator
    host = Host(deferred_restores=True)
    host.roots(count)
    assert all(group["registered"] for group in host.context["operand_root_groups"])
    assert not host.restore._instrs, "do not mutate projected counts during body discovery"
    host.finish()
    generator.emit_generator_operand_restores(host)
    module, blocks = _parsed_function(str(host.module), "resume")
    batches = assert_group_restore_contract(blocks, module.globals_, "restore", "dispatch", frame_offset=0)
    wanted = [min(16, count - start) for start in range(0, count, 16)]
    assert [len(run) for run in batches.values()] == [size for size in wanted if size > 1]
    for block in blocks:
        if block.name.startswith("gen.operand.scalar"):
            for position, ins in enumerate(block.instructions):
                if RootSlotContract.called(ins, "py_gen_frame_take"):
                    owner, flag = block.instructions[position + 1:position + 3]
                    assert owner.kind == flag.kind == "store" and flag.data[1] == "1"


def test_import_nongroup_cell_splits_batch_without_reordering_or_losing_cleanup():
    from pcc.frontends.python.codegen import generator_lowering as generator
    host = Host(with_error=True, deferred_restores=True)
    host.roots(2)
    slot = host._alloca_in_entry(PTR, "imported.module", init_null=True)
    from pcc.frontends.python.py_ast import DynType
    host.env["imported.module"] = (slot, PTR, DynType(name="dyn"))
    host._owned_local_names.add("imported.module")
    host._owned_local_has_value.add("imported.module")
    host._ensure_owned_local_gc_root("imported.module", slot, PTR)
    flag = host._ensure_owned_local_flag("imported.module", slot)
    generator.register_generator_operand_root(host, "imported.module", slot, flag)
    host.roots(2)
    host.finish()
    generator.emit_generator_operand_restores(host)
    module, blocks = _parsed_function(str(host.module), "resume")
    batches = assert_group_restore_contract(blocks, module.globals_, "restore", "dispatch", frame_offset=0)
    assert [tuple(index for index, _ in run) for run in batches.values()] == [(0, 1), (3, 4)]
    assert call_count(body_text(host._fn_err_exit_blocks["resume"]), "pcc_gc_store_root") == 5


def test_frontend_emitter_probe_covers_registered_consumer_and_all_exits(source_generator_group_ir):
    _, module, blocks = source_generator_group_ir
    batches = assert_group_restore_contract(blocks, module.globals_)
    assert len(batches) >= 2 and any(len(run) == 16 for run in batches.values())


class BatchMemory(_GetterMemory):
    def __init__(self, count=16, backend=4, phase="none", prior_pin=False):
        super().__init__(backend=backend, phase=phase, prior_pin=prior_pin)
        self.count, self.events = count, []
        self.values = [self.item] + [self.make(abi.PY_TYPE_LIST) for _ in range(count - 1)]
        self.items = _Slots(count * 8)
        self.items.fields.update({index * 8: value for index, value in enumerate(self.values)})
        self.payloads.append(self.items)
        self.lst.fields.update({abi.PYLISTOBJECT_LENGTH_OFFSET: count,
                                abi.PYLISTOBJECT_CAPACITY_OFFSET: count,
                                abi.PYLISTOBJECT_ITEMS_OFFSET: self.items})
        self.outputs, self.owner = _Slots(count * 8), _Slots(8)
        self.owner.fields[0] = self.lst
        for cell in range(count):
            self.register(self.add(self.outputs, cell * 8))
        self.register(self.owner)
        self.caller_handles = set(self.handles)
        self.global_cell("pcc_log_init_state").fields[0] = 2
        self.global_cell("pcc_gc_backend4_no_refcounts").fields[0] = 1
        def forbidden(*args):
            raise AssertionError("count-free NULL clear entered a callback/barrier")
        def zero(plan, value, size):
            for offset in range(0, size, 4):
                self.write(plan, offset, value)
        ns = self.namespace
        ns.update(i64=int, store_i32=self.write, memset=zero,
                  atomic_load_i32=lambda slot, offset, order: self.read(slot, offset),
                  pcc_gc_note_store=lambda: self.events.append("store-note"),
                  _gc_forwarding_population=lambda: int(bool(self.forward)),
                  _gc_relocation_candidate=lambda value: value is not None,
                  pcc_gc_note_relocation_read=forbidden,
                  pcc_gc_note_slot_write_barrier=forbidden,
                  pcc_gc_backend4_note_heap_store_locked=forbidden,
                  _py_decref_prepare=forbidden, _py_decref_finish=forbidden,
                  pcc_diagnostics_runtime_log_event_code=forbidden)
        exec(_plan_code(), ns)
        commit, retain_finish, store_finish = (ns[name] for name in (
            "pcc_gc_store_ptr_plan_commit_locked", "pcc_gc_retain_plan_finish", "pcc_gc_store_ptr_plan_finish"))
        def checked_commit(plan, owner, slot, value):
            assert self.depth > 0 and owner is self.lst and value is None
            old = self.read(*self.pointer(slot))
            assert any(self.read(*self.pointer(root)) is old for root in self.handles.values())
            self.events.append("commit")
            return commit(plan, owner, slot, value)
        def checked_finish(plan, kind, function):
            assert self.depth == 0 and set(self.handles) >= self.caller_handles
            self.gc(kind)
            for offset, original in enumerate(self.values):
                current = self.outputs.fields[offset * 8]
                assert current is self.resolve(original)
                if isinstance(current, _Object):
                    assert current.alive and current.references == 1
            function(plan)
            self.events.append(kind)
        body = ns["_native_body_py_gen_frame_take_group"]
        def body_entry(*args):
            self.gc("body_entry")  # Models its compiler-inserted entry poll.
            return body(*args)
        ns.update(pcc_list_body_py_gen_frame_take_group=body_entry,
                  pcc_gc_store_ptr_plan_commit_locked=checked_commit,
                  pcc_gc_retain_plan_finish=lambda p: checked_finish(p, "retain_finish", retain_finish),
                  pcc_gc_store_ptr_plan_finish=lambda p: checked_finish(p, "store_finish", store_finish))

    def root_read(self, owner, slot):
        # The real barrier resolves a forwarding chain, including a stale
        # source copied into the borrowed input immediately before entry.
        self.gc("read_barrier")
        value = self.resolve(self.read(*self.pointer(slot)))
        self.write(*self.pointer(slot), value)
        return value

    def run(self, start=0, count=None, frame=None):
        return self.namespace["py_gen_frame_take_group"](
            self.lst if frame is None else frame, start,
            self.count if count is None else count, self.outputs)

    def assert_clean(self):
        assert set(self.handles) == self.caller_handles
        assert not self.native_frames and not self.native_ranges
        assert self.depth == self.no_park_depth == self.pins == 0


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("count", (2, 15, 16))
@pytest.mark.parametrize("phase", ("no_park_exit", "body_entry", "graph_lock", "graph_unlock", "retain_finish", "store_finish"))
@pytest.mark.parametrize("prior_pin", (False, True))
def test_real_plans_keep_every_caller_root_through_move_stop_and_finishes(backend, count, phase, prior_pin):
    memory = BatchMemory(count, backend, phase, prior_pin)
    memory.global_cell("pcc_gc_mark_active").fields[0] = 1
    assert memory.run() == 1
    assert memory.events.count("commit") == memory.events.count("store-note") == count
    assert memory.events[-count * 2:] == ["retain_finish", "store_finish"] * count
    assert all(memory.items.fields[index * 8] is None for index in range(count))
    assert bool(memory.outputs.fields[0].flags & abi.PY_FLAG_GC_PINNED) == prior_pin
    assert memory.moves > 0
    memory.assert_clean()


def test_forwarding_chain_before_entry_is_healed_before_owner_or_value_dereference():
    memory = BatchMemory(2, phase="before_entry")
    old_frame, old_value = memory.lst, memory.item
    memory.gc("before_entry")
    middle = memory.resolve(old_value)
    memory.gc("before_entry")
    assert old_value in memory.forward and middle in memory.forward
    memory.phase = "none"
    # Retain a real old field address as input to the value barrier too.
    memory.items.fields[0] = old_value
    assert memory.run(frame=old_frame) == 1
    assert memory.outputs.fields[0] is memory.resolve(old_value)
    memory.assert_clean()


@pytest.mark.parametrize("reject", ("foreign", "cext", "unmanaged", "nonempty", "range", "capacity", "deallocating", "inline"))
def test_last_cell_or_shape_rejection_consumes_nothing_and_finishes_native_frame(reject):
    memory = BatchMemory(16, phase="body_entry")
    if reject in ("foreign", "cext"):
        memory.values[-1].tag = abi.PY_TYPE_CPY_HANDLE if reject == "foreign" else abi.PY_TYPE_CEXT_TAG_BASE
    elif reject == "unmanaged":
        memory.items.fields[15 * 8] = object()
    elif reject == "nonempty":
        memory.outputs.fields[15 * 8] = memory.item
    elif reject == "capacity":
        memory.lst.fields[abi.PYLISTOBJECT_CAPACITY_OFFSET] = 15
    elif reject == "deallocating":
        memory.lst.flags |= abi.PY_FLAG_GC_DEALLOCATING
    elif reject == "inline":
        memory.lst.tag = abi.PY_TYPE_GEN
    before = dict(memory.items.fields)
    assert memory.run(start=1 if reject == "range" else 0) == 0
    assert not memory.events
    assert all(memory.items.fields[offset] is memory.resolve(value) for offset, value in before.items())
    assert all(value is None for offset, value in memory.outputs.fields.items() if offset != 15 * 8 or reject != "nonempty")
    assert memory.moves > 0  # Failed body entry really crossed a moving poll.
    memory.assert_clean()


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("gate", ("backend", "config", "no_rc", "log_init", "logging", "provenance", "one", "zero", "over", "negative"))
def test_cheap_fallback_does_not_enter_frame_or_touch_roots(backend, gate):
    memory = BatchMemory(16, backend)
    gates = {"config": ("pcc_gc_config_initialized", 0), "no_rc": ("pcc_gc_backend4_no_refcounts", 0),
             "log_init": ("pcc_log_init_state", 1), "logging": ("pcc_diagnostics_runtime_log_fast_state", 1),
             "provenance": ("pcc_gc_refcount_provenance_probe", 1)}
    if gate == "backend" and backend in (3, 4):
        return
    if gate in gates:
        name, value = gates[gate]
        memory.global_cell(name).fields[0] = value
    count = {"one": 1, "zero": 0, "over": 17}.get(gate, 16)
    assert memory.run(start=-1 if gate == "negative" else 0, count=count) == 0
    assert not memory.events and not memory.moves
    assert all(value is None for value in memory.outputs.fields.values())
    assert list(memory.items.fields.values()) == memory.values
    memory.assert_clean()


def test_alias_growth_is_observed_after_acquisition_and_serialized_through_clear():
    memory = BatchMemory(16)
    old_items = memory.items
    def lock_after_growth():
        assert memory.depth == 0
        grown = _Slots(32 * 8)
        grown.fields.update(memory.items.fields)
        memory.items = grown
        memory.payloads.append(grown)
        memory.lst.fields[abi.PYLISTOBJECT_ITEMS_OFFSET] = grown
        memory.lst.fields[abi.PYLISTOBJECT_CAPACITY_OFFSET] = 32
        memory.lock()
    memory.namespace["pcc_py_gc_minor_graph_lock"] = lock_after_growth
    assert memory.run() == 1
    assert all(memory.items.fields[index * 8] is None for index in range(16))
    assert list(old_items.fields.values()) == memory.values
    memory.assert_clean()


NATIVE_SHAPES = r'''
#include "py_internal.h"
#include <stdio.h>
extern int32_t pcc_gc_backend4_no_refcounts;
extern int32_t pcc_log_init_state;
extern int32_t pcc_diagnostics_runtime_log_fast_state;
extern int32_t pcc_gc_refcount_provenance_probe;
extern int32_t pcc_gc_mark_active;
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern int64_t pcc_gc_metric_store_count(void);
extern int64_t pcc_py_gc_minor_graph_lock_depth(void);
static int foreign_token;
static void foreign_release(void *p) { (void)p; }
int main(void) {
    int backend = pcc_gc_backend();
    py_gc_disable();
    PyObject *roots[3] = {0, 0, 0};
    PyObject *outputs[18] = {0};
    int32_t root_map[1] = {3}, output_map[1] = {18};
    pcc_gc_frame_enter(root_map, roots);
    pcc_gc_frame_enter(output_map, outputs);
    /* This is the default generated constructor, which uses list frames
       on GC3/4 and inline storage only on GC0. No special test admission. */
    roots[0] = backend >= 3 ? py_gen_inline_frame_new(18) : py_gen_frame_new(18);
    roots[1] = py_list_new(0);
    py_cpy_handle_set_release_fn(foreign_release);
    roots[2] = py_cpy_handle_new(&foreign_token);
    if (!roots[0] || !roots[1] || !roots[2]) return 1;
    int admitted = (backend == 3 || backend == 4) && pcc_gc_backend4_no_refcounts
        && pcc_log_init_state == 2 && !pcc_diagnostics_runtime_log_fast_state
        && !pcc_gc_refcount_provenance_probe;
    if ((backend == 3 || backend == 4) && !pcc_gc_refcount_provenance_probe && !admitted) return 13;
    int counts[7] = {0, 1, 2, 15, 16, 17, 18};
    for (int test = 0; test < 7; test++) {
        for (int i = 0; i < 18; i++) {
            pcc_gc_store_root(&outputs[i], 0);
            py_gen_frame_set(roots[0], i, roots[1]);
        }
        int count = counts[test];
        int result = py_gen_frame_take_group(roots[0], 0, count, outputs);
        if (result != (admitted && count >= 2 && count <= 16)) return 2;
        if (!result) {
            for (int i = 0; i < 18; i++) {
                if (outputs[i] || ((PyListObject *)roots[0])->items[i] != roots[1]) return 3;
            }
            /* Compiler fallback reloads the rooted frame before each scalar. */
            for (int i = 0; i < count; i++) outputs[i] = py_gen_frame_take(roots[0], i);
        }
        for (int i = 0; i < count; i++) {
            if (outputs[i] != roots[1] || ((PyListObject *)roots[0])->items[i]) return 4;
        }
    }
    for (int i = 0; i < 18; i++) {
        pcc_gc_store_root(&outputs[i], 0);
        py_gen_frame_set(roots[0], i, roots[1]);
    }
    if (py_gen_frame_take_group(roots[0], -1, 2, outputs)
            || py_gen_frame_take_group(roots[0], 17, 2, outputs)
            || py_gen_frame_take_group(roots[0], INT64_MAX, 2, outputs)) return 5;
    for (int i = 0; i < 18; i++) {
        if (outputs[i] || ((PyListObject *)roots[0])->items[i] != roots[1]) return 6;
    }
    py_gen_frame_set(roots[0], 15, roots[2]);
    if (py_gen_frame_take_group(roots[0], 0, 16, outputs)) return 7;
    for (int i = 0; i < 16; i++) {
        if (outputs[i] || ((PyListObject *)roots[0])->items[i] != roots[i == 15 ? 2 : 1]) return 8;
        outputs[i] = py_gen_frame_take(roots[0], i);
    }
    if (py_cpy_handle_get(outputs[15]) != &foreign_token) return 9;
    for (int i = 0; i < 16; i++) {
        pcc_gc_store_root(&outputs[i], 0);
        py_gen_frame_set(roots[0], i, roots[1]);
    }
    if (admitted) {
        pcc_gc_pin(roots[1]);
        pcc_py_gc_minor_graph_lock();
        int64_t depth = pcc_py_gc_minor_graph_lock_depth();
        int64_t gray = pcc_gc_gray_count_load_acquire();
        int64_t stores = pcc_gc_metric_store_count();
        int64_t refs = py_header(roots[1])->refcount;
        __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
        if (py_gen_frame_take_group(roots[0], 0, 16, outputs) != 1) return 10;
        if (pcc_py_gc_minor_graph_lock_depth() != depth
                || pcc_gc_gray_count_load_acquire() != gray
                || pcc_gc_metric_store_count() != stores + 16
                || py_header(roots[1])->refcount != refs) return 11;
        for (int i = 0; i < 16; i++) {
            if (outputs[i] != roots[1] || !(py_header(outputs[i])->flags & PY_FLAG_GC_PINNED)) return 12;
        }
        __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
        pcc_py_gc_minor_graph_unlock();
        pcc_gc_unpin(roots[1]);
    }
    for (int i = 0; i < 18; i++) pcc_gc_store_root(&outputs[i], 0);
    for (int i = 0; i < 3; i++) pcc_gc_store_root(&roots[i], 0);
    pcc_gc_frame_leave(outputs);
    pcc_gc_frame_leave(roots);
    puts("FRAME_GROUP_SHAPES_OK");
    return 0;
}
'''


NATIVE_RELOCATION = r'''
#include "py_internal.h"
#include <stdio.h>
extern PyObject *pcc_gc_generational_oldify_copy(PyObject *);
int main(void) {
    int backend = pcc_gc_backend();
    if (backend != 3 && backend != 4) return 1;
    py_gc_disable();
    PyObject *roots[3] = {0}, *outputs[2] = {0};
    int32_t root_map[1] = {3}, output_map[1] = {2};
    pcc_gc_frame_enter(root_map, roots);
    pcc_gc_frame_enter(output_map, outputs);
    roots[0] = py_gen_frame_new(2);
    roots[1] = backend == 3 ? py_bytes_new("one", 3) : py_list_new(0);
    roots[2] = backend == 3 ? py_bytes_new("two", 3) : py_list_new(0);
    if (!roots[0] || !roots[1] || !roots[2]) return 2;
    py_gen_frame_set(roots[0], 0, roots[1]);
    py_gen_frame_set(roots[0], 1, roots[2]);
    for (int i = 0; i < 3; i++) pcc_gc_unpin(roots[i]);
    PyObject *source = roots[0], *first = roots[1], *second = roots[2];
    PyObject *moved_owner = source, *moved_first, *moved_second;
    if (backend == 3) {
        pcc_py_gc_minor_graph_lock();
        moved_first = pcc_gc_generational_oldify_copy(first);
        moved_second = pcc_gc_generational_oldify_copy(second);
        pcc_py_gc_minor_graph_unlock();
    } else {
        pcc_gc_reset_relocation_set();
        if (pcc_gc_select_relocation_set(128) <= 0) return 3;
        if (!pcc_gc_relocation_set_contains(source)
                || !pcc_gc_relocation_set_contains(first)
                || !pcc_gc_relocation_set_contains(second)) return 4;
        moved_first = pcc_gc_relocate_copy(first, sizeof(PyListObject));
        moved_second = pcc_gc_relocate_copy(second, sizeof(PyListObject));
        moved_owner = pcc_gc_relocate_copy(source, sizeof(PyListObject));
        if (!moved_owner || moved_owner == source) return 5;
    }
    if (!moved_first || !moved_second || moved_first == first || moved_second == second) return 6;
    if (py_gen_frame_take_group(source, 0, 2, outputs) != 1) return 7;
    if (outputs[0] != moved_first || outputs[1] != moved_second) return 8;
    if (pcc_gc_load_ptr(0, &roots[0]) != moved_owner) return 9;
    for (int i = 0; i < 2; i++) {
        if (((PyListObject *)roots[0])->items[i] || (py_header(outputs[i])->flags & PY_FLAG_GC_PINNED)) return 10;
        pcc_gc_store_root(&outputs[i], 0);
    }
    for (int i = 0; i < 3; i++) pcc_gc_store_root(&roots[i], 0);
    pcc_gc_frame_leave(outputs);
    pcc_gc_frame_leave(roots);
    puts("FRAME_GROUP_RELOCATION_OK");
    return 0;
}
'''


@pytest.mark.integration
def test_native_group_admission_boundaries_foreign_fallback_and_gray_state(tmp_path, pcc_runtime_archive):
    executable = _native(tmp_path, pcc_runtime_archive, "frame_group_shapes", NATIVE_SHAPES)
    for backend in range(5):
        result = _run(executable, backend)
        assert (result.returncode, result.stdout, result.stderr) == (0, "FRAME_GROUP_SHAPES_OK\n", ""), backend
    for backend in (3, 4):
        result = _run(executable, backend, PCC_GC_REFCOUNT_PROVENANCE_PROBE="1")
        assert (result.returncode, result.stdout, result.stderr) == (0, "FRAME_GROUP_SHAPES_OK\n", ""), backend


@pytest.mark.integration
def test_native_group_after_real_gc3_value_and_gc4_owner_value_relocation(tmp_path, pcc_runtime_archive):
    executable = _native(tmp_path, pcc_runtime_archive, "frame_group_relocation", NATIVE_RELOCATION)
    for backend in (3, 4):
        result = _run(executable, backend, PCC_GC_MINOR_ALLOC_MAX="4096")
        assert (result.returncode, result.stdout, result.stderr) == (0, "FRAME_GROUP_RELOCATION_OK\n", ""), backend


@pytest.mark.integration
def test_no_log_generator_group_finalization_and_cleanup_matches_reference(tmp_path, monkeypatch, pcc_runtime_archive):
    from hashlib import sha256
    from pcc.frontends.python.pipeline import compile_python
    from tests.python.process_timeout import run_process_group_timeout
    from tests.python.test_generator_operand_root_groups import _parsed_function
    from tests.python.test_generator_root_groups_native import _SOURCE

    # Use the existing repository fixture, not a relocated/simplified program.
    # Its explicit distinct suspension sites cover more than two root groups.
    monkeypatch.setenv("PCC_INLINE_GENERATOR_FRAMES", "1")
    monkeypatch.setenv("PCC_LOG", "")
    monkeypatch.setenv("PCC_GC_REFCOUNT_PROVENANCE_PROBE", "0")
    executable = tmp_path / "frame_group_finalizers"
    from pcc.frontends.python import pipeline
    original_link = pipeline._link_with_self_backend_ir_texts
    observed, saved_inputs = [], []
    def inspect_consumer(text, path):
        # Check the exact saved link input before any reference/native run.
        from pcc.backend.self_backend_parse import parse_self_backend_module
        from pcc.backend.self_backend_kernel import get_indexed_function_kernel
        module = parse_self_backend_module(text)
        for function in module.functions:
            if function.name.endswith("_grouped__gen_resume"):
                blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
                batches = assert_group_restore_contract(blocks, module.globals_, promoted_owners=True)
                assert any(len(run) == 16 for run in batches.values())
                observed.append(str(path))

    def save_consumer_inputs(texts):
        saved = []
        for index, text in enumerate(texts):
            if "grouped__gen_resume" not in text:
                continue
            path = tmp_path / ("group-link-input-" + str(index) + ".ll")
            data = text.encode("utf-8")
            path.write_bytes(data)
            saved.append((path, sha256(data).hexdigest()))
        return saved

    def inspect_saved_consumer(path, digest):
        data = path.read_bytes()
        assert sha256(data).hexdigest() == digest, "saved consumer link input changed"
        inspect_consumer(data.decode("utf-8"), path)

    def observe_link(texts, *args, **kwargs):
        # Keep only paths/digests across link, so consume_ir_texts can release
        # its original list without a test-local text or parsed-module owner.
        saved_inputs.extend(save_consumer_inputs(texts))
        return original_link(texts, *args, **kwargs)  # Same list/arguments, unchanged IR.
    monkeypatch.setattr(pipeline, "_link_with_self_backend_ir_texts", observe_link)
    compile_python(str(_SOURCE), str(executable), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    for path, digest in saved_inputs:
        inspect_saved_consumer(path, digest)
    assert observed, "native fixture did not link the new consumer"
    for case in ("normal", "early", "close", "exception", "weakref", "resurrection", "alias", "small"):
        reference = run_process_group_timeout([sys.executable, str(_SOURCE), case], timeout=20)
        assert reference.returncode == 0 and not reference.stderr
        for backend in range(5):
            env = dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_LOG="", PCC_GC_REFCOUNT_PROVENANCE_PROBE="0")
            result = run_process_group_timeout([str(executable), case], timeout=20, env=env)
            assert (result.returncode, result.stdout, result.stderr) == (
                0, reference.stdout, ""), (case, backend)


@pytest.mark.parametrize("empty", (False, True))
def test_accepted_null_tagged_and_managed_outputs_keep_exact_plan_lifecycles(empty):
    memory = BatchMemory(16, phase="store_finish")
    if empty:
        memory.values = [None] * 16
    else:
        memory.values[0], memory.values[1] = None, 85
    memory.items.fields.update({index * 8: value for index, value in enumerate(memory.values)})
    assert memory.run() == 1
    assert memory.events.count("commit") == memory.events.count("store-note") == 16
    assert memory.events[-32:] == ["retain_finish", "store_finish"] * 16
    assert all(memory.items.fields[index * 8] is None for index in range(16))
    memory.assert_clean()


@pytest.mark.parametrize("first_entry", ("0", "1"))
def test_real_generator_state_read_stays_before_restore_chain(monkeypatch, first_entry):
    from tests.python.test_slot_call_operand_roots import _emit
    monkeypatch.setenv("PCC_GENERATOR_FIRST_ENTRY_INIT", first_entry)
    text = _emit("def probe(value):\n    saved = value\n    yield slot_args_probe(saved, (yield 1))\n")
    module, blocks = _parsed_function(text, "user_slot_operand_probe__gen_resume")
    batches = assert_group_restore_contract(blocks, module.globals_)
    assert batches
    states = [(block.name, ins) for block in blocks for ins in block.instructions
              if RootSlotContract.called(ins, "py_gen_state")]
    assert len(states) == 1
    def successors(block):
        term = block.terminator
        if term.kind == "br":
            return tuple(term.data[:1])
        if term.kind == "br_cond":
            return tuple(term.data[1:])
        if term.kind == "switch":
            return (term.data[2],) + tuple(target for _, target in term.data[3])
        return ()
    by_name = {block.name: block for block in blocks}
    reached, pending = set(), [blocks[0].name]
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending.extend(successors(by_name[name]))
    predecessors = {name: set() for name in reached}
    for name in reached:
        for successor in successors(by_name[name]):
            predecessors[successor].add(name)
    dominators = {name: set(reached) for name in reached}
    dominators[blocks[0].name] = {blocks[0].name}
    changed = True
    while changed:
        changed = False
        for name in reached - {blocks[0].name}:
            common = set.intersection(*(dominators[pred] for pred in predecessors[name]))
            current = {name} | common
            if current != dominators[name]:
                dominators[name], changed = current, True
    if first_entry == "1":
        # The entry safepoint can split entry into continuation blocks.
        assert states[0][0] != "gen.dispatch" and states[0][0] in dominators["gen.dispatch"]
    else:
        assert states[0][0] == "gen.dispatch"
    assert states[0][0] in dominators["gen.select"]
    select = next(block for block in blocks if block.name == "gen.select")
    assert select.terminator.data[1] == states[0][1].data[0]


def test_admission_is_rechecked_after_a_parking_graph_acquisition():
    memory = BatchMemory(2, phase="graph_lock")
    def lock_then_reject():
        memory.lock()
        memory.global_cell("pcc_gc_refcount_provenance_probe").fields[0] = 1
    memory.namespace["pcc_py_gc_minor_graph_lock"] = lock_then_reject
    assert memory.run() == 0 and not memory.events
    assert memory.moves > 0
    assert all(value is None for value in memory.outputs.fields.values())
    assert all(memory.items.fields[i * 8] is memory.resolve(value) for i, value in enumerate(memory.values))
    memory.assert_clean()
