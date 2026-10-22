"""Growing a list that holds young objects stays linear on every collector.

Under GC4 a long list is old while each append stores a young node into it, so
its slots wait in the store buffer and the remembered set. Every capacity
growth moves those pending slots to the new payload. The move used to look up
each pending slot in the growth's list of (old slot, new slot) pairs, one pair
per element, so one growth cost pending slots times list length: building a
200,000-node list took 36 s on GC4 and 0.8 s on GC0.

The program must print what CPython prints on all five collectors. Each run
takes about 3.4 s on GC4 and under 2.1 s on the others; the 40 s bound holds
under load, and the quadratic move needed about twice that.
"""
import os
from pathlib import Path
import subprocess
import sys

PROGRAM = '''class Node:
    def __init__(self, value):
        self.value = value
        self.prev = None


def build(n):
    nodes = []
    i = 0
    while i < n:
        node = Node(i)
        if i > 0:
            node.prev = nodes[i - 1]
        nodes.append(node)
        i += 1
    return nodes


def main():
    nodes = build(300000)
    total = 0
    for node in nodes:
        total += node.value
    print(len(nodes), total, nodes[-1].prev.value)


main()
'''


def test_old_list_growth_with_young_items_is_linear_on_five_collectors(
    tmp_path: Path, pcc_runtime_archive
):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "old_list_growth.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=60
    )
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "old_list_growth"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                             capture_output=True, text=True, timeout=40)
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr[-2000:]
        assert ran.stdout == expected.stdout, f"GC{backend}: {ran.stdout}"
