"""C-linkage ellipsis declarations/calls use the existing C ABI, not erased tails."""
import os
import shutil
import struct
import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import Execution, compile_host_cpp, compile_reference_cpp, write_host_executable
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_owned_execution import owned_only


SOURCE = ('extern "C" int observe(int,...);'
          'int cpp_test_entry(){return observe(42,true,char(2),float(17.0),nullptr);}')
OBSERVER = '''.intel_syntax noprefix
.text
.globl observe
observe:
  cmp edi, 42
  jne bad
  cmp esi, 1
  jne bad
  cmp edx, 2
  jne bad
  test rcx, rcx
  jne bad
  cmp al, 1
  jne bad
  movq rax, xmm0
  mov rdx, 4625478292286210048
  cmp rax, rdx
  jne bad
  mov eax, 42
  ret
bad:
  mov eax, 1
  ret
'''
PROVIDER = ('#include <cstdarg>\nextern "C" int observe(int n,...){va_list args;va_start(args,n);'
            'int a=va_arg(args,int);int b=va_arg(args,int);double c=va_arg(args,double);'
            'void*p=va_arg(args,void*);va_end(args);'
            'return n==42&&a==1&&b==2&&c==17.0&&p==nullptr?42:1;}')


def test_variadic_default_promotions_compile_with_real_ellipsis(owned_only):
    parser = CppParser()
    ast = parser.parse(SOURCE)
    assert parser.scope.names['observe'].overloads[0].variadic
    assert isinstance(ast.ext[0].type.args.params[-1], c_ast.EllipsisParam)
    first = compile_host_cpp((('caller.cpp',SOURCE),), owned_support_assembly=(OBSERVER,))
    second = compile_host_cpp((('caller.cpp',SOURCE),), owned_support_assembly=(OBSERVER,))
    assert first == second
    assert '...' in first.ir[0]


@pytest.mark.integration
def test_variadic_caller_abi_matches_real_cpp17_reference(tmp_path):
    artifact = compile_host_cpp((('caller.cpp',SOURCE),), owned_support_assembly=(OBSERVER,))
    binary = tmp_path/'owned'
    write_host_executable(artifact,binary)
    expected = Execution(42,struct.pack('<i',42),b'')
    record_execution(binary,expected)
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE','g++'))
    assert compiler
    reference = compile_reference_cpp((('caller.cpp',SOURCE),('provider.cpp',PROVIDER)),tmp_path/'reference',compiler=compiler)
    record_execution(reference,expected)


@pytest.mark.parametrize('source,code', [
    ('extern "C" int f(int,...);extern "C" int f(int);','cpp.conflicting-c-linkage-type'),
    ('extern "C" int f(int,...);int main(){return f();}','cpp.no-viable-overload'),
    ('extern "C" int f(int,...,int);','cpp.invalid-parameter'),
    ('struct S{int f(int,...);};','cpp.unsupported.variadic-functions'),
    ('extern "C" int f(...);','cpp.unsupported.variadic-functions'),
])
def test_variadic_invalid_or_unimplemented_forms_are_explicit(source,code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


def test_variadic_redeclaration_retains_c_identity():
    parser = CppParser()
    parser.parse('extern "C" int f(int,...);int f(int...);namespace n{extern "C" int f(int,...);}')
    first = parser.scope.names['f'].overloads[0]
    assert first is parser.scope.names['n'].namespace_scope.names['f'].overloads[0]
    assert first.variadic


def test_fixed_conversion_beats_ellipsis_without_erasing_overloads(owned_only):
    source = ('extern "C" int f(int,...);int f(int,int){return 42;}'
              'int cpp_test_entry(){return f(1,2);}')
    artifact = compile_host_cpp((('overload.cpp',source),))
    assert artifact.executable


ORIGINAL_VARIADIC_DECLARATION = 'extern "C" int f(int,...);'
ORIGINAL_UNITS = (('original.cpp', ORIGINAL_VARIADIC_DECLARATION),
                  ('entry.cpp', 'int cpp_test_entry(){return 42;}'))


def test_original_variadic_declaration_has_explicit_c_abi_artifact(owned_only):
    parser = CppParser()
    ast = parser.parse(ORIGINAL_VARIADIC_DECLARATION)
    symbol = parser.scope.names['f'].overloads[0]
    assert symbol.variadic and symbol.language_linkage == 'C' and symbol.c_name == 'f'
    assert isinstance(ast.ext[0].type.args.params[-1], c_ast.EllipsisParam)
    first, second = compile_host_cpp(ORIGINAL_UNITS), compile_host_cpp(ORIGINAL_UNITS)
    assert first == second
    assert '...' in first.ir[0]


@pytest.mark.integration
def test_original_variadic_declaration_links_with_same_cpp17_reference(tmp_path, monkeypatch):
    from tests.cpp.test_cpp_class_execution import execute_and_record
    execute_and_record(tmp_path, monkeypatch, ORIGINAL_UNITS, 42)


NEWLY_SUPPORTED_ORIGINALS=('extern "C" int f(int,...){return 0;}', 'int f(int,...);')


@pytest.mark.parametrize('source',NEWLY_SUPPORTED_ORIGINALS)
def test_preserved_variadic_definition_and_cpp_declaration_originals(source):
    parser=CppParser()
    tree=parser.parse(source)
    symbol=parser.scope.names['f'].overloads[0]
    assert symbol.variadic
    assert symbol.c_name==('f' if source.startswith('extern') else '_Z1fiz')
    entry='int cpp_test_entry(){return f(7)+42;}' if symbol.defined else 'int cpp_test_entry(){return 42;}'
    units=(('original.cpp',source+entry),)
    assert compile_host_cpp(units)==compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('source',NEWLY_SUPPORTED_ORIGINALS)
def test_preserved_variadic_originals_execute_against_cpp17_reference(source,tmp_path,monkeypatch):
    from tests.cpp.test_cpp_class_execution import execute_and_record
    entry='int cpp_test_entry(){return f(7)+42;}' if source.startswith('extern') else 'int cpp_test_entry(){return 42;}'
    execute_and_record(tmp_path,monkeypatch,(('original.cpp',source+entry),),42)
