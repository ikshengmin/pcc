"""Class headers: superclass and superinterface clauses in the class model.

JLS21 8.1.2, 8.1.4, 8.1.5:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.1.4
The class model parses `extends ClassType` and `implements InterfaceType, ...`
(type arguments retained) and resolves them in the supplied closed world.
`extends Object` / `extends java.lang.Object` names the implicit superclass.
A cyclic superclass chain, a final or String superclass, type arguments on
Object and a class named by `implements` are errors; the header grammar is
checked. An unsupplied supertype is a source dependency, a supplied
superclass still needs inherited members, constructor chaining and dispatch,
and generic class declarations stay explicit. The native path keeps its
earlier boundary. OpenJDK's `public class BigInteger extends Number
implements Comparable<BigInteger>` (projects/openjdk-jdk8u512-ga,
GPL-2.0 with the Classpath exception, BigInteger.java:125) has this shape;
the sources below are independently authored originals.
"""
import os
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source
from pcc.frontends.java.source_syntax import SourceParser
from tests.java.test_source_array_model import check_originals_reference


def original(header, members=""):
    return "class ArrayExample" + header + "{" + members + "static int run(){return 42;}}"


PROGRAMS = [
    ("extends-object", original(" extends Object")),
    ("extends-qualified-object", original(" extends java.lang.Object", "int value;")),
    ("extends-object-with-instance-members", original(" extends Object", "int v;ArrayExample(){v=1;}int get(){return v;}")),
]

INVALID = [
    ("object-type-arguments", original(" extends Object<ArrayExample>"), "type Object does not take type arguments"),
    ("self-cycle", original(" extends ArrayExample"), "cyclic inheritance involving ArrayExample"),
    ("two-class-cycle", original(" extends Other") + "class Other extends ArrayExample{}",
     "cyclic inheritance involving ArrayExample"),
    ("final-superclass", original(" extends Other") + "final class Other{}", "cannot inherit from final Other"),
    ("string-superclass", original(" extends String"), "cannot inherit from final String"),
    ("implements-a-class", original(" implements Other") + "class Other{}", "interface expected here"),
    ("implements-string", original(" implements String"), "interface expected here"),
    ("implements-object", original(" implements Object"), "interface expected here"),
    ("primitive-superclass", original(" extends int"), "class type expected, found int"),
    ("two-superclasses", original(" extends Object, Other") + "class Other{}", "'\\{' expected"),
    ("empty-implements", original(" implements"), "interface type expected, found \\{"),
]

GAPS = [
    (original(" extends Other") + "class Other{}", "class-supertypes-generics",
     "inheritance from a supplied class requires inherited members, constructor chaining and dispatch"),
    (original(" extends Number"), "source-dependency", "requires supplied class source for Number"),
    (original(" implements Comparable<ArrayExample>"), "source-dependency",
     "requires supplied class source for Comparable<ArrayExample>"),
    (original("<T>"), "class-supertypes-generics", "generic class declarations require type-parameter scope and erasure"),
    (original(" permits Other") + "class Other{}", "class-supertypes-generics", "inheritance, generics"),
]


def model(text):
    return analyze_class_sources({"ArrayExample.java": text}).models["ArrayExample"]


def test_header_retains_superclass_and_interfaces_with_type_arguments():
    program, = SourceParser("class Big extends Number implements Comparable<Big>, Other{}", "Big.java",
                            class_model=True).parse_unit()
    assert (str(program.superclass), [str(item) for item in program.interfaces]) == (
        "Number", ["Comparable<Big>", "Other"])
    assert (program.superclass.tokens[0].line, program.superclass.tokens[0].column) == (1, 19)
    plain, = SourceParser("class Plain{}", "Plain.java", class_model=True).parse_unit()
    assert (plain.superclass, plain.interfaces) == (None, ())


@pytest.mark.parametrize("name,text", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_object_superclass_is_the_implicit_superclass(name, text):
    result = model(text)
    assert result.superclass == "java/lang/Object" and result.runtime_registered is False


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_class_headers_fail_as_java_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        model(text)
    assert not isinstance(caught.value, UnsupportedJava)


@pytest.mark.parametrize("text,suffix,message", GAPS)
def test_remaining_supertype_domains_stay_explicit(text, suffix, message):
    with pytest.raises(UnsupportedJava, match=message) as caught:
        model(text)
    assert caught.value.code == "java.unsupported." + suffix


def test_native_domain_keeps_its_header_boundary():
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(PROGRAMS[0][1], filename="ArrayExample.java")
    assert caught.value.code == "java.unsupported.class-supertypes-generics"


# Former gap row of test_source_class_model ("inheritance"), moved verbatim:
# the unsupplied superclass is now resolved as a source dependency.
MOVED_EXTENDS_ROW = "class Record extends Other{}"


def test_former_extends_row_is_a_source_dependency():
    with pytest.raises(UnsupportedJava, match="requires supplied class source for Other") as caught:
        analyze_class_sources({"Record.java": MOVED_EXTENDS_ROW})
    assert caught.value.code == "java.unsupported.source-dependency"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
                     "select an existing development JDK with PCC_JAVA_RAW_REFERENCE")
@pytest.mark.xdist_group(name="java-class-supertypes-reference")
def test_class_headers_against_selected_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, PROGRAMS, [], INVALID, source_input=Path(__file__))
