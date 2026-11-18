"""Closed-supplied-class and primitive-array references, not a JDK object model.

JLS 14.17, 15.7.4, 15.12.4.2, 15.21.3, 15.25: return values,
argument/operand order, object identity and conditional selection.
"""
import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source, compile_sources
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner


# Compiled with unchanged bytes by PCC and the selected JDK. Both fresh arrays
# and independently cloned handles for the SAME object occur on the normal path.
ARRAY = """class References {
 static int[] make(int n) { int[] values={n,42}; return values; }
 static int[] forward(int[] borrowed) { return borrowed; }
 static int[] choose(boolean first, int[] a, int[] b) { return first ? a : b; }
 static int[] relay(int n) { return forward(make(n)); }
 static boolean equal(int[] a, int[] b) { return a == b; }
 static int run() {
   int[] a={0,42}, alias=a;
   a[0]=42;
   int[] b=relay(0);
   int i=0;
   for (; i<8; i++) { forward(a); }
   if (alias[0]!=42 || !equal(a,forward(a)) || equal(a,b) || b==null) return 1;
   if (choose(false,make(1),b)!=b || choose(true,make(1),b)==b) return 2;
   if (choose(true,null,a)!=null || !equal(null,forward(null))) return 3;
   int order=0;
   boolean leftFirst=equal(choose(order++==0,a,b),choose(order++==1,a,b));
   if (!leftFirst || order!=2) return 4;
   return 42;
 }
}"""
NOMINAL = """package observer;
class Token {}
class RefOps {
 static Token choose(boolean first, Token a, Token b) { return first?a:b; }
 static Token forward(Token t) { final Token alias=t; return alias; }
 static boolean same(Token a, Token b) { return a==b; }
 static int run(Token a, Token b) {
   if (a==null || b==null || a==b || forward(a)!=a || forward(null)!=null) return 1;
   if (!same(choose(true,forward(a),b),a) || same(choose(false,a,forward(b)),a)) return 2;
   for (int i=0;i<7;i++) { forward(a); }
   return 42;
 }
}"""
TAG="(Lobserver/Token;Lobserver/Token;)I"
DECLARED="class Declared{static int run(){int[] a={42},b=a;return b[0];}}"
# These exact programs are also checked by the independent JDK. GenericBox is
# valid but not executable by the native subset; non-generic A<A> is invalid.
NONGENERIC="class A{static A<A> f(A x){return x;}static int run(){return 42;}}"
GENERIC="class GenericBox<T>{} class GenericUse{static GenericBox<GenericUse> f(GenericBox<GenericUse> x){return x;}}"
STATIC_QUALIFIED="class A{static int f(A x){return x.run();}static int run(){return 42;}}"



def test_reference_calls_emit_real_root_operations_and_ordinary_owned_objects(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    for compilation in (compile_source(ARRAY), compile_sources({"RefOps.java":NOMINAL},
            entry_class="observer.RefOps", descriptor=TAG)):
        text=str(compilation.module)
        assert compilation.managed_runtime
        for symbol in ("pcc_rt_managed_clone","pcc_rt_managed_drop","pcc_rt_managed_identity_equal"):
            assert symbol in text
        assert "pcc_debug_fatal_py" in text and "java_root_clone_failed" in text
        assert compilation.emit_object().startswith(b"\x7fELF")


@pytest.mark.parametrize("fragment,code", [
    ("static Unknown f(Unknown x){return x;}", "source-dependency"),
    ("static Missing<A> f(Missing<A> x){return x;}", "source-dependency"),
    ("static A f(){return new A();}", "object-expressions"),
    ("static int f(int[] x){return x.length;}", "array-storage"),
    ("static int f(int[] x){return x[0];}", "array-storage"),
    ("static int f(int[] x){for(int i:x){}return 0;}", "array-storage"),
    ("static int f(A x){A other=null;x=other;return 0;}", "array-storage"),
    ("static int f(A x){{A other=x;}return 0;}", "array-storage"),
    ("static int f(A x){return x.run();}", "instance-methods"),
    ("static int f(A x){A other;return 0;}", "array-storage"),
])
def test_unsupported_reference_lifetimes_and_class_shapes_fail_before_lowering(fragment,code,monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer,"lower",lambda *_:pytest.fail("unowned reference reached native IR"))
    with pytest.raises(UnsupportedJava) as caught:
        compile_source("class A{"+fragment+"static int run(){return 42;}}")
    assert caught.value.code == "java.unsupported."+code


@pytest.mark.parametrize("fragment", [
    "static A f(B x){return x;}", "static boolean f(A x,B y){return x==y;}",
    "static int[] f(A x){return x;}", "static A f(int[] x){return x;}",
    "static A f(A x){A a=a;return x;}", "static int f(A x){return x;}",
    "static int f(){A a=1;return 0;}", "static boolean f(A x){return x==0;}",
    "static int f(A x){return 1;}static int f(A y){return 2;}",
])
def test_invalid_reference_programs_are_source_errors(fragment):
    with pytest.raises(JavaError) as caught:
        compile_source("class A{"+fragment+"static int run(){return 42;}}class B{}")
    assert type(caught.value) is JavaError


@pytest.mark.parametrize("target",["aarch64-unknown-linux-gnu","aarch64-apple-darwin"])
def test_managed_signature_itself_requires_the_owned_gc0_target(target):
    with pytest.raises(UnsupportedJava) as caught:
        compile_source("class A{static int f(A unused){return 42;}static int run(){return 42;}}",target=target)
    assert caught.value.code=="java.unsupported.array-storage"


def test_hidden_missing_or_inaccessible_nominal_owners_never_become_the_same_pointer_type():
    sources={"App.java":"package app;import supplied.Token;class App{static Token f(Token x){return x;}}",
             "Token.java":"package supplied;class Token{}"}
    with pytest.raises(JavaError,match="accessible"):
        compile_sources(sources,entry_class="app.App",entry="f",descriptor="(Lsupplied/Token;)Lsupplied/Token;")
    sources["Token.java"]="package supplied;public class Token{}"
    result=compile_sources(sources,entry_class="app.App",entry="f",descriptor="(Lsupplied/Token;)Lsupplied/Token;")
    assert result.managed_runtime
    del sources["Token.java"]
    with pytest.raises(UnsupportedJava,match="supplied.Token"):
        compile_sources(sources,entry_class="app.App",entry="f",descriptor="(Lsupplied/Token;)Lsupplied/Token;")


def test_actual_nongeneric_syntax_is_a_java_error_not_a_runtime_gap(monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer,"lower",lambda *_:pytest.fail("invalid generic application reached native IR"))
    for text in (NONGENERIC,"class A{static int f(A<A> x){return 42;}static int run(){return 42;}}",
                 "class A{static int run(){A<A> x=null;return 42;}}"):
        with pytest.raises(JavaError,match="non-generic supplied class A cannot take type arguments") as caught:
            compile_source(text,filename="A.java")
        assert type(caught.value) is JavaError and caught.value.code is None
        assert str(caught.value).startswith(f"A.java:1:{text.index('A<A>')+1}:")


def test_real_generic_declaration_and_expression_selected_static_call_remain_closed(monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer,"lower",lambda *_:pytest.fail("unsupported member resolution reached IR"))
    with pytest.raises(UnsupportedJava) as actual_generic:
        compile_source(GENERIC,filename="GenericBox.java")
    assert actual_generic.value.code=="java.unsupported.class-supertypes-generics"
    with pytest.raises(UnsupportedJava,match="may select a static or instance member") as static:
        compile_source(STATIC_QUALIFIED,filename="A.java")
    assert static.value.code=="java.unsupported.instance-methods"
