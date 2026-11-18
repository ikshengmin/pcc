"""Typed deduction tests, not member-template parsing or native qualification.

N4659 [temp.deduct.type]: direct class-template non-type arguments participate
in deduction; arbitrary dependent expressions are not reverse-evaluated.
"""

from types import SimpleNamespace

import pytest

from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp import CppDiagnostic
from pcc.frontends.cpp.class_templates import ClassTemplatePattern, TemplateIdPattern
from pcc.frontends.cpp.classes import ClassType
from pcc.frontends.cpp.constants import known
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.templates import (
    IntegralArgument,
    Pattern,
    TemplateArgument,
    TypeParameter,
    ValueParameter,
    deduce,
)
from pcc.frontends.cpp.types import (
    ArithmeticTypes,
    ConstType,
    ReferenceType,
)

COORD = Coord("deduction.cpp", 1)
TYPES = ArithmeticTypes("x86_64-unknown-linux-gnu")


def pool_symbol(name="Pool"):
    pattern = ClassTemplatePattern(name, (), Scope(), "struct", COORD)
    return Symbol(name, "class-template", COORD, dtype=pattern)


def concrete(origin, *arguments):
    result = ClassType(origin.name, (origin.name,), COORD)
    result.template_origin = origin
    result.template_arguments = tuple(arguments)
    return result


def function(parameters, *signature):
    result = Symbol("f", "function", COORD, parameters=tuple(signature))
    result.template_parameters = tuple(parameters)
    return result


def size(value):
    return IntegralArgument("unsigned long", value, 64, True)


def value_argument(value, dtype="int"):
    expression = SimpleNamespace(dtype=dtype, constant_fact=known(dtype, value, COORD))
    return TemplateArgument("value", expression, COORD)


def test_explicit_node_type_and_pool_size_deduction():
    node = ClassType("Node", ("Node",), COORD)
    t = TypeParameter(0, "NodeType", COORD)
    n = ValueParameter(1, "PoolElementSize", "unsigned long", COORD)
    pool = pool_symbol()
    signature = ReferenceType(TemplateIdPattern(pool, (n,), COORD))
    f = function((t, n), signature)
    result = deduce(
        f, [concrete(pool, size(32))], [TemplateArgument("type", node, COORD)], TYPES
    )
    assert result == (node, size(32))


def test_two_arguments_must_agree_instead_of_choosing_last():
    n = ValueParameter(0, "N", "unsigned long", COORD)
    pool = pool_symbol()
    p = ReferenceType(TemplateIdPattern(pool, (n,), COORD))
    f = function((n,), p, p)
    assert deduce(
        f, [concrete(pool, size(4)), concrete(pool, size(4))], types=TYPES
    ) == (size(4),)
    assert (
        deduce(f, [concrete(pool, size(4)), concrete(pool, size(8))], types=TYPES)
        is None
    )


def test_nominal_origin_not_class_name_controls_match():
    n = ValueParameter(0, "N", "unsigned long", COORD)
    first, other = pool_symbol(), pool_symbol()
    f = function((n,), ReferenceType(TemplateIdPattern(first, (n,), COORD)))
    assert deduce(f, [concrete(other, size(4))], types=TYPES) is None


def test_deduced_value_type_does_not_use_explicit_argument_conversions():
    n = ValueParameter(0, "N", "unsigned long", COORD)
    pool = pool_symbol()
    f = function((n,), TemplateIdPattern(pool, (n,), COORD))
    assert (
        deduce(f, [concrete(pool, IntegralArgument("int", 4, 32, False))], types=TYPES)
        is None
    )


def test_explicit_value_still_has_converted_constant_expression_semantics():
    n = ValueParameter(0, "N", "unsigned char", COORD)
    f = function((n,), "int")
    assert deduce(f, ["int"], [value_argument(255)], TYPES) == (
        IntegralArgument("unsigned char", 255, 8, True),
    )
    with pytest.raises(CppDiagnostic) as error:
        deduce(f, ["int"], [value_argument(256)], TYPES)
    assert error.value.code == "cpp.narrowing-template-argument"


def test_type_deduction_retains_reference_const_and_removes_value_const():
    t = TypeParameter(0, "T", COORD)
    assert deduce(function((t,), ReferenceType(t)), [ConstType("int")]) == (
        ConstType("int"),
    )
    assert deduce(function((t,), t), [ConstType("int")]) == ("int",)
    assert deduce(function((t,), ReferenceType(ConstType(t))), [ConstType("int")]) == (
        "int",
    )


def test_nested_template_id_argument_deduces_direct_value():
    n = ValueParameter(0, "N", "unsigned long", COORD)
    inner, outer = pool_symbol("Inner"), pool_symbol("Outer")
    pattern = TemplateIdPattern(outer, (TemplateIdPattern(inner, (n,), COORD),), COORD)
    actual = concrete(outer, concrete(inner, size(9)))
    assert deduce(function((n,), pattern), [actual], types=TYPES) == (size(9),)


def test_non_deduced_expression_is_not_algebraically_inverted():
    n = ValueParameter(0, "N", "unsigned long", COORD)
    pool = pool_symbol()
    expression = Pattern("binary", COORD, "+", [])
    pattern = TemplateIdPattern(pool, (expression,), COORD)
    with pytest.raises(CppDiagnostic) as error:
        deduce(function((n,), pattern), [concrete(pool, size(9))], types=TYPES)
    assert error.value.code == "cpp.unsupported.non-type-template-deduction"


def test_fixed_argument_and_argument_count_must_match():
    t = TypeParameter(0, "T", COORD)
    pool = pool_symbol()
    f = function((t,), TemplateIdPattern(pool, (t, size(4)), COORD))
    assert deduce(f, [concrete(pool, "int", size(4))], types=TYPES) == ("int",)
    assert deduce(f, [concrete(pool, "int", size(5))], types=TYPES) is None
    assert deduce(f, [concrete(pool, "int")], types=TYPES) is None


def test_complete_explicit_arguments_leave_conversions_to_overload_resolution():
    n = ValueParameter(0, "N", "unsigned long", COORD)
    pool = pool_symbol()
    f = function((n,), ReferenceType(TemplateIdPattern(pool, (n,), COORD)))
    assert deduce(f, ["unrelated-convertible-type"], [value_argument(4)], TYPES) == (size(4),)

def test_runtime_value_parameter_is_not_a_deduction_source():
    n = ValueParameter(0, "N", "unsigned long", COORD)
    with pytest.raises(CppDiagnostic) as error:
        deduce(function((n,), "int"), ["int"], types=TYPES)
    assert error.value.code == "cpp.unsupported.non-type-template-deduction"


def test_fully_explicit_scoped_enum_source_retains_parser_admission_guard():
    from pcc.frontends.cpp import CppParser
    source = (
        'enum class E { item }; '
        'template<class T> int f(T x){return 1;} '
        'int run(){return f<int>(E::item);}'
    )
    with pytest.raises(CppDiagnostic) as error:
        CppParser('x86_64-unknown-linux-gnu').parse(source, 'explicit.cpp')
    assert error.value.code == 'cpp.unsupported.enum-templates'


def test_mixed_explicit_source_call_still_rejects_conflicting_deductions():
    from pcc.frontends.cpp import CppParser
    source = (
        'template<class T,class U> int f(T x,U y,U z){return 1;} '
        'int run(){return f<long>(1,2,3l);}'
    )
    with pytest.raises(CppDiagnostic) as error:
        CppParser('x86_64-unknown-linux-gnu').parse(source, 'mixed.cpp')
    assert error.value.code == 'cpp.no-viable-overload'


@pytest.mark.parametrize('parameter', ('T& x', 'const T& x'))
def test_reference_template_source_remains_explicitly_unimplemented(parameter):
    # Typed deduction support does not remove the parser/replay admission gate.
    from pcc.frontends.cpp import CppParser
    source = 'template<class T> int f(' + parameter + '){return 1;}'
    with pytest.raises(CppDiagnostic) as error:
        CppParser('x86_64-unknown-linux-gnu').parse(source, 'reference.cpp')
    assert error.value.code == 'cpp.unsupported.references'


@pytest.mark.integration
@pytest.mark.parametrize('source,accepted', (
    ('enum class E { item }; template<class T> int f(T x){return 1;} '
     'int run(){return f<int>(E::item);}', False),
    ('template<class T,class U> int f(T x,U y,U z){return 1;} '
     'int run(){return f<long>(1,2,3l);}', False),
    ('template<class T> int f(T& x){return 1;} '
     'int run(){const int x=0;return f(x);}', True),
    ('template<class T> int f(const T& x){return 1;} '
     'int run(){const int x=0;return f(x);}', True),
))
def test_source_boundaries_with_explicit_external_reference(source, accepted):
    import os
    import shutil
    import subprocess
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference compiler unavailable'
    result = subprocess.run(
        [compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', '-x', 'c++', '-'],
        input=source, text=True, capture_output=True, check=False,
    )
    assert result.returncode == (0 if accepted else 1), result.stderr
    if not accepted:
        assert result.stderr.strip()


def test_mixed_explicit_nondependent_parameter_leaves_conversion_to_resolver():
    n = ValueParameter(0, "N", "unsigned long", COORD)
    t = TypeParameter(1, "T", COORD)
    pool = pool_symbol()
    f = function((n, t), TemplateIdPattern(pool, (n,), COORD), t)
    assert deduce(f, ["convertible-type", "int"], [value_argument(4)], TYPES) == (size(4), "int")

def test_fixed_nested_template_identity_still_participates_in_deduction():
    t = TypeParameter(0, "T", COORD)
    outer, inner, other = pool_symbol("Outer"), pool_symbol("Inner"), pool_symbol("Other")
    f = function((t,), TemplateIdPattern(outer, (TemplateIdPattern(inner, (size(4),), COORD), t), COORD))
    assert deduce(f, [concrete(outer, concrete(other, size(4)), "int")], types=TYPES) is None


def test_fully_explicit_resolver_still_rejects_nonconvertible_argument():
    # The parser separately gates enum/template call syntax. Exercise the real
    # resolver here without claiming that source admission has been expanded.
    from pcc.frontends.cpp import CppParser
    from pcc.frontends.cpp.sema import resolve_overload
    parser = CppParser('x86_64-unknown-linux-gnu')
    parser.parse('enum class E { item }; template<class T> int f(T x){return 1;}', 'resolver.cpp')
    group = parser.scope.lookup('f')
    enum = parser.scope.lookup('E').dtype
    with pytest.raises(CppDiagnostic) as error:
        resolve_overload(group, [enum], TYPES, COORD, explicit_arguments=[TemplateArgument('type', 'int', COORD)])
    assert error.value.code == 'cpp.no-viable-overload'


def test_unknown_expression_wrapper_is_not_mistaken_for_fixed_type():
    n = ValueParameter(0, "N", "unsigned long", COORD)
    pool = pool_symbol()
    expression = SimpleNamespace(dtype="unsigned long", node=Pattern("binary", COORD, "+", []))
    pattern = TemplateIdPattern(pool, (expression,), COORD)
    with pytest.raises(CppDiagnostic) as error:
        deduce(function((n,), pattern), [concrete(pool, size(9))], types=TYPES)
    assert error.value.code == "cpp.unsupported.non-type-template-deduction"


def test_partial_explicit_nested_type_must_match():
    t = TypeParameter(0, 'T', COORD)
    n = ValueParameter(1, 'N', 'unsigned long', COORD)
    pool = pool_symbol()
    f = function((t, n), ReferenceType(TemplateIdPattern(pool, (t, n), COORD)))
    explicit = [TemplateArgument('type', 'int', COORD)]
    assert deduce(f, [concrete(pool, 'long', size(4))], explicit, TYPES) is None
    assert deduce(f, [concrete(pool, 'int', size(4))], explicit, TYPES) == ('int', size(4))


def test_partial_explicit_nested_value_must_match():
    n = ValueParameter(0, 'N', 'unsigned long', COORD)
    t = TypeParameter(1, 'T', COORD)
    pool = pool_symbol()
    f = function((n, t), ReferenceType(TemplateIdPattern(pool, (t, n), COORD)))
    assert deduce(f, [concrete(pool, 'int', size(5))], [value_argument(4)], TYPES) is None
    assert deduce(f, [concrete(pool, 'int', size(4))], [value_argument(4)], TYPES) == (size(4), 'int')


def test_template_id_substitution_uses_nominal_registry_callback():
    from pcc.frontends.cpp.templates import substitute
    from pcc.frontends.cpp.types import PointerType
    t = TypeParameter(0, 'T', COORD)
    n = ValueParameter(1, 'N', 'unsigned long', COORD)
    pool = pool_symbol()
    node = ClassType('Node', ('Node',), COORD)
    actual = concrete(pool, size(32))
    f = function((t, n), ReferenceType(TemplateIdPattern(pool, (n,), COORD)))
    bindings = deduce(f, [actual], [TemplateArgument('type', node, COORD)], TYPES)
    calls = []
    def registry(symbol, args, coord):
        calls.append((symbol, args, coord))
        assert symbol is pool and args == (size(32),)
        return actual
    result = substitute(f.parameters[0], bindings, registry)
    assert result.referent is actual
    assert substitute(PointerType(t), bindings, registry).pointee is node
    assert calls == [(pool, (size(32),), COORD)]
    with pytest.raises(CppDiagnostic) as error:
        substitute(f.parameters[0], bindings)
    assert error.value.code == 'cpp.unsupported.class-templates'


@pytest.mark.parametrize('formal_const,actual_const,lvalue,allowed', [
    (False, False, True, True), (False, True, True, False),
    (True, False, True, True), (True, True, True, True),
    (False, False, False, False), (True, False, False, False),
])
def test_deduced_reference_obeys_original_binding_owner(formal_const, actual_const, lvalue, allowed):
    from pcc.frontends.cpp.templates import substitute
    from pcc.frontends.cpp.references import directly_compatible
    n = ValueParameter(0, 'N', 'unsigned long', COORD)
    pool = pool_symbol()
    actual = concrete(pool, size(7))
    pattern = TemplateIdPattern(pool, (n,), COORD)
    formal = ReferenceType(ConstType(pattern) if formal_const else pattern)
    source = ConstType(actual) if actual_const else actual
    bindings = deduce(function((n,), formal), [source], types=TYPES)
    result = substitute(formal, bindings, lambda s, a, c: actual)
    assert directly_compatible(source, result, lvalue) is allowed


def test_structural_pattern_key_is_ordinal_and_nominal():
    from pcc.frontends.cpp.templates import same_type
    pool = pool_symbol()
    a = TemplateIdPattern(pool, (ValueParameter(1, 'N', 'unsigned long', COORD),), COORD)
    b = TemplateIdPattern(pool, (ValueParameter(1, 'M', 'unsigned long', COORD),), COORD)
    c = TemplateIdPattern(pool_symbol(), b.arguments, COORD)
    assert same_type(a, b)
    assert not same_type(a, c)


@pytest.mark.integration
@pytest.mark.parametrize('const_pool,repeat', [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize('namespace', ['', 'A'])
def test_template_pattern_abi_matches_gcc_without_running_code(tmp_path, const_pool, repeat, namespace):
    import subprocess
    from pcc.frontends.cpp.templates import mangle_instance
    from pcc.frontends.cpp.types import PointerType
    pool = pool_symbol()
    t = TypeParameter(0, 'T', COORD)
    n = ValueParameter(1, 'N', 'unsigned long', COORD)
    pattern = TemplateIdPattern(pool, (n,), COORD)
    signature = ReferenceType(ConstType(pattern) if const_pool else pattern)
    f = function((t, n), signature, *([signature] if repeat else []))
    f.name, f.dtype = 'make', PointerType(t)
    param = ('const ' if const_pool else '') + 'Pool<N>&'
    if namespace:
        scope = Scope().open_namespace(namespace, COORD)
        pool.dtype.qualified_name = (namespace, 'Pool')
        f.owner = scope
    source = 'template<unsigned long N> struct Pool {}; '
    source += 'template<class T,unsigned long N> T* make(' + param + ' a' + (',' + param + ' b' if repeat else '') + '){return nullptr;} '
    source += 'void use(){Pool<7> p; make<int>(p' + (',p' if repeat else '') + ');}'
    if namespace:
        source = 'namespace ' + namespace + '{' + source + '}'
    path = tmp_path / 'abi.cpp'; path.write_text(source, encoding='utf-8')
    assembly = tmp_path / 'abi.s'
    result = subprocess.run(['/usr/bin/g++','-std=c++17','-pedantic-errors','-S','-o',str(assembly),str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    symbol = mangle_instance(f, ('int', size(7)))
    assert '\n' + symbol + ':\n' in assembly.read_text(encoding='utf-8'), symbol


def test_substitution_refuses_unknown_value_expression_without_callback():
    from pcc.frontends.cpp.templates import substitute
    expression = SimpleNamespace(dtype='unsigned long', node=Pattern('binary', COORD, '+', []))
    pattern = TemplateIdPattern(pool_symbol(), (expression,), COORD)
    called = []
    with pytest.raises(CppDiagnostic) as error:
        substitute(pattern, (), lambda *args: called.append(args))
    assert error.value.code == 'cpp.unsupported.non-type-template-deduction'
    assert called == []


@pytest.mark.integration
@pytest.mark.parametrize('const_member', [False, True])
def test_member_owner_can_compose_template_abi_with_gcc(tmp_path, const_member):
    import subprocess
    from pcc.frontends.cpp.template_abi import mangle_template_type
    from pcc.frontends.cpp.types import PointerType
    t = TypeParameter(0, 'T', COORD)
    n = ValueParameter(1, 'N', 'unsigned long', COORD)
    signature = ReferenceType(TemplateIdPattern(pool_symbol(), (n,), COORD))
    # The member owner supplies its declaring class and template prefix entries.
    substitutions = [('class-owner', 'Doc'), ('member-template-prefix', 'make')]
    encoded = mangle_template_type(PointerType(t), substitutions)
    encoded += mangle_template_type(signature, substitutions)
    encoded += mangle_template_type(signature, substitutions)
    symbol = '_ZN' + ('K' if const_member else '') + '3Doc4makeIiLm7EEE' + encoded
    source = ('template<unsigned long N> struct Pool {}; struct Doc {'
              'template<class T,unsigned long N> T* make(Pool<N>& a,Pool<N>& b)' +
              (' const' if const_member else '') + '{return nullptr;}}; '
              'void use(){Doc d;Pool<7> p;d.make<int>(p,p);}')
    path = tmp_path / 'member.cpp'; path.write_text(source, encoding='utf-8')
    assembly = tmp_path / 'member.s'
    result = subprocess.run(['/usr/bin/g++','-std=c++17','-pedantic-errors','-S','-o',str(assembly),str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '\n' + symbol + ':\n' in assembly.read_text(encoding='utf-8'), symbol
