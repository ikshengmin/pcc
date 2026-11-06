"""Python reference 6.3.4: lookup/binding precede arguments and method calls.

A dynamic receiver's method name does not establish that it is numeric. This
regression exercises builtin values and user methods through the same calls.
"""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


pytestmark = pytest.mark.xdist_group(name="dynamic_numeric_method_guard")


PROGRAM = '''
events = []
class View:
    def to_bytes(self, arbitrary="plain"):
        events.append("custom:" + arbitrary)
        return b"view"
    def bit_length(self):
        return "custom length"
    def bit_count(self):
        return ["custom count"]
    def is_integer(self):
        return ("custom integer", False)
class PropertyView:
    @property
    def to_bytes(self):
        events.append("lookup")
        return self.convert
    def convert(self, arbitrary):
        return ["converted", arbitrary]
class Broken:
    @property
    def to_bytes(self):
        events.append("broken lookup")
        raise RuntimeError("lookup")
class Missing:
    pass
def mark(name, value):
    events.append(name)
    return value
def encode(value):
    return value.to_bytes()
def encode_keyword(value):
    return mark("receiver", value).to_bytes(arbitrary=mark("argument", "named"))
def length(value):
    return value.bit_length()
def count(value):
    return value.bit_count()
def integral(value):
    return value.is_integer()
def wide(value):
    return value.to_bytes(16, "little", signed=True)
def main():
    view = View()
    print(encode(view), encode(65), encode(True))
    print(length(view), length(2 ** 80), length(True), length(False))
    print(count(view), count(255), count(True), count(False))
    print(integral(view), integral(1.5), integral(2.0), integral(2 ** 2000), integral(True))
    print(wide(-(2 ** 80)))
    events.clear()
    print(encode_keyword(view))
    print(events)
    events.clear()
    print(encode_keyword(PropertyView()))
    print(events)
    events.clear()
    try:
        encode_keyword(Broken())
    except RuntimeError:
        print("lookup failed")
    print(events)
    try:
        encode(Missing())
    except AttributeError:
        print("missing method")
    try:
        encode_keyword(1)
    except TypeError:
        print("builtin binding error")
    print(encode(view), encode(66))
main()
'''


def test_dynamic_numeric_paths_guard_the_receiver_and_retain_generic_lookup(tmp_path):
    source = tmp_path / "numeric_guard.py"
    source.write_text(PROGRAM)
    output = tmp_path / "numeric_guard.ll"
    compile_python(str(source), str(output), emit_llvm_only=True, backend="self")
    text = output.read_text()
    for helper in ("py_obj_type_tag", "py_obj_getattr", "py_obj_call_slots",
                   "py_int_to_bytes_args", "py_int_bit_length", "py_int_bit_count"):
        assert "@" + helper + "(" in text
    assert "numeric.method.native" in text and "numeric.method.generic" in text
    assert not any("call " in line and "@py_cpy_" in line for line in text.splitlines())


@pytest.fixture(scope="module")
def compiled_numeric_guard(tmp_path_factory):
    directory = tmp_path_factory.mktemp("numeric-method-guard")
    source = directory / "numeric_guard.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=10)
    assert reference.returncode == 0, reference.stderr
    binary = directory / "numeric_guard"
    build = subprocess.run([sys.executable, "-m", "pcc", "--backend", "self",
                            str(source), "-o", str(binary)], capture_output=True,
                           text=True, timeout=120, env=dict(os.environ, PCC_NO_AUTO_PCC1="1"))
    assert build.returncode == 0, build.stdout + build.stderr
    return binary, reference.stdout


@pytest.mark.integration
@pytest.mark.parametrize("backend", range(5))
def test_native_dynamic_numeric_methods_match_cpython(compiled_numeric_guard, backend):
    binary, expected = compiled_numeric_guard
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, "")
