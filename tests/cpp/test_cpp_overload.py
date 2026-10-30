"""Host-development arithmetic Sema tests; not native/fixed-point qualification.

These independently authored cases call the selected functions and inspect the
actual C lowering. Execution/byte-oracle cases belong to the integration harness.
"""

import unittest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ArithmeticTypes, canonical_type


LINUX = "x86_64-unknown-linux-gnu"
WINDOWS = "x86_64-pc-windows-msvc"


class ArithmeticTypeTests(unittest.TestCase):
    def test_canonical_identity_is_not_storage_width(self):
        types = ArithmeticTypes(LINUX)
        self.assertEqual(types.width("long"), types.width("long long"))
        self.assertEqual(types.conversion_rank("long", "long long"), 2)
        self.assertEqual(canonical_type(["int", "long", "signed"]), "long")
        self.assertEqual(canonical_type(["long", "unsigned", "long"]), "unsigned long long")
        for words in (["int", "int"], ["signed", "unsigned"], ["long", "float"],
                      ["long", "long", "double"], ["unsigned", "bool"]):
            with self.subTest(words=words), self.assertRaises(ValueError):
                canonical_type(words)

    def test_integral_promotions_are_target_owned(self):
        types = ArithmeticTypes(LINUX)
        for name in ("bool", "char", "signed char", "unsigned char", "short", "unsigned short"):
            self.assertEqual(types.promote(name), "int")
        self.assertEqual(types.promote("unsigned int"), "unsigned int")
        self.assertFalse(types.unsigned("char"))
        self.assertTrue(ArithmeticTypes("aarch64-unknown-linux-gnu").unsigned("char"))

    def test_usual_conversions_use_rank_and_target_representability(self):
        linux = ArithmeticTypes(LINUX)
        windows = ArithmeticTypes(WINDOWS)
        self.assertEqual(linux.common("unsigned int", "long"), "long")
        self.assertEqual(windows.common("unsigned int", "long"), "unsigned long")
        self.assertEqual(linux.common("unsigned long", "long long"), "unsigned long long")
        self.assertEqual(windows.common("unsigned long", "long long"), "long long")
        self.assertEqual(linux.common("short", "unsigned short"), "int")
        self.assertEqual(linux.common("long long", "float"), "float")

    def test_literal_types_keep_source_base_suffix_and_target_rules(self):
        linux = ArithmeticTypes(LINUX)
        windows = ArithmeticTypes(WINDOWS)
        self.assertEqual(linux.integer_literal("0x80000000"), ("unsigned int", "2147483648u"))
        self.assertEqual(linux.integer_literal("2147483648"), ("long", "2147483648l"))
        self.assertEqual(windows.integer_literal("2147483648"), ("long long", "2147483648ll"))
        self.assertEqual(linux.integer_literal("01ULL"), ("unsigned long long", "1ull"))
        for raw in ("09", "1lL", "1uuu", "18446744073709551616ull"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                linux.integer_literal(raw)

    def test_floating_literal_syntax_is_not_assumed_from_lexer_token_kind(self):
        types = ArithmeticTypes(LINUX)
        for raw, expected in (("1.5f", "float"), (".25", "double"), ("1e+3", "double"),
                              ("0x1p-2", "double"), ("0x.8p+1F", "float")):
            self.assertEqual(types.floating_literal_type(raw), expected)
        for raw in ("0x1.0", "0x.p1", "1e+", "1.e", "1f", "1.2.3", "١.٠"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                types.floating_literal_type(raw)


class OverloadSemaTests(unittest.TestCase):
    def parse(self, source):
        return CppParser(LINUX).parse(source, "overload.cpp")

    def call(self, source):
        return self.parse(source).ext[-1].body.block_items[-1].expr

    def diagnostic(self, source, code):
        with self.assertRaises(CppDiagnostic) as context:
            self.parse(source)
        self.assertEqual(context.exception.code, code)
        self.assertEqual(context.exception.coord.file, "overload.cpp")

    def test_exact_match_and_separate_function_bodies(self):
        ast = self.parse("int pick(int x){return 11;} int pick(long x){return 22;} "
                         "int run(){return pick(7l);}")
        self.assertEqual(ast.ext[0].decl.name, "_Z4picki")
        self.assertEqual(ast.ext[1].decl.name, "_Z4pickl")
        self.assertEqual(ast.ext[2].body.block_items[0].expr.name.name, "_Z4pickl")

    def test_integral_promotion_beats_conversion_and_is_lowered_explicitly(self):
        call = self.call("int f(int x){return 11;} int f(long x){return 22;} "
                         "int run(){short x=2;return f(x);}")
        self.assertEqual(call.name.name, "_Z1fi")
        self.assertIsInstance(call.args.exprs[0], c_ast.Cast)
        self.assertEqual(call.args.exprs[0].to_type.type.type.names, ["int"])

    def test_float_promotion_beats_integral_conversion(self):
        call = self.call("int f(double x){return 11;} int f(long x){return 22;} "
                         "int run(){return f(2.5f);}")
        self.assertEqual(call.name.name, "_Z1fd")
        self.assertEqual(call.args.exprs[0].to_type.type.type.names, ["double"])

    def test_extended_float_value_abi_gap_is_diagnosed_before_c_lowering(self):
        self.diagnostic("int f(double x){return 11;} int f(long double x){return 22;} "
                        "int run(){return f(2.5f);}", "cpp.unsupported.extended-float")
        self.diagnostic("long double f(){return 1;}", "cpp.unsupported.extended-float")
        self.diagnostic("int run(){long double x=1;return 0;}", "cpp.unsupported.extended-float")
        self.diagnostic("int run(){return 1.0L;}", "cpp.unsupported.extended-float")
        self.assertTrue(ArithmeticTypes("aarch64-apple-darwin").value_lowering_supported("long double"))

    def test_same_width_long_long_remains_a_distinct_overload(self):
        call = self.call("int f(long x){return 11;} int f(long long x){return 22;} "
                         "int run(){return f(1ll);}")
        self.assertEqual(call.name.name, "_Z1fx")

    def test_bool_identity_and_promotion(self):
        call = self.call("int f(bool x){return 11;} int f(int x){return 22;} "
                         "int run(){return f(true);}")
        self.assertEqual(call.name.name, "_Z1fb")
        promoted = self.call("int f(int x){return 11;} int f(long x){return 22;} "
                             "int run(){return f(true);}")
        self.assertEqual(promoted.name.name, "_Z1fi")

    def test_canonical_alias_signature_merges_redeclaration(self):
        ast = self.parse("using I=signed int; int f(I); int f(int x){return x;} "
                         "int run(){return f(4);}")
        self.assertEqual(ast.ext[0].name, ast.ext[1].decl.name)
        self.assertEqual(ast.ext[2].body.block_items[0].expr.name.name, "_Z1fi")
        self.diagnostic("using I=int; int f(I x){return 1;} int f(int x){return 2;}",
                        "cpp.redefinition")

    def test_return_type_does_not_form_an_overload(self):
        self.diagnostic("int f(int); long f(int);", "cpp.return-type-overload")

    def test_same_rank_conversions_remain_ambiguous(self):
        self.diagnostic("int f(long); int f(double); int run(){return f(1);}",
                        "cpp.ambiguous-overload")

    def test_conversion_ranks_are_a_partial_order_not_a_sum(self):
        self.diagnostic("int f(short,int,int); int f(int,long,long); "
                        "int run(){short x=1;return f(x,2l,3l);}", "cpp.ambiguous-overload")
        self.diagnostic("int f(int,long); int f(long,int); int run(){return f(1,2);}",
                        "cpp.ambiguous-overload")

    def test_best_candidate_must_dominate_each_other_candidate(self):
        call = self.call("int f(long x,long y){return 11;} int f(int x,long y){return 22;} "
                         "int f(int x,int y){return 33;} int run(){return f(1,2);}")
        self.assertEqual(call.name.name, "_Z1fii")

    def test_wrong_arity_and_value_shadowing_are_not_candidate_fallbacks(self):
        self.diagnostic("int f(int); int f(long,long); int run(){return f();}",
                        "cpp.no-viable-overload")
        self.diagnostic("int f(int); int run(){int f=2;return f(1);}", "cpp.not-callable")

    def test_arithmetic_result_type_drives_the_next_overload(self):
        call = self.call("int f(long x){return 11;} int f(unsigned long x){return 22;} "
                         "int run(){return f(1u+2l);}")
        self.assertEqual(call.name.name, "_Z1fl")
        self.assertIsInstance(call.args.exprs[0].left, c_ast.Cast)

    def test_assignment_and_return_insert_target_conversions(self):
        ast = self.parse("short f(double x){short s=1;s=x;return x;}")
        body = ast.ext[0].body.block_items
        self.assertEqual(body[1].rvalue.to_type.type.type.names, ["short"])
        self.assertEqual(body[2].expr.to_type.type.type.names, ["short"])

    def test_invalid_types_operands_and_unsupported_semantics_fail_closed(self):
        self.diagnostic("unsigned bool f();", "cpp.invalid-type")
        self.diagnostic("int run(){return 1.0%2;}", "cpp.invalid-operands")
        self.diagnostic("int run(){return ~1.0;}", "cpp.invalid-operands")
        self.diagnostic("double main(){}", "cpp.main-return-type")
        self.diagnostic("int f(volatile int x);", "cpp.unsupported.volatile")
        self.diagnostic("int f(int&& x);", "cpp.unsupported.references")
        self.diagnostic("template<class T> struct Box {};", "cpp.unsupported.classes")
        for raw in ("0x1.0", "0x.p1", "1e+", "1.e"):
            self.diagnostic("double f(){return " + raw + ";}", "cpp.invalid-literal")

    def test_selected_overloads_reach_actual_c_codegen(self):
        from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
        from pcc.frontends.c.passes.context import PassContext

        ast = self.parse("int f(int x){return 11;} int f(double x){return 22;} "
                         "int run(){short x=2;return f(x)+f(1.5f);}")
        generator = CCodeGenerator(pass_ctx=PassContext(target_triple=LINUX))
        generator.generate_code(ast)
        ir = str(generator.module)
        self.assertIn("call i32 (i32) @_Z1fi", ir)
        self.assertIn("call i32 (double) @_Z1fd", ir)
        self.assertIn("sext i16", ir)
        self.assertIn("fpext float", ir)
        self.assertEqual(type(generator.module).__module__, "pcc.ir.ir")


if __name__ == "__main__":
    unittest.main()
