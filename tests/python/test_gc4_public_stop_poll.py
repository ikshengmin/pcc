"""Public-store poll and root-image regressions, not native concurrency proof."""

import re

import pytest

from pcc.ir.optimization.inline import SAFEPOINT_POLL_LOAD_ORDERING

from tests.python.test_gc4_public_direct_store import (
    OLD_PUBLIC_SOURCE,
    PublicStore,
    SOURCE,
    Store,
)


@pytest.mark.parametrize('stop_after_unlock', (False, True))
@pytest.mark.parametrize('owner', (0, Store.OWNER))
@pytest.mark.parametrize('value', (0, 35, Store.VALUE))
@pytest.mark.parametrize('forwarded', (False, True))
def test_public_keeps_finish_entry_and_committed_root_image(
    stop_after_unlock, owner, value, forwarded,
):
    options = dict(backend=4, config=1, count_free=True, logging=True,
                   forwarded=forwarded, marking=True)
    before = PublicStore(source=OLD_PUBLIC_SOURCE, **options)
    after = PublicStore(source=SOURCE.read_text(), **options)
    for model in (before, after):
        unlock = model.ns['pcc_py_gc_minor_graph_unlock']
        finish = model.ns['pcc_gc_store_ptr_plan_finish']
        pending = [False]
        resolved = model.COPY if forwarded and value == model.VALUE else value

        def unlock_then_request(model=model, unlock=unlock, pending=pending):
            unlock()
            pending[0] = stop_after_unlock

        def finish_entry(plan, model=model, finish=finish, pending=pending):
            assert model.depth == 0
            assert model.words[plan] == resolved
            assert model.words[plan + 112] == 4
            assert model.words[plan + 120] == 1
            assert model.words[plan + 124] == 3
            assert all(model.words[plan + i] == 0 for i in range(8, 112, 4))
            model.events.append(('finish-entry-poll', pending[0]))
            # An artificial late request. This does not execute native
            # parking, collection, relocation, or finalizers.
            if pending[0]:
                model.events.append(('modeled-park', resolved))
            finish(plan)

        model.ns['pcc_py_gc_minor_graph_unlock'] = unlock_then_request
        model.ns['pcc_gc_store_ptr_plan_finish'] = finish_entry
        model.ns['pcc_gc_store_ptr'](owner, model.SLOT, value)
        assert model.events.count(('finish-entry-poll', stop_after_unlock)) == 1
        assert model.words[model.PLAN + 124] == 7
    assert before.snapshot() == after.snapshot()
    assert before.words == after.words


def assert_finish_entry_request_poll(finish):
    """Require the entry branch to service either request through a plain poll."""
    entry = finish.blocks[0]
    assert not any(inst.opcode == 'store' for inst in entry.instructions)
    assert len(entry.instructions) >= 5
    tail = [inst.text.strip() for inst in entry.instructions[-5:]]
    loads = []
    for index, symbol in enumerate((
        'pcc_thread_stop_requested', 'pcc_gc_gray_worklist_flush_requested',
    )):
        load = re.fullmatch(
            r'%([\w.$-]+) = load atomic i32, ptr @' + symbol + ' '
            + re.escape(SAFEPOINT_POLL_LOAD_ORDERING) + r', align 4', tail[index],
        )
        assert load
        loads.append(load[1])
    pending = re.fullmatch(
        r'%([\w.$-]+) = or i32 %' + re.escape(loads[0])
        + r', %' + re.escape(loads[1]), tail[2],
    )
    assert pending
    need = re.fullmatch(
        r'%([\w.$-]+) = icmp ne i32 %' + re.escape(pending[1]) + r', 0', tail[3],
    )
    assert need
    assert len(set(loads + [pending[1], need[1]])) == 4
    branch = re.fullmatch(
        r'br i1 %' + re.escape(need[1]) + r', label %([\w.$-]+), label %([\w.$-]+)',
        entry.terminator.text.strip(),
    )
    assert branch and branch[1] != branch[2]
    finish_blocks = {block.name: block for block in finish.blocks}
    assert branch[1] in finish_blocks and branch[2] in finish_blocks
    slow = finish_blocks[branch[1]]
    assert len(slow.instructions) == 2
    assert re.fullmatch(
        r'call void (?:\(\) )?@pcc_thread_safepoint\(\)',
        slow.instructions[0].text.strip(),
    )
    assert slow.terminator.text.strip() == 'br label %' + branch[2]


def assert_public_unlock_reaches_finish_poll(text):
    """Validate the reachable entry poll, not an unused helper definition."""
    from pcc.ir.optimization.ir_mutator import MutableModule

    functions = {fn.name: fn for fn in MutableModule.parse(text).functions}
    finish_name = 'user_py_obj__pcc_gc_store_plan_finish'
    finish = functions[finish_name]
    assert_finish_entry_request_poll(finish)

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
                    assert finished, 'public store returns after graph unlock without finish entry poll'
                    returns += 1
            if block.terminator.opcode != 'ret':
                successors = re.findall(r'label %([\w.$-]+)', block.terminator.text)
                assert successors, 'unexpected nonreturning public-store path'
                pending.extend((label, 0, finished) for label in successors)
        assert returns

    wrapper_name = 'pcc_gc_store_ptr_plan_finish'
    wrapper = functions[wrapper_name]
    require_finish(wrapper, [(wrapper.blocks[0].name, 0, False)], {finish_name})
    public = functions['pcc_gc_store_ptr']
    starts = [(block.name, index + 1, False)
              for block in public.blocks
              for index, inst in enumerate(block.instructions)
              if '@pcc_py_gc_minor_graph_unlock(' in inst.text]
    require_finish(public, starts, {finish_name, wrapper_name})


@pytest.mark.integration
def test_public_store_emitted_finish_poll_and_removed_call_mutation(tmp_path):
    from pcc.frontends.python.owned_runtime_build import _compile_runtime_module
    from pcc.frontends.python.pipeline_targets import host_target_triple

    output = tmp_path / 'py_obj.ll'
    _compile_runtime_module('py_obj', str(SOURCE), str(output), host_target_triple())
    text = output.read_text()
    assert_public_unlock_reaches_finish_poll(text)
    bodies = [part.split('\n}', 1)[0] for part in text.split('\ndefine ')
              if '@pcc_gc_store_ptr(' in part.split('\n', 1)[0]]
    assert len(bodies) == 1
    body = bodies[0]
    removed = re.sub(
        r'^.*call void.*@(?:user_py_obj__pcc_gc_store_plan_finish|pcc_gc_store_ptr_plan_finish)\(.*\n',
        '', body, flags=re.MULTILINE,
    )
    assert removed != body
    with pytest.raises(AssertionError, match='without finish entry poll'):
        assert_public_unlock_reaches_finish_poll(text.replace(body, removed, 1))
