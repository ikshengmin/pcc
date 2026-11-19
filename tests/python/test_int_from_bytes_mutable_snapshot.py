"""A managed buffer address lease does not freeze a bytearray's contents."""
import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.owned_regression_support import (
    assert_owned_program, assert_reference_program, explicit_owned_runtime,
)
from tests.python.runtime_body_model import load_functions
from tests.python.test_int_bytes_gc_roots import PORT, _BytesMemory


def _put_payload(owner, data):
    owner.fields[abi.PYBYTESOBJECT_BYTE_LEN_OFFSET] = len(data)
    for index, value in enumerate(data):
        owner.fields[abi.PYBYTESOBJECT_DATA_OFFSET + index] = value


def _case(kind, data, big, *, replacement=None, copy_failure=False, release_failure=False):
    memory = _BytesMemory(phase="allocate", fail_release=release_failure)
    ns = memory.namespace
    mutable = kind.endswith("mutable")
    leaf = memory.make("leaf", abi.PY_TYPE_BYTEARRAY if mutable else abi.PY_TYPE_BYTES,
                       movable=False)  # The ordinary caller holds its input.
    _put_payload(leaf, data)
    arg = leaf
    for _ in range(kind.count("view")):
        view = memory.make("view", abi.PY_TYPE_MEMORYVIEW, movable=False)
        view.fields[abi.PYMEMORYVIEWOBJECT_BASE_OFFSET] = arg
        arg = view
    snapshots = []

    def snapshot(value):
        # Contract of the existing buffer factory. Its allocation/retry/locked
        # copy is tested against the real body in test_buffer_factory_ownership.
        assert memory.depth == 0
        base = ns["_int_bytes_like_base"](value)
        assert value is leaf and base is leaf and base.tag == abi.PY_TYPE_BYTEARRAY
        assert base.leases > 0 and base.references > 1  # Retained under the original graph lock.
        snapshots.append(base.name)
        if copy_failure:
            memory.error = (19, "snapshot failure")
            return None
        frozen = memory.make("snapshot", abi.PY_TYPE_BYTES)
        _put_payload(frozen, bytes(memory.read(base, abi.PYBYTESOBJECT_DATA_OFFSET + i)
                                   for i in range(memory.read(base, abi.PYBYTESOBJECT_BYTE_LEN_OFFSET))))
        return frozen

    def allocate(n):
        if mutable:
            # Equal-size edits are legal even while a whole view is alive.
            changed = bytes(byte ^ 0xff for byte in data) if replacement is None else replacement
            _put_payload(leaf, changed)
        memory.gc("allocate")
        out = memory.make("result", abi.PY_TYPE_INT)
        for i in range(n):
            out.fields[abi.PYINTOBJECT_DIGITS_OFFSET + 4 * i] = 0
        return out

    def barrier(slot):
        frozen = memory.read(slot, 0)
        assert slot in memory.handles.values() and frozen is memory.latest["snapshot"]
        assert frozen.references == 1 and frozen.alive

    ns.update(py_bytes_from_obj=snapshot, pcc_gc_note_root_slot_barrier=barrier,
              _byteorder_is_big=lambda value: big, load_i8=memory.read, load_ptr=memory.read,
              store_i32=memory.write, py_bigint_alloc=allocate, py_bigint_to_pyobject=lambda out: out)
    # Do not replace arithmetic by int.from_bytes: exercise the original
    # decoder's sign test, limb assembly, trim and output transfer.
    load_functions(PORT, {"_int_from_bytes_leased_base"}, ns)
    return memory, arg, snapshots


def _number(memory, out):
    assert out is memory.latest["result"] and out.references == 1
    used = memory.read(out, abi.PYINTOBJECT_NDIGITS_OFFSET)
    magnitude = sum(memory.read(out, abi.PYINTOBJECT_DIGITS_OFFSET + 4 * i) << (32 * i)
                    for i in range(used))
    return magnitude * memory.read(out, abi.PYINTOBJECT_SIGN_OFFSET)


@pytest.mark.parametrize("kind", ("mutable", "view-mutable", "view-view-mutable", "bytes", "view-bytes"))
@pytest.mark.parametrize("big,signed", ((1, 1), (0, 1), (1, 0), (0, 0)))
def test_snapshot_precedes_bigint_allocation(kind, big, signed):
    data = b"\x80\x00\x01\x00\xff"
    memory, arg, snapshots = _case(kind, data, big)
    out = memory.namespace["py_int_from_bytes_signed"](arg, memory.order, signed)
    assert _number(memory, out) == int.from_bytes(
        data, "big" if big else "little", signed=bool(signed))
    assert snapshots == (["leaf"] if kind.endswith("mutable") else [])
    if snapshots:
        assert not memory.latest["snapshot"].alive and memory.latest["snapshot"].leases == 0
    assert memory.latest["leaf"].references == 1 and memory.latest["leaf"].leases == 0
    memory.assert_closed()


def test_length_and_bytes_come_from_one_complete_mutable_state():
    # A plain bytearray may grow at allocation. Reading its old length and
    # the prefix of the new data would match neither of the two valid states.
    old, live = b"\x80\x00\x01\x00\xff", b"\x01\x02\x03\x04\x05\x06\x07"
    memory, leaf, snapshots = _case("mutable", old, 1, replacement=live)
    out = memory.namespace["py_int_from_bytes"](leaf, memory.order)
    assert _number(memory, out) == int.from_bytes(old)
    assert int.from_bytes(live[:len(old)]) not in (int.from_bytes(old), int.from_bytes(live))
    assert memory.read(leaf, abi.PYBYTESOBJECT_BYTE_LEN_OFFSET) == len(live)
    assert snapshots == ["leaf"] and not memory.latest["snapshot"].alive
    memory.assert_closed()


@pytest.mark.parametrize("failure,expected", (("snapshot-root", 7), ("snapshot", 19),
                                              ("snapshot-lease", 15), ("bigint", 19),
                                              ("lease-release", 7)))
def test_mutable_snapshot_failure_retires_only_owned_values(failure, expected):
    memory, view, snapshots = _case("view-mutable", b"\x80", 1,
                                    copy_failure=failure == "snapshot",
                                    release_failure=failure == "lease-release")
    ns = memory.namespace
    if failure == "snapshot-root":
        memory.fail_register = 2
    elif failure == "snapshot-lease":
        def acquire(slot):
            if memory.read(slot, 0).name == "snapshot":
                return -2
            return memory.acquire(slot)
        ns["pcc_gc_foreign_lease_acquire"] = acquire
    elif failure == "bigint":
        def allocate(_):
            memory.error = (19, "bigint failure")
            return None
        ns["py_bigint_alloc"] = allocate
    assert ns["py_int_from_bytes_signed"](view, memory.order, 1) is None
    assert snapshots == ([] if failure == "snapshot-root" else ["leaf"])
    assert memory.error[0] == expected
    if failure in ("bigint", "snapshot"):
        assert memory.error[1] == failure + " failure"
    for name in ("snapshot", "result"):
        if name in memory.latest:
            assert not memory.latest[name].alive and memory.latest[name].leases == 0
    assert memory.latest["leaf"].references == 1 and memory.latest["leaf"].leases == 0
    memory.assert_closed()


def test_nonbuffer_does_not_enter_generic_bytes_constructor():
    memory, _, snapshots = _case("bytes", b"x", 1)
    # The generic factory also accepts integer counts and sequences. The
    # decoder must retain its existing rejected-source branch on this ABI.
    assert memory.namespace["py_int_from_bytes_signed"](memory.value, memory.order, 0) is None
    assert memory.error == (3, "from_bytes expects a bytes object") and not snapshots
    memory.assert_closed()


PROGRAM = r"""class ChangeSign:
    def __init__(self, source):
        self.source = source
    def __bool__(self):
        self.source[0] = 128
        return True

def check(source):
    a = int.from_bytes(source)
    b = int.from_bytes(source, "little")
    c = int.from_bytes(source, "big", signed=True)
    d = int.from_bytes(source, "little", signed=True)
    assert a == 549755879679 and b == 1095216726144
    assert c == -549755748097 and d == -4294901632
    return d

def main():
    payload = b'\x80\x00\x01\x00\xff'
    owner = bytearray(payload)
    direct = check(owner)
    view = memoryview(owner)
    nested = check(memoryview(view))
    direct_bytes = check(payload)
    immutable_view = check(memoryview(payload))
    owner[0] = 1
    assert int.from_bytes(view, 'big') == 4295033087
    assert int.from_bytes(view, 'big', signed=ChangeSign(owner)) == -549755748097
    assert int.from_bytes(bytearray()) == 0
    assert int.from_bytes(memoryview(bytearray(b'\xff' * 9)), signed=True) == -1
    print(direct, nested, direct_bytes, immutable_view, len(owner))
main()
"""
EXPECTED = "-4294901632 -4294901632 -4294901632 -4294901632 5\n"


def test_mutable_decode_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_mutable_decode_public_native(python_program_compiler, request,
                                      explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler,
                         mode, explicit_owned_runtime, capfd)
