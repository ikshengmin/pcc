"""A returned value takes no graph transaction or pin.

The return path once reloaded and pinned a heap result under the graph lock
so a moving collector could not relocate it between the reload and the pin,
and skipped that for a tagged integer: every call returning one used to
serialize all worker threads on that global lock (the call primitive spent
84% of its time waiting for it with four workers). The one collector never
moves an object, so no return takes the lock or a pin (item 2 (d)).
"""
import re

from pcc.frontends.python.pipeline import compile_python


SOURCE = '''
def add(a: int, b: int) -> int:
    return a + b


def run(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        total = add(total, index)
        index += 1
    return total


print(run(10))
'''


def _function(text, name):
    match = re.search(r"^define[^\n]*@" + re.escape(name) + r"\(.*?^}", text, re.M | re.S)
    assert match is not None, name
    return match.group(0)


def _blocks(body):
    blocks = {}
    label = None
    for line in body.splitlines()[1:]:
        found = re.match(r"^([-\w.$]+):", line)
        if found:
            label = found.group(1)
            blocks[label] = []
        elif label is not None:
            blocks[label].append(line.strip())
    return blocks


def test_returns_take_their_result_without_a_graph_lock_or_pin(tmp_path):
    source = tmp_path / "scalar_return.py"
    source.write_text(SOURCE)
    output = tmp_path / "scalar_return.ll"
    compile_python(str(source), str(output), backend="self",
                   ir_scaffold_mode="on", emit_llvm_only=True)
    body = _function(output.read_text(), "user_scalar_return_add")
    blocks = _blocks(body)
    # One collector (item 2 (d)): neither a tagged int nor a heap result is
    # peeked, reloaded under the graph lock or pinned; the return takes its
    # result slot through pcc_gc_take_slot.
    assert not any("@pcc_py_gc_minor_graph_lock(" in line
                   for lines in blocks.values() for line in lines)
    assert not any(label.startswith(("return.pin.", "gc.root.pin.")) for label in blocks)
    assert "return.cleanup.peek" not in body
    assert "@pcc_gc_list_container_mode" not in body
    assert "@pcc_gc_take_slot(" in body
