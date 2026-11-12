"""Dependent template-id members and current-instantiation names in class templates.

N4659 [temp.dep.type]/1,9, [temp.dep.expr]/5, [class.qual], [except.spec]/8,
[except.terminate].
https://timsong-cpp.github.io/cppwp/n4659/temp.dep.type
https://timsong-cpp.github.io/cppwp/n4659/temp.dep.expr
https://timsong-cpp.github.io/cppwp/n4659/except.spec
A data member whose type is a class template-id with dependent type arguments
(`DynArray<Block*, 10>` in TinyXML2's MemPoolT) is a TemplateIdPattern; each
specialization substitutes and names the shared specialization, which its
constructors and destructor construct and destroy like any class member.
Member calls on such an object are looked up when the body is instantiated.
`Name<P1, ..., Pn>::member` with the primary template's own parameter names
denotes the current instantiation. A template constructor's closed-
construction proof becomes an obligation checked when its body is
instantiated. A destructor with the implicit non-throwing specification
needs no member cleanup path after an unknown call in its body.
Value-dependent value arguments and dependent template-ids in member function
signatures remain explicit gaps. Original tests modeled on TinyXML2 11.0.0
(projects/tinyxml2-11.0.0, Zlib); no external compiler implementation copied.
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

POOL = (
    'typedef unsigned long size_t;'
    'template<class T,size_t N>struct Box{'
    'Box():sink(nullptr),_size(0){}'
    '~Box(){if(sink)*sink=*sink*10+2;}'
    'void Push(T t){_items[_size]=t;_size=_size+1;}'
    'T Pop(){_size=_size-1;return _items[_size];}'
    'bool Empty()const{return _size==0;}'
    'size_t Size()const{return _size;}'
    'int*sink;T _items[N];size_t _size;};'
    'template<size_t S>struct Pool{'
    'union Item{Item*next;char data[S];};'
    'Pool(int*log):_boxes(),_count(0){_boxes.sink=log;*log=*log*10+1;}'
    '~Pool(){Pool< S >::Clear();}'
    'void Clear(){while(!_boxes.Empty()){Item*item=_boxes.Pop();item->next=nullptr;_count=_count+1;}}'
    'void Fill(){Add(&_storage[0]);Add(&_storage[1]);}'
    'void Add(Item*item){_boxes.Push(item);}'
    'size_t Pending()const{return _boxes.Size();}'
    'Box<Item*,4> _boxes;Item _storage[2];size_t _count;};'
)

PROGRAMS = (
    ('dependent-template-id-member-lifetime',
     POOL + 'int cpp_test_entry(){int pending=0;int log=0;'
            '{Pool<16> pool(&log);pool.Fill();pending=(int)pool.Pending();}'
            'return pending*15+log;}'),
    ('current-instantiation-qualified-calls',
     'template<int N>struct M{int value;void Clear(){value=N;}'
     '~M(){M< N >::Clear();}int get(){M<N>::Clear();return value;}};'
     'int cpp_test_entry(){M<42> m;return m.get();}'),
)


@pytest.mark.parametrize('name,source', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_dependent_member_programs_compile_to_stable_artifacts(owned_only, name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_template_id_member_names_the_shared_specialization(owned_only):
    text = '\n'.join(compile_host_cpp((('pool.cpp', PROGRAMS[0][1]),)).ir)
    for symbol in ('_ZN3BoxIPN4PoolILm16EE4ItemELm4EEC1Ev', '_ZN3BoxIPN4PoolILm16EE4ItemELm4EED1Ev',
                   '_ZN3BoxIPN4PoolILm16EE4ItemELm4EE4PushES3_', '_ZNK3BoxIPN4PoolILm16EE4ItemELm4EE5EmptyEv',
                   '_ZN4PoolILm16EE3AddEPNS0_4ItemE', '_ZN4PoolILm16EED1Ev'):
        assert '@' + symbol + '(' in text
    destructor = text[text.index('@_ZN4PoolILm16EED1Ev('):]
    destructor = destructor[:destructor.index('\n}')]
    # The body's qualified call is direct, then the member's D1 runs.
    assert destructor.index('@_ZN4PoolILm16EE5ClearEv') < destructor.index('@_ZN3BoxIPN4PoolILm16EE4ItemELm4EED1Ev')


@pytest.mark.parametrize('source,code', (
    ('typedef unsigned long size_t;template<class T,size_t N>struct B{T v[N];};'
     'template<size_t S>struct P{B<int,S> b;};', 'cpp.unsupported.class-templates'),
    ('template<class T>struct B{T v;};template<class T>struct P{B<T*> get();};',
     'cpp.unsupported.class-templates'),
    ('template<class T>struct B{T v;};template<int N>struct P{struct I{int x;};B<I*> f(){return B<I*>();}};',
     'cpp.unsupported.class-templates'),
))
def test_unmodeled_dependent_members_are_explicit(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)
    assert error.value.code == code
