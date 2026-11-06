"""Python reference 6.3.1/6.3.4: descriptor lookup precedes call arguments.

Reference 3.1 permits delayed garbage collection. Check exactly-once finalization
separately from lookup/argument order for tracing collectors; GC0 additionally
checks immediate release of a temporary descriptor receiver.
https://docs.python.org/3/reference/datamodel.html#objects-values-and-types
"""

import os
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


pytestmark = pytest.mark.xdist_group(name="vthread_loaded_methods")

PROGRAM = '''import gc
import pcc.virtual_thread as vt
events = []
drops = []
def plain(value):
    return value + 100
class Worker:
    factor = 3
    def __init__(self, base):
        self.base = base
    def method(self, value):
        return self.base + value
    @staticmethod
    def static(value):
        return value + 20
    @classmethod
    def classed(cls, value):
        return cls.factor + value
class Property:
    @property
    def method(self):
        events.append("lookup")
        return plain
    def __del__(self):
        drops.append("drop")
class Broken:
    @property
    def method(self):
        events.append("lookup-error")
        raise ValueError("lookup")
def argument(value):
    gc.collect()
    events.append("argument")
    return value
def mutate(obj, value):
    obj.method = plain
    gc.collect()
    return value
def bad_argument():
    gc.collect()
    raise ValueError("argument")
def main():
    obj = Worker(10)
    print(vt.call(obj.method, 1), vt.call(obj.method, 2))
    print(vt.call(obj.static, 2), vt.call(obj.classed, 2))
    # Load the old method before an argument shadows it on this instance.
    print(vt.call(obj.method, mutate(obj, 3)))
    print(vt.call(obj.method, 3))
    events.clear()
    print(vt.call(Property().method, argument(7)))
    gc.collect()
    print(events)
    print(drops)
    events.clear()
    try:
        vt.call(Broken().method, argument(8))
    except ValueError as error:
        print(str(error))
    print(events)
    try:
        vt.call(Worker(4).method, bad_argument())
    except ValueError as error:
        print(str(error))
    try:
        vt.call(obj.method)
    except TypeError:
        print("binding-error")
    print(vt.call(plain, 4))
main()
'''

IMMEDIATE_PROGRAM = PROGRAM.replace(
    '    events.append("argument")',
    '    assert drops == ["drop"]\n    events.append("argument")',
)

PARK_PROGRAM = '''import gc
import pcc.virtual_thread as vt
class Worker:
    def __init__(self, base):
        self.base = base
    def method(self, value):
        vt.yield_now()
        gc.collect()
        return self.base + value
def argument(value):
    vt.yield_now()
    gc.collect()
    return value
def run():
    # Warm resolution, then exercise the cached method with a temporary
    # receiver whose only remaining owner spans a parking argument.
    first = vt.call(Worker(10).method, vt.call(argument, 2))
    second = vt.call(Worker(20).method, vt.call(argument, 3))
    return first + second
def main():
    task = vt.spawn(run)
    vt.run_until_idle(1000)
    assert vt.outcome(task) == vt.OUTCOME_RETURNED
    assert vt.result(task) == 35
    print("LOADED_DEFERRED_METHOD_OK")
main()
'''


def test_deferred_attribute_call_loads_method_before_arguments(tmp_path):
    source = tmp_path / "loaded.py"
    source.write_text('''import pcc.virtual_thread as vt
def argument():
    return 7
def worker(receiver):
    return vt.call(receiver.method, argument())
''')
    output = source.with_suffix(".ll")
    compile_python(str(source), str(output), emit_llvm_only=True, backend="self")
    body = re.search(r"(?ms)^define[^\n]*@[^\n]*worker__gen_resume\([^\n]*\{\n(.*?)^}", output.read_text())
    assert body is not None
    assert "@py_obj_load_method(" in body[1]
    assert "@py_obj_call_method_deferred(" in body[1]
    assert "@py_obj_getattr(" not in body[1]
    assert body[1].index("@py_obj_load_method(") < body[1].index("@user_loaded_argument(")
    before_argument = body[1][:body[1].index("@user_loaded_argument(")]
    # Both plain-method and descriptor paths converge on this root clear;
    # only the separately rooted bound receiver may survive into arguments.
    ready = re.search(r"(?ms)^vthread.call.target.ready[^\n]*:\n(.*?)(?=^\w[^\n]*:|\Z)", before_argument)
    assert ready is not None
    assert re.search(r"@pcc_gc_store_root\(ptr %vthread.call.target.receiver[^,]+, ptr null\)", ready[1])


@pytest.fixture(scope="module")
def loaded_binaries(tmp_path_factory):
    root = tmp_path_factory.mktemp("loaded-deferred-method")
    results = {}
    for name, program in (("semantic", PROGRAM), ("parking", PARK_PROGRAM),
                          ("immediate", IMMEDIATE_PROGRAM)):
        source = root / (name + ".py")
        source.write_text(program)
        expected = "LOADED_DEFERRED_METHOD_OK\n"
        if name != "parking":
            reference = subprocess.run([sys.executable, str(source)],
                                       capture_output=True, text=True, timeout=10)
            assert reference.returncode == 0, reference.stderr
            expected = reference.stdout
        binary = source.with_suffix("")
        build = subprocess.run([sys.executable, "-m", "pcc", "--backend", "self",
                                str(source), "-o", str(binary)], capture_output=True,
                               text=True, timeout=120, env=dict(os.environ, PCC_NO_AUTO_PCC1="1"))
        assert build.returncode == 0, build.stdout + build.stderr
        results[name] = (binary, expected)
    return results


@pytest.mark.integration
@pytest.mark.parametrize("program", ("semantic", "parking"))
@pytest.mark.parametrize("collector", range(5))
def test_loaded_deferred_methods_preserve_semantics(loaded_binaries, program, collector):
    binary, expected = loaded_binaries[program]
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, PCC_GC_BACKEND=str(collector)))
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, "")


@pytest.mark.integration
def test_gc0_releases_temporary_descriptor_receiver_before_arguments(loaded_binaries):
    binary, expected = loaded_binaries["immediate"]
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, PCC_GC_BACKEND="0"))
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, "")
