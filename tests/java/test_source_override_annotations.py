"""The @Override marker annotation on methods of classes without supertypes.

JLS21 9.6.4.4 (@Override), 9.7.2 (marker annotations), 9.7.4 (applicable
contexts), 8.4.8.1/8.4.8.3 (overriding, return types, access) and 4.3.2 (the
methods of Object):
https://docs.oracle.com/javase/specs/jls/se21/html/jls-9.html#jls-9.6.4.4
https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.4.8
The annotation is a compile-time check only. With extends/implements gated,
Object is the only supertype: an annotated method must match toString,
hashCode, finalize or clone with a compatible result and access, and then
reaches the existing Object inherited-method boundary. equals(Object) needs
the Object type. Other annotations, element values, annotation interfaces
and annotations outside member modifiers stay java.unsupported.annotations.
"""
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources
from pcc.frontends.java.source_syntax import SourceParser
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_unicode_escapes import JDK_REASON


def original(members):
    return "class ArrayExample{" + members + "}"


# javac accepts these; PCC reaches the existing Object inherited-method boundary.
OBJECT_OVERRIDES = [
    ("hash-code", original("@Override public int hashCode(){return 42;}")),
    ("qualified-annotation", original("@java.lang.Override public int hashCode(){return 42;}")),
    ("to-string", original('@Override public String toString(){return "a";}')),
    ("finalize", original("@Override protected void finalize(){}")),
    ("covariant-clone", original("@Override protected ArrayExample clone(){return this;}")),
    ("modifier-order", original("public @Override final int hashCode(){return 42;}")),
]
INVALID = [
    ("misspelled-name", original("@Override public int hashcode(){return 1;}"), "does not override"),
    ("extra-parameter", original("@Override public int hashCode(int x){return x;}"), "does not override"),
    ("static-method", original("@Override public static int hashCode(){return 1;}"), "does not override"),
    ("weaker-access", original("@Override int hashCode(){return 1;}"), "weaker access"),
    ("private-finalize", original("@Override private void finalize(){}"), "weaker access"),
    ("incompatible-result", original("@Override public long hashCode(){return 1;}"), "incompatible return type"),
    ("primitive-clone", original("@Override protected int clone(){return 1;}"), "incompatible return type"),
    ("field", original("@Override int x;"), "not applicable to a field"),
    ("constructor", original("@Override ArrayExample(){}"), "not applicable to a constructor"),
    ("repeated", original("@Override @Override public int hashCode(){return 1;}"), "not a repeatable"),
    ("class", "@Override class ArrayExample{}", "not applicable to a class"),
]
# javac accepts these; PCC keeps an explicit capability boundary.
GAPS = [
    ("other-annotation", original("@Deprecated public int f(){return 1;}"), "annotations"),
    ("element-values", original('@SuppressWarnings("x") public int f(){return 1;}'), "annotations"),
    ("annotation-interface", "@interface ArrayExample{}", "annotations"),
    ("parameter-annotation", original("public int f(@Deprecated int x){return x;}"), "annotations"),
    ("local-annotation", original("public int f(){@Deprecated int x=1;return x;}"), "annotations"),
    ("object-equals", original("@Override public boolean equals(Object o){return false;}"), "source-dependency"),
]


def test_marker_is_recorded_on_the_method_without_changing_its_signature():
    program = SourceParser(original("@Override public int hashCode(){return 42;} public int f(){return 1;}"),
                           "ArrayExample.java", class_model=True).parse()
    annotated, plain = program.methods
    assert (annotated.override.text, annotated.override.line, annotated.override.column) == ("@", 1, 20)
    assert annotated.modifiers == frozenset({"public"}) and annotated.result == "int"
    assert plain.override is None


@pytest.mark.parametrize("name,text", OBJECT_OVERRIDES, ids=[row[0] for row in OBJECT_OVERRIDES])
def test_valid_object_overrides_reach_the_inherited_method_boundary(name, text):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert caught.value.code == "java.unsupported.object-inherited-methods"


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_override_annotations_are_ordinary_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("name,text,code", GAPS, ids=[row[0] for row in GAPS])
def test_unmodeled_annotations_keep_an_explicit_capability_code(name, text, code):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert caught.value.code == "java.unsupported." + code


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-array-model-reference")
def test_same_override_originals_have_independent_jdk_obligations(tmp_path, request):
    check_originals_reference(tmp_path, request, [], OBJECT_OVERRIDES + [(n, t) for n, t, _ in GAPS],
                              [(n, t, m) for n, t, m in INVALID], source_input=Path(__file__))
