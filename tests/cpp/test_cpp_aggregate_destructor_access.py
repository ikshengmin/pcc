"""Aggregate element D1 access: original C++17/DR obligations, locally UNRUN.

N4659 [dcl.init.aggr], [class.dtor]/12 and [class.ctor]/5.8,7:
https://timsong-cpp.github.io/cppwp/n4659/dcl.init.aggr
https://timsong-cpp.github.io/cppwp/n4659/class.dtor
https://timsong-cpp.github.io/cppwp/n4659/class.ctor
CWG2227, accepted as a DR in March 2018, corrects the aggregate rule:
https://cplusplus.github.io/CWG/issues/2227.html
Each class element's D1 is potentially invoked in the aggregate initializer's
context. This includes the final/omitted element, not just throwing paths.
Nonaggregate constructor internals retain their own class-access contexts.

Prototype-only positive controls establish syntax/access, not linkability or
destructor body proof. Separate complete original programs carry owned native
and C++17 reference obligations. Arrays, implicit D1 synthesis and exception
cleanup remain unsupported; no backend or runtime change is claimed.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.classes import (
    ClassDefinition,
    ClassType,
    empty_list_initialize,
)
from pcc.frontends.cpp.sema import Symbol
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


PRIVATE = 'class M{~M();};struct Middle{M member;~Middle();};struct O{Middle member;~O();};'
PROTECTED = 'class M{protected:~M();};struct Middle{M member;~Middle();};struct O{Middle member;~O();};'
CALLER = 'class M{~M();public:static void f();};struct Middle{M member;~Middle();};struct O{Middle member;~O();};'
NONAGGREGATE_CALLER = (
    'class M{~M();public:static void f();};class Middle{M member;public:~Middle();};'
    'struct O{Middle member;~O();};'
)

INVALID = (
    ('original-nested-empty-list', PRIVATE + 'void f(){O o{};}'),
    ('nested-copy-empty-list', PRIVATE + 'void f(){O o={};}'),
    ('nested-explicit-lists', PRIVATE + 'void f(){O o{{{}}};}'),
    ('nested-copy-explicit-lists', PRIVATE + 'void f(){O o={{{}}};}'),
    ('protected-nested-empty-list', PROTECTED + 'void f(){O o{};}'),
    ('protected-nested-explicit-lists', PROTECTED + 'void f(){O o{{{}}};}'),
    ('last-element-omitted-after-scalar', PRIVATE +
     'struct Tail{int scalar;O object;~Tail();};void f(){Tail t{1};}'),
    ('class-element-const-qualification', PRIVATE +
     'struct ConstOwner{const O object;~ConstOwner();};void f(){ConstOwner o{};}'),
    ('nested-nonaggregate-cannot-borrow-caller-access', NONAGGREGATE_CALLER + 'void M::f(){O o{};}'),
    ('nested-nonaggregate-copy-list-cannot-borrow-caller-access', NONAGGREGATE_CALLER + 'void M::f(){O o={{}};}'),
    ('default-initialization-still-uses-ctor-context', CALLER + 'void M::f(){O o;}'),
)

DECLARATION_CONTROLS = (
    ('unused-private-member-declarations', PRIVATE),
    ('direct-aggregate-borrows-real-caller-access',
     'class M{~M();public:static void f();};struct O{M member;~O();};void M::f(){O o{};}'),
    ('nested-empty-list-keeps-caller-access', CALLER + 'void M::f(){O o{};}'),
    ('nested-explicit-list-keeps-caller-access', CALLER + 'void M::f(){O o{{{}}};}'),
    ('nested-copy-list-keeps-caller-access', CALLER + 'void M::f(){O o={{{}}};}'),
    ('omitted-last-element-keeps-caller-access', CALLER +
     'struct Tail{int scalar;O object;~Tail();};void M::f(){Tail t{1};}'),
)

TRACE = 'int trace=0;struct M{int n;~M(){trace=trace*10+n;}};'
TREE = ('struct Middle{M first;M last;~Middle(){trace=trace*10+3;}};'
        'struct O{Middle member;M tail;~O(){trace=trace*10+5;}};')


def traced(initializer, expected):
    return TRACE + TREE + 'int cpp_test_entry(){{O o' + initializer + (
        ';}if(trace!=' + str(expected) + ')return 1;return 42;}')


SINGLE_PROGRAMS = (
    ('public-nested-direct-lists', traced('{{{1},{2}},{4}}', 54321)),
    ('public-nested-copy-lists', traced('={{{1},{2}},{4}}', 54321)),
    ('public-nested-empty-list', traced('{}', 50300)),
    ('public-nested-copy-empty-list', traced('={}', 50300)),
    ('public-nested-omitted-elements', traced('{{{1}}}', 50301)),
    ('aggregate-list-bypasses-deleted-default-ctor',
     'int trace=0;struct M{const int n;~M(){trace+=n;}};struct O{M member;~O(){}};'
     'int cpp_test_entry(){{O o{};}return trace+42;}'),
    ('aggregate-to-nonaggregate-constructor-transition', TRACE +
     'class Hidden{M member;public:~Hidden(){trace=trace*10+2;}};'
     'struct O{Hidden hidden;M tail;~O(){trace=trace*10+4;}};'
     'int cpp_test_entry(){{O o{{},{3}};}if(trace!=4320)return 1;return 42;}'),
    ('private-complete-object-d1-in-own-caller',
     'class M{~M(){}public:static int f(){M m{};return 42;}};'
     'int cpp_test_entry(){return M::f();}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in SINGLE_PROGRAMS)

DEFERRED = (
    ('class-array-lifetime', 'struct M{~M(){}};void f(){M values[2];}', 'cpp.unsupported.class-array-lifetime'),
    ('class-array-member', 'struct M{~M(){}};struct O{M values[2];~O(){}};', 'cpp.unsupported.class-array-lifetime'),
    ('implicit-enclosing-d1', 'struct M{~M(){}};struct O{M member;};void f(){O o{};}', 'cpp.unsupported.destructor-subobjects'),
    ('unknown-d1-effects', 'void effect();struct M{~M(){effect();}};struct O{M member;~O(){}};', 'cpp.unsupported.destructor-subobjects'),
)


@pytest.mark.parametrize('name,source', INVALID, ids=[row[0] for row in INVALID])
def test_aggregate_d1_access_rejects_original_source_before_emission(name, source, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid aggregate destructor access reached object emission')

    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + '.cpp', source),))
    assert error.value.code == 'cpp.member-access'


@pytest.mark.parametrize('name,source', DECLARATION_CONTROLS, ids=[row[0] for row in DECLARATION_CONTROLS])
def test_aggregate_d1_access_preserves_caller_and_prototype_controls(name, source):
    parser = CppParser()
    parser.parse(source)
    for class_name in ('M', 'O'):
        destructor = parser.scope.names[class_name].dtype.definition.destructor
        assert not destructor.defined
        assert not getattr(destructor, 'closed_destruction_body', False)


def test_empty_list_zero_storage_does_not_invent_aggregate_caller_contexts():
    parser = CppParser()
    parser.parse('struct Leaf{~Leaf();};struct Inner{Leaf leaf;~Inner();};'
                 'class Box{Inner inner;public:~Box();};struct Outer{Box box;~Outer();};')
    outer = parser.scope.names['Outer'].dtype
    checked = []

    class InitializerScope:
        def check_member_access(self, symbol, coord):
            checked.append(symbol)

    lowered = empty_list_initialize(outer, outer.coord, scope=InitializerScope())
    assert isinstance(lowered, c_ast.InitList)
    assert checked == [parser.scope.names['Box'].dtype.definition.destructor]


def test_aggregate_access_does_not_borrow_lexical_owner_privilege():
    # Model-level context test, not admission of nested-class source syntax.
    parser = CppParser()
    parser.parse('class M{~M();};')
    member = parser.scope.names['M'].dtype
    owner_scope = member.definition.scope
    aggregate = ClassType('Nested', ('M', 'Nested'), member.coord,
                          declaration_scope=owner_scope)
    definition = ClassDefinition(aggregate, owner_scope)
    definition.add_member(Symbol('member', 'member', member.coord, dtype=member))
    definition.finish()
    with pytest.raises(CppDiagnostic, match='cpp.member-access'):
        empty_list_initialize(aggregate, member.coord, scope=parser.scope)
    assert isinstance(empty_list_initialize(aggregate, member.coord, scope=owner_scope), c_ast.InitList)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_aggregate_access_positive_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_aggregate_access_does_not_expand_unowned_lifetimes(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code
