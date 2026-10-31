"""Namespace-object identity/initialization; original C++17 source cases.

N4659 [basic.def], [basic.link], [basic.start.static], [dcl.init], [dcl.link].
No class/dynamic initialization runtime is supplied by this frontend slice.
"""
import os
import shutil
import struct
import subprocess

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (
    Execution, compile_host_cpp, compile_reference_cpp, write_host_executable,
)
from tests.cpp.test_cpp_class_execution import execute_and_record, record_execution


CASES = (
    ('global-zero-is-a-definition', 'int value;int cpp_test_entry(){return value+42;}'),
    ('namespace-store', 'namespace n{int value=17;}int cpp_test_entry(){n::value=n::value+25;return n::value;}'),
    ('const-static-initializer-proof', 'const unsigned int x=4294967295U;const unsigned int y=x+1U;'
     'int cpp_test_entry(){return int(y)+42;}'),
    ('extern-then-definition', 'namespace n{extern int value;int read(){return value;}}'
     'namespace n{int value=42;}int cpp_test_entry(){return n::read();}'),
    ('repeat-extern-not-definitions', 'extern int value;extern int value;int value=42;'
     'extern int value;int cpp_test_entry(){return value;}'),
    ('const-inherits-external-linkage', 'extern const int value;const int value=42;'
     'int cpp_test_entry(){return value;}'),
    ('extern-inherits-internal-linkage', 'static int value=42;extern int value;'
     'int cpp_test_entry(){return value;}'),
    ('default-const-linkage-retained', 'const int value=42;extern const int value;'
     'int cpp_test_entry(){return value;}'),
    ('namespace-alias-and-using', 'namespace a{int value=42;}namespace b=a;using b::value;'
     'int cpp_test_entry(){int value=1;return b::value;}'),
    ('inline-and-anonymous-namespace', 'namespace n{inline namespace v{int value=17;}}'
     'namespace{int other=25;}int cpp_test_entry(){return n::value+other;}'),
    ('address-of-static-storage', 'int value=17;int *p=&value;'
     'int cpp_test_entry(){*p=*p+25;return value;}'),
    ('address-of-extern-before-definition', 'namespace n{extern int value;int*p=&value;int value=42;}'
     'int cpp_test_entry(){return *n::p;}'),
    ('later-pointer-definition-keeps-source-order', 'extern int*p;int value=42;int*p=&value;'
     'int cpp_test_entry(){return *p;}'),
    ('const-object-address', 'const int value=42;const int*p=&value;int cpp_test_entry(){return *p;}'),
    ('zero-and-null-pointers', 'int*p;int*q=nullptr;int cpp_test_entry(){if(p!=q)return 1;return 42;}'),
    ('enum-global-nominal-overload', 'enum class E{A=42};E value=E::A;int take(E x){return int(x);}'
     'int take(int x){return 1;}int cpp_test_entry(){return take(value);}'),
    ('unsigned64-static-data', 'unsigned long long value=0xf123456789abc000ULL;'
     'int cpp_test_entry(){if(value!=0xf123456789abc000ULL)return 1;return 42;}'),
    ('storage-specifier-order', 'const extern int value;int const value=42;'
     'int static other=0;int cpp_test_entry(){return value+other;}'),
    ('cpp-linkage-single-is-extern', 'extern "C++" const int value;const int value=42;'
     'int cpp_test_entry(){return value;}'),
    ('cpp-linkage-block-defines', 'extern "C++" {int value=42;}int cpp_test_entry(){return value;}'),
    ('visible-namespace-object-is-not-a-global-entity', 'namespace a{int value=17;}using a::value;'
     'namespace b{extern "C" int value(){return 25;}}int cpp_test_entry(){return ::value+b::value();}'),
    ('unrelated-template-remains-independent', 'int value=41;template<class T>T id(T x){return x;}'
     'int cpp_test_entry(){return id(value)+1;}'),
)

INVALID = (
    ('int value;int value;', 'cpp.redefinition'),
    ('extern int value;long value;', 'cpp.conflicting-object-type'),
    ('extern int value;const int value=1;', 'cpp.conflicting-object-type'),
    ('extern int value;static int value;', 'cpp.conflicting-linkage'),
    ('const int value;', 'cpp.const-initializer'),
    ('int main;', 'cpp.main-object'),
    ('namespace a{int value;}using a::value;extern int value;', 'cpp.using-declaration-conflict'),
    ('namespace a{extern "C" int value();}int value;', 'cpp.c-linkage-name-conflict'),
    ('int value;namespace a{extern "C" int value();}', 'cpp.c-linkage-name-conflict'),
    ('extern "C++" int extern value;', 'cpp.linkage-storage'),
    ('int static extern value;', 'cpp.invalid-storage-specifier'),
    ('const int value=42;int f(){value=1;return 0;}', 'cpp.const-assignment'),
    ('int value;int value(){return 1;}', 'cpp.redeclaration'),
)

UNIMPLEMENTED = (
    'int get();int value=get();',
    'int a=42;int b=a;',
    'double value=1.5;',
    'int value{42};',
    'int a=1,b=2;',
    'struct S{int value;};S object;',
    'inline int value=42;',
    'thread_local int value=42;',
    'int value=42;template<class T>T get(T x){return value;}',
    'namespace{extern int value;}',
    'struct S{};int S;',
    'int S;struct S{};',
    'int E;enum E{A};',
    'int *get();int *p=get();',
)


@pytest.mark.parametrize('name,source', CASES, ids=[x[0] for x in CASES])
def test_namespace_objects_compile_twice_without_byte_drift(name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('source,code', INVALID)
def test_namespace_object_errors_precede_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid namespace object reached emission')
    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('invalid.cpp', source),))
    assert error.value.code == code


@pytest.mark.parametrize('source', UNIMPLEMENTED)
def test_unimplemented_namespace_initialization_fails_explicitly(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, 'not-yet.cpp')
    assert error.value.code.startswith('cpp.unsupported.'), str(error.value)


def test_redeclarations_keep_one_symbol_one_non_tentative_definition():
    parser = CppParser()
    ast = parser.parse('namespace n{extern int value;}namespace n{int value=42;extern int value;}')
    symbol = parser.scope.names['n'].namespace_scope.names['value']
    assert symbol.defined and symbol.initialization_complete
    assert symbol.c_name == '_ZN1n5valueE' and not symbol.internal_linkage
    objects = [node for node in ast.ext if isinstance(node, c_ast.Decl) and node.name == symbol.c_name]
    assert len(objects) == 2
    assert objects[0].init is None and objects[0].storage == ["extern"]
    assert objects[1].init is not None and objects[1].storage == []


def test_default_const_and_prior_extern_linkage_are_distinct():
    parser = CppParser()
    parser.parse('const int local=1;extern const int exported;const int exported=2;')
    local, exported = parser.scope.names['local'], parser.scope.names['exported']
    assert local.internal_linkage and local.c_name == '_ZL5local'
    assert not exported.internal_linkage and exported.c_name == 'exported'


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES, ids=[x[0] for x in CASES])
def test_namespace_object_execution_matches_explicit_cpp17_reference(name, source, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name + '.cpp', source),), 42)


@pytest.mark.integration
def test_namespace_object_external_symbols_across_two_translation_units(tmp_path, monkeypatch):
    units = (('helper.cpp', 'namespace n{int value=17;extern const int delta=25;}'),
             ('entry.cpp', 'namespace n{extern int value;extern const int delta;}'
              'int cpp_test_entry(){n::value=n::value+n::delta;return n::value;}'))
    execute_and_record(tmp_path, monkeypatch, units, 42)


@pytest.mark.integration
def test_internal_objects_with_same_spelling_are_distinct_across_translation_units(tmp_path, monkeypatch):
    units = (('a.cpp', 'namespace n{const int value=17;}int first(){return n::value;}'),
             ('b.cpp', 'namespace n{const int value=25;}int second(){return n::value;}'),
             ('entry.cpp', 'int first();int second();int cpp_test_entry(){return first()+second();}'))
    execute_and_record(tmp_path, monkeypatch, units, 42)


@pytest.mark.integration
def test_namespace_data_names_and_full_width_storage_have_an_independent_observer(tmp_path, monkeypatch):
    source = ('namespace n{int value=17;unsigned long long bits=0xf123456789abc000ULL;}'
              'extern "C" int inspect_data();int cpp_test_entry(){return inspect_data();}')
    assembly = ('.intel_syntax noprefix\n.text\n.globl inspect_data\ninspect_data:\n'
                ' mov eax,dword ptr _ZN1n5valueE[rip]\n cmp eax,17\n jne bad_data\n'
                ' mov rax,qword ptr _ZN1n4bitsE[rip]\n mov rdx,0xf123456789abc000\n'
                ' cmp rax,rdx\n jne bad_data\n mov eax,42\n ret\nbad_data:\n mov eax,1\n ret\n')
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned namespace data compilation invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        artifact = compile_host_cpp((('data.cpp', source),), owned_support_assembly=(assembly,))
        assert artifact == compile_host_cpp((('data.cpp', source),), owned_support_assembly=(assembly,))
    binary = tmp_path / 'owned'
    write_host_executable(artifact, binary)
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(binary, expected)
    (tmp_path / 'observer.s').write_text(assembly)
    helper = ('namespace n{extern int value;extern unsigned long long bits;}'
              'extern "C" int inspect_data(){if(n::value!=17)return 1;'
              'if(n::bits!=0xf123456789abc000ULL)return 1;return 42;}')
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    oracle = compile_reference_cpp((('data.cpp', source), ('observer.cpp', helper)),
                                   tmp_path / 'reference', compiler=compiler)
    record_execution(oracle, expected)


PUBLIC_SOURCE = 'namespace n{int value=17;}int main(){n::value=n::value+25;return n::value;}'


def test_namespace_objects_use_the_real_public_preprocess_and_pass_pipeline():
    from tests.cpp.test_cpp_public_driver import compile_cpp
    first = compile_cpp(PUBLIC_SOURCE, 'public-namespace-object.cpp', use_system_cpp=False)
    assert first == compile_cpp(PUBLIC_SOURCE, 'public-namespace-object.cpp', use_system_cpp=False)
    assert '_ZN1n5valueE' in first[0][1]


@pytest.mark.integration
def test_public_namespace_object_artifact_executes_without_external_build_tools(tmp_path, monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    from tests.cpp.owned_execution import TARGET
    from tests.cpp.test_cpp_public_driver import compile_cpp
    from tests.cpp.test_cpp_public_driver_execution import link_object
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned public global object compilation invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        records = compile_cpp(PUBLIC_SOURCE, 'public-namespace-object.cpp', use_system_cpp=False)
        first = emit_owned_object(records[0][1], TARGET)
        assert first == emit_owned_object(records[0][1], TARGET)
        executable = link_object(first)
    binary = tmp_path / 'owned'
    binary.write_bytes(executable)
    binary.chmod(0o700)
    expected = Execution(42, b'', b'')
    record_execution(binary, expected)
    source = tmp_path / 'public.cpp'
    source.write_text(PUBLIC_SOURCE)
    oracle = tmp_path / 'oracle'
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-O0', str(source), '-o', str(oracle)],
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    record_execution(oracle, expected)


def test_original_c_object_declaration_is_now_supported_without_source_rewrite():
    # Exact former capability case; a declaration does not define storage.
    parser = CppParser()
    parser.parse('extern "C" int value;')
    symbol = parser.scope.names['value']
    assert symbol.language_linkage == "C" and not symbol.defined
    assert symbol.c_name == symbol.name and symbol.dtype == "int"
