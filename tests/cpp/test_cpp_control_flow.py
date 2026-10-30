"""Scalar C++ control-flow Sema and C AST tests; host-development evidence."""

import unittest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser


TARGET = "x86_64-unknown-linux-gnu"


class ScalarControlFlowTests(unittest.TestCase):
    def parse(self, source):
        self.parser = CppParser(TARGET)
        return self.parser.parse(source, "control.cpp")

    def diagnostic(self, source, code):
        with self.assertRaises(CppDiagnostic) as error:
            self.parse(source)
        self.assertEqual(error.exception.code, code)
        self.assertEqual(error.exception.coord.file, "control.cpp")
        return error.exception

    def test_relational_equality_logical_and_not_results_select_bool_overload(self):
        for expression in ("1<2", "1<=2", "1>2", "1>=2", "1==2", "1!=2",
                           "1&&2", "1||2", "!2", "!0.5"):
            with self.subTest(expression=expression):
                ast = self.parse("int tag(bool x){return 11;} int tag(int x){return 22;} "
                                 "int run(){return tag(" + expression + ");}")
                call = ast.ext[-1].body.block_items[0].expr
                self.assertEqual(call.name.name, "_Z3tagb")
                self.assertIsInstance(call.args.exprs[0], c_ast.Cast)
                self.assertEqual(call.args.exprs[0].to_type.type.type.names, ["_Bool"])

    def test_comparison_operands_use_usual_arithmetic_conversions(self):
        ast = self.parse("bool run(){return 1u<2l;}")
        comparison = ast.ext[0].body.block_items[0].expr.expr
        self.assertEqual(comparison.left.to_type.type.type.names, ["long"])
        self.assertEqual(comparison.right.value, "2l")

    def test_comparison_bool_promotes_for_arithmetic(self):
        ast = self.parse("int tag(bool x){return 11;} int tag(int x){return 22;} "
                         "int run(){return tag((1<2)+true);}")
        self.assertEqual(ast.ext[-1].body.block_items[0].expr.name.name, "_Z3tagi")

    def test_precedence_matches_cplusplus_pratt_table(self):
        ast = self.parse("bool run(){return 1+2<<1<8==true&1^0|0&&2||3;}")
        node = ast.ext[0].body.block_items[0].expr.expr
        self.assertEqual(node.op, "||")
        self.assertEqual(node.left.expr.op, "&&")
        # Explicit conversion nodes do not alter which operator binds first.
        ast = self.parse("int run(){return 1+2<<1;}")
        node = ast.ext[0].body.block_items[0].expr
        self.assertEqual(node.op, "<<")
        self.assertEqual(node.left.op, "+")

    def test_shift_promotes_each_operand_independently_and_returns_left_type(self):
        ast = self.parse("int tag(int x){return 11;} int tag(long x){return 22;} "
                         "int run(){short x=2;return tag(x<<1l);}")
        call = ast.ext[-1].body.block_items[-1].expr
        self.assertEqual(call.name.name, "_Z3tagi")
        shift = call.args.exprs[0]
        self.assertEqual(shift.left.to_type.type.type.names, ["int"])
        self.assertEqual(shift.right.value, "1l")
        self.diagnostic("int run(){return 1.5<<2;}", "cpp.invalid-operands")

    def test_short_circuit_remains_branching_c_ast_not_eager_helper_calls(self):
        ast = self.parse("int run(){int x=0;false&&(x=5);true||(x=7);return x;}")
        statements = ast.ext[0].body.block_items
        self.assertEqual(statements[1].expr.op, "&&")
        self.assertEqual(statements[2].expr.op, "||")
        self.assertIsInstance(statements[1].expr.right.expr, c_ast.Assignment)

    def test_alternative_operator_tokens_have_identical_semantics(self):
        ast = self.parse("bool run(){return not false and (3 bitand 1) not_eq 0 or false;}")
        self.assertEqual(ast.ext[0].body.block_items[0].expr.expr.op, "||")
        self.diagnostic("int and(){return 0;}", "cpp.unsupported.references")
        self.diagnostic("int run(){int not=0;return 0;}", "cpp.unsupported.syntax")

    def test_if_else_condition_is_contextually_converted_to_bool(self):
        ast = self.parse("int run(){if(0.5)return 11;else return 22;}")
        statement = ast.ext[0].body.block_items[0]
        self.assertIsInstance(statement, c_ast.If)
        self.assertEqual(statement.cond.to_type.type.type.names, ["_Bool"])
        self.assertEqual(statement.iftrue.expr.value, "11")
        self.assertEqual(statement.iffalse.expr.value, "22")

    def test_dangling_else_binds_to_nearest_if(self):
        ast = self.parse("int run(){if(1)if(0)return 11;else return 22;return 33;}")
        outer = ast.ext[0].body.block_items[0]
        self.assertIsNone(outer.iffalse)
        self.assertEqual(outer.iftrue.iffalse.expr.value, "22")

    def test_unbraced_selection_declarations_have_independent_scopes(self):
        self.parse("int run(){int x=7;if(1)int x=2;else int x=3;return x;}")
        self.diagnostic("int run(){if(1)int x=2;return x;}", "cpp.undeclared")
        self.diagnostic("int run(){if(1)using T=int;T x=2;return x;}", "cpp.undeclared")

    def test_while_break_continue_lower_to_existing_c_nodes(self):
        ast = self.parse("int run(){int x=0;while(x<7){x=x+1;if(x==2)continue;"
                         "if(x==5)break;}return x;}")
        loop = ast.ext[0].body.block_items[1]
        self.assertIsInstance(loop, c_ast.While)
        self.assertIsInstance(loop.stmt.block_items[1].iftrue, c_ast.Continue)
        self.assertIsInstance(loop.stmt.block_items[2].iftrue, c_ast.Break)

    def test_loop_scope_and_jump_target_do_not_leak(self):
        self.diagnostic("int run(){while(0)int x=2;return x;}", "cpp.undeclared")
        self.diagnostic("int run(){break;return 0;}", "cpp.jump-outside-loop")
        self.diagnostic("int run(){while(0){}continue;return 0;}", "cpp.jump-outside-loop")
        self.parse("void f(){while(0){while(1){break;}continue;}}")

    def test_unimplemented_condition_declarations_init_and_constexpr_are_explicit(self):
        self.diagnostic("int run(){if(int x=2)return x;return 0;}",
                        "cpp.unsupported.condition-declaration")
        self.diagnostic("int run(){while(int x=2){return x;}return 0;}",
                        "cpp.unsupported.condition-declaration")
        self.diagnostic("int run(){if constexpr(true)return 1;return 0;}", "cpp.unsupported.syntax")
        self.diagnostic("int run(){if(1;true)return 1;return 0;}", "cpp.unsupported.syntax")

    def test_void_condition_and_void_logical_operand_are_rejected(self):
        for statement in ("if(f())return 1;", "while(f())return 1;", "!f();",
                          "f()&&true;", "f()==f();"):
            with self.subTest(statement=statement):
                self.diagnostic("void f(){}int run(){" + statement + "return 0;}", "cpp.void-value")

    def test_boolean_result_is_not_an_assignable_lvalue(self):
        self.diagnostic("int run(){(1<2)=true;return 0;}", "cpp.not-lvalue")
        self.diagnostic("int run(){(!0)=true;return 0;}", "cpp.not-lvalue")

    def test_dependent_comparison_result_selects_bool_only_after_instantiation(self):
        ast = self.parse("int tag(bool x){return 11;} int tag(int x){return 22;} "
                         "template<class T> int compare(T x){return tag(x<T(3));} "
                         "int run(){return compare(2);}")
        instance = ast.ext[-1]
        self.assertEqual(instance.decl.name, "_Z7compareIiEiT_")
        self.assertEqual(instance.body.block_items[0].expr.name.name, "_Z3tagb")

    def test_template_while_if_break_continue_and_return_instantiate(self):
        ast = self.parse("template<class T> T count(T n){T x=0;while(x<n){x=x+T(1);"
                         "if(x==T(2))continue;if(x==T(5))break;}return x;} "
                         "int run(){return count(8);}")
        instance = ast.ext[-1]
        self.assertEqual(instance.decl.name, "_Z5countIiET_S0_")
        self.assertIsInstance(instance.body.block_items[1], c_ast.While)
        self.assertEqual(instance.body.block_items[0].type.type.names, ["int"])

    def test_dependent_condition_is_checked_again_after_substitution(self):
        self.diagnostic("void f(int);template<class T> int run(T x){if(f(x))return 1;return 0;} "
                        "int entry(){return run(2);}", "cpp.void-value")

    def test_actual_c_ir_contains_short_circuit_and_loop_branches(self):
        from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
        from pcc.frontends.c.passes.context import PassContext

        ast = self.parse("int run(){int x=0;while(x<5){x=x+1;if(x==2)continue;"
                         "if(x==4)break;}false&&(x=9);return x;}")
        generator = CCodeGenerator(pass_ctx=PassContext(target_triple=TARGET))
        generator.generate_code(ast)
        ir = str(generator.module)
        self.assertIn("and_rhs", ir)
        self.assertIn("and_merge", ir)
        self.assertIn("afterloop", ir)
        self.assertIn("br i1", ir)
        self.assertEqual(type(generator.module).__module__, "pcc.ir.ir")


if __name__ == "__main__":
    unittest.main()
