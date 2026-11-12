"""Shared natural-record/dependent sizeof CI obligations, execution UNRUN.

N4659 [expr.sizeof], [class.mem], [temp.dep.expr], [temp.inst], [expr]/8.
https://timsong-cpp.github.io/cppwp/n4659/expr.sizeof

Shared-owner changes require the complete affected C/C++ ABI/layout lanes in
CI, plus the original defaulted-C1 placement padding node. Focused source or
cross-target semantic assertions do not replace native/reference execution.
The unchanged TinyXML2 whole-project nodes remain independent requirements.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.class_template_members import MethodInstance
from pcc.frontends.cpp.constants import KNOWN
from pcc.frontends.cpp.size_queries import object_layout, sizeof_type
from pcc.frontends.cpp.templates import Pattern, value_dependent
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


FORMER_ORIGINALS = (
    ('class-type', 'struct S{int x;};int f(){return sizeof(S);}',
     'int f();int cpp_test_entry(){return f()+38;}'),
    ('class-expression', 'struct S{int x;};int f(S*p){return sizeof(*p);}',
     'struct S{int x;};int f(S*);int cpp_test_entry(){S s{0};return f(&s)+38;}'),
    ('dependent-expression', 'template<class T>int f(T x){return sizeof(x);}',
     'template<class T>int f(T x){return sizeof(x);}int cpp_test_entry(){return f(0)+38;}'),
    ('value-parameter-sizeof', 'template<int N> int f(){return sizeof(N);}',
     'template<int N> int f(){return sizeof(N);}int cpp_test_entry(){return f<1>()+38;}'),
)
FORMER_ENTRY_ORIGINALS = (
    ('private-template-layout', 'template<class T>class Box{T value;};int cpp_test_entry(){if(sizeof(Box<int>)==sizeof(int))return 42;return 1;}'),
    ('empty-template-layout', 'template<class T>struct Box{};int cpp_test_entry(){if(sizeof(Box<int>)==1)return 42;return 1;}'),
)
SINGLE_PROGRAMS = (
    ('interior-tail-padding', 'struct S{char first;int middle;char last;};'
     'int cpp_test_entry(){if(sizeof(S)!=12)return 1;S values[2];'
     'if(sizeof(values)!=24)return 2;return 42;}'),
    ('nested-member-layout', 'struct Inner{char a;int b;char c;};struct Outer{short first;Inner inner;char last;};'
     'int cpp_test_entry(){if(sizeof(Outer)!=20)return 1;return 42;}'),
    ('empty-member-byte', 'struct Empty{};struct S{char first;Empty empty;char last;};'
     'int cpp_test_entry(){if(sizeof(Empty)!=1||sizeof(S)!=3)return 1;return 42;}'),
    ('private-fields-size-not-access', 'class S{char first;int middle;char last;};'
     'int cpp_test_entry(){if(sizeof(S)!=12)return 1;return 42;}'),
    ('dependent-type-and-no-dereference', 'template<class T>struct S{T*p;int size(){return sizeof(T)+sizeof(*p);}};'
     'int cpp_test_entry(){S<int>s{nullptr};return s.size()+34;}'),
    ('dependent-pointer-and-reference-types', 'template<class T>struct S{int size(){return sizeof(T*)+sizeof(T&);}};'
     'int cpp_test_entry(){S<int>s;return s.size()+30;}'),
    ('dependent-self-layout', 'template<class T>struct S{T n;int size(){return sizeof(S)+sizeof(*this);}};'
     'int cpp_test_entry(){S<int>s{0};return s.size()+34;}'),
    ('dependent-increment-unevaluated', 'template<class T>struct S{T*p;int size(){return sizeof(++*p);}};'
     'int cpp_test_entry(){int n=38;S<int>s{&n};int size=s.size();return n+size;}'),
    ('dependent-call-not-instantiated', 'template<class T>struct S{T bad(){return T(1)%T(2);}'
     'int size(){return sizeof(bad());}};'
     'int cpp_test_entry(){S<double>s;return s.size()+34;}'),
    ('dependent-sizeof-integral-argument', 'template<unsigned long N>int select(){return N;}'
     'template<class T>struct S{int size(){return select<sizeof(T)>();}};'
     'int cpp_test_entry(){S<int>s;return s.size()+38;}'),
    ('nondependent-query-in-unused-pattern', 'template<class T>struct S{int size(){return sizeof(int);}};'
     'int cpp_test_entry(){S<int>s;return s.size()+38;}'),
    ('template-class-size-no-d1-definition', 'template<class T>struct S{T n;~S(){delete n;}};'
     'int cpp_test_entry(){return sizeof(S<int*>)+34;}'),
)
PROGRAMS = tuple((name, (('original.cpp', source), ('entry.cpp', entry)))
                 for name, source, entry in FORMER_ORIGINALS) + tuple(
    (name, ((name+'.cpp', source),)) for name, source in FORMER_ENTRY_ORIGINALS + SINGLE_PROGRAMS)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_class_size_originals_compile_deterministically(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


ORIGINAL_TARGET_DECLARATIONS = (
    'struct Long{char a;long b;char c;};struct Extended{char a;long double b;char c;};'
)


@pytest.mark.parametrize('triple,long_size,extended_size,extended_align,value_supported', (
    ('x86_64-unknown-linux-gnu', 8, 16, 16, False),
    ('x86_64-pc-windows-msvc', 4, 8, 8, True),
    ('x86_64-pc-windows-gnu', 4, 16, 16, False),
    ('aarch64-unknown-linux-gnu', 8, 16, 16, False),
    ('aarch64-apple-darwin', 8, 8, 8, True),
))
def test_class_size_queries_use_target_scalar_facts(triple, long_size, extended_size, extended_align, value_supported):
    class QueryParser(CppParser):
        def _sizeof_expression(self, token):
            result = super()._sizeof_expression(token)
            self.last_query = result
            return result

    parser = QueryParser(triple)
    # A sizeof type-id may ask for extended storage facts even when ordinary
    # value declarations remain explicitly unavailable on the selected target.
    parser.parse('struct Long{char a;long b;char c;};int f(){return sizeof(long double);}')
    long_owner = parser.scope.names['Long'].dtype
    assert sizeof_type(long_owner, parser.types, long_owner.coord) == 3 * long_size
    assert parser.last_query.constant_fact.status == KNOWN
    assert parser.last_query.constant_fact.value == extended_size
    layout = object_layout('long double', parser.types, long_owner.coord)
    assert (layout.size, layout.alignment) == (extended_size, extended_align)
    assert parser.types.value_lowering_supported('long double') is value_supported
    if value_supported:
        declaration_parser = CppParser(triple)
        declaration_parser.parse(ORIGINAL_TARGET_DECLARATIONS)
        extended = declaration_parser.scope.names['Extended'].dtype
        layout = object_layout(extended, declaration_parser.types, extended.coord)
        assert (layout.size, layout.alignment) == (3 * extended_size, extended_align)


@pytest.mark.parametrize('triple', (
    'x86_64-unknown-linux-gnu', 'x86_64-pc-windows-gnu', 'aarch64-unknown-linux-gnu',
))
def test_original_extended_field_declaration_keeps_target_value_rejection(triple):
    # Retain the exact original normal-declaration input, rather than making
    # class sizeof a route around the existing extended-value capability gate.
    with pytest.raises(CppDiagnostic) as error:
        CppParser(triple).parse(ORIGINAL_TARGET_DECLARATIONS)
    assert error.value.code == 'cpp.unsupported.extended-float'


def test_dependent_sizeof_retains_typed_operand_identity_and_value_dependency():
    parser = CppParser()
    parser.parse('template<class T>struct S{T*p;int size(){return sizeof(T)+sizeof(++*p);}};')
    owner = parser.scope.names['S'].dtype
    record, = owner.methods
    expression = record.body.children[0].children[0]

    def queries(node):
        node = getattr(node, 'node', node)
        if not isinstance(node, Pattern):
            return
        if node.kind == 'sizeof':
            yield node
        for child in node.children:
            yield from queries(child)

    first, second = tuple(queries(expression))
    assert first.value is owner.parameters[0] and not first.children
    assert second.children[0].node.kind == 'increment'
    assert value_dependent(expression)
    assert not parser.instance_registry.entries
    assert parser.unevaluated_depth == 0


def test_sizeof_replay_does_not_emit_side_effects_or_request_member_bodies():
    parser = CppParser()
    tree = parser.parse(dict(SINGLE_PROGRAMS)['dependent-call-not-instantiated'])
    contexts = [entry.context for entry in parser.instance_registry.entries.values()
                if isinstance(entry.context, MethodInstance)]
    assert [context.symbol.name for context in contexts] == ['size']
    size_body = next(node.body for node in tree.ext if isinstance(node, c_ast.FuncDef)
                     and node.decl.name == contexts[0].symbol.c_name)

    class Observe(c_ast.NodeVisitor):
        def visit_FuncCall(self, node):
            raise AssertionError('sizeof replay retained a runtime call')

        def visit_UnaryOp(self, node):
            assert node.op not in ('++', '--', 'p++', 'p--')
            self.generic_visit(node)

    Observe().visit(size_body)
    assert parser.unevaluated_depth == 0


INVALID = (
    ('incomplete', 'struct S;int f(){return sizeof(S);}', 'cpp.incomplete-class'),
    ('dependent-incomplete', 'struct S;template<class T>struct Box{int size(){return sizeof(T);}};'
     'int f(Box<S>*p){return p->size();}', 'cpp.incomplete-class'),
    ('dependent-void', 'template<class T>struct Box{int size(){return sizeof(T);}};'
     'int f(Box<void>*p){return p->size();}', 'cpp.invalid-sizeof'),
    ('dependent-invalid-increment', 'template<class T>struct Box{T*p;int size(){return sizeof(++*p);}};'
     'int f(Box<bool>*p){return p->size();}', 'cpp.invalid-operands'),
    ('definition-point-undeclared', 'template<class T>int f(){return sizeof(missing);}', 'cpp.undeclared'),
)
BOUNDARIES = (
    ('dependent-array-type', 'template<class T>int f(){return sizeof(T[2]);}', 'cpp.unsupported.array-templates'),
    # Single non-virtual bases are owned by inheritance.py; a virtual base is not.
    ('unowned-bases', 'struct B{};struct S:virtual B{};int f(){return sizeof(S);}', 'cpp.unsupported.base-classes'),
)


@pytest.mark.parametrize('name,source,code', INVALID + BOUNDARIES,
                         ids=[row[0] for row in INVALID + BOUNDARIES])
def test_invalid_or_unowned_sizeof_stops_before_ir(name, source, code, monkeypatch):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid or unowned sizeof reached IR')

    monkeypatch.setattr(CCodeGenerator, 'generate_code', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name+'.cpp', source),))
    assert error.value.code == code
