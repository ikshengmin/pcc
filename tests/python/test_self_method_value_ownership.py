"""Python reference 3.2.8.2 / 6.3.1: bound method values own their receiver.

Obtaining a callback inside its class must use the same binding and lifetime
contract as obtaining it through an external instance reference.
"""

import os
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python

pytestmark = pytest.mark.xdist_group(name="self_method_values")

LIFETIME = '''import gc
counts = [0, 0, 0, 0]
class Token:
    def __init__(self, kind):
        self.kind = kind
        self.callback = None
    def method(self, number=1):
        return number
    def call_bound(self):
        callback = self.method
        assert callback() == 1
    def make_cycle(self):
        self.callback = self.method
    def __del__(self):
        counts[self.kind] += 1
def ordinary():
    obj = Token(0)
    callback = obj.method
    assert callback() == 1
    return obj
def cyclic():
    obj = Token(1)
    obj.callback = obj.method
    return obj
def own_bound():
    obj = Token(2)
    obj.call_bound()
    return obj
def own_cycle():
    obj = Token(3)
    obj.make_cycle()
    return obj
def run():
    objects = []
    for index in range(101):
        objects.append(ordinary())
        objects.append(cyclic())
        objects.append(own_bound())
        objects.append(own_cycle())
    return objects
def main():
    objects = run()
    # Keep each shape's last created object explicitly live. Every earlier
    # owner must retire, including cycles; the live canaries must survive.
    # This avoids treating a conservative last-use word as an ownership leak.
    live = objects[-4:]
    objects.clear()
    # Require complete finalization, allowing bounded collector progress for
    # concurrent marking and relocation retirement. Never relax the count.
    for attempt in range(8):
        gc.collect()
        if counts == [100, 100, 100, 100]:
            break
    print(counts)
    for token in live:
        assert token.method() == 1
main()
'''

BINDING = r'''import gc
class Base:
    def target(self, value=1):
        gc.collect()
        return "base:" + str(value)
    def callback(self):
        return self.target
    def __private(self):
        return 37
    def private_callback(self):
        return self.__private
class Derived(Base):
    def target(self, value=1):
        gc.collect()
        return "derived:" + str(value)
    def __private(self):
        return 38
    def own_private_callback(self):
        return self.__private
def replacement(value=1):
    return "instance:" + str(value)
def class_replacement(self, value=1):
    return "new:" + str(value)
def main():
    base = Base()
    callback = base.callback()
    print(callback(value=7), callback.__self__ is base)
    derived = Derived()
    print(derived.callback()(8))
    derived.target = replacement
    print(derived.callback()(9))
    print(base.private_callback()())
    print(derived.private_callback()(), derived.own_private_callback()())
    print(base.private_callback().__name__)
    print(hasattr(Base, "__private"), hasattr(Base, "_Base__private"))
    print(callback())
    Base.target = class_replacement
    # The saved callback keeps its selected function after class mutation.
    print(callback(5), base.callback()(6))
main()
'''

PARKING = r'''import gc
import pcc.virtual_thread as vt
class Worker:
    def __init__(self, value):
        self.value = value
    def target(self, value):
        vt.yield_now()
        gc.collect()
        return self.value + value
    def callback(self):
        return self.target
def argument():
    vt.yield_now()
    gc.collect()
    return 2
def run():
    callback = Worker(40).callback()
    return vt.call(callback, vt.call(argument))
def main():
    task = vt.spawn(run)
    vt.run_until_idle(100)
    assert vt.outcome(task) == vt.OUTCOME_RETURNED
    assert vt.result(task) == 42
    print("BOUND_CALLBACK_PARK_OK")
main()
'''


def test_self_method_value_uses_the_shared_attribute_owner(tmp_path):
    source = tmp_path / "self_method.py"
    source.write_text("""class Worker:
    def method(self, value=1):
        return value
    def callback(self):
        return self.method
""")
    output = source.with_suffix(".ll")
    compile_python(str(source), str(output), emit_llvm_only=True, backend="self")
    body = re.search(r"(?ms)^define[^\n]*@user_self_method_Worker_callback\([^\n]*\{\n(.*?)^}", output.read_text())
    assert body is not None and "strict.nolib.stub" not in body[1]
    assert "@py_obj_getattr(" in body[1]
    assert "@pcc_gc_take_pinned_slot(" in body[1]
    assert "Worker_method_bound_adapter" not in output.read_text()


@pytest.fixture(scope="module")
def self_method_binaries(tmp_path_factory):
    root = tmp_path_factory.mktemp("self-method-values")
    results = {}
    for name, source_text in (("lifetime", LIFETIME), ("binding", BINDING), ("parking", PARKING)):
        source = root / (name + ".py")
        source.write_text(source_text)
        expected = "BOUND_CALLBACK_PARK_OK\n"
        if name != "parking":
            reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                                       text=True, timeout=10)
            assert reference.returncode == 0, reference.stderr
            expected = reference.stdout
        binary = root / name
        built = subprocess.run(
            [sys.executable, "-m", "pcc", "--backend", "self", str(source), "-o", str(binary)],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, PCC_NO_AUTO_PCC1="1"),
        )
        assert built.returncode == 0, built.stdout + built.stderr
        results[name] = (binary, expected)
    return results


@pytest.mark.integration
@pytest.mark.parametrize("collector", range(5))
@pytest.mark.parametrize("shape", ("lifetime", "binding", "parking"))
def test_self_method_values_preserve_binding_and_lifetimes(self_method_binaries, shape, collector):
    binary, expected = self_method_binaries[shape]
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, PCC_GC_BACKEND=str(collector)))
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, "")
