"""First-declaration defaulted D1 source obligations; all execution UNRUN.

N4659 [dcl.fct.def.default]/1,5, [class.dtor]/5-9,12, [class.base.init]/12,
[basic.def.odr], [dcl.init.aggr] and CWG2227:
https://timsong-cpp.github.io/cppwp/n4659/dcl.fct.def.default
https://timsong-cpp.github.io/cppwp/n4659/class.dtor
https://timsong-cpp.github.io/cppwp/n4659/class.base.init

Defaulting/deletion/triviality are independent. Only evaluated use requests
a body, which reuses the existing exact cleanup-call proof and D1 emission.
No implicit nontrivial D1, template defaulting, later defaulting, class-array
lifetime or exception owner is synthesized. Native obligations are direct
x86_64 ELF, not native pcc1 or merged-stackmap platform qualification.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.support.instantiation import InstanceState
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only
from tests.cpp.test_cpp_placement_new import DECLARATION


# Exact formerly rejected original, retained unchanged from both destructor
# test modules. The original TU remains separate from its authored observer.
ORIGINAL_DECLARATION = 'struct S{~S()=default;};'
TRACE = 'int trace=0;struct M{int n;~M(){trace=trace*10+n;}};'
UNUSED_MISSING = 'struct M{~M();};struct S{M member;~S()=default;};'
UNEVALUATED_MISSING = UNUSED_MISSING + 'void inspect(S*p){using V=decltype(p->~S());V*q=nullptr;}'
IMPLICIT_DELETED = 'class L{~L()=default;};struct M{L leaf;};struct S{M member;~S()=default;};'
# Exact independent-review counterexample. The class default binds only after
# class completion, but its unevaluated D1 use must already observe deletion.
DELETED_CLASS_DEFAULT_ARGUMENT = 'class M{~M()=default;};struct S{M member;~S()=default;void f(S*p,int n=sizeof(decltype(p->~S())*));};'
UNEVALUATED_CLASS_DEFAULT_ARGUMENT = ('struct M{~M();};struct S{M member;~S()=default;'
                                    'void f(S*p,int n=sizeof(decltype(p->~S())*));};')


CASES = (
    ('scalar-defaulted-d1-keeps-aggregate', 'struct S{int n;~S()=default;};'
     'int cpp_test_entry(){S s{42};return s.n;}'),
    ('scalar-array-members-remain-trivial', 'struct S{int values[2];~S()=default;};'
     'int cpp_test_entry(){S s{{20,22}};return s.values[0]+s.values[1];}'),
    ('const-object-and-member', 'struct S{const int n;~S()=default;};'
     'int cpp_test_entry(){const S s{42};return s.n;}'),
    ('repeated-trivial-defaulted-use', 'struct S{int n;~S()=default;};'
     'int cpp_test_entry(){S a{20};S b{22};return a.n+b.n;}'),
    ('defaulted-c1-and-d1', 'struct S{S()=default;int n=42;~S()=default;};'
     'int cpp_test_entry(){S s;return s.n;}'),
    ('trivial-defaulted-member-needs-no-implicit-d1-body', 'struct M{int n;~M()=default;};struct O{M member;};'
     'int cpp_test_entry(){O o{{42}};return o.member.n;}'),
    ('trivial-defaulted-member-c1-without-enclosing-d1', 'struct M{int n;M(int n):n(n){}~M()=default;};'
     'struct O{M member;O():member(42){}};int cpp_test_entry(){O o;return o.member.n;}'),
    ('reverse-member-order', TRACE + 'struct S{M first;M last;~S()=default;};'
     'int cpp_test_entry(){{S s{{1},{2}};}if(trace!=21)return 1;return 42;}'),
    ('return-member-cleanup', TRACE + 'struct S{M member;~S()=default;};'
     'int work(){S s{{2}};return 40;}int cpp_test_entry(){int value=work();return value+trace;}'),
    ('loop-cleanup-uses-defaulted-d1', TRACE + 'struct S{M member;~S()=default;};'
     'int cpp_test_entry(){int i=0;while(i<2){++i;S s{{i}};if(i==1)continue;break;}'
     'if(trace!=12)return 1;return 42;}'),
    ('nested-defaulted-member-chain', TRACE + 'struct Middle{M first;M last;~Middle()=default;};'
     'struct S{Middle middle;M tail;~S()=default;};'
     'int cpp_test_entry(){{S s{{{1},{2}},{3}};}if(trace!=321)return 1;return 42;}'),
    ('defaulted-chain-as-user-d1-member', TRACE + 'struct Middle{M member;~Middle()=default;};'
     'struct S{Middle middle;~S(){trace=trace*10+2;}};'
     'int cpp_test_entry(){{S s{{{1}}};}if(trace!=21)return 1;return 42;}'),
    ('defaulted-chain-as-body-local', TRACE + 'struct Local{M member;~Local()=default;};'
     'struct S{M member;~S(){Local local{{2}};trace=trace*10+3;return;}};'
     'int cpp_test_entry(){{S s{{1}};}if(trace!=321)return 1;return 42;}'),
    ('closed-member-c1-with-defaulted-owner-d1', 'int result=0;'
     'struct M{int n;M(int n):n(n){}~M(){result+=n;}};'
     'struct S{M member;S(int n):member(n){}~S()=default;};'
     'int cpp_test_entry(){{S s(42);}return result;}'),
    ('private-defaulted-d1-from-own-member', 'class S{~S()=default;public:int n;'
     'static int value(){S s;s.n=42;return s.n;}};int cpp_test_entry(){return S::value();}'),
    ('unused-missing-member-body-no-relocation', UNUSED_MISSING + 'int cpp_test_entry(){return 42;}'),
    ('unused-unknown-member-effects-not-bound', 'void effect(){}struct M{~M(){effect();}};'
     'struct S{M member;~S()=default;};int cpp_test_entry(){return 42;}'),
    ('unused-implicit-nontrivial-member-keeps-declaration', 'struct Leaf{~Leaf(){}};struct M{Leaf leaf;};'
     'struct S{M member;~S()=default;};int cpp_test_entry(){return 42;}'),
    ('unused-deleted-defaulted-d1-is-valid', IMPLICIT_DELETED + 'int cpp_test_entry(){return 42;}'),
    ('late-member-definition-before-selected-use', 'int result=0;struct M{int n;~M();};'
     'struct S{M member;~S()=default;};M::~M(){result+=n;}'
     'int cpp_test_entry(){{S s{{42}};}return result;}'),
    ('unevaluated-explicit-defaulted-d1-needs-no-body', UNEVALUATED_MISSING + 'int cpp_test_entry(){return 42;}'),
    ('unevaluated-class-default-argument-needs-no-body', UNEVALUATED_CLASS_DEFAULT_ARGUMENT +
     'int cpp_test_entry(){return 42;}'),
)

CROSS = 'extern int result;struct M{int n;~M(){result+=n;}};struct S{M member;~S()=default;};'
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES) + (
    ('exact-formerly-rejected-defaulted-original', (
        ('original.cpp', ORIGINAL_DECLARATION),
        ('observer.cpp', ORIGINAL_DECLARATION + 'int cpp_test_entry(){S s;return 42;}'),
    )),
    ('defaulted-d1-external-odr-across-tus', (
        ('provider.cpp', CROSS + 'int result=0;void work(){S s{{17}};}'),
        ('entry.cpp', CROSS + 'void work();int cpp_test_entry(){work();{S s{{25}};}return result;}'),
    )),
)

PLACEMENT_PROGRAMS = (
    ('placement-explicit-defaulted-d1-chain', (('placement.cpp', DECLARATION +
     'int result=0;struct M{int n;M(int n):n(n){}~M(){result+=n;}};'
     'struct S{M member;S(int n):member(n){}~S()=default;};'
     'int cpp_test_entry(){long raw[16];S*p=new(raw)S(42);p->~S();return result;}'),)),
)

INVALID = (
    ('duplicate-defaulted-d1', 'struct S{~S()=default;~S()=default;};', 'cpp.redeclaration'),
    ('out-of-class-redefinition', 'struct S{~S()=default;};S::~S(){}', 'cpp.redefinition'),
    ('private-own-defaulted-d1', 'class S{~S()=default;};void f(S*p){p->~S();}', 'cpp.member-access'),
    ('private-defaulted-d1-unevaluated', 'class S{~S()=default;};void f(S*p){using V=decltype(p->~S());}', 'cpp.member-access'),
    ('deleted-defaulted-d1-at-explicit-use', 'class M{~M()=default;};struct S{M member;~S()=default;};'
     'void f(S*p){p->~S();}', 'cpp.invalid-destructor'),
    ('deleted-defaulted-d1-unevaluated', IMPLICIT_DELETED + 'void f(S*p){using V=decltype(p->~S());}', 'cpp.invalid-destructor'),
    ('deleted-defaulted-d1-in-class-default-argument', DELETED_CLASS_DEFAULT_ARGUMENT, 'cpp.invalid-destructor'),
    ('deleted-defaulted-d1-declared-after-class-default-argument', 'class M{~M()=default;};'
     'struct S{M member;void f(S*p,int n=sizeof(decltype(p->~S())*));~S()=default;};', 'cpp.invalid-destructor'),
    ('implicit-trivial-member-deletion-propagates', IMPLICIT_DELETED + 'void f(S*p){p->~S();}', 'cpp.invalid-destructor'),
    ('implicit-trivial-member-deletion-in-user-d1', 'class L{~L()=default;};struct M{L leaf;};'
     'struct S{M member;~S(){}};', 'cpp.invalid-destructor'),
    ('deleted-member-in-constructor-context', 'class L{~L()=default;public:L(){}};'
     'struct M{M();L leaf;~M()=default;};struct S{M member;S(){}~S(){}};', 'cpp.invalid-destructor'),
    ('private-trivial-member-in-aggregate-caller', 'class M{~M()=default;};'
     'struct S{M member;~S();};void f(){S s{};}', 'cpp.member-access'),
)

DEFERRED = (
    ('later-defaulting', 'struct S{~S();};S::~S()=default;', 'cpp.unsupported.inline-member-functions'),
    ('deleted-declaration', 'struct S{~S()=delete;};', 'cpp.unsupported.inline-member-functions'),
    ('defaulted-template', 'template<class T>struct S{~S()=default;};', 'cpp.unsupported.member-templates'),
    ('explicit-exception-specification', 'struct S{~S()noexcept=default;};', 'cpp.unsupported.exceptions'),
    ('internal-linkage-defaulting', 'namespace{struct S{~S()=default;};}', 'cpp.unsupported.class-linkage'),
    ('member-body-unavailable-at-use', UNUSED_MISSING + 'void f(){S s;}M::~M(){}', 'cpp.unsupported.destructor-subobjects'),
    ('member-unknown-effects-at-use', 'void effect();struct M{~M(){effect();}};'
     'struct S{M member;~S()=default;};void f(){S s;}', 'cpp.unsupported.destructor-subobjects'),
    ('implicit-nontrivial-member-use', 'struct L{~L(){}};struct M{L leaf;};'
     'struct S{M member;~S()=default;};void f(S*p){p->~S();}', 'cpp.unsupported.destructor-subobjects'),
    ('class-array-lifetime', 'struct M{~M()=default;};struct S{M members[2];~S()=default;};', 'cpp.unsupported.class-array-lifetime'),
)


@pytest.mark.parametrize('name,units', PROGRAMS + PLACEMENT_PROGRAMS,
                         ids=[row[0] for row in PROGRAMS + PLACEMENT_PROGRAMS])
def test_defaulted_d1_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED,
                         ids=[row[0] for row in INVALID + DEFERRED])
def test_defaulted_d1_invalid_or_unowned_stops_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid or unsupported defaulted D1 reached emission')

    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + '.cpp', source),))
    assert error.value.code == code


def test_original_defaulted_declaration_has_no_body_or_instance():
    parser = CppParser()
    tree = parser.parse(ORIGINAL_DECLARATION)
    symbol = parser.scope.names['S'].dtype.definition.destructor
    assert symbol.destructor_defaulted and not symbol.destructor_user_provided
    assert symbol.destructor_trivial and not symbol.destructor_deleted
    assert symbol.defaulted_destructor_definition.function is None
    assert not any(isinstance(node, c_ast.FuncDef) for node in tree.ext)
    assert not parser.instance_registry.entries


@pytest.mark.parametrize('source', (UNUSED_MISSING, UNEVALUATED_MISSING, IMPLICIT_DELETED,
                                  UNEVALUATED_CLASS_DEFAULT_ARGUMENT))
def test_unused_or_unevaluated_defaulted_d1_does_not_request_member_body(source):
    parser = CppParser()
    parser.parse(source)
    symbol = parser.scope.names['S'].dtype.definition.destructor
    assert symbol.defaulted_destructor_definition.function is None
    assert not parser.instance_registry.entries


def test_deletion_and_triviality_are_independent_through_implicit_members():
    parser = CppParser()
    parser.parse(IMPLICIT_DELETED)
    symbol = parser.scope.names['S'].dtype.definition.destructor
    assert symbol.destructor_deleted and symbol.destructor_trivial


def test_defaulted_member_body_is_requested_once_with_external_odr():
    parser = CppParser()
    tree = parser.parse(TRACE + 'struct S{M member;~S()=default;};void f(){S a{{1}};S b{{2}};}')
    symbol = parser.scope.names['S'].dtype.definition.destructor
    pending = symbol.defaulted_destructor_definition
    definitions = [node for node in tree.ext if isinstance(node, c_ast.FuncDef) and node.decl.name == symbol.c_name]
    assert definitions == [pending.function]
    assert pending.function.decl.definition_linkage == 'external_odr'
    assert pending.declaration.definition_linkage == 'external'
    assert symbol.closed_destruction_body and not symbol.closed_normal_body
    entries = list(parser.instance_registry.entries.values())
    assert len(entries) == 1 and entries[0].context is pending
    assert entries[0].state is InstanceState.COMPLETE
    assert parser.lifetimes.frames == [] and parser.lifetimes.cleanup_destructors is None


def test_trivial_defaulted_member_needs_access_but_no_cleanup_body():
    parser = CppParser()
    parser.parse('struct M{~M()=default;};struct S{M member;};void f(){S s;}')
    symbol = parser.scope.names['M'].dtype.definition.destructor
    assert symbol.defaulted_destructor_definition.function is None
    assert not parser.instance_registry.entries


def test_defaulted_d1_request_can_join_active_worklist_once():
    from pcc.frontends.cpp.default_destructors import emit_definition, request_definition

    parser = CppParser()
    parser.parse(ORIGINAL_DECLARATION)
    symbol = parser.scope.names['S'].dtype.definition.destructor
    driver = object()
    parser.instance_registry.request(driver, driver)

    def emit(entry):
        if entry.key is driver:
            request_definition(parser, symbol, symbol.coord)
            request_definition(parser, symbol, symbol.coord)
            return None
        return emit_definition(parser, entry.context)

    emitted = parser.instance_registry.drain(emit)
    assert emitted == [None, symbol.defaulted_destructor_definition.function]
    assert len(parser.instance_registry.entries) == 2


def test_lazy_defaulted_d1_still_requires_external_odr_contract(monkeypatch):
    from pcc.backend import definition_linkage

    monkeypatch.setattr(definition_linkage, 'supports_external_odr_function_definitions', lambda target: False)
    CppParser().parse(ORIGINAL_DECLARATION)
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(ORIGINAL_DECLARATION + 'void f(){S s;}')
    assert error.value.code == 'cpp.unsupported.inline-member-functions'
