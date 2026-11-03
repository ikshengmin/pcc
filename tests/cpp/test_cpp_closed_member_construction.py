"""Closed declared member C1/D1 chains: source-only C++17 obligations.

N4659 [class.base.init]/7,9,12,13, [class.ctor]/10, [class.dtor]/9,
[dcl.fct.default], [except.ctor]/3:
https://timsong-cpp.github.io/cppwp/n4659/class.base.init
https://timsong-cpp.github.io/cppwp/n4659/class.ctor
https://timsong-cpp.github.io/cppwp/n4659/class.dtor
https://timsong-cpp.github.io/cppwp/n4659/except.ctor

Only exact generated member C1 calls may use already-bound construction proofs.
Arguments/defaults, scalar stores and the completed body must be closed too.
This is a bounded no-unknown-call proof, not a noexcept declaration, exception
runtime or implicit special-member synthesis. Reserved placement now consumes
the same proof. Unknown body-local C1/D1 calls remain conservative boundaries.
All compiler, test, native and reference execution is locally UNRUN.
"""

from types import SimpleNamespace

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.constructors import closed_construction_body
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_member_destruction import ORIGINAL_CLOSED_MEMBER_C1
from tests.cpp.test_cpp_owned_execution import owned_only


MEMBER = ('int trace=0;struct M{int n;M(int n):n(n){trace=trace*10+n;}'
          '~M(){trace=trace*10+n+5;}};')


def observed(definition, body, expected):
    return MEMBER + definition + 'int cpp_test_entry(){' + body + (
        'if(trace!=' + str(expected) + ')return 1;return 42;}')


SINGLE_PROGRAMS = (
    ('exact-original-formerly-deferred-source', ORIGINAL_CLOSED_MEMBER_C1 +
     'int cpp_test_entry(){O o;return 42;}'),
    ('construction-and-destruction-declaration-order', observed(
        'struct O{M first;M last;O():last(2),first(1){trace=trace*10+3;}'
        '~O(){trace=trace*10+4;}};', '{O o;if(trace!=123)return 1;}', 123476)),
    ('constructor-return-retains-members', observed(
        'struct O{M first;M last;O():first(1),last(2){trace=trace*10+3;return;}'
        '~O(){trace=trace*10+4;}};', '{O o;if(trace!=123)return 1;}', 123476)),
    ('nested-constructor-return-retains-members', observed(
        'struct O{M first;M last;O():first(1),last(2){while(true){int n=3;'
        'trace=trace*10+n;return;}}~O(){trace=trace*10+4;}};',
        '{O o;if(trace!=123)return 1;}', 123476)),
    ('recursive-closed-construction-tree', observed(
        'struct Middle{M first;M last;Middle():first(1),last(2){trace=trace*10+3;}'
        '~Middle(){trace=trace*10+4;}};'
        'struct O{Middle middle;M tail;O():tail(3),middle(){trace=trace*10+5;}'
        '~O(){trace=trace*10+6;}};', '{O o;}', 1233568476)),
    ('const-class-members-retain-real-c1-d1', observed(
        'struct O{const M first;const M last;O():first(1),last(2){trace=trace*10+3;}'
        '~O(){trace=trace*10+4;}};', '{O o;}', 123476)),
    ('member-argument-side-effects-follow-declaration-order', observed(
        'int next=0;struct O{M first;int middle;M last;'
        'O():last(2),middle(++next),first(++next){trace=trace*10+3;}'
        '~O(){trace=trace*10+4;}};', '{O o;if(next!=2||o.middle!=2)return 1;}', 123476)),
    ('omitted-and-empty-member-initializers-expand-closed-defaults',
     'int trace=0;struct M{int n;M(int n=21):n(n){}~M(){trace+=n;}};'
     'struct O{M first;M last;O():last(){}~O(){}};'
     'int cpp_test_entry(){{O o;}return trace;}'),
    ('member-address-is-not-a-temporary-copy',
     'int trace=0;struct M{M*self;int n;M(int n):self(this),n(n){}'
     '~M(){if(self==this)trace+=n;}};struct O{M first;M last;O():first(20),last(22){}~O(){}};'
     'int cpp_test_entry(){{O o;}return trace;}'),
    ('explicit-member-copy-keeps-closed-proof-and-d1',
     'int trace=0;struct M{int n;M(int n):n(n){}M(const M&other):n(other.n+1){}~M(){trace+=n;}};'
     'struct O{M member;O(int n):member(n){}O(const O&other):member(other.member){}~O(){}};'
     'int cpp_test_entry(){{O first(41);O second(first);}if(trace!=83)return 1;return 42;}'),
    ('selected-overload-proof-does-not-require-every-overload-closed',
     'int trace=0;void effect(){}struct M{int n;M(int n):n(n){}'
     'M(long n):n(1){effect();}~M(){trace+=n;}};'
     'struct O{M member;O():member(42){}~O(){}};int cpp_test_entry(){{O o;}return trace;}'),
    ('unevaluated-argument-call-does-not-invent-an-effect',
     'int trace=0;int effect();struct M{int n;M(int n):n(n){}~M(){trace+=n;}};'
     'struct O{M member;O():member(sizeof(effect())){}~O(){}};'
     'int cpp_test_entry(){{O o;}return trace+38;}'),
    ('out-of-line-c1-d1-bodies-before-use',
     'int trace=0;struct M{int n;M(int);~M();};M::M(int n):n(n){}M::~M(){trace+=n;}'
     'struct O{M member;O(int);~O();};O::O(int n):member(n){}O::~O(){}'
     'int cpp_test_entry(){{O o(42);}return trace;}'),
    ('direct-list-complete-object-call-keeps-closed-proof',
     'int trace=0;struct M{int n;M(int n):n(n){}~M(){trace+=n;}};'
     'struct O{M member;O(int n):member(n){}~O(){}};int cpp_test_entry(){{O o{42};}return trace;}'),
    ('private-enclosing-c1-d1-inside-own-method',
     'int trace=0;struct M{int n;M(int n):n(n){}~M(){trace+=n;}};'
     'class O{M member;O():member(42){}~O(){}public:static void work(){O o;}};'
     'int cpp_test_entry(){O::work();return trace;}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in SINGLE_PROGRAMS)

DECLARATION_CONTROLS = (
    ('no-construction-does-not-require-c1-body', 'struct M{M();~M();};struct O{M member;O();~O();};'),
    ('no-construction-does-not-check-private-member-d1',
     'class M{~M();public:M();};struct O{M member;O();~O();};'),
)

INVALID = (
    ('private-member-d1-even-in-closed-constructor',
     'class M{~M();public:M(){}};struct O{M member;O(){}~O();};', 'cpp.member-access'),
    ('protected-member-d1-even-in-closed-constructor',
     'class M{protected:~M();public:M(){}};struct O{M member;O(){}~O();};', 'cpp.member-access'),
    ('private-selected-member-c1',
     'class M{M(){}public:~M(){}};struct O{M member;O(){}~O(){}};', 'cpp.member-access'),
    ('constructor-return-expression-remains-invalid',
     'struct M{M(){}~M(){}};struct O{M member;O(){return 1;}~O(){}};', 'cpp.constructor-return-value'),
)

CLOSED_MEMBER = 'struct M{M(int n=1){}~M(){}};'
CLOSED_OWNER = CLOSED_MEMBER + 'struct O{M member;O(int n):member(n){}~O(){}};'
ORIGINAL_CLOSED_PLACEMENT = (
    'using Size=decltype(sizeof(0));void*operator new(Size,void*)noexcept;' + CLOSED_OWNER +
    'void f(O*p){new(p) O(1);}'
)
DEFERRED = (
    ('unknown-member-c1-body-effect', 'void effect();struct M{M(){effect();}~M(){}};struct O{M member;O(){}~O(){}};'),
    ('member-c1-body-not-bound', 'struct M{M();~M(){}};struct O{M member;O(){}~O(){}};'),
    ('later-member-c1-definition-is-not-a-proof', 'struct M{M();~M(){}};struct O{M member;O(){}~O(){}};M::M(){}'),
    ('explicit-member-argument-effect', 'int effect();' + CLOSED_MEMBER + 'struct O{M member;O():member(effect()){}~O(){}};'),
    ('expanded-member-default-effect', 'int effect();struct M{M(int n=effect()){}~M(){}};struct O{M member;O(){}~O(){}};'),
    ('later-expanded-member-default-effect', 'int effect();struct M{M(int n=effect()){}~M(){}};'
     'struct O{M first;M last;O():first(1){}~O(){}};'),
    ('nested-function-default-effect-in-member-argument', 'int effect();int use(int n=effect());' + CLOSED_MEMBER +
     'struct O{M member;O():member(use()){}~O(){}};'),
    ('scalar-meminitializer-effect-after-member', 'int effect();' + CLOSED_MEMBER +
     'struct O{M member;int n;O():n(effect()){}~O(){}};'),
    ('later-trivial-member-c1-may-throw', 'void effect();' + CLOSED_MEMBER +
     'struct T{T(){effect();}};struct O{M first;T last;O(){}~O(){}};'),
    ('later-trivial-member-default-may-throw', 'int effect();' + CLOSED_MEMBER +
     'struct T{T(int n=effect()){}};struct O{M first;T last;O(){}~O(){}};'),
    ('enclosing-body-user-call-is-not-a-member-c1', 'void effect(){}' + CLOSED_MEMBER +
     'struct O{M member;O(){effect();}~O(){}};'),
    ('unreachable-user-call-retains-conservative-boundary', 'void effect();' + CLOSED_MEMBER +
     'struct O{M member;O(){if(false)effect();}~O(){}};'),
    ('body-local-c1-is-not-a-member-c1', 'struct Local{Local(){}};' + CLOSED_MEMBER +
     'struct O{M member;O(){Local local;}~O(){}};'),
    ('body-local-d1-needs-a-separate-effect-owner', 'struct Local{~Local(){}};' + CLOSED_MEMBER +
     'struct O{M member;O(){Local local;}~O(){}};'),
    ('selected-unclosed-overload', 'void effect();struct M{M(int){}M(long){effect();}~M(){}};long n=1;'
     'struct O{M member;O():member(n){}~O(){}};'),
    ('implicit-enclosing-d1-not-synthesized', CLOSED_MEMBER + 'struct O{M member;O(){}};'),
    ('implicit-member-d1-not-synthesized', CLOSED_MEMBER + 'struct Middle{M member;};struct O{Middle member;O(){}~O(){}};'),
    ('complete-object-prototype-call-has-no-body-proof', CLOSED_MEMBER +
     'struct O{M member;O();~O();};void f(){O o;}'),
    ('complete-object-argument-effects-stay-closed', 'int effect();' + CLOSED_OWNER + 'void f(){O o(effect());}'),
    ('complete-object-direct-list-preparation-effects-stay-closed', 'int effect();' + CLOSED_OWNER + 'void f(){O o{effect()};}'),
    ('complete-object-expanded-default-effects-stay-closed', 'int effect();' + CLOSED_MEMBER +
     'struct O{M member;O(int n=effect()):member(n){}~O(){}};void f(){O o;}'),
)

RETAINED_BOUNDARIES = (
    ('scalar-dmi-effect-inside-member-keeps-existing-path-gate',
     'int effect();struct M{int n=effect();M(){}~M(){}};struct O{M member;O(){}~O(){}};',
     'cpp.unsupported.default-member-initializer-path'),
    ('ignored-member-scalar-dmi-keeps-existing-path-gate',
     'int trace=0;int effect();struct M{int n=effect();M():n(42){}~M(){trace+=n;}};'
     'struct O{M member;O(){}~O(){}};int cpp_test_entry(){{O o;}return trace;}',
     'cpp.unsupported.default-member-initializer-path'),
    ('braced-member-c1-keeps-list-boundary', CLOSED_MEMBER +
     'struct O{M member;O():member{1}{}~O(){}};', 'cpp.unsupported.constructor-list-initialization'),
    ('member-array-keeps-element-lifetime-boundary', CLOSED_MEMBER +
     'struct O{M members[2];O(){}~O(){}};', 'cpp.unsupported.class-array-lifetime'),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_closed_member_construction_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,source', DECLARATION_CONTROLS, ids=[row[0] for row in DECLARATION_CONTROLS])
def test_member_construction_declarations_do_not_invent_body_proofs(name, source):
    parser = CppParser()
    parser.parse(source)
    for class_name in ('M', 'O'):
        selected = parser.scope.names[class_name].dtype.definition.constructors.overloads[0]
        assert not getattr(selected, 'closed_construction_body', False)


@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_closed_member_construction_stops_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid member construction reached emission')

    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + '.cpp', source),))
    assert error.value.code == code


@pytest.mark.parametrize('name,source', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_unclosed_member_construction_remains_explicit(name, source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == 'cpp.unsupported.destructor-subobjects'


@pytest.mark.parametrize('name,source,code', RETAINED_BOUNDARIES, ids=[row[0] for row in RETAINED_BOUNDARIES])
def test_closed_construction_does_not_expand_other_lifetime_owners(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


@pytest.mark.parametrize('initializer,closed', (('', False), (':n(42)', True)))
def test_leaf_construction_proof_visits_only_consumed_scalar_dmi(initializer, closed):
    parser = CppParser()
    parser.parse('int effect();struct M{int n=effect();M()' + initializer + '{}~M(){}};')
    selected = parser.scope.names['M'].dtype.definition.constructors.overloads[0]
    assert selected.closed_construction_body is closed


def test_nontrivial_member_initialization_requires_its_owning_proof_collector():
    from pcc.frontends.cpp.member_initialization import initialize

    parser = CppParser()
    parser.parse('struct M{M();~M();};struct O{M member;O();~O();};')
    member, = parser.scope.names['O'].dtype.definition.members
    with pytest.raises(CppDiagnostic, match='cpp.unsupported.destructor-subobjects'):
        initialize(parser, member, None, None)


def test_construction_proof_uses_exact_nodes_and_separate_metadata():
    selected = SimpleNamespace(closed_construction_body=True, closed_normal_body=False)
    known = c_ast.FuncCall(c_ast.ID('same_name'), c_ast.ExprList([c_ast.Constant('int', '1')]))
    other = c_ast.FuncCall(c_ast.ID('same_name'), c_ast.ExprList([c_ast.Constant('int', '1')]))
    assert closed_construction_body(known, {known: selected})
    assert not closed_construction_body(other, {known: selected})
    assert not closed_construction_body(known, {known: SimpleNamespace(closed_normal_body=True)})


@pytest.mark.parametrize('position', (0, 1))
def test_permitted_member_call_never_hides_address_or_argument_effects(position):
    effect = c_ast.FuncCall(c_ast.ID('effect'), None)
    arguments = [c_ast.Constant('int', '0'), c_ast.Constant('int', '1')]
    arguments[position] = effect
    member = c_ast.FuncCall(c_ast.ID('member_c1'), c_ast.ExprList(arguments))
    selected = SimpleNamespace(closed_construction_body=True)
    assert not closed_construction_body(member, {member: selected})


def test_member_constructor_body_has_no_member_cleanup_frame():
    parser = CppParser()
    parser.parse(ORIGINAL_CLOSED_MEMBER_C1)
    owner = parser.scope.names['O'].dtype.definition
    pending, = owner.inline_constructors
    call, = pending.function.body.block_items
    assert isinstance(call, c_ast.FuncCall)
    assert call.name.name.endswith('C1Ev')
    assert owner.constructors.overloads[0].closed_construction_body
    assert not owner.constructors.overloads[0].closed_normal_body
    assert parser.lifetimes.frames == [] and parser.lifetimes.controls == []
