"""C language linkage of external object names, not of their C++ types.

Original PCC cases: N4659 [dcl.link]/4-7, [basic.link], [basic.def].
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
Opaque pointers remain nominal and incomplete. This supplies no FILE runtime,
locale, initialization runtime, cross-language record layout or variadic ABI.
"""
import os
import re
import shutil
import struct
import subprocess

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ConstType, PointerType
from tests.cpp.owned_execution import (
    Execution, compile_host_cpp, compile_reference_cpp, write_host_executable,
)
from tests.cpp.test_cpp_class_execution import execute_and_record, record_execution


CASES = (
    ('single-declaration-then-definition', 'extern "C" int value;int value=42;int cpp_test_entry(){return value;}'),
    ('block-zero-is-definition', 'extern "C"{int value;}int cpp_test_entry(){return value+42;}'),
    ('single-initializer-is-definition', 'extern "C" int value=42;int cpp_test_entry(){return value;}'),
    ('cross-namespace-identity', 'namespace a{extern "C" int value;}'
     'namespace b{extern "C" int value=17;}int cpp_test_entry(){a::value=a::value+25;return b::value;}'),
    ('plain-redeclaration-in-second-namespace', 'namespace a{extern "C" int value;}'
     'namespace b{extern "C" int value;int value=42;}int cpp_test_entry(){return a::value;}'),
    ('global-c-declaration-after-namespaced-one', 'namespace a{extern "C" int value;}'
     'extern "C" int value=42;int cpp_test_entry(){return a::value;}'),
    ('namespace-c-declaration-after-global-one', 'extern "C" int value=42;'
     'namespace a{extern "C" int value;}int cpp_test_entry(){return a::value;}'),
    ('repeated-import-of-same-c-object', 'namespace a{extern "C" int value;}'
     'namespace b{extern "C" int value=42;}using a::value;using b::value;'
     'int cpp_test_entry(){return value;}'),
    ('c-redeclaration-after-using-same-entity', 'namespace a{extern "C" int value=42;}'
     'namespace b{using a::value;extern "C" int value;}int cpp_test_entry(){return b::value;}'),
    ('inline-alias-and-local-shadow', 'namespace a{inline namespace v{extern "C" int value=42;}}'
     'namespace alias=a;int cpp_test_entry(){int value=1;return alias::value;}'),
    ('parameter-shadow-keeps-qualified-object', 'namespace a{extern "C" int value=17;}'
     'int f(int value){return value+a::value;}int cpp_test_entry(){return f(25);}'),
    ('array-shadow-keeps-qualified-object', 'namespace a{extern "C" int value=17;}'
     'int cpp_test_entry(){int value[2]={25,1};return value[0]+a::value;}'),
    ('class-shadow-keeps-qualified-object', 'struct R{int x;};namespace a{extern "C" int value=17;}'
     'int cpp_test_entry(){R value{25};return value.x+a::value;}'),
    ('for-shadow-keeps-qualified-object', 'namespace a{extern "C" int value=42;}'
     'int cpp_test_entry(){int result=0;for(int value=0;value<1;++value){result=a::value;}return result;}'),
    ('template-parameter-shadow-keeps-qualified-c-function', 'namespace a{extern "C" int value(int x){return x;}}'
     'template<class T>int f(T value){return a::value(value);}int cpp_test_entry(){return f(42);}'),
    ('external-const-single', 'namespace a{extern "C" const int value=42;}'
     'namespace b{extern "C" const int value;}int cpp_test_entry(){return b::value;}'),
    ('external-const-block', 'extern "C"{extern const int value;const int value=42;}'
     'int cpp_test_entry(){return value;}'),
    ('internal-const-does-not-share-c-name', 'namespace a{extern "C"{const int value=17;}}'
     'namespace b{extern "C" int value=25;}int cpp_test_entry(){return a::value+b::value;}'),
    ('internal-static-does-not-share-c-name', 'namespace a{extern "C"{static int value=17;}}'
     'namespace b{extern "C" int value=25;}int cpp_test_entry(){return a::value+b::value;}'),
    ('plain-namespace-object-independent', 'namespace a{int value=17;}'
     'namespace b{extern "C" int value=25;}int cpp_test_entry(){return a::value+b::value;}'),
    ('cpp-function-and-namespaced-c-object', 'int value(){return 17;}'
     'namespace b{extern "C" int value=25;}int cpp_test_entry(){return value()+b::value;}'),
    ('c-object-pointer-static-relocation', 'namespace a{extern "C" int value;}'
     'extern "C" int*selected=&a::value;namespace b{extern "C" int value=42;}'
     'int cpp_test_entry(){return *selected;}'),
    ('opaque-object-pointer-is-not-layout', 'struct Opaque;namespace a{extern "C" Opaque*stream;}'
     'namespace b{extern "C" Opaque*stream=nullptr;}int cpp_test_entry(){if(a::stream!=nullptr)return 1;return 42;}'),
    ('qualified-pointer-type-is-preserved', 'const int value=42;extern "C" const int*selected=&value;'
     'int cpp_test_entry(){return *selected;}'),
    ('enum-object-retains-type', 'enum class E{Answer=42};namespace a{extern "C" E value=E::Answer;}'
     'int take(E x){return int(x);}int take(int x){return 1;}int cpp_test_entry(){return take(a::value);}'),
    ('nested-linkage-restoration', 'namespace n{extern "C"{int a=17;extern "C++"{int b=25;}}}'
     'int cpp_test_entry(){return n::a+n::b;}'),
    ('internal-prior-linkage-is-not-overridden', 'namespace a{static int value=17;extern "C" int value;}'
     'namespace b{extern "C" int value=25;}int cpp_test_entry(){return a::value+b::value;}'),
)

INVALID = (
    ('extern int value;extern "C" int value;', 'cpp.conflicting-language-linkage'),
    ('extern "C" int value;extern "C++" int value;', 'cpp.conflicting-language-linkage'),
    ('extern "C++" int value;extern "C" int value;', 'cpp.conflicting-language-linkage'),
    ('extern "C"{int value;}int value;', 'cpp.redefinition'),
    ('namespace a{extern "C" int value=1;}namespace b{extern "C" int value=2;}', 'cpp.redefinition'),
    ('namespace a{extern "C" int value;}namespace b{extern "C" long value;}', 'cpp.conflicting-c-linkage-type'),
    ('namespace a{extern "C" int*value;}namespace b{extern "C" const int*value;}', 'cpp.conflicting-c-linkage-type'),
    ('namespace a{struct R;extern "C" R*value;}namespace b{struct R;extern "C" R*value;}', 'cpp.conflicting-c-linkage-type'),
    ('extern "C" int value;const int value=1;', 'cpp.conflicting-object-type'),
    ('extern "C" int value;static int value;', 'cpp.conflicting-linkage'),
    ('namespace a{extern "C" int value;}namespace b{extern "C" int value();}', 'cpp.c-linkage-name-conflict'),
    ('namespace a{extern "C" int value();}namespace b{extern "C" int value;}', 'cpp.c-linkage-name-conflict'),
    ('namespace a{extern "C" int value;}int value;', 'cpp.c-linkage-name-conflict'),
    ('int value;namespace a{extern "C" int value;}', 'cpp.c-linkage-name-conflict'),
    ('static int value;namespace a{extern "C" int value;}', 'cpp.c-linkage-name-conflict'),
    ('namespace a{extern "C" int value;}const int value=1;', 'cpp.c-linkage-name-conflict'),
    ('extern "C"{const int value;}', 'cpp.const-initializer'),
    ('extern "C" static int value;', 'cpp.linkage-storage'),
    ('extern "C" extern int value;', 'cpp.linkage-storage'),
    ('namespace a{extern "C" int value;}int f(){return value;}', 'cpp.undeclared'),
    ('namespace a{extern "C" int value=1;}using a::value;int value;', 'cpp.using-declaration-conflict'),
    ('namespace a{extern "C" int value;}namespace b{extern "C" int value;}namespace b{extern "C++" int value;}', 'cpp.conflicting-language-linkage'),
    ('namespace a{extern "C" int value;}namespace b{extern "C" int value;}namespace c{int value;}namespace b{using c::value;}', 'cpp.ambiguous-name'),
    ('extern "C" const int value=42;int f(){value=1;return 0;}', 'cpp.const-assignment'),
)

UNSUPPORTED = (
    'struct R{int x;};extern "C" R value;',
    'extern "C" int value[2];',
    'int get();extern "C" int value=get();',
    'extern "C"{thread_local int value;}',
    'extern "C"{inline int value=1;}',
)


@pytest.mark.parametrize('name,source', CASES, ids=[case[0] for case in CASES])
def test_c_object_artifacts_are_byte_stable(name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('source,code', INVALID)
def test_c_object_errors_precede_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid C object declaration reached emission')
    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('invalid.cpp', source),))
    assert error.value.code == code


@pytest.mark.parametrize('source', UNSUPPORTED)
def test_c_objects_do_not_complete_initialization_or_storage_domains(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code.startswith('cpp.unsupported.'), str(error.value)


def test_cross_namespace_object_is_one_identity_with_ordered_declarations():
    parser = CppParser()
    ast = parser.parse('namespace a{extern "C" int value;}'
                       'namespace b{extern "C" int value;}namespace b{int value=42;}')
    a = parser.scope.names['a'].namespace_scope
    b = parser.scope.names['b'].namespace_scope
    symbol = a.names['value']
    assert symbol is b.names['value'] is parser.scope.c_linkage_objects['value']
    assert symbol.owner is a and symbol.declaration_scopes == [a, b]
    assert symbol.language_linkage == 'C' and symbol.c_name == 'value'
    assert symbol.defined and symbol.initialization_complete
    decls = [node for node in ast.ext if isinstance(node, c_ast.Decl) and node.name == 'value']
    assert len(decls) == 2 and decls[0].init is None and decls[0].storage == ['extern']
    assert decls[1].init is not None and decls[1].storage == []


def test_opaque_pointer_object_keeps_nominal_const_type():
    parser = CppParser()
    parser.parse('namespace a{struct Opaque;extern "C" const Opaque*stream;}'
                 'namespace b{using Alias=a::Opaque;extern "C" const Alias*stream;}')
    a = parser.scope.names['a'].namespace_scope
    b = parser.scope.names['b'].namespace_scope
    opaque = a.names['Opaque'].dtype
    assert opaque.definition is None
    assert a.names['stream'] is b.names['stream']
    assert a.names['stream'].dtype == PointerType(ConstType(opaque))
    assert not a.names['stream'].defined


def test_internal_and_external_language_name_registries_are_distinct():
    parser = CppParser()
    parser.parse('namespace a{extern "C"{static int value=17;const int other=25;}}'
                 'namespace b{extern "C" int value=42;}')
    a = parser.scope.names['a'].namespace_scope
    b = parser.scope.names['b'].namespace_scope
    assert a.names['value'].internal_linkage and a.names['other'].internal_linkage
    assert a.names['value'].language_linkage == 'C++'
    assert parser.scope.c_linkage_objects == {'value': b.names['value']}


def test_c_linkage_external_object_public_pipeline_keeps_undefined_symbol(monkeypatch):
    from pcc.backend.elf_x86_64 import _object_symbol_sets, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from tests.cpp.owned_execution import TARGET
    source = ('struct Opaque;namespace owned{extern "C" Opaque*stream;}'
              'int main(){if(owned::stream==nullptr)return 42;return 1;}')
    def compile_once():
        return CEvaluator(backend='self', target_triple=TARGET).compile_translation_units(
            [TranslationUnit('public-c-object.cpp', None, source)], use_system_cpp=False,
            use_compile_cache=False, frontend_opt_level=0,
        )
    def forbidden(*args, **kwargs):
        raise AssertionError('owned C object compilation invoked an external process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    first = compile_once()
    assert first == compile_once()
    obj = emit_owned_object(first[0][1], TARGET)
    assert obj == emit_owned_object(first[0][1], TARGET)
    defined, undefined = _object_symbol_sets(parse_relocatable(obj))
    assert 'main' in defined and 'stream' not in defined
    assert undefined == frozenset(('stream',))


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES, ids=[case[0] for case in CASES])
def test_c_objects_execute_against_explicit_cpp17_reference(name, source, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name + '.cpp', source),), 42)


@pytest.mark.integration
def test_c_objects_cross_translation_units_and_namespaces(tmp_path, monkeypatch):
    units = (
        ('define.cpp', 'namespace a{extern "C"{int value=17;extern const int delta=25;}}'),
        ('use.cpp', 'namespace b{extern "C" int value;extern "C" const int delta;}'
         'int cpp_test_entry(){b::value=b::value+b::delta;return b::value;}'),
    )
    execute_and_record(tmp_path, monkeypatch, units, 42)


@pytest.mark.integration
def test_c_object_external_names_and_storage_have_an_independent_observer(tmp_path, monkeypatch):
    source = ('namespace api{extern "C"{int marker=-123456789;'
              'unsigned long long wide=0xfedcba9876543210ULL;'
              'int*selected=&marker;}}'
              'extern "C" int inspect_objects();int cpp_test_entry(){return inspect_objects();}')
    assembly = ('.intel_syntax noprefix\n.text\n.globl inspect_objects\ninspect_objects:\n'
                ' mov eax,dword ptr marker[rip]\n mov ecx,-123456789\n cmp eax,ecx\n jne .Lbad\n'
                ' mov rax,qword ptr wide[rip]\n mov rcx,0xfedcba9876543210\n cmp rax,rcx\n jne .Lbad\n'
                ' mov rax,qword ptr selected[rip]\n lea rcx,marker[rip]\n cmp rax,rcx\n jne .Lbad\n'
                ' mov eax,42\n ret\n.Lbad:\n mov eax,1\n ret\n')
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned C object observer invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        first = compile_host_cpp((('observer.cpp', source),), owned_support_assembly=(assembly,))
        assert first == compile_host_cpp((('observer.cpp', source),), owned_support_assembly=(assembly,))
    binary = tmp_path / 'owned'
    write_host_executable(first, binary)
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(binary, expected)
    (tmp_path / 'observer.s').write_text(assembly)
    (tmp_path / 'owned.o').write_bytes(first.objects[0])
    helper = ('extern "C"{extern int marker;extern unsigned long long wide;extern int*selected;}'
              'extern "C" int inspect_objects(){if(marker!=-123456789)return 1;'
              'if(wide!=0xfedcba9876543210ULL||selected!=&marker)return 2;return 42;}')
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    oracle = compile_reference_cpp((('observer.cpp', source), ('helper.cpp', helper)),
                                   tmp_path / 'reference', compiler=compiler)
    record_execution(oracle, expected)
