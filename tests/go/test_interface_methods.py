"""Compiler-only Go method sets; no table ABI or dispatch implementation.

https://go.dev/ref/spec#Method_sets
https://go.dev/ref/spec#Interface_types
https://go.dev/ref/spec#Selectors
https://go.dev/ref/spec#Type_identity
The complete dynamic original has a reference-only execution obligation below;
its model checks do not claim PCC runtime execution. All execution is CI-only.
"""
from pathlib import Path
from types import SimpleNamespace
import os

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.interface_methods import (
    basic_interface, member_key, method_set, require_implements,
)
from pcc.frontends.go.records import (
    DefinedType, Field, InterfaceMethod, InterfaceType, MethodSignature, StructType,
)
from pcc.frontends.go.syntax import Token
from pcc.frontends.go.types import PointerType, Results, SliceType
from tests.go.owned_execution import Execution, compile_reference, run


TOKEN = Token('name', 'I', 'original.go', 1, 1)


def method(owner, name='M', *, pointer=False, params=(), result='int', variadic=False):
    receiver = PointerType(owner) if pointer else owner
    function = SimpleNamespace(params=[(TOKEN, receiver)] + [(TOKEN, p) for p in params],
        result=result, receiver=(TOKEN, receiver), variadic=variadic)
    owner.methods[name] = function
    return function


def interface(name='M', *, package='', params=(), result='int', variadic=False):
    return InterfaceType((InterfaceMethod(name, package, MethodSignature(params, result, variadic)),))


def test_pointer_membership_is_not_addressable_call_permission():
    owner = DefinedType('T', 'p', 'int')
    value = method(owner, 'V')
    pointer = method(owner, 'P', pointer=True)
    assert method_set(owner).lookup('V').implementation is value
    assert method_set(owner).lookup('P') is None
    assert method_set(PointerType(owner)).lookup('P').implementation is pointer
    assert method_set(PointerType(owner)).lookup('V').indirect
    require_implements(PointerType(owner), interface('P'), TOKEN)
    with pytest.raises(GoDiagnostic, match='missing method P'):
        require_implements(owner, interface('P'), TOKEN)
    defined_pointer = DefinedType('P', 'p', PointerType(owner))
    assert method_set(defined_pointer).selections == ()
    assert method_set(PointerType(interface())).selections == ()


def test_signatures_use_nominal_types_results_and_variadicness():
    first = DefinedType('T', 'p', 'int')
    second = DefinedType('T', 'p', 'int')
    owner = DefinedType('S', 'p', 'int')
    method(owner, params=(first,), result=Results(('int', first)))
    require_implements(owner, interface(params=(first,), result=Results(('int', first))), TOKEN)
    with pytest.raises(GoDiagnostic, match='wrong type'):
        require_implements(owner, interface(params=(second,), result=Results(('int', first))), TOKEN)
    method(owner, params=(SliceType('int'),), variadic=True)
    with pytest.raises(GoDiagnostic, match='wrong type'):
        require_implements(owner, interface(params=(SliceType('int'),)), TOKEN)


def test_private_member_identity_uses_import_path_not_spelling():
    assert member_key('M', 'a') == member_key('M', 'b')
    assert member_key('m', 'a') != member_key('m', 'b')
    owner = DefinedType('T', 'path/a', 'int')
    method(owner, 'm')
    require_implements(owner, interface('m', package='path/a'), TOKEN)
    with pytest.raises(GoDiagnostic, match='missing method m'):
        require_implements(owner, interface('m', package='path/b'), TOKEN)
    combined = basic_interface((), (interface('m', package='a'), interface('m', package='b')), TOKEN)
    assert len(combined.methods) == 2


def test_embedding_preserves_distinct_paths_shallow_hiding_and_field_conflicts():
    leaf = DefinedType('Leaf', 'p', 'int')
    implementation = method(leaf)
    left = DefinedType('Left', 'p', StructType((Field('Leaf', leaf, 'p', True),)))
    right = DefinedType('Right', 'p', StructType((Field('Leaf', leaf, 'p', True),)))
    diamond = DefinedType('Diamond', 'p', StructType((Field('Left', left, 'p', True), Field('Right', right, 'p', True))))
    assert method_set(left).lookup('M').field_path == (0,)
    assert method_set(diamond).lookup('M') is None
    assert member_key('M') in method_set(diamond).blocked
    shallow = DefinedType('Shallow', 'p', StructType((Field('Diamond', diamond, 'p', True), Field('Leaf', leaf, 'p', True))))
    selection = method_set(shallow).lookup('M')
    assert selection.field_path == (1,) and selection.implementation is implementation
    hidden = DefinedType('Hidden', 'p', StructType((Field('Left', left, 'p', True), Field('M', 'int', 'p'))))
    assert method_set(hidden).lookup('M') is None


def test_recursive_pointer_embedding_terminates_without_inventing_methods():
    owner = DefinedType('Node', 'p')
    owner.underlying = StructType((Field('Node', PointerType(owner), 'p', True),))
    implementation = method(owner)
    selected = method_set(owner).lookup('M')
    assert selected.implementation is implementation and selected.field_path == ()


def test_embedded_pointer_and_interface_methods_retain_adjustment_obligations():
    leaf = DefinedType('Leaf', 'p', 'int')
    method(leaf, pointer=True)
    value_embed = DefinedType('Value', 'p', StructType((Field('Leaf', leaf, 'p', True),)))
    pointer_embed = DefinedType('Pointer', 'p', StructType((Field('Leaf', PointerType(leaf), 'p', True),)))
    assert method_set(value_embed).lookup('M') is None
    assert method_set(PointerType(value_embed)).lookup('M').indirect
    assert method_set(pointer_embed).lookup('M').indirect
    iface = interface()
    via_interface = DefinedType('Box', 'p', StructType((Field('I', iface, 'p', True),)))
    selected = method_set(via_interface).lookup('M')
    assert selected.implementation is None and selected.indirect and selected.field_path == (0,)


def test_interface_merge_preserves_explicit_uniqueness_and_embedded_compatibility():
    required = interface()
    assert basic_interface((), (required, required), TOKEN) == required
    with pytest.raises(GoDiagnostic, match='duplicate'):
        basic_interface(required.methods * 2, (), TOKEN)
    with pytest.raises(GoDiagnostic, match='conflicting'):
        basic_interface((), (required, interface(result='int64')), TOKEN)


DYNAMIC = Path(__file__).parent / 'fixtures/interface_source/dynamic_contract.go'


def test_complete_dynamic_original_remains_explicitly_unimplemented(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unimplemented dynamic program reached IR'))
    with pytest.raises(UnsupportedGo):
        compile_go(DYNAMIC.read_text(), DYNAMIC.name)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_exact_dynamic_contract_has_official_reference_only_execution(tmp_path):
    original = DYNAMIC.read_text()
    reference = compile_reference(((DYNAMIC.name, original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (reference.parent / DYNAMIC.name).read_bytes() == DYNAMIC.read_bytes()
    assert run(reference) == Execution(0, b'', b'')
