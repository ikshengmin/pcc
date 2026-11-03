"""Closed composite placement/explicit D1 obligations, all locally UNRUN.

N4659 [expr.new]/17-18, [new.delete.placement], [class.base.init]/13,
[class.dtor]/9, [basic.life]/8:
https://timsong-cpp.github.io/cppwp/n4659/expr.new
https://timsong-cpp.github.io/cppwp/n4659/new.delete.placement
https://timsong-cpp.github.io/cppwp/n4659/class.base.init
https://timsong-cpp.github.io/cppwp/n4659/class.dtor
https://timsong-cpp.github.io/cppwp/n4659/basic.life

No allocator, implicit special member, array lifetime, virtual/base dispatch or
unwind owner is introduced. Reference execution explicitly force-includes real
<new>; original program TU bytes match, standard-header closures do not.
"""

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_closed_member_construction import ORIGINAL_CLOSED_PLACEMENT
from tests.cpp.test_cpp_member_construction import ORIGINAL_PLACEMENT
from tests.cpp.test_cpp_owned_execution import owned_only
from tests.cpp.test_cpp_placement_new import DECLARATION


LEAF = ('int trace=0;struct M{M*self;int n;M(int n):self(this),n(n){trace=trace*10+n;}'
        '~M(){if(self!=this)trace=-1000;else trace=trace*10+n+5;}};')
OWNER = ('struct O{M first;M last;O():last(2),first(1){trace=trace*10+3;}'
         '~O(){trace=trace*10+4;}};')
SUM_OWNER = ('int destroyed=0;struct M{int n;M(int n):n(n){}~M(){destroyed+=n;}};'
             'struct O{M member;O(int n):member(n){}~O(){}};')

PRESERVED_ORIGINALS = (
    ('original-trivial-placement-subobject', ORIGINAL_PLACEMENT),
    ('original-closed-placement-subobject', ORIGINAL_CLOSED_PLACEMENT),
)

CASES = (
    ('c1-declaration-order-d1-reverse-order', LEAF + OWNER +
     'int cpp_test_entry(){long long raw[32];O*p=new(raw)O;'
     'if(trace!=123||p->first.self!=&p->first||p->last.self!=&p->last)return 1;'
     'p->~O();if(trace!=123476)return 2;return 42;}'),
    ('constructor-return-keeps-placement-members-live', LEAF +
     'struct O{M first;M last;O():first(1),last(2){trace=trace*10+3;return;}'
     '~O(){trace=trace*10+4;return;}};'
     'int cpp_test_entry(){long long raw[32];O*p=new(raw)O;'
     'if(trace!=123)return 1;p->~O();if(trace!=123476)return 2;return 42;}'),
    ('recursive-composite-chain', LEAF + OWNER +
     'struct Outer{O middle;M tail;Outer():tail(3),middle(){trace=trace*10+5;}'
     '~Outer(){trace=trace*10+6;}};'
     'int cpp_test_entry(){long long raw[32];Outer*p=new(raw)Outer;'
     'if(trace!=12335)return 1;p->~Outer();if(trace!=1233568476)return 2;return 42;}'),
    ('const-complete-and-member-subobjects', LEAF +
     'struct O{const M first;const M last;O():first(1),last(2){trace=trace*10+3;}'
     '~O(){trace=trace*10+4;}};'
     'int cpp_test_entry(){long long raw[32];const O*p=new(raw)const O;'
     'if(trace!=123)return 1;p->~O();if(trace!=123476)return 2;return 42;}'),
    ('no-automatic-placement-d1', SUM_OWNER +
     'int work(){long long raw[32];O*p=new(raw)O(42);return p->member.n;}'
     'int cpp_test_entry(){int n=work();if(destroyed!=0)return 1;return n;}'),
    ('storage-and-arrow-receiver-evaluated-once', SUM_OWNER +
     'int stores=0;int receives=0;int order=0;'
     'void*storage(void*p){stores+=1;order=order*10+1;return p;}'
     'O*receiver(O*p){receives+=1;return p;}'
     'int cpp_test_entry(){long long raw[32];'
     'O*p=new(storage(raw))O(order=order*10+2);if(stores!=1||order!=12)return 1;'
     'receiver(p)->~O();if(receives!=1||destroyed!=12)return 2;return 42;}'),
    ('effectful-dot-receiver-evaluated-once', SUM_OWNER +
     'int receives=0;O*receiver(O*p){receives+=1;return p;}'
     'int cpp_test_entry(){long long raw[32];O*p=new(raw)O(42);'
     '(*receiver(p)).~O();if(receives!=1)return 1;return destroyed;}'),
    ('same-type-transparent-composite-replacement', SUM_OWNER +
     'int work(){O o(1);o.~O();O*p=new(&o)O(41);'
     'if(p!=&o||o.member.n!=41||destroyed!=1)return 0;return p->member.n;}'
     'int cpp_test_entry(){int n=work();if(n!=41||destroyed!=42)return 1;return 42;}'),
    ('bound-out-of-line-c1-d1-before-placement',
     'int destroyed=0;struct M{int n;M(int);~M();};'
     'M::M(int n):n(n){}M::~M(){destroyed+=n;}'
     'struct O{M member;O(int);~O();};O::O(int n):member(n){}O::~O(){}'
     'int cpp_test_entry(){long long raw[32];O*p=new(raw)O(42);p->~O();return destroyed;}'),
    ('trivial-member-c1-with-declared-outer-d1',
     'int destroyed=0;struct M{int n;M(int n=42):n(n){}};'
     'struct O{M member;O(){}~O(){destroyed+=member.n;}};'
     'int cpp_test_entry(){long long raw[32];O*p=new(raw)O();p->~O();return destroyed;}'),
    ('placement-does-not-demand-private-outer-d1-access',
     'struct M{int n;M(int n):n(n){}~M(){}};'
     'class O{~O(){}public:M member;O(int n):member(n){}};'
     'int cpp_test_entry(){long long raw[32];O*p=new(raw)O(42);return p->member.n;}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', DECLARATION + source),)) for name, source in CASES) + tuple(
    (name, ((name + '.cpp', source +
             'int cpp_test_entry(){long long raw[32];void*storage=raw;'
             'O*p=static_cast<O*>(storage);f(p);return 42;}'),))
    for name, source in PRESERVED_ORIGINALS
)

DEFERRED = (
    ('unknown-trivial-member-c1-body',
     'void effect();struct M{M(){effect();}};struct O{M member;O(){}};'
     'void f(void*p){new(p)O;}', 'cpp.unsupported.placement-constructor-effects'),
    ('unbound-trivial-member-c1',
     'struct M{M();};struct O{M member;O(){}};void f(void*p){new(p)O;}',
     'cpp.unsupported.placement-constructor-effects'),
    ('unknown-composite-c1-body',
     'void effect();struct M{M(){}};struct O{M member;O(){effect();}};'
     'void f(void*p){new(p)O;}', 'cpp.unsupported.placement-constructor-effects'),
    ('unbound-composite-c1',
     'struct M{M(){}};struct O{M member;O();};void f(void*p){new(p)O;}',
     'cpp.unsupported.placement-constructor-effects'),
    ('unknown-member-c1-default',
     'int effect();struct M{M(int n=effect()){}};struct O{M member;O(){}};'
     'void f(void*p){new(p)O;}', 'cpp.unsupported.placement-constructor-effects'),
    ('nontrivial-complete-c1-argument-effect', 'int effect();' + SUM_OWNER +
     'void f(void*p){new(p)O(effect());}', 'cpp.unsupported.destructor-subobjects'),
    ('unbound-composite-d1',
     'struct M{~M(){}};struct O{M member;~O();};void f(O*p){p->~O();}',
     'cpp.unsupported.placement-constructor-effects'),
    ('unknown-outer-d1-with-trivial-member',
     'void effect();struct M{};struct O{M member;~O(){effect();}};void f(O*p){p->~O();}',
     'cpp.unsupported.placement-constructor-effects'),
    ('unknown-member-d1',
     'void effect();struct M{~M(){effect();}};struct O{M member;~O(){}};void f(O*p){p->~O();}',
     'cpp.unsupported.destructor-subobjects'),
    ('implicit-outer-d1-not-a-noop',
     'struct M{~M(){}};struct O{M member;};void f(O*p){p->~O();}',
     'cpp.unsupported.destructor-subobjects'),
    ('implicit-trivial-composite-d1-stays-outside-this-batch',
     'struct M{};struct O{M member;};void f(O*p){p->~O();}',
     'cpp.unsupported.destructor-subobjects'),
    ('implicit-composite-placement-c1',
     'struct M{int n;};struct O{M member;~O(){}};void f(void*p){new(p)O;}',
     'cpp.unsupported.placement-implicit-construction'),
    ('array-member-lifetimes',
     'struct M{M(){}~M(){}};struct O{M members[2];O(){}~O(){}};',
     'cpp.unsupported.class-array-members'),
    ('placement-array-lifetimes',
     'struct M{M(){}~M(){}};struct O{M member;O(){}~O(){}};void f(void*p){new(p)O[2];}',
     'cpp.unsupported.array-new'),
)

INVALID = (
    ('private-composite-c1',
     'struct M{M(){}};class O{M member;O(){}};void f(void*p){new(p)O;}',
     'cpp.member-access'),
    ('private-composite-d1',
     'struct M{~M(){}};class O{M member;~O(){}};void f(O*p){p->~O();}',
     'cpp.member-access'),
    ('private-member-d1',
     'class M{~M(){}public:M(){}};struct O{M member;O(){}~O(){}};',
     'cpp.member-access'),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_composite_placement_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,source', PRESERVED_ORIGINALS, ids=[row[0] for row in PRESERVED_ORIGINALS])
def test_formerly_deferred_placement_originals_are_positive(name, source):
    CppParser().parse(source)


@pytest.mark.parametrize('name,source,code', DEFERRED + INVALID,
                         ids=[row[0] for row in DEFERRED + INVALID])
def test_unowned_or_invalid_composite_lifetimes_fail_closed(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(DECLARATION + source)
    assert error.value.code == code


@pytest.mark.parametrize('kind', ('constructor', 'destructor'))
def test_placement_never_substitutes_call_free_metadata_for_missing_chain_proof(kind, monkeypatch):
    if kind == 'constructor':
        from pcc.frontends.cpp import constructors as owner
        attribute, binding = 'closed_construction_body', 'bind_constructor_body'
        source = 'struct M{M(){}};struct O{M member;O(){}};void f(void*p){new(p)O;}'
    else:
        from pcc.frontends.cpp import destructors as owner
        attribute, binding = 'closed_destruction_body', 'bind_destructor_body'
        source = 'struct M{~M(){}};struct O{M member;~O(){}};void f(O*p){p->~O();}'
    original = getattr(owner, binding)

    def without_proof(parser, definition, symbol, *args, **kwargs):
        function = original(parser, definition, symbol, *args, **kwargs)
        if definition.dtype.name == 'O':
            delattr(symbol, attribute)
            symbol.closed_normal_body = True
        return function

    monkeypatch.setattr(owner, binding, without_proof)
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(DECLARATION + source)
    assert error.value.code == 'cpp.unsupported.placement-constructor-effects'


def test_composite_chain_proofs_remain_distinct_from_call_free_metadata():
    parser = CppParser()
    parser.parse(DECLARATION + LEAF + OWNER + 'void f(void*p){O*o=new(p)O;o->~O();}')
    definition = parser.scope.names['O'].dtype.definition
    assert definition.constructors.overloads[0].closed_construction_body
    assert not definition.constructors.overloads[0].closed_normal_body
    assert definition.destructor.closed_destruction_body
    assert not definition.destructor.closed_normal_body
    assert parser.lifetimes.frames == [] and parser.lifetimes.controls == []
