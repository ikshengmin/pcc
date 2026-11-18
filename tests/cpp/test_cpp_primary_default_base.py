"""Actual synthesized primary-base C1, not empty/stub constructors.

The first addon run exposed retained Unsupported boundaries. Identical sources
go through host PCC/self/ELF
and G++17; external outputs never substitute for the owned lane.
"""
import hashlib
import json
import subprocess

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.virtual_functions import VPTR_FIELD
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_reference_cpp, run_executable,
    write_host_executable,
)
from tests.cpp.test_cpp_qualified_member_lookup import _host, _compiler, _owned

TARGET = 'x86_64-unknown-linux-gnu'
ORDER_SOURCE = (
    'struct B{int base_value=10;virtual int value(){return base_value;}};'
    'struct D:B{int member_value=32;int value()override{return base_value+member_value;}};'
    'int cpp_test_entry(){D obj;B*p=&obj;return p->value();}'
)
VALUE_SOURCE = (
    'struct B{int raw;virtual int value(){return raw;}};'
    'struct D:B{int marker=42;int value()override{return raw+marker;}};'
    'int cpp_test_entry(){D obj{};B*p=&obj;return p->value();}'
)
DEFAULT_SOURCE = (
    'struct B{int raw;virtual int value(){return raw;}};'
    'struct D:B{int marker=35;int value()override{return raw+marker;}};'
    'int cpp_test_entry(){D obj;obj.raw=7;B*p=&obj;return p->value();}'
)
ORIGINAL_TAIL_PADDING = (
    ('base_vptr_member', ORDER_SOURCE),
    ('value_init_zero_then_default_c1', VALUE_SOURCE),
    ('default_init_assign_before_read', DEFAULT_SOURCE),
)
# The three original sources above remain byte-identical. Their G++ success
# is reference-only: the owned layout owner rejects non-POD base tail padding.
QUALIFIED_CLASSES = (
    'struct Base{int seed=40; int base_omitted; virtual int current(){return 0;}};'
    'struct Derived:Base{int from_base=seed+2; int derived_omitted; '
    'int current()override{return from_base;}};'
)
QUALIFIED_ORDER_SOURCE = QUALIFIED_CLASSES + (
    'int cpp_test_entry(){Derived d;d.base_omitted=7;d.derived_omitted=9;'
    'Base*p=&d;if(d.base_omitted!=7||d.derived_omitted!=9||p->current()!=42)return 1;'
    'Derived v{};Base*q=&v;'
    'if(v.base_omitted!=0||v.derived_omitted!=0||q->current()!=42)return 2;return 42;}'
)
QUALIFIED_DEFAULT_SOURCE = QUALIFIED_CLASSES + (
    'int cpp_test_entry(){Derived d;d.base_omitted=7;d.derived_omitted=9;'
    'if(d.base_omitted!=7||d.derived_omitted!=9)return 1;Base*p=&d;return p->current();}'
)
QUALIFIED_VALUE_SOURCE = QUALIFIED_CLASSES + (
    'int cpp_test_entry(){Derived v{};'
    'if(v.base_omitted!=0||v.derived_omitted!=0)return 1;Base*p=&v;return p->current();}'
)
POSITIVE = (
    ('qualified_base_vptr_member', QUALIFIED_ORDER_SOURCE),
    ('qualified_default_initialization', QUALIFIED_DEFAULT_SOURCE),
    ('qualified_value_initialization', QUALIFIED_VALUE_SOURCE),
)
EXPLICIT_DELETE_BASE = 'struct B{B()=delete;virtual int value(){return 1;}};'

# Default initialization never reads raw until explicitly assigned. Testing
# indeterminate raw against a nonzero value would be undefined behavior, not an
# oracle for omitted zero-initialization. The structural test below checks the
# absent default-init zero stores independently.
DELETED_BASES = (
    ('private_constructor',
     'class B{B()=default;public:virtual int value(){return 1;}};'),
    ('const_member_implicit_deletion',
     'struct B{const int uninitialized;int pad;virtual int value(){return 1;}};'),
)
UNSUPPORTED = (
    'struct B{B(){}};struct S:B{S()=default;};',
    'struct B{B(){}virtual int value(){return 1;}};struct D:B{};',
    'int unknown();struct B{int n=unknown();virtual int value(){return 1;}};struct D:B{};',
)


def _parse(source):
    parser = CppParser(TARGET)
    tree = parser.parse(source, 'primary.cpp')
    return parser, tree


def _walk(node):
    yield node
    for _, child in node.children():
        yield from _walk(child)


def _ctor(parser, name):
    overloads = parser.scope.lookup(name).dtype.definition.constructors.overloads
    matches = [s for s in overloads if s.parameters == ()]
    assert len(matches) == 1
    return matches[0]


def test_real_base_call_precedes_derived_vptr_and_member_store():
    parser, _ = _parse(QUALIFIED_ORDER_SOURCE)
    base, derived = _ctor(parser, 'Base'), _ctor(parser, 'Derived')
    context = derived.defaulted_definition
    assert context.function is not None and base.defaulted_definition.function is not None
    assert context.construction_dependencies == (base,)
    assert derived.closed_construction_body and base.closed_construction_body

    def events(function, base_symbol=None):
        result = []
        for node in _walk(function.body):
            if isinstance(node, c_ast.FuncCall):
                assert base_symbol is not None
                assert isinstance(node.name, c_ast.ID) and node.name.name == base_symbol.c_name
                result.append(('call', base_symbol.c_name))
            if isinstance(node, c_ast.Assignment):
                assert isinstance(node.lvalue, c_ast.StructRef)
                result.append(('store', node.lvalue.field.name))
        return result

    bmembers = {m.name: m.c_name for m in parser.scope.lookup('Base').dtype.definition.members}
    dmembers = {m.name: m.c_name for m in parser.scope.lookup('Derived').dtype.definition.members}
    # Exact sequences also forbid stores to base_omitted/derived_omitted.
    assert events(base.defaulted_definition.function) == [
        ('store', VPTR_FIELD), ('store', bmembers['seed'])]
    assert events(context.function, base) == [
        ('call', base.c_name), ('store', VPTR_FIELD), ('store', dmembers['from_base'])]


def test_default_and_value_init_share_c1_but_zero_only_at_value_call_site():
    for source, zero_expected in ((QUALIFIED_DEFAULT_SOURCE, False), (QUALIFIED_VALUE_SOURCE, True)):
        parser, tree = _parse(source)
        ctor = _ctor(parser, 'Derived')
        assert not any(isinstance(n, c_ast.CompoundLiteral)
                       for n in _walk(ctor.defaulted_definition.function.body))
        entry = parser.scope.lookup('cpp_test_entry').overloads[0]
        definitions = [n for n in tree.ext if isinstance(n, c_ast.FuncDef)
                       and n.decl.name == entry.c_name]
        assert len(definitions) == 1
        zeros = [n for n in _walk(definitions[0].body)
                 if isinstance(n, c_ast.CompoundLiteral)]
        assert len(zeros) == (1 if zero_expected else 0)


@pytest.mark.parametrize('name,base', DELETED_BASES, ids=[c[0] for c in DELETED_BASES])
def test_unused_deleted_derived_valid_actual_construction_rejected(name, base):
    source = base + 'struct D:B{};'
    parser, _ = _parse(source)
    ctor = _ctor(parser, 'D')
    assert ctor.constructor_deleted
    assert ctor.defaulted_definition.function is None
    with pytest.raises(CppDiagnostic) as caught:
        _parse(source + 'int cpp_test_entry(){D obj;return 42;}')
    assert caught.value.code == 'cpp.deleted-default-constructor'


def test_protected_constructor_uses_derived_access_scope():
    _parse('struct B{protected:B()=default;public:virtual int value(){return 42;}};'
           'struct D:B{};int cpp_test_entry(){D obj;return obj.value();}')


@pytest.mark.parametrize('source', UNSUPPORTED)
def test_original_unqualified_base_contracts_remain_unsupported(source):
    with pytest.raises(CppDiagnostic) as caught:
        _parse(source)
    assert caught.value.code == 'cpp.unsupported.constructor-subobjects'


@pytest.mark.integration
@pytest.mark.parametrize('name,source', POSITIVE, ids=[c[0] for c in POSITIVE])
def test_identical_source_owned_and_gcc17(tmp_path, monkeypatch, name, source):
    _host()
    compiler = _compiler()
    units = ((name + '.cpp', source),)
    actual, errors = {}, {}
    for lane in ('owned', 'gcc'):
        try:
            if lane == 'owned':
                artifact = _owned(units, monkeypatch)
                assert artifact.owner == 'host-python/pcc-cpp/pcc-self/pcc-elf'
                binary = tmp_path / 'owned'
                write_host_executable(artifact, binary)
            else:
                binary = compile_reference_cpp(units, tmp_path / 'gcc', compiler=compiler)
                assert (tmp_path / 'gcc' / units[0][0]).read_bytes() == source.encode()
            actual[lane] = run_executable(binary)
        except Exception as error:
            errors[lane] = error
    (tmp_path / 'observations.json').write_text(json.dumps({
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'executions': {lane: {'returncode': r.returncode, 'stdout_hex': r.stdout.hex(),
                              'stderr_hex': r.stderr.hex()} for lane, r in actual.items()},
        'errors': {lane: repr(e) for lane, e in errors.items()},
    }, indent=2) + '\n')
    if errors:
        raise next(iter(errors.values()))
    for result in actual.values():
        assert_exact(result, Execution(42, b'\x2a\x00\x00\x00', b''))


@pytest.mark.integration
@pytest.mark.parametrize('name,base', DELETED_BASES, ids=[c[0] for c in DELETED_BASES])
def test_identical_deleted_constructor_sources_gcc17(tmp_path, name, base):
    _host()
    source = base + 'struct D:B{};'
    for suffix, text, expected in (
        ('unused', source, 0),
        ('used', source + 'int cpp_test_entry(){D obj;return 42;}', 1),
    ):
        path = tmp_path / (name + '-' + suffix + '.cpp')
        path.write_bytes(text.encode())
        result = subprocess.run([_compiler(), '-std=c++17', '-pedantic-errors',
                                 '-fsyntax-only', str(path)], capture_output=True, timeout=10)
        assert path.read_bytes() == text.encode()
        assert result.returncode == expected and result.stdout == b''
        if expected:
            assert b'error:' in result.stderr
            assert b'internal compiler error' not in result.stderr.lower()


@pytest.mark.parametrize('name,source', ORIGINAL_TAIL_PADDING,
                         ids=[c[0] for c in ORIGINAL_TAIL_PADDING])
def test_original_tail_padding_source_remains_truthfully_unsupported(name, source):
    with pytest.raises(CppDiagnostic) as caught:
        _parse(source)
    assert caught.value.code == 'cpp.unsupported.base-classes'


@pytest.mark.integration
@pytest.mark.parametrize('name,source', ORIGINAL_TAIL_PADDING,
                         ids=[c[0] for c in ORIGINAL_TAIL_PADDING])
def test_original_tail_padding_same_bytes_gcc_reference_only(tmp_path, name, source):
    _host()
    units = ((name + '.cpp', source),)
    binary = compile_reference_cpp(units, tmp_path / 'gcc', compiler=_compiler())
    assert (tmp_path / 'gcc' / units[0][0]).read_bytes() == source.encode()
    assert_exact(run_executable(binary), Execution(42, b'\x2a\x00\x00\x00', b''))


@pytest.mark.parametrize('used', (False, True))
def test_original_explicit_delete_preserves_earlier_capability_diagnostic(used):
    source = EXPLICIT_DELETE_BASE + 'struct D:B{};'
    if used:
        source += 'int cpp_test_entry(){D obj;return 42;}'
    with pytest.raises(CppDiagnostic) as caught:
        _parse(source)
    assert caught.value.code == 'cpp.unsupported.inline-member-functions'


@pytest.mark.integration
@pytest.mark.parametrize('used', (False, True))
def test_original_explicit_delete_gcc_reference_only(tmp_path, used):
    _host()
    source = EXPLICIT_DELETE_BASE + 'struct D:B{};'
    if used:
        source += 'int cpp_test_entry(){D obj;return 42;}'
    path = tmp_path / 'explicit-delete.cpp'
    path.write_bytes(source.encode())
    result = subprocess.run([_compiler(), '-std=c++17', '-pedantic-errors',
                             '-fsyntax-only', str(path)], capture_output=True, timeout=10)
    assert path.read_bytes() == source.encode()
    assert result.returncode == (1 if used else 0) and result.stdout == b''
    if used:
        assert b'error:' in result.stderr
        assert b'internal compiler error' not in result.stderr.lower()
