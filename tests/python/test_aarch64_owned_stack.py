"""Owned source-kernel contracts against the independent legacy text oracle.

Production driver execution is a separate qualification; timer tests invoke
the real admission helper, not a duplicated or source-extracted predicate.
"""
import pytest

from pcc.backend import self_backend_aarch64_owned_stack as kernel
from pcc.backend import self_backend_aarch64_typed_peepholes as typed
from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend.self_backend_value_arena import CompilerInt4, CompilerIntArena
from tests.python import test_aarch64_typed_peepholes as oracle

NAMES = {1: 'forward_adjacent_stack_store_load',
         2: 'forward_one_intervening_stack_store_load'}


def address(base='x29', offset=-8, radix=10):
    return (CompilerInt4(sem.TOKEN_ADDRESS, sem.ADDRESS_OFFSET, 2, 0),
            oracle.R(base), oracle.N(offset, radix))


def items(mode, case):
    base = 'fp' if case == 'fp' else 'x29'
    byte = case == 'byte'
    source = 'w9' if byte else 'x9'
    dest = 'w10' if byte else 'x10'
    if case.startswith('same_'):
        source = dest = case.removeprefix('same_')
    offset = -16 if case == 'offset' else -8
    result = [oracle.I('sturb' if byte else 'stur', oracle.R(source), address(base))]
    if mode == 2:
        result.append(oracle.I('ldur', oracle.R('w9' if case == 'alias' else 'x11'), address('x29', -24)))
    result.append(oracle.I('ldurb' if byte else 'ldur', oracle.R(dest), address(base, offset)))
    return result


CASES = [(mode, case) for mode in (1, 2)
         for case in ('basic', 'fp', 'offset', 'byte', 'same_x9', 'same_w9', 'same_s9', 'same_d9')]
CASES.append((2, 'alias'))


@pytest.mark.parametrize('mode,case', CASES)
def test_actual_owned_source_matches_legacy_words_and_events(monkeypatch, mode, case):
    decisions = []
    original_decide = kernel.decide
    original_run = typed._run
    def decide(*args):
        decisions.append(args[1])
        return original_decide(*args)
    def dispatch(fragments, semantics, span, selected_mode):
        if selected_mode in (1, 2):
            return typed._run_owned_stack(fragments, semantics, span, selected_mode)
        return original_run(fragments, semantics, span, selected_mode)
    monkeypatch.setattr(kernel, 'decide', decide)
    monkeypatch.setattr(typed, '_run', dispatch)
    oracle._differential(monkeypatch, NAMES[mode], items(mode, case))
    assert decisions and set(decisions) == {mode}


def read_cursor(arena, cursor):
    result = []
    while True:
        rid = arena.next_record(cursor)
        if rid < 0:
            return result
        result.append(rid)


@pytest.mark.parametrize('mode', (1, 2))
@pytest.mark.parametrize('case', ('basic', 'fp'))
def test_owned_output_is_independent_and_original_cursor_stays_a_snapshot(mode, case):
    fragments, semantics, source, _ = oracle._fixture(items(mode, case))
    cursor = CompilerIntArena()
    try:
        before = oracle._ids(fragments, source)
        records = list(fragments.records._values)
        headers = list(semantics.headers._values)
        tokens = list(semantics.tokens._values)
        fragments.spans.start_cursor(source, cursor)
        first = fragments.spans.next_record(cursor)
        result = typed._run_owned_stack(fragments, semantics, source, mode)
        output = oracle._ids(fragments, result)
        assert result != source
        assert oracle._ids(fragments, source) == before
        assert fragments.records._values[:len(records)] == records
        assert semantics.headers._values[:len(headers)] == headers
        assert semantics.tokens._values[:len(tokens)] == tokens
        fragments.spans.append(source, before[0])
        assert oracle._ids(fragments, result) == output
        fragments.extend_fragment(result, result)
        assert oracle._ids(fragments, result) == output * 2
        assert oracle._ids(fragments, source) == before + before[:1]
        assert [first] + read_cursor(fragments.spans, cursor) == before
    finally:
        cursor.close()
        semantics.close()
        fragments.close()


class CustomTimer:
    def __init__(self):
        self.events = []
    def begin_backend_finalize(self, name):
        self.events.append(('begin', name))
        return 0
    def complete_backend_finalize(self, name, started):
        self.events.append(('complete', name))
    def __bool__(self):
        raise AssertionError('timer truthiness is not a route-admission operation')


@pytest.mark.parametrize('with_timer', (False, True))
def test_public_runner_never_enters_owned_kernel(monkeypatch, with_timer):
    fragments, semantics, span, _ = oracle._fixture(items(1, 'basic'))
    timer = CustomTimer() if with_timer else None
    def forbidden(*args, **kwargs):
        raise AssertionError('public runner entered private owned kernel')
    monkeypatch.setattr(typed, '_run_owned_stack', forbidden)
    try:
        result = typed.run_aarch64_typed_peepholes(fragments, semantics, span, phase_timing=timer)
        assert oracle._ids(fragments, result)
        if with_timer:
            assert ('begin', 'typed_forward_adjacent_stack_store_load') in timer.events
            assert ('complete', 'typed_forward_one_intervening_stack_store_load') in timer.events
    finally:
        semantics.close()
        fragments.close()


def test_real_timer_helper_accepts_only_supported_exact_timer():
    from pcc.backend.indexed_phase_timing import ModulePhaseTiming, CoarsePhaseTiming
    class ModuleSubclass(ModulePhaseTiming):
        pass
    assert typed._owned_timer_is_canonical(None) is True
    assert typed._owned_timer_is_canonical(ModulePhaseTiming('owned-test')) is True
    assert typed._owned_timer_is_canonical(CoarsePhaseTiming()) is False
    assert typed._owned_timer_is_canonical(CustomTimer()) is False
    assert typed._owned_timer_is_canonical(ModuleSubclass('owned-test')) is False


@pytest.mark.parametrize('method', ('begin_backend_finalize', 'complete_backend_finalize', 'start'))
def test_real_timer_helper_rejects_exact_instance_method_override(method):
    from pcc.backend.indexed_phase_timing import ModulePhaseTiming
    timer = ModulePhaseTiming('owned-test')
    def callback(*args, **kwargs):
        raise AssertionError('admission must not execute overridden timer callback')
    setattr(timer, method, callback)
    assert typed._owned_timer_is_canonical(timer) is False


@pytest.mark.parametrize('method', ('begin_backend_finalize', 'complete_backend_finalize', 'start'))
def test_real_timer_helper_rejects_method_bound_to_another_owner(method):
    from pcc.backend.indexed_phase_timing import ModulePhaseTiming
    timer = ModulePhaseTiming('owned-test')
    other = ModulePhaseTiming('other-owner')
    setattr(timer, method, getattr(other, method))
    assert typed._owned_timer_is_canonical(timer) is False


@pytest.mark.parametrize('field', ('failed', 'module_name'))
def test_real_timer_helper_rejects_custom_control_values_without_callbacks(field):
    from pcc.backend.indexed_phase_timing import ModulePhaseTiming
    timer = ModulePhaseTiming('owned-test')
    class Flag:
        def __bool__(self):
            raise AssertionError('admission executed custom truthiness')
    class Name(str):
        def __str__(self):
            raise AssertionError('admission rendered a custom module name')
    setattr(timer, field, Flag() if field == 'failed' else Name('owned-test'))
    assert typed._owned_timer_is_canonical(timer) is False


@pytest.mark.parametrize('failure', ('copy_1', 'copy_2', 'copy_3', 'producer', 'output_append', 'finish'))
def test_owned_failure_closes_real_scratch_and_preserves_input(monkeypatch, failure):
    """Private monkeypatches here inject failures, not new public hook contracts."""
    fragments, semantics, source, _ = oracle._fixture(items(1, 'basic'))
    old_cursor = CompilerIntArena()
    original_ids = oracle._ids(fragments, source)
    record_prefix = list(fragments.records._values)
    header_prefix = list(semantics.headers._values)
    token_prefix = list(semantics.tokens._values)
    fragments.spans.start_cursor(source, old_cursor)
    expected_error = (MemoryError('injected owned list allocation') if failure.startswith('copy_')
                      else RuntimeError('injected owned ' + failure))
    passes = []
    closed = []
    original_init = typed._Pass.__init__
    original_close = CompilerIntArena.close
    original_append = CompilerIntArena.append
    original_new_fragment = fragments.new_fragment

    def initialize(self, *args):
        original_init(self, *args)
        passes.append(self)

    def close(self):
        if passes and (self is passes[0].ids or self is passes[0].output):
            closed.append(self)
        return original_close(self)

    monkeypatch.setattr(typed._Pass, '__init__', initialize)
    monkeypatch.setattr(CompilerIntArena, 'close', close)
    if failure.startswith('copy_'):
        copy_count = 0
        fail_at = int(failure[-1])
        def copy(values):
            nonlocal copy_count
            copy_count += 1
            if copy_count == fail_at:
                raise expected_error
            return list(values)
        monkeypatch.setattr(typed, 'list', copy, raising=False)
    elif failure == 'producer':
        def produce(*args, **kwargs):
            raise expected_error
        monkeypatch.setattr(sem, 'append_semantic_instruction', produce)
    elif failure == 'output_append':
        def append(self, value):
            if passes and self is passes[0].output:
                raise expected_error
            return original_append(self, value)
        monkeypatch.setattr(CompilerIntArena, 'append', append)
    else:
        new_roots = 0
        def new_fragment():
            nonlocal new_roots
            new_roots += 1
            # _Pass construction obtains its staging root first. The next
            # root is requested inside the actual finish() implementation.
            if new_roots == 2:
                raise expected_error
            return original_new_fragment()
        monkeypatch.setattr(fragments, 'new_fragment', new_fragment)
    try:
        with pytest.raises(type(expected_error)) as raised:
            typed._run_owned_stack(fragments, semantics, source, 1)
        assert raised.value is expected_error
        assert len(passes) == 1
        context = passes[0]
        assert len(closed) == 2
        assert closed[0] is context.output and closed[1] is context.ids
        for scratch in (context.ids, context.output):
            assert scratch._closed is True
            assert scratch._address == 0 and len(scratch) == 0
            assert scratch._values == []
        if failure.startswith('copy_'):
            assert copy_count == fail_at
        if failure == 'finish':
            assert new_roots == 2
        assert oracle._ids(fragments, source) == original_ids
        assert read_cursor(fragments.spans, old_cursor) == original_ids
        assert fragments.records._values[:len(record_prefix)] == record_prefix
        assert semantics.headers._values[:len(header_prefix)] == header_prefix
        assert semantics.tokens._values[:len(token_prefix)] == token_prefix
        # Original owners remain usable; only private scratch was retired.
        assert not fragments.closed and not semantics.closed
        semantics.require_complete(fragments.records, len(fragments.symbol_names))
    finally:
        old_cursor.close()
        semantics.close()
        fragments.close()


@pytest.mark.parametrize('mode', (1, 2))
@pytest.mark.parametrize('mutation', ('complete_method', 'failed_value'))
def test_internal_runner_rechecks_timer_after_actual_begin(monkeypatch, mode, mutation):
    """Inject the transition inside the real timer begin, not a fake runner."""
    from pcc.backend import indexed_phase_timing as timing
    timer = timing.ModulePhaseTiming('owned-boundary-test')
    fragments, semantics, span, _ = oracle._fixture(items(1, 'basic'))
    target_phase = ('typed_forward_adjacent_stack_store_load' if mode == 1
                    else 'typed_forward_one_intervening_stack_store_load')
    events = []
    owned_modes = []
    regular_modes = []
    original_owned = typed._run_owned_stack
    original_regular = typed._run
    injected = False

    class FailedValue:
        def __bool__(self):
            events.append('custom_failed_truth')
            return False

    def write_event(*args, **kwargs):
        nonlocal injected
        if len(args) >= 4 and args[1] == target_phase and args[3] == 'begin' and not injected:
            injected = True
            events.append('mutation')
            if mutation == 'failed_value':
                timer.failed = FailedValue()
            else:
                def custom_complete(*args):
                    events.append('custom_complete')
                timer.complete_backend_finalize = custom_complete

    def owned(f, s, current, selected_mode):
        owned_modes.append(selected_mode)
        events.append(('owned', selected_mode))
        return original_owned(f, s, current, selected_mode)

    def regular(f, s, current, selected_mode):
        regular_modes.append(selected_mode)
        events.append(('regular', selected_mode))
        return original_regular(f, s, current, selected_mode)

    monkeypatch.setattr(timing, '_write_event', write_event)
    monkeypatch.setattr(typed, '_run_owned_stack', owned)
    monkeypatch.setattr(typed, '_run', regular)
    assert typed._owned_timer_is_canonical(timer) is True
    try:
        result = typed._run_aarch64_typed_peepholes(
            fragments, semantics, span, phase_timing=timer, owned_stack_windows=True,
        )
        assert injected
        assert owned_modes == ([] if mode == 1 else [1])
        assert mode in regular_modes
        assert events.index('mutation') < events.index(('regular', mode))
        if mutation == 'failed_value':
            # The admission recheck rejects by type without executing custom
            # truthiness; that is reached only by the later real timer close.
            assert events.index(('regular', mode)) < events.index('custom_failed_truth')
        else:
            assert events.index(('regular', mode)) < events.index('custom_complete')
        assert typed._owned_timer_is_canonical(timer) is False
        assert oracle._ids(fragments, result)
        semantics.require_complete(fragments.records, len(fragments.symbol_names))
    finally:
        semantics.close()
        fragments.close()


@pytest.mark.parametrize("name", ("begin_backend_finalize", "complete_backend_finalize", "start"))
def test_timer_callable_admission_does_not_probe_user_attributes(name):
    from pcc.backend.indexed_phase_timing import ModulePhaseTiming
    timer = ModulePhaseTiming("fixture.callable")
    calls = []
    class Callback:
        def __getattr__(self, attribute):
            calls.append(attribute)
            raise AssertionError("admission invoked custom attribute hook")
        def __call__(self, *args):
            return 0
    setattr(timer, name, Callback())
    assert typed._owned_timer_is_canonical(timer) is False
    assert calls == []
