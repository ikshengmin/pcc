"""A returned tagged int or NULL takes no graph transaction or pin.

The return path reloads and pins a heap result under the graph lock so a
moving collector cannot relocate it between the reload and the pin. A
tagged integer has no header and never moves: every call returning one
used to serialize all worker threads on that global lock (the call
primitive spent 84% of its time waiting for it with four workers).
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


def test_scalar_return_skips_graph_lock_and_heap_return_keeps_it(tmp_path):
    source = tmp_path / "scalar_return.py"
    source.write_text(SOURCE)
    output = tmp_path / "scalar_return.ll"
    compile_python(str(source), str(output), backend="self",
                   ir_scaffold_mode="on", emit_llvm_only=True)
    body = _function(output.read_text(), "user_scalar_return_add")
    blocks = _blocks(body)
    locking = [label for label, lines in blocks.items()
               if any("@pcc_py_gc_minor_graph_lock(" in line for line in lines)]
    assert locking, "the heap-result branch keeps its graph transaction"
    # Every lock sits in the branch the peeked result selects only when it
    # is a non-NULL, untagged pointer.
    assert all(label.startswith("return.pin.header") for label in locking), locking
    for label, lines in blocks.items():
        branch = next((line for line in lines if line.startswith("br i1")), None)
        if branch is None or "return.pin.header" not in branch:
            continue
        assert any("return.cleanup.peek" in line for line in lines), lines
        assert re.search(r"label %return\.pin\.ready", branch), branch
