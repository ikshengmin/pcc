"""Ordinary member-class identity, lexical access and original-token bodies.

C++17 [class.nest], [class.access.nest]. The two formerly gated declarations
remain byte-for-byte here; separate executable observers do not replace them.
Owned compilation is independently compared with G++ only in integration tests.
No native pcc1 or whole TinyXML2 qualification is claimed. Class-member templates,
local classes and qualified out-of-class class definitions remain unsupported.
"""

import pytest
from pcc.frontends.cpp import CppParser, CppDiagnostic
from tests.cpp.owned_execution import compile_host_cpp


@pytest.mark.parametrize(
    "source",
    [
        "struct O{struct I{int n;};};int cpp_test_entry(){O::I i{42};return i.n;}",
        "class O{int n;public:struct I{int f(O*p){return p->n;}};};int cpp_test_entry(){return 42;}",
        "class O{class I{public:explicit I(O*p){p->push();}~I(){}};void push(){}};int cpp_test_entry(){return 42;}",
        "struct O{struct I{int n;};I i;};int cpp_test_entry(){O o{{42}};return o.i.n;}",
    ],
)
def test_valid(source):
    compile_host_cpp((("input.cpp", source),))


@pytest.mark.parametrize(
    "source",
    [
        "class O{class I{};};O::I i;",
        "struct O{class I{int n;};int f(I*p){return p->n;}};",
    ],
)
def test_access_rejected(source):
    with pytest.raises(CppDiagnostic) as e:
        CppParser().parse(source)
    assert e.value.code == "cpp.member-access"


def test_outer_nonstatic_has_no_implicit_object():
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse("class O{int n;public:struct I{int f(){return n;}};};")
    assert error.value.code == "cpp.nonstatic-member-without-object"


def test_owner_paths_are_distinct():
    p = CppParser()
    p.parse("struct A{struct I{int n;};};struct B{struct I{int n;};};")
    a = p.scope.names["A"].dtype.definition.scope.names["I"].dtype
    b = p.scope.names["B"].dtype.definition.scope.names["I"].dtype
    assert (
        a is not b
        and a.qualified_name == ("A", "I")
        and (b.qualified_name == ("B", "I"))
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "source",
    [
        "struct O{struct I{int n;};};int cpp_test_entry(){O::I i{42};return i.n;}",
        "class O{int n;public:explicit O(int x):n(x){}struct I{int f(O*p){return p->n;}};};int cpp_test_entry(){O o{42};O::I i;return i.f(&o);}",
        "class O{int depth;class Tracker{public:explicit Tracker(O*p){this->doc=p;p->push();}~Tracker(){doc->pop();}private:O*doc;};void push(){depth+=1;}void pop(){depth-=1;}public:O():depth(0){}int run(){ {Tracker t(this);if(depth!=1)return 1;}return depth==0?42:2;}};int cpp_test_entry(){O o;return o.run();}",
        "struct O{struct I{int f();};};int O::I::f(){return 42;}int cpp_test_entry(){O::I i;return i.f();}",
        "struct O{struct I{static int n;};};int O::I::n=42;int cpp_test_entry(){return O::I::n;}",
    ],
)
def test_owned_reference_execution(source, tmp_path, monkeypatch):
    from tests.cpp.sizeof_execution import execute_row_pair

    execute_row_pair(tmp_path, monkeypatch, (("input.cpp", source),))


ORIGINAL_NESTED_ENUM = "struct S{struct Inner{enum E{A};};};"


def test_original_nested_enum_identity():
    p = CppParser()
    p.parse(ORIGINAL_NESTED_ENUM)
    outer = p.scope.names["S"].dtype
    inner = outer.definition.scope.names["Inner"].dtype
    enum = inner.definition.scope.names["E"].dtype
    assert enum.qualified_name == ("S", "Inner", "E")
    assert not outer.definition.members and (not inner.definition.members)


@pytest.mark.integration
def test_original_nested_enum_observation(tmp_path, monkeypatch):
    from tests.cpp.sizeof_execution import execute_row_pair

    execute_row_pair(
        tmp_path,
        monkeypatch,
        (
            (
                "input.cpp",
                ORIGINAL_NESTED_ENUM
                + "int cpp_test_entry(){S::Inner::E e=S::Inner::A;return e==S::Inner::A?42:1;}",
            ),
        ),
    )


ORIGINAL_NESTED_EMPTY = "struct S{struct Nested{};};"


def test_original_nested_empty_identity():
    p = CppParser()
    p.parse(ORIGINAL_NESTED_EMPTY)
    outer = p.scope.names["S"].dtype
    nested = outer.definition.scope.names["Nested"].dtype
    assert nested.qualified_name == ("S", "Nested")
    assert not outer.definition.members and (not nested.definition.members)


@pytest.mark.integration
def test_original_nested_empty_observation(tmp_path, monkeypatch):
    from tests.cpp.sizeof_execution import execute_row_pair

    execute_row_pair(
        tmp_path,
        monkeypatch,
        (
            (
                "input.cpp",
                ORIGINAL_NESTED_EMPTY
                + "int cpp_test_entry(){S::Nested n;S s;return sizeof(n)==1&&sizeof(s)==1?42:1;}",
            ),
        ),
    )


@pytest.mark.parametrize(
    "source,code",
    [
        ("struct O{class I;private:class I{};};", "cpp.redeclaration"),
        ("struct O{struct O{};};", "cpp.class-name-member"),
        ("struct O{struct I{};};I* p;", "cpp.unsupported.syntax"),
    ],
)
def test_additional_invalid(source, code):
    with pytest.raises(CppDiagnostic) as e:
        CppParser().parse(source)
    assert e.value.code == code


@pytest.mark.integration
@pytest.mark.parametrize(
    "source",
    [
        "struct O{struct I{struct J{int n;};};};int cpp_test_entry(){O::I::J j{42};return j.n;}",
        "struct O{struct I;struct I{int n;};};int cpp_test_entry(){O::I i{42};return i.n;}",
        "struct A{struct I{int n;};};struct B{struct I{int n;};};int f(A::I*x){return x->n;}int f(B::I*x){return x->n+1;}int cpp_test_entry(){A::I a{17};B::I b{24};return f(&a)+f(&b);}",
    ],
)
def test_nested_identity_execution(source, tmp_path, monkeypatch):
    from tests.cpp.sizeof_execution import execute_row_pair

    execute_row_pair(tmp_path, monkeypatch, (("input.cpp", source),))


@pytest.mark.integration
@pytest.mark.parametrize(
    "source",
    [
        "class O{int n;public:struct I{int f(){return n;}};};",
        "class O{class I{};};O::I i;",
        "struct O{class I{int n;};int f(I*p){return p->n;}};",
        "struct O{class I;private:class I{};};",
        "struct O{struct O{};};",
        "struct O{struct I{};};I* p;",
    ],
)
def test_original_nested_errors_are_rejected_by_reference(source, tmp_path):
    from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference

    result = syntax_reference(tmp_path, "nested-negative", source)
    assert result.returncode == 1, result.stderr
    assert result.stderr
