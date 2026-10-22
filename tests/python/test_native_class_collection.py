"""Classes created and dropped at runtime are collected on all five collectors.

Each collected class frees the name copy py_class_new made. The program builds
classes in a loop, uses an instance of each, keeps weak references and churns
allocations so every collector runs; the output must match CPython's.

A method call consumes a receiver its own expression created
(``cls(2).method()``), as it consumes argument temporaries; the receiver
program checks each lowering of that shape finalizes the temporary at once.
"""
import os
from pathlib import Path
import subprocess
import sys

PROGRAM = '''import gc
import weakref


def make(index):
    class Local:
        tag = "local-" + str(index)

        def __init__(self, value):
            self.value = value

        def describe(self):
            return type(self).tag + ":" + str(self.value)

    return Local


def churn(n):
    out = []
    i = 0
    while i < n:
        out.append([i, str(i)])
        i += 1
    return len(out)


def main():
    refs = []
    seen = []
    i = 0
    while i < 300:
        cls = make(i)
        seen.append(cls(i * 2).describe())
        refs.append(weakref.ref(cls))
        cls = None
        churn(50)
        i += 1
    gc.collect()
    churn(2000)
    gc.collect()
    print(len(seen), seen[0], seen[-1])
    print(sum(1 for ref in refs if ref() is None))


main()
'''


RECEIVER_PROGRAM = '''import gc
import weakref

events = []


def make(index):
    class Local:
        tag = "local-" + str(index)

        def __init__(self, value):
            self.value = value

        def plain(self):
            return self.value

        def keyed(self, k=0):
            return self.value + k

        def __del__(self):
            events.append(self.value)

    return Local


class Box:
    def __init__(self, value):
        self.value = value

    def plain(self):
        return self.value

    def keyed(self, k=0):
        return self.value + k

    def __del__(self):
        events.append(self.value)


def check(label, expect):
    print(label, expect in events)


def dyn_plain(cls):
    return cls(1).plain()


def dyn_keyed(cls):
    return cls(2).keyed(k=1)


def dyn_attr(cls):
    return cls(3).value


def dyn_missing(cls):
    try:
        cls(4).missing()
    except AttributeError:
        return "attr-error"
    return "no-error"


def dyn_assign(cls):
    v = cls(5).plain()
    return v


def dyn_as_arg(cls):
    return str(cls(6).plain())


def static_plain():
    return Box(7).plain()


def static_keyed():
    return Box(8).keyed(k=1)


def static_attr():
    return Box(9).value


def static_missing():
    try:
        Box(10).missing()
    except AttributeError:
        return "attr-error"
    return "no-error"


def factory_call():
    return make(99)(11).plain()


def main():
    cls = make(0)
    print(dyn_plain(cls)); check("dyn_plain", 1)
    print(dyn_keyed(cls)); check("dyn_keyed", 2)
    print(dyn_attr(cls)); check("dyn_attr", 3)
    print(dyn_missing(cls)); check("dyn_missing", 4)
    print(dyn_assign(cls)); check("dyn_assign", 5)
    print(dyn_as_arg(cls)); check("dyn_as_arg", 6)
    print(static_plain()); check("static_plain", 7)
    print(static_keyed()); check("static_keyed", 8)
    print(static_attr()); check("static_attr", 9)
    print(static_missing()); check("static_missing", 10)
    print(factory_call()); check("factory_call", 11)
    ref = weakref.ref(cls)
    cls = None
    gc.collect()
    print("class dead", ref() is None)


main()
'''


def _assert_matches_cpython_on_five_collectors(tmp_path: Path, archive, name: str, program: str):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / (name + ".py")
    source.write_text(program, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / name
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(archive))
    for backend in range(5):
        ran = subprocess.run([str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                             capture_output=True, text=True, timeout=60)
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr[-2000:]
        assert ran.stdout == expected.stdout, f"GC{backend}: {ran.stdout}"


def test_runtime_classes_are_collected_on_five_collectors(tmp_path: Path, pcc_runtime_archive):
    _assert_matches_cpython_on_five_collectors(tmp_path, pcc_runtime_archive, "classes", PROGRAM)


def test_method_call_releases_its_temporary_receiver(tmp_path: Path, pcc_runtime_archive):
    _assert_matches_cpython_on_five_collectors(
        tmp_path, pcc_runtime_archive, "receivers", RECEIVER_PROGRAM,
    )
