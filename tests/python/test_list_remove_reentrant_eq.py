"""list.remove deletes the slice at the compared index, as CPython does.

Reference: CPython Objects/listobject.c, ``list_remove``: after
``PyObject_RichCompareBool(obj, value, Py_EQ)`` returns true for index i it
calls ``list_ass_slice(self, i, i+1, NULL)``, which deletes whatever element
is at i by then, and nothing when the list became shorter than i + 1;
remove then returns None. Python Language Reference 3.15, "Sequence types":
``s.remove(x)`` removes the first item where ``s[i] == x``.

The runtime used to delete only when the compared element was still at
index, and otherwise scan again from the start, so an ``__eq__`` that kept
inserting before itself made remove compare forever. The program runs on all
five collectors and must print what CPython prints.
"""
import os
from pathlib import Path
import subprocess
import sys

PROGRAM = '''class InsertsBefore:
    def __init__(self, lst):
        self.lst = lst
        self.calls = 0

    def __eq__(self, other):
        self.calls += 1
        if self.calls > 5:
            raise RuntimeError("remove kept comparing")
        self.lst.insert(0, 4242)
        return True


class Clears:
    def __init__(self, lst):
        self.lst = lst
        self.calls = 0

    def __eq__(self, other):
        self.calls += 1
        self.lst.clear()
        return True


class Plain:
    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return self.value == other


def main():
    lst = []
    first = InsertsBefore(lst)
    lst.append(first)
    lst.append(99)
    lst.remove(123456)
    # The comparison inserted 4242 at index 0; the slice at the compared
    # index is deleted, which is now that 4242.
    print("inserts-before", first.calls, lst[0] is first, lst[1:], len(lst))

    lst = [1, 2]
    clears = Clears(lst)
    lst.append(clears)
    lst.remove(7)
    # The list is empty when the comparison returns: nothing is deleted and
    # remove returns without raising.
    print("clears", clears.calls, lst)

    lst = [Plain(1), Plain(2), Plain(3)]
    lst.remove(2)
    print("plain", [item.value for item in lst])

    try:
        lst.remove(9)
    except ValueError as exc:
        print("missing", exc)


main()
'''


def test_remove_deletes_at_the_compared_index_on_five_collectors(tmp_path: Path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "list_remove_reentry.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    assert "inserts-before 1 True [99] 2" in expected.stdout
    binary = tmp_path / "list_remove_reentry"
    compile_python(str(source), str(binary), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                             capture_output=True, text=True, timeout=60)
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr[-2000:]
        assert ran.stdout == expected.stdout, f"GC{backend}: {ran.stdout}"
