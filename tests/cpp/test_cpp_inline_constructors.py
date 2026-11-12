"""Original completed-class C1 binding obligations, not inline execution proof.

N4659 [class.mem], [class.base.init], [basic.def.odr], [dcl.fct.spec].
The shared external-ODR emitter is a real prerequisite, not an internal-linkage
substitute. These tests inspect the bound AST even while emission fails closed.
"""
import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser


def bind_original(source, name='S'):
    parser = CppParser()
    try:
        tree = parser.parse(source, filename='inline-original.cpp')
    except CppDiagnostic as error:
        assert error.code == 'cpp.unsupported.inline-member-functions'
        tree = None
    try:
        from pcc.backend.definition_linkage import supports_external_odr_function_definitions
    except ModuleNotFoundError as error:
        if error.name != 'pcc.backend.definition_linkage':
            raise
        supported = False
    else:
        supported = supports_external_odr_function_definitions(parser.types.target_triple)
    definition = parser.scope.names[name].dtype.definition
    assert definition.complete
    pending = definition.inline_constructors
    assert pending and all(item.function is not None for item in pending)
    for item in pending:
        assert item.definition_linkage == 'external_odr'
        assert item.symbol.definition_linkage == 'external_odr'
        assert item.symbol.defined
        assert item.declaration.storage == []
        assert item.declaration.funcspec == []
        if supported and hasattr(item.declaration, 'definition_linkage'):
            assert tree is not None
            assert item.declaration.definition_linkage == 'external'
            assert item.function.decl.definition_linkage == 'external_odr'
            assert item.function.decl is not item.declaration
            assert item.function in tree.ext
            assert all(tree.ext.index(prototype) < tree.ext.index(item.function)
                       for prototype in definition.method_declarations
                       if isinstance(prototype, c_ast.Decl))
        else:
            assert tree is None  # No unmarked strong definition escapes parsing.
    return parser, definition, pending


def walk(node):
    yield node
    for _, child in node.children():
        yield from walk(child)


def stores(pending):
    return [node for node in pending.function.body.block_items
            if isinstance(node, c_ast.Assignment)]


def test_inline_constructor_binds_later_members_and_keeps_parameter_identity():
    source = ('struct S{S(int a):b(a+2),a(a){} int a;int b;};')
    _, definition, (pending,) = bind_original(source)
    assignments = stores(pending)
    assert [node.lvalue.field.name for node in assignments] == ['a', 'b']
    parameter = pending.parameter_scope.names['a']
    assert parameter is not definition.scope.names['a']
    assert any(isinstance(node, c_ast.ID) and node.name == parameter.c_name
               for node in walk(assignments[0].rvalue))


def test_inline_constructor_keeps_original_tokens_and_coordinates():
    parser, _, (pending,) = bind_original('struct S{S():n((40+2)){} int n;};')
    assert [token.value for token in pending.tokens] == [':', 'n', '(', '(', '40', '+', '2', ')', ')', '{', '}']
    assert all(any(token is original for original in parser.cursor.tokens)
               for token in pending.tokens)
    assert all(token.coord.file == 'inline-original.cpp' for token in pending.tokens)
    assert parser.member_function is None and parser.this_symbol is None


def test_inline_constructor_orders_effectful_initializers_by_declaration():
    _, _, (pending,) = bind_original(
        'int next(int*p){*p=*p+1;return *p;}'
        'struct S{S(int*p):b(next(p)),a(next(p)){} int a;int b;};')
    assignments = stores(pending)
    assert [node.lvalue.field.name for node in assignments] == ['a', 'b']
    assert [sum(isinstance(node, c_ast.FuncCall) for node in walk(store.rvalue))
            for store in assignments] == [1, 1]


def test_inline_constructor_consumes_dmi_without_parameter_capture():
    _, _, (pending,) = bind_original(
        'int seed=40;struct S{S(int seed):a(seed){} int a=1;int b=seed+2;};')
    assignments = stores(pending)
    parameter = pending.parameter_scope.names['seed'].c_name
    assert any(isinstance(n, c_ast.ID) and n.name == parameter for n in walk(assignments[0]))
    assert not any(isinstance(n, c_ast.ID) and n.name == parameter for n in walk(assignments[1]))


def test_inline_constructor_does_not_zero_omitted_scalar_members():
    _, _, (pending,) = bind_original('struct S{S(){} int n;int*p;};')
    assert pending.function.body.block_items == []


def test_inline_constructor_retains_local_normal_exit_cleanup():
    _, _, (pending,) = bind_original('struct Guard{~Guard();};struct S{S(){Guard g;} int n;};')
    assert any(isinstance(n, c_ast.FuncCall) and isinstance(n.name, c_ast.ID)
               and 'D1E' in n.name.name for n in walk(pending.function.body))


def test_tinyxml_strpair_original_constructor_body_binds_later_fields():
    # The exact upstream declaration at tinyxml2.h:149 (11.0.0), in a reduced
    # declaration fixture. Not the unchanged whole-project acceptance gate.
    original = 'StrPair() : _flags( 0 ), _start( 0 ), _end( 0 ) {}'
    _, _, (pending,) = bind_original(
        'class StrPair{public:' + original +
        'private:int _flags;char* _start;char* _end;};', 'StrPair')
    assert [n.lvalue.field.name for n in stores(pending)] == ['_flags', '_start', '_end']


@pytest.mark.parametrize('source,code', (
    # Local statics in an inline (in-class) body keep their explicit gate.
    ('struct S{S(){static int n=0;} };', 'cpp.unsupported.static-initialization-analysis'),
    ('struct S{S(){thread_local int n=0;} };', 'cpp.unsupported.syntax'),
    # The string-literal original moved verbatim to test_cpp_string_literals' MOVED_ORIGINALS.
    ('struct S{S():n{1}{} int n;};', 'cpp.unsupported.constructor-list-initialization'),
    ('struct S{S():S(1){} S(int);};', 'cpp.unsupported.delegating-constructors'),
    ('struct S{using Self=S;S():Self(1){} S(int);};', 'cpp.unsupported.delegating-constructors'),
    ('struct S;using Self=S;struct S{S():Self(1){} S(int);};', 'cpp.unsupported.delegating-constructors'),
    ('struct S{S():n(later()){} int n;};int later();', 'cpp.undeclared'),
    ('struct S{S()try{}catch(...){} };', 'cpp.unsupported.exceptions'),
    ('struct S{S()noexcept{} };', 'cpp.unsupported.exceptions'),
    ('struct B{};struct S:B{S():B(){} };', 'cpp.unsupported.base-classes'),
    ('struct S{S():missing(1){} int n;};', 'cpp.invalid-member-initializer'),
    ('struct S{S():n(1),n(2){} int n;};', 'cpp.duplicate-member-initializer'),
    ('struct S{S(){} const int n;};', 'cpp.const-initializer'),
    ('struct S{S(){absent=1;} int n;};', 'cpp.undeclared'),
))
def test_inline_constructor_missing_semantics_and_invalid_originals_stay_explicit(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


# Real same-input programs reserved for the shared-linkage integration gate.
# They are not executed or marked passed by the AST tests above.
INLINE_PROGRAMS = (
    ('complete-class-value', 'struct S{S(int x):n(x){} int n;};int cpp_test_entry(){S s(42);return s.n;}'),
    ('member-order-once', 'int next(int*p){*p=*p+1;return *p;}struct S{S(int*p):b(next(p)),a(next(p)){}int a;int b;};int cpp_test_entry(){int n=0;S s(&n);if(n!=2||s.a!=1||s.b!=2)return 1;return 42;}'),
    ('const-initialization', 'struct S{S(int x):n(x){} const int n;};int cpp_test_entry(){S s(42);return s.n;}'),
)


def test_inline_constructor_rejects_emission_when_shared_capability_is_absent(monkeypatch):
    import sys
    from types import SimpleNamespace
    from pcc.backend import owned_object_emit
    from tests.cpp.owned_execution import compile_host_cpp

    def forbidden(*args, **kwargs):
        raise AssertionError('an unsupported external-ODR definition reached emission')

    monkeypatch.setitem(sys.modules, 'pcc.backend.definition_linkage', SimpleNamespace(
        supports_external_odr_function_definitions=lambda target: False,
    ))
    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('inline.cpp', INLINE_PROGRAMS[0][1]),))
    assert error.value.code == 'cpp.unsupported.inline-member-functions'


def test_inline_constructor_body_looks_up_a_later_method():
    _, definition, (pending,) = bind_original(
        'struct S{S(){n=answer();} int n;int answer()const;};')
    method = definition.scope.names['answer'].overloads[0]
    assert any(isinstance(node, c_ast.FuncCall) and isinstance(node.name, c_ast.ID)
               and node.name.name == method.c_name for node in walk(pending.function.body))


UNSIGNED_LATER_METHOD = ('struct S{S():n(answer()){} unsigned long long n;unsigned int answer()const;};'
                         'unsigned int S::answer()const{return 4294967295U;}'
                         'int cpp_test_entry(){S s;if(s.n==4294967295ULL)return 42;return 7;}')


def test_inline_constructor_preserves_later_unsigned_method_prototype_before_body():
    _, definition, (pending,) = bind_original(UNSIGNED_LATER_METHOD)
    answer = definition.scope.names['answer'].overloads[0]
    assert answer.dtype == 'unsigned int'
    assert any(isinstance(n, c_ast.FuncCall) and isinstance(n.name, c_ast.ID)
               and n.name.name == answer.c_name for n in walk(pending.function.body))
    # When the shared capability is present, bind_original additionally checks
    # every prototype's placement before the body and definition-only linkage.


ORIGINAL_INLINE_DECLARATION = 'struct S{S(){}};'


def test_original_inline_constructor_declaration_is_retained_as_positive():
    _, _, (pending,) = bind_original(ORIGINAL_INLINE_DECLARATION)
    assert pending.symbol.constructor
    assert pending.symbol.c_name == '_ZN1SC1Ev'
    assert pending.function.body.block_items == []


def test_original_inline_constructor_declaration_has_stable_owned_artifacts():
    from tests.cpp.owned_execution import compile_host_cpp
    from pcc.backend.elf_x86_64 import parse_relocatable

    # The formerly rejected TU remains byte-for-byte unchanged. A separate
    # observer TU supplies the normal artifact adapter's required entry point.
    units = (('original.cpp', ORIGINAL_INLINE_DECLARATION),
             ('entry.cpp', 'int cpp_test_entry(){return 42;}'))
    first = compile_host_cpp(units)
    assert first == compile_host_cpp(units)
    definitions = [symbol for symbol in parse_relocatable(first.objects[0]).symbols
                   if symbol.name == '_ZN1SC1Ev' and symbol.section_index]
    assert len(definitions) == 1 and definitions[0].binding == 2
