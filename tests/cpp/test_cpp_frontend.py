"""Host-development frontend checks, NOT pcc1/native qualification.

No external C++ compiler is used. The lowering integration test exercises the
actual existing CCodeGenerator, not an interpreter of the expected result.
"""

import unittest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.parser import CppToken, TokenCursor
from pcc.frontends.cpp.sema import Scope, Symbol


class CppScopeTests(unittest.TestCase):
    def test_value_hides_outer_type_without_searching_past_it(self):
        outer = Scope()
        outer.declare(Symbol("T", "type", Coord("scope.cpp", 1)))
        inner = Scope(outer)
        self.assertTrue(inner.is_type_name("T"))
        inner.declare(Symbol("T", "value", Coord("scope.cpp", 2)))
        self.assertFalse(inner.is_type_name("T"))
        self.assertTrue(outer.is_type_name("T"))

    def test_tentative_cursor_restores_nested_success_and_exception(self):
        coord = Coord("scope.cpp", 1)
        cursor = TokenCursor([CppToken("ID", "T", coord),
                              CppToken("ID", "a", coord),
                              CppToken("EOF", "<eof>", coord)])
        with cursor.tentative():
            cursor.take()
            with cursor.tentative():
                cursor.take()
            self.assertEqual(cursor.position, 1)
        self.assertEqual(cursor.position, 0)
        with self.assertRaisesRegex(RuntimeError, "abort"):
            with cursor.tentative():
                cursor.take()
                raise RuntimeError("abort")
        self.assertEqual(cursor.position, 0)


class CppParserTests(unittest.TestCase):
    def parse(self, source):
        return CppParser().parse(source, "test.cpp")

    def diagnostic(self, source, code):
        with self.assertRaises(CppDiagnostic) as context:
            self.parse(source)
        self.assertEqual(context.exception.code, code)
        self.assertEqual(context.exception.coord.file, "test.cpp")
        return context.exception

    def test_type_name_selects_parenthesized_declaration(self):
        ast = self.parse("using T = int; int main() { T(a); a = 7; return a; }")
        body = ast.ext[0].body.block_items
        self.assertIsInstance(body[0], c_ast.Decl)
        self.assertEqual(body[0].name, "a")
        self.assertIsInstance(body[1], c_ast.Assignment)

    def test_failed_declaration_lookahead_becomes_conversion(self):
        ast = self.parse("using T=int; int main(){int a=1; T(a+2); return T(7);}")
        body = ast.ext[0].body.block_items
        self.assertIsInstance(body[1], c_ast.Cast)
        self.assertIsInstance(body[2].expr, c_ast.Cast)

    def test_declaration_lookahead_does_not_install_names(self):
        parser = CppParser()
        parser.scope = Scope()
        parser.scope.declare(Symbol("T", "type", Coord("test.cpp", 1)))
        parser.cursor = TokenCursor(parser._tokenize("T(a);", "test.cpp"))
        before = dict(parser.scope.names)
        self.assertTrue(parser._is_declaration())
        self.assertEqual(parser.cursor.position, 0)
        self.assertEqual(parser.scope.names, before)

    def test_inner_value_hides_type_and_leaving_block_restores_type(self):
        ast = self.parse("using T=int; int main(){ {int T=5; T=T+1;} T(a); return 0;}")
        body = ast.ext[0].body.block_items
        self.assertIsInstance(body[0].block_items[1], c_ast.Assignment)
        self.assertIsInstance(body[1], c_ast.Decl)
        self.diagnostic("using T=int; int main(){int T=3; T(4);}", "cpp.not-callable")

    def test_local_alias_does_not_escape(self):
        self.diagnostic("int main(){ {using T=int; T a=3;} T x=4;}", "cpp.undeclared")

    def test_function_and_alias_are_resolved_before_c_ast(self):
        ast = self.parse("using T=int; int add(T x,T y){return x+y;} "
                         "int main(){T a(3); return add(a,4);}")
        self.assertEqual(ast.ext[0].decl.name, "_Z3addii")
        call = ast.ext[1].body.block_items[1].expr
        self.assertEqual(call.name.name, ast.ext[0].decl.name)
        self.assertEqual(ast.ext[0].decl.type.args.params[0].type.type.names, ["int"])

    def test_prototype_then_definition_reuses_function(self):
        ast = self.parse("int f(int); int f(int x){return x;} int main(){return f(7);}")
        self.assertEqual(ast.ext[0].name, ast.ext[1].decl.name)

    def test_main_fallthrough_is_explicit_zero(self):
        ast = self.parse("int main() {}")
        self.assertEqual(ast.ext[0].body.block_items[-1].expr.value, "0")

    def test_initializer_point_of_declaration_hides_type(self):
        ast = self.parse("using T=int; int main(){int T=3; return T;}")
        self.assertIsInstance(ast.ext[0].body.block_items[1].expr, c_ast.ID)

    def test_parameter_scope_hides_type_in_later_parameters(self):
        self.diagnostic("using T=int; int f(int T, T x);", "cpp.not-a-type")
        self.diagnostic("int f(int x,int x);", "cpp.redeclaration")
        ast = self.parse("using T=int; int f(int T); int g(T x){return x;}")
        self.assertEqual(ast.ext[1].decl.type.args.params[0].type.type.names, ["int"])

    def test_cpp_main_use_and_assignment_result_lvalue_are_not_silently_c(self):
        self.diagnostic("int main(){return main();}", "cpp.main-use")
        self.diagnostic("int main(){int x=1;(x=2)=3;return x;}", "cpp.unsupported.syntax")

    def test_semantic_errors_are_not_forwarded_to_c(self):
        self.diagnostic("int f(int x){int x=3;return x;}", "cpp.redeclaration")
        self.diagnostic("int main(){return missing;}", "cpp.undeclared")
        self.diagnostic("int f(int x){return x;} int main(){return f();}", "cpp.no-viable-overload")
        self.diagnostic("int f(){return 1;} int f(){return 2;}", "cpp.redefinition")

    def test_arity_overloads_and_unimplemented_class_templates(self):
        ast = self.parse("int f(int); int f(int,int); int main(){return f(1,2);}")
        self.assertEqual(ast.ext[2].body.block_items[0].expr.name.name, "_Z1fii")
        self.diagnostic("template<class T> struct Box {};", "cpp.unsupported.classes")

    def test_lifetimes_and_exceptions_are_never_dropped(self):
        self.diagnostic("struct Guard { ~Guard(); };", "cpp.unsupported.classes")
        self.diagnostic("int main(){throw 1;}", "cpp.unsupported.exceptions")
        self.diagnostic("int main(){try{return 1;}catch(...){return 0;}}",
                        "cpp.unsupported.exceptions")

    def test_references_and_function_pointer_conversions_are_explicit(self):
        self.diagnostic("int f(int &x){return x;}", "cpp.unsupported.references")
        self.diagnostic("int f(){return 1;} int main(){return f;}", "cpp.unsupported.syntax")

    def test_preprocessor_and_unknown_trailing_source_do_not_disappear(self):
        self.diagnostic("#include <vector>\nint main(){}", "cpp.unsupported.preprocessor")
        self.diagnostic("int main(){return 0;} namespace n{struct Guard{~Guard();};}", "cpp.unsupported.classes")

    def test_reusing_parser_does_not_retain_prior_names(self):
        parser = CppParser()
        parser.parse("using T=int; int f(T a){return a;}")
        with self.assertRaises(CppDiagnostic):
            parser.parse("int main(){T a;}")

    def test_integer_literals_and_precedence(self):
        ast = self.parse("int main(){return 0x10 + 0b10 * 03;}")
        expr = ast.ext[0].body.block_items[0].expr
        self.assertEqual(expr.op, "+")
        self.assertEqual(expr.left.value, "16")
        self.assertEqual(expr.right.op, "*")
        self.assertEqual(expr.right.right.value, "3")
        large = self.parse("long f(){return 2147483648;}")
        self.assertEqual(large.ext[0].body.block_items[0].expr.value, "2147483648l")
        unsigned = self.parse("unsigned f(){return 1u;}")
        self.assertEqual(unsigned.ext[0].body.block_items[0].expr.value, "1u")
        self.diagnostic("int main(){return 09;}", "cpp.invalid-literal")


class CppCLoweringTests(unittest.TestCase):
    def test_resolved_ast_uses_existing_c_codegen_and_owned_ir(self):
        from pcc.frontends.c.codegen.c_codegen import CCodeGenerator

        ast = CppParser().parse("using T=int; int add(T x,T y){return x+y;} "
                                "int main(){T(a); a=3; return add(a,4);}", "lower.cpp")
        generator = CCodeGenerator()
        generator.generate_code(ast)
        ir_text = str(generator.module)
        self.assertIn("_Z3addii", ir_text)
        self.assertIn("call i32", ir_text)
        self.assertIn("add i32", ir_text)
        self.assertEqual(type(generator.module).__module__, "pcc.ir.ir")


if __name__ == "__main__":
    unittest.main()
