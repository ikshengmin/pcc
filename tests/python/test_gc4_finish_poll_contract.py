"""Host-only IR contract controls; these do not compile or execute a runtime."""

import pytest

from pcc.ir.optimization.inline import SAFEPOINT_POLL_LOAD_ORDERING
from tests.python.test_gc4_field_direct_store import (
    assert_field_unlock_reaches_finish_poll,
)
from tests.python.test_gc4_public_stop_poll import (
    assert_public_unlock_reaches_finish_poll,
)


FINISH = 'user_py_obj__pcc_gc_store_plan_finish'
WRAPPER = 'pcc_gc_store_ptr_plan_finish'
CHECKERS = (
    (assert_public_unlock_reaches_finish_poll, 'pcc_gc_store_ptr'),
    (assert_field_unlock_reaches_finish_poll, 'pcc_gc_store_field_ptr'),
)
STOP_LOAD = (
    '  %stop = load atomic i32, ptr @pcc_thread_stop_requested '
    + SAFEPOINT_POLL_LOAD_ORDERING + ', align 4\n'
)
FLUSH_LOAD = (
    '  %flush = load atomic i32, ptr @pcc_gc_gray_worklist_flush_requested '
    + SAFEPOINT_POLL_LOAD_ORDERING + ', align 4\n'
)
COMBINE = '  %pending = or i32 %stop, %flush\n'
COMPARE = '  %need = icmp ne i32 %pending, 0\n'
BRANCH = '  br i1 %need, label %slow, label %cont\n'
CALL = '  call void @pcc_thread_safepoint()\n'


def _module(*, direct=False):
    source = (
        '@pcc_thread_stop_requested = external global i32\n'
        '@pcc_gc_gray_worklist_flush_requested = external global i32\n'
        'declare void @pcc_thread_safepoint()\n'
        'declare void @pcc_thread_safepoint_gate()\n'
        'declare void @pcc_py_gc_minor_graph_unlock()\n'
        'define void @' + FINISH + '() {\nentry:\n'
        '  %other = add i32 0, 0\n'
        '  %other_need = icmp ne i32 %other, 0\n'
        + STOP_LOAD + FLUSH_LOAD + COMBINE + COMPARE + BRANCH
        + 'slow:\n' + CALL + '  br label %cont\ncont:\n  ret void\n}\n'
        + 'define void @' + WRAPPER + '() {\nentry:\n'
        + '  call void @' + FINISH + '()\n  ret void\n}\n'
    )
    callee = FINISH if direct else WRAPPER
    for _checker, name in CHECKERS:
        source += (
            'define void @' + name + '() {\nentry:\n'
            '  call void @pcc_py_gc_minor_graph_unlock()\n'
            '  call void @' + callee + '()\n  ret void\n}\n'
        )
    return source


def _replace_once(source, before, after):
    assert before in source
    changed = source.replace(before, after, 1)
    assert changed != source
    return changed


@pytest.mark.parametrize('checker,store', CHECKERS)
@pytest.mark.parametrize('direct', (False, True), ids=('wrapper', 'direct'))
@pytest.mark.parametrize('typed_call', (False, True), ids=('plain-call', 'typed-call'))
def test_finish_poll_accepts_both_requests_on_reachable_store_paths(
    checker, store, direct, typed_call,
):
    source = _module(direct=direct)
    if typed_call:
        source = _replace_once(source, CALL, CALL.replace('call void @', 'call void () @'))
    checker(source)


@pytest.mark.parametrize('checker,store', CHECKERS)
@pytest.mark.parametrize('before,after', (
    pytest.param(STOP_LOAD, '', id='missing-stop'),
    pytest.param(FLUSH_LOAD, '', id='missing-flush'),
    pytest.param(STOP_LOAD, '  %stop = load i32, ptr @pcc_thread_stop_requested, align 4\n',
                 id='non-atomic-stop'),
    pytest.param(FLUSH_LOAD, '  %flush = load i32, ptr @pcc_gc_gray_worklist_flush_requested, align 4\n',
                 id='non-atomic-flush'),
    pytest.param(STOP_LOAD, STOP_LOAD.replace(SAFEPOINT_POLL_LOAD_ORDERING, 'acquire'),
                 id='wrong-stop-order'),
    pytest.param(FLUSH_LOAD, FLUSH_LOAD.replace(SAFEPOINT_POLL_LOAD_ORDERING, 'acquire'),
                 id='wrong-flush-order'),
    pytest.param(STOP_LOAD + FLUSH_LOAD,
                 (STOP_LOAD + FLUSH_LOAD).replace(SAFEPOINT_POLL_LOAD_ORDERING, 'acquire'),
                 id='both-wrong-order'),
    pytest.param(FLUSH_LOAD, FLUSH_LOAD.replace('align 4', 'align 8'), id='wrong-alignment'),
    pytest.param(COMBINE, COMBINE.replace(' or ', ' and '), id='and-not-or'),
    pytest.param(COMBINE, COMBINE.replace('%flush', '%stop'), id='duplicate-stop-operand'),
    pytest.param(COMBINE, COMBINE.replace('%flush', '%other'), id='unrelated-or-operand'),
    pytest.param(COMPARE, COMPARE.replace('%pending', '%stop'), id='compare-stop-only'),
    pytest.param(COMPARE, COMPARE.replace('%pending', '%flush'), id='compare-flush-only'),
    pytest.param(COMPARE, COMPARE.replace('%pending', '%other'), id='compare-unrelated-ssa'),
    pytest.param(COMPARE, COMPARE.replace(' ne ', ' eq '), id='wrong-comparison'),
    pytest.param(COMPARE, COMPARE.replace(', 0', ', 1'), id='wrong-comparison-constant'),
    pytest.param(BRANCH, BRANCH.replace('%need', '%other_need'), id='unrelated-branch-condition'),
    pytest.param(BRANCH, '  br i1 %need, label %cont, label %slow\n', id='swapped-successors'),
    pytest.param(CALL, '', id='missing-slow-call'),
    pytest.param(CALL, CALL.replace('pcc_thread_safepoint', 'pcc_thread_safepoint_gate'),
                 id='gate-not-plain-poll'),
    pytest.param('  br label %cont\n', '  ret void\n', id='slow-path-does-not-rejoin'),
    pytest.param('entry:\n', 'entry:\n  store i32 0, ptr @pcc_thread_stop_requested\n',
                 id='entry-store'),
))
def test_finish_poll_rejects_inexact_request_chain(checker, store, before, after):
    source = _replace_once(_module(), before, after)
    with pytest.raises(AssertionError):
        checker(source)


@pytest.mark.parametrize('checker,store', CHECKERS)
def test_finish_poll_in_unused_function_cannot_cover_real_finish(checker, store):
    source = _module()
    start = source.index('define void @' + FINISH + '(')
    end = source.index('\n}\n', start) + len('\n}\n')
    good_finish = source[start:end]
    unpolled_finish = (
        'define void @' + FINISH + '() {\nentry:\n  ret void\n}\n'
    )
    source = source[:start] + unpolled_finish + source[end:]
    source += good_finish.replace('@' + FINISH + '(', '@unused_finish(')
    with pytest.raises(AssertionError):
        checker(source)


@pytest.mark.parametrize('checker,store', CHECKERS)
def test_plain_poll_in_unreachable_block_cannot_cover_slow_path(checker, store):
    source = _replace_once(_module(), CALL, '')
    source = _replace_once(
        source, 'cont:\n  ret void\n}\n',
        'cont:\n  ret void\ndead:\n' + CALL + '  ret void\n}\n',
    )
    with pytest.raises(AssertionError):
        checker(source)


@pytest.mark.parametrize('checker,store', CHECKERS)
def test_finish_poll_requires_wrapper_to_call_finish(checker, store):
    source = _replace_once(_module(), '  call void @' + FINISH + '()\n', '')
    with pytest.raises(AssertionError, match='without finish entry poll'):
        checker(source)


@pytest.mark.parametrize('checker,store', CHECKERS)
@pytest.mark.parametrize('direct', (False, True), ids=('wrapper', 'direct'))
def test_finish_poll_requires_call_after_each_store_unlock(checker, store, direct):
    source = _module(direct=direct)
    callee = FINISH if direct else WRAPPER
    before = (
        'define void @' + store + '() {\nentry:\n'
        '  call void @pcc_py_gc_minor_graph_unlock()\n'
        '  call void @' + callee + '()\n'
    )
    after = before.removesuffix('  call void @' + callee + '()\n')
    source = _replace_once(source, before, after)
    with pytest.raises(AssertionError, match='without finish entry poll'):
        checker(source)
