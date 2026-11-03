"""Original N4659 [expr.new]/[new.delete.placement]/[basic.life] regressions.

Reserved nonallocating placement, existing complete-object C1/D1, normal exits.
The explicit standard signature below is genuine C++ source on Linux LP64;
no source rewriting or helper replacement function is supplied. The external
reference explicitly force-includes its real <new> to provide the reserved
inline definition: TU bytes match, effective include closures do not. Full owned
<new>, general heap allocation and exception cleanup remain gaps.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp
from pcc.frontends.cpp.types import PointerType, mangle_function
from tests.cpp.owned_execution import (
    Execution, compile_host_cpp, write_host_executable,
)
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_owned_execution import owned_only


def compile_reference_with_new(units, directory, *, observer=True, force_header=True):
    """Explicit external library prerequisite, not identical include closure."""
    directory.mkdir()
    paths = []
    original_hashes = {}
    for filename, source in units:
        path = directory / filename
        path.write_bytes(source.encode())
        original_hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
        paths.append(str(path))
    if observer:
        driver = directory / 'pcc_test_observer.cpp'
        driver.write_text('#include <cstdio>\nint cpp_test_entry();\n'
                          'int main(){int result=cpp_test_entry();unsigned value=static_cast<unsigned>(result);'
                          'unsigned char bytes[4];for(unsigned i=0;i<4;++i)bytes[i]=value>>(8*i);'
                          'if(std::fwrite(bytes,1,4,stdout)!=4)return 125;return result;}\n')
        paths.append(str(driver))
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    binary, dependencies = directory / 'oracle', directory / 'oracle.d'
    # -MD includes actual system headers: the provider identity is not guessed
    # from the host's default version or a differently configured compiler.
    command = [compiler, '-std=c++17', '-pedantic-errors', '-O0', '-MD', '-MF', str(dependencies)]
    if force_header:
        command += ['-include', 'new']
    command += [*paths, '-o', str(binary)]
    result = subprocess.run(command, capture_output=True, timeout=30)
    (directory / 'compile.stdout').write_bytes(result.stdout)
    (directory / 'compile.stderr').write_bytes(result.stderr)
    providers = {}
    if dependencies.exists():
        tokens = shlex.split(dependencies.read_text().replace('\\\n', ' '))
        for token in tokens[1:]:
            path = Path(token)
            if path.is_file():
                providers[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / 'reference-environment.json').write_text(json.dumps({
        'command': command, 'compiler_sha256': hashlib.sha256(Path(compiler).resolve().read_bytes()).hexdigest(),
        'program_tu_sha256': original_hashes, 'provider_sha256': providers,
        'returncode': result.returncode, 'forced_standard_header': 'new' if force_header else None,
        'qualification': 'external reference; identical program TU bytes, different standard-header environment',
    }, indent=2) + '\n')
    assert result.returncode == 0, result.stderr
    assert any(Path(path).name == 'new' for path in providers)
    return binary


DECLARATION = 'using size_type=unsigned long;void* operator new(size_type,void*) noexcept;'
CASES = (
    ('placement-address-and-value', 'struct S{int n;S(int);};S::S(int n):n(n){}'
     'int cpp_test_entry(){long long raw[4];void* storage=raw;S*p=new(storage)S(42);'
     'if(p!=storage)return 1;return p->n;}'),
    ('globally-qualified-placement', 'struct S{int n;S(int);};S::S(int n):n(n){}'
     'int cpp_test_entry(){long long raw[4];S*p=::new(raw)S(42);return p->n;}'),
    ('storage-before-argument-once',
     'void* place(void*p,int*log){*log=*log*10+1;return p;}'
     'int argument(int*log){*log=*log*10+2;return 42;}'
     'struct S{int n;S(int,int*);};S::S(int n,int*log):n(n){*log=*log*10+3;}'
     'int cpp_test_entry(){long long raw[4];int log=0;'
     'S*p=new(place(raw,&log))S(argument(&log),&log);if(log!=123)return 1;return p->n;}'),
    ('constructor-overload', 'struct S{int n;S(int);S(long);};'
     'S::S(int n):n(n){}S::S(long n):n(1){}'
     'int cpp_test_entry(){long long raw[4];S*p=new(raw)S(42);return p->n;}'),
    ('user-default-without-initializer', 'struct S{int n;S();};S::S():n(42){}'
     'int cpp_test_entry(){long long raw[4];S*p=new(raw)S;return p->n;}'),
    ('user-default-with-empty-initializer', 'struct S{int n;S();};S::S():n(42){}'
     'int cpp_test_entry(){long long raw[4];S*p=new(raw)S();return p->n;}'),
    ('const-complete-object', 'struct S{const int n;S(int);};S::S(int n):n(n){}'
     'int cpp_test_entry(){long long raw[4];const S*p=new(raw)const S(42);return p->n;}'),
    ('no-automatic-placement-destruction', 'int destroyed=0;'
     'struct S{int n;S(int);~S();};S::S(int n):n(n){}S::~S(){destroyed+=1;}'
     'int work(){long long raw[4];S*p=new(raw)S(42);return p->n;}'
     'int cpp_test_entry(){int n=work();if(destroyed!=0)return 1;return n;}'),
    ('explicit-destruction-once', 'int destroyed=0;'
     'struct S{int n;S(int);~S();};S::S(int n):n(n){}S::~S(){destroyed+=n;}'
     'S* receiver(S*p,int*count){*count+=1;return p;}'
     'int cpp_test_entry(){long long raw[4];S*p=new(raw)S(42);int count=0;'
     'receiver(p,&count)->~S();if(count!=1)return 1;return destroyed;}'),
    ('destructor-type-alias', 'int destroyed=0;'
     'struct S{int n;S(int);~S();};using Alias=S;'
     'S::S(int n):n(n){}S::~S(){destroyed+=n;}'
     'int cpp_test_entry(){long long raw[4];S*p=new(raw)S(42);p->~Alias();return destroyed;}'),
    ('same-type-transparent-replacement', 'int destroyed=0;'
     'struct S{int n;S(int);~S();};S::S(int n):n(n){}S::~S(){destroyed+=n;}'
     'int work(){S s(1);s.~S();S*p=new(&s)S(41);'
     'if(p!=&s||s.n!=41||destroyed!=1)return 0;return p->n;}'
     'int cpp_test_entry(){int n=work();if(n!=41||destroyed!=42)return 1;return 42;}'),
    ('effectful-dot-destruction', 'int destroyed=0;'
     'struct S{int n;S(int);~S();};S::S(int n):n(n){}S::~S(){destroyed+=n;}'
     'S* receiver(S*p,int*count){*count+=1;return p;}'
     'int cpp_test_entry(){long long raw[4];S*p=new(raw)S(42);int count=0;'
     '(*receiver(p,&count)).~S();if(count!=1)return 1;return destroyed;}'),
    ('outer-alias-despite-different-class-alias',
     'struct S{using Alias=int;int n;S(int);};S::S(int n):n(n){}using Alias=S;'
     'int cpp_test_entry(){long long raw[4];S*p=new(raw)S(42);int n=p->n;p->~Alias();return n;}'),
    ('outer-type-despite-same-named-data-member',
     'struct S{int S;};int cpp_test_entry(){S object;object.~S();return 42;}'),
    ('injected-class-name-outside-owner-namespace',
     'namespace N{struct S{int n;};}int cpp_test_entry(){N::S object;object.~S();return 42;}'),
    ('trivial-explicit-destruction', 'struct S{int n;S(int);};S::S(int n):n(n){}'
     'int cpp_test_entry(){long long raw[4];S*p=new(raw)S(42);int n=p->n;p->~S();return n;}'),
)


@pytest.mark.parametrize('name,body', CASES, ids=[name for name, _ in CASES])
def test_placement_originals_have_stable_owned_artifacts(name, body, owned_only):
    units = ((name + '.cpp', DECLARATION + body),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,body', CASES, ids=[name for name, _ in CASES])
def test_placement_originals_execute_against_genuine_cpp17_reference(name, body, tmp_path, monkeypatch):
    units = ((name + '.cpp', DECLARATION + body),)
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned placement compilation invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        artifact = compile_host_cpp(units)
        assert artifact == compile_host_cpp(units)
    owned = tmp_path / 'owned'
    write_host_executable(artifact, owned)
    oracle = compile_reference_with_new(units, tmp_path / 'reference')
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(owned, expected)
    record_execution(oracle, expected)


def test_reserved_operator_declaration_preserves_exact_cpp_signature_and_name():
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    parser.parse(DECLARATION)
    function = parser.scope.names['operator new'].overloads[0]
    assert function.dtype == PointerType('void')
    assert function.parameters == ('unsigned long', PointerType('void'))
    assert function.nonthrowing and function.language_linkage == 'C++'
    assert function.c_name == '_ZnwmPv' and not function.defined
    assert mangle_function('operator new[]', function.parameters) == '_ZnamPv'
    assert mangle_function('operator delete', (PointerType('void'), PointerType('void'))) == '_ZdlPvS_'


def test_user_provided_default_placement_does_not_invent_zero_initialization():
    parser = CppParser()
    tree = parser.parse(DECLARATION + 'struct S{int untouched;S();};S::S(){}'
                        'int f(){long long raw[4];S*p=new(raw)S();return 42;}')
    assignments = []
    class Assignments(c_ast.NodeVisitor):
        def visit_Assignment(self, node):
            assignments.append(node)
    Assignments().visit(tree)
    assert assignments == []


@pytest.mark.parametrize('body,code', (
    ('struct S{int n;S();};S::S(){}int f(){S*p=new S;return 0;}', 'cpp.unsupported.allocation'),
    ('struct S{int n;S();};S::S(){}int f(){long long raw[4];new(raw)S[2];return 0;}', 'cpp.unsupported.array-new'),
    ('struct S{int n;S();};S::S(){}int f(){long long raw[4];new(raw)S{};return 0;}', 'cpp.unsupported.placement-list-initialization'),
    ('struct S{int n;};int f(){long long raw[4];new(raw)S();return 0;}', 'cpp.unsupported.placement-implicit-construction'),
    ('struct S;int f(){long long raw[4];new(raw)S();return 0;}', 'cpp.incomplete-class'),
    ('class S{S();};S::S(){}int f(){long long raw[4];new(raw)S();return 0;}', 'cpp.member-access'),
    ('struct S{int n;S();};int f(){long long raw[4];new(raw)S();return 0;}', 'cpp.unsupported.placement-constructor-effects'),
    ('int unknown();struct S{int n;S();};S::S():n(unknown()){}'
     'int f(){long long raw[4];new(raw)S();return 0;}', 'cpp.unsupported.placement-constructor-effects'),
    ('struct S{int n;S();};S::S(){}int f(){const long long raw[4]={};new(raw)S();return 0;}', 'cpp.no-viable-overload'),
    ('void* operator new(size_type,int*) noexcept;struct S{int n;S();};S::S(){}'
     'int f(){int raw[4];new(raw)S();return 0;}', 'cpp.unsupported.placement-allocation-overload'),
    ('struct S{int n;S();};S::S(){}struct T{int n;};int f(S*p){p->~T();return 0;}', 'cpp.destructor-type-mismatch'),
    ('struct S{int n;~S();};int f(S*p){p->~S();return 0;}', 'cpp.unsupported.placement-constructor-effects'),
    ('class S{~S();public:int n;};S::~S(){}int f(S*p){p->~S();return 0;}', 'cpp.member-access'),
    ('struct S{int n;};int f(S*p){p->~::S();return 0;}', 'cpp.invalid-destructor-name'),
    ('namespace N{struct S{int n;};}int f(N::S*p){p->~N::S();return 0;}', 'cpp.invalid-destructor-name'),
    ('class S{using Alias=S;public:int n;};int f(S*p){p->~Alias();return 0;}', 'cpp.member-access'),
    ('class S{using Alias=S;public:int n;};using Alias=S;int f(S*p){p->~Alias();return 0;}',
     'cpp.unsupported.destructor-alias-lookup'),
    ('void* operator new(size_type,void*,...)noexcept;struct S{S();};S::S(){}'
     'int f(void*p){new(p)S();return 0;}', 'cpp.ambiguous-overload'),
    ('namespace N{struct S{int n;};using Alias=S;}int f(N::S*p){p->~Alias();return 0;}',
     'cpp.destructor-type-mismatch'),
    ('struct S{int S;};int f(S*p){int S=0;p->~S();return 0;}', 'cpp.unsupported.destructor-hidden-name'),
    ('int f(int*p){p->~int();return 0;}', 'cpp.unsupported.syntax'),
))
def test_unimplemented_or_invalid_placement_paths_fail_before_emission(body, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(DECLARATION + body)
    assert error.value.code == code


@pytest.mark.parametrize('source,code', (
    ('void* operator new(unsigned long,void*);', 'cpp.invalid-placement-function'),
    ('void* operator new(unsigned long,void*)noexcept(false);', 'cpp.invalid-placement-function'),
    ('int operator new(unsigned long,void*)noexcept;', 'cpp.invalid-allocation-function'),
    ('void* operator new(int,void*)noexcept;', 'cpp.invalid-allocation-function'),
    ('extern "C" void* operator new(unsigned long,void*)noexcept;', 'cpp.invalid-allocation-function'),
    ('namespace n{void* operator new(unsigned long,void*)noexcept;}', 'cpp.invalid-allocation-function'),
    ('void* operator new(unsigned long,void*p)noexcept{return p;}', 'cpp.reserved-placement-definition'),
))
def test_allocation_declarations_cannot_acquire_intrinsic_semantics_by_spelling_alone(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


FULL_HEADER_ORIGINAL = '#include <new>\nint main(){return 42;}\n'


def test_full_new_header_keeps_real_unsupported_syntax_visible():
    text = preprocess_cpp(FULL_HEADER_ORIGINAL)
    compact = ''.join(text.split())
    assert 'structnothrow_t{explicitnothrow_t()=default;};' in compact
    assert 'conststd::nothrow_t&' in compact
    assert 'constexprT*launder' in compact
    parser = CppParser()
    with pytest.raises(CppDiagnostic) as error:
        parser.parse(text)
    # The exact defaulted C1 is now owned. The next untouched declaration
    # still requires namespace class-object declaration/lifetime semantics.
    assert error.value.code == 'cpp.unsupported.static-class-lifetime'
    assert error.value.message == 'namespace class objects require lifetime/static initialization semantics'
    position = parser.cursor.position
    assert [token.value for token in parser.cursor.tokens[position - 4:position + 1]] == [
        'extern', 'const', 'nothrow_t', 'nothrow', ';',
    ]


def test_extern_reserved_declaration_keeps_external_cpp_identity():
    parser = CppParser()
    parser.parse('extern void* operator new(unsigned long,void*)noexcept;')
    function = parser.scope.names['operator new'].overloads[0]
    assert function.c_name == '_ZnwmPv'
    assert function.language_linkage == 'C++' and not function.internal_linkage


@pytest.mark.integration
def test_real_new_header_original_is_valid_for_explicit_external_reference(tmp_path):
    binary = compile_reference_with_new((('real-new-header.cpp', FULL_HEADER_ORIGINAL),),
                                        tmp_path / 'reference', observer=False, force_header=False)
    record_execution(binary, Execution(42, b'', b''))


@pytest.mark.integration
def test_preserved_variadic_placement_original_is_ambiguous_in_cpp17(tmp_path):
    # N4659 [over.match.best]/1-2: both fixed arguments have identical
    # conversions; unused ellipsis supplies no better-function tie breaker.
    # https://timsong-cpp.github.io/cppwp/n4659/over.match.best
    source=(DECLARATION+'void* operator new(size_type,void*,...)noexcept;'
            'struct S{S();};S::S(){}int f(void*p){new(p)S();return 0;}')
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code=='cpp.ambiguous-overload'
    path=tmp_path/'original.cpp'
    path.write_text(source)
    compiler=shutil.which(os.environ.get('PCC_CPP_ORACLE','g++'))
    assert compiler
    command=[compiler,'-std=c++17','-pedantic-errors','-fsyntax-only',str(path)]
    result=subprocess.run(command,capture_output=True,timeout=30)
    (tmp_path/'reference.stdout').write_bytes(result.stdout)
    (tmp_path/'reference.stderr').write_bytes(result.stderr)
    (tmp_path/'reference.json').write_text(json.dumps({
        'command':command,'returncode':result.returncode,
        'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'compiler_sha256':hashlib.sha256(Path(compiler).resolve().read_bytes()).hexdigest(),
    },indent=2)+'\n')
    assert result.returncode!=0
    assert b'ambiguous' in result.stderr
