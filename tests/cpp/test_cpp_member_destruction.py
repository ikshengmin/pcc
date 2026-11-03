"""Explicit member D1s: authored C++17 CI obligations, locally UNRUN.

N4659 [class.dtor]/8-9, [class.base.init]/12, [dcl.init.aggr],
[class.ctor]/5.8,7, [stmt.jump]/2 and [stmt.return]/3:
https://timsong-cpp.github.io/cppwp/n4659/class.dtor
https://timsong-cpp.github.io/cppwp/n4659/class.base.init
https://timsong-cpp.github.io/cppwp/n4659/dcl.init.aggr
https://timsong-cpp.github.io/cppwp/n4659/class.ctor

Only explicit D1s with source-proven closed cleanup chains are admitted.
Unknown-effect member C1 construction, implicit D1 synthesis, unknown effects,
exception cleanup, arrays, bases and virtual destruction remain deferred.
Owned native/reference obligations use fixed-target direct x86_64 ELF; this
is not native pcc1, platform merged-stackmap or whole-project acceptance.

The exact StrPair destructor excerpt is TinyXML2 11.0.0 tinyxml2.cpp:152-155,
revision 9148bdf719e997d1f474be6bcc7943881046dba1, Lee Thomason, Zlib.
Original notice/identity: projects/tinyxml2-11.0.0/{LICENSE.txt,pcc-fixture.json}.
Its Reset call deliberately remains an unsupported effect, not a stubbed
positive. Surrounding declarations are explicitly PCC-authored adaptations.
"""

import hashlib

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


TRACE_MEMBER = 'int trace=0;struct M{int n;~M(){trace=trace*10+n;}};'
PAIR = 'struct O{M first;M last;~O(){trace=trace*10+3;}};'


def traced(definition, body, expected):
    return TRACE_MEMBER + definition + 'int cpp_test_entry(){' + body + (
        'if(trace!=' + str(expected) + ')return 1;return 42;}')


SINGLE_PROGRAMS = (
    ('body-then-reverse-members', traced(PAIR, '{O o{{1},{2}};}', 321)),
    ('default-initialized-members', traced(PAIR, '{O o;o.first.n=1;o.last.n=2;}', 321)),
    ('empty-list-member-value-initialization', traced(PAIR, '{O o{};}', 300)),
    ('body-locals-before-members', traced(
        'struct O{M first;M last;~O(){M local{4};trace=trace*10+3;}};',
        '{O o{{1},{2}};}', 3421)),
    ('early-return-cleans-members-once', traced(
        'struct O{M first;M last;~O(){M local{4};trace=trace*10+3;return;}};',
        '{O o{{1},{2}};}', 3421)),
    ('conditional-return-before-later-local', traced(
        'struct O{M first;bool early;~O(){M before{2};if(early)return;M after{3};}};',
        '{O o{{1},true};}{O o{{4},false};}', 21324)),
    ('return-through-loop-cleans-locals-before-members', traced(
        'struct O{M member;~O(){M outer{2};while(true){M inner{3};return;}}};',
        '{O o{{1}};}', 321)),
    ('break-and-continue-do-not-destroy-members', traced(
        'struct O{M member;~O(){int i=0;while(i<2){++i;M local{2};'
        'if(i==1)continue;break;}trace=trace*10+3;}};',
        '{O o{{1}};}', 2231)),
    ('switch-break-keeps-member-frame', traced(
        'struct O{M member;~O(){switch(1){case 1:{M local{2};break;}}trace=trace*10+3;}};',
        '{O o{{1}};}', 231)),
    ('recursive-explicit-d1-tree', traced(
        'struct Middle{M first;M last;~Middle(){trace=trace*10+3;}};'
        'struct Outer{Middle middle;M tail;~Outer(){trace=trace*10+5;}};',
        '{Outer o{{{1},{2}},{4}};}', 54321)),
    ('recursive-default-initialization-with-public-d1', traced(
        'struct Middle{M first;M last;~Middle(){trace=trace*10+3;}};'
        'struct Outer{Middle middle;M tail;~Outer(){trace=trace*10+5;}};',
        '{Outer o;o.middle.first.n=1;o.middle.last.n=2;o.tail.n=4;}', 54321)),
    ('private-d1-default-construction-in-owner',
     'class Leaf{~Leaf(){}public:static int work(){Leaf value;return 42;}};'
     'int cpp_test_entry(){return Leaf::work();}'),
    ('nonaggregate-empty-list-public-member-d1',
     'int trace=0;struct M{int n;~M(){trace+=n+21;}};'
     'class Middle{M member;public:~Middle(){}};struct O{Middle member;~O(){}};'
     'int cpp_test_entry(){{O o{};}return trace+21;}'),
    ('nonaggregate-copy-list-public-member-d1',
     'int trace=0;struct M{int n;~M(){trace+=n+21;}};'
     'class Middle{M member;public:~Middle(){}};struct O{Middle member;~O(){}};'
     'int cpp_test_entry(){{O o={};}return trace+21;}'),
    ('aggregate-empty-list-bypasses-deleted-default-constructor',
     'int trace=0;struct M{const int n;~M(){trace+=n;}};struct O{M member;~O(){}};'
     'int cpp_test_entry(){{O o{};}return trace+42;}'),
    ('independent-outer-local-order', traced(PAIR, '{O first{{1},{2}};O last{{4},{5}};}', 354321)),
    ('const-member-and-mixed-trivial-fields', traced(
        'struct Empty{};struct O{const M first;int scalar;Empty empty;M last;~O(){}};',
        '{O o{{1},0,{}, {2}};}', 21)),
    ('private-field-public-member-destructor', traced(
        'class O{M member;public:~O(){}static void work(){O o;o.member.n=2;}};',
        'O::work();', 2)),
    ('out-of-line-member-and-owner-d1',
     'int trace=0;struct M{int n;~M();};M::~M(){trace=trace*10+n;}'
     'struct O{M first;M last;~O();};O::~O(){trace=trace*10+3;}'
     'int cpp_test_entry(){{O o{{1},{2}};}if(trace!=321)return 1;return 42;}'),
    ('actual-member-address-not-copy',
     'struct M{M*self;int*p;~M(){if(self==this)*p+=21;}};'
     'struct O{M first;M last;~O(){}};int cpp_test_entry(){int result=0;'
     '{O o;o.first.self=&o.first;o.last.self=&o.last;o.first.p=&result;o.last.p=&result;}return result;}'),
    ('return-value-before-outer-member-cleanup', TRACE_MEMBER + PAIR +
     'int work(){O o{{1},{2}};return trace+42;}'
     'int cpp_test_entry(){int result=work();if(trace!=321)return 1;return result;}'),
    ('body-local-with-its-own-member-cleanup', traced(
        'struct Local{M member;~Local(){trace=trace*10+3;}};'
        'struct O{M member;~O(){Local local{{2}};trace=trace*10+4;}};',
        '{O o{{1}};}', 4321)),
)

CROSS_DECLARATIONS = 'extern int trace;struct M{int n;~M();};struct O{M member;~O();};'
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in SINGLE_PROGRAMS) + (
    ('member-d1-across-translation-units', (
        ('definition.cpp', CROSS_DECLARATIONS + 'int trace=0;M::~M(){trace+=n;}O::~O(){}'),
        ('entry.cpp', CROSS_DECLARATIONS + 'int cpp_test_entry(){{O o{{42}};}return trace;}'),
    )),
)

STRPAIR_DESTRUCTOR = 'StrPair::~StrPair()\r\n{\r\n    Reset();\r\n}\r\n'
STRPAIR_SHA256 = 'a57ee0c74413af6f03762f867f8704b0bb4c1f3fa08560b02017f2fbd649d88b'
STRPAIR_BOUNDARY = ('class StrPair{public:~StrPair();void Reset();};\n' + STRPAIR_DESTRUCTOR +
                    'struct Owner{StrPair value;~Owner(){}};')

NESTED_PRIVATE_DECLARATIONS = (
    'class M{~M();};struct Middle{M member;~Middle();};struct O{Middle member;~O();};'
)
NESTED_PROTECTED_DECLARATIONS = (
    'class M{protected:~M();};struct Middle{M member;~Middle();};struct O{Middle member;~O();};'
)
NESTED_PUBLIC_DECLARATIONS = (
    'struct M{~M();};struct Middle{M member;~Middle();};struct O{Middle member;~O();};'
)
NONAGGREGATE_PRIVATE_DECLARATIONS = (
    'class M{~M();};class Middle{M member;public:~Middle();};struct O{Middle member;~O();};'
)
NONAGGREGATE_PROTECTED_DECLARATIONS = (
    'class M{protected:~M();};class Middle{M member;public:~Middle();};struct O{Middle member;~O();};'
)
NONAGGREGATE_PUBLIC_DECLARATIONS = (
    'struct M{~M();};class Middle{M member;public:~Middle();};struct O{Middle member;~O();};'
)
DECLARATION_CONTROLS = (
    ('nested-private-d1-declarations-without-construction', NESTED_PRIVATE_DECLARATIONS),
    ('nested-protected-d1-declarations-without-construction', NESTED_PROTECTED_DECLARATIONS),
    ('nested-public-d1-implicit-construction-without-bodies', NESTED_PUBLIC_DECLARATIONS + 'void f(){O o;}'),
    ('nonaggregate-private-d1-declarations-without-construction', NONAGGREGATE_PRIVATE_DECLARATIONS),
    ('nonaggregate-public-d1-empty-list-without-bodies', NONAGGREGATE_PUBLIC_DECLARATIONS + 'void f(){O o{};}'),
)

# Exact previously deferred source, now a positive declaration/body obligation.
ORIGINAL_CLOSED_MEMBER_C1 = 'struct M{M(){}~M(){}};struct O{M member;O(){}~O(){}};'

INVALID = (
    ('private-member-d1-inline', 'class M{~M(){}};struct O{M member;~O(){}};', 'cpp.member-access'),
    ('private-member-d1-outside', 'class M{~M(){}};struct O{M member;~O();};O::~O(){}', 'cpp.member-access'),
    ('private-member-d1-implicit-construction', 'class M{~M(){}};struct O{M member;~O();};void f(){O o;}', 'cpp.member-access'),
    ('nested-private-member-d1-implicit-construction', NESTED_PRIVATE_DECLARATIONS + 'void f(){O o;}', 'cpp.member-access'),
    ('nested-protected-member-d1-implicit-construction', NESTED_PROTECTED_DECLARATIONS + 'void f(){O o;}', 'cpp.member-access'),
    ('nonaggregate-private-member-d1-default', NONAGGREGATE_PRIVATE_DECLARATIONS + 'void f(){O o;}', 'cpp.member-access'),
    ('nonaggregate-private-member-d1-direct-empty-list', NONAGGREGATE_PRIVATE_DECLARATIONS + 'void f(){Middle o{};}', 'cpp.member-access'),
    ('nonaggregate-private-member-d1-nested-empty-list', NONAGGREGATE_PRIVATE_DECLARATIONS + 'void f(){O o{};}', 'cpp.member-access'),
    ('nonaggregate-private-member-d1-nested-copy-list', NONAGGREGATE_PRIVATE_DECLARATIONS + 'void f(){O o={};}', 'cpp.member-access'),
    ('nonaggregate-protected-member-d1-nested-empty-list', NONAGGREGATE_PROTECTED_DECLARATIONS + 'void f(){O o{};}', 'cpp.member-access'),
    ('implicit-constructor-cannot-borrow-callers-private-access',
     'class M{~M();public:static void f();};struct Middle{M member;~Middle();};'
     'void M::f(){Middle o;}', 'cpp.member-access'),
    ('empty-list-constructor-cannot-borrow-callers-private-access',
     'class M{~M();public:static void f();};class Middle{M member;public:~Middle();};'
     'void M::f(){Middle o{};}', 'cpp.member-access'),
    ('private-outer-d1-at-use', 'struct M{~M(){}};class O{M member;~O(){}};void f(){O o;}', 'cpp.member-access'),
    ('return-value-in-member-owning-d1', 'struct M{~M(){}};struct O{M member;~O(){return 1;}};', 'cpp.destructor-return-value'),
)

DEFERRED = (
    ('implicit-default-parentheses-declare-function', NONAGGREGATE_PRIVATE_DECLARATIONS + 'void f(){O o();}', 'cpp.unsupported.class-initialization'),
    ('implicit-outer-d1', 'struct M{~M(){}};struct O{M member;};void f(){O o;}', 'cpp.unsupported.destructor-subobjects'),
    ('implicit-intermediate-d1', 'struct M{~M(){}};struct Middle{M member;};struct O{Middle member;~O(){}};', 'cpp.unsupported.destructor-subobjects'),
    ('member-d1-definition-not-available', 'struct M{~M();};struct O{M member;~O(){}};M::~M(){}', 'cpp.unsupported.destructor-subobjects'),
    ('unknown-member-d1-call', 'void effect();struct M{~M(){effect();}};struct O{M member;~O(){}};', 'cpp.unsupported.destructor-subobjects'),
    ('unknown-outer-d1-call', 'void effect();struct M{~M(){}};struct O{M member;~O(){effect();}};', 'cpp.unsupported.destructor-subobjects'),
    ('user-call-to-same-closed-d1-is-not-synthetic-cleanup', 'struct M{~M(){}};'
     'struct O{M member;M*other;~O(){other->~M();}};', 'cpp.unsupported.destructor-subobjects'),
    ('unknown-body-local-d1-call', 'void effect();struct Local{~Local(){effect();}};struct M{~M(){}};'
     'struct O{M member;~O(){Local local;}};', 'cpp.unsupported.destructor-subobjects'),
    ('body-local-constructor-call', 'struct Local{Local(){}};struct M{~M(){}};struct O{M member;~O(){Local local;}};', 'cpp.unsupported.destructor-subobjects'),
    ('aggregate-initializer-call-after-member', 'int effect();struct M{~M(){}};struct O{M member;int n;~O(){}};'
     'void f(){O o{{},effect()};}', 'cpp.unsupported.destructor-subobjects'),
    ('aggregate-initializer-default-argument-call', 'int effect();int use(int n=effect());'
     'struct M{~M(){}};struct O{M member;int n;~O(){}};void f(){O o{{},use()};}', 'cpp.unsupported.destructor-subobjects'),
    ('original-strpair-reset-effect', STRPAIR_BOUNDARY, 'cpp.unsupported.destructor-subobjects'),
    ('member-array', 'struct M{~M(){}};struct O{M members[2];~O(){}};', 'cpp.unsupported.class-array-lifetime'),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_member_destructor_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_member_cleanup_uses_field_addresses_in_reverse_declaration_order():
    parser = CppParser()
    parser.parse(TRACE_MEMBER + PAIR)
    definition = parser.scope.names['O'].dtype.definition
    pending, = definition.inline_methods
    body, last, first = pending.function.body.block_items
    assert isinstance(body, c_ast.Assignment)
    for call, name in ((last, 'last'), (first, 'first')):
        assert isinstance(call, c_ast.FuncCall) and call.name.name == '_ZN1MD1Ev'
        address, = call.args.exprs
        assert isinstance(address, c_ast.UnaryOp) and address.op == '&'
        assert isinstance(address.expr, c_ast.StructRef) and address.expr.type == '->'
        assert address.expr.field.name == name
    assert definition.destructor.closed_destruction_body
    assert not definition.destructor.closed_normal_body
    assert parser.lifetimes.frames == [] and parser.lifetimes.controls == []
    assert parser.lifetimes.cleanup_destructors is None


def test_member_d1_prototype_does_not_require_or_invent_a_body_proof():
    parser = CppParser()
    parser.parse('class M{~M();};struct O{M member;~O();};')
    destructor = parser.scope.names['O'].dtype.definition.destructor
    assert not destructor.defined
    assert not getattr(destructor, 'closed_destruction_body', False)


def test_original_closed_member_c1_source_is_preserved_as_positive():
    parser = CppParser()
    parser.parse(ORIGINAL_CLOSED_MEMBER_C1)
    owner = parser.scope.names['O'].dtype.definition
    assert owner.constructors.overloads[0].closed_construction_body
    assert not owner.constructors.overloads[0].closed_normal_body


@pytest.mark.parametrize('name,source', DECLARATION_CONTROLS, ids=[row[0] for row in DECLARATION_CONTROLS])
def test_default_construction_access_does_not_require_destructor_bodies(name, source):
    parser = CppParser()
    parser.parse(source)
    for name in ('M', 'Middle', 'O'):
        destructor = parser.scope.names[name].dtype.definition.destructor
        assert not destructor.defined
        assert not getattr(destructor, 'closed_destruction_body', False)


def test_exact_upstream_strpair_destructor_bytes_remain_an_explicit_boundary():
    assert hashlib.sha256(STRPAIR_DESTRUCTOR.encode()).hexdigest() == STRPAIR_SHA256
    assert STRPAIR_DESTRUCTOR in STRPAIR_BOUNDARY


@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_member_destruction_stops_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid member destruction reached object emission')

    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + '.cpp', source),))
    assert error.value.code == code


@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_unowned_member_destruction_domains_remain_explicit(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code
