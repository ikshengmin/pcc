"""Real C++ consumer of the shared compiler-session instantiation worklist.

C++ keeps deduction, overload selection, dependent lookup and diagnostics; the
common owner retains identity, placeholders, FIFO discovery and failure state.
No generic interpreter, runtime registration, or emitted object-header change.
"""
import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.sema import Symbol
from pcc.support.instantiation import InstanceRegistry, InstanceState
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record


def parse(source):
    parser = CppParser('x86_64-unknown-linux-gnu')
    result = parser.parse(source, 'registry.cpp')
    assert isinstance(parser.instance_registry, InstanceRegistry)
    return parser, result


def test_shared_helper_is_an_actual_compiler_cache_source_dependency(monkeypatch):
    import builtins
    import io
    import os
    from pcc.support import instantiation
    from pcc.frontends.c.evaluator.c_evaluator import (
        _compiler_cache_tracked_files, _host_compiler_cache_fingerprint,
    )
    helper = os.path.abspath(instantiation.__file__)
    assert helper in _compiler_cache_tracked_files()
    before = _host_compiler_cache_fingerprint()
    original_open = builtins.open
    with original_open(helper, 'rb') as stream:
        changed = stream.read() + b'\n# modeled compiler source change\n'
    def changed_open(path, mode='r', *args, **kwargs):
        if path == helper and mode == 'rb':
            return io.BytesIO(changed)
        return original_open(path, mode, *args, **kwargs)
    # Model changed helper bytes at the actual fingerprint read boundary;
    # never mutate frozen input files or claim cached descriptor API repair.
    monkeypatch.setattr(builtins, 'open', changed_open)
    assert _host_compiler_cache_fingerprint() != before


def test_speculative_signature_has_no_separate_writable_cache():
    symbol = Symbol('unselected', 'function', None)
    assert not symbol.instances
    with pytest.raises(TypeError):
        symbol.instances[('int',)] = object()


def test_selected_calls_share_one_registry_placeholder():
    parser, tree = parse('template<class T>T id(T x){return x;}'
                         'int cpp_test_entry(){return id(17)+id(25);}')
    entries = tuple(parser.instance_registry.entries.values())
    assert len(entries) == len(parser.pending_instances) == 1
    entry = entries[0]
    origin = parser.scope.lookup('id').overloads[0]
    assert entry.key == (origin, ('int',))
    assert origin.instances[('int',)] is entry.value
    assert entry.result in tree.ext
    assert entry.state is InstanceState.COMPLETE
    with pytest.raises(TypeError):
        origin.instances[('long',)] = entry.value


def test_unselected_viable_template_never_registers_work():
    parser, _ = parse('template<class T>int f(T x){return 1;}int f(int x){return 42;}'
                      'int cpp_test_entry(){return f(1);}')
    assert not parser.instance_registry.entries
    assert not parser.pending_instances
    origin = next(item for item in parser.scope.lookup('f').overloads if item.template_parameters)
    assert not origin.instances


def test_work_discovered_during_callback_is_drained_in_order():
    parser, tree = parse('template<class T>T inner(T x){return x+x;}'
                         'template<class T>T outer(T x){return inner(x);}'
                         'int cpp_test_entry(){return outer(21);}')
    entries = tuple(parser.instance_registry.entries.values())
    assert [entry.key[0].name for entry in entries] == ['outer', 'inner']
    assert all(entry.state is InstanceState.COMPLETE for entry in entries)
    assert [item for item in tree.ext if isinstance(item, c_ast.FuncDef)][-2:] == [entry.result for entry in entries]


def test_recursive_call_uses_in_progress_placeholder(monkeypatch):
    seen = []
    original = InstanceRegistry.request
    def recording(self, key, value, context=None):
        previous = self.entries.get(key)
        if previous is not None:
            seen.append((previous.state, previous.value))
        return original(self, key, value, context=context)
    monkeypatch.setattr(InstanceRegistry, 'request', recording)
    parser, _ = parse('template<class T>T count(T n){if(n==0)return T(42);return count(n-1);}'
                      'int cpp_test_entry(){return count(3);}')
    entry = next(iter(parser.instance_registry.entries.values()))
    assert (InstanceState.IN_PROGRESS, entry.value) in seen
    assert len(parser.instance_registry.entries) == 1


def test_declaration_identity_and_full_arguments_distinguish_entries():
    parser, _ = parse('template<class T>T f(T x){return x;}'
                      'template<class T>T f(T x,T y){return x+y;}'
                      'int cpp_test_entry(){return f(1)+f(20,20)+int(f(1L));}')
    entries = tuple(parser.instance_registry.entries.values())
    assert len(entries) == 3
    assert entries[0].key[0] is not entries[1].key[0]
    assert entries[0].key[1] == entries[1].key[1] == ('int',)
    assert entries[0].key[0] is entries[2].key[0]
    assert entries[2].key[1] == ('long',)


def test_body_failure_is_sticky_original_diagnostic_and_parse_can_restart():
    parser = CppParser('x86_64-unknown-linux-gnu')
    source = ('template<class T>int fail(T x){return x%2;}'
              'int cpp_test_entry(){return fail(1.5);}')
    with pytest.raises(CppDiagnostic) as error:
        parser.parse(source, 'failed-registry.cpp')
    assert error.value.code == 'cpp.invalid-operands'
    assert 'while instantiating' in error.value.message
    registry = parser.instance_registry
    entry = next(iter(registry.entries.values()))
    assert entry.state is InstanceState.FAILED and entry.failure is error.value
    with pytest.raises(CppDiagnostic) as repeated:
        registry.get(entry.key)
    assert repeated.value is error.value
    with pytest.raises(CppDiagnostic) as repeated:
        registry.drain(lambda item: pytest.fail('failed session processed more work'))
    assert repeated.value is error.value
    parser.parse('int cpp_test_entry(){return 42;}', 'fresh-registry.cpp')
    assert parser.instance_registry is not registry
    assert not parser.instance_registry.entries


CASES = (
    ('same-args-reused', 'template<class T>T id(T x){return x;}int cpp_test_entry(){return id(17)+id(25);}'),
    ('nested-discovery', 'template<class T>T inner(T x){return x+x;}template<class T>T outer(T x){return inner(x);}'
     'int cpp_test_entry(){return outer(21);}'),
    ('recursive-placeholder', 'template<class T>T count(T n){if(n==0)return T(42);return count(n-1);}'
     'int cpp_test_entry(){return count(3);}'),
    ('full-arguments', 'template<class T>T id(T x){return x;}int cpp_test_entry(){return id(17)+int(id(25L));}'),
    ('canonical-forward-identity', 'template<class T>T id(T);int cpp_test_entry(){return id(42);}'
     'template<class U>U id(U value){return value;}'),
    ('unselected-template', 'template<class T>int f(T x){return 1;}int f(int x){return 42;}'
     'int cpp_test_entry(){return f(1);}'),
)


@pytest.mark.parametrize('name,source', CASES, ids=[row[0] for row in CASES])
def test_shared_registry_preserves_deterministic_products(name, source):
    units = ((name+'.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES, ids=[row[0] for row in CASES])
def test_shared_registry_cpp_execution_matches_explicit_reference(name, source, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name+'.cpp', source),), 42)
