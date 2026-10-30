"""Dynamic/bound find: CPython controls and a separately selected native gate.

The integration test uses the repository's source-matched owned-runtime
contract, with pcc0/pcc1 and real execution under all five GC backends. It must
not be replaced by collection, IR, or the slot-adapter suite. Missing archives
or compiler fixtures are errors, never a reason to provision or silently skip.

Known baseline gap: bytes-subclass builtin payload construction is unsupported;
this file intentionally does not require a new inheritance implementation.
The slot-adapter companion verifies that instance tags cannot enter the exact
binary binder. Full bytes-subclass support requires a separate native gate.
Only existing whole-buffer memoryviews are in the positive corpus; released
or strided memoryview capabilities are not claimed.
"""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys

import pytest

from tests.python.test_str_len_protocol import (
    EXPECTED as STR_LEN_OUTPUT,
    PROGRAM as STR_LEN_PROGRAM,
)


@pytest.fixture(autouse=True)
def forbid_implicit_native_provisioning(request, monkeypatch):
    # Autouse runs before the compiler fixture: selecting pcc1 must not create
    # a compiler/runtime before explicit_owned_runtime can apply its guards.
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    monkeypatch.setenv("PCC_NO_AUTO_PCC1", "1")
    if request.node.get_closest_marker("integration") is not None:
        assert os.environ.get("PCC_RUNTIME_ARCHIVE"), (
            "Set PCC_RUNTIME_ARCHIVE to a source-matched owned archive"
        )


PROGRAMS = {
    "exact_binary": r'''
import gc
from typing import Any

def one(value: Any, needle: Any):
    return value.find(needle)
def two(value: Any, needle: Any, start: Any):
    return value.find(needle, start)
def three(value: Any, needle: Any, start: Any, end: Any):
    return value.find(needle, start, end)

def main():
    for value in (b'a\x00\xffababa', bytearray(b'a\x00\xffababa')):
        assert one(value, b'ab') == 3
        assert two(value, b'ab', 4) == 5
        assert three(value, b'ab', 0, 5) == 3
        assert one(value, 0) == 1
        assert one(value, 255) == 2
        assert one(value, 97) == 0
        assert one(value, False) == 1
        assert one(value, bytearray(b'ab')) == 3
        assert one(value, memoryview(b'ab')) == 3
        assert one(value, memoryview(bytearray(b'ab'))) == 3
        assert three(value, b'', None, None) == 0
        assert two(value, b'', len(value)) == len(value)
        assert two(value, b'', len(value) + 1) == -1
        assert three(value, b'', 2, 1) == -1
        assert three(value, b'ab', -(10 ** 100), 10 ** 100) == 3
        assert two(value, b'', 10 ** 100) == -1
        assert three(value, b'ab', 0, -(10 ** 100)) == -1
        for needle in (-1, 256, 10 ** 100, -(10 ** 100)):
            try:
                one(value, needle)
            except ValueError:
                pass
            else:
                raise AssertionError('out-of-range integer needle accepted')
        method = value.find
        fetched = getattr(value, 'find')
        assert method.__self__ is value and fetched.__self__ is value
        assert method.__name__ == 'find' and fetched.__name__ == 'find'
        assert method(b'ab', 4) == 5 and fetched(b'ab', 4) == 5
    changing = bytearray(b'abc')
    saved = changing.find
    changing[0] = 120
    del changing
    gc.collect()
    assert saved(b'x') == 0 and saved(b'a') == -1
    print('BOUND_FIND_OK')
main()
''',
    "str_payload": r'''
import gc
from typing import Any

class Text(str):
    def __str__(self):
        raise AssertionError('must use str payload, not __str__')
class Child(Text):
    pass
def invoke(value: Any, needle: Any, start: Any, end: Any):
    return value.find(needle, start, end)

def main():
    for value in ('hé🙂\x00hé', Child('hé🙂\x00hé')):
        needle = Text('é')
        assert invoke(value, needle, 0, None) == 1
        assert invoke(value, needle, 2, None) == 5
        assert invoke(value, '🙂', 0, None) == 2
        assert invoke(value, '\x00', 0, None) == 3
        assert invoke(value, '', len(value), None) == len(value)
        assert invoke(value, '', len(value) + 1, None) == -1
        assert invoke(value, '', 2, 1) == -1
        assert invoke(value, needle, -(10 ** 100), 10 ** 100) == 1
        saved = value.find
        assert saved.__self__ is value and saved.__name__ == 'find'
        assert getattr(value, 'find')(needle) == 1
        assert str.find(value, needle, 2) == 5
        assert str.find(value, needle, 2, 5) == -1
    text = Child('hé🙂hé')
    saved = text.find
    ident = id(text)
    del text
    gc.collect()
    assert id(saved.__self__) == ident and saved(Text('é'), 2) == 4
    print('BOUND_FIND_OK')
main()
''',
    "binary_order": r'''
import gc
from typing import Any
events = []
marker = ValueError('needle-marker')
start_marker = RuntimeError('start-marker')
class Index:
    def __init__(self, name, value):
        self.name, self.value = name, value
    def __index__(self):
        events.append(self.name)
        gc.collect()
        return self.value
class Broken:
    def __init__(self, name, error):
        self.name, self.error = name, error
    def __index__(self):
        events.append(self.name)
        gc.collect()
        raise self.error
def invoke(value: Any, needle: Any, start: Any, end: Any):
    return value.find(needle, start, end)
def operand(name, value):
    events.append(name)
    gc.collect()
    return value

def main():
    for value in (b'abcabc', bytearray(b'abcabc')):
        events.clear()
        assert invoke(value, Index('needle', 98), Index('start', 2), Index('end', 6)) == 4
        assert events == ['start', 'end', 'needle']
        events.clear()
        try:
            invoke(value, object(), Index('start', 0), Index('end', 6))
        except TypeError:
            pass
        else:
            raise AssertionError('bad needle accepted')
        assert events == ['start', 'end']
        events.clear()
        try:
            invoke(value, Broken('needle', marker), Broken('start', start_marker), Index('end', 6))
        except RuntimeError as error:
            assert error is start_marker
        else:
            raise AssertionError('start exception replaced')
        assert events == ['start']
        events.clear()
        try:
            invoke(value, Broken('needle', marker), Index('start', 0), Index('end', 6))
        except ValueError as error:
            assert error is marker
        else:
            raise AssertionError('needle exception replaced')
        assert events == ['start', 'end', 'needle']
        for bad in (-1, 256, 10 ** 100, -(10 ** 100)):
            try:
                invoke(value, Index('needle', bad), None, None)
            except ValueError:
                pass
            else:
                raise AssertionError('bad indexed needle accepted')
        for bad in (1.5, 'not-int', None):
            try:
                invoke(value, Index('needle', bad), None, None)
            except TypeError:
                pass
            else:
                raise AssertionError('non-int __index__ result accepted')
        events.clear()
        assert value.find(operand('arg-needle', Index('needle', 98)), operand('arg-start', Index('start', 2)), operand('arg-end', Index('end', 6))) == 4
        assert events == ['arg-needle', 'arg-start', 'arg-end', 'start', 'end', 'needle']
        # A successful call after failure detects a stale pending exception.
        assert invoke(value, b'bc', 0, 6) == 1
    print('BOUND_FIND_OK')
main()
''',
    "str_order": r'''
import gc
from typing import Any
events = []
marker = ValueError('bound-marker')
class Index:
    def __init__(self, name, value):
        self.name, self.value = name, value
    def __index__(self):
        events.append(self.name)
        gc.collect()
        return self.value
class Broken:
    def __index__(self):
        events.append('broken')
        gc.collect()
        raise marker
def invoke(value: Any, needle: Any, start: Any, end: Any):
    return value.find(needle, start, end)

def main():
    for needle in (42, b'h', bytearray(b'h'), memoryview(b'h'), object(), Index('needle', 104)):
        events.clear()
        try:
            invoke('héllo', needle, Broken(), Index('end', 5))
        except TypeError:
            pass
        else:
            raise AssertionError('non-string needle accepted')
        assert events == []
    events.clear()
    assert invoke('héllo', 'é', Index('start', 1), Index('end', 2)) == 1
    assert events == ['start', 'end']
    events.clear()
    try:
        invoke('héllo', 'é', Broken(), Index('end', 5))
    except ValueError as error:
        assert error is marker
    else:
        raise AssertionError('index exception lost')
    assert events == ['broken']
    for bound in (1.5, Index('not-int', 'x')):
        try:
            invoke('héllo', 'é', bound, None)
        except TypeError:
            pass
        else:
            raise AssertionError('bad bound accepted')
    assert invoke('héllo', 'é', 0, None) == 1
    print('BOUND_FIND_OK')
main()
''',
    "arity_kwargs": r'''
from typing import Any
events = []
class Forbidden:
    def __index__(self):
        events.append('called')
        raise AssertionError('arity/keyword error must precede conversion')
def missing(value: Any):
    return value.find()
def excess(value: Any, needle: Any):
    return value.find(needle, Forbidden(), Forbidden(), Forbidden())
def keyword(value: Any, needle: Any):
    return value.find(needle, start=Forbidden())

def main():
    for value, needle in ((b'abc', b'a'), (bytearray(b'abc'), b'a'), ('abc', 'a')):
        method = value.find
        for call in (missing,):
            try:
                call(value)
            except TypeError:
                pass
            else:
                raise AssertionError('missing argument accepted')
        for call in (excess, keyword):
            try:
                call(value, needle)
            except TypeError:
                pass
            else:
                raise AssertionError('invalid call accepted')
        for args in ((), (needle, Forbidden(), Forbidden(), Forbidden())):
            try:
                method(*args)
            except TypeError:
                pass
            else:
                raise AssertionError('bound arity check bypassed')
        try:
            method(needle, end=Forbidden())
        except TypeError:
            pass
        else:
            raise AssertionError('bound keyword accepted')
        assert events == [] and method(needle) == 0
    print('BOUND_FIND_OK')
main()
''',
    "custom_lookup": r'''
import gc
from typing import Any
events = []
marker = ValueError('lookup-marker')
class Custom:
    def find(self, *args, **kwargs):
        gc.collect()
        return ('custom', args, kwargs)
class Override(str):
    def find(self, *args, **kwargs):
        gc.collect()
        return ('override', args, kwargs)
class Missing:
    def __getattr__(self, name):
        events.append(name)
        if name == 'find':
            return Custom().find
        raise AttributeError(name)
class Hook:
    def __getattribute__(self, name):
        if name == 'find':
            events.append('hook')
            raise marker
        return object.__getattribute__(self, name)
class Descriptor:
    def __get__(self, instance, owner):
        events.append('descriptor')
        gc.collect()
        return Custom().find
class Described:
    find = Descriptor()
def invoke(value: Any):
    return value.find('needle', start=2)

def main():
    assert invoke(Custom()) == ('custom', ('needle',), {'start': 2})
    assert invoke(Override('abc')) == ('override', ('needle',), {'start': 2})
    assert invoke(Missing()) == ('custom', ('needle',), {'start': 2})
    assert events == ['find']
    assert invoke(Described()) == ('custom', ('needle',), {'start': 2})
    assert events == ['find', 'descriptor']
    value = Custom()
    value.find = Override('abc').find
    assert invoke(value) == ('override', ('needle',), {'start': 2})
    try:
        invoke(Hook())
    except ValueError as error:
        assert error is marker
    else:
        raise AssertionError('__getattribute__ bypassed')
    assert events == ['find', 'descriptor', 'hook']
    print('BOUND_FIND_OK')
main()
''',
}


NATIVE_MODULES = {
    "bound_find_" + case + ".py": program
    for case, program in PROGRAMS.items()
}
NATIVE_SOURCE = "".join("import " + name[:-3] + "\n" for name in NATIVE_MODULES)
NATIVE_OUTPUT = "BOUND_FIND_OK\n" * len(NATIVE_MODULES)


def _write_native_suite(directory):
    """Preserve each complete case in its own module, then import them all.

    Both compilers build this same entry once; each emitted binary executes
    every original assertion under all five GC backends. Hashes are generated
    beside run artifacts, never checked-in expectations for mutable inputs.
    """
    module_hashes = {}
    for name, program in NATIVE_MODULES.items():
        path = directory / name
        path.write_text(program, encoding="utf-8")
        module_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    source = directory / "program.py"
    source.write_text(NATIVE_SOURCE, encoding="utf-8")
    (directory / "native-module-manifest.json").write_text(
        json.dumps(module_hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return source, NATIVE_OUTPUT, module_hashes


FIND_AND_STR_LEN_NATIVE_MODULES = {
    **NATIVE_MODULES,
    "str_len_protocol.py": STR_LEN_PROGRAM,
}
FIND_AND_STR_LEN_NATIVE_SOURCE = "".join(
    "import " + name[:-3] + "\n" for name in FIND_AND_STR_LEN_NATIVE_MODULES
)
FIND_AND_STR_LEN_NATIVE_OUTPUT = NATIVE_OUTPUT + STR_LEN_OUTPUT


def _write_native_find_and_str_len_suite(directory):
    """Append the complete length protocol without changing any find case.

    This separate entry preserves the original six-module native gate. The CI
    aggregate imports those same six modules in order, then the complete length
    program, with one compilation per compiler and the existing five-GC gate.
    """
    module_hashes = {}
    for name, program in FIND_AND_STR_LEN_NATIVE_MODULES.items():
        path = directory / name
        path.write_bytes(program.encode("utf-8"))
        module_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    source = directory / "program.py"
    source.write_text(FIND_AND_STR_LEN_NATIVE_SOURCE, encoding="utf-8")
    (directory / "native-module-manifest.json").write_text(
        json.dumps(module_hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return source, FIND_AND_STR_LEN_NATIVE_OUTPUT, module_hashes


@pytest.mark.parametrize("case", PROGRAMS)
def test_bound_find_cpython_reference(case, tmp_path):
    source = tmp_path / "find_protocol.py"
    source.write_text(PROGRAMS[case])
    result = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=20,
    )
    receipt = {
        "scope": "CPython reference only; not PCC native execution",
        "case": case, "command": [sys.executable, str(source)],
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
    }
    (tmp_path / "reference.json").write_text(json.dumps(receipt, indent=2) + "\n")
    assert (result.returncode, result.stdout, result.stderr) == (0, "BOUND_FIND_OK\n", ""), receipt
    if case == "exact_binary":
        # Reuse one existing host node to execute the exact native entry and
        # module boundary, not a function-wrapped or reduced stand-in corpus.
        source, expected, module_hashes = _write_native_suite(tmp_path)
        result = subprocess.run(
            [sys.executable, str(source)], capture_output=True, text=True, timeout=20,
        )
        aggregate = {
            "scope": "CPython aggregate-entry reference only; not PCC native",
            "command": [sys.executable, str(source)],
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "module_sha256": module_hashes,
            "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        }
        (tmp_path / "aggregate-reference.json").write_text(json.dumps(aggregate, indent=2) + "\n")
        assert tuple(NATIVE_MODULES.values()) == tuple(PROGRAMS.values())
        for name, program in NATIVE_MODULES.items():
            member = tmp_path / name
            assert member.read_text(encoding="utf-8") == program
            assert hashlib.sha256(member.read_bytes()).hexdigest() == module_hashes[name]
        assert (result.returncode, result.stdout, result.stderr) == (0, expected, ""), aggregate


def test_bound_find_and_str_len_cpython_reference(tmp_path):
    source, expected, module_hashes = _write_native_find_and_str_len_suite(tmp_path)
    result = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=20,
    )
    receipt = {
        "scope": "CPython seven-module aggregate reference only; not PCC native",
        "command": [sys.executable, str(source)],
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "module_sha256": module_hashes,
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
    }
    (tmp_path / "find-and-str-len-aggregate-reference.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8",
    )
    assert source.read_text(encoding="utf-8") == NATIVE_SOURCE + "import str_len_protocol\n"
    assert tuple(FIND_AND_STR_LEN_NATIVE_MODULES) == (*NATIVE_MODULES, "str_len_protocol.py")
    assert tuple(FIND_AND_STR_LEN_NATIVE_MODULES.values()) == (*PROGRAMS.values(), STR_LEN_PROGRAM)
    assert expected == "BOUND_FIND_OK\n" * 6 + "STR_LEN_OK\n"
    for name, program in FIND_AND_STR_LEN_NATIVE_MODULES.items():
        member = tmp_path / name
        assert member.read_bytes() == program.encode("utf-8")
        assert hashlib.sha256(member.read_bytes()).hexdigest() == module_hashes[name]
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, ""), receipt


@pytest.fixture
def find_owned_runtime(request, monkeypatch):
    # Lazy only so sparse reference-only runs do not need the native fixture
    # closure. A selected integration run must resolve the real support module.
    from tests.python.owned_regression_support import explicit_owned_runtime
    return explicit_owned_runtime.__wrapped__(request, monkeypatch)


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_bound_find_and_str_len_owned_native_five_gc(
    python_program_compiler, request, find_owned_runtime, tmp_path, capfd,
):
    from tests.python.owned_regression_support import assert_owned_program
    mode = request.node.callspec.params["python_program_compiler"]
    source, expected, _ = _write_native_find_and_str_len_suite(tmp_path)
    assert_owned_program(
        source.read_text(encoding="utf-8"), expected, tmp_path,
        python_program_compiler, mode, find_owned_runtime, capfd,
    )
