"""C/C++ function language linkage, retaining unmodified source semantics.

N4659 [dcl.link]/2,5,6 and [expr.static.cast]. Original PCC cases; external
G++ is explicitly a reference, never an owner of PCC compilation or linking.
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
"""
import os
import re
import shutil
import struct
import subprocess

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (
    Execution, compile_host_cpp, compile_reference_cpp, write_host_executable,
)
from tests.cpp.test_cpp_class_execution import execute_and_record, record_execution


CASES = (
    ("single-definition", 'extern "C" int add(int x){return x+1;}int cpp_test_entry(){return add(41);}'),
    ("block-does-not-introduce-scope", 'extern "C" {int add(int);int sub(int x){return x-1;}}'
     'int add(int x){return sub(x)+2;}int cpp_test_entry(){return add(41);}'),
    ("cross-namespace-c-identity", 'namespace A{extern "C" int f(int);}'
     'namespace B{extern "C" int f(int x){return x+1;}}int cpp_test_entry(){return A::f(41);}'),
    ("using-one-c-entity", 'namespace A{extern "C" int f(int);}'
     'namespace B{extern "C" int f(int x){return x+1;}}using A::f;using B::f;'
     'int cpp_test_entry(){return f(41);}'),
    ("c-and-cpp-overloads", 'extern "C" int f(int x){return x+1;}'
     'int f(long x){return 9;}int cpp_test_entry(){return f(41);}'),
    ("nested-linkage-restoration", 'extern "C" {int f(int x){return x;}'
     'extern "C++" {int g(int x){return x;}int g(long x){return 9;}}'
     'int h(int x){return x;}}int cpp_test_entry(){return f(10)+g(12)+h(20);}'),
    ("namespace-c-vs-independent-cpp", 'namespace A{extern "C" int f(int x){return x;}}'
     'int f(int x){return x+2;}int cpp_test_entry(){return A::f(20)+f(20);}'),
    ("eight-signed32", 'extern "C" int sum(int a,int b,int c,int d,int e,int f,int g,int h)'
     '{return a+b+c+d+e+f+g+h;}int cpp_test_entry(){return sum(-2000000000,2000000000,1,2,4,8,16,11);}'),
    ("eight-signed64", 'extern "C" long long sum(long long a,long long b,long long c,long long d,'
     'long long e,long long f,long long g,long long h){return a+b+c+d+e+f+g+h;}'
     'int cpp_test_entry(){return int(sum(-5000000000000LL,5000000000000LL,1LL,2LL,4LL,8LL,16LL,11LL));}'),
    ("eight-unsigned64", 'extern "C" unsigned long long sum(unsigned long long a,unsigned long long b,'
     'unsigned long long c,unsigned long long d,unsigned long long e,unsigned long long f,'
     'unsigned long long g,unsigned long long h){return a+b+c+d+e+f+g+h;}'
     'int cpp_test_entry(){if(sum(0xf123456789abc000ULL,1ULL,2ULL,4ULL,8ULL,16ULL,32ULL,64ULL)'
     '!=0xf123456789abc07fULL)return 1;return 42;}'),
    ("unsigned32-static-cast", 'extern "C" unsigned int f(unsigned int x){return x+256U;}'
     'int cpp_test_entry(){int x=-2;if(f(static_cast<unsigned int>(x))!=254U)return 1;return 42;}'),
    ("unsigned32-to-signed64-static-cast", 'extern "C" long long f(long long x){return x;}'
     'int cpp_test_entry(){unsigned int x=0xf1234500U;'
     'if(f(static_cast<long long>(x))!=4045620480LL)return 1;return 42;}'),
    ("c-void-call", 'extern "C" void nothing(){return;}int cpp_test_entry(){nothing();return 42;}'),
    ("extern-storage-is-not-language-linkage", 'extern int f(int);int f(int x){return x;}'
     'int cpp_test_entry(){return f(42);}'),
)

INVALID = (
    ('int f(int);extern "C" int f(int);', "cpp.conflicting-language-linkage"),
    ('extern "C" int f(int);extern "C++" int f(int);', "cpp.conflicting-language-linkage"),
    ('extern "C" int f(int);extern "C" int f(long);', "cpp.conflicting-c-linkage-type"),
    ('namespace A{extern "C" int f(int);}namespace B{extern "C" long f(int);}', "cpp.conflicting-c-linkage-type"),
    ('namespace A{extern "C" int f(){return 1;}}namespace B{extern "C" int f(){return 2;}}', "cpp.redefinition"),
    ('extern "C" int f(int);extern "C" long f(int);', "cpp.return-type-overload"),
    ('int f(){extern "C" int g();return 0;}', "cpp.linkage-scope"),
    ('extern "C" {template<class T>T f(T x){return x;}}', "cpp.template-c-linkage"),
    ('namespace A{extern "C" int f();}int cpp_test_entry(){return f();}', "cpp.undeclared"),
    ('extern "C";', "cpp.linkage-declaration"),
    ('extern "C" extern int f();', "cpp.linkage-storage"),
    ('extern "C" static int f();', "cpp.linkage-storage"),
    ('extern "C" int main();', "cpp.main-language-linkage"),
    ('extern "C++" int main();', "cpp.main-language-linkage"),
    ('namespace N{extern "C" int main();}', "cpp.main-language-linkage"),
    ('namespace A{extern "C" int f(int);}namespace B{extern "C" int f(int);}'
     'namespace D{int f(int);}namespace B{using D::f;}', "cpp.using-declaration-conflict"),
)

UNSUPPORTED = (
    'extern "C" int f(int*);',
    'extern "C" double f(double);',
    'extern "Fortran" int f();',
    'extern "C" {static int f(){return 1;}}',
    'namespace {extern "C" int f();}',
    'extern "C" int x;',
    'int f(){return static_cast<int>(1.5);}',
    'int f(){int x=0;return static_cast<int>(&x);}',
    'template<class T>T f(T x){return static_cast<T>(x);}',
)


@pytest.mark.parametrize("name,source", CASES, ids=[x[0] for x in CASES])
def test_c_linkage_owned_artifacts_are_byte_stable(name, source):
    units = ((name + ".cpp", source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize("source,code", INVALID)
def test_invalid_linkage_is_diagnosed_before_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid language linkage reached the emitter")
    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((("invalid.cpp", source),))
    assert error.value.code == code


@pytest.mark.parametrize("source", UNSUPPORTED)
def test_missing_linkage_dependencies_remain_explicit(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, "unsupported-linkage.cpp")
    assert error.value.code.startswith("cpp.unsupported."), str(error.value)


def test_c_function_identity_is_shared_without_flattening_cpp_lookup():
    parser = CppParser()
    parser.parse('namespace A{extern "C" int f(int);}namespace B{extern "C" int f(int);}')
    a = parser.scope.names['A'].namespace_scope
    b = parser.scope.names['B'].namespace_scope
    first, second = a.names['f'].overloads[0], b.names['f'].overloads[0]
    assert first is second and first.language_linkage == 'C' and first.c_name == 'f'
    assert first.declaration_scopes == [a, b]
    assert parser.scope.lookup('f') is None


def test_linkage_context_does_not_leak_after_a_single_declaration():
    parser = CppParser()
    parser.parse('extern "C" int f(int);int g(int);')
    assert parser.scope.names['f'].overloads[0].c_name == 'f'
    assert parser.scope.names['g'].overloads[0].c_name == '_Z1gi'


def test_c_alias_redeclaration_and_definition_preserve_identity():
    parser = CppParser()
    parser.parse('namespace A{extern "C" int f(int);}using A::f;'
                 'extern "C" int f(int x){return x;}')
    assert parser.scope.names['f'].overloads[0] is parser.scope.c_linkage_functions['f']


def test_public_cpp_pipeline_preserves_plain_c_names_without_ir_rewriting():
    from tests.cpp.test_cpp_public_driver import compile_cpp
    source = 'extern "C" unsigned int foreign(unsigned int);extern "C" int observer(){return int(foreign(static_cast<unsigned int>(-2)));}'
    first = compile_cpp(source, 'public-linkage.cpp', use_system_cpp=False)
    assert first == compile_cpp(source, 'public-linkage.cpp', use_system_cpp=False)
    ir = first[0][1]
    assert re.search(r'\bcall\b[^\n]*@foreign\(', ir)
    assert re.search(r'\bdefine\b[^\n]*@observer\(', ir)
    assert '_Z7foreign' not in ir and '_Z8observer' not in ir


@pytest.mark.integration
@pytest.mark.parametrize("name,source", CASES, ids=[x[0] for x in CASES])
def test_c_linkage_executes_against_explicit_cpp17_reference(name, source, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name + ".cpp", source),), 42)


@pytest.mark.integration
def test_c_linkage_identity_across_independent_translation_units(tmp_path, monkeypatch):
    units = (("helper.cpp", 'namespace A{extern "C" int f(int x){return x+1;}}'),
             ("entry.cpp", 'extern "C" int f(int);int cpp_test_entry(){return f(41);}'))
    execute_and_record(tmp_path, monkeypatch, units, 42)


@pytest.mark.integration
def test_c_eight_argument_entry_is_observed_by_independent_amd64_assembly(tmp_path, monkeypatch):
    source = ('extern "C" long long c_check(long long a,long long b,long long c,long long d,'
              'long long e,long long f,long long g,long long h){'
              'if(a!=-5000000000000LL)return 1;if(b!=81985529216486895LL)return 2;'
              'if(c!=-3)return 3;if(d!=4)return 4;if(e!=-5)return 5;if(f!=6)return 6;'
              'if(g!=-7)return 7;if(h!=8)return 8;return 42;}'
              'extern "C" long long observe();int cpp_test_entry(){return int(observe());}')
    assembly = ('.intel_syntax noprefix\n.text\n.globl observe\nobserve:\n'
                ' sub rsp,24\n mov rdi,-5000000000000\n mov rsi,81985529216486895\n'
                ' mov rdx,-3\n mov rcx,4\n mov r8,-5\n mov r9,6\n'
                ' mov rax,-7\n mov qword ptr [rsp],rax\n mov rax,8\n'
                ' mov qword ptr [rsp+8],rax\n call c_check\n add rsp,24\n ret\n')
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError("owned C linkage compilation invoked an external process")
        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp((("entry.cpp", source),), owned_support_assembly=(assembly,))
        assert artifact == compile_host_cpp((("entry.cpp", source),), owned_support_assembly=(assembly,))
    binary = tmp_path / 'owned'
    write_host_executable(artifact, binary)
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(binary, expected)
    (tmp_path / 'observer.s').write_text(assembly)
    helper = ('extern "C" long long c_check(long long,long long,long long,long long,long long,long long,long long,long long);'
              'extern "C" long long observe(){return c_check(-5000000000000LL,81985529216486895LL,-3,4,-5,6,-7,8);}')
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    oracle = compile_reference_cpp((("entry.cpp", source), ("observer.cpp", helper)),
                                   tmp_path / 'reference', compiler=compiler)
    record_execution(oracle, expected)
