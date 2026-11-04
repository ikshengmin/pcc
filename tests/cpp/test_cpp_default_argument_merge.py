"""Default-merge state and original inputs, without changing any diagnostic.

The production change avoids one Boolean augmented-bitwise lowering shape.
These host unit/owned-C++ obligations do not qualify Python self compilation,
native pcc1 or Stage1. The unchanged failing Python module is retained in the
source packet; the Python Boolean-bitwise lowering defect remains open.
"""

from itertools import product
from types import SimpleNamespace

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp import default_arguments
from pcc.frontends.cpp.sema import Scope, Symbol
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_default_arguments import CASES
from tests.cpp.test_cpp_owned_execution import owned_only


TRAILING_MESSAGE = 'a parameter after a default argument requires a default'
REDEFINITION_MESSAGE = 'a default argument cannot be specified twice'
SCOPE_MESSAGE = 'default arguments across different redeclaration scopes are not implemented'

# Two pre-existing C++ inputs are reused byte-for-byte. New programs observe
# merging and call-time effects; none rewrites the failing Python input.
LEGACY_CASES = dict(CASES)
PROGRAMS = (
    ('three-step-union', (('union.cpp',
        'int f(int a,int b,int c=3);int f(int a,int b=9,int c);'
        'int f(int a=30,int b,int c);int f(int a,int b,int c){return a+b+c;}'
        'int cpp_test_entry(){return f();}'),)),
    ('no-new-default-call-time-effects', (('effects.cpp',
        'int count=0;int next(){count=count+1;return 21;}'
        'int f(int x=next());int f(int x);int f(int x){return x;}'
        'int cpp_test_entry(){int a=f();int b=f();if(count!=2)return 1;return a+b;}'),)),
    ('completed-class-default-order', (('class.cpp',
        'struct S{static int f(int a=first(),int b=second()){return a+b;}'
        'static int first(){return 20;}static int second(){return 22;}};'
        'int cpp_test_entry(){return S::f();}'),)),
    ('original-same-scope-union', (('original_union.cpp', LEGACY_CASES['same-scope-union']),)),
    ('original-tinyxml-setstr', (('original_setstr.cpp',
        LEGACY_CASES['tinyxml-original-setstr-declaration']),)),
)
INVALID = (
    ('original-trailing', 'int f(int x=1,int y);',
     'cpp.default-argument-trailing', TRAILING_MESSAGE),
    ('original-redefinition', 'int f(int x=1);int f(int x=1);',
     'cpp.default-argument-redefinition', REDEFINITION_MESSAGE),
    ('interior-hole', 'int f(int a=1,int b,int c=3);',
     'cpp.default-argument-trailing', TRAILING_MESSAGE),
    ('redeclaration-does-not-fill-hole',
     'int f(int a,int b,int c=3);int f(int a=1,int b,int c);',
     'cpp.default-argument-trailing', TRAILING_MESSAGE),
)


def record(scope, name):
    result = default_arguments.DefaultArgument((), 'int', Scope(scope), (), name)
    result.expression = object()
    return result


def symbol(scope, prior):
    result = Symbol('f', 'function', 'declaration-coordinate',
                    parameters=('int',) * len(prior))
    result.owner = scope
    result.declaration_scopes = [scope]
    result.default_arguments = tuple(prior)
    result.default_argument_scope = scope
    return result


def bind_spy(monkeypatch, fail_on=None, failure=None):
    calls = []
    original_bind = default_arguments.bind

    def observed(parser, item):
        calls.append((parser, item))
        if item is fail_on:
            raise failure
        return original_bind(parser, item)

    monkeypatch.setattr(default_arguments, 'bind', observed)
    return calls


LAYOUTS = tuple(product(('none', 'prior', 'new'), repeat=3))


@pytest.mark.parametrize('layout', LAYOUTS, ids=['-'.join(row) for row in LAYOUTS])
def test_merge_all_three_slot_layouts_keep_identity_and_diagnostic_order(layout, monkeypatch):
    scope = Scope()
    prior = tuple(record(scope, 'prior-%d' % i) if state == 'prior' else None
                  for i, state in enumerate(layout))
    incoming = tuple(record(scope, 'new-%d' % i) if state == 'new' else None
                     for i, state in enumerate(layout))
    expected = tuple(old if old is not None else new for old, new in zip(prior, incoming))
    occupied = [i for i, item in enumerate(expected) if item is not None]
    # Suffix occupancy is the independent language invariant, not a copied
    # Boolean-accumulator implementation of the production loop.
    valid = not occupied or all(item is not None for item in expected[min(occupied):])
    function = symbol(scope, prior)
    original_tuple = function.default_arguments
    parser = object()
    calls = bind_spy(monkeypatch)
    if valid:
        default_arguments.merge(parser, function, incoming, scope)
        assert all(actual is wanted for actual, wanted in zip(function.default_arguments, expected))
        wanted_calls = [item for item in incoming if item is not None]
        assert len(calls) == len(wanted_calls)
        assert all(owner is parser and actual is wanted
                   for (owner, actual), wanted in zip(calls, wanted_calls))
    else:
        with pytest.raises(CppDiagnostic) as caught:
            default_arguments.merge(parser, function, incoming, scope)
        assert caught.value.code == 'cpp.default-argument-trailing'
        assert caught.value.message == TRAILING_MESSAGE
        assert caught.value.coord is function.coord
        assert function.default_arguments is original_tuple
        assert calls == []
    # Existing mutation order is preserved even on the trailing diagnostic:
    # newly supplied records acquire their function before suffix validation.
    for item in incoming:
        if item is not None:
            assert item.function is function
    for item in prior:
        if item is not None:
            assert item.function is None


def test_no_defaults_returns_before_scope_checks_or_metadata_publication(monkeypatch):
    scope = Scope()
    function = Symbol('f', 'function', 'original-coordinate', parameters=('int', 'int'))
    function.owner = Scope()
    function.declaration_scopes = [Scope(), Scope()]
    calls = bind_spy(monkeypatch)
    default_arguments.merge(object(), function, (None, None), scope)
    assert not hasattr(function, 'default_arguments')
    assert not hasattr(function, 'default_argument_scope')
    assert calls == []


@pytest.mark.parametrize('complete', (False, True))
def test_class_merge_preserves_pending_indices_or_immediate_binding(complete, monkeypatch):
    scope = Scope(Scope(), kind='class')
    old = record(scope, 'old')
    pending = [old]
    definition = SimpleNamespace(complete=complete, default_arguments=pending)
    scope.class_type = SimpleNamespace(definition=definition)
    first, second = record(scope, 'first'), record(scope, 'second')
    function = symbol(scope, (None, None, old))
    parser = object()
    calls = bind_spy(monkeypatch)
    default_arguments.merge(parser, function, (first, second, None), scope)
    assert all(actual is wanted for actual, wanted in
               zip(function.default_arguments, (first, second, old)))
    if complete:
        assert len(pending) == 1 and pending[0] is old
        assert first.declaration_index is second.declaration_index is None
        assert len(calls) == 2
        assert calls[0][0] is parser and calls[0][1] is first
        assert calls[1][0] is parser and calls[1][1] is second
    else:
        assert len(pending) == 3
        assert pending[0] is old and pending[1] is first and pending[2] is second
        assert (first.declaration_index, second.declaration_index) == (1, 2)
        assert calls == []


@pytest.mark.parametrize('duplicate_index', (0, 1))
def test_redefinition_keeps_first_error_and_existing_partial_record_effects(duplicate_index, monkeypatch):
    scope = Scope()
    old = record(scope, 'old')
    prior = [None, None]
    prior[duplicate_index] = old
    incoming = (record(scope, 'new-0'), record(scope, 'new-1'))
    function = symbol(scope, prior)
    original_tuple = function.default_arguments
    calls = bind_spy(monkeypatch)
    with pytest.raises(CppDiagnostic) as caught:
        default_arguments.merge(object(), function, incoming, scope)
    assert caught.value.code == 'cpp.default-argument-redefinition'
    assert caught.value.message == REDEFINITION_MESSAGE
    assert caught.value.coord is incoming[duplicate_index].coord
    assert function.default_arguments is original_tuple
    assert calls == []
    for index, item in enumerate(incoming):
        assert item.function is (function if index < duplicate_index else None)


@pytest.mark.parametrize('conflict', ('default-scope', 'owner', 'redeclaration-scopes'))
def test_scope_diagnostic_still_precedes_any_record_mutation(conflict, monkeypatch):
    scope = Scope()
    item = record(scope, 'new')
    function = symbol(scope, (None,))
    if conflict == 'default-scope':
        function.default_argument_scope = Scope()
    elif conflict == 'owner':
        function.owner = Scope()
    else:
        function.declaration_scopes.append(Scope())
    before = function.default_arguments
    calls = bind_spy(monkeypatch)
    with pytest.raises(CppDiagnostic) as caught:
        default_arguments.merge(object(), function, (item,), scope)
    assert caught.value.code == 'cpp.unsupported.default-argument-scopes'
    assert caught.value.message == SCOPE_MESSAGE
    assert caught.value.coord is function.coord
    assert function.default_arguments is before
    assert item.function is None
    assert calls == []


@pytest.mark.parametrize('failure_index', (0, 1))
def test_binding_exception_preserves_identity_order_and_published_defaults(failure_index, monkeypatch):
    scope = Scope()
    incoming = (record(scope, 'first'), record(scope, 'second'))
    function = symbol(scope, (None, None))
    failure = RuntimeError('original binding failure')
    parser = object()
    calls = bind_spy(monkeypatch, incoming[failure_index], failure)
    with pytest.raises(RuntimeError) as caught:
        default_arguments.merge(parser, function, incoming, scope)
    assert caught.value is failure
    assert len(calls) == failure_index + 1
    assert all(owner is parser and actual is wanted
               for (owner, actual), wanted in zip(calls, incoming))
    assert function.default_argument_scope is scope
    assert all(actual is wanted for actual, wanted in zip(function.default_arguments, incoming))
    assert all(item.function is function for item in incoming)


class ObservableRecord(default_arguments.DefaultArgument):
    def __init__(self, scope, failure=None):
        super().__init__((), 'int', Scope(scope), (), 'observable-coordinate')
        self.expression = object()
        self.truth_calls = 0
        self.failure = failure

    def __bool__(self):
        self.truth_calls += 1
        if self.failure is not None:
            raise self.failure
        return True

    def __eq__(self, other):
        raise AssertionError('merge identity checks must not invoke equality')


@pytest.mark.parametrize('origin', ('prior', 'new'))
def test_accumulator_adds_no_record_truth_or_equality_evaluations(origin, monkeypatch):
    scope = Scope()
    item = ObservableRecord(scope)
    prior = (None, item if origin == 'prior' else None)
    incoming = (None, item if origin == 'new' else None)
    function = symbol(scope, prior)
    calls = bind_spy(monkeypatch)
    default_arguments.merge(object(), function, incoming, scope)
    assert item.truth_calls == 1  # only the unchanged initial any(records/prior)
    assert function.default_arguments[1] is item
    assert len(calls) == (1 if origin == 'new' else 0)


def test_initial_truth_exception_is_not_swallowed_or_moved(monkeypatch):
    scope = Scope()
    failure = RuntimeError('original truth failure')
    item = ObservableRecord(scope, failure)
    function = symbol(scope, (None,))
    before = function.default_arguments
    calls = bind_spy(monkeypatch)
    with pytest.raises(RuntimeError) as caught:
        default_arguments.merge(object(), function, (item,), scope)
    assert caught.value is failure
    assert item.truth_calls == 1
    assert function.default_arguments is before
    assert item.function is None
    assert calls == []


@pytest.mark.parametrize('name,source,code,message', INVALID, ids=[row[0] for row in INVALID])
def test_original_and_added_invalid_cpp_inputs_keep_exact_diagnostics(name, source, code, message):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source, name + '.cpp')
    assert caught.value.code == code
    assert caught.value.message == message
    assert str(caught.value.coord).startswith(name + '.cpp:')


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_merge_original_artifacts_are_byte_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)
