"""Static literal emitters share the fixed-layout L1CodeGen host state."""

import os
import subprocess
import sys


PROGRAM = '''
def tables():
    first = {"one": 1, "two": 2}
    second = {"three": 3, "four": 4}
    sequence = [5, 6]
    pair = (7, 8)
    nested = {"outer": [(9, 10), {"inner": 11}]}
    another = ([12, 13], {"last": 14})
    return first, second, sequence, pair, nested, another

def main():
    left = tables()
    right = tables()
    left[4]["outer"][1]["inner"] = 99
    left[5][0].append(15)
    print(left[0]["one"], right[1]["four"], left[2][1], right[3][0])
    print(left[4]["outer"][1]["inner"], right[4]["outer"][1]["inner"])
    print(len(left[5][0]), len(right[5][0]), right[5][1]["last"])
    try:
        raise ValueError("after literals")
    except ValueError as error:
        print(str(error))

main()
'''


def test_static_container_emission_preserves_values_and_fresh_nested_objects(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    monkeypatch.setenv("PCC_STATIC_AGGREGATE", "1")
    source = tmp_path / "static_containers.py"
    source.write_text(PROGRAM)
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=10)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "static_containers"
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_runtime_archive))
    for backend in (0,):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10,
                                env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert result.returncode == 0, f"GC{backend}: {result.stdout}{result.stderr}"
        assert result.stdout == expected.stdout, f"GC{backend}: {result.stdout}"


def test_static_container_probe_reaches_each_counter(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.setenv("PCC_STATIC_AGGREGATE", "1")
    source = tmp_path / "static_containers.py"
    source.write_text(PROGRAM)
    output = tmp_path / "static_containers.ll"
    compile_python(str(source), str(output), backend="self",
                   ir_scaffold_mode="on", emit_llvm_only=True)
    text = output.read_text()
    for name in (".pcc.static.dict.1", ".pcc.static.dict.2", ".pcc.static.list.1",
                 ".pcc.static.tuple.2", ".pcc.static.agg.dict.1", ".pcc.static.agg.tuple.2"):
        assert "@" + name + " =" in text, name
    monkeypatch.setenv("PCC_STATIC_AGGREGATE", "off")
    plain_output = tmp_path / "plain_containers.ll"
    compile_python(str(source), str(plain_output), backend="self",
                   ir_scaffold_mode="on", emit_llvm_only=True)
    assert "@.pcc.static.agg." not in plain_output.read_text()


def _descriptor_host(expr_text):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift

    module = parse_and_lift("table = " + expr_text + "\n", "table.py", "table")
    host = L1CodeGen(module, ir_scaffold_mode="on")
    return host, module.body[0].value


def test_static_nested_descriptor_has_one_accumulator(monkeypatch):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.codegen.literal_lowering import _CSTR, _I64
    from pcc.ir.compat import ir

    host, expr = _descriptor_host(
        '("start", {"left": [None, True, False, -7], "right": (9, "tail")})'
    )
    original = L1CodeGen._static_aggregate_words
    visits = []

    def counted(self, node):
        visits.append(node)
        return original(self, node)

    monkeypatch.setattr(L1CodeGen, "_static_aggregate_words", counted)
    words = host._static_aggregate_words(expr)
    assert visits == [expr]  # the descriptor list is allocated once, not at every node
    assert list(host._str_obj_pool) == ["start", "left", "right", "tail"]

    def number(value):
        return str(ir.Constant(_I64, value).inttoptr(_CSTR))

    def string(value):
        return str(host._str_obj_pool[value].bitcast(_CSTR))

    expected = [
        number(4), number(5), string("start"), number(2), number(5),
        string("left"), number(6), number(9), number(10), number(14),
        number(12), number(-13), string("right"), number(4), number(5),
        number(19), string("tail"),
    ]
    assert list(map(str, words)) == expected
    again = host._static_aggregate_words(expr)
    assert again is not words
    assert list(map(str, again)) == expected


def test_static_nested_descriptor_failure_retains_original_literal_order():
    for expr_text in (
        '("before", {"key": [1, len("dynamic"), "after"]})',
        '("before", {"key": 1, **mapping, "after": 2})',
        '("before", {"key": [], "after": 2})',
        '("before", {"key": [1, 1267650600228229401496703205376, "after"]})',
    ):
        host, expr = _descriptor_host(expr_text)
        assert host._static_aggregate_words(expr) is None
        # No later strings are pooled after rejection, and the already visited
        # prefix remains in encounter order, just as in the general lowering.
        assert list(host._str_obj_pool) == ["before", "key"]
        # A discarded partial descriptor cannot affect later independent
        # descriptor construction on the same lowering host.
        _, accepted = _descriptor_host('("key", [2])')
        words = host._static_aggregate_words(accepted)
        assert words is not None
        assert len(words) == 6
        assert list(host._str_obj_pool) == ["before", "key"]


def test_large_descriptor_reuses_only_exact_numeric_words(monkeypatch):
    from collections import Counter
    from pcc.frontends.python.codegen.literal_lowering import _CSTR
    from pcc.ir.compat import ir

    item = '(-4611686018427387904, 4611686018427387903, None, True, False, "s")'
    host, expr = _descriptor_host("(" + ",".join([item] * 256) + ",)")
    original = ir.Constant.inttoptr
    calls = Counter()

    def counted(self, target):
        calls[self.value] += 1
        return original(self, target)

    monkeypatch.setattr(ir.Constant, "inttoptr", counted)
    words = host._static_aggregate_words(expr)
    assert words is not None
    assert len(words) == 2 + 256 * 8
    expected_bits = {4, 513, 13, -9223372036854775807, 9223372036854775807, 10, 14, 12}
    assert calls == Counter({bits: 1 for bits in expected_bits})
    # All value words, including the most negative and positive tagged
    # signed values, retain the exact prior spelling and descriptor order.
    expected_item = [
        "inttoptr (i64 4 to ptr)",
        "inttoptr (i64 13 to ptr)",
        "inttoptr (i64 -9223372036854775807 to ptr)",
        "inttoptr (i64 9223372036854775807 to ptr)",
        "inttoptr (i64 10 to ptr)",
        "inttoptr (i64 14 to ptr)",
        "inttoptr (i64 12 to ptr)",
        str(host._str_obj_pool["s"].bitcast(_CSTR)),
    ]
    assert list(map(str, words[2:])) == expected_item * 256
    assert list(host._str_obj_pool) == ["s"]
    calls.clear()
    again = host._static_aggregate_words(expr)
    assert calls == Counter({bits: 1 for bits in expected_bits})
    assert again is not words and again[0] is not words[0]
    assert list(map(str, again)) == list(map(str, words))

    # Below the admission threshold, no memo is installed.
    calls.clear()
    small_host, small_expr = _descriptor_host("(" + item + "," + item + ")")
    small = small_host._static_aggregate_words(small_expr)
    assert small is not None
    assert calls[4] == 3
    assert calls[10] == 2


def test_large_descriptor_numeric_memo_stops_admitting_at_bound(monkeypatch):
    from collections import Counter
    from pcc.ir.compat import ir

    items = list(range(4100)) + [0, 4099, 4099]
    host, expr = _descriptor_host("(" + ",".join(map(str, items)) + ",)")
    original = ir.Constant.inttoptr
    calls = Counter()

    def counted(self, target):
        calls[self.value] += 1
        return original(self, target)

    monkeypatch.setattr(ir.Constant, "inttoptr", counted)
    words = host._static_aggregate_words(expr)
    assert words is not None
    assert len(words) == len(items) + 2
    assert calls[1] == 1  # already-admitted low value still hits
    assert calls[8199] == 3  # later unique value stays uncached
    assert list(map(str, words[2:])) == [
        "inttoptr (i64 " + str((i << 1) | 1) + " to ptr)" for i in items
    ]
