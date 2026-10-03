"""Sum publishes owned results before cleanup, preserving integer semantics.

Host IR/CFG and reference cases are independent of the native five-GC gate.
"""
from __future__ import annotations

import re
import textwrap

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from tests.python.test_shared_call_binding import _emit, _function
from tests.python.owned_regression_support import (
    assert_owned_program,
    assert_reference_program,
    explicit_owned_runtime,
)


EXPRESSIONS = (
    'sum(source)', 'sum(source, start)', 'sum([1, 2, 3])',
    'sum((), start)', 'sum((value for value in source))',
    'sum([1.5, 2, True], 0.5)', 'sum((), 1.5)',
)


def _source(expression, site='argument', annotation='list'):
    prefix = ('def take(*, value, later=None):\n    return value\n'
              'def fail():\n    raise ValueError("later")\n')
    if site == 'default':
        body = '    def saved(item=' + expression + '):\n        return item\n    return saved()\n'
    elif site == 'return':
        body = '    return ' + expression + '\n'
    elif site == 'discard':
        body = '    ' + expression + '\n    return None\n'
    else:
        body = '    return take(value=' + expression + (', later=fail()' if site == 'later-error' else '') + ')\n'
    return prefix + 'def probe(source: ' + annotation + ', start: int):\n' + body


@pytest.mark.parametrize('expression', EXPRESSIONS)
@pytest.mark.parametrize('site', ('argument', 'return', 'default', 'discard', 'later-error'))
def test_sum_publication_and_caller_result_sink(expression, site):
    function = _function(_emit(_source(expression, site)))
    calls = list(re.finditer(
        r'^  (%call\.slot\.runtime[^ ]+) = call [^\n]*@'
        r'(py_int_add|py_int_from_i64|py_obj_getitem|py_float_from_f64)\([^\n]*\)\n',
        function, re.M,
    ))
    assert calls
    for call in calls:
        assert function[call.end():].lstrip().startswith('store ptr ' + call.group(1) + ',')
    if site in ('argument', 'default', 'later-error'):
        assert 'sum.result.operand' not in function
    else:
        assert '@pcc_gc_take_pinned_slot(' in function
    assert '@py_tls_exc_swap_slot(' in function
    assert '@py_cpy_' not in function


@pytest.mark.parametrize('expression,annotation', (
    ('sum(source)', 'list'), ('sum(source, start)', 'tuple'),
    ('sum(source)', 'object'), ('sum((value for value in source))', 'list'),
    ('sum([1.5, 2, True], 0.5)', 'list'),
))
def test_sum_success_and_error_edges_balance_precise_root_stacks(expression, annotation):
    from tests.python.test_slot_call_lexical_roots import _emit as emit_roots
    from pcc.backend.self_backend_prepare import prepare_module_for_target
    from pcc.backend.self_backend_precise_stackmaps import build_stack_map_plans
    from pcc.backend.self_backend_x86_64_linux import _aggregate_returned_indirect
    source = _source(expression, 'later-error', annotation)
    codegen, text = emit_roots(source)
    prepared = prepare_module_for_target(text, aggregate_returned_indirect=_aggregate_returned_indirect)
    plans = build_stack_map_plans(prepared.functions, prepared.globals_, target='x86_64-linux')
    assert len(plans) == len(prepared.functions)
    assert all(flag is None and lifo for _slot, flag, lifo in codegen._slot_call_root_records)


def test_sum_original_lowerer_uses_registered_input_and_result_owners(monkeypatch):
    original = L1CodeGen._slot_call_runtime_call
    observed = []
    helpers = {'py_obj_len', 'py_obj_iter', 'py_obj_next', 'py_exc_matches',
               'py_int_from_i64', 'py_obj_getitem', 'py_int_add'}
    producers = {'py_obj_iter', 'py_obj_next', 'py_int_from_i64', 'py_obj_getitem', 'py_int_add'}

    def observe(self, name, roots, **kwargs):
        if name in helpers:
            for root in roots:
                self._slot_call_root_record(root)
            if name in producers:
                self._slot_call_root_record(kwargs['result_slot'])
            observed.append((name, len(roots)))
        return original(self, name, roots, **kwargs)

    monkeypatch.setattr(L1CodeGen, '_slot_call_runtime_call', observe)
    _emit(_source('sum(source)', annotation='object'))
    assert ('py_obj_iter', 1) in observed and ('py_obj_next', 1) in observed
    assert ('py_exc_matches', 2) in observed and ('py_int_add', 2) in observed
    observed.clear()
    _emit(_source('sum(source)', annotation='list'))
    assert ('py_obj_len', 1) in observed and ('py_obj_getitem', 2) in observed
    assert ('py_int_add', 2) in observed


def test_sum_source_literal_finishes_evaluation_before_start_and_arithmetic():
    body = _function(_emit(
        'def first() -> int:\n    return 1\ndef second() -> int:\n    return 2\n'
        'def initial() -> int:\n    return 3\ndef probe():\n'
        '    return sum([first(), second()], initial())\n'
    ))
    positions = [body.index('@user_binding_' + name + '(') for name in ('first', 'second', 'initial')]
    assert positions == sorted(positions)
    assert positions[-1] < body.index('@py_int_add(')


def test_sum_accumulator_replacement_empties_destination_after_new_owner_is_published():
    body = _function(_emit(_source('sum(source)')))
    addition = re.search(r'(%call\.slot\.runtime[^ ]+) = call [^\n]*@py_int_add\([^\n]*\)\n', body)
    assert addition
    after = body[addition.end():]
    assert after.lstrip().startswith('store ptr ' + addition.group(1) + ',')
    move = re.search(r'@pcc_gc_root_move\(ptr (%[^,]+), ptr (%[^)]+)\)', after)
    assert move
    prefix = after[:move.start()]
    assert 'ptr null)' in prefix
    assert '@pcc_gc_store_root(' in prefix


def test_sum_error_cleanup_restores_tls_after_finalizing_releases():
    body = _function(_emit(_source('sum(source)', annotation='object')))
    blocks = re.findall(r'^sum\.cleanup[^:]*:\n(.*?)(?=^[^ ;\n][^\n]*:|^})', body, re.M | re.S)
    assert blocks
    for block in blocks:
        swaps = [m.start() for m in re.finditer('@py_tls_exc_swap_slot\\(', block)]
        assert len(swaps) == 2
        clear = block.index('@py_clear_exception(')
        assert swaps[0] < clear < swaps[1]
        assert '@pcc_gc_store_root(' in block[swaps[1]:]


@pytest.mark.parametrize('initial,item', ((10**40, 7), (4, 4), (True, 2)))
def test_sum_actual_accumulator_body_survives_moving_disposal(initial, item):
    from pcc.frontends.python.codegen.numeric_builtin_lowering import NumericBuiltinLoweringMixin
    from tests.python.test_dict_constructor_slot_roots import _MovingBoundaryModel

    class Model(_MovingBoundaryModel, NumericBuiltinLoweringMixin):
        def _slot_call_runtime_call(self, runtime, roots, result_slot=None, **kwargs):
            assert runtime == 'py_int_add'
            assert result_slot in self.roots
            assert all(root in self.roots for root in roots)
            self.boundary(runtime)
            self._publish_slot_call_owned(result_slot, roots[0].current + roots[1].current)

        def _guard_cpy_value_not_null(self, current):
            assert current[0] is not None and current[1] == self.generation

        def call(self, runtime, arguments, **kwargs):
            self.boundary(runtime)
            if runtime == 'pcc_gc_store_root':
                assert str(arguments[1]) == "null"
                arguments[0].current = None
                return None
            assert runtime == 'pcc_gc_root_move'
            destination, source = arguments
            assert destination in self.roots and source in self.roots
            assert destination.current is None
            destination.current, source.current = source.current, None
            return 0

    model = Model({}, False)
    model.runtime.update({name: name for name in ('pcc_gc_store_root', 'pcc_gc_root_move')})
    accumulator = model._new_slot_call_root('accumulator')
    operand = model._new_slot_call_root('operand')
    accumulator.current, operand.current = initial, item
    model._emit_sum_add_objects(accumulator, operand)
    assert accumulator.current == initial + item
    assert operand.current == item
    assert model.roots == [accumulator, operand]
    assert model.events.index('publish:sum.next') < model.events.index('pcc_gc_store_root')
    assert model.events.index('pcc_gc_store_root') < model.events.index('pcc_gc_root_move')
    assert model._try_err_block is model._cpy_operand_cleanup_block is None


PROGRAM = textwrap.dedent('''\
    import gc
    events = []
    class Counter:
        def __init__(self, count):
            self.count = count
            self.index = 0
        def __iter__(self):
            gc.collect()
            return self
        def __next__(self):
            gc.collect()
            if self.index >= self.count:
                raise StopIteration()
            value = self.index
            self.index += 1
            return value
        def __del__(self):
            events.append('counter-disposed')
            gc.collect()
    class Failing(Counter):
        def __next__(self):
            gc.collect()
            raise ValueError('iterator-failed')
    def take(*, value, later=None):
        gc.collect()
        return value
    def mark(label, value: int) -> int:
        events.append(label)
        gc.collect()
        return value
    def fail():
        gc.collect()
        raise ValueError('later')
    def main():
        assert take(value=sum([2**70, 3, 4])) == 2**70 + 7
        assert sum((), 2**75) == 2**75
        empty_start = 1.5
        assert sum((), empty_start) == empty_start
        assert sum([mark('first', 2), mark('second', 3)], mark('start', 7)) == 12
        assert events == ['first', 'second', 'start']
        events.clear()
        assert sum(Counter(5), 100) == 110
        gc.collect()
        assert events == ['counter-disposed']
        events.clear()
        assert sum(Counter(0)) == 0
        gc.collect()
        assert events == ['counter-disposed']
        events.clear()
        try:
            sum(Failing(1))
        except ValueError as error:
            assert str(error) == 'iterator-failed'
        else:
            raise AssertionError('iterator error lost')
        gc.collect()
        assert events == ['counter-disposed']
        def saved(value=sum([2**70, 9])):
            gc.collect()
            return value
        assert saved() == 2**70 + 9
        assert saved() is saved()
        try:
            take(value=sum([2**70, 4]), later=fail())
        except ValueError as error:
            assert str(error) == 'later'
        else:
            raise AssertionError('later error lost')
        print('SUM_RESULT_OWNERS_OK')
    main()
''')


def test_sum_program_reference(tmp_path):
    assert_reference_program(PROGRAM, 'SUM_RESULT_OWNERS_OK\n', tmp_path)


def test_sum_full_native_fixture_uses_owned_sum_paths():
    text = _emit(PROGRAM)
    assert re.search(r'call [^\n]*@py_obj_iter\(', text)
    assert re.search(r'call [^\n]*@py_obj_next\(', text)
    assert re.search(r'call [^\n]*@py_int_add\(', text)
    assert not re.search(r'call [^\n]*@py_cpy_', text)


@pytest.mark.integration
@pytest.mark.parametrize('python_program_compiler', ('pcc0', 'pcc1'), indirect=True)
def test_sum_native_five_gc(python_program_compiler, request, explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params['python_program_compiler']
    assert_owned_program(PROGRAM, 'SUM_RESULT_OWNERS_OK\n', tmp_path,
                         python_program_compiler, mode, explicit_owned_runtime, capfd)
