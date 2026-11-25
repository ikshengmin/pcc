"""N4659 [except.spec]/1-3,7 and [except.ctor]/3.

A user-provided C1 has no implicit non-throwing promise. A declared promise
belongs to the selected constructor rather than a source-shape recognizer.
The supported normal-exit cases do not exercise throwing/termination or
exception unwinding.
"""

import struct

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
    run_executable, write_host_executable,
)
from tests.cpp.test_cpp_placement_new import compile_reference_with_new


@pytest.mark.parametrize('specifier,expected', (
    ('', False),
    ('noexcept', True),
    ('noexcept(true)', True),
    ('noexcept((1+2)==3)', True),
    ('noexcept(false)', False),
    ('noexcept(1==2)', False),
    ('throw()', True),
))
def test_user_provided_c1_contract_is_not_implicit(specifier, expected):
    parser = CppParser()
    parser.parse('struct S{S()' + specifier + ';};')
    symbol, = parser.scope.names['S'].dtype.definition.constructors.overloads
    assert symbol.nonthrowing is expected
    assert not symbol.defined
    assert not getattr(symbol, 'closed_construction_body', False)


@pytest.mark.parametrize('source,expected', (
    ('struct S{S()noexcept;};S::S()throw(){}', True),
    ('struct S{S()throw();};S::S()noexcept(1){}', True),
    ('struct S{S()noexcept(false);};S::S(){}', False),
    ('struct S{S();};S::S()noexcept(false){}', False),
    ('struct S{S(){} };', False),
    ('struct S{S()noexcept{} };', True),
))
def test_matching_c1_definitions_keep_contract_and_independent_body_proof(source, expected):
    parser = CppParser()
    parser.parse(source)
    symbol, = parser.scope.names['S'].dtype.definition.constructors.overloads
    assert symbol.defined and symbol.closed_construction_body
    assert symbol.nonthrowing is expected


@pytest.mark.parametrize('source,code', (
    ('struct S{S()noexcept;};S::S(){}', 'cpp.conflicting-exception-specification'),
    ('struct S{S();};S::S()noexcept{}', 'cpp.conflicting-exception-specification'),
    ('struct S{S()noexcept(false);};S::S()throw(){}', 'cpp.conflicting-exception-specification'),
    ('struct S{S()noexcept(2);};', 'cpp.narrowing-conversion'),
    ('struct S{S()throw(int);};', 'cpp.invalid-exception-specification'),
    ('int n;struct S{S()noexcept(n);};', 'cpp.unsupported.exceptions'),
    ('struct S{S()noexcept{throw 1;}};', 'cpp.unsupported.exceptions'),
    # A valid declaration does not implement termination if a new PCC body
    # calls a potentially-throwing function. Do not silently discard it.
    ('void unknown();struct S{S()noexcept{unknown();}};', 'cpp.unsupported.exceptions'),
    ('void unknown();struct S{S()noexcept;};S::S()noexcept{unknown();}', 'cpp.unsupported.exceptions'),
    ('void unknown();template<class T>struct S{S()noexcept{unknown();}};void f(){S<int>s;}',
     'cpp.unsupported.exceptions'),
    # A member's declared contract says nothing about its default argument or
    # the arguments of a later call in an owning constructor.
    ('int unknown();struct M{M(int n=unknown())noexcept{}~M(){}};'
     'struct O{M first;M second;O():first(1){}~O(){}};', 'cpp.unsupported.destructor-subobjects'),
    ('int unknown();struct M{M(int)noexcept;~M(){}};'
     'struct O{M first;M second;O():first(1),second(unknown()){}~O(){}};', 'cpp.unsupported.destructor-subobjects'),
    ('struct M{M()noexcept;M(int);~M(){}};struct O{M m;O():m(1){}~O(){}};',
     'cpp.unsupported.destructor-subobjects'),
    ('struct M{M()noexcept(false);~M(){}};struct O{M m;O(){}~O(){}};',
     'cpp.unsupported.destructor-subobjects'),
    # Constructors unlike destructors have no implicit noexcept default.
    ('struct M{M();~M(){}};struct O{M m;O(){}~O(){}};', 'cpp.unsupported.destructor-subobjects'),
))
def test_unowned_effects_and_invalid_declarations_fail_closed(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


HEADER = ('struct Member{int*out;int n;Member(int*,int)noexcept;~Member();};'
          'struct Owner{Member first;Member last;Owner(int*)noexcept;~Owner();};')
PROVIDER = HEADER + (
    'Member::Member(int*p,int n)noexcept:out(p),n(n){*out=*out*10+n;}'
    'Member::~Member(){*out=*out*10+(n+4);}'
    'Owner::Owner(int*p)throw():last(p,2),first(p,1){*p=*p*10+3;}'
    'Owner::~Owner(){*first.out=*first.out*10+4;}'
)
CALLER = HEADER + 'int cpp_test_entry(){int trace=0;{Owner o(&trace);if(trace!=123)return 1;}return trace==123465?42:2;}'

# Normal C1/D1 order is observed independently on each compiled executable.
PROGRAMS = (
    ('declared-composite-c1-across-tus', (('provider.cpp', PROVIDER), ('caller.cpp', CALLER))),
    ('declared-member-c1-across-tus', (
        ('leaf.cpp', 'struct Member{int*out;int n;Member(int*,int)throw();~Member();};'
         'Member::Member(int*p,int n)noexcept:out(p),n(n){*out=*out*10+n;}'
         'Member::~Member(){*out=*out*10+(n+4);}'),
        ('owner.cpp', 'struct Member{int*out;int n;Member(int*,int)noexcept;~Member();};'
         'struct Owner{Member first;Member last;Owner(int*p):last(p,2),first(p,1){*p=*p*10+3;}~Owner(){*first.out=*first.out*10+4;}};'
         'int cpp_test_entry(){int trace=0;{Owner o(&trace);if(trace!=123)return 1;}return trace==123465?42:2;}'),
    )),
    ('templated-nonthrowing-member-c1', (('template.cpp',
         'template<class T>struct Member{T*out;T n;Member(T*p,T n)noexcept:out(p),n(n){}~Member(){*out+=n;}};'
         'struct Owner{Member<int> m;Owner(int*p)noexcept:m(p,42){}~Owner(){}};'
         'int cpp_test_entry(){int destroyed=0;{Owner o(&destroyed);}return destroyed;}'),)),
    ('selected-overload', (('overload.cpp',
         'int destroyed=0;struct Member{int n;Member(int n)noexcept:n(n){}Member(long n)noexcept(false):n(0){}~Member(){destroyed+=n;}};'
         'struct Owner{Member m;Owner()noexcept:m(42){}~Owner(){}};'
         'int cpp_test_entry(){{Owner o;}return destroyed;}'),)),
)


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[case[0] for case in PROGRAMS])
def test_c1_contract_programs_match_cpp17(name, units, tmp_path):
    artifact = compile_host_cpp(units)
    binary = tmp_path / 'owned'
    write_host_executable(artifact, binary)
    reference = compile_reference_cpp(units, tmp_path / 'reference', compiler='g++')
    expected = Execution(42, struct.pack('<i', 42), b'')
    assert_exact(run_executable(reference), expected)
    assert_exact(run_executable(binary), expected)


@pytest.mark.integration
def test_reserved_placement_can_call_declared_nonthrowing_c1_across_tus(tmp_path):
    declaration = 'using size_type=unsigned long;void*operator new(size_type,void*)noexcept;'
    units = (
        ('provider.cpp', HEADER + PROVIDER[len(HEADER):]),
        ('caller.cpp', declaration + HEADER +
         'int cpp_test_entry(){long long storage[16];int trace=0;Owner*o=new(storage)Owner(&trace);'
         'if(trace!=123)return 1;o->~Owner();return trace==123465?42:2;}'),
    )
    artifact = compile_host_cpp(units)
    binary = tmp_path / 'owned'
    write_host_executable(artifact, binary)
    reference = compile_reference_with_new(units, tmp_path / 'reference')
    expected = Execution(42, struct.pack('<i', 42), b'')
    assert_exact(run_executable(reference), expected)
    assert_exact(run_executable(binary), expected)
