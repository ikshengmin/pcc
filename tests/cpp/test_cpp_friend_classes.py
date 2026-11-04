"""Existing-class friendship, N4659 [class.friend] and [namespace.memdef]/3.

Nested using-directives follow the CWG 138 resolution in P1787R6; an external
reference that implements older lookup rules can disagree on those originals.

The original two friend declarations
come from TinyXML2 11.0.0 tinyxml2.h:672-673, commit
9148bdf719e997d1f474be6bcc7943881046dba1, by Lee Thomason under Zlib.
The surrounding classes and observer are PCC-authored. This is a component,
not the original XMLNode layout, XMLDocument behavior or whole project.
"""

import hashlib

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


TINYXML_FRIENDS = '    friend class XMLDocument;\r\n    friend class XMLElement;\r\n'
TINYXML_COMPONENT = (
    'class XMLDocument;class XMLElement;class XMLNode{\n' + TINYXML_FRIENDS +
    'int value;};class XMLDocument{public:static void set(XMLNode*p){p->value=17;}};'
    'class XMLElement{public:static int finish(XMLNode*p){p->value+=25;return p->value;}};'
    'int cpp_test_entry(){XMLNode node;XMLDocument::set(&node);return XMLElement::finish(&node);}'
)
OUTER_NAMESPACE_ORIGINAL = (
    'class Reader;namespace n{int Reader;class Owner{friend class Reader;int n;};}'
    'class Reader{public:static int run(){n::Owner o;o.n=42;return o.n;}};'
    'int cpp_test_entry(){return Reader::run();}'
)

CASES = (
    ('forward-identity-private-field',
     'class Reader;class Owner{friend class Reader;int n;};'
     'class Reader{public:static int run(){Owner o;o.n=42;return o.n;}};'
     'int cpp_test_entry(){return Reader::run();}'),
    ('protected-field',
     'class Reader;class Owner{friend struct Reader;protected:int n;};'
     'struct Reader{static int run(){Owner o;o.n=42;return o.n;}};'
     'int cpp_test_entry(){return Reader::run();}'),
    ('private-static-method',
     'struct Reader;class Owner{friend class Reader;static int hidden(){return 42;}};'
     'struct Reader{static int run(){return Owner::hidden();}};'
     'int cpp_test_entry(){return Reader::run();}'),
    ('private-instance-method',
     'struct Reader;class Owner{friend class Reader;int hidden(){return 42;}};'
     'struct Reader{static int run(){Owner o;return o.hidden();}};'
     'int cpp_test_entry(){return Reader::run();}'),
    ('private-alias-member-signature',
     'class Reader;class Owner{friend class Reader;using I=int;};'
     'class Reader{public:static Owner::I run(){return 42;}};'
     'int cpp_test_entry(){return Reader::run();}'),
    ('out-of-class-private-return-alias',
     'class Reader;class Owner{friend class Reader;using I=int;};'
     'class Reader{public:static Owner::I run();};Owner::I Reader::run(){return 42;}'
     'int cpp_test_entry(){return Reader::run();}'),
    ('private-enumerator',
     'class Reader;class Owner{friend class Reader;enum{answer=42};};'
     'class Reader{public:static int run(){return Owner::answer;}};'
     'int cpp_test_entry(){return Reader::run();}'),
    ('private-constructor-and-destructor',
     'class Reader;class Owner{friend class Reader;int*p;Owner(int*q):p(q){}~Owner(){*p+=1;}};'
     'class Reader{public:static int run(){int n=41;{Owner o(&n);}return n;}};'
     'int cpp_test_entry(){return Reader::run();}'),
    ('qualified-namespace-identity',
     'namespace allowed{class Reader;}namespace other{class Reader;}'
     'class Owner{friend class allowed::Reader;int n;};'
     'namespace allowed{class Reader{public:static int run(){Owner o;o.n=42;return o.n;}};}'
     'int cpp_test_entry(){return allowed::Reader::run();}'),
    ('global-qualified-namespace-alias',
     'namespace allowed{class Reader;}namespace alias=allowed;'
     'class Owner{friend class ::alias::Reader;int n;};'
     'namespace allowed{class Reader{public:static int run(){Owner o;o.n=42;return o.n;}};}'
     'int cpp_test_entry(){return allowed::Reader::run();}'),
    ('using-directive-class-lookup',
     'namespace allowed{class Reader;}using namespace allowed;'
     'class Owner{friend class Reader;int n;};'
     'namespace allowed{class Reader{public:static int run(){Owner o;o.n=42;return o.n;}};}'
     'int cpp_test_entry(){return allowed::Reader::run();}'),
    ('using-declaration-class-lookup',
     'namespace allowed{class Reader;}using allowed::Reader;'
     'class Owner{friend class Reader;int n;};'
     'namespace allowed{class Reader{public:static int run(){Owner o;o.n=42;return o.n;}};}'
     'int cpp_test_entry(){return Reader::run();}'),
    ('member-value-does-not-hide-class',
     'class Reader;class Owner{int Reader;friend class Reader;};'
     'class Reader{public:static int run(){Owner o;o.Reader=42;return o.Reader;}};'
     'int cpp_test_entry(){return Reader::run();}'),
    ('namespace-local-class-survives-member-value',
     'namespace n{class Reader;class Owner{int Reader;friend class Reader;};'
     'class Reader{public:static int run(){Owner o;o.Reader=42;return o.Reader;}};}'
     'int cpp_test_entry(){return n::Reader::run();}'),
    ('qualifier-value-does-not-hide-namespace',
     'namespace allowed{class Reader;}namespace other{int allowed;'
     'class Owner{friend class allowed::Reader;int n;};}'
     'namespace allowed{class Reader{public:static int run(){other::Owner o;o.n=42;return o.n;}};}'
     'int cpp_test_entry(){return allowed::Reader::run();}'),
    ('qualified-injected-class-name',
     'class Reader{public:static int run();};class Owner{friend class Reader::Reader;int n;};'
     'int Reader::run(){Owner o;o.n=42;return o.n;}'
     'int cpp_test_entry(){return Reader::run();}'),
    ('complete-global-friend-out-of-class-body',
     'class Reader{public:static int run();};namespace n{class Owner{friend class ::Reader;int n;};}'
     'int Reader::run(){n::Owner o;o.n=42;return o.n;}'
     'int cpp_test_entry(){return Reader::run();}'),
    ('self-friend',
     'class Owner{friend class Owner;int n;public:static int run(){Owner o;o.n=42;return o.n;}};'
     'int cpp_test_entry(){return Owner::run();}'),
    ('original-tinyxml-friend-declarations', TINYXML_COMPONENT),
)

SHARED = ('class Reader;class Owner{friend class Reader;int n;};'
          'class Reader{public:static int read(int n){Owner o;o.n=n;return o.n;}};')
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES) + (
    ('two-tu-friendship-keeps-inline-odr', (
        ('provider.cpp', SHARED + 'int provider(){return Reader::read(17);}'),
        ('entry.cpp', SHARED + 'int provider();int cpp_test_entry(){return provider()+Reader::read(25);}'),
    )),
)

INVALID = (
    # Preserve the original bytes. The friend would introduce n::Reader, not
    # befriend ::Reader; PCC stops at the earlier unimplemented introduction.
    ('namespace-value-does-not-hide-class', OUTER_NAMESPACE_ORIGINAL,
     'cpp.unsupported.classes'),
    ('unrelated-function',
     'class Reader;class Owner{friend class Reader;int n;};int read(Owner*p){return p->n;}',
     'cpp.member-access'),
    ('friendship-not-reciprocal',
     'class B;class A{friend class B;public:static int read(B*);};class B{int n;};'
     'int A::read(B*p){return p->n;}', 'cpp.member-access'),
    ('friendship-not-transitive',
     'class C;class B;class A{friend class B;int n;};class B{friend class C;};'
     'class C{public:static int read(A*p){return p->n;}};', 'cpp.member-access'),
    ('same-spelling-different-class',
     'namespace allowed{class Reader;}class Owner{friend class allowed::Reader;int n;};'
     'namespace other{class Reader{public:static int read(Owner*p){return p->n;}};}',
     'cpp.member-access'),
    ('elaborated-typedef-alias',
     'class Reader;typedef Reader Alias;class Owner{friend class Alias;};', 'cpp.redeclaration'),
    ('elaborated-using-alias',
     'class Reader;using Alias=Reader;class Owner{friend struct Alias;};', 'cpp.redeclaration'),
    ('elaborated-enum', 'enum E{v};class Owner{friend class E;};', 'cpp.redeclaration'),
    ('elaborated-scalar-alias', 'using I=int;class Owner{friend class I;};', 'cpp.redeclaration'),
    ('qualified-undeclared-class', 'namespace n{}class Owner{friend class n::Missing;};',
     'cpp.undeclared'),
    ('inaccessible-nominated-alias',
     'class Reader;class Hidden{using Alias=Reader;};class Owner{friend class Hidden::Alias;};',
     'cpp.member-access'),
    ('nearer-alias-stops-outer-class-lookup',
     'class Reader;namespace n{using Reader=int;class Owner{friend class Reader;};}',
     'cpp.redeclaration'),
    ('ambiguous-using-directive-classes',
     'namespace a{class Reader;}namespace b{class Reader;}using namespace a;using namespace b;'
     'class Owner{friend class Reader;};', 'cpp.ambiguous-name'),
    ('class-template-qualifier-is-not-a-namespace',
     'namespace Q{class R;}namespace N{template<class T>class Q{};'
     'class Owner{friend class Q::R;};}', 'cpp.unsupported.dependent-lookup'),
)

NAMESPACE_BOUNDARIES = (
    ('enclosing-namespace-class-is-not-a-local-friend',
     'class Reader;namespace inner{class Owner{friend class Reader;};}'),
    ('sibling-directive-does-not-import-friend',
     'namespace external{class Reader;}namespace inner{using namespace external;'
     'class Owner{friend class Reader;};}'),
    ('outer-using-declaration-not-visible-to-friend',
     'namespace external{class Reader;}using external::Reader;'
     'namespace inner{class Owner{friend class Reader;};}'),
    ('outer-alias-not-visible-to-friend',
     'using Reader=int;namespace inner{class Owner{friend class Reader;};}'),
)

UNIMPLEMENTED = (
    ('new-unqualified-class', 'class Owner{friend class NewClass;};'),
    ('simple-class-name', 'class Reader;class Owner{friend Reader;};'),
    ('simple-type-alias', 'class Reader;using Alias=Reader;class Owner{friend Alias;};'),
    ('ignored-nonclass-friend', 'using I=int;class Owner{friend I;};'),
    ('friend-function-declaration', 'class Owner{friend int read(Owner*);};'),
    ('friend-function-definition', 'class Owner{friend int read(Owner*){return 42;}};'),
    ('class-template-specialization',
     'template<class T>class Reader{};class Owner{friend class Reader<int>;};'),
) + NAMESPACE_BOUNDARIES


@pytest.mark.parametrize('name,source', NAMESPACE_BOUNDARIES,
                         ids=[row[0] for row in NAMESPACE_BOUNDARIES])
def test_unqualified_friend_does_not_escape_its_namespace_or_install_a_grant(name, source):
    parser = CppParser()
    with pytest.raises(CppDiagnostic) as error:
        parser.parse(source, filename=name + '.cpp')
    assert error.value.code == 'cpp.unsupported.classes'
    inner = parser.scope.global_scope().names['inner'].namespace_scope
    assert 'Reader' not in inner.names
    assert inner.names['Owner'].dtype.definition.friend_classes == []


def test_class_template_qualifier_cannot_fall_through_to_an_outer_namespace():
    source = ('namespace Q{class R;}namespace N{template<class T>class Q{};'
              'class Owner{friend class Q::R;};}')
    parser = CppParser()
    with pytest.raises(CppDiagnostic) as error:
        parser.parse(source)
    assert error.value.code == 'cpp.unsupported.dependent-lookup'
    root = parser.scope.global_scope()
    owner = root.names['N'].namespace_scope.names['Owner'].dtype
    assert owner.definition.friend_classes == []


def test_friendship_preserves_forward_identity_visibility_and_layout():
    parser = CppParser()
    tree = parser.parse('class Reader;class Owner{friend class Reader;int n;};class Reader{};')
    reader = parser.scope.names['Reader'].dtype
    owner = parser.scope.names['Owner'].dtype
    assert owner.definition.friend_classes == [reader]
    assert reader.complete
    assert reader.declaration_scope is parser.scope
    assert 'Reader' not in owner.definition.scope.names
    assert [member.name for member in owner.definition.members] == ['n']
    projected = [item.type for item in tree.ext if isinstance(item, c_ast.Decl)
                 and isinstance(item.type, c_ast.Struct) and item.type.name == owner.c_tag]
    assert len(projected) == 1
    assert [member.name for member in projected[0].decls] == ['n']
    assert all(not getattr(member, 'friend_classes', ()) for member in owner.definition.members)


@pytest.mark.parametrize('access', ('public', 'protected', 'private'))
def test_friendship_access_section_and_duplicate_declarations_do_not_change_grant(access):
    parser = CppParser()
    parser.parse('class Reader;class Owner{' + access + ':friend class Reader;'
                 'friend struct Reader;private:int n;};'
                 'class Reader{public:static int read(Owner*p){return p->n;}};')
    owner = parser.scope.names['Owner'].dtype
    assert owner.definition.friend_classes == [parser.scope.names['Reader'].dtype]
    assert owner.definition.members[0].access == 'private'


def test_failed_friend_declaration_does_not_install_a_grant():
    parser = CppParser()
    with pytest.raises(CppDiagnostic):
        parser.parse('class Reader;using Alias=Reader;class Owner{friend class Alias;};')
    root = parser.scope.global_scope()
    assert root.names['Owner'].dtype.definition.friend_classes == []
    assert 'Alias' not in root.names['Owner'].dtype.definition.scope.names


def test_tinyxml_friend_declaration_bytes_are_unmodified():
    assert hashlib.sha256(TINYXML_FRIENDS.encode('utf-8')).hexdigest() == (
        '890617bc158b318b388a5b400e4eec37507af9cb9755a4759eea4cebfd27e404'
    )
    assert TINYXML_COMPONENT.encode('utf-8').count(TINYXML_FRIENDS.encode('utf-8')) == 1
    parser = CppParser()
    parser.parse(TINYXML_COMPONENT, filename='tinyxml-friend-component.cpp')
    assert parser.scope.names['XMLNode'].dtype.definition.friend_classes == [
        parser.scope.names['XMLDocument'].dtype, parser.scope.names['XMLElement'].dtype,
    ]


@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_friend_originals_keep_access_and_type_diagnostics(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, filename=name + '.cpp')
    assert error.value.code == code
    assert error.value.coord.file == name + '.cpp'


@pytest.mark.parametrize('name,source', UNIMPLEMENTED, ids=[row[0] for row in UNIMPLEMENTED])
def test_valid_unimplemented_friend_originals_remain_explicit(name, source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, filename=name + '.cpp')
    assert error.value.code == 'cpp.unsupported.classes'


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_friend_original_owned_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)
