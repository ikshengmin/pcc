"""Exact field-body differential including byte-level scratch at unlock.

The intrinsic model does not establish native stack layout, moving-collector
coordination, stop admission or object survival. Integration compilation below
checks emitted IR only; it is not native execution or throughput evidence.
"""

import ast
import re
from functools import lru_cache
from itertools import product

import pytest

from pcc.ir.optimization.inline import SAFEPOINT_POLL_LOAD_ORDERING

from tests.python.test_gc4_public_direct_store import PublicStore
from tests.python.test_gc4_count_free_store_plans import SOURCE, Store


OLD_FIELD_SOURCE = '''
def pcc_gc_store_field_ptr(owner, slot, value) -> None:
    """Rebind a field other carriers may read or rebind (an instance field).

    An unlocked read-modify-write released one old value twice under two
    concurrent stores, and a reader's retain raced the release. Every
    collector swaps such a field inside one transaction and releases the
    displaced value after it; readers load and retain inside one. On GC0
    the transaction holds the owner's container lock, so carriers touching
    different objects no longer wait on the one graph lock; elsewhere it is
    a graph transaction. Private and runtime-owned slots keep
    pcc_gc_store_ptr.
    """
    if ptr_is_null(slot):
        return
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = pcc_gc_backend()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    plan = stack_alloc(128)
    pcc_gc_store_ptr_plan_init(plan, owner, backend)
    if _fields_lock_themselves() != 0:
        pcc_container_lock(owner)
        pcc_gc_store_ptr_plan_commit_locked(plan, owner, slot, value)
        pcc_container_unlock(owner)
    else:
        pcc_py_gc_minor_graph_lock()
        pcc_gc_store_ptr_plan_commit_locked(plan, owner, slot, value)
        pcc_py_gc_minor_graph_unlock()
    pcc_gc_store_ptr_plan_finish(plan)
'''


@lru_cache(maxsize=1)
def current_source():
    return SOURCE.read_text()


@lru_cache(maxsize=2)
def field_code(source):
    node = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef)
                and node.name == 'pcc_gc_store_field_ptr')
    node.decorator_list = []
    return compile(ast.Module(body=[node], type_ignores=[]), '<field-store>', 'exec')


class FieldStore(PublicStore):
    def __init__(self, *, field_source, container, **options):
        super().__init__(source=current_source(), **options)
        self.plan_bytes = bytearray(self.VALUE.to_bytes(8, 'little') * 16)
        self.unlock_images = []
        self.globals['pcc_gc_list_container_mode'] = container
        self.ns.update(
            load_i32=lambda p, o: self.typed_load(p, o, 4),
            load_i64=lambda p, o: self.typed_load(p, o, 8),
            load_ptr=lambda p, o: self.typed_load(p, o, 8),
            store_i32=lambda p, o, v: self.typed_store(p, o, v, 4),
            store_i64=lambda p, o, v: self.typed_store(p, o, v, 8),
            store_ptr=lambda p, o, v: self.typed_store(p, o, v, 8),
            _fields_lock_themselves=lambda: self.globals['pcc_gc_list_container_mode'],
            pcc_container_lock=self.container_lock,
            pcc_container_unlock=self.container_unlock,
        )
        exec(field_code(field_source), self.ns)

    def typed_load(self, pointer, offset, width):
        value = self.load(pointer, offset)
        if isinstance(pointer, int) and self.PLAN <= pointer + offset < self.PLAN + 128:
            start = pointer + offset - self.PLAN
            return int.from_bytes(self.plan_bytes[start:start + width], 'little')
        return value

    def typed_store(self, pointer, offset, value, width):
        self.store(pointer, offset, value)
        if self.PLAN <= pointer + offset < self.PLAN + 128:
            start = pointer + offset - self.PLAN
            self.plan_bytes[start:start + width] = value.to_bytes(width, 'little')

    def zero(self, pointer, value, size):
        super().zero(pointer, value, size)
        self.plan_bytes[:] = bytes(size)

    def allocate(self, size):
        self.ready = False
        return super().allocate(size)

    def lock(self):
        super().lock()
        self.ready = True

    def unlock(self):
        self.unlock_images.append(bytes(self.plan_bytes))
        super().unlock()

    def container_lock(self, owner):
        assert self.depth == 0
        self.depth = 1
        self.ready = True
        self.events.append(('container-lock', owner))

    def container_unlock(self, owner):
        assert self.depth == 1
        self.unlock_images.append(bytes(self.plan_bytes))
        self.depth = 0
        self.events.append(('container-unlock', owner))

    def snapshot(self):
        return (super().snapshot(), dict(self.words), bytes(self.plan_bytes),
                self.unlock_images, self.initialized)


def test_field_store_exact_differential_including_unlock_scratch():
    candidate = SOURCE.read_text()
    for backend, config, count_free, container, owner, slot, value, forwarded, marking, logging in product(
        range(5), (0, 1), (False, True), (0, 1), (0, Store.OWNER),
        (0, Store.SLOT), (0, 35, 2, 6, Store.VALUE, Store.OLD, Store.COPY, 0x100004000),
        (False, True), (False, True), (False, True),
    ):
        options = dict(backend=backend, config=config, count_free=count_free,
                       container=container, logging=logging,
                       forwarded=forwarded, marking=marking)
        before = FieldStore(field_source=OLD_FIELD_SOURCE, **options)
        after = FieldStore(field_source=candidate, **options)
        left = before.ns['pcc_gc_store_field_ptr'](owner, slot, value)
        right = after.ns['pcc_gc_store_field_ptr'](owner, slot, value)
        assert left == right and before.snapshot() == after.snapshot(), (
            options, owner, slot, value, before.snapshot(), after.snapshot())
        if slot and backend == 4 and count_free:
            assert len(after.unlock_images) == 1
            image = after.unlock_images[0]
            expected = after.COPY if forwarded and value == after.VALUE else value
            assert int.from_bytes(image[0:8], 'little') == expected
            assert image[8:112] == bytes(104)
            assert int.from_bytes(image[112:120], 'little') == 4
            assert int.from_bytes(image[120:128], 'little') == (3 << 32) | 1
            assert int.from_bytes(after.plan_bytes[120:128], 'little') == (7 << 32) | 1
            assert after.old_reads == 0


@pytest.mark.parametrize('container', (0, 1))
def test_count_free_field_reuse_clears_stale_scratch(container):
    options = dict(backend=4, config=1, count_free=True, container=container,
                   logging=True, forwarded=False, marking=True)
    before = FieldStore(field_source=OLD_FIELD_SOURCE, **options)
    after = FieldStore(field_source=SOURCE.read_text(), **options)
    for value in (Store.VALUE, 0x100004000, 0, 35, Store.OLD):
        for model in (before, after):
            model.words[model.SLOT] = model.OLD
            model.ns['pcc_gc_store_field_ptr'](model.OWNER, model.SLOT, value)
        assert before.snapshot() == after.snapshot()


@pytest.mark.parametrize('count_free', (False, True))
@pytest.mark.parametrize('change_at', ('log', 'lock'))
def test_field_commit_uses_mode_captured_before_logging_and_lock(count_free, change_at):
    options = dict(backend=4, config=1, count_free=count_free, container=0,
                   logging=True, forwarded=True, marking=True)
    before = FieldStore(field_source=OLD_FIELD_SOURCE, **options)
    after = FieldStore(field_source=SOURCE.read_text(), **options)
    name = ('pcc_diagnostics_runtime_log_event_code' if change_at == 'log'
            else 'pcc_py_gc_minor_graph_lock')
    for model in (before, after):
        original = model.ns[name]

        def change_mode(*args, model=model, original=original):
            original(*args)
            model.globals['pcc_gc_backend4_no_refcounts'] = int(not count_free)

        model.ns[name] = change_mode
        model.ns['pcc_gc_store_field_ptr'](model.OWNER, model.SLOT, model.VALUE)
        assert int.from_bytes(model.plan_bytes[120:124], 'little') == int(count_free)
    assert before.snapshot() == after.snapshot()


@pytest.mark.parametrize('backend', range(5))
@pytest.mark.parametrize('count_free', (False, True))
@pytest.mark.parametrize('change_at', ('log', 'lock'))
def test_field_commit_uses_backend_captured_before_logging_and_lock(
    backend, count_free, change_at,
):
    options = dict(backend=backend, config=1, count_free=count_free, container=0,
                   logging=True, forwarded=True, marking=True)
    before = FieldStore(field_source=OLD_FIELD_SOURCE, **options)
    after = FieldStore(field_source=SOURCE.read_text(), **options)
    name = ('pcc_diagnostics_runtime_log_event_code' if change_at == 'log'
            else 'pcc_py_gc_minor_graph_lock')
    for model in (before, after):
        original = model.ns[name]

        def change_backend(*args, model=model, original=original):
            original(*args)
            # Artificial reconfiguration after plan initialization. Dispatch
            # must consume the captured plan, even if live globals disagree.
            model.globals['pcc_gc_backend_selected'] = (backend + 1) % 5
            model.globals['pcc_gc_config_initialized'] = 0

        model.ns[name] = change_backend
        model.ns['pcc_gc_store_field_ptr'](model.OWNER, model.SLOT, model.VALUE)
        assert int.from_bytes(model.plan_bytes[112:120], 'little') == backend
    assert before.snapshot() == after.snapshot()


def test_field_fallback_body_remains_exact():
    def body(source):
        return next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == 'pcc_gc_store_field_ptr')

    old = body(OLD_FIELD_SOURCE)
    new = body(SOURCE.read_text())
    new.decorator_list = []
    transaction = next(node for node in new.body if isinstance(node, ast.If)
                       and '_fields_lock_themselves' in ast.unparse(node.test))
    fallback = []
    for node in transaction.orelse:
        if (isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id in ('_FIELD_PLAN_BACKEND', '_FIELD_PLAN_MODE')):
            continue
        if (isinstance(node, ast.If)
                and '_FIELD_PLAN_MODE' in ast.unparse(node.test)):
            fallback.extend(node.orelse)
        else:
            fallback.append(node)
    transaction.orelse = fallback
    # Keep the complete baseline transaction after accounting for its
    # exact typed acquisition, rather than discarding either lock call.
    locks = [node for node in ast.walk(old) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == 'pcc_py_gc_minor_graph_lock']
    assert len(locks) == 1 and not locks[0].args and not locks[0].keywords
    locks[0].func.id = 'pcc_py_gc_minor_graph_lock_for'
    locks[0].args = [ast.Constant(value=3)]
    assert ast.dump(new) == ast.dump(old)


def test_field_direct_and_fallback_share_unlock_and_finish_tail():
    field = next(node for node in ast.parse(SOURCE.read_text()).body
                 if isinstance(node, ast.FunctionDef)
                 and node.name == 'pcc_gc_store_field_ptr')
    calls = [node.func.id for node in ast.walk(field)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert calls.count('pcc_py_gc_minor_graph_unlock') == 1
    assert calls.count('pcc_gc_store_ptr_plan_finish') == 1
    assert ast.unparse(field.body[-1]) == 'pcc_gc_store_ptr_plan_finish(plan)'


@pytest.mark.parametrize('stop_after_unlock', (False, True))
@pytest.mark.parametrize('owner', (0, Store.OWNER))
@pytest.mark.parametrize('value', (0, Store.VALUE))
@pytest.mark.parametrize('forwarded', (False, True))
def test_field_keeps_finish_entry_after_unlock_before_final_state(
    stop_after_unlock, owner, value, forwarded,
):
    """Model the compiled helper-entry poll, whose real IR is checked below.

    These hooks model a late stop request; they do not execute a native poll
    or establish actual parked-thread/object-survival behavior.
    """
    options = dict(backend=4, config=1, count_free=True, container=0,
                   logging=True, forwarded=forwarded, marking=True)
    before = FieldStore(field_source=OLD_FIELD_SOURCE, **options)
    after = FieldStore(field_source=SOURCE.read_text(), **options)
    for model in (before, after):
        unlock = model.ns['pcc_py_gc_minor_graph_unlock']
        finish = model.ns['pcc_gc_store_ptr_plan_finish']
        pending = [False]

        def unlock_then_request(model=model, unlock=unlock, pending=pending):
            unlock()
            pending[0] = stop_after_unlock

        def finish_entry(plan, model=model, finish=finish, pending=pending):
            assert model.depth == 0
            assert int.from_bytes(model.plan_bytes[124:128], 'little') == 3
            assert model.plan_bytes[8:112] == bytes(104)
            model.events.append(('finish-entry-poll', pending[0]))
            if pending[0]:
                resolved = model.COPY if forwarded and value == model.VALUE else value
                assert int.from_bytes(model.plan_bytes[:8], 'little') == resolved
                model.events.append(('modeled-park', resolved))
            finish(plan)

        model.ns['pcc_py_gc_minor_graph_unlock'] = unlock_then_request
        model.ns['pcc_gc_store_ptr_plan_finish'] = finish_entry
        model.ns['pcc_gc_store_field_ptr'](owner, model.SLOT, value)
        assert model.events.count(('finish-entry-poll', stop_after_unlock)) == 1
        assert int.from_bytes(model.plan_bytes[124:128], 'little') == 7
    assert before.snapshot() == after.snapshot()


def assert_field_unlock_reaches_finish_poll(text):
    """Every IR path from graph release to return must retain the old helper."""
    from pcc.ir.optimization.ir_mutator import MutableModule

    functions = {fn.name: fn for fn in MutableModule.parse(text).functions}
    field = functions['pcc_gc_store_field_ptr']
    finish_name = 'user_py_obj__pcc_gc_store_plan_finish'
    finish = functions[finish_name]
    entry = finish.blocks[0]
    entry_text = ''.join(inst.text for inst in entry.instructions)
    flag = re.search(r'%([\w.$-]+) = load atomic i32, ptr @pcc_thread_stop_requested '
                     + SAFEPOINT_POLL_LOAD_ORDERING + r', align 4', entry_text)
    assert flag
    need = re.search(r'%([\w.$-]+) = icmp ne i32 %' + re.escape(flag[1]) + r', 0', entry_text)
    assert need
    assert entry.terminator.text.strip().startswith('br i1 %' + need[1] + ',')
    assert not any(inst.opcode == 'store' for inst in entry.instructions)
    labels = re.findall(r'label %([\w.$-]+)', entry.terminator.text)
    assert len(labels) == 2
    finish_blocks = {block.name: block for block in finish.blocks}
    assert any('@pcc_thread_safepoint()' in inst.text
               for inst in finish_blocks[labels[0]].instructions)

    def require_finish(fn, starts, callees):
        blocks = {block.name: block for block in fn.blocks}
        assert starts
        pending, seen, returns = list(starts), set(), 0
        while pending:
            name, index, finished = pending.pop()
            if (name, index, finished) in seen:
                continue
            seen.add((name, index, finished))
            block = blocks[name]
            for inst in block.instructions[index:]:
                if any('@' + callee + '(' in inst.text for callee in callees):
                    finished = True
                if inst.opcode == 'ret':
                    assert finished, 'field returns after graph unlock without finish entry poll'
                    returns += 1
            if block.terminator.opcode != 'ret':
                successors = re.findall(r'label %([\w.$-]+)', block.terminator.text)
                assert successors, 'unexpected nonreturning field path'
                pending.extend((label, 0, finished) for label in successors)
        assert returns

    wrapper_name = 'pcc_gc_store_ptr_plan_finish'
    wrapper = functions[wrapper_name]
    # Frontend IR retains this wrapper; the owned runtime optimizer normally
    # inlines it. Both shapes must reach the same core helper's entry poll.
    require_finish(wrapper, [(wrapper.blocks[0].name, 0, False)], {finish_name})
    starts = [(block.name, index + 1, False)
              for block in field.blocks
              for index, inst in enumerate(block.instructions)
              if '@pcc_py_gc_minor_graph_unlock(' in inst.text]
    require_finish(field, starts, {finish_name, wrapper_name})


@pytest.mark.integration
def test_field_direct_store_keeps_scalar_ir_and_opaque_plan(tmp_path):
    from pcc.frontends.python.owned_runtime_build import _compile_runtime_module
    from pcc.frontends.python.pipeline_targets import host_target_triple

    output = tmp_path / 'py_obj.ll'
    _compile_runtime_module('py_obj', str(SOURCE), str(output), host_target_triple())
    text = output.read_text()
    bodies = [part.split('\n}', 1)[0] for part in text.split('\ndefine ')
              if '@pcc_gc_store_field_ptr(' in part.split('\n', 1)[0]]
    assert len(bodies) == 1
    body = bodies[0]
    assert 'alloca [128 x i8]' in body
    assert 'icmp ' in body
    assert '@pcc_gc_backend4_note_heap_store_locked(' in body
    assert '@pcc_gc_note_slot_write_barrier(' in body
    for symbol in ('py_obj_eq_value', 'py_int_from_i64', 'pcc_gc_pin', 'pcc_gc_retain'):
        assert '@' + symbol + '(' not in body, symbol
    assert '.modvar.py_obj._FIELD_PLAN_' not in body
    assert_field_unlock_reaches_finish_poll(text)
    # A removed helper call must fail this check even if its unused definition
    # (and therefore its entry stop poll) remains in the module.
    removed_body = re.sub(
        r'^.*call void.*@(?:user_py_obj__pcc_gc_store_plan_finish|pcc_gc_store_ptr_plan_finish)\(.*\n',
        '', body, flags=re.MULTILINE,
    )
    assert removed_body != body
    removed = text.replace(body, removed_body, 1)
    with pytest.raises(AssertionError, match='without finish entry poll'):
        assert_field_unlock_reaches_finish_poll(removed)
