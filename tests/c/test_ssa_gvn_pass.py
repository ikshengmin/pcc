from pcc.frontends.c.parse.c_parser import CParser
from pcc.frontends.c.passes import PassContext
from pcc.frontends.c.passes.ssa_gvn import SSAGVNPass


_PARSER = None


def _get_parser():
    """Reuse this module's parser without constructing it during collection."""
    global _PARSER
    if _PARSER is None:
        _PARSER = CParser(lex_optimize=True, yacc_debug=False, yacc_optimize=True)
    return _PARSER


def test_ssa_gvn_pass_bootstraps_and_records_redundancies():
    ast = _get_parser().parse(
        """
        int same(int a, int b) {
            int x = a + b;
            int y = a + b;
            return y - x;
        }
        """
    )
    ctx = PassContext()

    out = SSAGVNPass().run(ast, ctx)

    assert out is None
    assert ctx.stats["ssa.bootstrap.success"] == 1
    assert ctx.stats["ssa.gvn.functions"] == 1
    assert ctx.stats["ssa.gvn.redundant_values"] == 1
    assert ctx.ssa_gvn_results["same"].redundant_value_names() == {"$t.1": "$t.0"}
    assert any(
        entry.pass_name == "ssa-gvn"
        and entry.action == "analyzed"
        and entry.target == "same"
        for entry in ctx.log
    )


def test_ssa_gvn_pass_ignores_unsupported_functions_after_bootstrap_skip():
    # Construct a function the bootstrap builder is known to reject: a
    # local with a qualifier/storage class the SSA layer does not yet
    # model (label/goto is a concrete unsupported shape).
    ast = _get_parser().parse(
        """
        int dispatch(int n) {
            if (n) goto out;
        out:
            return n;
        }
        """
    )
    ctx = PassContext()

    out = SSAGVNPass().run(ast, ctx)

    assert out is None
    assert ctx.stats["ssa.bootstrap.skipped"] == 1
    assert "ssa.gvn.functions" not in ctx.stats
    assert ctx.ssa_gvn_results == {}
