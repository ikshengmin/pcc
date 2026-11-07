"""Unqualified dependent free calls in class-template members.

N4659 [temp.dep]/1, [temp.dep.candidate], [basic.lookup.argdep]. The call keeps
its definition-point ordinary candidates; argument-dependent lookup from the
instantiated argument types adds associated-namespace functions, including
functions declared after the template. Qualified calls and nondependent calls
keep exactly their recorded group. Dependent explicit template arguments in
class-template members and ADL-only dependent calls remain explicit gaps.
"""

from pathlib import Path

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.parser import Expression
from pcc.frontends.cpp.templates import Pattern
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


TARGET = 'x86_64-unknown-linux-gnu'
# TinyXML2 DynArray<T, INITIAL_SIZE>::EnsureCapacity, the first unchanged
# project input that needed this rule (tinyxml2.h line 306).
UPSTREAM_CALL = (b'            memcpy( newMem, _mem, sizeof(T) * _size );'
                 b'\t// warning: not using constructors, only works for PODs\r\n')
ASSOCIATED = 'namespace N{struct S{int n;};int pick(S*s){return s->n;}}'
BOX = 'template<class T>struct Box{T*p;int get(){return pick(p);}};'
ENTRY_S = 'int cpp_test_entry(){N::S s{42};Box<N::S> b;b.p=&s;return b.get();}'
CASES = (
    ('definition-point-candidate', 'int pick(int*p){return *p;}' + BOX
     + 'int cpp_test_entry(){int v=42;Box<int> b;b.p=&v;return b.get();}'),
    ('instantiation-adl-candidate', ASSOCIATED + 'int pick(int*p){return *p;}' + BOX + ENTRY_S),
    ('associated-declaration-after-template', 'int pick(int*p){return *p;}' + BOX + ASSOCIATED + ENTRY_S),
    # A later global overload is neither a definition-point candidate nor
    # found by ADL for int*: the const overload stays selected.
    ('later-ordinary-overload-not-visible', 'int pick(const int*p){return *p;}' + BOX
     + 'int pick(int*p){return 1;}int cpp_test_entry(){int v=42;Box<int> b;b.p=&v;return b.get();}'),
    # T itself names a class pointer: ADL adds N::choose, an exact match
    # that beats the definition-point void* candidate.
    ('pointer-type-argument-prefers-associated-function',
     'int choose(void*p){return 1;}namespace N{struct Tag{};int choose(Tag*p){return 42;}}'
     'template<class T>struct Box{int f(T p){return choose(p);}};'
     'int cpp_test_entry(){N::Tag t;Box<N::Tag*> b;return b.f(&t);}'),
    # The DynArray shape: T* arguments convert to void* only once T is known.
    ('dependent-pointer-to-void-arguments',
     'void copy(void*d,const void*s,unsigned long n){char*a=static_cast<char*>(d);'
     'const char*b=static_cast<const char*>(s);for(unsigned long i=0;i<n;++i)a[i]=b[i];}'
     'template<class T>struct Pair{T a;T b;void back(){copy(&b,&a,sizeof(T));}};'
     'int cpp_test_entry(){Pair<int> p;p.a=42;p.b=0;p.back();return p.b;}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES)
INVALID = (
    ('ambiguous-after-instantiation-adl',
     'namespace N{struct S{int n;};int pick(S*s){return 1;}}int pick(N::S*s){return 2;}' + BOX + ENTRY_S,
     'cpp.ambiguous-overload'),
    # A global class associates the global namespace, whose only pick is not viable.
    ('no-viable-after-instantiation-adl',
     'int pick(int*p){return *p;}' + BOX
     + 'struct S{int n;};int cpp_test_entry(){S s{42};Box<S> b;b.p=&s;return b.get();}',
     'cpp.no-viable-overload'),
)
DEFERRED = (
    ('dependent-explicit-template-argument',
     'template<class U>int f(U u){return 42;}template<class T>struct B{int g(T t){return f<T>(t);}};'
     'int cpp_test_entry(){B<int> b;return b.g(1);}',
     'cpp.unsupported.dependent-lookup'),
    ('adl-only-dependent-call', ASSOCIATED + BOX + ENTRY_S, 'cpp.unsupported.dependent-lookup'),
)


def _calls(node):
    node = node.node if isinstance(node, Expression) else node
    if isinstance(node, Pattern):
        if node.kind == 'call':
            yield node
        for child in node.children:
            if child is not None:
                yield from _calls(child)


def test_tinyxml_dynarray_call_is_unchanged_source():
    root = Path(__file__).resolve().parents[2]
    lines = (root / 'projects/tinyxml2-11.0.0/tinyxml2.h').read_bytes().splitlines(keepends=True)
    assert lines[305] == UPSTREAM_CALL


def test_formerly_rejected_definition_is_accepted_unchanged():
    # Retained byte-for-byte from the earlier class-template boundary list.
    CppParser().parse('int choose(void*);namespace N{struct Tag{};int choose(Tag*);}'
                      'template<class T>struct Box{int f(T p){return choose(p);}};')


def test_only_unqualified_dependent_calls_repeat_lookup_at_instantiation():
    parser = CppParser(TARGET)
    parser.parse('int pick(int*p){return *p;}int answer(){return 42;}'
                 'template<class T>struct Box{T*p;int get(){return pick(p);}'
                 'int qualified(){return ::pick(p);}int fixed(){return answer();}};',
                 'dependent-call-patterns.cpp')
    owner = parser.scope.names['Box'].dtype
    recorded = {record.symbol.name: [(call.value.name, call.value.instantiation_adl)
                                     for call in _calls(record.body)]
                for record in owner.methods}
    assert recorded == {'get': [('pick', True)], 'qualified': [('pick', False)],
                        'fixed': [('answer', False)]}


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_dependent_call_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED,
                         ids=[row[0] for row in INVALID + DEFERRED])
def test_dependent_call_boundaries_precede_ir(owned_only, name, source, code, monkeypatch):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid or deferred dependent call reached IR generation')
    monkeypatch.setattr(CCodeGenerator, 'generate_code', forbidden)
    with pytest.raises(CppDiagnostic) as caught:
        compile_host_cpp(((name + '.cpp', source),))
    assert caught.value.code == code
