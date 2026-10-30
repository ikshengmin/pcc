"""Execute current find descriptor/binder bodies, not a replacement find model.

The shared slot-memory adapter injects relocation at ABI boundaries. Runtime
dispatch, arity, ownership cleanup, payload access and byte scanning execute
the actual current source. Allocation, root/lease primitives and __index__ are
host adapters: these tests are NOT native, five-GC or bootstrap evidence.

The native corpus in test_bound_find_protocol.py supplies the separate real
execution gate. Bytes-subclass payloads and sliced/released memoryviews are
outside this adapter's supported representation; they must not be inferred to
work from exact bytes/bytearray and whole-buffer memoryview coverage.
"""

from __future__ import annotations

import ast
import operator
from pathlib import Path

import pytest

from pcc.runtime.py import py_abi_constants as abi
import test_set_call_slot_roots as slot_support
import test_str_method_slot_models as str_support


ROOT = Path(__file__).resolve().parents[2]
PORT = ROOT / "pcc/runtime/py"
Block = slot_support.Block
Object = slot_support.Object
_FUNCTION_CACHE = {}


@pytest.fixture(autouse=True)
def current_runtime_sources(monkeypatch):
    """A sparse checkout may reuse test adapters, never their old runtime."""
    monkeypatch.setattr(slot_support, "PORT", PORT / "py_set.py")
    monkeypatch.setattr(str_support, "PORT", PORT / "py_class.py")
    monkeypatch.setattr(str_support, "_TREES", {
        name: ast.parse((PORT / name).read_text())
        for name in ("py_class.py", "py_obj_ops_dispatch.py", "py_str_accessors.py")
    })


def _load_functions(namespace, filename, names, prefix=(), constants=()):
    path = PORT / filename
    key = (filename, tuple(sorted(names)), prefix, constants, path.stat().st_mtime_ns)
    if key in _FUNCTION_CACHE:
        exec(_FUNCTION_CACHE[key], namespace)
        return
    body = []
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.FunctionDef) and (
            node.name in names or node.name.startswith(prefix)
        ):
            node.decorator_list = []
            body.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id.startswith(constants)
            for target in node.targets
        ):
            body.append(node)
    code = compile(ast.Module(body=body, type_ignores=[]), str(path), "exec")
    _FUNCTION_CACHE[key] = code
    exec(code, namespace)


class FindMemory(str_support.StringMemory):
    def __init__(self, phase="callback", **kwargs):
        super().__init__(phase=phase, **kwargs)
        self.maps.update({
            "pcc_bound_method_borrowed_map": -2,
            "pcc_bound_method_owned_map": 8,
        })
        self.callback_error = None
        self.ns.update({
            "strcmp": lambda left, right: (left > right) - (left < right),
            "unsigned_div_i64": lambda left, right: left // right,
            "store_i8": self.write,
            "py_obj_has_index": self.has_index,
            "py_obj_index_slots": self.index_slots,
        })
        _load_functions(self.ns, "py_str_accessors.py", {
            "_bytes_eq", "_byte_find", "_byte_offset_to_cp_offset",
            "_utf8_codepoint_count", "_str_cp_len",
            "_utf8_byte_offset_for_codepoint", "py_str_find_range",
        })
        _load_functions(self.ns, "py_obj_stubs.py", {
            "_type_of", "_bytes_data", "py_bytes_len", "py_bytes_find_range",
        })
        _load_functions(self.ns, "py_obj_ops_dispatch.py", {
            "_py_bound_method", "_py_bound_method_entry", "_cstr_append",
        }, prefix=("_bound_method_",), constants=("_BOUND_METHOD_",))

    def byte(self, pointer, offset):
        if isinstance(pointer, str):
            return ord(pointer[offset]) if offset < len(pointer) else 0
        return self.read(pointer, offset)

    def wrap(self, value):
        if type(value) in (bytes, bytearray):
            tag = abi.PY_TYPE_BYTES if type(value) is bytes else abi.PY_TYPE_BYTEARRAY
            obj = self.make(value, tag)
            obj.fields.update({12: 0, 16: len(value)})
            for index, byte in enumerate(value):
                obj.fields[24 + index] = byte
            return obj
        if type(value) is memoryview:
            assert value.contiguous and value.nbytes == len(value.obj), (
                "only existing whole-buffer memoryview payload is represented"
            )
            obj = self.make(value, abi.PY_TYPE_MEMORYVIEW)
            obj.fields.update({12: 0, 16: self.wrap(value.obj)})
            return obj
        if type(value) is int and not -(1 << 62) <= value < (1 << 62):
            obj = self.make(value, abi.PY_TYPE_INT)
            obj.fields[12] = 0
            return obj
        return super().wrap(value)

    @staticmethod
    def unbox(value):
        return value.value if isinstance(value, Object) else value

    def has_index(self, value):
        return int(hasattr(type(self.unbox(value)), "__index__"))

    def save_callback_error(self, error):
        kind = 3 if isinstance(error, TypeError) else 2 if isinstance(error, ValueError) else 7
        self.error = (kind, str(error))
        self.callback_error = self.error

    def index(self, value, default):
        self.collect("callback")
        if value is None or value is self.none:
            return default
        if isinstance(value, Object):
            assert value.alive and value.leases > 0
        try:
            return max(-(1 << 63), min((1 << 63) - 1, operator.index(self.unbox(value))))
        except Exception as error:
            self.save_callback_error(error)
            return -1

    def index_slots(self, source, destination):
        self.collect("callback")
        value = self.read(source, 0)
        if isinstance(value, Object):
            assert value.alive and value.leases > 0
        try:
            result = operator.index(self.unbox(value))
        except Exception as error:
            self.save_callback_error(error)
            return -1
        self.write(destination, 0, self.integer(result))
        return 0

    def invoke_find(self, receiver, *args):
        caller = Block()
        self.roots[("caller", 0)] = caller
        if isinstance(receiver, str):
            caller.fields[0] = self.wrap((receiver,) + args)
            captures = self.wrap((self.ns["_STR_METHOD_FIND"],))
            result = self.ns["_str_method_entry"](captures, self.read(caller, 0))
        else:
            tag = abi.PY_TYPE_BYTES if type(receiver) is bytes else abi.PY_TYPE_BYTEARRAY
            operation = self.ns["_bound_method_operation"](tag, "find")
            assert operation != 0, "exact binary receiver has no bound find operation"
            caller.fields[0] = self.wrap((receiver, operation))
            caller.fields[8] = self.wrap(args)
            self.roots[("caller", 8)] = self.add(caller, 8)
            result = self.ns["_py_bound_method_entry"](
                self.read(caller, 0), self.read(caller, 8)
            )
        assert not self.frame_handles, "entry leaked a GC frame"
        assert not self.root_groups, "entry leaked a continuation root group"
        assert all(isinstance(key, tuple) for key in self.roots), "entry leaked a root handle"
        assert all(obj.leases == 0 for obj in self.objects if obj.alive and obj is not self.none)
        if self.error is None:
            return ("value", self.unbox(result))
        return ("error", {2: ValueError, 3: TypeError, 7: RuntimeError, 19: MemoryError}[self.error[0]])


def _outcome(call):
    try:
        return ("value", call())
    except Exception as error:
        return ("error", type(error))


@pytest.mark.parametrize("receiver", (b"a\x00\xffababa", bytearray(b"a\x00\xffababa"), "hé🙂\x00hé"))
def test_actual_find_bodies_match_cpython_windows(receiver):
    needles = ("", "h", "é", "🙂", "\x00", "not-there", 98, b"h") if isinstance(receiver, str) else (
        b"", b"a", b"aba", b"\x00", b"\xff", b"not-there", bytearray(b"ab"),
        memoryview(b"ab"), memoryview(bytearray(b"ab")), 0, 255, 97, True,
        -1, 256, 10 ** 100, -(10 ** 100), "a", None,
    )
    bounds = ((), (None,), (None, None), (0,), (1, 5), (-3,), (-100, 100),
              (2, 1), (0, 0), (len(receiver),), (len(receiver) + 1,),
              (10 ** 100,), (-(10 ** 100), 10 ** 100), (0, -(10 ** 100)), (1.5,))
    for needle in needles:
        for window in bounds:
            actual = FindMemory().invoke_find(receiver, needle, *window)
            expected = _outcome(lambda: receiver.find(needle, *window))
            assert actual == expected, (receiver, needle, window, actual, expected)


@pytest.mark.parametrize("receiver", (b"abcabc", bytearray(b"abcabc"), "abcabc"))
@pytest.mark.parametrize("phase", ("register", "copy", "acquire", "callback", "release", "drop", "allocation", "frame_enter", "frame_leave"))
def test_find_bounds_callbacks_remain_owned_during_relocation(receiver, phase):
    events = []
    class Index:
        def __init__(self, name, value):
            self.name, self.value = name, value
        def __index__(self):
            events.append(self.name)
            return self.value
    needle = "bc" if isinstance(receiver, str) else Index("needle", 98)
    memory = FindMemory(phase)
    assert memory.invoke_find(receiver, needle, Index("start", 2), Index("end", 6)) == ("value", 4)
    assert events == (["start", "end"] if isinstance(receiver, str) else ["start", "end", "needle"])
    if phase not in ("register", "allocation"):
        assert memory.moves > 0


@pytest.mark.parametrize("receiver", (b"abc", bytearray(b"abc"), "abc"))
@pytest.mark.parametrize("bad", ("needle", "start", "end", "needle-result"))
def test_find_conversion_order_and_callback_exception_survive_cleanup(receiver, bad):
    def execute(native_body):
        events = []
        class Index:
            def __init__(self, name, value):
                self.name, self.value = name, value
            def __index__(self):
                events.append(self.name)
                if self.name == bad:
                    raise ValueError(self.name + " failure")
                return "not-int" if bad == "needle-result" and self.name == "needle" else self.value
        needle = Index("needle", 98)
        start, end = Index("start", 0), Index("end", 3)
        if native_body:
            memory = FindMemory("release")
            original = memory.ns["pcc_gc_store_root"]
            def cleanup(slot, value):
                previous = memory.read(slot, 0)
                original(slot, value)
                if isinstance(previous, Object) and previous.tag == abi.PY_TYPE_FLOAT:
                    memory.error = (7, "cleanup must not replace pending exception")
            memory.ns["pcc_gc_store_root"] = cleanup
            result = memory.invoke_find(receiver, needle, start, end)
            if memory.callback_error is not None:
                assert memory.error is memory.callback_error
        else:
            result = _outcome(lambda: receiver.find(needle, start, end))
        return result, events
    assert execute(True) == execute(False)


@pytest.mark.parametrize("receiver", (b"abc", bytearray(b"abc"), "abc"))
def test_find_arity_errors_do_not_convert_arguments(receiver):
    class Forbidden:
        def __index__(self):
            pytest.fail("arity rejection must precede argument conversion")
    needle = "a" if isinstance(receiver, str) else b"a"
    for arguments in ((), (needle, Forbidden(), Forbidden(), Forbidden())):
        assert FindMemory().invoke_find(receiver, *arguments) == ("error", TypeError)


def test_supported_string_subtype_payload_is_not_coerced_through_str():
    class Text(str):
        def __str__(self):
            pytest.fail("find must use the existing builtin subtype payload")
    assert FindMemory("copy").invoke_find(Text("hé🙂hé"), Text("é"), 2) == ("value", 4)


def test_binary_binder_does_not_treat_instance_or_memoryview_as_exact_bytes():
    memory = FindMemory()
    for tag in (abi.PY_TYPE_INSTANCE, abi.PY_TYPE_USER_CLASS_START, abi.PY_TYPE_MEMORYVIEW, abi.PY_TYPE_STR):
        assert memory.ns["_bound_method_operation"](tag, "find") == 0
    # Bytes subclasses have no supported builtin payload at baseline. This
    # locks the exact-tag boundary, not a claim of full subtype support.


@pytest.mark.parametrize("receiver", (b"abc", "abc"))
@pytest.mark.parametrize("failure", (1, 2))
def test_find_copy_failure_retires_partial_input_owners(receiver, failure):
    needle = "a" if isinstance(receiver, str) else b"a"
    assert FindMemory(fail_copy=failure).invoke_find(receiver, needle)[0] == "error"


@pytest.mark.parametrize("receiver", (b"abc", bytearray(b"abc")))
@pytest.mark.parametrize("phase", ("copy", "acquire", "release", "allocation", "frame_enter", "frame_leave"))
def test_binary_bound_find_creation_owns_its_receiver(receiver, phase):
    memory = FindMemory(phase)
    caller = Block()
    caller.fields[0] = memory.wrap(receiver)
    memory.roots[("caller", 0)] = caller
    operation = memory.ns["_bound_method_operation"](memory.read(caller, 0).tag, "find")
    function = memory.ns["_py_bound_method"](memory.read(caller, 0), operation)
    assert memory.error is None and function.alive
    assert function.fields[72] == "find"
    assert function.fields[80] is memory.read(caller, 0)
    captures = function.fields[64]
    assert captures.fields[24] is memory.read(caller, 0)
    assert captures.fields[32] == operation
    assert not memory.frame_handles and not memory.root_groups
    assert all(obj.leases == 0 for obj in memory.objects if obj.alive and obj is not memory.none)


@pytest.mark.parametrize("kind", ("tuple", "function"))
def test_binary_bound_find_allocation_failure_releases_partial_owners(kind):
    memory = FindMemory("allocation", fail_kind=kind)
    caller = Block()
    caller.fields[0] = memory.wrap(b"abc")
    memory.roots[("caller", 0)] = caller
    operation = memory.ns["_bound_method_operation"](abi.PY_TYPE_BYTES, "find")
    assert memory.ns["_py_bound_method"](memory.read(caller, 0), operation) is None
    assert memory.error == (19, kind + " allocation failed")
    assert not memory.frame_handles and not memory.root_groups
    assert all(obj.leases == 0 for obj in memory.objects if obj.alive and obj is not memory.none)
