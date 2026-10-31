"""Original C++17 loop/increment cases, not modified upstream project fragments.

Rules: N4659 [stmt.for], [basic.scope.block], [expr.pre.incr], [expr.post.incr].
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
G++ is a labeled reference; all production lowering is PCC -> shared C AST/IR.
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record


CASES = (
    ("counted-prefix", "int cpp_test_entry(){int n=0;for(int i=0;i<7;++i)n=n+6;return n;}"),
    ("counted-postfix", "int cpp_test_entry(){int n=0;for(int i=0;i<7;i++)n=n+6;return n;}"),
    ("continue-runs-step", "int cpp_test_entry(){int n=0;for(int i=0;i<8;++i){if(i==0)continue;n=n+6;}return n;}"),
    ("break-skips-step", "int cpp_test_entry(){int n=0;int steps=0;for(;;++steps){n=n+6;if(n==42)break;}return n+steps-6;}"),
    ("condition-false-before-body", "int cpp_test_entry(){int n=42;for(n=n+1;false;--n)n=0;return n-1;}"),
    ("empty-header", "int cpp_test_entry(){for(;;){return 42;}}"),
    ("empty-body", "int cpp_test_entry(){int n=0;for(;n<42;++n);return n;}"),
    ("header-shadow-restored", "int cpp_test_entry(){int i=40;for(int i=0;i<2;++i){}return i+2;}"),
    ("nested-block-may-shadow", "int cpp_test_entry(){int n=0;for(int i=0;i<2;++i){{int i=21;n=n+i;}}return n;}"),
    ("nested-for-continue-break", "int cpp_test_entry(){int n=0;for(int i=0;i<3;++i){for(int j=0;j<9;++j){if(j==0)continue;if(j==3)break;n=n+7;}}return n;}"),
    ("comma-header-effects-order", "int cpp_test_entry(){int i=0;int j=0;int n=0;for(i=1,j=i+1;i<7;++i,j=i+1)n=n+j;return n+15;}"),
    ("postfix-old-value", "int cpp_test_entry(){int i=20;int old=i++;return old+i+1;}"),
    ("prefix-new-value", "int cpp_test_entry(){int i=20;int now=++i;return now+i;}"),
    ("prefix-lvalue-assignment", "int cpp_test_entry(){int i=0;(++i)=42;return i;}"),
    ("prefix-lvalue-address", "int cpp_test_entry(){int i=41;int*p=&++i;if(p!=&i)return 1;return *p;}"),
    ("nested-prefix-lvalue", "int cpp_test_entry(){int i=40;return ++(++i);}"),
    ("effectful-prefix-lvalue-once", "int*get(int*p,int*n){*n=*n+1;return p;}int cpp_test_entry(){int i=40;int n=0;++*get(&i,&n);return i+n;}"),
    ("effectful-postfix-lvalue-once", "int*get(int*p,int*n){*n=*n+1;return p;}int cpp_test_entry(){int i=40;int n=0;int old=(*get(&i,&n))++;return old+i+n-40;}"),
    ("unsigned-char-wrap", "int cpp_test_entry(){unsigned char i=255;int old=i++;if(old!=255)return 1;if(i!=0)return 2;--i;if(i!=255)return 3;return 42;}"),
    ("decrement", "int cpp_test_entry(){int n=0;for(int i=7;i>0;--i)n=n+6;return n;}"),
    ("floating-increment", "int cpp_test_entry(){double x=40.0;x++;return int(++x);}"),
    ("pointer-inc-dec", "int cpp_test_entry(){int x=42;int*p=&x;int*q=p++;if(p==q)return 1;--p;if(p!=q)return 2;return *p;}"),
    ("pointer-prefix-lvalue", "int cpp_test_entry(){int x=42;int*p=&x;int*q=++p;if(q!=p)return 1;--p;return *p;}"),
    ("class-pointer-stride", "struct S{int x;long y;};int cpp_test_entry(){S s{};s.x=42;S*p=&s;++p;--p;return p->x;}"),
    ("template-dependent-increment", "template<class T>T sum(T n){T s=0;for(T i=0;i<n;++i)s=s+6;return s;}int cpp_test_entry(){return sum(7);}"),
    ("template-for-init-environment", "template<class T>T sum(T n){T i=40;for(T i=0;i<n;i++){if(i==1)continue;}return i+2;}int cpp_test_entry(){return sum(3);}"),
    ("typedef-init-scope", "int cpp_test_entry(){for(typedef int I;false;){I n=0;}return 42;}"),
    ("data-member-increment", "struct S{int x;};int cpp_test_entry(){S s{};s.x=40;s.x++;return ++s.x;}"),
)

INVALID = (
    ("int f(){for(int i=0;i<2;++i){}return i;}", "cpp.undeclared"),
    ("int f(){for(int i=0;i<2;++i){int i=0;}return 0;}", "cpp.redeclaration"),
    ("int f(){for(int i=0;i<2;++i)int i=0;return 0;}", "cpp.redeclaration"),
    ("int f(){for(int i=0;i<2;++i){using i=int;}return 0;}", "cpp.redeclaration"),
    ("int f(){bool b=false;return ++b;}", "cpp.invalid-operands"),
    ("int f(){bool b=true;return b--;}", "cpp.invalid-operands"),
    ("int f(){const int i=0;return i++;}", "cpp.const-assignment"),
    ("int f(){return ++1;}", "cpp.not-lvalue"),
    ("int f(){int i=0;return (i++)++;}", "cpp.not-lvalue"),
    ("enum E{A};int f(){E e=A;return ++e;}", "cpp.invalid-operands"),
    ("int f(){void*p=nullptr;p++;return 0;}", "cpp.invalid-operands"),
    ("struct S;int f(){S*p=nullptr;++p;return 0;}", "cpp.incomplete-class"),
    ("int f(){for(;;)break;continue;return 0;}", "cpp.jump-outside-loop"),
    ("int f(){for(;;)break;break;return 0;}", "cpp.jump-outside-loop"),
)

UNSUPPORTED = (
    ("int f(){for(int i=0,j=0;i<2;++i){}return 0;}", "cpp.unsupported.for-declaration-list"),
    ("int f(){int r=0;for(int i:r){}return 0;}", "cpp.unsupported.range-for"),
    ("int f(){for(;int i=0;){}return 0;}", "cpp.unsupported.condition-declaration"),
    ("struct S{};int f(){S s{};++s;return 0;}", "cpp.unsupported.class-operators"),
    ("int f(){int i=0;return ++(i=1);}", "cpp.unsupported.syntax"),
)


@pytest.mark.parametrize("name,source", CASES, ids=[row[0] for row in CASES])
def test_for_and_increment_artifacts_are_byte_stable(name, source):
    units = ((name + ".cpp", source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize("source,code", INVALID + UNSUPPORTED)
def test_for_and_increment_diagnose_before_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError("unsupported or invalid loop reached emitter")
    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((("invalid-loop.cpp", source),))
    assert error.value.code == code


def test_public_for_pipeline_is_deterministic_and_preserves_real_loop_blocks():
    from tests.cpp.test_cpp_public_driver import compile_cpp
    source = CASES[2][1]
    first = compile_cpp(source, "for-public.cpp", use_system_cpp=False)
    assert first == compile_cpp(source, "for-public.cpp", use_system_cpp=False)
    assert "__pcc_for_test" in first[0][1]
    assert "__pcc_for_next" in first[0][1]


def test_for_scope_is_restored_after_a_diagnostic():
    parser = CppParser()
    with pytest.raises(CppDiagnostic):
        parser.parse("int f(){for(int i=0;i<2;++i){int i;}}")
    assert parser.scope.parent is None
    assert parser.loop_depth == 0


@pytest.mark.integration
@pytest.mark.parametrize("name,source", CASES, ids=[row[0] for row in CASES])
def test_for_and_increment_execute_against_explicit_cpp17_reference(name, source, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name + ".cpp", source),), 42)
