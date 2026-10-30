"""Void/return Sema and C-lowering checks, not native compiler qualification."""

import unittest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.templates import Pattern
from pcc.frontends.cpp.types import ArithmeticTypes, canonical_type


TARGET = "x86_64-unknown-linux-gnu"


class VoidSemaTests(unittest.TestCase):
    def parse(self, source):
        self.parser = CppParser(TARGET)
        return self.parser.parse(source, "void.cpp")

    def bodies(self, ast):
        return {node.decl.name: node for node in ast.ext if isinstance(node, c_ast.FuncDef)}

    def diagnostic(self, source, code):
        with self.assertRaises(CppDiagnostic) as error:
            self.parse(source)
        self.assertEqual(error.exception.code, code)
        self.assertEqual(error.exception.coord.file, "void.cpp")
        return error.exception

    def test_void_identity_is_not_an_arithmetic_conversion(self):
        self.assertEqual(canonical_type(["void"]), "void")
        types = ArithmeticTypes(TARGET)
        self.assertFalse(types.integral("void"))
        for source, target in (("void", "void"), ("void", "int"), ("int", "void")):
            self.assertIsNone(types.conversion_rank(source, target))
        for words in (["int", "void"], ["void", "void"], ["unsigned", "void"]):
            with self.subTest(words=words), self.assertRaises(ValueError):
                canonical_type(words)

    def test_void_function_empty_return_and_implicit_fallthrough(self):
        ast = self.parse("void empty(){} void done(){return;} int run(){empty();done();return 7;}")
        functions = self.bodies(ast)
        self.assertEqual(functions["_Z5emptyv"].decl.type.type.type.names, ["void"])
        self.assertIsNone(functions["_Z4donev"].body.block_items[0].expr)
        self.assertEqual(functions["_Z3runv"].body.block_items[0].name.name, "_Z5emptyv")

    def test_sole_unnamed_void_and_alias_parameter_list_are_empty_signatures(self):
        ast = self.parse("using V=void; void f(V); void f(void){} void run(){f();}")
        self.assertEqual(ast.ext[0].type.args.params, [])
        self.assertEqual(ast.ext[1].decl.name, "_Z1fv")
        self.assertEqual(len(self.parser.scope.lookup("f").overloads), 1)
        self.assertEqual(self.parser.scope.lookup("f").overloads[0].parameters, ())

    def test_named_or_nonsole_void_parameter_is_not_erased(self):
        for parameters in ("void x", "void,int", "int,void", "V x", "V,int", "int,V"):
            with self.subTest(parameters=parameters):
                self.diagnostic("using V=void; int f(" + parameters + ");", "cpp.invalid-parameter")

    def test_void_object_is_rejected_including_alias_and_ambiguous_declaration(self):
        for source in ("void f(){void x;}", "using V=void;void f(){V x;}",
                       "void f(){void(x);}"):
            with self.subTest(source=source):
                self.diagnostic(source, "cpp.invalid-object-type")

    def test_nonvoid_function_needs_expression_in_return_statement(self):
        self.diagnostic("int f(){return;}", "cpp.return-value-required")
        self.diagnostic("int main(){return;}", "cpp.return-value-required")

    def test_void_function_cannot_implicitly_discard_return_value(self):
        self.diagnostic("void f(){return 42;}", "cpp.return-value-in-void")
        self.diagnostic("int g(){return 42;} void f(){return g();}", "cpp.return-value-in-void")

    def test_void_return_expression_preserves_call(self):
        ast = self.parse("void emit(int); void forward(){return emit(7);}")
        returned = ast.ext[-1].body.block_items[0]
        self.assertIsInstance(returned, c_ast.Return)
        self.assertIsInstance(returned.expr, c_ast.FuncCall)
        self.assertEqual(returned.expr.name.name, "_Z4emiti")
        self.assertEqual(returned.expr.args.exprs[0].value, "7")

    def test_nonvoid_return_cannot_consume_void_call_or_void_cast(self):
        self.diagnostic("void g(){} int f(){return g();}", "cpp.return-void-expression")
        self.diagnostic("int f(){return void();}", "cpp.return-void-expression")

    def test_explicit_void_discard_preserves_assignment(self):
        ast = self.parse("int run(){int x=2;void(x=x+3);return x;}")
        discarded = ast.ext[0].body.block_items[1]
        self.assertIsInstance(discarded, c_ast.Cast)
        self.assertEqual(discarded.to_type.type.type.names, ["void"])
        self.assertIsInstance(discarded.expr, c_ast.Assignment)

    def test_void_prvalue_and_alias_cast_can_be_discarded_or_returned(self):
        ast = self.parse("using V=void; void f(){void();V(1);return V();}")
        self.assertEqual(len(ast.ext[0].body.block_items), 3)
        self.assertIsInstance(ast.ext[0].body.block_items[-1].expr, c_ast.Cast)
        self.parse("void g(){} void f(){void(g());return g();}")

    def test_void_value_is_rejected_before_arithmetic_assignment_argument_conversion(self):
        for body in ("return int(g());", "return +g();", "return g()+1;",
                     "int x=g();return x;", "int x=2;x=g();return x;",
                     "return take(g());"):
            with self.subTest(body=body):
                self.diagnostic("void g(){} int take(int x){return x;} int f(){" + body + "}",
                                "cpp.void-value")

    def test_void_template_signature_and_empty_return_are_instantiated(self):
        ast = self.parse("template<class T> void sink(T x){return;} int run(){sink(3);return 7;}")
        body = self.bodies(ast)["_Z4sinkIiEvT_"]
        self.assertEqual(body.decl.type.type.type.names, ["void"])
        self.assertEqual(body.decl.type.args.params[0].type.type.names, ["int"])
        self.assertIsNone(body.body.block_items[0].expr)

    def test_template_return_void_call_is_instantiated_with_argument_conversion(self):
        ast = self.parse("void emit(long); template<class T> void f(T x){return emit(x);} "
                         "int run(){f(7);return 0;}")
        result = self.bodies(ast)["_Z1fIiEvT_"].body.block_items[0]
        self.assertEqual(result.expr.name.name, "_Z4emitl")
        self.assertIsInstance(result.expr.args.exprs[0], c_ast.Cast)
        self.assertEqual(result.expr.args.exprs[0].to_type.type.type.names, ["long"])

    def test_dependent_void_return_mismatch_is_an_instantiation_error_not_fallback(self):
        self.parse("template<class T> void f(T x){return x;}")
        error = self.diagnostic("template<class T> void f(T x){return x;} void f(long x){} "
                                "int run(){f(7);return 0;}", "cpp.return-value-in-void")
        self.assertIn("while instantiating _Z1fIiEvT_", error.message)
        self.diagnostic("template<class T> void f(T x){return 1;}", "cpp.return-value-in-void")

    def test_dependent_nonvoid_empty_return_is_deferred_not_dropped(self):
        self.parse("template<class T> T f(T x){return;}")
        pattern = self.parser.scope.lookup("f").overloads[0].pattern.children[0]
        self.assertIsInstance(pattern, Pattern)
        self.assertEqual(pattern.children, [])
        error = self.diagnostic("template<class T> T f(T x){return;} int run(){return f(2);}",
                                "cpp.return-value-required")
        self.assertIn("while instantiating", error.message)

    def test_dependent_result_cannot_silently_convert_void_call_to_zero(self):
        self.parse("void g(){} template<class T> T f(T x){return g();}")
        self.diagnostic("void g(){} template<class T> T f(T x){return g();} "
                        "int run(){return f(2);}", "cpp.return-void-expression")

    def test_template_explicit_discard_preserves_dependent_expression(self):
        ast = self.parse("template<class T> T f(T x){void(x=x+T(3));return x;} "
                         "int run(){return f(2);}")
        discarded = self.bodies(ast)["_Z1fIiET_S0_"].body.block_items[0]
        self.assertIsInstance(discarded.expr, c_ast.Assignment)

    def test_void_alias_does_not_make_a_deducible_template_parameter(self):
        self.diagnostic("using V=void; template<class T> void f(V){} void run(){f();}",
                        "cpp.no-viable-overload")

    def test_void_results_reach_existing_c_codegen_with_ret_void(self):
        from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
        from pcc.frontends.c.passes.context import PassContext

        ast = self.parse("void emit(int); template<class T> void f(T x){return emit(x);} "
                         "int run(){f(7);int x=2;void(x=x+3);return x;}")
        generator = CCodeGenerator(pass_ctx=PassContext(target_triple=TARGET))
        generator.generate_code(ast)
        ir = str(generator.module)
        self.assertIn("define void @_Z1fIiEvT_", ir)
        self.assertIn("call void (i32) @_Z4emiti", ir)
        self.assertIn("ret void", ir)
        self.assertEqual(type(generator.module).__module__, "pcc.ir.ir")


if __name__ == "__main__":
    unittest.main()
