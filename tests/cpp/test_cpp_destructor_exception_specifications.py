"""N4659 [except.spec]/1-2,5,8: a D1 contract belongs to its declaration.

Exception specifications are independent of a bound body's effect proof.
No implementation of C++ throw/catch or exception unwinding is claimed here.
"""

import struct

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, run_executable,
    write_host_executable,
)
from tests.cpp.test_cpp_placement_new import compile_reference_with_new


@pytest.mark.parametrize('specifier,expected', (
    ('', True),
    ('noexcept', True),
    ('noexcept(true)', True),
    ('noexcept((1+2)==3)', True),
    ('noexcept(false)', False),
    ('noexcept(1==2)', False),
    ('throw()', True),
))
def test_destructor_declaration_retains_its_exception_specification(specifier, expected):
    parser = CppParser()
    parser.parse('struct S{~S()' + specifier + ';};')
    symbol = parser.scope.names['S'].dtype.definition.destructor
    assert symbol.nonthrowing is expected
    assert not symbol.defined


@pytest.mark.parametrize('source,expected', (
    ('struct S{~S();int n;};', True),
    ('struct M{~M()noexcept(false);};struct S{~S();M member;};', False),
    ('struct M{~M()noexcept(false);};struct S{M member;~S()=default;};', False),
    ('struct M{~M()noexcept(false);};struct S{~S();M *member;};', True),
    ('struct B{~B()noexcept(false);};struct S:B{~S();};', False),
    ('struct M{~M()noexcept(false);};struct S{~S()noexcept;M member;};', True),
    ('struct S{~S()noexcept(false)=default;};', False),
    ('struct S{~S()noexcept=default;};', True),
))
def test_implicit_specification_uses_completed_subobjects_not_body_order(source, expected):
    parser = CppParser()
    parser.parse(source)
    assert parser.scope.names['S'].dtype.definition.destructor.nonthrowing is expected


@pytest.mark.parametrize('source', (
    'struct S{~S();};S::~S()noexcept{}',
    'struct S{~S()noexcept;};S::~S()throw(){}',
    'struct M{~M()noexcept(false);};struct S{M member;~S();};S::~S()noexcept(false){}',
    'struct S{~S()noexcept(1);};S::~S(){}',
))
def test_matching_out_of_class_definition_keeps_one_contract(source):
    parser = CppParser()
    parser.parse(source)
    symbol = parser.scope.names['S'].dtype.definition.destructor
    assert symbol.defined


@pytest.mark.parametrize('source', (
    'struct S{~S()noexcept;};S::~S()noexcept(false){}',
    'struct S{~S()noexcept(false);};S::~S(){}',
    'struct M{~M()noexcept(false);};struct S{M member;~S();};S::~S()noexcept{}',
))
def test_out_of_class_definition_cannot_change_exception_specification(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == 'cpp.conflicting-exception-specification'


@pytest.mark.parametrize('source', (
    'struct B{virtual ~B();};struct D:B{~D()noexcept(false) override;};',
    'struct B{virtual ~B();};struct M{~M()noexcept(false);};struct D:B{~D();M m;};',
    'struct B{virtual ~B();};struct M{~M()noexcept(false);};struct D:B{M m;~D();};',
    'struct B{virtual ~B();};struct M{~M()noexcept(false);};struct D:B{M m;};',
    'struct B{virtual ~B();};template<class T>struct D:B{~D()noexcept(false){}};',
))
def test_virtual_overrider_cannot_have_looser_exception_specification(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == 'cpp.looser-exception-specification'


def test_throwing_base_can_have_a_nonthrowing_overrider():
    parser = CppParser()
    parser.parse('struct B{virtual ~B()noexcept(false);};struct D:B{~D()noexcept override;};')
    assert parser.scope.names['D'].dtype.definition.destructor.nonthrowing


@pytest.mark.parametrize('source', (
    'struct S{~S()noexcept(false);};void f(S*p){p->~S();}',
    'struct M{~M()noexcept(false);};struct S{~S();M member;};void f(S*p){p->~S();}',
    'struct B{virtual ~B()noexcept(false){}};void f(B*p){p->~B();}',
))
def test_potentially_throwing_explicit_destructor_keeps_cleanup_boundary(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == 'cpp.unsupported.placement-constructor-effects'


@pytest.mark.parametrize('source', (
    'struct S{~S()noexcept;};void f(S*p){p->~S();}',
    'struct S{int n;~S();};int f(S*p){p->~S();return 0;}',
))
def test_declared_nonthrowing_destructor_needs_no_local_body_proof(source):
    parser = CppParser()
    parser.parse(source)
    symbol = parser.scope.names['S'].dtype.definition.destructor
    assert symbol.nonthrowing and not symbol.defined
    assert not getattr(symbol, 'closed_destruction_body', False)
    assert parser.pending_explicit_destruction_members == []


def test_template_explicit_destructor_requests_the_real_instantiated_body():
    parser = CppParser()
    tree = parser.parse('template<class T>struct Box{~Box(){}};void f(Box<int>*p){p->~Box();}')
    from pcc.frontends.c.ast import c_ast
    assert any(isinstance(node, c_ast.FuncDef) and node.decl.name == '_ZN3BoxIiED1Ev'
               for node in tree.ext)


@pytest.mark.integration
def test_nonthrowing_virtual_d1_crosses_translation_units_with_real_overrides(tmp_path):
    header = (
        'using size_type=unsigned long;void*operator new(size_type,void*)noexcept;'
        'void append(int*,int);'
        'struct Base{Base(int*p):out(p){}int*out;virtual~Base()noexcept;};'
        'void dispose(Base*);'
    )
    units = (
        ('destroy.cpp', header + 'Base::~Base()throw(){append(out,2);}'
         'void dispose(Base*p){p->~Base();}'),
        ('caller.cpp', header + 'void append(int*p,int digit){*p=*p*10+digit;}'
         'struct Child:Base{Child(int*p):Base(p){}~Child()noexcept(2>1)override{append(out,4);}};'
         'int cpp_test_entry(){long long storage[16];int result=0;'
         'Base*p=new(storage)Child(&result);dispose(p);return result;}'),
    )
    expected = Execution(42, struct.pack('<i', 42), b'')
    artifact = compile_host_cpp(units)
    binary = tmp_path / 'owned'
    write_host_executable(artifact, binary)
    reference = compile_reference_with_new(units, tmp_path / 'reference')
    assert_exact(run_executable(reference), expected)
    assert_exact(run_executable(binary), expected)


@pytest.mark.parametrize('source,code', (
    ('struct S{~S()noexcept(2);};', 'cpp.narrowing-conversion'),
    ('struct S{~S()throw(int);};', 'cpp.invalid-exception-specification'),
    ('int n;struct S{~S()noexcept(n);};', 'cpp.unsupported.exceptions'),
    ('struct S{~S()noexcept{throw 1;}};', 'cpp.unsupported.exceptions'),
))
def test_exception_specification_does_not_admit_narrowing_or_unowned_exceptions(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


def test_implicit_destructor_contracts_exist_before_synthesis():
    parser = CppParser()
    parser.parse('struct M{~M()noexcept(false);};struct S{M member;};struct T{S*p;};')
    assert not parser.scope.names['S'].dtype.definition.destructor_nonthrowing
    assert parser.scope.names['T'].dtype.definition.destructor_nonthrowing


@pytest.mark.parametrize('suffix', (
    'void f(){S object;}',
    'void f(S*p){delete p;}',
))
def test_potentially_throwing_d1_does_not_admit_unknown_automatic_or_heap_cleanup(suffix):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse('struct S{~S()noexcept(false);};' + suffix)
    assert error.value.code == 'cpp.unsupported.placement-constructor-effects'


@pytest.mark.integration
def test_explicit_potentially_throwing_d1_still_accepts_a_proven_direct_body(tmp_path):
    units = (('body.cpp',
              'using size_type=unsigned long;void*operator new(size_type,void*)noexcept;'
              'struct S{S(int*p):out(p){}int*out;~S()noexcept(false){*out=42;}};'
              'int cpp_test_entry(){long long raw[4];int result=0;'
              'S*p=new(raw)S(&result);p->~S();return result;}'),)
    expected = Execution(42, struct.pack('<i', 42), b'')
    binary = tmp_path / 'owned'
    write_host_executable(compile_host_cpp(units), binary)
    reference = compile_reference_with_new(units, tmp_path / 'reference')
    assert_exact(run_executable(reference), expected)
    assert_exact(run_executable(binary), expected)
