"""Allocating new/delete over the global replaceable allocation functions.

N4659 [expr.new], [expr.delete]/10, [basic.stc.dynamic]/2, [replacement.functions].
https://timsong-cpp.github.io/cppwp/n4659/expr.new
https://timsong-cpp.github.io/cppwp/n4659/expr.delete
https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling-operator
`new T` calls operator new(sizeof(T)) then initializes; `new E[n]` (trivially
destructible E, no cookie) passes n*sizeof(E), or SIZE_MAX when that
overflows or n is negative; `delete p` destroys a non-null object and calls
the sized operator delete; `delete[] p` calls the unsized operator delete[].
These programs replace the usual functions (valid C++), so the freestanding
host lane needs no runtime. Each replacement pair counts sized and unsized
forms equally, so a reference without sized deallocation agrees.
Original tests modeled on TinyXML2 11.0.0 MemPoolT (projects/tinyxml2-11.0.0,
Zlib); no external compiler implementation is copied.
"""
import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

ALLOCATOR = (
    'typedef unsigned long size_t;'
    'static unsigned char*arena;static size_t used;static int frees;'
    'void*take(size_t n){void*p=arena+used;used+=(n+15)&~(size_t)15;return p;}'
    'void*operator new(size_t n){return take(n);}'
    'void*operator new[](size_t n){return take(n);}'
    'void operator delete(void*p){frees=frees+1;}'
    'void operator delete(void*p,size_t n){frees=frees+1;}'
    'void operator delete[](void*p){frees=frees+10;}'
    'void operator delete[](void*p,size_t n){frees=frees+10;}'
)

PROGRAMS = (
    ('scalars-classes-and-arrays',
     ALLOCATOR +
     'struct Counter{int*log;Counter(int*l):log(l){*log=*log*10+1;}~Counter(){*log=*log*10+2;}};'
     'struct Plain{int a;long b;};'
     'int cpp_test_entry(){double storage[128];arena=reinterpret_cast<unsigned char*>(storage);'
     'int log=0;Counter*c=new Counter(&log);delete c;'
     'int*x=new int(20);int*z=new int();Plain*p=new Plain();'
     'int value=*x+*z+(int)p->b;delete x;delete z;delete p;'
     'int n=3;long*arr=new long[n];arr[0]=1;arr[2]=1;value=value+(int)(arr[0]+arr[2]);delete[] arr;'
     'Plain*none=nullptr;delete none;'
     'return value+log+frees-6;}'),
    ('member-class-blocks-of-a-class-template',
     ALLOCATOR +
     'template<size_t S>struct Pool{'
     'union Item{Item*next;char data[S];};struct Block{Item items[4];};'
     'Block*current;Pool():current(nullptr){}~Pool(){delete current;}'
     'Item*Grow(){Block*block=new Block;block->items[0].next=nullptr;current=block;return &block->items[0];}};'
     'int cpp_test_entry(){double storage[128];arena=reinterpret_cast<unsigned char*>(storage);'
     '{Pool<24> pool;pool.Grow();}return frees*21+(int)(used/96)*21;}'),
)


@pytest.mark.parametrize('name,source', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_heap_allocation_programs_compile_to_stable_artifacts(owned_only, name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


def _function(text, symbol):
    body = text[text.index('@' + symbol + '('):]
    return body[:body.index('\n}')]


def test_new_and_delete_call_the_selected_usual_functions(owned_only):
    text = '\n'.join(compile_host_cpp((('heap.cpp', PROGRAMS[0][1]),)).ir)
    entry = _function(text, '_Z14cpp_test_entryv')
    assert '@_Znwm(i64 4)' in entry and '@_Znwm(i64 16)' in entry
    assert '@_ZN7CounterC1EPi' in entry and '@_Znam(' in entry
    # [expr.delete]/10: sized operator delete for complete objects, after D1.
    assert entry.index('@_ZN7CounterD1Ev') < entry.index('@_ZdlPvm')
    assert '@_ZdaPv(' in entry and '@_ZdaPvm(' not in entry


# Former boundary rows that now allocate, moved verbatim from
# test_cpp_placement_new, test_cpp_pointers, test_cpp_template_subscript_results
# and test_cpp_template_destructors (DynArray scaffold).
def _moved_originals():
    from tests.cpp.test_cpp_template_destructors import DYNARRAY_SCAFFOLD
    return (
        ('placement-file-allocating-new', 'struct S{int n;S();};S::S(){}int f(){S*p=new S;return 0;}', '_Znwm'),
        ('pointers-file-new-int', 'int f(){int *p=new int;return 0;}', '_Znwm'),
        ('subscript-results-allocation-use', 'template<class T>struct S{T*p;T&get(){delete[]p;return*p;}};'
         'int f(S<int>*p){return p->get();}', '_ZdaPv'),
        ('used-dependent-delete', DYNARRAY_SCAFFOLD + 'int f(){DynArray<int>b{nullptr,nullptr};return 0;}', None),
    )


def test_moved_allocation_originals_reach_the_allocation_functions():
    for name, source, symbol in _moved_originals():
        tree = CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source, name + '.cpp')
        pending, names = [tree], set()
        while pending:
            node = pending.pop()
            if isinstance(node, c_ast.FuncCall) and isinstance(node.name, c_ast.ID):
                names.add(node.name.name)
            pending.extend(child for _name, child in node.children())
        assert symbol is None and names & {'_ZdlPvm', '_ZdaPv'} or symbol in names, name


@pytest.mark.parametrize('source,code', (
    ('struct B{B(){}virtual int g(){return 0;}};void f(B*p){delete p;}', 'cpp.unsupported.allocation'),
    ('struct S{static void*operator new(unsigned long);};void f(){new S;}', 'cpp.unsupported.class-allocation-functions'),
    ('void f(){new int{1};}', 'cpp.unsupported.allocation'),
    ('void f(int n){new int[n]();}', 'cpp.unsupported.allocation'),
    ('struct A{virtual void g()=0;};void f(){new A;}', 'cpp.abstract-class'),
    ('void f(){new void;}', 'cpp.invalid-new-type'),
    ('struct S{S();~S();};void f(int n){new S[n];}', 'cpp.unsupported.class-array-lifetime'),
    ('void f(int n){new int[n][4];}', 'cpp.unsupported.array-new'),
))
def test_unmodeled_or_invalid_allocation_is_explicit(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)
    assert error.value.code == code


def test_cpp_executables_link_weak_default_allocation_functions():
    # [replacement.functions]: the defaults are weak so a program's own
    # replacement wins. Executing them needs the owned runtime (unrun here).
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    evaluator = CEvaluator(target_triple='x86_64-unknown-linux-gnu')
    assert evaluator._cxx_allocation_support_units([('main.c', '', None, [])]) == []
    unit, = evaluator._cxx_allocation_support_units([('main.cpp', '', None, [])])
    defines = [line for line in unit[1].splitlines() if line.startswith('define')]
    assert len(defines) == 6 and all(line.startswith('define weak ') for line in defines)
    assert {name for kind, name, _ in unit[3]} == {'_Znwm', '_Znam', '_ZdlPv', '_ZdlPvm', '_ZdaPv', '_ZdaPvm'}
    assert all(kind == 'weak function' for kind, _name, _ in unit[3])
    calls = unit[1][unit[1].index('@_Znwm('):]
    assert '@malloc(' in calls and '@abort(' in calls
