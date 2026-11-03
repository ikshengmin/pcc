"""Declaration-only self-reference members, CI execution still UNRUN.

N4659 [class.copy.ctor], [class.copy.assign], [class.access], [temp.inst],
[temp.res], [basic.lookup.argdep], [over.sub]. No copy body is synthesized.
COPY_DECLARATIONS is exact CRLF TinyXML2 11.0.0 tinyxml2.h:296-297,
upstream 9148bdf719e997d1f474be6bcc7943881046dba1, Lee Thomason, Zlib.
The scalar scaffold is authored, not a replacement for unchanged DynArray,
its dependent array/allocation/lifetime semantics, or the xmltest gate.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.class_template_members import MethodInstance
from pcc.frontends.cpp.types import ConstType, ReferenceType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only
from tests.cpp.test_cpp_class_size_queries import SINGLE_PROGRAMS as SIZE_ORIGINALS
from tests.cpp.test_cpp_template_subscript_results import SINGLE_PROGRAMS as SUBSCRIPT_ORIGINALS


COPY_DECLARATIONS = (
    '    DynArray( const DynArray& ); // not supported\r\n'
    '    void operator=( const DynArray& ); // not supported\r\n'
)
SCAFFOLD = ('template<class T>class DynArray{T value;public:'
            'DynArray(T n):value(n){}T get()const{return value;}private:' +
            COPY_DECLARATIONS + '};')

PROGRAMS = (
    ('exact-private-copy-declarations-unused', (('original.cpp', SCAFFOLD +
     'int cpp_test_entry(){DynArray<int>b(42);return b.get();}'),)),
    ('public-copy-declarations-unused', (('original.cpp',
     'template<class T>struct Box{T n;Box(T v):n(v){}Box(const Box&);'
     'void operator=(const Box&);T get(){return n;}};'
     'int cpp_test_entry(){Box<int>b(42);return b.get();}'),)),
    ('cv-distinct-copy-overloads-unused', (('original.cpp',
     'template<class T>struct Box{T n;Box(T v):n(v){}Box(Box&);Box(const Box&);'
     'void operator=(Box&);void operator=(const Box&);T get(){return n;}};'
     'int cpp_test_entry(){Box<int>b(42);return b.get();}'),)),
    ('two-tu-copy-declaration-identity', (
        ('provider.cpp', SCAFFOLD + 'int read(const DynArray<int>*p){return p->get();}'),
        ('entry.cpp', SCAFFOLD + 'int read(const DynArray<int>*);'
         'int cpp_test_entry(){DynArray<int>b(21);return read(&b)+b.get();}'),
    )),
    ('retained-ordinary-member-subscript', (('ordinary-member-subscript.cpp',
     dict(SUBSCRIPT_ORIGINALS)['ordinary-member-subscript']),)),
    ('retained-dependent-sizeof-integral-argument', (('dependent-sizeof-integral-argument.cpp',
     dict(SIZE_ORIGINALS)['dependent-sizeof-integral-argument']),)),
    ('zero-argument-definition-point-overloads', (('original.cpp',
     'template<unsigned long N>int select(){return N;}'
     'template<class T>struct S{int size(){return select<sizeof(T)>();}};'
     'template<int N>int select(){return 1;}'
     'int cpp_test_entry(){S<int>s;return s.size()+38;}'),)),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_copy_declarations_and_retained_routes_compile_deterministically(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_copy_declarations_are_exact_upstream_source_bytes():
    header = Path(__file__).parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.h'
    assert COPY_DECLARATIONS.encode() in header.read_bytes()


def test_unused_primary_copy_declarations_retain_reference_and_access_identity():
    parser = CppParser()
    tree = parser.parse(SCAFFOLD)
    owner = parser.scope.names['DynArray'].dtype
    records = [record for record in owner.methods if record.symbol.template_copy_declaration]
    assert len(records) == 2
    for record in records:
        assert record.symbol.parameters == (ReferenceType(ConstType(owner.injected)),)
        assert record.symbol.access == 'private'
        assert record.symbol.class_owner is owner.injected
        assert record.tokens is record.body is None
        assert not record.symbol.defined
    assert not tree.ext and not parser.instance_registry.entries


def test_specializations_project_only_exact_declared_copy_symbols():
    parser = CppParser()
    tree = parser.parse(SCAFFOLD + 'using I=DynArray<int>;using L=DynArray<long>;'
                        'int f(){return sizeof(I)+sizeof(L);}')
    expected = {
        'I': {'_ZN8DynArrayIiEC1ERKS0_', '_ZN8DynArrayIiEaSERKS0_'},
        'L': {'_ZN8DynArrayIlEC1ERKS0_', '_ZN8DynArrayIlEaSERKS0_'},
    }
    for alias, names in expected.items():
        dtype = parser.scope.names[alias].dtype
        assert len(dtype.definition.constructors.overloads) == 2
        assert len(dtype.definition.scope.names['operator='].overloads) == 1
        methods = dtype.definition.constructors.overloads + dtype.definition.scope.names['operator='].overloads
        copies = [method for method in methods if method.template_copy_declaration]
        assert {method.c_name for method in copies} == names
        assert all(method.parameters == (ReferenceType(ConstType(dtype)),) for method in copies)
        assert all(method.class_owner is dtype and method.access == 'private' for method in copies)
        assert all(method.class_template_instance.record.body is None and not method.defined for method in copies)
    declarations = {node.name: node for node in tree.ext if isinstance(node, c_ast.Decl)}
    for name in set.union(*expected.values()):
        assert isinstance(declarations[name].type, c_ast.FuncDecl)
        parameters = declarations[name].type.args.params
        assert len(parameters) == 2 and all(isinstance(item.type, c_ast.PtrDecl) for item in parameters)
        assert parameters[1].type.type.quals == ['const']
    assert not any(isinstance(node, c_ast.FuncDef) and node.decl.name in set.union(*expected.values()) for node in tree.ext)
    assert not any(isinstance(entry.context, MethodInstance) for entry in parser.instance_registry.entries.values())


@pytest.mark.parametrize('source,code', (
    ('template<class T>class Box{Box(const Box&);};void f(const Box<int>*p){Box<int>b(*p);}', 'cpp.member-access'),
    ('template<class T>struct Box{Box(const Box&);};void f(const Box<int>*p){Box<int>b(*p);}', 'cpp.template-definition'),
    ('template<class T>class Box{Box(Box&);public:Box(const Box&);};void f(Box<int>*p){Box<int>b(*p);}', 'cpp.member-access'),
    ('template<class T>class Box{Box(Box&);public:Box(const Box&);};void f(const Box<int>*p){Box<int>b(*p);}', 'cpp.template-definition'),
    ('template<class T>struct Box{Box(int);};void f(Box<int>*p){Box<int>b(*p);}', 'cpp.unsupported.class-initialization'),
    ('template<class T>struct Box{Box(const Box&){}};', 'cpp.unsupported.member-templates'),
    ('template<class T>struct Box{void operator=(const Box&){}};', 'cpp.unsupported.member-templates'),
    ('template<class T>struct Box{static void operator=(const Box&);};', 'cpp.invalid-assignment-operator'),
    ('template<class T>struct Box{void f(const Box&);};', 'cpp.unsupported.references'),
    ('template<class T>struct Box{Box(T&);};', 'cpp.unsupported.references'),
    ('template<class T>struct Box{Box(const Box&,int);};', 'cpp.unsupported.references'),
    ('template<class T>struct Box{Box(Box&&);};', 'cpp.unsupported.references'),
    ('template<class T>struct Box{void operator=(int);};', 'cpp.unsupported.class-templates'),
    ('template<class T>struct Box{void operator=(const Box&);};void f(Box<int>*p,Box<int>*q){*p=*q;}', 'cpp.unsupported.class-assignment'),
    ('struct S{int&operator[];};', 'cpp.invalid-subscript-operator'),
    ('struct S{static int&operator[](int);};', 'cpp.invalid-subscript-operator'),
    ('struct S{int&operator[](int,int);};', 'cpp.invalid-subscript-operator'),
    ('struct S{int&operator[](int=0);};', 'cpp.invalid-subscript-operator'),
    ('template<class T>int select(int);template<class T>struct S{int f(int n){return select<T>(n);}};', 'cpp.unsupported.dependent-lookup'),
))
def test_copy_and_call_boundaries_still_fail_closed(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


def test_private_copy_access_is_checked_before_any_body_request(monkeypatch):
    from pcc.frontends.cpp import class_template_members
    requested = []
    def forbidden(*args, **kwargs):
        requested.append(args)
        raise AssertionError('private copy reached body scheduling')
    monkeypatch.setattr(class_template_members, 'request_body', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse('template<class T>class Box{Box(const Box&);};'
                          'void f(const Box<int>*p){Box<int>b(*p);}')
    assert error.value.code == 'cpp.member-access'
    assert not requested


def test_zero_argument_call_retains_definition_point_candidate_identity():
    parser = CppParser()
    parser.parse('template<unsigned long N>int select(){return N;}'
                 'template<class T>struct S{int size(){return select<sizeof(T)>();}};'
                 'template<int N>int select(){return 1;}')
    owner = parser.scope.names['S'].dtype
    returned = owner.methods[0].body.children[0].children[0]
    # Return conversion may wrap the typed call; keep the check about the
    # declaration snapshot, not a particular scalar-conversion representation.
    node = returned.node
    while node.kind != 'call':
        assert len(node.children) == 1
        child = node.children[0]
        node = child.node if hasattr(child, 'node') else child
    snapshot, explicit = node.value
    assert not node.children and len(explicit) == 1
    current = parser.scope.names['select']
    assert len(current.overloads) == 2 and snapshot.overloads == [current.overloads[0]]
