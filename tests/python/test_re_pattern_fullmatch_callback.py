"""Argument conversion differential model; not native/collector execution."""
import ast
import operator
from pathlib import Path
import re

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_re_match_runtime.py"


def call(args):
    slots = {}
    error = []
    outcome = []
    borrowed = {0: ("a", 6, 0), 8: args}
    def load(base, offset):
        if isinstance(base, tuple):
            base, initial = base
            offset += initial
        return base.get(offset)
    def copy(slots, tokens, index, borrowed, source):
        slots[index * 8] = borrowed[source * 8]
        return 1
    def index(slot):
        try:
            value = operator.index(load(slot, 0))
            if not -(2**63) <= value < 2**63:
                raise OverflowError()
            return value
        except Exception as exc:
            error.append(type(exc))
            return 0
    def run(slots, tokens, flags, start, end):
        outcome.append(re.compile(slots[16], flags).fullmatch(slots[24], start, end))
    ns = dict(c_ptr=object, stack_alloc=lambda size: {}, memset=lambda *args: None,
              _re_copy_input=copy, load_ptr=load, store_ptr=lambda b,o,v: b.__setitem__(o,v),
              ptr_add=lambda b,o: (b,o), ptr_is_null=lambda obj: int(obj is None),
              py_tuple_len=len, py_tuple_get=lambda seq,i: seq[i], cstr=lambda s:s,
              py_err_occurred=lambda: bool(error), _re_adopt=lambda *args: 1,
              _re_drop=lambda b,t,i: b.pop(i*8,None), py_index_i64_checked_slots=index,
              _re_error=lambda s,t,tag,msg: error.append({3:TypeError,7:RuntimeError}[tag]),
              _re_run_fullmatch=run, _re_finish=lambda *args: 0)
    nodes=[]
    for node in ast.parse(SOURCE.read_text()).body:
        if isinstance(node,ast.Assign) and any(isinstance(n,ast.Name) and n.id.startswith('_RE_') for n in node.targets):
            nodes.append(node)
        if isinstance(node,ast.FunctionDef) and node.name in ('_re_pattern_bound','pcc_re_pattern_fullmatch_into'):
            node.decorator_list=[]
            nodes.append(node)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),ns)
    ns['pcc_re_pattern_fullmatch_into'](borrowed,slots)
    if error:
        return error[0]
    return None if outcome[0] is None else outcome[0].span()


class Index:
    def __index__(self):
        return 0


class BadIndex:
    def __index__(self):
        raise ValueError('index callback failed')


@pytest.mark.parametrize('args', [(), ('a',), ('a',0), ('a',0,1), ('a',1,0),
    ('a',-100,100), ('a',100,100), ('a',None), ('a',1.2), ('a',Index()),
    ('a',BadIndex()), ('a',2**100), ('a',-(2**100)), ('a',0,None),
    ('a',0,2**100), ('a',0,1,2)])
def test_pattern_arguments_match_cpython(args):
    try:
        expected=re.compile('a').fullmatch(*args)
        expected=None if expected is None else expected.span()
    except Exception as exc:
        expected=type(exc)
    assert call(args)==expected
