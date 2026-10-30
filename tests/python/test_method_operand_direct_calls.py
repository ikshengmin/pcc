"""A known class's method called as an object operand calls the method
directly and publishes its result at the return instruction.

``s += v.dot(w) % 7`` registers an owning output slot for ``v.dot(w)`` before
its operands evaluate. Such a call took the runtime-bound route: getattr
built a bound method (a signature tuple, captured defaults, fourteen scheduler
roots), and py_obj_call_slots called it through an argument tuple. Two
million iterations of tests/benchmarks/threading/programs/st_objects.py took
26 s on GC0 against CPython's 0.6 s, 39% of it binding methods. The direct
method ABI publishes into that slot itself (through _call_user), so only a
closure method, run-time-bound definition defaults, an async or may-park
method and a raw-pointer result keep the bound call.

The program covers object, int, big int, float, str, bool and None results in
operands, an exception raised by a method in an operand, a subclass override
and a classmethod; its output must equal CPython's on all five collectors.
A method a subclass overrides keeps the run-time lookup.
"""
from pathlib import Path
import os
import re
import subprocess
import sys

PROGRAM = '''class Vec:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def add(self, other):
        return Vec(self.x + other.x, self.y + other.y)

    def dot(self, other):
        return self.x * other.x + self.y * other.y

    def big(self):
        return 10 ** 30 + self.x

    def ratio(self):
        return self.x / 4

    def name(self):
        return "v" + str(self.x)

    def positive(self):
        return self.x > 0

    def nothing(self):
        return None

    def fail(self):
        raise ValueError("bad " + str(self.x))

    @classmethod
    def unit(cls, scale):
        return cls(scale, scale)


class Scaled(Vec):
    def name(self):
        return "s" + str(self.x)


def apply(o, other):
    # Parameters of no known class: the receiver's class is found at run
    # time; an instance attribute shadows a method of the same name.
    total = o.dot(other) % 7 + len(o.name())
    try:
        total += o.missing() + 1
    except AttributeError as error:
        total += len(str(error))
    return total


def main():
    acc = Vec(0, 0)
    s = 0
    names = []
    total = 0.0
    for i in range(3000):
        v = Vec(i, i + 1)
        acc = acc.add(v)
        s += v.dot(acc) % 7
        names.append(v.name() + "!")
        total += v.ratio() * 2
        if v.positive() and v.nothing() is None:
            s += 1
        pair = [v.add(v).x, v.add(acc).y]
        s += pair[0] - pair[1]
        b = v.big() + 1
        if b % 1000 == 1:
            s += 1
        try:
            s += v.fail() + 1
        except ValueError as error:
            if str(error) != "bad " + str(i):
                print("wrong error", error)
    w = Scaled(3, 4)
    print(acc.x, acc.y, s, total, len(names), names[-1])
    print(w.dot(Vec(1, 1)) + 1, Vec.unit(5).dot(Vec(1, 2)) * 3, w.name() + "?")
    shadowed = Vec(2, 3)
    shadowed.ratio = lambda: 100
    print(apply(Vec(1, 2), w), apply(w, shadowed), apply(shadowed, w), shadowed.ratio() + 1)


main()
'''


def _compile(tmp_path: Path, archive) -> tuple[Path, str]:
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "method_operands.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=60
    )
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "method_operands"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(archive))
    return binary, expected.stdout


def test_method_operands_match_cpython_on_five_collectors(tmp_path: Path, pcc_runtime_archive):
    binary, expected = _compile(tmp_path, pcc_runtime_archive)
    for backend in range(5):
        ran = subprocess.run([str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                             capture_output=True, text=True, timeout=120)
        assert ran.returncode == 0, f"GC{backend}: " + ran.stderr[-2000:]
        assert ran.stdout == expected, f"GC{backend}: {ran.stdout}"


def _ir_text(tmp_path: Path) -> str:
    from pcc.frontends.python.pipeline import compile_python

    ir_path = tmp_path / "method_operands.ll"
    if not ir_path.exists():
        source = tmp_path / "method_operands.py"
        source.write_text(PROGRAM, encoding="utf-8")
        compile_python(str(source), str(ir_path), emit_llvm_only=True, backend="self",
                       libpython_mode="off", ir_scaffold_mode="on")
    return ir_path.read_text(encoding="utf-8")


def _main_ir(tmp_path: Path) -> str:
    body = re.search(r"^define [^\n]*@user_method_operands_main\(.*?^}", _ir_text(tmp_path),
                     re.M | re.S)
    assert body is not None
    return body.group(0)


def test_method_operand_calls_the_method_and_publishes_at_return(tmp_path: Path):
    body = _main_ir(tmp_path)
    # Every Vec method no subclass overrides and nothing rebinds is called
    # directly on a receiver of known class; none is bound. ``name`` (Scaled
    # overrides it) and ``ratio`` (an instance attribute shadows it) keep
    # the run-time lookup, as does ``dot`` on ``Vec.unit(5)``, whose class
    # the compiler does not know.
    for method in ("add", "dot", "big", "positive", "nothing", "fail"):
        assert re.search(r"\bcall [^\n]*@user_method_operands_Vec_" + method + r"\(", body), method
    for method in ("name", "ratio"):
        assert not re.search(r"\bcall [^\n]*@user_method_operands_Vec_" + method + r"\(", body), method
    assert not re.search(r"@py_obj_getattr\([^\n]*@\.pyattr\.(add|dot|big|positive|nothing|fail)\b",
                         body)
    # ``o.dot(other) % 7`` on receivers of unknown class loads the method
    # (py_obj_load_method) before the arguments and calls it with the
    # receiver; no bound method, no generic slot call.
    apply_body = re.search(r"^define [^\n]*@user_method_operands_apply\(.*?^}",
                           _ir_text(tmp_path), re.M | re.S).group(0)
    assert re.search(r"\bcall [^\n]*@py_obj_load_method\(", apply_body)
    assert re.search(r"\bcall [^\n]*@py_obj_call_loaded_method\(", apply_body)
    assert not re.search(r"@py_obj_getattr\(", apply_body)
    assert not re.search(r"\bcall [^\n]*@py_obj_call_slots\(", apply_body)
    # The dot result in ``v.dot(acc) % 7`` is stored into its operand slot
    # by the very next instruction after the call returns.
    call = re.search(r"(%[\w.]+) = call [^\n]*@user_method_operands_Vec_dot\(ptr %v[^\n]*\n\s*([^\n]+)", body)
    assert call is not None
    assert re.match(r"store ptr " + re.escape(call.group(1)) + r", ptr %binary\.object\.", call.group(2))

