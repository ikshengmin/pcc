"""Host-development template Sema/real C-lowering checks, not native evidence."""

import unittest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.templates import Pattern, TypeParameter


TARGET = "x86_64-unknown-linux-gnu"


class FunctionTemplateTests(unittest.TestCase):
    def parse(self, source):
        self.parser = CppParser(TARGET)
        return self.parser.parse(source, "templates.cpp")

    def bodies(self, ast):
        return {node.decl.name: node for node in ast.ext if isinstance(node, c_ast.FuncDef)}

    def diagnostic(self, source, code):
        with self.assertRaises(CppDiagnostic) as context:
            self.parse(source)
        self.assertEqual(context.exception.code, code)
        self.assertEqual(context.exception.coord.file, "templates.cpp")
        return context.exception

    def test_uninstantiated_body_is_typed_pattern_not_c_ast_or_source_text(self):
        ast = self.parse("template<class T> T twice(T x){return x+x;}")
        self.assertEqual(ast.ext, [])
        template = self.parser.scope.lookup("twice").overloads[0]
        self.assertIsInstance(template.template_parameters[0], TypeParameter)
        self.assertIsInstance(template.pattern, Pattern)
        self.assertEqual(template.pattern.kind, "block")
        binary = template.pattern.children[0].children[0]
        self.assertEqual(binary.node.kind, "binary")
        self.assertIsNone(binary.dtype)
        self.assertIs(binary.node.children[0].symbol, template.parameter_symbols[0])

    def test_selected_int_and_double_instances_are_distinct_real_function_bodies(self):
        ast = self.parse("template<class T> T twice(T x){return x+x;} "
                         "int run(){return twice(3)+int(twice(2.5));}")
        functions = self.bodies(ast)
        self.assertEqual(set(functions), {"_Z3runv", "_Z5twiceIiET_S0_", "_Z5twiceIdET_S0_"})
        integer = functions["_Z5twiceIiET_S0_"].decl.type.args.params[0]
        floating = functions["_Z5twiceIdET_S0_"].decl.type.args.params[0]
        self.assertEqual(integer.type.type.names, ["int"])
        self.assertEqual(floating.type.type.names, ["double"])

    def test_repeated_calls_reuse_one_instance(self):
        ast = self.parse("template<typename T> T id(T x){return x;} "
                         "int run(){return id(3)+id(4);}")
        template = self.parser.scope.lookup("id").overloads[0]
        self.assertEqual(len(template.instances), 1)
        self.assertEqual(list(self.bodies(ast)), ["_Z3runv", "_Z2idIiET_S0_"])

    def test_multiple_type_parameters_and_dependent_cast(self):
        ast = self.parse("template<class T,class U> T combine(T x,U y){return x+T(y);} "
                         "int run(){return combine(3,2.5);}")
        body = self.bodies(ast)["_Z7combineIidET_S0_T0_"].body.block_items[0].expr
        self.assertIsInstance(body.right, c_ast.Cast)
        self.assertEqual(body.right.to_type.type.type.names, ["int"])

    def test_conflicting_deductions_are_not_common_type_conversions(self):
        self.diagnostic("template<class T> T add(T x,T y){return x+y;} "
                        "int run(){return add(1,2l);}", "cpp.no-viable-overload")

    def test_return_only_type_parameter_cannot_be_deduced(self):
        self.diagnostic("template<class T> T make(int x){return T(x);} "
                        "int run(){return make(1);}", "cpp.no-viable-overload")

    def test_nontemplate_wins_only_when_conversion_sequences_tie(self):
        ast = self.parse("template<class T> int f(T x){return 11;} int f(int x){return 22;} "
                         "int run(){return f(1)+f(1l);}")
        expression = self.bodies(ast)["_Z3runv"].body.block_items[0].expr
        self.assertEqual(expression.left.name.name, "_Z1fi")
        self.assertEqual(expression.right.name.name, "_Z1fIlEiT_")

    def test_body_error_in_selected_template_is_not_a_viability_fallback(self):
        error = self.diagnostic("template<class T> int f(T x){return x%2;} int f(long x){return 22;} "
                                "int run(){return f(1.5);}", "cpp.invalid-operands")
        self.assertIn("while instantiating _Z1fIdEiT_", error.message)
        # A dependent operation is not prematurely diagnosed on an unused body.
        self.parse("template<class T> int f(T x){return x%2;} int run(){return 0;}")

    def test_nondependent_errors_are_diagnosed_at_definition(self):
        self.diagnostic("template<class T> int f(T x){return missing;}", "cpp.undeclared")
        self.diagnostic("template<class T> int f(T x){return 1.5%2;}", "cpp.invalid-operands")

    def test_dependent_call_preserves_definition_point_candidate_set(self):
        ast = self.parse("int tag(long x){return 11;} "
                         "template<class T> int choose(T x){return tag(x);} "
                         "int tag(int x){return 22;} int run(){return choose(3);}")
        body = self.bodies(ast)["_Z6chooseIiEiT_"].body.block_items[0].expr
        self.assertEqual(body.name.name, "_Z3tagl")
        self.assertEqual(body.args.exprs[0].to_type.type.type.names, ["long"])

    def test_instantiated_operator_result_type_drives_dependent_overload(self):
        ast = self.parse("int tag(bool x){return 11;} int tag(int x){return 22;} "
                         "template<class T> int choose(T x){return tag(+x);} "
                         "int run(){return choose(true);}")
        body = self.bodies(ast)["_Z6chooseIbEiT_"].body.block_items[0].expr
        self.assertEqual(body.name.name, "_Z3tagi")

    def test_local_aliases_declarations_assignments_and_scope_bindings_substitute(self):
        ast = self.parse("template<class T> T f(T x){using U=T;U y=x;{int x=9;y=y+T(x);}"
                         "y=y+T(1);return y;} int run(){return f(2);}")
        body = self.bodies(ast)["_Z1fIiET_S0_"].body.block_items
        self.assertEqual(body[0].type.type.names, ["int"])
        self.assertIsInstance(body[1], c_ast.Compound)
        self.assertIsInstance(body[2], c_ast.Assignment)

    def test_forward_definition_and_renamed_template_parameters_merge(self):
        ast = self.parse("template<class T> T id(T); int run(){return id(4);} "
                         "template<class U> U id(U value){return value;}")
        self.assertEqual(len(self.parser.scope.lookup("id").overloads), 1)
        self.assertIn("_Z2idIiET_S0_", self.bodies(ast))

    def test_missing_selected_definition_is_explicit(self):
        self.diagnostic("template<class T> T id(T); int run(){return id(4);}",
                        "cpp.template-definition")

    def test_self_recursion_uses_cached_signature_not_recursive_instantiation(self):
        ast = self.parse("template<class T> T recurse(T x){return recurse(x);} "
                         "int run(){return recurse(4);}")
        self.assertEqual(len(self.parser.pending_instances), 1)
        self.assertEqual(len(self.bodies(ast)), 2)

    def test_template_parameter_cannot_be_shadowed(self):
        self.diagnostic("template<class T> int f(int T){return T;}", "cpp.template-parameter-shadowing")
        self.diagnostic("template<class T> int f(T x){{int T=1;}return 0;}",
                        "cpp.template-parameter-shadowing")
        self.diagnostic("template<class T,class T> int f(T x){return 0;}",
                        "cpp.template-parameter-shadowing")

    def test_remaining_partial_ordering_and_adl_are_explicit(self):
        self.diagnostic("template<class T> int f(T x,T y){return 11;} "
                        "template<class T> int f(T x,int y){return 22;} "
                        "int run(){return f(1,2);}", "cpp.unsupported.template-ordering")
        self.diagnostic("template<class T> int f(T x){return adl_only(x);}",
                        "cpp.unsupported.dependent-lookup")

    def test_instances_reach_actual_c_codegen_not_a_template_interpreter(self):
        from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
        from pcc.frontends.c.passes.context import PassContext

        ast = self.parse("template<class T> T twice(T x){return x+x;} "
                         "int run(){return twice(3)+int(twice(2.5));}")
        generator = CCodeGenerator(pass_ctx=PassContext(target_triple=TARGET))
        generator.generate_code(ast)
        ir = str(generator.module)
        self.assertIn("define i32 @_Z5twiceIiET_S0_", ir)
        self.assertIn("define double @_Z5twiceIdET_S0_", ir)
        self.assertIn("call double (double) @_Z5twiceIdET_S0_", ir)
        self.assertIn("fadd contract double", ir)
        self.assertEqual(type(generator.module).__module__, "pcc.ir.ir")


if __name__ == "__main__":
    unittest.main()
