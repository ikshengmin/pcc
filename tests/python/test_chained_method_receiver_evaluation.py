"""Python reference 6.3.1/6.3.4: evaluate a method receiver once.

The ordinary native route must not pre-evaluate an attribute chain to probe
for a foreign representation. Lookup order and ownership are both observable.
"""

import os
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python

pytestmark = pytest.mark.xdist_group(name="chained_method_receivers")

OBJECTS = '''counts = [0,0,0,0,0,0,0,0,0,0,0]
class Token:
    def __init__(self, kind):
        self.kind = kind
        self.data = [1]
    def __del__(self):
        counts[self.kind] += 1
    def touch(self):
        return None
    def keep(self):
        return self
    @staticmethod
    def static():
        return 1
    @classmethod
    def classed(cls):
        return 2
class Box:
    def __init__(self, token):
        self.token = token
class KeepBox:
    def __init__(self, token):
        self.token = token.keep()
class OptionalBox:
    def __init__(self, token=None):
        if token is None:
            token = Token(5)
        self.token = token
class TypedBox:
    def __init__(self, token: Token):
        self.token = token
'''

OWNERSHIP = '''import gc
from receiver_objects import Token, Box, KeepBox, OptionalBox, TypedBox, counts

def direct():
    token = Token(0)
def stored():
    box = Box(Token(1))
def method():
    box = Box(Token(2))
    box.token.touch()
def field():
    box = Box(Token(3))
    assert box.token.data[0] == 1
def kept():
    box = KeepBox(Token(4))
def optional():
    box = OptionalBox(Token(5))
def through():
    box = Box(Token(6))
    child = box.token
    child.touch()
def result():
    box = Box(Token(7))
    value = box.token.keep()
    assert value is box.token
def discarded():
    box = Box(Token(8))
    box.token.keep()
def static():
    box = TypedBox(Token(9))
    assert box.token.static() == 1
def classed():
    box = TypedBox(Token(10))
    assert box.token.classed() == 2
def run():
    for index in range(100):
        direct()
        stored()
        method()
        field()
        kept()
        optional()
        through()
        result()
        discarded()
        static()
        classed()
def main():
    run()
    gc.collect()
    print(counts)
main()
'''

SIDE_EFFECTS = r'''import gc
events = []
class Child:
    def __init__(self, number):
        self.number = number
    def method(self, value=0):
        events.append("call")
        return self.number + value
class Parent:
    def __init__(self):
        self.number = 0
    @property
    def child(self):
        self.number += 1
        events.append("lookup")
        return Child(self.number)
class Broken:
    @property
    def child(self):
        events.append("broken")
        raise ValueError("lookup")
def argument():
    gc.collect()
    events.append("argument")
    return 7
def bad_argument():
    events.append("argument-error")
    raise ValueError("argument")
def open():
    events.append("receiver")
    return Parent()
def main():
    parent = Parent()
    print(parent.child.method(argument()))
    print(events)
    events.clear()
    print(parent.child.method(value=argument()))
    print(events)
    events.clear()
    parent.child.method()
    print(events)
    events.clear()
    print(open().child.method(argument()))
    print(events)
    events.clear()
    try:
        Broken().child.method(argument())
    except ValueError as error:
        print(str(error), events)
    events.clear()
    try:
        parent.child.method(bad_argument())
    except ValueError as error:
        print(str(error), events)
main()
'''


def test_native_chained_receiver_has_one_attribute_evaluation(tmp_path):
    source = tmp_path / "receiver_once.py"
    source.write_text("""class Child:
    def method(self):
        return 7
class Parent:
    def __init__(self, child):
        self.child = child
def invoke(parent):
    return parent.child.method()
""")
    output = source.with_suffix(".ll")
    compile_python(str(source), str(output), emit_llvm_only=True, backend="self")
    body = re.search(r"(?ms)^define[^\n]*@user_receiver_once_invoke\([^\n]*\{\n(.*?)^}", output.read_text())
    assert body is not None and "strict.nolib.stub" not in body[1]
    assert body[1].count("@py_obj_getattr(") == 1
    assert "@py_instance_get_field(" not in body[1]
    assert "@py_obj_load_method(" in body[1]


@pytest.fixture(scope="module")
def receiver_binaries(tmp_path_factory):
    root = tmp_path_factory.mktemp("chained-receiver")
    (root / "receiver_objects.py").write_text(OBJECTS)
    results = {}
    for name, program in (("ownership", OWNERSHIP), ("side_effects", SIDE_EFFECTS)):
        source = root / (name + ".py")
        source.write_text(program)
        reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                                   text=True, timeout=10)
        assert reference.returncode == 0, reference.stderr
        binary = root / name
        built = subprocess.run(
            [sys.executable, "-m", "pcc", "--backend", "self", str(source), "-o", str(binary)],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, PCC_NO_AUTO_PCC1="1"),
        )
        assert built.returncode == 0, built.stdout + built.stderr
        results[name] = (binary, reference.stdout)
    return results


@pytest.mark.integration
@pytest.mark.parametrize("collector", range(5))
@pytest.mark.parametrize("shape", ("ownership", "side_effects"))
def test_chained_method_receivers_match_python(receiver_binaries, shape, collector):
    binary, expected = receiver_binaries[shape]
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, PCC_GC_BACKEND=str(collector)))
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, "")
