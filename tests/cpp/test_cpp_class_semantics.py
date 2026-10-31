"""Public class/data-member C++17 cases authored for PCC, not upstream suites.

These are local frontend regressions, not a complete class conformance suite or
TinyXML2 project qualification. Constructors, exceptions and class-value ABI
remain independent required work. See test_cpp_class_model for rule sources.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.classes import ClassType
from pcc.frontends.cpp.types import ConstType, PointerType


CLASS_CASES = (
    ("data-member-storage-and-mutation",
     "struct Point { int x,y; }; int cpp_test_entry(){Point p;"
     "p.x=17;p.y=25;return p.x+p.y;}", 42),
    ("empty-list-zero-initializes-every-member",
     "struct Record { char tag; int value; long tail; int *pointer; };"
     "int cpp_test_entry(){Record r{};return r.tag+r.value+r.tail+int(r.pointer==nullptr);}", 1),
    ("class-public-data-and-default-private-type",
     "class Record { public: int value; };"
     "int cpp_test_entry(){Record r{};r.value=42;return r.value;}", 42),
    ("nested-object-members-retain-identity",
     "struct Inner{int value;};struct Outer{char tag;Inner inner;long tail;};"
     "int cpp_test_entry(){Outer o{};o.tag=3;o.inner.value=17;o.tail=22;"
     "return o.tag+o.inner.value+o.tail;}", 42),
    ("recursive-pointer-member-not-recursive-value",
     "struct Node{Node *next;int value;};int cpp_test_entry(){Node a{};Node b{};"
     "a.value=5;a.next=&b;a.next->value=37;return a.value+b.value;}", 42),
    ("forward-definition-completes-same-type",
     "class Record;Record *identity(Record *p);struct Record{int value;};"
     "Record *identity(Record *p){return p;}int cpp_test_entry(){Record r{};"
     "identity(&r)->value=42;return r.value;}", 42),
    ("aliases-preserve-class-and-pointer-types",
     "struct Record{int value;};using R=Record;using P=R *;"
     "int cpp_test_entry(){R r{};P p=&r;p->value=42;return r.value;}", 42),
    ("member-type-alias-is-lexically-resolved",
     "using T=long;struct Record{using T=int;T value;};"
     "int cpp_test_entry(){Record r{};r.value=42;return r.value;}", 42),
    ("namespace-qualified-class-type",
     "namespace data{struct Record{int value;};}using R=data::Record;"
     "int cpp_test_entry(){R r{};r.value=42;return r.value;}", 42),
    ("same-spelled-namespace-classes-stay-distinct",
     "namespace a{struct S{int value;};}namespace b{struct S{long value;};}"
     "int take(a::S *p){return p->value;}int take(b::S *p){return p->value+1;}"
     "int cpp_test_entry(){a::S a{};b::S b{};a.value=20;b.value=21;"
     "return take(&a)+take(&b);}", 42),
    ("const-class-pointee-and-mutable-data",
     "struct Record{mutable int cache;int value;};int read(const Record *p){return p->value;}"
     "int cpp_test_entry(){const Record r{};r.cache=42;return r.cache+read(&r);}", 42),
    ("const-pointer-does-not-make-object-const",
     "struct Record{int value;};int cpp_test_entry(){Record r{};Record *const p=&r;"
     "p->value=42;return r.value;}", 42),
    ("const-pointer-data-member-does-not-qualify-pointee",
     "struct Record{int *pointer;};int cpp_test_entry(){Record r{};int value=7;"
     "r.pointer=&value;const Record *p=&r;*p->pointer=42;return value;}", 42),
    ("empty-list-initializes-const-aggregate-members",
     "struct Inner{const int value;};struct Outer{Inner inner;};"
     "int cpp_test_entry(){const Outer o{};return o.inner.value;}", 0),
    ("const-empty-default-construction-cwg2394",
     "struct Empty{};struct Outer{const Empty empty;};"
     "int cpp_test_entry(){const Outer o;return 42;}", 42),
    ("distinct-empty-objects-and-subobjects",
     "struct Empty{};struct Pair{Empty first;Empty second;};"
     "int cpp_test_entry(){Empty a;Empty b;Pair pair;return int(&a!=&b)"
     "+int(&pair.first!=&pair.second)*2;}", 3),
    ("class-pointer-to-void-is-not-object-conversion",
     "struct Record{int value;};void *erase(Record *p){return p;}"
     "int cpp_test_entry(){Record r{};return int(erase(&r)!=nullptr);}", 1),
    ("member-assignment-sequences-rhs-before-pointer-lhs",
     "struct Record{int value;};Record *left(Record *p){p->value=1;return p;}"
     "int right(Record *p){return p->value;}int cpp_test_entry(){Record r{};r.value=42;"
     "left(&r)->value=right(&r);return r.value;}", 42),
    ("class-pointer-adl-adds-better-namespace-candidate",
     "int choose(void *p){return 11;}namespace n{struct S{};"
     "int choose(S *p){return 42;}}int cpp_test_entry(){n::S s{};return choose(&s);}", 42),
    ("qualified-call-suppresses-class-pointer-adl",
     "int choose(void *p){return 42;}namespace n{struct S{};"
     "int choose(S *p){return 11;}}int cpp_test_entry(){n::S s{};return ::choose(&s);}", 42),
    ("class-pointer-adl-ignores-using-directives",
     "namespace other{int choose(void *p){return 99;}}"
     "namespace n{using namespace other;struct S{};}int choose(const void *p){return 42;}"
     "int cpp_test_entry(){n::S s{};return choose(&s);}", 42),
    ("class-alias-keeps-original-adl-namespace",
     "namespace n{struct S{};int choose(S *p){return 42;}}"
     "namespace alias{using T=n::S;}int choose(void *p){return 11;}"
     "int cpp_test_entry(){alias::T s{};return choose(&s);}", 42),
    ("class-declarations-coexist-with-scalar-template-instances",
     "struct S{int value;};template<class T>T identity(T value){return value;}"
     "int cpp_test_entry(){S s{};s.value=42;return identity(s.value);}", 42),
    ("same-named-ordinary-data-member-is-not-a-type-alias",
     "struct S{int S;};int cpp_test_entry(){S s{};s.S=42;return s.S;}", 42),
    ("namespace-type-alias-redeclaration-is-not-a-class-member-redeclaration",
     "using T=int;using T=int;typedef int T;struct S{T value;};"
     "int cpp_test_entry(){S s{};s.value=42;return s.value;}", 42),
)


@pytest.mark.parametrize("name,source,value", CLASS_CASES, ids=[case[0] for case in CLASS_CASES])
def test_public_class_parser_retains_real_structs_and_function_body(name, source, value):
    parser = CppParser("x86_64-unknown-linux-gnu")
    ast = parser.parse(source, name + ".cpp")
    assert any(isinstance(decl, c_ast.Decl) and isinstance(decl.type, c_ast.Struct)
               for decl in ast.ext)
    entry = parser.scope.lookup("cpp_test_entry").overloads[0]
    assert entry.defined
    assert any(isinstance(decl, c_ast.FuncDef) and decl.decl.name == entry.c_name
               for decl in ast.ext)


def test_class_forward_alias_cv_and_pointer_identity_survive_parser():
    parser = CppParser()
    parser.parse("class Record;using R=Record;struct Record{int value;};"
                 "const R *view(R *p){return p;}")
    dtype = parser.scope.lookup("Record").dtype
    assert isinstance(dtype, ClassType)
    assert dtype.complete
    assert dtype.declaration_scope is parser.scope
    assert parser.scope.lookup("R").dtype is dtype
    view = parser.scope.lookup("view").overloads[0]
    assert view.parameters == (PointerType(dtype),)
    assert view.dtype == PointerType(ConstType(dtype))
    assert view.c_name == "_Z4viewP6Record"


@pytest.mark.parametrize("source,path,expected", (
    ("struct S{};int f(S*,S*);", (), "_Z1fP1SS0_"),
    ("namespace n{struct S{};int f(S*,S*);}", ("n",), "_ZN1n1fEPNS_1SES1_"),
    ("namespace n{struct S{};}int f(n::S*,n::S*);", (), "_Z1fPN1n1SES1_"),
    ("namespace n{struct S{};int f(const S*,const S*);}", ("n",),
     "_ZN1n1fEPKNS_1SES2_"),
))
def test_class_pointer_mangling_shares_namespace_and_type_substitution_table(source, path, expected):
    parser = CppParser()
    parser.parse(source, "class-mangling.cpp")
    scope = parser.scope
    for name in path:
        scope = scope.lookup(name).namespace_scope
    assert scope.lookup("f").overloads[0].c_name == expected


CLASS_NEGATIVE_CASES = (
    ("struct Bad{void value;};", "cpp.invalid-object-type"),
    ("struct Bad{Bad value;};", "cpp.incomplete-class"),
    ("struct Other;struct Bad{Other value;};", "cpp.incomplete-class"),
    ("struct Bad{int value;long value;};", "cpp.redeclaration"),
    ("struct Bad{};struct Bad{};", "cpp.class-redefinition"),
    ("using Bad=int;struct Bad;", "cpp.redeclaration"),
    ("struct S{};using Bad=S;struct Bad;", "cpp.redeclaration"),
    ("struct S{using S=int;S value;};", "cpp.class-name-member"),
    ("struct S{typedef int S;S value;};", "cpp.class-name-member"),
    ("struct S{using S=S;};", "cpp.class-name-member"),
    ("struct S{using T=int;using T=int;T value;};", "cpp.redeclaration"),
    ("struct S{typedef int T;typedef int T;T value;};", "cpp.redeclaration"),
    ("struct S{using T=int;typedef int T;T value;};", "cpp.redeclaration"),
    ("struct Bad{mutable const int value;};", "cpp.invalid-mutable"),
    ("namespace ns{}struct Bad{using namespace ns;};", "cpp.using-directive-scope"),
    ("struct S;int f(){S object;return 0;}", "cpp.incomplete-class"),
    ("struct S;int f(S *p){return p->value;}", "cpp.incomplete-class"),
    ("class S{int value;};int f(){S s;return s.value;}", "cpp.member-access"),
    ("struct S{protected:int value;};int f(S *p){return p->value;}", "cpp.member-access"),
    ("struct S{};int value();int f(S *p){return p->value;}", "cpp.no-member"),
    ("struct S{using T=int;};int f(S *p){return p->T;}", "cpp.member-not-value"),
    ("struct S{int value;};int f(){const S s{};s.value=1;return 0;}", "cpp.const-assignment"),
    ("struct S{int value;};int f(const S *p){p->value=1;return 0;}", "cpp.const-assignment"),
    ("struct S{const int value;};int f(){S s;return 0;}", "cpp.deleted-default-constructor"),
    ("class S{const int value;};int f(){S s{};return 0;}", "cpp.deleted-default-constructor"),
    ("struct S{int value;};int f(){const S s;return 0;}", "cpp.const-initializer"),
    ("int f(){int value=1;return value.member;}", "cpp.invalid-member-access"),
    ("int f(int *p){return p->member;}", "cpp.invalid-member-access"),
)


@pytest.mark.parametrize("source,code", CLASS_NEGATIVE_CASES)
def test_public_class_semantic_diagnostics(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, "class-negative.cpp")
    assert error.value.code == code
    assert error.value.coord.file == "class-negative.cpp"


CLASS_UNIMPLEMENTED_CASES = (
    "class P{int value;};int f(){const P p{};return 0;}",
    "namespace {struct S{};}int f(S *);",
    "struct S;int f(S *p){*p;return 0;}",
    # Original member-alias source moved verbatim to test_cpp_class_enums.
    "union S{int value;};",
    "struct S{S();};",
    "struct S{~S();};",
    "struct S{int method();};",
    "struct Base{};struct S:Base{};",
    "struct S{virtual int method();};",
    "struct S{static int value;};",
    "struct S{int value=7;};",
    "struct S{int value:3;};",
    "struct S{struct Nested{};};",
    "namespace n{using T=int;}struct S{using n::T;};",
    "namespace n{struct S;}using n::S;struct S{int value;};",
    "int f(){struct Local{};return 0;}",
    "struct S{};int f(S s);",
    "struct S{};S f();",
    "struct S{};int f(){S a;S b;a=b;return 0;}",
    "struct S{};int f(){S a;S b;a+b;return 0;}",
    "struct S{};int f(){S a;+a;return 0;}",
    "struct S{};int f(){S a;bool b=a;return 0;}",
    "struct S{};template<class T>T id(T p){return p;}int f(){S a;id(a);return 0;}",
    # Nonempty aggregate case moved unchanged to test_cpp_aggregate_initialization.
    "struct S{};int f(){S a=S();return 0;}",
)


@pytest.mark.parametrize("source", CLASS_UNIMPLEMENTED_CASES)
def test_unimplemented_class_lifetime_and_abi_never_become_c_semantics(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, "class-capability.cpp")
    assert error.value.code.startswith("cpp.unsupported.")
    assert error.value.coord.file == "class-capability.cpp"


def test_class_declarations_are_not_duplicated_as_template_prototypes():
    parser = CppParser()
    ast = parser.parse("struct S{int value;};template<class T>T id(T x){return x;}"
                       "int cpp_test_entry(){S s{};s.value=42;return id(s.value);}")
    definitions = [decl for decl in ast.ext if isinstance(decl, c_ast.Decl)
                   and isinstance(decl.type, c_ast.Struct) and decl.type.decls is not None]
    assert len(definitions) == 1


def test_x86_64_long_double_class_member_is_not_lowered_as_double():
    parser = CppParser(target_triple="x86_64-unknown-linux-gnu")
    with pytest.raises(CppDiagnostic) as error:
        parser.parse("struct S{long double value;};", "extended-class.cpp")
    assert error.value.code == "cpp.unsupported.extended-float"
