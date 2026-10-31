"""C language linkage with typed object pointers, not integer surrogates.

Original PCC C++17 cases for [dcl.link], pointer qualification and nominal
object-type identity. Opaque pointers do not imply complete-layout compatibility.
External G++ is a labeled reference only; PCC emission/linking remain owned.
"""
import os
import re
import shutil
import struct
import subprocess

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.classes import ClassType
from pcc.frontends.cpp.types import ConstType, PointerType
from tests.cpp.owned_execution import (
    Execution, compile_host_cpp, compile_reference_cpp, write_host_executable,
)
from tests.cpp.test_cpp_class_execution import execute_and_record, record_execution


CASES = (
    ('int-pointer-mutation', 'extern "C" int add(int*p){*p=*p+25;return *p;}'
     'int cpp_test_entry(){int value=17;return add(&value);}'),
    ('pointer-return-and-qualification', 'extern "C" const int*same(const int*p){return p;}'
     'int cpp_test_entry(){int value=42;return *same(&value);}'),
    ('void-pointer-preserves-address', 'extern "C" void*same(void*p){return p;}'
     'int cpp_test_entry(){int value=42;if(same(&value)!=&value)return 1;return value;}'),
    ('const-void-pointer', 'extern "C" const void*same(const void*p){return p;}'
     'int cpp_test_entry(){const int value=42;if(same(&value)!=&value)return 1;return value;}'),
    ('opaque-pointer-return', 'struct Opaque;extern "C" const Opaque*same(const Opaque*p){return p;}'
     'int cpp_test_entry(){if(same(nullptr)!=nullptr)return 1;return 42;}'),
    ('opaque-then-completed', 'struct Record;extern "C" int read(const Record*);'
     'struct Record{int value;};int read(const Record*p){return p->value;}'
     'int cpp_test_entry(){Record r{42};return read(&r);}'),
    ('nominal-pointer-in-overload-set', 'struct Record{int value;};extern "C" int read(const Record*p){return p->value;}'
     'int read(int*p){return 1;}int cpp_test_entry(){Record r{42};return read(&r);}'),
    ('canonical-across-namespace-and-alias', 'namespace n{struct Record{int value;};extern "C" int read(const Record*);}'
     'namespace m{using Item=n::Record;extern "C" int read(const Item*p){return p->value;}}'
     'int cpp_test_entry(){n::Record r{42};return n::read(&r);}'),
    ('array-parameter-adjustment', 'extern "C" int sum(const int values[2]);'
     'extern "C" int sum(const int*values){return values[0]+values[1];}'
     'int cpp_test_entry(){int values[2]={17,25};return sum(values);}'),
    ('row-array-pointer', 'extern "C" int sum(const int(*values)[3]){return values[0][1]+values[1][2];}'
     'int cpp_test_entry(){int values[2][3]={{0,17,0},{0,0,25}};return sum(values);}'),
    ('enum-pointer-keeps-nominal-type', 'enum class Value{Answer=42};'
     'extern "C" int read(const Value*p){return int(*p);}'
     'int cpp_test_entry(){Value v=Value::Answer;return read(&v);}'),
    ('pointer-to-floating-object-not-float-abi', 'extern "C" double*same(double*p){return p;}'
     'int cpp_test_entry(){double value=42.0;return int(*same(&value));}'),
    ('eight-object-pointers-include-stack-arguments', 'extern "C" int sum(const int*a,const int*b,const int*c,const int*d,'
     'const int*e,const int*f,const int*g,const int*h){return *a+*b+*c+*d+*e+*f+*g+*h;}'
     'int cpp_test_entry(){int a=1;int b=2;int c=3;int d=4;int e=5;int f=6;int g=7;int h=14;'
     'return sum(&a,&b,&c,&d,&e,&f,&g,&h);}'),
)

INVALID = (
    ('extern "C" int f(int*);extern "C" int f(const int*);', 'cpp.conflicting-c-linkage-type'),
    ('extern "C" int*f();extern "C" const int*f();', 'cpp.return-type-overload'),
    ('struct A;struct B;namespace n{extern "C" int f(A*);}namespace m{extern "C" int f(B*);}', 'cpp.conflicting-c-linkage-type'),
    ('namespace a{struct R;extern "C" int f(R*);}namespace b{struct R;extern "C" int f(R*);}', 'cpp.conflicting-c-linkage-type'),
    ('extern "C" int f(int*);extern "C++" int f(int*);', 'cpp.conflicting-language-linkage'),
    ('extern "C" int f(int*);int main(){const int value=42;return f(&value);}', 'cpp.no-viable-overload'),
    ('struct A{};struct B{};extern "C" int f(A*);int main(){B b{};return f(&b);}', 'cpp.no-viable-overload'),
    ('struct R;extern "C" int f(const R*p){return p->value;}', 'cpp.incomplete-class'),
    ('class R{int value;};extern "C" int f(const R*p){return p->value;}', 'cpp.member-access'),
    ('namespace a{extern "C" int f(int*);}namespace b{extern "C" int f(int*);}'
     'namespace c{int f(int*);}namespace b{using c::f;}', 'cpp.using-declaration-conflict'),
)

UNSUPPORTED = (
    'extern "C" double f(double);',
    'struct R{int value;};extern "C" R f(R);',
    'extern "C" int f(int (*callback)(int));',
    'extern "C" int f(int&);',
    'extern "C" int f(int,...);',
)


def test_original_multilevel_positive_intent_remains_an_explicit_deferred_gap():
    # Retain the exact original positive source and its intent. The first gate
    # failed here: C language linkage does not supply missing deep-pointer cv
    # semantics or address-of-pointer lowering. Rejection is not acceptance.
    source = ('extern "C" int**same(int**p){return p;}'
              'int cpp_test_entry(){int value=42;int*p=&value;return **same(&p);}')
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, 'pointer-to-pointer.cpp')
    assert error.value.code == 'cpp.unsupported.multilevel-pointers'


@pytest.mark.parametrize('name,source', CASES, ids=[case[0] for case in CASES])
def test_c_object_pointer_compilation_is_byte_stable(name, source):
    units = ((name + '.cpp', source),)
    first = compile_host_cpp(units)
    assert first == compile_host_cpp(units)


@pytest.mark.parametrize('source,code', INVALID)
def test_c_pointer_types_are_checked_before_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid typed C-pointer source reached emission')
    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('invalid.cpp', source),))
    assert error.value.code == code


@pytest.mark.parametrize('source', UNSUPPORTED)
def test_c_pointer_admission_does_not_enable_other_abi_features(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code.startswith('cpp.unsupported.'), str(error.value)


def test_c_pointer_canonical_signature_retains_const_and_nominal_identity():
    parser = CppParser()
    parser.parse('namespace n{struct Record;extern "C" const Record*read(const Record*);}'
                 'namespace m{using Alias=n::Record;extern "C" const Alias*read(const Alias*);}')
    n = parser.scope.names['n'].namespace_scope
    m = parser.scope.names['m'].namespace_scope
    first = n.names['read'].overloads[0]
    second = m.names['read'].overloads[0]
    record = n.names['Record'].dtype
    assert isinstance(record, ClassType) and record.definition is None
    assert first is second and first.c_name == 'read' and first.language_linkage == 'C'
    assert first.parameters == (PointerType(ConstType(record)),)
    assert first.dtype == PointerType(ConstType(record))
    assert first.parameters[0].pointee.base is record
    assert first.declaration_scopes == [n, m]


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES, ids=[case[0] for case in CASES])
def test_c_object_pointers_execute_against_explicit_cpp17_reference(name, source, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name + '.cpp', source),), 42)


@pytest.mark.integration
def test_const_record_c_pointer_across_independent_translation_units(tmp_path, monkeypatch):
    units = (
        ('caller.cpp', 'struct Record{int tag;unsigned long long wide;long long tail;};'
         'extern "C" unsigned long long read_record(const Record*);'
         'int cpp_test_entry(){Record r{-123456789,0xfedcba9876543210ULL,-72623859790382856LL};'
         'if(read_record(&r)!=0xfedcba9876543210ULL)return 1;return 42;}'),
        ('callee.cpp', 'struct Record{int tag;unsigned long long wide;long long tail;};'
         'extern "C" unsigned long long read_record(const Record*r){'
         'if(r->tag!=-123456789||r->tail!=-72623859790382856LL)return 0ULL;return r->wide;}'),
    )
    execute_and_record(tmp_path, monkeypatch, units, 42)


@pytest.mark.integration
def test_const_record_c_pointer_has_independent_owned_layout_observer(tmp_path, monkeypatch):
    declaration = 'struct AbiRecord{int tag;unsigned long long wide;long long signed_value;};'
    source = (declaration + 'extern "C" unsigned long long inspect_record(const AbiRecord*);'
              'int cpp_test_entry(){AbiRecord r{-123456789,0xfedcba9876543210ULL,-72623859790382856LL};'
              'if(inspect_record(&r)!=0xfedcba9876543210ULL)return 1;'
              'if(r.tag!=-123456789||r.wide!=0xfedcba9876543210ULL||r.signed_value!=-72623859790382856LL)return 2;return 42;}')
    # AMD64 observer checks address alignment and only initialized field bytes
    # at 0/8/16, not padding. It implements the test observer, not a C++ feature.
    assembly = ('.intel_syntax noprefix\n.text\n.globl inspect_record\ninspect_record:\n'
                '  mov rax,rdi\n  and eax,7\n  jne .Lbad\n'
                '  mov eax,dword ptr [rdi]\n  mov ecx,-123456789\n  cmp eax,ecx\n  jne .Lbad\n'
                '  mov rax,qword ptr [rdi+16]\n  mov rcx,-72623859790382856\n  cmp rax,rcx\n  jne .Lbad\n'
                '  mov rax,qword ptr [rdi+8]\n  ret\n.Lbad:\n  xor eax,eax\n  ret\n')
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned C-pointer observer invoked an external build tool')
        guard.setattr(subprocess, 'Popen', forbidden)
        first = compile_host_cpp((('observer.cpp', source),), owned_support_assembly=(assembly,))
        assert first == compile_host_cpp((('observer.cpp', source),), owned_support_assembly=(assembly,))
    binary = tmp_path / 'owned'
    write_host_executable(first, binary)
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(binary, expected)
    (tmp_path / 'observer.s').write_text(assembly)
    (tmp_path / 'owned.o').write_bytes(first.objects[0])
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    reference = (declaration + 'extern "C" unsigned long long inspect_record(const AbiRecord*r){'
                 'if(r->tag!=-123456789||r->signed_value!=-72623859790382856LL)return 0ULL;return r->wide;}')
    oracle = compile_reference_cpp((('observer.cpp', source), ('reference.cpp', reference)),
                                   tmp_path / 'reference', compiler=compiler)
    record_execution(oracle, expected)
