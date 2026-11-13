"""Diamond and explicit type arguments in class instance creation.

JLS21 15.9 and 15.9.1:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.9
In the class model `new C<>(...)` and `new C<T, U>(...)` parse, retaining
the diamond or the arguments on the created type; wildcard arguments are a
compile-time error. Every supplied class here is non-generic, so applying
either form to one is an ordinary error, as javac reports; a class that is
not supplied (a JDK collection such as `HashSet`) keeps the
source-dependency boundary. The native static domain keeps its earlier
generic-declarations boundary. Commons Numbers Primes' SmallPrimes static
block creates `new HashSet<>()` (projects/commons-numbers-1.2, Apache-2.0);
the sources below are independently authored originals.
"""
import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source
from pcc.frontends.java.source_syntax import SourceParser
from pcc.frontends.java.source_types import NamedType
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner


def created(body):
    program = SourceParser("class A{void run(){" + body + "}}", "A.java", class_model=True).parse()
    creation = program.methods[0].body.children[0].children[0]
    assert creation.kind == "class_new" and isinstance(creation.value, NamedType)
    return creation.value


def test_diamond_and_arguments_are_retained_on_the_created_type():
    diamond = created("new HashSet<>();")
    assert diamond.diamond and diamond.arguments == () and str(diamond) == "HashSet<>"
    explicit = created("new HashMap<String, int[]>(16);")
    assert not explicit.diamond and str(explicit) == "HashMap<String,int[]>"


# Former gap row of test_source_construction_model ('diamond'), moved verbatim:
# A is non-generic, so `<>` is javac's ordinary error, not a capability gap.
MOVED_DIAMOND = 'class A{void run(){new A<>();}}'


@pytest.mark.parametrize('text,message', [
    (MOVED_DIAMOND, "cannot use '<>' with non-generic class A"),
    ('class A{void run(){new A<String>();}}', 'type A does not take type arguments'),
    ('class A{void run(){new A<?>();}}', 'cannot use wildcard type arguments'),
    ('class A{void run(){new A<? extends A>();}}', 'cannot use wildcard type arguments'),
])
def test_invalid_creation_type_arguments_are_ordinary_errors(text, message):
    with pytest.raises(JavaError, match=message) as caught:
        analyze_class_sources({'A.java': text})
    assert not isinstance(caught.value, UnsupportedJava)


@pytest.mark.parametrize('text', [
    'class A{void run(){new HashSet<>();}}',
    'class A{void run(){new HashMap<String, Integer>();}}',
])
def test_unsupplied_generic_classes_keep_the_source_dependency_boundary(text):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({'A.java': text})
    assert caught.value.code == 'java.unsupported.source-dependency'


def test_native_domain_keeps_the_generic_boundary(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    with pytest.raises(UnsupportedJava) as caught:
        compile_source('class A{static int run(){new A<>();return 1;}}', filename='A.java')
    assert caught.value.code == 'java.unsupported.generic-declarations'
