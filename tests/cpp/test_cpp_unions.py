"""C++ unions with trivial members: N4659 [class.union], [dcl.init.aggr]/16, [dcl.type.elab]/3.

https://timsong-cpp.github.io/cppwp/n4659/class.union
https://timsong-cpp.github.io/cppwp/n4659/dcl.init.aggr#16
https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling-type
A union is a class whose data members share offset 0. The frontend admits
trivial members only, so the union's implicit special members are trivial,
and projects it to the shared C union record. A list initializes the first
member, which must cover the union so that zeroed padding is real. Member
functions, non-trivial members and default member initializers remain
explicit gaps. Original tests; no external compiler implementation is copied.
"""
import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.size_queries import object_layout
from pcc.frontends.cpp.types import PointerType, mangle_function
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

PROGRAMS = (
    ('member-storage-is-shared',
     'union Word{unsigned int whole;unsigned char bytes[4];};'
     'int cpp_test_entry(){Word w;w.whole=0x01020304u;'
     'return w.bytes[0]+w.bytes[1]+w.bytes[2]+w.bytes[3]+32;}'),
    ('self-referential-free-list',
     'union Node{Node*next;char data[16];};'
     'int cpp_test_entry(){Node nodes[3];nodes[0].next=&nodes[1];nodes[1].next=&nodes[2];nodes[2].next=nullptr;'
     'int count=0;for(Node*n=nodes;n;n=n->next)count=count+1;'
     'return count*10+(sizeof(Node)==16)*12;}'),
    ('union-member-of-struct-and-list-init',
     'union Word{unsigned int whole;unsigned char bytes[4];};struct Holder{int tag;Word word;};'
     'int cpp_test_entry(){Holder h={30,{0x0a0b0c0cu}};Word zero{};'
     'return h.tag+h.word.bytes[0]+(int)zero.whole+(sizeof(Holder)==8)-1;}'),
    ('pointer-parameter-and-mangling',
     'union Word{unsigned int whole;unsigned char bytes[4];};'
     'int low(const Word*w){return w->bytes[0];}'
     'int cpp_test_entry(){Word w;w.whole=0x2a;return low(&w);}'),
)


def test_union_is_a_class_with_a_c_union_record_and_overlapping_layout():
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    unit = parser.parse('union Node{Node*next;char data[12];short s;};')
    node = parser.scope.names['Node'].dtype
    assert node.is_union and node.definition.access == 'public'
    records = [ext.type for ext in unit.ext if isinstance(ext, c_ast.Decl)]
    assert any(isinstance(record, c_ast.Union) and record.name == node.c_tag for record in records)
    layout = object_layout(node, parser.types, None)
    assert (layout.size, layout.alignment) == (16, 8)
    assert mangle_function('f', (PointerType(node),)) == '_Z1fP4Node'


def test_original_union_declaration_from_class_semantics_is_positive():
    # Moved verbatim from test_cpp_class_semantics' unimplemented cases.
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    parser.parse('union S{int value;};')
    union = parser.scope.names['S'].dtype
    assert union.is_union and [member.name for member in union.definition.members] == ['value']


@pytest.mark.parametrize('source,code', (
    ('struct B{};union U:B{int x;};', 'cpp.invalid-union-base'),
    ('union U{virtual int f();};', 'cpp.invalid-union-member'),
    ('union U;struct U{int x;};', 'cpp.class-key-mismatch'),
    ('struct U;union U{int x;};', 'cpp.class-key-mismatch'),
    ('union U{int x;int y;};int f(){U u={1,2};return u.x;}', 'cpp.too-many-initializers'),
    ('union U{int f(){return 1;}};', 'cpp.unsupported.classes'),
    ('union U{U();int x;};', 'cpp.unsupported.classes'),
    ('union U{~U();int x;};', 'cpp.unsupported.classes'),
    ('union U{int x=1;};', 'cpp.unsupported.classes'),
    ('struct S{S();int x;};union U{S s;};', 'cpp.unsupported.unions'),
    ('union U{char c;int i;};int f(){U u{};return u.i;}', 'cpp.unsupported.unions'),
))
def test_invalid_or_unimplemented_union_routes_are_explicit(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)
    assert error.value.code == code


@pytest.mark.parametrize('name,source', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_union_programs_compile_to_stable_artifacts(owned_only, name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)
