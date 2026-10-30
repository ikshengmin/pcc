"""Inherited str length: reference/IR checks and the shared owned native gate.

The complete program is shared unchanged by the three routes. Host execution
and IR checks are not native qualification. The integration gate requires an
explicit source-matched runtime and executes the emitted program with every
requested GC backend; it never provisions a compiler or archive implicitly.
The native wrapper lives in test_bound_find_protocol's seven-module aggregate,
so selecting all integration tests does not compile this corpus twice.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

import pytest


PROGRAM = r'''
import gc
from typing import Any

marker = ValueError('length-marker')

class Text(str):
    def __str__(self):
        raise AssertionError('length must use the builtin payload')
class Child(Text):
    pass
class Base(Text):
    def __len__(self):
        return 7
class Left(Base):
    pass
class Right(Base):
    def __len__(self):
        return 9
class Diamond(Left, Right):
    pass
class Missing(Text):
    __len__ = None
class Negative(Text):
    def __len__(self):
        return -1
class NotInteger(Text):
    def __len__(self):
        return 'not an integer'
class TooLarge(Text):
    def __len__(self):
        return 10 ** 100
class Index:
    def __index__(self):
        gc.collect()
        return 3
class Indexed(Text):
    def __len__(self):
        return Index()
class Broken(Text):
    def __len__(self):
        gc.collect()
        raise marker
class Hooked(Text):
    def __getattribute__(self, name):
        if name == '__len__':
            raise AssertionError('len must use type-level special lookup')
        return object.__getattribute__(self, name)

def size(value: Any):
    return len(value)
def base_size(value: Any):
    return str.__len__(value)
def instance_length():
    return 99

def main():
    for text, expected in (('', 0), ('ascii', 5), ('hé🙂\x00hé', 6), ('A\ud800B', 3)):
        for value in (text, Child(text)):
            assert size(value) == expected
            assert base_size(value) == expected
            saved = value.__len__
            assert saved.__self__ is value
            assert saved.__name__ == '__len__'
            assert saved() == expected
            assert getattr(value, '__len__')() == expected
    value = Child('hé🙂\x00hé')
    value.__len__ = instance_length
    assert value.__len__() == 99
    assert size(value) == 6 and base_size(value) == 6
    assert size(Hooked('hé🙂')) == 3
    assert size(Left('hé🙂')) == 7
    assert size(Diamond('hé🙂')) == 9
    assert base_size(Diamond('hé🙂')) == 3
    assert size(Indexed('abcdef')) == 3
    assert base_size(Indexed('abcdef')) == 6
    try:
        size(Missing('abc'))
    except TypeError:
        pass
    else:
        raise AssertionError('selected None __len__ was ignored')
    try:
        size(Negative('abc'))
    except ValueError:
        pass
    else:
        raise AssertionError('negative length accepted')
    try:
        size(NotInteger('abc'))
    except TypeError:
        pass
    else:
        raise AssertionError('non-integer length accepted')
    try:
        size(TooLarge('abc'))
    except OverflowError:
        pass
    else:
        raise AssertionError('overflowed length accepted')
    try:
        size(Broken('abc'))
    except ValueError as error:
        assert error is marker
    else:
        raise AssertionError('length exception lost')
    assert size(Child('after-error')) == 11
    assert base_size(Broken('abc')) == 3
    try:
        str.__len__()
    except TypeError:
        pass
    else:
        raise AssertionError('missing explicit receiver accepted')
    try:
        str.__len__('abc', 1)
    except TypeError:
        pass
    else:
        raise AssertionError('extra descriptor argument accepted')
    try:
        str.__len__(1)
    except TypeError:
        pass
    else:
        raise AssertionError('non-str receiver accepted')
    try:
        str.__len__(value='abc')
    except TypeError:
        pass
    else:
        raise AssertionError('unbound descriptor keyword accepted')
    text = Child('hé🙂')
    saved = text.__len__
    try:
        saved(1)
    except TypeError:
        pass
    else:
        raise AssertionError('extra bound argument accepted')
    try:
        saved(extra=1)
    except TypeError:
        pass
    else:
        raise AssertionError('bound descriptor keyword accepted')
    ident = id(text)
    del text
    gc.collect()
    assert id(saved.__self__) == ident and saved() == 3
    print('STR_LEN_OK')
main()
'''
EXPECTED = "STR_LEN_OK\n"


@pytest.fixture(autouse=True)
def forbid_implicit_native_provisioning(request, monkeypatch):
    # This runs before the parametrized compiler fixture can provision pcc1.
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    monkeypatch.setenv("PCC_NO_AUTO_PCC1", "1")
    if request.node.get_closest_marker("integration") is not None:
        assert os.environ.get("PCC_RUNTIME_ARCHIVE"), (
            "Set PCC_RUNTIME_ARCHIVE to a source-matched owned archive"
        )


def test_str_len_cpython_reference(tmp_path):
    source = tmp_path / "str_len_protocol.py"
    source.write_text(PROGRAM, encoding="utf-8")
    command = [sys.executable, str(source)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    receipt = {
        "scope": "CPython reference only; not PCC native execution",
        "command": command,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    (tmp_path / "reference.json").write_text(json.dumps(receipt, indent=2) + "\n")
    assert (result.returncode, result.stdout, result.stderr) == (0, EXPECTED, ""), receipt


def test_complete_str_len_protocol_has_no_strict_ir_stub(tmp_path):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module
    from tests.owned_ir_validation import verify_ir_text

    module = infer_module(parse_and_lift(PROGRAM, "str_len_protocol.py", "str_len_protocol"))
    generator = L1CodeGen(module, ir_scaffold_mode="on")
    generator._strict_no_libpython = True
    generator._prefer_native_callable_values = True
    text = str(generator.generate(module))
    (tmp_path / "str_len_protocol.py.txt").write_text(PROGRAM, encoding="utf-8")
    (tmp_path / "str_len_protocol.ll").write_text(text, encoding="utf-8")
    verify_ir_text(text)
    assert "strict.nolib.stub" not in text
    assert not re.search(r'\bcall\b[^\n]*@"?py_cpy_', text)
    for name, helper in (("size", "py_builtin_len"), ("base_size", "py_obj_call_slots")):
        function = generator.functions[name]
        definition = re.search(
            r'^define [^\n]*@"?' + re.escape(function.name) + r'"?\([^\n]*\{.*?^}',
            text, re.M | re.S,
        )
        assert definition is not None, function.name
        assert re.search(r'\bcall\b[^\n]*@"?' + helper + r'\(', definition.group(0))
