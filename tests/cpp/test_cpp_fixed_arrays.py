"""Original fixed-array C++17 cases; no upstream sources are rewritten.

N4659 dcl.array/dcl.fct/dcl.init.aggr/conv.array/expr.sub. The typed array
survives until decay/parameter adjustment; G++ is an explicit reference only.
"""
import os
import shutil
import subprocess

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ArrayType, ConstType, PointerType, qualify_const
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record

CASES = (
    ('unnamed-parameters-keep-register-and-stack-positions', 'int f(int,int b,int,int d,int,int f,int,int h){return b+d+f+h;}'
     'int cpp_test_entry(){return f(100,1,200,2,300,4,400,35);}'),
    ('local-store-index', 'int cpp_test_entry(){int a[2];a[0]=17;a[1]=25;return a[0]+a[1];}'),
    ('list-initialization', 'int cpp_test_entry(){int a[2]={17,25};return a[0]+a[1];}'),
    ('direct-list-initialization', 'int cpp_test_entry(){int a[2]{17,25};return a[0]+a[1];}'),
    ('omitted-elements-zero', 'int cpp_test_entry(){int a[3]={42};return a[0]+a[1]+a[2];}'),
    ('empty-list-zero', 'int cpp_test_entry(){int a[3]{};a[1]=42;return a[0]+a[1]+a[2];}'),
    ('const-elements', 'int read(const int*p){return p[0]+p[1];}int cpp_test_entry(){const int a[2]={17,25};return read(a);}'),
    ('typedef-const-array', 'typedef int A[2];int cpp_test_entry(){const A a={17,25};return a[0]+a[1];}'),
    ('typed-constant-bound', 'int cpp_test_entry(){const int n=1+1;int a[n]={17,25};return a[0]+a[1];}'),
    ('enum-bound', 'enum N{Size=2};int cpp_test_entry(){int a[Size]={17,25};return a[0]+a[1];}'),
    ('bool-bound', 'int cpp_test_entry(){int a[true]={42};return a[0];}'),
    ('array-parameter-adjustment', 'int sum(int a[2]){return a[0]+a[1];}int cpp_test_entry(){int a[2]={17,25};return sum(a);}'),
    ('unknown-outer-parameter-bound', 'int sum(int a[]){return a[0]+a[1];}int cpp_test_entry(){int a[2]={17,25};return sum(a);}'),
    ('adjusted-parameter-redeclaration', 'int sum(int*);int sum(int a[9]){return a[0]+a[1];}int cpp_test_entry(){int a[2]={17,25};return sum(a);}'),
    ('multidimensional-explicit-braces', 'int cpp_test_entry(){int a[2][2]={{17},{0,25}};return a[0][0]+a[0][1]+a[1][0]+a[1][1];}'),
    ('multidimensional-parameter-stride', 'int sum(int a[][3]){return a[0][1]+a[1][2];}int cpp_test_entry(){int a[2][3]={{0,17},{0,0,25}};return sum(a);}'),
    ('address-of-array-preserves-bound', 'int cpp_test_entry(){int a[2]={17,25};int(*p)[2]=&a;return (*p)[0]+p[0][1];}'),
    ('pointer-array-row-stride', 'int cpp_test_entry(){int a[2][2]={{0,0},{17,25}};int(*p)[2]=a;++p;return (*p)[0]+(*p)[1];}'),
    ('pointer-loop', 'int cpp_test_entry(){int a[3]={10,12,20};int*p=a;int sum=0;for(int i=0;i<3;++i,++p)sum=sum+*p;return sum;}'),
    ('subscript-lvalue-address', 'int cpp_test_entry(){int a[2]={0,42};int*p=&a[1];return *p;}'),
    ('subscript-prefix-lvalue', 'int cpp_test_entry(){int a[1]={40};++a[0];return ++a[0];}'),
    ('commuted-subscript', 'int cpp_test_entry(){int a[2]={17,25};return 0[a]+1[a];}'),
    ('left-before-right-subscript', 'int*base(int*p,int*n){*n=*n*10+1;return p;}int index(int*n){*n=*n*10+2;return 0;}int cpp_test_entry(){int n=0;int a[1]={30};int v=base(a,&n)[index(&n)];return v+n;}'),
    ('commuted-left-before-right', 'int index(int*n){*n=*n*10+1;return 0;}int*base(int*p,int*n){*n=*n*10+2;return p;}int cpp_test_entry(){int n=0;int a[1]={30};int v=index(&n)[base(a,&n)];return v+n;}'),
    ('rhs-before-subscript-lhs', 'int rhs(int*n){*n=*n*10+1;return 42;}int index(int*n){*n=*n*10+2;return 0;}int cpp_test_entry(){int n=0;int a[1]{};a[index(&n)]=rhs(&n);if(n!=12)return 1;return a[0];}'),
    ('initializer-order-once', 'int take(int*n,int v){*n=*n*10+v;return v;}int cpp_test_entry(){int n=0;int a[2]={take(&n,1),take(&n,2)};if(n!=12)return 1;return a[0]+a[1]+39;}'),
    ('prior-element-readable', 'int cpp_test_entry(){int a[2]={17,a[0]+25};return a[1];}'),
    ('pointer-elements', 'int cpp_test_entry(){int x=17;int y=25;int*a[2]={&x,&y};return *a[0]+*a[1];}'),
    ('nominal-enum-elements', 'enum class E{A=17,B=25};int cpp_test_entry(){E a[2]={E::A,E::B};return int(a[0])+int(a[1]);}'),
    ('enum-array-adl', 'namespace n{enum class E{A=17,B=25};int sum(E a[]){return int(a[0])+int(a[1]);}}int cpp_test_entry(){n::E a[2]={n::E::A,n::E::B};return sum(a);}'),
    ('unsigned64-storage', 'int cpp_test_entry(){unsigned long long a[2]={0xf123456789abc000ULL,42ULL};if(a[0]!=0xf123456789abc000ULL)return 1;return int(a[1]);}'),
    ('narrowing-constant-exception', 'int cpp_test_entry(){unsigned char a[2]={17,25};return a[0]+a[1];}'),
    ('array-decay-bool', 'int cpp_test_entry(){int a[1]={42};if(!a)return 1;return a[0];}'),
    ('array-decay-pointer-overload', 'int f(int*){return 42;}int f(bool){return 1;}int cpp_test_entry(){int a[1]{};return f(a);}'),
    ('const-array-address-to-const-void', 'int f(void*){return 1;}int f(const void*){return 42;}int cpp_test_entry(){const int a[2]={};return f(&a);}'),
    ('array-pointer-qualification', 'int read(const int(*p)[2]){return p[0][0]+p[0][1];}int cpp_test_entry(){int a[2]={17,25};return read(&a);}'),
    ('array-pointer-qualification-preference', 'int f(int(*p)[2]){return 42;}int f(const int(*p)[2]){return 1;}int cpp_test_entry(){int a[2]{};return f(&a);}'),
    ('array-pointer-qualification-equality', 'int cpp_test_entry(){int a[2]{};int(*p)[2]=&a;const int(*q)[2]=&a;if(p!=q)return 1;return 42;}'),
    ('function-return-pointer-to-array', 'int(*same(int(*p)[2]))[2]{return p;}int cpp_test_entry(){int a[2]={17,25};return same(&a)[0][0]+same(&a)[0][1];}'),
)

INVALID = (
    ('int f(int){return __pcc_cpp_unnamed_0;}', 'cpp.undeclared'),
    ('int f(){int a[1]={};bool b[1]={a};return b[0];}', 'cpp.narrowing-conversion'),
    ('struct S{bool b;};int f(){int a[1]={};S s{a};return s.b;}', 'cpp.narrowing-conversion'),
    ('int f(){int a[0];return 0;}', 'cpp.invalid-array-bound'),
    ('int f(){int a[-1];return 0;}', 'cpp.invalid-array-bound'),
    ('int f(){int a[1.5];return 0;}', 'cpp.invalid-array-bound'),
    ('int f(){int n=2;int a[n];return 0;}', 'cpp.nonconstant-array-bound'),
    ('int f(){const int n=1/0;int a[n];return 0;}', 'cpp.nonconstant-array-bound'),
    ('enum class E{N=2};int f(){int a[E::N];return 0;}', 'cpp.invalid-array-bound'),
    ('int f(){void a[2];return 0;}', 'cpp.invalid-array-element'),
    ('int f(){const int a[2];return 0;}', 'cpp.const-initializer'),
    ('int f(){const int a[2]={};a[0]=1;return 0;}', 'cpp.const-assignment'),
    ('int f(){int a[2]{};int b[2]{};a=b;return 0;}', 'cpp.array-not-assignable'),
    ('int f(){int a[2]{};++a;return 0;}', 'cpp.invalid-operands'),
    ('int f(){int a[1]={1,2};return 0;}', 'cpp.too-many-initializers'),
    ('int f(){int a[1]={1.5};return 0;}', 'cpp.narrowing-conversion'),
    ('int f(){unsigned char a[1]={256};return 0;}', 'cpp.narrowing-conversion'),
    ('int f(){int a[1]={{{1}}};return 0;}', 'cpp.invalid-scalar-initializer'),
    ('int f(){int a[1]{};return a[0.5];}', 'cpp.invalid-subscript'),
    ('int f(){int a[1]{};return a[a];}', 'cpp.invalid-subscript'),
    ('int f(){void*p=nullptr;return p[0];}', 'cpp.invalid-subscript'),
    ('int f(){const int a[1]={};int*p=a;return 0;}', 'cpp.invalid-conversion'),
    ('int f(){const int a[1]={};void*p=&a;return 0;}', 'cpp.invalid-conversion'),
    ('int f(){int a[2][];return 0;}', 'cpp.incomplete-array-element'),
    ('int f()[2];', 'cpp.invalid-function-return'),
)

UNSUPPORTED = (
    'int f(){int a[]={1,2};return 0;}',
    'int f(){int a[2][2]={1,2,3,4};return 0;}',
    'struct S{};int f(){S a[2];return 0;}',
    'struct S{int a[2];};',
    'int a[2]={1,2};',
    'int f(){char a[2]="x";return 0;}',
    'template<class T>int f(T x){int a[2]{};return 0;}',
    'template<class T>T id(T x){return x;}int f(){int a[2]{};id(a);return 0;}',
    'int f(int*a[]);',
)


@pytest.mark.parametrize('name,source', CASES, ids=[row[0] for row in CASES])
def test_fixed_array_artifacts_are_byte_stable(name, source):
    units = ((name+'.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('source,code', INVALID)
def test_fixed_array_errors_precede_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid array reached object emission')
    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('invalid-array.cpp', source),))
    assert error.value.code == code


@pytest.mark.parametrize('source', UNSUPPORTED)
def test_array_dependencies_are_explicit(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code.startswith('cpp.unsupported.'), str(error.value)


def test_array_nominal_shape_const_and_parameter_adjustment():
    parser = CppParser()
    parser.parse('typedef int A[3];int f(int a[3]);int f(int*);int g(int(*p)[3]);')
    assert parser.scope.names['A'].dtype == ArrayType('int', 3)
    assert parser.scope.names['A'].dtype != PointerType('int')
    assert qualify_const(ArrayType('int', 3)) == ArrayType(ConstType('int'), 3)
    assert len(parser.scope.names['f'].overloads) == 1
    assert parser.scope.names['f'].overloads[0].c_name == '_Z1fPi'
    assert parser.scope.names['g'].overloads[0].c_name == '_Z1gPA3_i'


def test_array_pointer_mangling_keeps_element_qualification_and_substitutions():
    parser = CppParser()
    parser.parse('int f(int(*a)[3],int(*b)[3]);int g(const int(*a)[3]);')
    assert parser.scope.names['f'].overloads[0].c_name == '_Z1fPA3_iS0_'
    assert parser.scope.names['g'].overloads[0].c_name == '_Z1gPA3_Ki'


def test_public_array_pipeline_preserves_original_source_and_is_deterministic():
    from tests.cpp.test_cpp_public_driver import compile_cpp
    source = dict(CASES)['multidimensional-parameter-stride']
    first = compile_cpp(source, 'public-array.cpp', use_system_cpp=False)
    assert first == compile_cpp(source, 'public-array.cpp', use_system_cpp=False)
    assert '_Z3sumPA3_i' in first[0][1]


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES, ids=[row[0] for row in CASES])
def test_fixed_arrays_execute_against_explicit_cpp17_reference(name, source, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name+'.cpp', source),), 42)


@pytest.mark.integration
def test_array_parameter_abi_across_independent_translation_units(tmp_path, monkeypatch):
    units = (('helper.cpp', 'int sum(const int a[][3]){return a[0][1]+a[1][2];}'),
             ('entry.cpp', 'int sum(const int(*p)[3]);int cpp_test_entry(){int a[2][3]={{0,17},{0,0,25}};return sum(a);}'))
    execute_and_record(tmp_path, monkeypatch, units, 42)


@pytest.mark.parametrize('target,bound', [('x86_64-unknown-linux-gnu', 4294967297),
                                         ('x86_64-pc-windows-msvc', 1)])
def test_array_bound_uses_converted_target_const_proof_without_allocating(target, bound):
    parser = CppParser(target)
    tree = parser.parse('int f(){const unsigned long n=4294967297ULL;int a[n];return 0;}')
    assert tree.ext[0].body.block_items[1].type.dim.value == str(bound)


@pytest.mark.integration
def test_array_pointer_mangle_and_row_stride_have_an_independent_observer(tmp_path, monkeypatch):
    import struct
    from tests.cpp.owned_execution import Execution, compile_reference_cpp, write_host_executable
    from tests.cpp.test_cpp_class_execution import record_execution
    source = ('int sum(const int a[][3]){return a[0][1]+a[1][2];}'
              'extern "C" int inspect_array();int cpp_test_entry(){return inspect_array();}')
    assembly = ('.intel_syntax noprefix\n.text\n.globl inspect_array\ninspect_array:\n'
                ' sub rsp,40\n mov eax,0\n mov dword ptr [rsp],eax\n'
                ' mov dword ptr [rsp+8],eax\n mov dword ptr [rsp+12],eax\n'
                ' mov dword ptr [rsp+16],eax\n mov eax,17\n mov dword ptr [rsp+4],eax\n'
                ' mov eax,25\n mov dword ptr [rsp+20],eax\n lea rdi,[rsp]\n'
                ' call _Z3sumPA3_Ki\n add rsp,40\n ret\n')
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned array ABI compilation invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        artifact = compile_host_cpp((('array-abi.cpp', source),), owned_support_assembly=(assembly,))
        assert artifact == compile_host_cpp((('array-abi.cpp', source),), owned_support_assembly=(assembly,))
    binary = tmp_path/'owned'
    write_host_executable(artifact, binary)
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(binary, expected)
    (tmp_path/'observer.s').write_text(assembly)
    helper = ('int sum(const int a[][3]);extern "C" int inspect_array(){'
              'const int a[2][3]={{0,17,0},{0,0,25}};return sum(a);}')
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    oracle = compile_reference_cpp((('array-abi.cpp', source), ('observer.cpp', helper)),
                                   tmp_path/'reference', compiler=compiler)
    record_execution(oracle, expected)


def test_unnamed_definition_parameter_has_projection_name_but_no_source_binding():
    tree = CppParser().parse('int f(int,long named){return int(named);}')
    parameters = tree.ext[0].decl.type.args.params
    assert parameters[0].name == '__pcc_cpp_unnamed_0'
    assert parameters[1].name == 'named'
