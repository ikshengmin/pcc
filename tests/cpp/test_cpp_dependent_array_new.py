"""Dependent member array-new Patterns, not owned heap execution, UNRUN.

N4659 [expr.new]/1,5-7,18,20-21; [temp.res], [temp.inst], [expr.const].
Unused bodies are typed and retained; native positive observers execute only
unrelated supported members. Every actual new[] use remains fail-before-IR.
Original allocation examples are preserved, never replaced by a fake allocator.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.constants import KNOWN, NOT_CONSTANT
from pcc.frontends.cpp.types import PointerType
from pcc.support.instantiation import InstanceState
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


TARGET = 'x86_64-unknown-linux-gnu'
UPSTREAM_NEW = '            T* newMem = new T[newAllocated];\r\n'
UPSTREAM_COUNT = '            const size_t newAllocated = cap * 2;\r\n'
OBSERVER = ('using size_t=unsigned long;template<class T>struct Buffer{'
            'T*allocate(size_t cap){' + UPSTREAM_COUNT + UPSTREAM_NEW + 'return newMem;}'
            'int get(){return 42;}};')
RETAINED_ORIGINALS = (
    ('retained-local-pointer-allocation-original', 'using size_t=unsigned long;template<class T>struct S{T*f(size_t cap){const size_t newAllocated=cap*2;T*newMem=new T[newAllocated];return newMem;}};'),
    ('retained-destructor-allocation-original', 'template<class T>struct Box{T*p;~Box(){p=new T[2];}};'),
)


def test_exact_new_excerpt_retains_allocated_type_and_local_count_identity():
    header = Path(__file__).resolve().parents[2] / 'projects' / 'tinyxml2-11.0.0' / 'tinyxml2.h'
    lines = header.read_bytes().splitlines(keepends=True)
    assert lines[302] == UPSTREAM_COUNT.encode() and lines[303] == UPSTREAM_NEW.encode()
    parser = CppParser(TARGET)
    tree = parser.parse(OBSERVER)
    owner = parser.scope.names['Buffer'].dtype
    first, second = owner.methods[0].body.children[:2]
    count_symbol = first.value[0]
    allocation = second.children[0]
    assert allocation.node.kind == 'new-array'
    global_only, element = allocation.node.value
    assert not global_only and element is owner.parameters[0]
    assert allocation.dtype == PointerType(element) and not allocation.null_pointer
    count = allocation.node.children[0]
    assert count.node.kind == 'name' and count.node.value is count_symbol
    assert allocation.constant_fact.status == NOT_CONSTANT
    assert not any(isinstance(node, c_ast.FuncDef) for node in tree.ext)
    assert not parser.instance_registry.entries


def test_zero_new_count_is_valid_and_global_lookup_flag_is_retained():
    parser = CppParser(TARGET)
    parser.parse('template<class T>struct S{T*f(){return ::new T[0];}};')
    expression = parser.scope.names['S'].dtype.methods[0].body.children[0].children[0]
    assert expression.node.kind == 'new-array' and expression.node.value[0]
    fact = expression.node.children[0].constant_fact
    assert fact.status == KNOWN and fact.value == 0
    assert expression.constant_fact.status == NOT_CONSTANT and not expression.null_pointer


def test_instantiated_class_bound_keeps_user_conversion_as_an_explicit_gap():
    from pcc.frontends.cpp.allocation_patterns import check_count
    from pcc.frontends.cpp.parser import Expression
    parser = CppParser(TARGET)
    parser.parse('struct Count{};')
    owner = parser.scope.names['Count'].dtype
    # Builtin conversion rank is not evidence about user-defined conversions.
    # This post-substitution type check emits no value or allocation IR.
    count = Expression(c_ast.ID('unlowered_count', owner.coord), owner, True)
    with pytest.raises(CppDiagnostic) as error:
        check_count(parser, count, owner.coord)
    assert error.value.code == 'cpp.unsupported.class-conversions'


@pytest.mark.parametrize('name,source', RETAINED_ORIGINALS, ids=[row[0] for row in RETAINED_ORIGINALS])
def test_retained_unused_allocation_originals_are_typed_without_emitting_bodies(name, source):
    parser = CppParser(TARGET)
    tree = parser.parse(source, name + '.cpp')
    assert not any(isinstance(node, c_ast.FuncDef) for node in tree.ext)
    assert not parser.instance_registry.entries


USED_GAPS = (
    ('runtime-count', 'template<class T>struct S{T*f(int n){return new T[n];}};int run(){S<int>s;s.f(2);return 0;}', 'cpp.unsupported.allocation'),
    ('zero-count-still-needs-allocation', 'template<class T>struct S{T*f(){return new T[0];}};int run(){S<int>s;s.f();return 0;}', 'cpp.unsupported.allocation'),
    ('pointer-element-allocation', 'template<class T>struct S{T**f(int n){return new T*[n];}};int run(){S<int>s;s.f(2);return 0;}', 'cpp.unsupported.allocation'),
    ('class-element-lifetime', 'struct E{E();~E();};template<class T>struct S{T*f(){return new T[2];}};int run(){S<E>s;s.f();return 0;}', 'cpp.unsupported.class-array-lifetime'),
    ('destructor-allocation-use', 'template<class T>struct Box{T*p;~Box(){p=new T[2];}};int run(){Box<int>b{nullptr};return 0;}', 'cpp.unsupported.allocation'),
)
INVALID = (
    ('negative-known-count', 'template<class T>struct S{T*f(){return new T[-1];}};int run(){S<int>s;s.f();return 0;}', 'cpp.invalid-new-array-bound'),
    ('negative-substituted-count', 'template<int N>struct S{int*f(){return new int[N];}};int run(){S<-1>s;s.f();return 0;}', 'cpp.invalid-new-array-bound'),
    ('void-element', 'template<class T>struct S{T*f(){return new T[2];}};int run(){S<void>s;s.f();return 0;}', 'cpp.invalid-new-type'),
    ('reference-element', 'using R=int&;template<class T>struct S{void f(){new T[2];}};void run(S<R>*p){p->f();}', 'cpp.invalid-new-type'),
    ('incomplete-class-element', 'struct E;template<class T>struct S{T*f(){return new T[2];}};void run(S<E>*p){p->f();}', 'cpp.incomplete-class'),
    ('const-scalar-default-init', 'template<class T>struct S{T*f(){return new T[2];}};int run(){S<const int>s;s.f();return 0;}', 'cpp.const-initializer'),
    ('pointer-count', 'template<class T>struct S{T*f(int*n){return new T[n];}};int run(){S<int>s;s.f(nullptr);return 0;}', 'cpp.invalid-new-array-bound'),
)
SYNTAX_GAPS = (
    ('initialized-array-new', 'template<class T>struct S{T*f(int n){return new T[n]();}};', 'cpp.unsupported.array-new'),
    ('multidimensional-array-new', 'template<class T>struct S{void f(int n){new T[n][2];}};', 'cpp.unsupported.array-new'),
    ('unevaluated-array-new', 'template<class T>struct S{int f(){return sizeof(new T[2]);}};', 'cpp.unsupported.array-new'),
    ('scalar-dependent-new', 'template<class T>struct S{T*f(){return new T;}};', 'cpp.unsupported.allocation-templates'),
    ('function-template-new', 'template<class T>int f(){new T[2];return 0;}', 'cpp.unsupported.allocation-templates'),
    ('nondependent-template-new', 'template<class T>struct S{int*f(int n){return new int[n];}};', 'cpp.unsupported.allocation-templates'),
)


@pytest.mark.parametrize('name,source,code', USED_GAPS + INVALID + SYNTAX_GAPS,
                         ids=[row[0] for row in USED_GAPS + INVALID + SYNTAX_GAPS])
def test_array_new_actual_uses_and_remaining_boundaries_fail_before_ir(name, source, code, monkeypatch, owned_only):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
    def forbidden(*args, **kwargs):
        pytest.fail('unowned array-new reached IR generation')
    monkeypatch.setattr(CCodeGenerator, 'generate_code', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + '.cpp', source),))
    assert error.value.code == code


def test_used_array_new_failure_is_sticky_in_the_shared_instance_registry():
    parser = CppParser(TARGET)
    with pytest.raises(CppDiagnostic) as first:
        parser.parse(USED_GAPS[0][1])
    assert first.value.code == 'cpp.unsupported.allocation'
    failed = [entry for entry in parser.instance_registry.entries.values() if entry.state is InstanceState.FAILED]
    assert len(failed) == 1 and failed[0].failure is first.value
    with pytest.raises(CppDiagnostic) as repeated:
        parser.instance_registry.get(failed[0].key)
    assert repeated.value is first.value


CASES = (
    ('exact-new-and-count-excerpts-unused', OBSERVER + 'int cpp_test_entry(){Buffer<int>b;return b.get();}'),
    ('zero-array-new-body-unused', 'template<class T>struct S{T*f(){return new T[0];}int get(){return 42;}};int cpp_test_entry(){S<int>s;return s.get();}'),
    ('pointer-array-new-body-unused', 'template<class T>struct S{T**f(int n){return new T*[n];}int get(){return 42;}};int cpp_test_entry(){S<int>s;return s.get();}'),
    ('negative-nttp-body-remains-lazy', 'template<int N>struct S{int*f(){return new int[N];}int get(){return 42;}};int cpp_test_entry(){S<-1>s;return s.get();}'),
    ('unbound-count-function-body-unused', 'int count();template<class T>struct S{T*f(){return new T[count()];}int get(){return 42;}};int cpp_test_entry(){S<int>s;return s.get();}'),
)
PROGRAMS = (tuple((name, ((name + '.cpp', source),)) for name, source in CASES) +
            tuple((name, (('original.cpp', source), ('entry.cpp', 'int cpp_test_entry(){return 42;}')))
                  for name, source in RETAINED_ORIGINALS))


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_unused_array_new_originals_have_stable_owned_artifacts(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)
