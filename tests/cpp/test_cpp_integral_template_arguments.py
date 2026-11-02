"""N4659 [temp.param], [temp.arg.nontype], [temp.deduct.call], [expr.const].

Source/typed-AST obligations for a bounded integral function-template slice.
These are authored CI obligations, not executed evidence or class-template
support. The shared instance registry remains the only instantiation owner.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.constants import KNOWN
from pcc.frontends.cpp.templates import IntegralArgument, Pattern, TypeParameter, ValueParameter
from pcc.support.instantiation import InstanceState


TARGET = "x86_64-unknown-linux-gnu"


def parse(source, target=TARGET):
    parser = CppParser(target)
    tree = parser.parse(source, "integral-template.cpp")
    return parser, tree


def bodies(tree):
    return {node.decl.name: node for node in tree.ext if isinstance(node, c_ast.FuncDef)}


def test_mixed_parameters_retain_ordinals_declaration_identity_and_pattern():
    parser, tree = parse("template<class T, unsigned long N, class U> T add(T x,U y){return x+T(N)+T(y);}")
    origin = parser.scope.lookup("add").overloads[0]
    assert not tree.ext
    assert [type(p) for p in origin.template_parameters] == [TypeParameter, ValueParameter, TypeParameter]
    assert [p.index for p in origin.template_parameters] == [0, 1, 2]
    value = origin.pattern.children[0].children[0].node.children[0].node.children[1].node.children[0]
    assert value.node.kind == "template-value"
    assert value.node.value is origin.template_parameters[1]
    assert value.symbol.template_parameter is value.node.value
    assert value.dtype == "unsigned long"
    assert not value.lvalue


def test_converted_value_not_source_spelling_is_shared_registry_key():
    parser, tree = parse("template<unsigned long N> int f(){return int(N);} "
                         "int run(){return f<2+2>()+f<4u>()+f<4ul>();}")
    origin = parser.scope.lookup("f").overloads[0]
    argument = IntegralArgument("unsigned long", 4, 64, True)
    assert tuple(origin.instances) == ((argument,),)
    entry = parser.instance_registry.get((origin, (argument,)))
    assert entry.state is InstanceState.COMPLETE
    assert entry.value.c_name == "_Z1fILm4EEiv"
    syntax = entry.value.explicit_template_arguments[0]
    assert syntax.kind == "value"
    assert [token.value for token in syntax.tokens] == ["2", "+", "2"]
    assert isinstance(syntax.value.node, c_ast.BinaryOp)
    assert syntax.value.constant_fact.status == KNOWN
    assert set(bodies(tree)) == {"_Z3runv", "_Z1fILm4EEiv"}


@pytest.mark.parametrize("dtype,expression,encoding,width,unsigned,value", (
    ("int", "-7", "Lin7E", 32, False, -7),
    ("unsigned int", "4294967295u", "Lj4294967295E", 32, True, 4294967295),
    ("long long", "(-9223372036854775807ll-1)", "Lxn9223372036854775808E", 64, False, -9223372036854775808),
    ("unsigned long long", "18446744073709551615ull", "Ly18446744073709551615E", 64, True, 18446744073709551615),
    ("bool", "true", "Lb1E", 8, True, 1),
    ("bool", "1", "Lb1E", 8, True, 1),
    ("signed char", "-128", "Lan128E", 8, False, -128),
    ("unsigned char", "255", "Lh255E", 8, True, 255),
    ("short", "32767", "Ls32767E", 16, False, 32767),
))
def test_typed_mangling_and_target_value_identity(dtype, expression, encoding, width, unsigned, value):
    parser, tree = parse("template<" + dtype + " N> int f(){return N!=0;} int run(){return f<" + expression + ">();}")
    origin = parser.scope.lookup("f").overloads[0]
    assert tuple(origin.instances) == ((IntegralArgument(dtype, value, width, unsigned),),)
    assert "_Z1fI" + encoding + "Eiv" in bodies(tree)


def test_boolean_integer_and_nominal_enum_values_do_not_collide_as_types():
    parser, tree = parse("enum E{one=1}; template<int N> int a(){return N;} "
                         "template<bool N> int b(){return N;} int run(){return a<true>()+a<one>()+b<1>();}")
    a = next(iter(parser.scope.lookup("a").overloads[0].instances))[0]
    b = next(iter(parser.scope.lookup("b").overloads[0].instances))[0]
    assert a == IntegralArgument("int", 1, 32, False)
    assert b == IntegralArgument("bool", 1, 8, True)
    assert a != b and a != 1 and b != True
    assert set(bodies(tree)) == {"_Z3runv", "_Z1aILi1EEiv", "_Z1bILb1EEiv"}


def test_type_value_parameter_kind_and_type_distinguish_overloads():
    parser, _ = parse("template<int N> int f(){return N;} template<long N> int f(){return int(N);} "
                      "template<class T> int f(){return 1;}")
    assert len(parser.scope.lookup("f").overloads) == 3


def test_renamed_forward_declarations_merge_and_use_definition_parameter_identity():
    parser, tree = parse("template<class T,T N> T f(); int run(){return f<int,42>();} "
                         "template<class U,U M> U f(){return M;}")
    origin = parser.scope.lookup("f").overloads[0]
    assert len(parser.scope.lookup("f").overloads) == 1
    assert origin.pattern.children[0].children[0].node.value.name == "M"
    assert set(bodies(tree)) == {"_Z3runv", "_Z1fIiLi42EET_v"}


def test_explicit_type_prefix_converts_but_remaining_types_are_deduced():
    parser, tree = parse("template<class T,int N,class U> T add(T x,U y){return x+T(y)+T(N);} "
                         "int run(){return add<long,2>(10,30);}")
    origin = parser.scope.lookup("add").overloads[0]
    assert tuple(origin.instances) == (("long", IntegralArgument("int", 2, 32, False), "int"),)
    assert "_Z3addIlLi2EiET_S0_T1_" in bodies(tree)


def test_empty_explicit_list_excludes_nontemplate_candidate():
    _, tree = parse("template<class T> int f(T x){return 42;} int f(int x){return 9;} "
                    "int run(){return f<>(1);}")
    call = bodies(tree)["_Z3runv"].body.block_items[0].expr
    assert call.name.name == "_Z1fIiEiT_"


def test_explicit_value_prefix_allows_later_type_deduction():
    parser, _ = parse("template<int N,class T> T f(T x){return x+T(N);} int run(){return f<2>(40);}")
    origin = parser.scope.lookup("f").overloads[0]
    assert tuple(origin.instances) == ((IntegralArgument("int", 2, 32, False), "int"),)


def test_value_dependent_nested_call_and_definition_point_constant_binding():
    source = ("const int step=2; template<int N> int inner(){return N;} "
              "template<int N> int outer(){return inner<N+step>();} "
              "int run(){const int step=100;return outer<40>();}")
    parser, tree = parse(source)
    entries = tuple(parser.instance_registry.entries.values())
    assert [entry.key[0].name for entry in entries] == ["outer", "inner"]
    assert [entry.key[1][0].value for entry in entries] == [40, 42]
    assert all(entry.state is InstanceState.COMPLETE for entry in entries)
    assert set(bodies(tree)) == {"_Z3runv", "_Z5outerILi40EEiv", "_Z5innerILi42EEiv"}


def test_value_dependent_calls_keep_definition_point_overload_snapshot():
    parser, tree = parse("template<int N> int tag(long x){return N+1;} "
                         "template<int N> int f(){return tag<N>(0);} "
                         "template<int N> int tag(int x){return N+100;} int run(){return f<41>();}")
    call = bodies(tree)["_Z1fILi41EEiv"].body.block_items[0].expr
    assert call.name.name == "_Z3tagILi41EEil"
    assert len(parser.scope.lookup("tag").overloads) == 2


def test_recursive_same_value_uses_one_registry_placeholder():
    parser, _ = parse("template<int N> int f(int x){if(x==0)return N;return f<N>(x-1);} "
                      "int run(){return f<42>(3);}")
    assert len(parser.instance_registry.entries) == 1


@pytest.mark.parametrize("expression,value", (
    ("(8 >> 1)", 4), ("(3 > 2)", 1), ("1 < 2", 1),
    ("(1 < 2) == (3 > 2)", 1), ("int(4)", 4),
    ("(int())", 0), ("static_cast<int>(4u)", 4), ("(0 && (1/0))", 0),
))
def test_template_closing_delimiters_do_not_steal_nested_expression_operators(expression, value):
    parser, _ = parse("template<int N> int f(){return N;} int run(){return f<" + expression + ">();}")
    argument = next(iter(parser.scope.lookup("f").overloads[0].instances))[0]
    assert argument.value == value


def test_target_width_is_not_host_python_width():
    source = "template<unsigned long N> int f(){return 1;} int run(){return f<4294967296ull>();}"
    parser, _ = parse(source)
    assert next(iter(parser.scope.lookup("f").overloads[0].instances))[0].width == 64
    with pytest.raises(CppDiagnostic) as error:
        parse(source, "x86_64-pc-windows-msvc")
    assert error.value.code == "cpp.narrowing-template-argument"


NEGATIVE_CASES = (
    ("narrow-unsigned", "template<unsigned N> int f(){return 1;} int run(){return f<-1>();}", "cpp.narrowing-template-argument"),
    ("narrow-bool", "template<bool N> int f(){return 1;} int run(){return f<2>();}", "cpp.narrowing-template-argument"),
    ("narrow-character", "template<unsigned char N> int f(){return 1;} int run(){return f<256>();}", "cpp.narrowing-template-argument"),
    ("narrow-signed", "template<int N> int f(){return 1;} int run(){return f<2147483648ll>();}", "cpp.narrowing-template-argument"),
    ("float-conversion", "template<int N> int f(){return 1;} int run(){return f<1.0>();}", "cpp.invalid-template-argument"),
    ("runtime-object", "template<int N> int f(){return 1;} int run(){int n=1;return f<n>();}", "cpp.nonconstant-template-argument"),
    ("division-by-zero", "template<int N> int f(){return 1;} int run(){return f<(1/0)>();}", "cpp.nonconstant-template-argument"),
    ("overflow", "template<int N> int f(){return 1;} int run(){return f<(2147483647+1)>();}", "cpp.nonconstant-template-argument"),
    ("not-type", "template<class T> int f(){return 1;} int run(){return f<1>();}", "cpp.template-argument-kind"),
    ("not-value", "template<int N> int f(){return 1;} int run(){return f<int>();}", "cpp.template-argument-kind"),
    ("too-many", "template<int N> int f(){return 1;} int run(){return f<1,2>();}", "cpp.template-argument-count"),
    ("missing-value", "template<int N> int f(int x){return x+N;} int run(){return f(42);}", "cpp.unsupported.non-type-template-deduction"),
    ("missing-value-empty-list", "template<int N> int f(){return N;} int run(){return f<>();}", "cpp.unsupported.non-type-template-deduction"),
    ("pointer-parameter", "template<int *P> int f(){return 1;}", "cpp.unsupported.non-type-template-parameters"),
    ("reference-parameter", "template<int &P> int f(){return 1;}", "cpp.unsupported.non-type-template-parameters"),
    ("float-parameter", "template<double V> int f(){return 1;}", "cpp.unsupported.non-type-template-parameters"),
    ("enum-parameter", "enum E{one=1}; template<E N> int f(){return 1;}", "cpp.unsupported.enum-templates"),
    ("value-default", "template<int N=1> int f(){return N;}", "cpp.unsupported.template-default-arguments"),
    ("dependent-default", "template<int N,int M=N+1> int f(){return M;}", "cpp.unsupported.template-default-arguments"),
    ("shadow-function-parameter", "template<int N> int f(int N){return N;}", "cpp.template-parameter-shadowing"),
    ("shadow-local", "template<int N> int f(){{int N=1;}return N;}", "cpp.template-parameter-shadowing"),
    ("shadow-type-parameter", "template<class T,int T> int f(){return 1;}", "cpp.template-parameter-shadowing"),
    ("value-parameter-is-not-object", "template<int N> int f(){N=1;return N;}", "cpp.not-lvalue"),
    ("nondependent-bad-expression", "template<int N> int f(){return N;} template<class T> int unused(){return f<(1/0)>();}", "cpp.nonconstant-template-argument"),
    ("dependent-static-assert", "template<int N> int f(){static_assert(N>0);return N;}", "cpp.unsupported.template-static-assert"),
    ("dependent-array-bound", "template<int N> int f(){int values[N];return 1;}", "cpp.unsupported.array-templates"),
    ("dependent-case-label", "template<int N> int f(int x){switch(x){case N:return 1;default:return 0;}}", "cpp.unsupported.switch-templates"),
    ("dependent-sizeof", "template<int N> int f(){return sizeof(N);}", "cpp.unsupported.sizeof-templates"),
    ("type-id-wins-empty-functional-cast", "template<int N> int f(){return N;} int run(){return f<int()>();}", "cpp.unsupported.template-type-arguments"),
    ("type-id-wins-function-parameter", "template<int N> int f(){return N;} int run(){return f<int(int)>();}", "cpp.unsupported.template-type-arguments"),
    ("array-type-argument", "template<class T> int f(){return 1;} int run(){return f<int[3]>();}", "cpp.unsupported.template-type-arguments"),
    ("cv-type-argument", "template<class T> int f(){return 1;} int run(){return f<const int>();}", "cpp.unsupported.template-type-arguments"),
    ("pointer-type-argument", "template<class T> int f(){return 1;} int run(){return f<int*>();}", "cpp.unsupported.template-type-arguments"),
    ("template-id-object", "template<int N> int f(){return N;} int run(){return f<1>;}", "cpp.unsupported.template-id-values"),
)


@pytest.mark.parametrize("name,source,code", NEGATIVE_CASES, ids=[row[0] for row in NEGATIVE_CASES])
def test_rejected_boundaries_are_explicit_and_source_located(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        parse(source)
    assert error.value.code == code
    assert error.value.coord.file == "integral-template.cpp"


CASES = (
    ("integral-direct", "template<int N> int value(){return N;} int cpp_test_entry(){return value<42>();}"),
    ("mixed-explicit-deduced", "template<int N,class T> T add(T x){return x+T(N);} int cpp_test_entry(){return add<2>(40);}"),
    ("mixed-dependent-value-type", "template<class T,T N> T add(T x){return x+N;} int cpp_test_entry(){return int(add<long,2>(40));}"),
    ("bool-preserves-overload-type", "int tag(bool x){return 42;} int tag(int x){return 9;} template<bool N> int f(){return tag(N);} int cpp_test_entry(){return f<true>();}"),
    ("negative-value", "template<int N> int value(){return N;} int cpp_test_entry(){return 49+value<-7>();}"),
    ("unsigned-full-width", "template<unsigned long long N> int f(){return (N>>63)==1;} int cpp_test_entry(){return 41+f<18446744073709551615ull>();}"),
    ("signed-minimum", "template<long long N> int f(){return N<0;} int cpp_test_entry(){return 41+f<(-9223372036854775807ll-1)>();}"),
    ("nested-value-expression", "const int step=2;template<int N> int inner(){return N;} template<int N> int outer(){return inner<N+step>();} int cpp_test_entry(){const int step=100;return outer<40>();}"),
    ("renamed-forward-definition", "template<class T,T N> T f();int cpp_test_entry(){return f<int,42>();}template<class U,U M> U f(){return M;}"),
    ("definition-overload-snapshot", "template<int N> int tag(long x){return N+1;}template<int N> int f(){return tag<N>(0);}template<int N> int tag(int x){return N+100;}int cpp_test_entry(){return f<41>();}"),
    ("recursive-constant-identity", "template<int N> int f(int x){if(x==0)return N;return f<N>(x-1);}int cpp_test_entry(){return f<42>(3);}"),
    ("enum-to-integral", "enum E{answer=42};template<int N> int f(){return N;}int cpp_test_entry(){return f<answer>();}"),
    ("dependent-condition", "template<int N> int f(){if(N>0)return N;return 9;}int cpp_test_entry(){return f<42>();}"),
    ("target-size-alias", "typedef unsigned long count;template<class T,count N> T f(T x){return x+T(N);}int cpp_test_entry(){return f<int,2>(40);}"),
    ("parenthesized-delimiters", "template<int N> int f(){return N;}int cpp_test_entry(){return f<(84 >> 1)>();}"),
    ("converted-reuse", "template<unsigned long N> int f(){return int(N);}int cpp_test_entry(){return f<20+1>()+f<21u>();}"),
)


@pytest.mark.parametrize("name,source", CASES, ids=[row[0] for row in CASES])
def test_original_programs_form_real_typed_instances(name, source):
    parser, tree = parse(source)
    assert parser.instance_registry.entries
    assert all(entry.state is InstanceState.COMPLETE for entry in parser.instance_registry.entries.values())
    assert "_Z14cpp_test_entryv" in bodies(tree)
