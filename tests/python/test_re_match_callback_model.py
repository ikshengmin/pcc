"""CPython differential model of actual slot-only Match method bodies.

This checks method semantics, not native execution or moving collector safety.
"""
import ast
import operator
from pathlib import Path
import re

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_re_match_runtime.py"


class Heap:
    def __init__(self, value):
        self.value = value


def materialize(obj):
    value = obj.value
    if isinstance(value, list):
        return tuple(materialize(item) for item in value)
    if isinstance(value, dict):
        return {key: materialize(item) for key, item in value.items()}
    return value


class CallbackModel:
    def __init__(self, match, args):
        self.slots = {}
        self.tokens = {}
        self.error = None
        self.none = Heap(None)
        self.slots[4 * 8] = Heap(match.string)
        self.slots[5 * 8] = Heap([Heap(index) for group in range(match.re.groups + 1) for index in match.span(group)])
        by_group = {number: name for name, number in match.re.groupindex.items()}
        self.slots[6 * 8] = Heap([Heap(by_group.get(group)) for group in range(1, match.re.groups + 1)])
        self.slots[3 * 8] = Heap([Heap(arg) for arg in args])
        ns = {
            "c_ptr": object,
            "ptr_add": lambda base, offset: (base, offset),
            "load_ptr": self.load,
            "store_ptr": self.store,
            "load_i32": self.load,
            "store_i64": self.store,
            "ptr_is_null": lambda obj: int(obj == 0),
            "null": lambda: 0,
            "stack_alloc": lambda size: {},
            "memset": lambda base, zero, size: base.update({0: zero}),
            "load_i8": lambda value, offset: value[offset],
            "global_load_ptr": lambda name: self.none,
            "cstr": lambda text: text,
            "py_err_occurred": lambda: self.error is not None,
            "py_tuple_get": lambda obj, index: obj.value[index],
            "py_tuple_len": lambda obj: len(obj.value),
            "py_tuple_new": lambda length: Heap([0] * length),
            "py_tuple_set_item": lambda obj, index, value: obj.value.__setitem__(index, value),
            "py_int_to_i64": self.to_i64,
            "py_int_from_i64": lambda value: Heap(value),
            "py_index_i64_checked": self.index,
            "py_obj_has_index": lambda obj: int(hasattr(type(obj.value), "__index__")),
            "py_obj_index_slots": self.index_slots,
            "py_str_check": lambda obj: int(isinstance(obj.value, str)),
            "py_str_utf8": lambda obj: obj.value.encode(),
            "py_str_byte_len": lambda obj: len(obj.value.encode()),
            "py_str_byte_slice_i64": lambda obj, lo, hi: Heap(obj.value.encode()[lo:hi].decode()),
            "py_dict_new": lambda: Heap({}),
            "py_dict_set": lambda obj, name, item: obj.value.__setitem__(name.value, item),
            "pcc_gc_root_copy_lease": self.copy,
            "_re_adopt": lambda slots, tokens, index: int(self.load(slots, index * 8) != 0),
            "_re_drop": lambda slots, tokens, index: self.store(slots, index * 8, 0),
            "_re_error": self.raise_error,
            "py_runtime_error_if_unset": lambda op, msg: self.raise_error(None, None, 7, msg),
        }
        wanted = {"_re_span_value", "_re_same_name", "_re_select_group", "_re_group_item", "_re_callback_result"}
        nodes = []
        for node in ast.parse(SOURCE.read_text()).body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id.startswith(("_RE_", "_CB_")) for target in node.targets):
                nodes.append(node)
            elif isinstance(node, ast.FunctionDef) and node.name in wanted:
                nodes.append(node)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), ns)
        self.run = ns["_re_callback_result"]

    def load(self, base, offset):
        if isinstance(base, tuple):
            base, initial = base
            offset += initial
        return base.get(offset, 0)

    def store(self, base, offset, value):
        if isinstance(base, tuple):
            base, initial = base
            offset += initial
        base[offset] = value

    def copy(self, destination, source):
        self.store(destination, 0, self.load(source, 0))
        return 0

    def index(self, obj):
        try:
            return operator.index(obj.value)
        except Exception as exc:
            self.error = type(exc)
            return -1

    def to_i64(self, obj, overflow):
        if not -(2**63) <= obj.value < 2**63:
            self.store(overflow, 0, 1)
            return 0
        return obj.value

    def index_slots(self, source, destination):
        value = self.index(self.load(source, 0))
        if self.error is not None:
            return -1
        self.store(destination, 0, Heap(value))
        return 0

    def raise_error(self, slots, tokens, tag, message):
        if self.error is None:
            self.error = {3: TypeError, 5: IndexError, 7: RuntimeError}[tag]


class IndexErrorValue:
    def __index__(self):
        raise ValueError("custom index failed")


class Index:
    def __index__(self):
        return 1


@pytest.mark.parametrize("pattern,text,method,args", [
    ("(a|ab)", "ab", "group", ()),
    ("(a|ab)", "ab", "group", (1,)),
    ("(?P<x>a)(b)?", "a", "group", ("x",)),
    ("(?P<x>a)(b)?", "a", "group", (0, "x", 2)),
    ("(?P<x>a)(b)?", "a", "group", (Index(),)),
    ("(?P<x>a)(b)?", "a", "group", (True,)),
    ("(?P<x>a)(b)?", "a", "group", ("missing",)),
    ("(?P<x>a)(b)?", "a", "group", ("x\0extra",)),
    ("(?P<x>a)(b)?", "a", "group", (-1,)),
    ("(?P<x>a)(b)?", "a", "group", (99,)),
    ("(?P<x>a)(b)?", "a", "group", (None,)),
    ("(?P<x>a)(b)?", "a", "groups", ()),
    ("(?P<x>a)(b)?", "a", "groups", ("fallback",)),
    ("(?P<x>a)(?P<y>b)?", "a", "groupdict", ()),
    ("(?P<x>a)(?P<y>b)?", "a", "groupdict", (17,)),
    ("(?P<x>a)(?P<y>b)?", "a", "start", ("y",)),
    ("(?P<x>a)(?P<y>b)?", "a", "end", ()),
    ("(?P<x>a)(?P<y>b)?", "a", "span", (0,)),
    ("(?P<x>a)(?P<y>b)?", "a", "span", ("y",)),
    ("a", "a", "group", (10**100,)),
    ("a", "a", "group", (1.5,)),
    ("a", "a", "group", (IndexErrorValue(),)),
    ("a", "a", "span", (0, 1)),
    ("a", "a", "groups", (0, 1)),
    ("a", "a", "groupdict", (0, 1)),
    ("(a.*)", "a\0b", "group", (1,)),
])
def test_callback_bodies_match_cpython(pattern, text, method, args):
    match = re.fullmatch(pattern, text)
    assert match is not None
    kind = {"group": 0, "start": 1, "end": 2, "span": 3, "groups": 4, "groupdict": 5}[method]
    try:
        expected = getattr(match, method)(*args)
    except Exception as exc:
        expected_error = type(exc)
    else:
        expected_error = None
    model = CallbackModel(match, args)
    model.run(model.slots, model.tokens, kind, len(args), match.re.groups)
    assert model.error == expected_error
    if expected_error is None:
        assert materialize(model.slots[0]) == expected
