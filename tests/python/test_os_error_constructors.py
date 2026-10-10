"""Bounded OSError/FileNotFoundError constructor and ownership regressions.

The runtime model executes actual constructor/payload bodies, including forced
relocation. Native execution remains a separate explicit-runtime integration
gate. BlockingIOError.characters_written and Windows winerror are not qualified.
"""
from pathlib import Path
import re

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.test_exception_constructor_roots import _Slots
from tests.python.test_os_error_attribute_owner import ErrorMemory, bodies
from tests.python.owned_regression_support import (
    assert_owned_program,
    assert_reference_program,
    explicit_owned_runtime,
)


ROOT = Path(__file__).resolve().parents[2]
PHASES = ("", "allocation", "frame_enter", "frame_leave", "load_root",
          "field_store", "publish", "graph_unlock", "tuple_set", "tuple_get")
ARGUMENTS = (
    (), ("",), (None,), (84,), (84, "msg"), (2, "missing"),
    (13, "denied", "/path"), (84, "msg", b"path"),
    (2, "missing", None), (2, "missing", None, 100),
    (2, "missing", None, None, "ignored-second"),
    (2, "missing", "first", None, "second"),
    (2, "missing", "first", 100, None),
    (None, None), ("2", "text errno"), (2 ** 100, "large errno"),
    (2, "missing", "first", None, "second", "extra"),
)


def constructor_memory(phase):
    memory = ErrorMemory(phase)
    memory.namespace["i64"] = int
    memory.namespace["target_sys_platform"] = lambda: "linux"
    bodies(ROOT / "pcc/runtime/py/freestanding_errno.py", memory.namespace,
           names={"pcc_errno_exception_kind"})
    return memory


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("tag,cls", [(14, OSError), (34, FileNotFoundError)])
@pytest.mark.parametrize("arguments", ARGUMENTS)
def test_os_error_constructor_payload_matches_cpython(phase, tag, cls, arguments):
    memory = constructor_memory(phase)
    caller = _Slots(8)
    caller.fields[0] = memory.argument_tuple(arguments)
    memory.frames["caller"] = caller
    error = memory.namespace["py_os_error_new"](tag, caller.fields[0])
    assert error is not None and error.references == 1
    memory.handled.fields[0] = error  # transfer the NEW owner before another call
    memory.store_root(caller, None)
    del memory.frames["caller"]
    oracle = cls(*arguments)
    assert error.fields[16].fields[abi.PYCLASSOBJECT_NAME_OFFSET] == type(oracle).__name__
    for name in ("args", "errno", "strerror", "filename", "filename2"):
        value = memory.dispatch["py_obj_getattr"](memory.handled.fields[0], name)
        assert memory.python(value) == getattr(oracle, name), (name, arguments)
        memory.decref(value)
    assert memory.render() == str(oracle)
    assert memory.render(1) == repr(oracle)
    assert not memory.pin_metric and not memory.graph_depth
    assert set(memory.frames) == {"class_cache", "handled", "pending"}
    assert memory.pending.fields[0] is None


def test_os_error_null_argument_tuple_means_no_arguments():
    memory = constructor_memory("graph_unlock")
    error = memory.namespace["py_os_error_new"](14, None)
    memory.handled.fields[0] = error
    args = memory.dispatch["py_obj_getattr"](memory.handled.fields[0], "args")
    assert memory.python(args) == ()
    memory.decref(args)
    assert memory.render() == ""
    assert not memory.pin_metric and not memory.graph_depth


@pytest.mark.xfail(strict=True, reason="BlockingIOError.characters_written is outside this constructor slice")
def test_blocking_numeric_third_argument_known_unqualified_field():
    memory = constructor_memory("")
    cls = memory.make(abi.PY_TYPE_CLASS, 65)
    cls.fields["exception_tag"] = 43
    cls.fields[abi.PYCLASSOBJECT_NAME_OFFSET] = "BlockingIOError"
    mro = _Slots(16)
    mro.fields[0] = cls
    mro.fields[8] = memory.class_cache.fields[14 * 8]
    cls.fields[abi.PYCLASSOBJECT_MRO_OFFSET] = mro
    cls.fields[abi.PYCLASSOBJECT_N_MRO_OFFSET] = 2
    memory.class_cache.fields[43 * 8] = cls
    args = memory.argument_tuple((11, "blocked", 3))
    error = memory.namespace["py_os_error_new"](14, args)
    memory.handled.fields[0] = error
    filename = memory.dispatch["py_obj_getattr"](error, "filename")
    assert memory.python(filename) is None
    memory.decref(filename)
    written = memory.dispatch["py_obj_getattr"](error, "characters_written")
    assert memory.python(written) == 3


@pytest.mark.parametrize("failure", [1, 2])
def test_os_error_constructor_allocation_error_does_not_leak_arguments(failure):
    memory = constructor_memory("graph_unlock")
    caller = _Slots(8)
    caller.fields[0] = memory.argument_tuple((2, "missing", "path"))
    memory.frames["caller"] = caller
    original = memory.namespace["py_tuple_new"]
    calls = 0

    def allocate(count):
        nonlocal calls
        calls += 1
        if calls == failure:
            memory.namespace["py_raise_owned"](
                memory.namespace["py_exc_new"](19, "constructor allocation"))
            return None
        return original(count)

    memory.namespace["py_tuple_new"] = allocate
    assert memory.namespace["py_os_error_new"](14, caller.fields[0]) is None
    assert memory.pending.fields[0].fields[16].fields["exception_tag"] == 19
    assert caller.fields[0].references == 1
    assert not memory.pin_metric and not memory.graph_depth
    assert set(memory.frames) == {"class_cache", "handled", "pending", "caller"}


@pytest.mark.parametrize("expression", [
    "OSError(84, 'msg')", "FileNotFoundError(13, 'msg', 'path')",
    "IOError(2, 'missing')", "EnvironmentError(2, 'missing')",
    "OSError(*(2, 'missing', 'path'))", "OSError(message='invalid')",
])
def test_os_error_direct_constructor_uses_authoritative_call_slots(expression):
    from tests.python.test_shared_call_binding import _emit

    text = _emit("def probe():\n    return " + expression + "\n")
    body = re.search(r"(?ms)^define[^\n]*@user_binding_probe\([^\n]*\).*?^}", text).group(0)
    assert "@py_obj_call_slots(" in body
    assert "@py_obj_str(" not in body
    assert not re.search(r"@py_exc_new\(i64 (?:14|34),", body)
    assert "@py_cpy_" not in body


PROGRAM = r'''
import gc

EVENTS = []

def operand(name, value):
    EVENTS.append(name)
    gc.collect()
    return value

class Unformatted:
    def __str__(self):
        raise AssertionError('constructor formatted an argument')

def direct():
    return OSError(84, 'msg')

def verify(error, cls, args, number, message, filename=None, filename2=None):
    gc.collect()
    assert type(error) is cls
    assert isinstance(error, OSError) and isinstance(error, Exception)
    assert error.args == args
    assert error.errno == number and error.strerror == message
    assert error.filename == filename and error.filename2 == filename2

def main():
    verify(direct(), OSError, (84, 'msg'), 84, 'msg')
    verify(OSError(2, 'missing', 'path'), FileNotFoundError,
           (2, 'missing'), 2, 'missing', 'path')
    verify(FileNotFoundError(13, 'denied'), FileNotFoundError,
           (13, 'denied'), 13, 'denied')
    evaluated = OSError(operand('errno', 84), operand('message', 'msg'),
                        operand('filename', 'path'))
    assert EVENTS == ['errno', 'message', 'filename']
    verify(evaluated, OSError, (84, 'msg'), 84, 'msg', 'path')
    marker = Unformatted()
    untouched = OSError(marker, marker)
    assert untouched.errno is marker and untouched.strerror is marker
    assert untouched.args[0] is marker and untouched.args[1] is marker
    constructor = OSError
    verify(constructor(84, 'msg'), OSError, (84, 'msg'), 84, 'msg')
    for cls in (OSError, IOError, EnvironmentError):
        verify(cls(*(2, 'missing', b'path')), FileNotFoundError,
               (2, 'missing'), 2, 'missing', b'path')
    for args in ((), ('',), (None,), (84,)):
        verify(constructor(*args), OSError, args, None, None)
    verify(constructor(2, 'missing', None), FileNotFoundError,
           (2, 'missing', None), 2, 'missing')
    verify(constructor(2, 'missing', 'first', None, 'second'), FileNotFoundError,
           (2, 'missing'), 2, 'missing', 'first', 'second')
    verify(constructor(2, 'missing', None, None, 'ignored'), FileNotFoundError,
           (2, 'missing', None, None, 'ignored'), 2, 'missing')
    verify(constructor(2 ** 100, 'large'), OSError,
           (2 ** 100, 'large'), 2 ** 100, 'large')
    verify(constructor('2', 'text'), OSError, ('2', 'text'), '2', 'text')
    args = (2, 'missing', 'first', None, 'second', 'extra')
    verify(constructor(*args), OSError, args, None, None)
    try:
        raise OSError(2, 'missing', 'path')
    except PermissionError:
        raise AssertionError('sibling handler matched')
    except FileNotFoundError as error:
        assert error.args == (2, 'missing') and error.filename == 'path'
        assert str(error) == "[Errno 2] missing: 'path'"
        assert repr(error) == "FileNotFoundError(2, 'missing')"
    except OSError:
        raise AssertionError('errno subclass was lost')
    for cls in (OSError, FileNotFoundError):
        try:
            cls(message='invalid')
        except TypeError:
            pass
        else:
            raise AssertionError('constructor accepted keywords')
    try:
        OSError(84, strerror='invalid')
    except TypeError:
        pass
    else:
        raise AssertionError('direct constructor accepted keywords')
    value = constructor(84, 'original', 'path')
    value.args = ('changed',)
    gc.collect()
    assert value.errno == 84 and value.strerror == 'original'
    assert value.filename == 'path' and value.args == ('changed',)
    print('OS_ERROR_CONSTRUCTORS_OK')

main()
'''


def test_os_error_constructor_reference(tmp_path):
    assert_reference_program(PROGRAM, "OS_ERROR_CONSTRUCTORS_OK\n", tmp_path)


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_os_error_constructor_native_five_gc(python_program_compiler, request,
                                          explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, "OS_ERROR_CONSTRUCTORS_OK\n", tmp_path,
                         python_program_compiler, mode, explicit_owned_runtime,
                         capfd, provenance_probe="2")
