from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.c_parser import CParser
from pcc.frontends.c.passes import PassContext
from pcc.frontends.c.passes.ssa_adce import SSAADCEPass


_PARSER = None


def _get_parser():
    """Reuse this module's parser without constructing it during collection."""
    global _PARSER
    if _PARSER is None:
        _PARSER = CParser(lex_optimize=True, yacc_debug=False, yacc_optimize=True)
    return _PARSER


def _run_pass(source: str):
    ast = _get_parser().parse(source)
    ctx = PassContext()
    out = SSAADCEPass().run(ast, ctx)
    return ast, out, ctx


def test_ssa_adce_pass_clears_dead_initializer_before_live_overwrite():
    ast, out, ctx = _run_pass(
        """
        int f(int a) {
            int x = a + 1;
            x = 0;
            return x;
        }
        """
    )

    assert out is ast
    func = ast.ext[0]
    decl = func.body.block_items[0]
    assert isinstance(decl, c_ast.Decl)
    assert decl.init is None
    assert ctx.stats["ssa.adce.functions"] == 1
    assert ctx.stats["ssa_adce.drop_init"] == 1


def test_ssa_adce_pass_removes_dead_assignment_in_cross_block_branch():
    ast, out, ctx = _run_pass(
        """
        int f(int a, int flag) {
            int x;
            if (flag) {
                x = a + 1;
            }
            return 0;
        }
        """
    )

    assert out is ast
    func = ast.ext[0]
    branch = func.body.block_items[1]
    assert isinstance(branch, c_ast.If)
    assert branch.iftrue is None or not branch.iftrue.block_items
    assert ctx.stats["ssa_adce.drop_assign"] == 1


def test_ssa_adce_pass_keeps_live_initializer():
    ast, out, ctx = _run_pass(
        """
        int f(int a) {
            int x = a + 1;
            return x;
        }
        """
    )

    assert out is None
    func = ast.ext[0]
    decl = func.body.block_items[0]
    assert isinstance(decl, c_ast.Decl)
    assert isinstance(decl.init, c_ast.BinaryOp)
    assert "ssa_adce.drop_init" not in ctx.stats


def test_ssa_adce_pass_keeps_initializer_used_via_call_argument():
    ast, out, ctx = _run_pass(
        """
        int id(int x);

        int f(void) {
            int z = 7;
            return id(z);
        }
        """
    )

    assert out is None
    func = ast.ext[1]
    decl = func.body.block_items[0]
    assert isinstance(decl, c_ast.Decl)
    assert isinstance(decl.init, c_ast.Constant)
    assert decl.init.value == "7"
    assert "ssa_adce.drop_init" not in ctx.stats


def test_ssa_rewriters_share_one_declared_type_collector():
    """ADCE, DSE, GVN and SCCP read declared types through ast_utils.

    Each rewriter used to carry its own copy of the collector; they all key
    SSA facts by the same normalized type names, so they must agree on them.
    """
    from pcc.frontends.c.passes import ast_utils
    from pcc.frontends.c.passes import ssa_adce, ssa_dse, ssa_gvn_rewrite, ssa_sccp_rewrite

    owners = (
        ssa_adce._SSAADCETransformer,
        ssa_dse._SSADSERewriter,
        ssa_gvn_rewrite._SSAGVNRewriter,
        ssa_sccp_rewrite._SSASCCPRewriter,
    )
    for owner in owners:
        for private in ("_normalize_type_name", "_decl_type_name", "_collect_function_types"):
            assert private not in vars(owner), (owner.__name__, private)

    ast = _get_parser().parse(
        """
        typedef unsigned long size_t;
        long f(const int a, char *p, size_t n) {
            unsigned int b = 1;
            { volatile long c = 2; register short d = 3; }
            for (int i = 0; i < a; i++) { long long e = i; }
            int b2[4];
            return a;
        }
        """
    )
    func = ast.ext[1]
    # Parameters first, then body declarations in source order; pointers and
    # arrays have no identifier type and are left out.
    assert list(ast_utils.collect_function_types(func).items()) == [
        ("a", "int"),
        ("n", "size_t"),
        ("b", "unsigned int"),
        ("c", "long"),
        ("d", "short"),
        ("i", "int"),
        ("e", "long long"),
    ]
    assert ast_utils.decl_type_name(func.decl.type) == "long"
    assert ast_utils.decl_type_name(None) == ""
    assert (
        ast_utils.normalize_type_name("const\tunsigned  int restrict __restrict register volatile")
        == "unsigned int"
    )
