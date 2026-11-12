"""Member classes of class templates: N4659 [temp.mem.class], [temp.dep.type]/1, [class.nest].

https://timsong-cpp.github.io/cppwp/n4659/temp.mem.class
https://timsong-cpp.github.io/cppwp/n4659/class.nest
https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangle.nested-name
A member class of the current instantiation is a dependent type. Each
specialization builds its own class (struct or union, data members only)
before its own members, with an Itanium nested-name under that
specialization; data member types substitute the enclosing parameters,
member enumerators and earlier member classes. Arrays of a member class
whose special members are all trivial need no element lifetime actions.
Member functions, bases and other declarations in member classes, and naming
`Outer<args>::Member` outside the template, remain explicit gaps.
The shape follows TinyXML2 11.0.0 MemPoolT (projects/tinyxml2-11.0.0, Zlib).
Original tests; no external compiler implementation is copied.
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

POOL = (
    'typedef unsigned long size_t;'
    'namespace t{template<size_t N>struct M{'
    'union Item{Item*next;char data[static_cast<size_t>(N)];};'
    'struct Block{Item items[4];};'
    'enum{PER=64/N};'
    'struct Many{Item items[PER];};'
    'Item*root;Block*block;size_t count;'
    'int keep(Item*item,Block*b){root=item;block=b;return (int)count;}'
    'size_t sizes()const{return sizeof(Item)*1000+sizeof(Block)*10+sizeof(Many)/64;}'
    '};}'
)

PROGRAMS = (
    ('per-specialization-layout-and-mangling',
     POOL + 'int cpp_test_entry(){t::M<8> m;m.count=2;t::M<16> w;w.count=3;'
            'int r=m.keep(nullptr,nullptr)+w.keep(nullptr,nullptr);'
            'int sum=(int)(m.sizes()%256)+(int)(w.sizes()%7)+r;return sum==136?42:sum;}'),
    ('free-list-through-member-union',
     'typedef unsigned long size_t;template<size_t N>struct P{'
     'union Item{Item*next;char data[N];};struct Block{Item items[3];};'
     'Block storage;Item*root;'
     'int link(){Item*items=storage.items;items[0].next=&items[1];items[1].next=&items[2];items[2].next=nullptr;'
     'root=items;int n=0;for(Item*i=root;i;i=i->next)n=n+1;return n;}};'
     'int cpp_test_entry(){P<16> p;return p.link()*14;}'),
)


def test_each_specialization_owns_its_member_classes():
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    parser.parse(POOL + 'int f(){t::M<8> a;t::M<16> b;a.count=1;b.count=1;return 0;}')
    names = {}
    for declaration in parser.class_template_declarations:
        tag = getattr(declaration.type, 'name', None)
        if tag:
            names[tag] = type(declaration.type).__name__
    items = sorted(tag for tag in names if tag.endswith('_4Item'))
    assert len(items) == 2 and all(names[tag] == 'Union' for tag in items)
    assert len([tag for tag in names if tag.endswith('_5Block')]) == 2


@pytest.mark.parametrize('source,code', (
    ('template<int N>struct M{struct S{int f();};};', 'cpp.unsupported.class-templates'),
    ('struct B{};template<int N>struct M{struct S:B{int x;};};', 'cpp.unsupported.class-templates'),
    ('struct B{};template<int N>struct M{union U:B{int x;};};', 'cpp.invalid-union-base'),
    ('template<int N>struct M{struct S{S s;};};', 'cpp.incomplete-class'),
    ('template<int N>struct M{struct N{int x;};};', 'cpp.template-parameter-shadowing'),
    ('template<int N>struct M{struct M{int x;};};', 'cpp.class-name-member'),
    ('template<int N>struct M{struct S{int x;};struct S{int y;};};', 'cpp.redeclaration'),
    ('template<int N>struct M{struct S{int x=1;};};', 'cpp.unsupported.class-templates'),
    ('struct C{C();int x;};template<int N>struct M{union U{C c;};U*p;};int f(){M<1> m;m.p=nullptr;return 0;}',
     'cpp.unsupported.unions'),
))
def test_invalid_or_unimplemented_member_classes_are_explicit(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)
    assert error.value.code == code


@pytest.mark.parametrize('name,source', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_member_class_programs_compile_to_stable_artifacts(owned_only, name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_member_class_symbols_use_itanium_nested_names_under_the_specialization(owned_only):
    artifact = compile_host_cpp((('mangling.cpp', PROGRAMS[0][1]),))
    text = '\n'.join(artifact.ir)
    for symbol in ('_ZN1t1MILm8EE4keepEPNS1_4ItemEPNS1_5BlockE', '_ZN1t1MILm16EE4keepEPNS1_4ItemEPNS1_5BlockE',
                   '_ZNK1t1MILm8EE5sizesEv', '_ZNK1t1MILm16EE5sizesEv'):
        assert '@' + symbol + '(' in text
