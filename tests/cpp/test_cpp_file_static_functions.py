"""N4659 [basic.link]/3-4, [dcl.stc]/2: ordinary TU-local functions.

Observe source linkage, canonical redeclarations, the Itanium local
source-name marker and the owned ELF binding, not the spelling order of
storage specifiers.
"""

import os
import shutil
import struct

import pytest

from pcc.backend.elf_x86_64 import parse_relocatable
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
    run_executable, write_host_executable,
)


PROGRAMS = (
    ('plain-static-separate-tus', (
        ('helper.cpp', 'static int value(){return 17;}int helper(){return value();}'),
        ('entry.cpp', 'static int value(){return 25;}int helper();int cpp_test_entry(){return helper()+value();}'),
    )),
    ('forward-declaration-retains-linkage', (
        ('caller.cpp', 'static int value(int);int helper(){return value(17);}int value(int n){return n;}'),
        ('entry.cpp', 'static int value(int);extern int value(int n){return n;}int helper();'
         'int cpp_test_entry(){return helper()+value(25);}'),
    )),
    ('namespace-static-separate-tus', (
        ('helper.cpp', 'namespace N{static int value(){return 17;}}int helper(){return N::value();}'),
        ('entry.cpp', 'namespace N{static int value(){return 25;}}int helper();'
         'int cpp_test_entry(){return helper()+N::value();}'),
    )),
    ('qualified-definition-after-static-prototype', (
        ('entry.cpp', 'namespace N{static int value(int);}int N::value(int x){return x;}'
         'int cpp_test_entry(){return N::value(42);}'),
    )),
    ('independent-overload-linkage', (
        ('helper.cpp', 'int value(long n){return int(n)+17;}'),
        ('entry.cpp', 'static int value(int n){return n;}int value(long);'
         'int cpp_test_entry(){return value(24)+value(1L);}'),
    )),
    ('specifier-order-and-inline-prototype', (
        ('entry.cpp', 'inline static int first();int static inline first(){return 17;}'
         'static int second();inline int second(){return 25;}'
         'int cpp_test_entry(){return first()+second();}'),
    )),
    ('named-static-inline-local-state', (
        ('helper.cpp', 'namespace N{inline static int value(){static int n=15;return ++n;}}'
         'int helper(){N::value();return N::value();}'),
        ('entry.cpp', 'namespace N{static int inline value(){static int n=23;return ++n;}}'
         'int helper();int cpp_test_entry(){N::value();return helper()+N::value();}'),
    )),
    ('static-pointer-result-and-parameter', (
        ('entry.cpp', 'struct Record{int value;};static Record*identity(Record*p){return p;}'
         'int cpp_test_entry(){Record r{42};return identity(&r)->value;}'),
    )),
    ('cxx-language-linkage-static', (
        ('entry.cpp', 'extern "C++"{static int value(){return 42;}}'
         'int cpp_test_entry(){return value();}'),
    )),
    ('unnamed-namespace-inline', (
        ('entry.cpp', 'namespace { inline int f(int n){return n;} }'
         'int cpp_test_entry(){return f(42);}'),
    )),
    ('unused-internal-prototype', (
        ('entry.cpp', 'static int never_used(int);int cpp_test_entry(){return 42;}'),
    )),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[p[0] for p in PROGRAMS])
def test_static_function_originals_have_owned_elf_artifacts(name, units):
    artifact = compile_host_cpp(units)
    assert artifact == compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[p[0] for p in PROGRAMS])
def test_static_function_originals_match_cpp17(tmp_path, name, units):
    artifact = compile_host_cpp(units)
    owned = tmp_path / 'owned'
    write_host_executable(artifact, owned)
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    reference = compile_reference_cpp(units, tmp_path / 'reference', compiler=compiler)
    expected = Execution(42, struct.pack('<i', 42), b'')
    assert_exact(run_executable(owned), expected)
    assert_exact(run_executable(reference), expected)


@pytest.mark.parametrize('name,suffix', (
    ('plain-static-separate-tus', '_ZL5valuev'),
    ('namespace-static-separate-tus', '_ZN1NL5valueEv'),
    ('forward-declaration-retains-linkage', '_ZL5valuei'),
))
def test_static_functions_are_local_and_never_coalesced(name, suffix):
    artifact = compile_host_cpp(dict(PROGRAMS)[name])
    names = []
    for data in artifact.objects:
        matches = [symbol for symbol in parse_relocatable(data).symbols
                   if symbol.name.endswith(suffix)]
        assert len(matches) == 1
        assert matches[0].binding == 0 and matches[0].section_index
        names.append(matches[0].name)
    assert len(set(names)) == len(artifact.objects)


def test_static_prototype_and_definition_share_canonical_identity():
    parser = CppParser()
    tree = parser.parse('namespace N{static int answer(int);}'
                        'int N::answer(int x){return x;}')
    symbol, = parser.scope.names['N'].namespace_scope.names['answer'].overloads
    assert symbol.internal_linkage and symbol.defined
    assert symbol.c_name == '_ZN1NL6answerEi'
    from pcc.frontends.c.ast import c_ast
    decls = [node.decl if isinstance(node, c_ast.FuncDef) else node for node in tree.ext]
    assert [(decl.name, decl.storage) for decl in decls] == [
        (symbol.c_name, ['static']), (symbol.c_name, ['static']),
    ]


@pytest.mark.parametrize('source,code', (
    ('int f();static int f(){return 1;}', 'cpp.conflicting-function-linkage'),
    ('namespace N{int f();static int f(){return 1;}}', 'cpp.conflicting-function-linkage'),
    ('static int main(){return 0;}', 'cpp.invalid-main-linkage'),
    ('static static int f(){return 0;}', 'cpp.invalid-storage-specifier'),
    ('template<class T>static int f(T n){return 0;}', 'cpp.unsupported.function-storage'),
    ('extern "C"{static int f(){return 1;}}', 'cpp.unsupported.c-linkage-internal'),
    ('inline int f(){return 1;}', 'cpp.unsupported.inline-declarations'),
    ('namespace N{inline int f();}', 'cpp.unsupported.inline-declarations'),
    ('static inline int x=0;', 'cpp.unsupported.inline-declarations'),
))
def test_unsupported_or_conflicting_linkage_never_reaches_projection(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


def test_missing_internal_definition_cannot_resolve_to_another_tu():
    from pcc.backend.elf_x86_64 import ElfError
    units = (
        ('missing.cpp', 'static int value();int cpp_test_entry(){return value();}'),
        ('other.cpp', 'static int value(){return 42;}int other(){return value();}'),
    )
    with pytest.raises(ElfError, match='undefined static ELF symbols'):
        compile_host_cpp(units)
