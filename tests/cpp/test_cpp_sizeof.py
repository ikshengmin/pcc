"""Bounded C++17 sizeof, with target facts and genuinely unevaluated operands.

N4659 [expr.sizeof]/1-4,6, [expr]/8, [expr.prim.id]/2.3,
[basic.fundamental]/10, [dcl.array]/1, [expr.unary.op]/3-4,
[class.mfct.non-static]/3, [class.this]/1:
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
No class layout duplication, template sizeof, volatile or packs.
Long double storage queries do NOT qualify long double arithmetic/call ABI.
TinyXML2's original sizeof(char)/sizeof(error) uses motivate this slice;
sizeof(T), sizeof(*item), cstdio and the whole project remain separate gaps.
"""
import json
import os
import re
import shutil
import struct
import subprocess
import traceback

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.constants import KNOWN
from tests.cpp.owned_execution import Execution, assert_exact, compile_host_cpp
from tests.cpp.sizeof_execution import (
    capture_row_execution, compile_public_observed, execute_public_row_pair,
    execute_row_pair,
)


CASES = (
    ('fundamental-type', 'int cpp_test_entry(){return int(sizeof(int))+38;}'),
    ('character-types', 'int cpp_test_entry(){return int(sizeof(char)+sizeof(signed char)+sizeof(unsigned char))+39;}'),
    ('array-expression-no-decay', 'int cpp_test_entry(){int a[3];return int(sizeof(a))+30;}'),
    ('array-type-id', 'int cpp_test_entry(){return int(sizeof(int[2][3]))+18;}'),
    ('cv-row-alias', 'typedef const int Row[3];int cpp_test_entry(){Row a[2]={};return int(sizeof(a))+18;}'),
    ('pointer-to-row-type-id', 'int cpp_test_entry(){return int(sizeof(int(*)[3]))+34;}'),
    ('opaque-pointee-pointer', 'struct S;int cpp_test_entry(){return int(sizeof(S*))+34;}'),
    ('void-pointer', 'int cpp_test_entry(){return int(sizeof(void*))+34;}'),
    ('nullptr-size', 'int cpp_test_entry(){return int(sizeof(nullptr))+34;}'),
    ('enum-no-integral-promotion', 'enum E:unsigned char{A=1};int cpp_test_entry(){return int(sizeof(A))+41;}'),
    ('scoped-enum', 'enum class E:unsigned short{A=1};int cpp_test_entry(){return int(sizeof(E::A))+40;}'),
    ('fixed-opaque-enum-complete', 'enum E:unsigned short;int cpp_test_entry(){return int(sizeof(E))+40;}'),
    ('bool-expression', 'int cpp_test_entry(){return int(sizeof(1<2))+41;}'),
    ('adjusted-array-parameter', 'int size(int a[3]){return int(sizeof(a));}int cpp_test_entry(){int a[3];return size(a)+34;}'),
    ('member-name-arithmetic', 'struct S{int x;};int cpp_test_entry(){return int(sizeof(S::x+1))+38;}'),
    ('nested-query', 'int cpp_test_entry(){return int(sizeof(sizeof(int)))+34;}'),
    ('prefix-not-evaluated', 'int cpp_test_entry(){int x=38;int n=int(sizeof(++x));return n+x;}'),
    ('postfix-not-evaluated', 'int cpp_test_entry(){int x=38;int n=int(sizeof(x++));return n+x;}'),
    ('assignment-not-evaluated', 'int cpp_test_entry(){int x=38;int n=int(sizeof(x=1));return n+x;}'),
    ('undefined-call-not-odr-used', 'int unavailable();int cpp_test_entry(){return int(sizeof(unavailable()))+38;}'),
    ('undefined-call-argument-not-evaluated', 'int unavailable(int);int cpp_test_entry(){int x=38;return int(sizeof(unavailable(++x)))+x;}'),
    ('null-pointer-not-dereferenced', 'int cpp_test_entry(){int*p=nullptr;return int(sizeof(*p))+38;}'),
    ('array-subscript-not-evaluated', 'int cpp_test_entry(){int a[1];int x=38;return int(sizeof(a[x++]))+x;}'),
    ('nonconstant-operand-still-typechecked', 'int cpp_test_entry(){return int(sizeof(1/0))+38;}'),
    ('functional-conversion-parentheses', 'int cpp_test_entry(){return int(sizeof(int((7))))+38;}'),
    ('ordinary-lookup-value-hides-alias', 'using T=long long;int cpp_test_entry(){int T=0;return int(sizeof(T))+38;}'),
    ('size-used-as-array-bound', 'int cpp_test_entry(){int a[sizeof(int)]={};return int(sizeof(a))+26;}'),
    ('size-used-as-enumerator', 'enum E{N=sizeof(int[3])};int cpp_test_entry(){return int(N)+30;}'),
    ('target-size-type-overload', 'int pick(unsigned long){return 42;}int pick(unsigned long long){return 1;}int cpp_test_entry(){return pick(sizeof(int));}'),
    ('nonstatic-member-name-no-object', 'struct S{int x;};int cpp_test_entry(){return int(sizeof(S::x))+38;}'),
    ('parenthesized-array-type-id', 'int cpp_test_entry(){return int(sizeof(int([3])))+30;}'),
    ('mutable-member-unevaluated-no-update', 'struct S{mutable int x;int f()const;};int S::f()const{return int(sizeof(x=1)+sizeof(++x))+x;}int cpp_test_entry(){const S s{34};const S*p=&s;return p->f();}'),
    ('const-member-address-keeps-cv', 'int pick(int*);long long pick(const int*);struct S{int x;int f()const;};int S::f()const{return int(sizeof(pick(&x))+sizeof(pick(&(S::x))))+26;}int cpp_test_entry(){const S s{0};const S*p=&s;return p->f();}'),
    ('member-call-not-odr-used', 'struct S{int f()const;};int cpp_test_entry(){const S*p=nullptr;return int(sizeof(p->f()))+38;}'),
    ('template-call-body-not-instantiated', 'template<class T>T invalid_when_instantiated(T x){return x%2;}int cpp_test_entry(){return int(sizeof(invalid_when_instantiated(1.0)))+34;}'),
    ('long-double-storage-only', 'int cpp_test_entry(){return int(sizeof(long double))+26;}'),
    ('tinyxml-size-of-error-expression', 'enum Error{OK=0};int cpp_test_entry(){Error error=OK;if(sizeof(error)>sizeof(int))return 1;return int(sizeof(char))+41;}'),
)

INVALID = (
    # These originals are ill-formed; PCC stops at the earlier explicit
    # pointer-to-member boundary rather than erasing it to an ordinary T*.
    ('struct S{int x;};int f(){return sizeof(&S::x + 1);}', 'cpp.unsupported.member-pointers'),
    ('struct S{int x;};int f(){return sizeof(*&S::x);}', 'cpp.unsupported.member-pointers'),
    ('struct S{int x;int f()const;};int S::f()const{return sizeof(x=1);}', 'cpp.const-assignment'),
    ('struct S{int x;int f()const;};int S::f()const{return sizeof(++x);}', 'cpp.const-assignment'),
    ('struct S{int x;int f()const;};int S::f()const{return sizeof(S::x=1);}', 'cpp.const-assignment'),
    ('int f(){return sizeof(void);}', 'cpp.invalid-sizeof'),
    ('void g();int f(){return sizeof(g());}', 'cpp.invalid-sizeof'),
    ('int g();int f(){return sizeof(g);}', 'cpp.invalid-sizeof'),
    ('int f(){return sizeof(int());}', 'cpp.invalid-sizeof'),
    ('int f(){return sizeof(int(int));}', 'cpp.invalid-sizeof'),
    ('struct S;int f(){return sizeof(S);}', 'cpp.incomplete-class'),
    ('struct S;int f(S*p){return sizeof(*p);}', 'cpp.incomplete-class'),
    ('typedef int A[];int f(){return sizeof(A);}', 'cpp.invalid-sizeof'),
    ('int f(){return sizeof(int[]);}', 'cpp.invalid-sizeof'),
    ('int f(){return sizeof(int[0]);}', 'cpp.invalid-array-bound'),
    ('int f(int n){return sizeof(int[n]);}', 'cpp.nonconstant-array-bound'),
    ('int f(){return sizeof(int[4611686018427387904ULL]);}', 'cpp.sizeof-overflow'),
    ('int f(){return sizeof(missing);}', 'cpp.undeclared'),
    ('int f(){const int x=0;return sizeof(x=1);}', 'cpp.const-assignment'),
    ('int f(){return sizeof(1.0%2);}', 'cpp.invalid-operands'),
    ('int f(){void*p=nullptr;return sizeof(*p);}', 'cpp.invalid-dereference'),
    ('class S{int x;};int f(){return sizeof(S::x);}', 'cpp.member-access'),
    ('class S{int g();};int f(S*p){return sizeof(p->g());}', 'cpp.member-access'),
)

DEFERRED = (
    ('struct S{int x;};int f(){return sizeof(&S::x);}', 'cpp.unsupported.member-pointers'),
    ('int f(){return sizeof(int(*)());}', 'cpp.unsupported.function-pointers'),
    ('struct S{};int f(){return sizeof(S[2]);}', 'cpp.unsupported.class-array-lifetime'),
    ('int f(){return sizeof(volatile int);}', 'cpp.unsupported.volatile'),
    ('int f(){return sizeof...(T);}', 'cpp.unsupported.sizeof-packs'),
)


@pytest.mark.parametrize('name,source', CASES, ids=[row[0] for row in CASES])
def test_sizeof_owned_object_is_stable(name, source, monkeypatch):
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned sizeof compilation invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        first = compile_host_cpp(((name + '.cpp', source),))
        second = compile_host_cpp(((name + '.cpp', source),))
    assert first == second


@pytest.mark.parametrize('source,code', INVALID + DEFERRED)
def test_sizeof_invalid_and_explicit_gaps_before_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid/deferred sizeof reached emission')
    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('sizeof-negative.cpp', source),))
    assert error.value.code == code


class RecordingParser(CppParser):
    def _sizeof_expression(self, token):
        result = super()._sizeof_expression(token)
        self.queries.append(result)
        return result


TARGETS = (
    ('x86_64-unknown-linux-gnu', 'unsigned long', 8, 16),
    ('x86_64-pc-windows-msvc', 'unsigned long long', 4, 8),
    ('x86_64-pc-windows-gnu', 'unsigned long long', 4, 16),
    ('aarch64-unknown-linux-gnu', 'unsigned long', 8, 16),
    ('aarch64-apple-darwin', 'unsigned long', 8, 8),
)


@pytest.mark.parametrize('triple,size_type,long_size,long_double_size', TARGETS)
def test_sizeof_target_facts_and_expression_type(triple, size_type, long_size, long_double_size):
    parser = RecordingParser(triple)
    parser.queries = []
    source = ('int f(){sizeof(bool);sizeof(char);sizeof(short);sizeof(int);'
              'sizeof(long);sizeof(long long);sizeof(float);sizeof(double);'
              'sizeof(long double);sizeof(int*);sizeof(int[2][3]);sizeof(nullptr);return 0;}')
    parser.parse(source)
    expected = (1, 1, 2, 4, long_size, 8, 4, 8, long_double_size, 8, 24, 8)
    assert len(parser.queries) == len(expected)
    for expression, value in zip(parser.queries, expected):
        assert expression.dtype == size_type
        assert expression.constant_fact.status == KNOWN
        assert expression.constant_fact.value == value
        assert not expression.lvalue and not expression.null_pointer


def test_sizeof_preserves_array_type_and_drops_unevaluated_nodes():
    from pcc.frontends.c.ast import c_ast
    source = 'int missing(int);int f(){int a[3];int x=0;return int(sizeof(a)+sizeof(missing(++x))+sizeof(a[x++]));}'
    parser = CppParser('x86_64-unknown-linux-gnu')
    tree = parser.parse(source)
    class Observe(c_ast.NodeVisitor):
        def __init__(self):
            self.calls = []
            self.increments = []
        def visit_FuncCall(self, node):
            self.calls.append(node)
        def visit_UnaryOp(self, node):
            if node.op in ('++', '--', 'p++', 'p--'):
                self.increments.append(node)
            self.generic_visit(node)
    observe = Observe()
    observe.visit(tree)
    assert not observe.calls and not observe.increments
    assert parser.unevaluated_depth == 0


def test_unevaluated_template_call_does_not_register_a_body():
    parser = CppParser('x86_64-unknown-linux-gnu')
    parser.parse(dict(CASES)['template-call-body-not-instantiated'])
    assert len(parser.instance_registry.entries) == 0


def test_sizeof_failure_restores_unevaluated_context():
    parser = CppParser('x86_64-unknown-linux-gnu')
    with pytest.raises(CppDiagnostic):
        parser.parse('int f(){return sizeof(missing);}')
    assert parser.unevaluated_depth == 0
    parser.parse('int f(){return 0;}')
    assert parser.unevaluated_depth == 0


PUBLIC_SOURCE = 'int absent(int);int main(){int a[2][3];int n=0;if(sizeof(a)!=24||sizeof(absent(++n))!=4||n!=0)return 1;if((sizeof(int)<<40)!=4398046511104ULL)return 2;return int(sizeof(a[0]))+30;}'


@pytest.mark.parametrize('opt', [0, 2])
def test_sizeof_public_pipeline_is_typed_and_unevaluated(opt, tmp_path, monkeypatch):
    records, observed = compile_public_observed(PUBLIC_SOURCE, opt, monkeypatch)
    (tmp_path / 'public.cpp').write_text(PUBLIC_SOURCE)
    (tmp_path / 'public.ll').write_text(records[0][1])
    (tmp_path / 'pipeline-observation.json').write_text(json.dumps(observed, indent=2))
    assert not re.search(r'call[^\n]*@_Z6absenti\(', records[0][1])
    assert len(observed['pass_report']) == 1
    assert observed['pass_report'][0]['opt_level'] == opt
    assert all(row['result'] != 'exception' for row in observed['ssa_attempts'])
    # Observations report the actual route; no promise that an SSA build
    # survives existing canonicalization, and no pass is disabled for this test.


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES, ids=[row[0] for row in CASES])
def test_sizeof_same_original_native_and_reference(name, source, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, ((name + '.cpp', source),))


@pytest.mark.integration
@pytest.mark.parametrize('source,code', INVALID)
def test_sizeof_invalid_original_reference_rejection(source, code, tmp_path):
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference is required'
    original = tmp_path / 'invalid.cpp'
    original.write_text(source)
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(original)],
                            capture_output=True, timeout=20)
    (tmp_path / 'reference.stdout.bin').write_bytes(result.stdout)
    (tmp_path / 'reference.stderr.bin').write_bytes(result.stderr)
    (tmp_path / 'reference.json').write_text(json.dumps({'returncode': result.returncode}))
    assert result.returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('opt', [0, 2])
def test_sizeof_public_native_same_original_reference(opt, tmp_path, monkeypatch):
    execute_public_row_pair(PUBLIC_SOURCE, opt, tmp_path, monkeypatch)


FULL_WIDTH = (
    ('over-32bit-object-size', 'extern "C" unsigned long long sizeof_value(){return sizeof(unsigned char[4294967297ULL]);}', 4294967297),
    ('size-type-shift-width', 'extern "C" unsigned long long sizeof_value(){return sizeof(int)<<40;}', 4398046511104),
)
# The assembly is an observer only. It calls the original function and exposes
# all eight returned bytes, not a four-byte sentinel or only the exit status.
FULL_WIDTH_STARTUP = '.intel_syntax noprefix\n.text\n.globl _start\n_start:\n sub rsp,16\n call sizeof_value\n mov qword ptr [rsp],rax\n mov rax,1\n mov rdi,1\n mov rsi,rsp\n mov rdx,8\n syscall\n mov rax,60\n xor rdi,rdi\n syscall\n'


@pytest.mark.integration
@pytest.mark.parametrize('opt', [0, 2])
@pytest.mark.parametrize('name,source,value', FULL_WIDTH, ids=[row[0] for row in FULL_WIDTH])
def test_sizeof_public_full_width_raw_observer(name, source, value, opt, tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    original = tmp_path / 'original.cpp'
    original.write_text(source)
    observer = tmp_path / 'observer.s'
    observer.write_text(FULL_WIDTH_STARTUP)
    owned_error = None
    actual = None
    first = second = None
    try:
        with monkeypatch.context() as guard:
            def forbidden(*args, **kwargs):
                raise AssertionError('owned sizeof compilation invoked an external process')
            guard.setattr(subprocess, 'Popen', forbidden)
            records, observed = compile_public_observed(source, opt, monkeypatch)
            (tmp_path / 'owned.ll').write_text(records[0][1])
            (tmp_path / 'pipeline-observation.json').write_text(json.dumps(observed, indent=2))
            first = emit_owned_object(records[0][1], 'x86_64-unknown-linux-gnu')
            second = emit_owned_object(compile_public_observed(source, opt, monkeypatch)[0][0][1],
                                       'x86_64-unknown-linux-gnu')
            (tmp_path / 'owned.o').write_bytes(first)
            executable = link_static_executable([assemble_file(FULL_WIDTH_STARTUP), parse_relocatable(first)], entry='_start')
        binary = tmp_path / 'owned'
        binary.write_bytes(executable)
        binary.chmod(0o700)
        actual = capture_row_execution(binary)
    except Exception as error:
        owned_error = error
        (tmp_path / 'owned-error.txt').write_text(traceback.format_exc())
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference is required'
    oracle = tmp_path / 'reference'
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-O' + str(opt),
                             '-nostdlib', '-no-pie', str(original), str(observer), '-o', str(oracle)],
                            capture_output=True, timeout=20)
    (tmp_path / 'reference-build.stdout.bin').write_bytes(result.stdout)
    (tmp_path / 'reference-build.stderr.bin').write_bytes(result.stderr)
    (tmp_path / 'reference-build.json').write_text(json.dumps({'returncode': result.returncode}))
    assert result.returncode == 0, result.stderr
    reference = capture_row_execution(oracle)
    if owned_error is not None:
        raise owned_error
    assert first == second
    expected = Execution(0, struct.pack('<Q', value), b'')
    assert_exact(actual, expected)
    assert_exact(reference, expected)


@pytest.mark.integration
@pytest.mark.parametrize('source', [
    'struct S{int x;};int f(){return sizeof(&S::x);}',
    'struct S{int a[3];};int cpp_test_entry(){return int(sizeof(S::a))+30;}',
])
def test_sizeof_valid_member_gaps_original_reference_only(source, tmp_path):
    # Member pointers remain a gap. The exact original array-member program
    # also has new owned-native/reference obligations in the scalar-array corpus.
    # This observer remains reference acceptance only, not a PCC runtime record.
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference is required'
    original = tmp_path / 'member-pointer.cpp'
    original.write_text(source)
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(original)],
                            capture_output=True, timeout=20)
    (tmp_path / 'reference.stdout.bin').write_bytes(result.stdout)
    (tmp_path / 'reference.stderr.bin').write_bytes(result.stderr)
    (tmp_path / 'reference.json').write_text(json.dumps({'returncode': result.returncode}))
    assert result.returncode == 0, result.stderr
