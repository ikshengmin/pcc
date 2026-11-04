"""Supplied constructor source bindings, not owned allocation or execution.

JLS21 6.5.5,8.8.9,12.4.1,15.9.1/3/4:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.9.3
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.9.4
All new source/native-boundary/JDK obligations are authored for CI, UNRUN.
The JDK alone allocates objects; it receives the exact complete originals.
"""
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source, compile_sources
from pcc.frontends.java.source_construction_model import (
    CLASS_CREATION_REQUIREMENTS, CLASS_CREATION_STEPS, ClassConstructionObligation,
)
from pcc.frontends.java.source_syntax import SourceParser
from pcc.frontends.java.source_types import ClassType, NamedType
from scripts.ci_java_primes_project import PRODUCTION, load_inputs, original_sources
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_raw_reference import JVM_LIMITS, record_process, tool_identity
from tests.java.test_source_unicode_escapes import JDK_REASON


def original(members, body):
    return 'class ArrayExample{' + members + 'int run(){' + body + '}}'


PROGRAMS = [
    ('implicit-default', original('', 'ArrayExample value=new ArrayExample();return value==null?0:42;')),
    ('own-private', original('ArrayExample(){}private ArrayExample(int n){}', 'new ArrayExample(42);return 42;')),
    ('strict-overload', original('ArrayExample(){}ArrayExample(int n){}ArrayExample(long n){}', 'new ArrayExample(1);return 42;')),
    ('widening', original('ArrayExample(){}ArrayExample(long n){}', 'new ArrayExample((byte)1);return 42;')),
    ('explicit-narrow', original('ArrayExample(){}ArrayExample(byte n){}', 'new ArrayExample((byte)1);return 42;')),
    ('null-reference', original('ArrayExample(){}ArrayExample(ArrayExample value){}', 'new ArrayExample(null);return 42;')),
    ('null-string-contract', original('ArrayExample(){}ArrayExample(String value){}', 'new ArrayExample(null);return 42;')),
    ('literal-string-contract', original('ArrayExample(){}ArrayExample(String value){}', 'new ArrayExample("x");return 42;')),
    ('nested-creation', original('ArrayExample(){}ArrayExample(ArrayExample value){}', 'new ArrayExample(new ArrayExample());return 42;')),
    ('value-name-is-not-type-name', original('', 'int ArrayExample=7;new ArrayExample();return ArrayExample+35;')),
    ('final-is-not-a-constant-variable', original('', 'final ArrayExample value=new ArrayExample();return value==null?0:42;')),
    ('argument-order', original('int order;ArrayExample(){}ArrayExample(int a,int b){}int next(int n){order=order*10+n;return n;}', 'new ArrayExample(next(1),next(2));return order==12?42:0;')),
    ('array-argument', original('ArrayExample(){}ArrayExample(int[] values){}', 'new ArrayExample(new int[]{1,2});return 42;')),
    ('conditional-reference', original('', 'ArrayExample value=true?new ArrayExample():null;return value==null?0:42;')),
]
VALID_ONLY = [
    # Construction recursion is runtime behavior, not this-delegation recursion.
    ('recursive-field-construction', original('ArrayExample value=new ArrayExample(1);ArrayExample(){}ArrayExample(int n){}', 'return 42;')),
    ('new-in-delegation-arguments', original('ArrayExample(){this(new ArrayExample(1));}ArrayExample(int n){}ArrayExample(ArrayExample value){}', 'return 42;')),
    ('constructor-local', original('ArrayExample(){ArrayExample value=new ArrayExample(1);}ArrayExample(int n){}', 'return 42;')),
]
INVALID = [
    ('no-default-with-explicit-constructor', original('ArrayExample(int n){}', 'new ArrayExample();return 42;'), 'no applicable supplied-source constructor', 'constructor ArrayExample'),
    ('no-invocation-narrowing', original('ArrayExample(){}ArrayExample(byte n){}', 'new ArrayExample(1);return 42;'), 'no applicable supplied-source constructor', 'possible lossy conversion'),
    ('wrong-primitive', original('ArrayExample(){}ArrayExample(int n){}', 'new ArrayExample(true);return 42;'), 'no applicable supplied-source constructor', 'incompatible types|constructor ArrayExample'),
    ('void-argument', original('ArrayExample(){}ArrayExample(int n){}void nothing(){}', 'new ArrayExample(nothing());return 42;'), 'void value cannot be a constructor argument', 'void.*not allowed'),
    ('ambiguous-null', original('ArrayExample(){}ArrayExample(ArrayExample value){}ArrayExample(String value){}', 'new ArrayExample(null);return 42;'), 'ambiguous source-reference', 'reference to ArrayExample is ambiguous'),
    ('unassigned-argument', original('ArrayExample(){}ArrayExample(int n){}', 'int n;new ArrayExample(n);return 42;'), 'not definitely assigned', 'might not have been initialized'),
    ('invalid-reference-arithmetic', original('', 'return new ArrayExample()+1;'), 'integer value required', 'bad operand types'),
    ('group-is-not-statement-expression', original('', '(new ArrayExample());return 42;'), 'only assignment, increment or method invocation', 'not a statement'),
    ('receiver-before-super', original('ArrayExample(){this(new ArrayExample(this));}ArrayExample(ArrayExample value){}', 'return 42;'), 'before constructor invocation', 'before supertype constructor'),
    ('creation-is-not-constant-flow', original('', 'int n;if(new ArrayExample()==null){n=42;}return n;'), 'not definitely assigned', 'might not have been initialized'),
]


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def creation_nodes(project):
    for program in project.programs:
        for field in program.fields:
            if field.initializer is not None:
                yield from (node for node in walk(field.initializer) if node.kind == 'class_new')
        for method in (*program.methods, *program.constructors):
            yield from (node for node in walk(method.body) if node.kind == 'class_new')
        for initializer in program.instance_initializers:
            yield from (node for node in walk(initializer) if node.kind == 'class_new')


@pytest.mark.parametrize('name,text', PROGRAMS + VALID_ONLY)
def test_exact_creation_originals_bind_real_constructors_without_runtime_values(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, 'lower', lambda *args: pytest.fail('construction model invoked native lowering'))
    inputs = {'ArrayExample.java': text}
    project = analyze_class_sources(inputs)
    nodes = list(creation_nodes(project))
    assert nodes and inputs == {'ArrayExample.java': text}
    obligations = [item for item in project.compiler_context_obligations if isinstance(item, ClassConstructionObligation)]
    assert len(obligations) == len(nodes)
    assert not project.class_creation_context_complete and not project.semantic_context_complete
    for node in nodes:
        assert isinstance(node.value, NamedType) and node.dtype == ClassType('ArrayExample')
        assert any(node.symbol is item for item in project.models['ArrayExample'].constructors)
        assert node.symbol.owner == 'ArrayExample' and node.symbol.constructor_target is not None
        assert node.constant is node.string_constant is None
        assert node.runtime_steps == CLASS_CREATION_STEPS
        assert not hasattr(node, 'address') and not hasattr(node, 'object')
        obligation, = [item for item in obligations if node.token in item.tokens]
        assert obligation.owner == obligation.type_name == 'ArrayExample'
        assert obligation.constructor_descriptor == node.symbol.descriptor
        assert obligation.requirements == CLASS_CREATION_REQUIREMENTS
        with pytest.raises(FrozenInstanceError):
            obligation.type_name = 'invented'
    assert not project.models['ArrayExample'].runtime_registered


@pytest.mark.parametrize('shape', ['method', 'field'])
def test_empty_target_default_constructor_identity_survives_later_owner_analysis(shape):
    sources = {'A.java': 'class A{' + ('B make(){return new B();}' if shape == 'method' else 'B value=new B();') + '}',
               'B.java': 'class B{int value=42;}'}
    project = analyze_class_sources(sources)
    node, = creation_nodes(project)
    actual, = project.models['B'].constructors
    assert actual.implicit and node.symbol is actual
    assert actual is project.analyzers['B'].constructors['B', ()]
    assert actual.constructor_target == 'java/lang/Object::<init>()V'
    assert actual.initialization_steps == project.classes['B'].initialization_steps
    assert actual.initialization_steps


def test_widening_overload_array_and_string_contracts_use_selected_signature_only():
    expected = {'strict-overload': '(I)V', 'widening': '(J)V', 'explicit-narrow': '(B)V',
                'null-reference': '(LArrayExample;)V', 'array-argument': '([I)V',
                'null-string-contract': '(Ljava/lang/String;)V'}
    for name, descriptor in expected.items():
        project = analyze_class_sources({'ArrayExample.java': dict(PROGRAMS)[name]})
        assert next(creation_nodes(project)).symbol.descriptor == descriptor
    sources = {'A.java': 'class A{B make(){return new B(null);}}',
               'B.java': 'class B{B(String value){}}'}
    project = analyze_class_sources(sources)
    string = next(item for item in project.models['A'].compiler_context_obligations
                  if not isinstance(item, ClassConstructionObligation))
    assert set(string.requirements) == {'owned-bootstrap-type-definition', 'shared-owning-root-and-safepoint-ABI'}
    assert 'managed-literal-materialization' not in string.requirements
    control = analyze_class_sources({'A.java': 'class A{B make(){return new B(1);}}',
                                    'B.java': 'class B{B(int value){}B(String value){}}'})
    assert all(isinstance(item, ClassConstructionObligation) for item in control.models['A'].compiler_context_obligations)


def test_runtime_order_retains_allocation_failure_before_arguments_and_original_ast_identity():
    project = analyze_class_sources({'ArrayExample.java': dict(PROGRAMS)['argument-order']})
    node, = creation_nodes(project)
    assert [arg.children[0].constant for arg in node.children] == [1, 2]
    assert all(arg.kind == 'receiver_call' and arg.symbol.token.text == 'next' for arg in node.children)
    assert node.runtime_steps.index('allocate-and-default-initialize-or-throw-OOME') < node.runtime_steps.index('arguments-left-to-right')
    assert node.runtime_steps.index('arguments-left-to-right') < node.runtime_steps.index('invoke-selected-constructor')
    # No argument is executed, copied to a fake object, or evaluated into metadata.
    assert all(arg.constant is None for arg in node.children)


@pytest.mark.parametrize('name,text,message,reference_message', INVALID)
def test_exact_invalid_creation_originals_remain_ordinary_java_errors(name, text, message, reference_message):
    with pytest.raises(JavaError, match=message) as caught:
        analyze_class_sources({'ArrayExample.java': text})
    assert type(caught.value) is JavaError and caught.value.code is None


GAPS = [
    ('missing-owned-class', 'class A{void run(){new Missing();}}', 'source-dependency'),
    ('bootstrap-string', 'class A{void run(){new String();}}', 'string-literals'),
    ('qualified-type', 'class A{void run(){new q.B();}}', 'implicit-type-metadata'),
    ('diamond', 'class A{void run(){new A<>();}}', 'generic-declarations'),
    ('constructor-type-arguments', 'class A{void run(){new <String>A();}}', 'object-expressions'),
    ('anonymous', 'class A{void run(){new A(){};}}', 'member-types'),
    ('reference-array', 'class A{void run(){new A[1];}}', 'array-model-shape'),
    ('created-member', 'class A{int value;int run(){return new A().value;}}', 'object-expressions'),
    ('grouped-created-length', 'class A{int length;int run(){return (new A()).length;}}', 'object-expressions'),
    ('conditional-created-length', 'class A{int length;int run(){return (true?new A():null).length;}}', 'object-expressions'),
    ('static-created-reference', 'class A{static final A VALUE=new A();}', 'static-initialization'),
    ('throw-creation', 'class A{void run(){throw new IllegalArgumentException();}}', 'source-dependency'),
]


@pytest.mark.parametrize('name,text,code', GAPS)
def test_wider_creation_and_execution_contracts_stay_explicit(name, text, code):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({'A.java': text})
    assert caught.value.code == 'java.unsupported.' + code


@pytest.mark.parametrize('text', [
    'class A{static int run(){new A();return 42;}}',
    'class A{static int run(){return new A()==null?0:42;}}',
])
def test_even_static_scalar_entry_cannot_advance_creation_into_native_lowering(text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, '__init__', lambda *args: pytest.fail('construction reached native lowerer'))
    for invoke in (lambda: compile_source(text, filename='A.java'),
                   lambda: compile_sources({'A.java': text}, entry_class='A')):
        with pytest.raises(UnsupportedJava) as caught:
            invoke()
        assert caught.value.code == 'java.unsupported.object-expressions'


# Full unchanged source graph cases; no substituted bootstrap/library classes.
PROJECT_CASES = [
    ('imported-default', {'A.java': 'package app;import provider.B;class A{B make(){return new B();}}',
                          'B.java': 'package provider;public class B{}'}, None),
    ('accessible-widening', {'A.java': 'package app;import provider.B;class A{B make(){return new B(1);}}',
                             'B.java': 'package provider;public class B{private B(int n){}public B(long n){}}'}, None),
    ('same-package-protected', {'A.java': 'package app;class A{B make(){return new B();}}',
                                'B.java': 'package app;public class B{protected B(){}}'}, None),
    ('imported-string-name', {'A.java': 'package app;import provider.String;class A{String make(){return new String();}}',
                              'String.java': 'package provider;public class String{}'}, None),
    ('private-constructor', {'A.java': 'package app;import provider.B;class A{B make(){return new B();}}',
                             'B.java': 'package provider;public class B{private B(){}}'}, 'not accessible'),
    ('protected-cross-package', {'A.java': 'package app;import provider.B;class A{B make(){return new B();}}',
                                 'B.java': 'package provider;public class B{protected B(){}}'}, 'not accessible'),
    ('inaccessible-class', {'A.java': 'package app;import provider.B;class A{B make(){return new B();}}',
                            'B.java': 'package provider;class B{}'}, 'not accessible'),
    ('different-nominal-argument', {'A.java': 'class A{B make(){return new B(this);}}',
                                   'B.java': 'class B{B(B value){}}'}, 'no applicable supplied-source constructor'),
]


@pytest.mark.parametrize('name,sources,message', PROJECT_CASES)
def test_exact_supplied_project_lookup_and_constructor_access(name, sources, message):
    original_sources = dict(sources)
    if message is not None:
        with pytest.raises(JavaError, match=message) as caught:
            analyze_class_sources(sources)
        assert type(caught.value) is JavaError and caught.value.code is None
    else:
        project = analyze_class_sources(sources)
        node, = creation_nodes(project)
        assert any(node.symbol is item for item in project.models[node.dtype.class_name].constructors)
        assert node.symbol.owner == node.dtype.class_name
        assert not project.class_creation_context_complete
        if name == 'accessible-widening':
            assert node.symbol.descriptor == '(J)V'
        if name == 'imported-string-name':
            assert node.dtype == ClassType('provider/String') and project.string_context_complete
    assert sources == original_sources


def test_complete_primes_original_retains_throw_before_generic_type_boundary():
    _, files = load_inputs()
    sources = original_sources(files, PRODUCTION)
    before = dict(sources)
    first = PRODUCTION[0]
    assert sources[first].splitlines()[69].strip() == 'throw new IllegalArgumentException(String.format(NUMBER_TOO_SMALL, n, 0));'
    parsed_loops, parsed_throws = [], []

    class OriginalParser(SourceParser):
        def statement(self, in_block=False):
            node = super().statement(in_block=in_block)
            if node.kind == 'enhanced_for':
                parsed_loops.append(node)
            if node.kind == 'throw':
                parsed_throws.append(node)
            return node

    parser = OriginalParser(sources[first], first, class_model=True)
    with pytest.raises(UnsupportedJava, match='generic reference declarations') as caught:
        parser.parse()
    assert caught.value.code == 'java.unsupported.generic-declarations'
    assert ':108:23:' in str(caught.value)
    loop, = parsed_loops
    variable, expression, _ = loop.children
    assert (loop.token.line, variable.token.line, variable.token.column) == (53, 53, 24)
    assert variable.kind == 'enhanced_var' and variable.value == 'int' and variable.is_final
    assert variable.token.text == 'p' and expression.value == 'SmallPrimes.PRIMES'
    statement, = parsed_throws
    creation, = statement.children
    assert (statement.token.line, statement.token.column) == (70, 13)
    assert creation.kind == 'class_new' and creation.value.tokens[0].text == 'IllegalArgumentException'
    call, = creation.children
    assert call.kind == 'call' and call.value == 'String.format'
    assert [child.value for child in call.children] == ['NUMBER_TOO_SMALL', 'n', '0']
    # Production still rejects the original String declaration before either
    # downstream syntax prerequisite. Do not turn parser reach into execution.
    with pytest.raises(UnsupportedJava) as native:
        compile_source(sources[first], filename=first)
    assert native.value.code == 'java.unsupported.declared-result-type'
    assert ':31:' in str(native.value)
    assert sources == before
    # Complete original parsing still fails before project Sema or lowering.
    # No token removal, source slice, fake library, or project acceptance.


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name='java-array-model-reference')
def test_same_creation_originals_have_independent_jdk_only_obligations(tmp_path, request):
    # These are JDK runtime/typecheck/rejection observations, never PCC native
    # object results. Reject infrastructure failures rather than any nonzero.
    check_originals_reference(tmp_path, request, PROGRAMS, VALID_ONLY,
        [(name, text, message) for name, text, message, _ in INVALID],
        invocation='new ArrayExample().run()', source_input=Path(__file__))
    for name, _, _, diagnostic in INVALID:
        directory = tmp_path / name
        assert (directory / 'compiler-status.txt').read_text() == '1'
        stderr = (directory / 'compiler.stderr.bin').read_text()
        assert 'ArrayExample.java:' in stderr
        assert re.search(diagnostic, stderr), stderr


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name='java-array-model-reference')
def test_exact_project_creation_inputs_match_independent_jdk_access_checks(tmp_path, request):
    java = Path(os.environ['PCC_JAVA_RAW_REFERENCE']).resolve(strict=True)
    level = os.environ.get('PCC_JAVA_RAW_REFERENCE_SOURCE', '21')
    assert level in {'17', '21'}
    release = (java.parent.parent / 'release').read_text()
    version = re.search(r'^JAVA_VERSION="([^"]+)"', release, re.MULTILINE)
    assert version and int(version.group(1).split('.')[0]) >= int(level)
    before = tool_identity(java)
    originals = []
    def finish():
        assert tool_identity(java) == before
        assert all(path.read_bytes() == raw for path, raw in originals)
    request.addfinalizer(finish)
    observed = record_process((str(java), *JVM_LIMITS, '--version'), tmp_path, 'version')
    assert observed[0] == 0
    for name, sources, message in PROJECT_CASES:
        directory = tmp_path / name
        directory.mkdir()
        classes = directory / 'classes'
        classes.mkdir()
        files = []
        for filename, text in sources.items():
            path = directory / filename
            raw = text.encode('utf-8')
            path.write_bytes(raw)
            originals.append((path, raw))
            files.append(str(path))
        command = (str(java), *JVM_LIMITS, '--module', 'jdk.compiler/com.sun.tools.javac.Main',
                   '-proc:none', '-encoding', 'UTF-8', '-source', level, '-target', level,
                   '-classpath', str(classes), '-d', str(classes), *files)
        actual = record_process(command, directory, 'compiler', 20)
        (directory / 'source-identities.json').write_text(json.dumps({
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path, _ in originals if path.parent == directory}, indent=2) + '\n')
        assert actual[0] == (0 if message is None else 1), actual
        if message is not None:
            diagnostic = actual[2].decode('utf-8')
            assert any(filename + ':' in diagnostic for filename in sources)
            pattern = ('private access|protected access|cannot be accessed' if name != 'different-nominal-argument'
                       else 'incompatible types|constructor B')
            assert re.search(pattern, diagnostic), diagnostic
