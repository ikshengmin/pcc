"""Single-level builtin pointer Sema; no native pcc1 qualification claim."""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ConstType, PointerType, mangle_function


def test_pointer_type_const_identity_and_real_lvalue_lowering():
    parser = CppParser("x86_64-unknown-linux-gnu")
    ast = parser.parse(
        "const int *view(int *p){return p;} "
        "int run(){int x=4;int *p=&x;*p=7;return *view(p);}", "pointer.cpp")
    function = parser.scope.lookup("view").overloads[0]
    assert function.dtype == PointerType(ConstType("int"))
    assert function.parameters == (PointerType("int"),)
    assert function.c_name == "_Z4viewPi"
    assert isinstance(ast.ext[0].decl.type.type, c_ast.PtrDecl)
    body = ast.ext[1].body.block_items
    assert body[1].init.op == "&"
    assert isinstance(body[2], c_ast.StmtExpr)
    assert body[2].stmt.block_items[1].lvalue.op == "*"
    assert body[3].expr.expr.op == "*"  # explicit top-level const removal


def test_parameter_top_const_is_not_an_overload_but_pointee_const_is():
    parser = CppParser()
    parser.parse("int f(int *const); int f(int *p){return *p;} int f(const int *);"
                 "int g(const int); int g(int x){return x;}")
    assert len(parser.scope.lookup("f").overloads) == 2
    assert len(parser.scope.lookup("g").overloads) == 1


def test_itanium_pointer_substitutions_preserve_qualified_pointee_identity():
    p, cp = PointerType("int"), PointerType(ConstType("int"))
    assert mangle_function("f", (p, p)) == "_Z1fPiS_"
    assert mangle_function("f", (cp, cp)) == "_Z1fPKiS0_"
    assert mangle_function("f", (p, cp, p, cp)) == "_Z1fPiPKiS_S1_"


NEGATIVE_CASES = (
    ("int f(){const int x=1;x=2;return x;}", "cpp.const-assignment"),
    ("int f(){int x=1;const int *p=&x;*p=2;return x;}", "cpp.const-assignment"),
    ("int f(){int x=1;int *const p=&x;p=nullptr;return x;}", "cpp.const-assignment"),
    ("int f(int *const p){p=nullptr;return 0;}", "cpp.const-assignment"),
    ("int f(){const int x;return 0;}", "cpp.const-initializer"),
    ("int f(){int *const p;return 0;}", "cpp.const-initializer"),
    ("int f(){const int x=1;int *p=&x;return 0;}", "cpp.invalid-conversion"),
    ("int *f(const int *p){return p;}", "cpp.invalid-conversion"),
    ("int f(){int x=1;void *p=&x;int *q=p;return 0;}", "cpp.invalid-conversion"),
    ("int f(){int x=1;long *p=&x;return 0;}", "cpp.invalid-conversion"),
    ("int f(){int *p=1;return 0;}", "cpp.invalid-conversion"),
    ("int f(){int zero=0;int *p=zero;return 0;}", "cpp.invalid-conversion"),
    ("int f(){int *p=int(0);return 0;}", "cpp.invalid-conversion"),
    ("int f(){int *p=+0;return 0;}", "cpp.invalid-conversion"),
    ("int f(){int *p=false;return 0;}", "cpp.invalid-conversion"),
    ("int f(){bool b=nullptr;return b;}", "cpp.invalid-conversion"),
    ("int tag(bool);int f(){return tag(nullptr);}", "cpp.no-viable-overload"),
    ("int f(){return *1;}", "cpp.invalid-dereference"),
    ("int f(){void *p=nullptr;*p;return 0;}", "cpp.invalid-dereference"),
    ("int f(){int *p=&1;return 0;}", "cpp.not-lvalue"),
    ("int f(){int x=1;int *p=&x;*p+=2;return x;}", "cpp.unsupported.compound-assignment"),
    ("int f(){int x=1;int *p=&x;(*p=2)=3;return x;}", "cpp.unsupported.syntax"),
    ("int f(){int x=1;int *p=&x;int *q=&(*p=2);return x;}", "cpp.unsupported.syntax"),
    ("int f(){int x=1;int *p=&x;return p;}", "cpp.invalid-conversion"),
    ("int f(){int x=1;int *p=&x;return p==1;}", "cpp.invalid-operands"),
    ("int f(){int x=1;long y=2;return &x==&y;}", "cpp.invalid-operands"),
    ("int f(){int x=1;int *p=&x;p+1;return 0;}", "cpp.unsupported.pointer-operators"),
    ("int f(){int x=1;int *p=&x;return p<p;}", "cpp.unsupported.pointer-operators"),
    ("int f(){int **p;return 0;}", "cpp.unsupported.multilevel-pointers"),
    ("int f(){int *p=nullptr;&p;return 0;}", "cpp.unsupported.multilevel-pointers"),
    ("int f(){volatile int x=1;return x;}", "cpp.unsupported.volatile"),
    ("int f(int *volatile p);", "cpp.unsupported.volatile"),
    ("int f(int &p);", "cpp.unsupported.references"),
    ("int (*fp)();", "cpp.unsupported.function-pointers"),
    ("int f(){int a[2];return 0;}", "cpp.unsupported.arrays-or-lambdas"),
    ("int f(){int *p=new int;return 0;}", "cpp.unsupported.allocation"),
    ("template<class T>T *f(T *p){return p;}", "cpp.unsupported.pointer-templates"),
    ("template<class T>T f(T p){return p;}int g(){int x=1;f(&x);return 0;}",
     "cpp.unsupported.pointer-templates"),
    ("int f(int *);int f(const int *);int g(){return f(nullptr);}", "cpp.ambiguous-overload"),
    ("int f(int *);int f(void *);int g(){return f(0);}", "cpp.ambiguous-overload"),
    ("int f(int *,long);int f(const int *,int);"
     "int g(){int x=1;return f(&x,1);}", "cpp.ambiguous-overload"),
)


@pytest.mark.parametrize("source,code", NEGATIVE_CASES)
def test_pointer_invalid_and_unimplemented_semantics_fail_closed(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, "pointer-negative.cpp")
    assert error.value.code == code
    assert error.value.coord.file == "pointer-negative.cpp"
