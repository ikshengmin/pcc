"""Original tests of the non-throwing native array domain.

JLS 10.3-10.7 (array creation, access, initializers), 14.14.2 (enhanced for),
15.10.3-15.10.4 and 15.26.1 (array access and component assignment).
Only operations that cannot complete abruptly are lowered, onto the shared GC0
managed envelope; every other array use keeps an explicit capability code.
Native execution needs the owned runtime archive and the opt-in shared managed
object, which the runtime-free Java executable cannot link: PCC native
execution of these programs is UNRUN. The selected JDK independently checks
that every positive original returns 42 and that every invalid one is
rejected.
"""
import re

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source, compile_sources
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner


def original(body, helpers=""):
    return "class ArrayExample{static int run(){" + body + "}" + helpers + "}"


PROGRAMS = [
    ("initializer-components", original("int[] a={10,20,12};return a[0]+a[1]+a[2];")),
    ("length", original("long[] a={1L,2L,3L,4L};return a.length+38;")),
    ("empty-array", original("short[] a={};return a.length+42;")),
    ("enhanced-for-sum", original("int[] a={7,14,21};int s=0;for(int v:a)s+=v;return s;")),
    ("enhanced-for-final-widening", original("byte[] a={-1,43};long s=0;for(final long v:a)s+=v;return (int)s;")),
    ("enhanced-for-continue-break",
     original("int[] a={5,-1,37,100,9};int s=0;for(int v:a){if(v<0)continue;if(v>50)break;s+=v;}return s;")),
    ("enhanced-for-return", original("int[] a={3,42,7};for(int v:a){if(v>40)return v;}return 0;")),
    ("nested-enhanced-for",
     original("int[] a={1,2};int[] b={10,11};int s=0;for(int x:a)for(int y:b)s+=x*y;return s-21;")),
    ("component-store", original("int[] a={1,2,3};a[1]=40;return a[1]+a[0]+a[2]-2;")),
    ("store-value-is-converted", original("byte[] a={0};int r=(a[0]=42);return r;")),
    ("boolean-components",
     original("boolean[] a={true,false,true};int n=0;for(boolean b:a)if(b)n++;return a[1]?0:40+n;")),
    ("char-zero-extended", original("char[] a={'*'};return a[0];")),
    ("short-sign-extended", original("short[] a={-2};return a[0]+44;")),
    ("narrow-constant-initializers", original("byte[] a={127,-128};return a[0]+a[1]+43;")),
    ("long-components", original("long[] a={1L<<40,42L};return (int)(a[0]>>40)+(int)a[1]-1;")),
    ("initializer-calls", original("int[] a={f(40),f(1),f(1)};return a[0]+a[1]+a[2];",
                                   "static int f(int x){return x;}")),
    ("index-promotion", original("int[] a={41,42};byte i=1;final byte j=1;return a[j];")),
    ("parenthesized-array", original("int[] a={42};return (a)[0]+(a).length-1;")),
    ("array-after-scalar-declarator", original("int x=40,a[]={2};return x+a[0];")),
    ("void-method-array", original("touch();return 42;", "static void touch(){int[] a={1};a[0]=2;}")),
]
# Valid Java with an explicit native capability code (JDK accepts, no run).
GAPS = [
    ("nonconstant-index", original("int[] a={42};int i=0;return a[i];"), "java.unsupported.array-index-exception"),
    ("constant-out-of-range", original("int[] a={42};return a[1];"), "java.unsupported.array-index-exception"),
    ("negative-constant-index", original("int[] a={42};return a[-1];"), "java.unsupported.array-index-exception"),
    ("store-nonconstant-index", original("int[] a={42};int i=0;a[i]=1;return 42;"),
     "java.unsupported.array-index-exception"),
    ("array-creation", original("int[] a=new int[3];return 42;"), "java.unsupported.array-storage"),
    ("blank-array", original("int[] a;return 42;"), "java.unsupported.array-storage"),
    ("alias", original("int[] a={42},b=a;return b[0];"), "java.unsupported.array-storage"),
    ("reassignment", original("int[] a={42};int[] b={1};a=b;return 42;"), "java.unsupported.array-storage"),
    ("reference-equality", original("int[] a={42};return a==a?42:0;"), "java.unsupported.array-storage"),
    ("nested-block-local", original("if(true){int[] a={42};return a[0];}return 0;"),
     "java.unsupported.array-storage"),
    ("loop-local", original("int s=0;for(int i=0;i<2;i++){int[] a={21};s+=a[0];}return s;"),
     "java.unsupported.array-storage"),
    ("compound-component", original("int[] a={41};a[0]+=1;return a[0];"),
     "java.unsupported.assignment-expression-target"),
    ("increment-component", original("int[] a={41};a[0]++;return a[0];"),
     "java.unsupported.assignment-expression-target"),
    ("array-parameter", original("return f(new int[]{42});", "static int f(int[] a){return a[0];}"),
     "java.unsupported.array-storage"),
    ("array-result", original("return 42;", "static int[] f(){int[] a={42};return a;}"),
     "java.unsupported.array-storage"),
    ("static-array-field", "class ArrayExample{static final int[] V={42};static int run(){return V[0];}}",
     "java.unsupported.array-storage"),
    ("float-components", original("float[] a={42f};return 42;"), "java.unsupported.array-model-shape"),
]
# Invalid Java: ordinary errors, never capability codes.
INVALID = [
    ("narrowing-initializer", original("byte[] a={128};return 42;")),
    ("floating-initializer", original("int[] a={1.5};return 42;")),
    ("narrowing-iteration", original("int[] a={42};for(byte b:a){}return 42;")),
    ("length-assignment", original("int[] a={42};a.length=1;return 42;")),
    ("long-index", original("int[] a={42};return a[0L];")),
    ("boolean-store", original("int[] a={42};a[0]=true;return 42;")),
]


def ir_of(text):
    return str(compile_source(text, filename="ArrayExample.java").module)


@pytest.mark.parametrize("name,text", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_non_throwing_originals_lower_onto_the_managed_envelope(name, text, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    for invoke in (lambda: compile_source(text, filename="ArrayExample.java"),
                   lambda: compile_sources({"ArrayExample.java": text}, entry_class="ArrayExample")):
        compilation = invoke()
        assert compilation.managed_runtime
        assert compilation.emit_object().startswith(b"\x7fELF")


@pytest.mark.parametrize("name,text", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_every_method_exit_retires_each_frame_root(name, text):
    ir = ir_of(text)
    for function in re.findall(r"^define [^\n]*\{\n(.*?)^}", ir, re.M | re.S):
        handles = len(re.findall(r"alloca ptr, align \d+|java_array_handle[.\d]* = alloca", function))
        for block in re.split(r"\n(?=\S+:\n)", function):
            if re.search(r"^\s*ret ", block, re.M):
                drops = len(re.findall(r"call void \(ptr\) @pcc_rt_managed_drop", block))
                assert drops == len(re.findall(r"%java_array_handle[.\d]* = alloca", function)), (name, block)
        assert handles >= 0


@pytest.mark.parametrize("component,size", [("boolean", 1), ("byte", 1), ("short", 2), ("char", 2),
                                            ("int", 4), ("long", 8)])
def test_descriptors_are_version_one_scalar_layouts(component, size):
    literal = {"boolean": "true", "char": "'a'", "long": "1L"}.get(component, "1")
    ir = ir_of(original(component + "[] a={" + literal + "};return 42;"))
    assert ("@__pcc_java_array_descriptor_" + component + " = private constant [6 x i64] "
            f"[i64 1, i64 0, i64 {size}, i64 {size}, i64 0, i64 0]") in ir


def test_allocation_precedes_left_to_right_initializers():
    # JLS 10.6: the array exists before any initializer runs.
    ir = ir_of(PROGRAMS[[row[0] for row in PROGRAMS].index("initializer-calls")][1])
    run = re.search(r"^define [^\n]*@pcc_java_[0-9a-f_]*72756e[^\n]*\{\n(.*?)^}", ir, re.M | re.S).group(1)
    alloc = run.index("@pcc_rt_managed_alloc")
    calls = [match.start() for match in re.finditer(r"call i32 \([^)]*\) @pcc_java_", run)]
    assert len(calls) == 3 and alloc < min(calls)


def test_allocation_failure_is_a_labeled_fatal_boundary():
    ir = ir_of(original("int[] a={42};return a[0];"))
    failure = re.search(r"java_array_allocation_failed:\n(.*?)\n\n", ir, re.S).group(1)
    assert "@pcc_debug_fatal_py" in failure and failure.rstrip().endswith("unreachable")
    assert "OutOfMemoryError requires shared Java exception support" in bytes.fromhex(
        "".join(re.findall(r"\\([0-9A-F]{2})", re.search(
            r"@__pcc_java_array_allocation_failure = [^\n]*", ir).group(0)))).decode()


def test_runtime_free_executable_cannot_host_managed_arrays(tmp_path):
    compilation = compile_source(original("int[] a={42};return a[0];"))
    with pytest.raises(UnsupportedJava) as caught:
        compilation.write_executable(tmp_path / "program")
    assert caught.value.code == "java.unsupported.array-storage"
    assert not (tmp_path / "program").exists()


def test_scalar_programs_do_not_select_the_managed_runtime():
    assert not compile_source(original("int x=42;return x;")).managed_runtime


def test_other_targets_do_not_receive_the_gc0_envelope():
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(original("int[] a={42};return a[0];"), target="aarch64-unknown-linux-gnu")
    assert caught.value.code == "java.unsupported.array-storage"
    assert "x86_64-unknown-linux-gnu GC0" in str(caught.value)


@pytest.mark.parametrize("name,text,code", GAPS, ids=[row[0] for row in GAPS])
def test_throwing_or_escaping_array_uses_keep_explicit_codes(name, text, code, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("array gap reached native IR"))
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(text, filename="ArrayExample.java")
    assert caught.value.code == code


@pytest.mark.parametrize("name,text", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_array_originals_are_java_errors(name, text):
    with pytest.raises(JavaError) as caught:
        compile_source(text, filename="ArrayExample.java")
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if __import__("os").environ.get("PCC_JAVA_RAW_REFERENCE") else
                      "select an existing development JDK with PCC_JAVA_RAW_REFERENCE")
@pytest.mark.xdist_group(name="java-array-model-reference")
def test_native_array_originals_against_selected_jdk(tmp_path, request):
    """JDK runs every positive original (42) and accepts the gap originals."""
    from tests.java.test_source_array_model import check_originals_reference
    check_originals_reference(tmp_path, request, PROGRAMS, [row[:2] for row in GAPS],
                              [(*row, None) for row in INVALID])
