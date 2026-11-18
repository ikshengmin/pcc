"""Reviewed corrections to the immutable pre-safety matcher fixture.

Inputs are the historical fixture ONLY, never the production matcher. Each
exact replacement has one expected site and an independent effect/mutant owner.
These deltas freeze control flow; the shared helper implementations are not
an independent ISA oracle. See the cited existing test modules.
"""
import ast
import copy

# Expression replacements specified from the safety obligations, in original
# evaluation order. An unexpected old shape or duplicate site fails closed.
EXPRESSIONS = (
    ('frame_and_branch_liveness',
     'mode == 2 and (middle is None or _same(middle[1], store[1]) or _forward_alias(middle[0]) == _forward_alias(store[0]))',
     'mode == 2 and (middle is None or middle[0] in (29, 61, sem.REGISTER_FP) or _same(middle[1], store[1]) or _forward_alias(middle[0]) == _forward_alias(store[0]))'),
    ('store_source_address',
     'old >= 0 and _scratch(old) and p.op(b) in ("stur", "str", "sturb", "strb") and p.register(b, 0) == old and p.dead(index + 2, old)',
     'old >= 0 and _scratch(old) and p.op(b) in ("stur", "str", "sturb", "strb") and p.register(b, 0) == old and not p.store_address_uses(b, old) and (mode == 3 or p.store_replacement_safe(b, move[1])) and p.dead(index + 2, old)'),
    ('zero_compare_flags',
     'mode == 5 and zero >= 0 and rhs == zero and lhs != zero and p.dead(index + 2, zero)',
     'mode == 5 and zero >= 0 and rhs == zero and lhs != zero and lhs not in (sem.REGISTER_XZR, sem.REGISTER_WZR) and p.dead(index + 2, zero)'),
    ('mov_compare_sp',
     'mode == 6 and move and _scratch(move[0]) and move[0] in (lhs, rhs) and p.dead(index + 2, move[0])',
     'mode == 6 and move and _scratch(move[0]) and move[0] in (lhs, rhs) and not (rhs == move[0] and move[1] == sem.REGISTER_WSP) and p.dead(index + 2, move[0])'),
    ('branch_live_registers',
     'move and branch and move[0] == branch[1] and _class(move[0]) == "w" and _class(move[1]) == "w" and _scratch(move[0]) and p.false_path(index + 2)',
     'move and branch and move[0] == branch[1] and _class(move[0]) == "w" and _class(move[1]) == "w" and move[1] != sem.REGISTER_WSP and _scratch(move[0]) and p.false_path(index + 2)'),
    ('frame_and_branch_liveness',
     'cset and store and branch and p.op(c) == "and" and len(t) == 3 and dst >= 0 and src >= 0 and _class(dst) == _class(src) == "w" and _same(t[2], CompilerInt4(sem.TOKEN_IMMEDIATE, 255, 16, sem.IMMEDIATE_HASH)) and store[0] == src == cset[0] and branch[1] == dst',
     'cset and store and branch and p.op(c) == "and" and len(t) == 3 and dst >= 0 and src >= 0 and _class(dst) == _class(src) == "w" and _same(t[2], CompilerInt4(sem.TOKEN_IMMEDIATE, 255, 16, sem.IMMEDIATE_HASH)) and store[0] == src == cset[0] and branch[1] == dst and dst == src'),
)

STATEMENTS = (
    ('w_self_move', 'replacement = -1', 'replacement = p.new_move(move[0], move[0])'),
    ('branch_live_registers',
     'replacement = p.new_branch(branch[0], branch[2], move[1])',
     'replacement = p.new_branch(branch[0], branch[2], move[1])\np.output.append(a)'),
    ('zero_branch_live',
     'replacement = p.new_branch("b", branch[2]) if branch[0] == "cbz" else a',
     'replacement = p.new_branch("b", branch[2]) if branch[0] == "cbz" else a\nif branch[0] == "cbz":\n    p.output.append(a)'),
)

MODE14_OLD = 'p.output.append(a)\nreplacement = d\nconsumed = 4'
MODE14_NEW = 'p.output.append(a)\np.output.append(b)\np.output.append(c)\nreplacement = d\nconsumed = 4'

def _dump(node):
    return ast.dump(node, include_attributes=False)

class _ExactNode(ast.NodeTransformer):
    def __init__(self, old, new):
        self.old = _dump(old); self.new = new; self.count = 0
    def generic_visit(self, node):
        if _dump(node) == self.old:
            self.count += 1
            return copy.deepcopy(self.new)
        return super().generic_visit(node)

class _ExactSequence(ast.NodeTransformer):
    def __init__(self, old, new):
        self.old = [_dump(n) for n in old]; self.new = new; self.count = 0
    def generic_visit(self, node):
        for field, value in ast.iter_fields(node):
            if isinstance(value, list) and value and all(isinstance(n, ast.stmt) for n in value):
                result=[];index=0
                while index < len(value):
                    if [_dump(n) for n in value[index:index+len(self.old)]] == self.old:
                        self.count += 1;result.extend(copy.deepcopy(self.new));index+=len(self.old)
                    else:
                        result.append(self.visit(value[index]));index+=1
                setattr(node,field,result)
            elif isinstance(value,ast.AST):
                setattr(node,field,self.visit(value))
        return node

def corrected_frozen(historical_source):
    tree=ast.parse(historical_source)
    assert len(tree.body)==1 and isinstance(tree.body[0],ast.FunctionDef)
    for owner, old, new in EXPRESSIONS:
        edit=_ExactNode(ast.parse(old,mode='eval').body,ast.parse(new,mode='eval').body)
        tree=edit.visit(tree);assert edit.count==1,(owner,'expression',edit.count)
    for owner, old, new in STATEMENTS:
        edit=_ExactNode(ast.parse(old).body[0],ast.parse(new).body)
        tree=edit.visit(tree);assert edit.count==1,(owner,'statement',edit.count)
    edit=_ExactSequence(ast.parse(MODE14_OLD).body,ast.parse(MODE14_NEW).body)
    tree=edit.visit(tree);assert edit.count==1,('branch_live_registers','mode14',edit.count)
    return ast.fix_missing_locations(tree.body[0])
