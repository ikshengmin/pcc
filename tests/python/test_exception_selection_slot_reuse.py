"""Short-lived exception-selection slots are one per function, not per site.

``handled.resume.pending`` (a generator's resume with a pending exception),
``except.class`` (an except clause's class while matching) and
``except.class.replacement`` (an exception raised while evaluating that
class) hold their value from creation to a clear a few instructions later,
so a function reuses one slot per label. Each site used to add a hidden owned
local that every exit registered, released and left. Handler bindings and
``except.match`` stay per site, because a handler body can nest another try
(Python Language Reference 8.4 "The try statement").
"""
from __future__ import annotations

import re
import sys

from tests.python.process_timeout import run_process_group_timeout


SOURCE = '''
class Canary:
    def __init__(self, name):
        self.name = name
    def __del__(self):
        print("del", self.name)


def bad_class():
    raise RuntimeError("class evaluation failed")


def classify(n):
    try:
        if n == 0:
            raise KeyError("k")
        if n == 1:
            raise ValueError("v")
        if n == 2:
            raise TypeError("t")
        return "none"
    except KeyError:
        return "key"
    except ValueError as error:
        try:
            raise IndexError(str(error))
        except IndexError as inner:
            return "nested " + str(inner)
    except (TypeError, ArithmeticError):
        return "type"


def replacement():
    try:
        try:
            raise ValueError("original")
        except bad_class():
            return "matched"
    except RuntimeError as error:
        context = error.__context__
        return "replaced " + str(error) + " from " + type(context).__name__


def resumed():
    try:
        raise ValueError("held")
    except ValueError as error:
        first = Canary("first")
        yield 1
        first = None
        second = Canary("second")
        yield 2
        yield str(error)
    yield 4


for n in range(4):
    print(classify(n))
print(replacement())
for value in resumed():
    print("got", value)
print("end")
'''


def _ir(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.setenv("PCC_LOG", "")
    source = tmp_path / "selection.py"
    source.write_text(SOURCE)
    output = tmp_path / "selection.ll"
    compile_python(str(source), str(output), backend="self",
                   ir_scaffold_mode="on", emit_llvm_only=True)
    return output.read_text()


def _body(text, name):
    match = re.search(r"define[^\n]*" + name + r"[^\n]*\{\n(.*?)\n\}", text, re.S)
    assert match is not None, name
    return match.group(1)


def test_one_selection_slot_per_label_per_function(tmp_path, monkeypatch):
    text = _ir(tmp_path, monkeypatch)
    classify = _body(text, "classify")
    # Three except clauses (one a tuple) and a nested handler: one class slot.
    assert len(re.findall(r"%\"?except\.class\.slot[.0-9]*\"? = alloca", classify)) == 1
    resumed = _body(text, "resumed__gen_resume")
    # Three parks inside one handler: one pending-exception slot.
    assert len(re.findall(r"%\"?handled\.resume\.pending\.slot[.0-9]*\"? = alloca", resumed)) == 1


def test_native_selection_matches_cpython(tmp_path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "selection.py"
    source.write_text(SOURCE)
    reference = run_process_group_timeout(
        [sys.executable, "-E", "-B", str(source)], timeout=30,
    )
    assert reference.returncode == 0 and not reference.stderr, reference.stderr
    binary = tmp_path / "selection"
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    ran = run_process_group_timeout([str(binary)], timeout=30)
    assert (ran.returncode, ran.stderr) == (0, ""), ran.stderr[-2000:]
    assert ran.stdout == reference.stdout
