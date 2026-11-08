"""C11 6.8.4.1 / 6.8.5: SCCP facts belong to one control-flow site.

Line-only parser coordinates are not statement identities. Macro expansion and
ordinary single-line C can put dynamic ifs beside a folded loop/if/expression.
"""
from __future__ import annotations

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.frontends.c.parse import make_c_parser
from pcc.frontends.c.passes import PassContext
from pcc.frontends.c.passes.ssa_branch_prune import SSABranchPrunePass
from pcc.frontends.c.passes.ssa_sccp import SSASCCPPass
from pcc.frontends.c.ssa import SSABranch
from pcc.driver.project import TranslationUnit
from tests.owned_c_corpus import run_owned_c_corpus


def _ifs(node):
    found = []
    if isinstance(node, c_ast.If):
        found.append(node)
    for _, child in node.children():
        found.extend(_ifs(child))
    return found


@pytest.mark.parametrize('body', [
    'do { if (c) return 7; } while (0); return 0;',
    'while (0) { if (c) return 7; } return 0;',
    'for (; 0;) { if (c) return 7; } return 0;',
    'if (c) return 7; if (0) return 9; return 0;',
    'if (1) { if (c) return 7; } return 0;',
    'if (c) { if (1) return 7; } return 0;',
    'if (c && 1) return 7; if (0) return 9; return 0;',
    'if (c || 0) return 7; if (1) return 9; return 0;',
    'int x = 1 ? 3 : 4; if (c) return x; return 0;',
    'int x = 0 && c; if (c) return 7; return x;',
    'switch (0) { case 0: if (c) return 7; } if (0) return 9; return 0;',
])
def test_branch_prune_rejects_colliding_control_coordinates(body):
    ast = make_c_parser().parse('int f(int c) { ' + body + ' }')
    original_ifs = _ifs(ast)
    ctx = PassContext()
    result = SSABranchPrunePass().run(ast, ctx)
    # Ensure this was an actual SSA/SCCP attempt, not an unsupported-function
    # skip masking the regression as the initial callback instrumentation did.
    assert 'f' in ctx.ssa_functions
    assert ctx.ssa_sccp_results['f'].folded_branches
    assert result is None
    assert _ifs(ast) == original_ifs
    assert not ctx.stats.get('ssa_branch_prune.fold_true', 0)
    assert not ctx.stats.get('ssa_branch_prune.fold_false', 0)


def test_branch_prune_keeps_optimizing_unambiguous_if_beside_collision():
    ast = make_c_parser().parse('''
int f(int c) {
    int result = 0;
    do { if (c) result = 7; } while (0);
    if (1) return result;
    return 9;
}
''')
    ctx = PassContext()
    result = SSABranchPrunePass().run(ast, ctx)
    assert result is ast
    remaining = _ifs(ast)
    assert len(remaining) == 1
    assert isinstance(remaining[0].cond, c_ast.ID)
    assert remaining[0].cond.name == 'c'
    assert ctx.stats['ssa_branch_prune.fold_true'] == 1
    assert not ctx.stats.get('ssa_branch_prune.fold_false', 0)


def test_branch_prune_counts_nonfolded_ssa_branches_at_same_coordinate():
    ast = make_c_parser().parse('''
int f(int c) {
    int result = 0;
    if (c) result = 7;
    if (1) result = result + 1;
    return result;
}
''')
    ctx = PassContext()
    SSASCCPPass().run(ast, ctx)
    ssa = ctx.ssa_functions['f']
    branches = [block for block in ssa.blocks
                if isinstance(block.terminator, SSABranch)]
    folded = ctx.ssa_sccp_results['f'].folded_branches
    dynamic = next(block for block in branches if block.name not in folded)
    constant = next(block for block in branches if block.name in folded)
    # Simulate two lowering edges carrying one source coordinate while their
    # AST sites remain distinct. Only counting folded edges is insufficient.
    dynamic.terminator.source_coord = constant.terminator.source_coord
    original_ifs = _ifs(ast)
    assert SSABranchPrunePass().run(ast, ctx) is None
    assert _ifs(ast) == original_ifs


_CONTROLS = r'''
static int once(int c) { do { if (c) return 7; } while (0); return 0; }
static int nested(int c) { if (1) { if (c) return 7; } return 0; }
static int while_zero(int c) { while (0) { if (c) return 9; } if (c) return 7; return 0; }
static int for_zero(int c) { for (; 0;) { if (c) return 9; } if (c) return 7; return 0; }
static int conjunction(int c) { if (c && 1) return 7; if (0) return 9; return 0; }
static int disjunction(int c) { if (c || 0) return 7; if (1) return 9; return 0; }
static int conditional(int c) { int x = 1 ? 7 : 9; if (c) return x; return 0; }
int main(void) {
    if (once(1) != 7 || once(0) != 0) return 1;
    if (nested(1) != 7 || nested(0) != 0) return 2;
    if (while_zero(1) != 7 || while_zero(0) != 0) return 3;
    if (for_zero(1) != 7 || for_zero(0) != 0) return 4;
    if (conjunction(1) != 7 || conjunction(0) != 0) return 5;
    if (disjunction(1) != 7 || disjunction(0) != 9) return 6;
    if (conditional(1) != 7 || conditional(0) != 0) return 7;
    return 0;
}
'''


_CALLBACK = r'''
typedef int (*visitproc)(void *, void *);
struct RawObject { void *edge; };
struct Type { int (*traverse)(void *, visitproc, void *); };
static int visit_slot(void **slot, visitproc visit, void *arg) {
    return visit(*slot, arg);
}
#define VISIT(op) do { if (op) { int result = visit_slot((void **)&(op), visit, arg); if (result) return result; } } while (0)
static int raw_traverse(void *self, visitproc visit, void *arg) {
    struct RawObject *raw = (struct RawObject *)self;
    VISIT(raw->edge);
    return 0;
}
static struct Type raw_type = { raw_traverse };
struct VisitState { void *expected; int calls; int result; };
static int record_visit(void *object, void *arg) {
    struct VisitState *state = (struct VisitState *)arg;
    if (object != state->expected) return 91;
    state->calls++;
    return state->result;
}
int main(void) {
    int child = 7;
    struct RawObject raw = { &child };
    struct VisitState state = { &child, 0, 0 };
    if (raw_type.traverse(&raw, record_visit, &state) != 0) return 1;
    if (state.calls != 1) return 2;
    state.result = 23;
    if (raw_type.traverse(&raw, record_visit, &state) != 23) return 3;
    if (state.calls != 2) return 4;
    raw.edge = 0;
    if (raw_type.traverse(&raw, record_visit, &state) != 0) return 5;
    if (state.calls != 2) return 6;
    return 0;
}
'''


@pytest.mark.parametrize(('name', 'source'), [
    ('colliding_controls', _CONTROLS),
    ('first_do_while_callback', _CALLBACK),
])
def test_branch_prune_coordinate_controls_owned_native(name, source):
    # C11 6.8.5.2 requires a do body to run before its condition. Keep the
    # callback uninstrumented and reachable through a static data initializer;
    # a global counter in it would force SSA fallback and hide this bug.
    unit = TranslationUnit(name + '.c', name + '.c', source)
    result = run_owned_c_corpus(
        CEvaluator(backend='self'), [unit], optimize=True,
        base_dir='.', jobs=1, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
