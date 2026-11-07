"""Actual metadata writers must not undo a concurrent header transition.

Host tests execute production function bodies over the existing provider ABI
model. The injected collector step models the header plus gray credit/token;
it is not a native race or an implementation of the persistent worklist.
The legacy mutation control restores only the former load/OR/store operation.
Native execution of the new intrinsic/ABI is a separate test below.
"""
from __future__ import annotations

import ast
from functools import lru_cache
import inspect
import os
from pathlib import Path
import subprocess

import pytest

from tests.python.test_owned_tempfile_provider import Obj, Runtime

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"
COLOR, WHITE, GRAY, BLACK = 56, 8, 16, 32
PINNED, IMMORTAL, DYING = 64, 1, 524288
SITES = (
    ("py_obj.py", "pcc_gc_immortalize", IMMORTAL),
    ("py_coroutine.py", "py_coroutine_class", IMMORTAL),
    ("py_coroutine.py", "py_continuation_class", IMMORTAL),
    ("py_os_path.py", "_path_clear_root_prefix", PINNED),
    ("py_os_mkdir.py", "_mkdir_drop", PINNED),
    ("py_os_mkdir.py", "_mkdir_finish", PINNED),
    ("py_tempfile.py", "_td_drop", PINNED),
    ("py_tempfile.py", "_td_init", PINNED),
    ("py_tempfile.py", "_nt_finish", PINNED),
    ("py_tempfile.py", "_nt_cleanup", PINNED),
    ("py_tempfile.py", "_nt_cleanup_entry", PINNED),
)


@lru_cache(maxsize=32)
def _body_code(source, filename, name, legacy):
    node = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    node.decorator_list = []
    if legacy:
        class RestoreWholeWordStore(ast.NodeTransformer):
            def visit_Call(self, call):
                if isinstance(call.func, ast.Name) and call.func.id == "atomic_rmw_i32":
                    op, obj, offset, bit, order = call.args
                    assert ast.literal_eval(op) == "or"
                    assert ast.literal_eval(order) == "relaxed"
                    return ast.copy_location(ast.Call(
                        func=ast.Name(id="store_i32", ctx=ast.Load()),
                        args=[obj, offset, ast.BinOp(
                            left=ast.Call(func=ast.Name(id="load_i32", ctx=ast.Load()),
                                          args=[obj, offset], keywords=[]),
                            op=ast.BitOr(), right=bit)], keywords=[]), call)
                return self.generic_visit(call)
        node = RestoreWholeWordStore().visit(node)
    return compile(ast.fix_missing_locations(ast.Module([node], type_ignores=[])),
                   filename, "exec")


class MetadataRuntime(Runtime):
    def __init__(self, site, transition, preset, legacy=False):
        super().__init__()
        self.site, self.transition, self.bit = site[1], transition, site[2]
        self.target = Obj("exception", (19, "original"))
        color = WHITE if transition == "shade" else GRAY if transition == "consume" else BLACK
        self.target.flags = color | 0x1000000 | (self.bit if preset else 0)
        self.credit = self.tokens = int(color == GRAY)
        self.events, self.updates = [], []
        self.pin_calls = self.unpin_calls = 0
        self.load(RUNTIME / "py_os_mkdir.py")
        path = RUNTIME / site[0]
        exec(_body_code(path.read_text(), str(path), site[1], legacy), self.ns)
        self.ns.update(atomic_rmw_i32=self.metadata_or, store_i32=self.metadata_store,
                       pcc_gc_store_root=self.clear_root,
                       py_tls_exc_swap_slot=self.swap_error_recorded)

    def pin(self, obj):
        if obj is getattr(self, "target", None):
            self.pin_calls += 1
            self.events.append("pin")
        super().pin(obj)

    def unpin(self, obj):
        if obj is getattr(self, "target", None):
            self.unpin_calls += 1
            self.events.append("unpin")
        super().unpin(obj)

    def interfere(self, obj, offset, caller):
        if obj is not self.target or offset != 12 or caller != self.site:
            return False
        assert not self.updates, "the target metadata site must execute exactly once"
        # At the linearization point even an already-set target bit must not
        # justify a stale whole-word store. Pin restore normally follows unpin.
        if self.transition == "shade":
            assert obj.flags & COLOR == WHITE and (self.credit, self.tokens) == (0, 0)
            obj.flags = (obj.flags & ~COLOR) | GRAY
            self.credit = self.tokens = 1
        elif self.transition == "consume":
            assert obj.flags & COLOR == GRAY and (self.credit, self.tokens) == (1, 1)
            obj.flags = (obj.flags & ~COLOR) | BLACK
            self.credit = self.tokens = 0
        else:
            # Add DEALLOCATING and a high bit while concurrently clearing
            # another unrelated bit. An OR must preserve both directions.
            obj.flags = (obj.flags & ~0x1000000) | DYING | 0x80000000
        self.expected = obj.flags | self.bit
        self.events.append("collector")
        return True

    def metadata_or(self, op, obj, offset, bit, order):
        assert op == "or" and order == "relaxed" and offset == 12
        target = self.interfere(obj, offset, inspect.currentframe().f_back.f_code.co_name)
        old = self.read(obj, offset)
        self.write(obj, offset, old | bit)
        if target:
            self.updates.append(obj.flags)
            self.events.append("metadata")
        return old

    def metadata_store(self, obj, offset, value):
        target = self.interfere(obj, offset, inspect.currentframe().f_back.f_code.co_name)
        self.write(obj, offset, value)
        if target:
            self.updates.append(obj.flags)
            self.events.append("metadata")

    def clear_root(self, slot, value):
        if self.read(slot, 0) is self.target:
            self.events.append("clear")
        self.write(slot, 0, value)

    def swap_error_recorded(self, slot):
        if self.error is self.target or self.read(slot, 0) is self.target:
            self.events.append("swap")
        self.swap_error(slot)

    def frame(self, count):
        slots, pins = self.alloc(count * 8), self.alloc(count * 8)
        self.memset(slots, 0, count * 8)
        self.memset(pins, 0, count * 8)
        self.frames.append(slots)
        return slots, pins

    def invoke(self):
        n, obj, name = self.ns, self.target, self.site
        if name == "pcc_gc_immortalize":
            n[name](obj)
            assert (self.pin_calls, self.unpin_calls, obj.pins) == (1, 0, 1)
            return
        if name in ("py_coroutine_class", "py_continuation_class"):
            n["py_class_new_builtin"] = lambda *args: obj
            assert n[name]() is obj
            assert n[name]() is obj  # Cached class must not rewrite its header.
            assert (self.pin_calls, self.unpin_calls, obj.pins) == (0, 0, 0)
            return
        if name in ("_path_clear_root_prefix", "_mkdir_drop", "_td_drop"):
            count = 12 if name == "_mkdir_drop" else 24
            slots, pins = self.frame(count)
            obj.pins = 1
            n["store_ptr"](slots, 0, obj)
            n["store_i64"](pins, 0, PINNED)
            n[name](slots, pins, 1 if name == "_path_clear_root_prefix" else 0)
            self.frames.remove(slots)
            assert (self.pin_calls, self.unpin_calls, obj.pins) == (0, 1, 0)
            assert self.events == ["unpin", "collector", "metadata", "clear"]
        elif name in ("_mkdir_finish", "_nt_finish"):
            slots, pins = self.frame(12 if name == "_mkdir_finish" else 24)
            # A pre-existing pin is independent of this scope's counted pin.
            obj.flags |= PINNED
            self.error = obj
            assert n[name](slots, pins) is None
            assert self.error is obj
            assert self.events == ["swap", "pin", "unpin", "collector", "metadata", "swap"]
        elif name == "_td_init":
            obj.flags |= PINNED
            manager = Obj("instance")
            n["_td_mkdtemp"] = lambda *args: Obj("str", "/not-used")
            def fail_state(size):
                self.error = obj
                return None
            n["py_list_new"] = fail_state
            n["_td_remove"] = lambda *args: None
            args = Obj("tuple", [manager, self.none, self.none, self.none, self.false, self.true])
            assert n[name](None, args) is None
            assert self.error is obj
        elif name == "_nt_cleanup":
            obj.flags |= PINNED
            manager = Obj("instance")
            manager.attrs.update(_ready=self.true, _cleanup_file=Obj("file"),
                _cleanup_name=self.none, _delete=self.false,
                _delete_on_close=self.false, _removed=self.false)
            def fail_close(file):
                self.error = obj
            n["py_file_close_checked"] = fail_close
            assert n[name](manager, 0) is None
            assert self.error is obj
        elif name == "_nt_cleanup_entry":
            obj.flags |= PINNED
            self.error = obj
            n["_nt_cleanup"] = lambda *args: None
            assert n[name](Obj("tuple", [Obj("instance")]), 2) is None
            assert self.error is obj
        else:
            raise AssertionError(name)
        assert not self.frames and not self.leases
        assert obj.pins == 0 and self.pin_calls == self.unpin_calls - int(name in (
            "_path_clear_root_prefix", "_mkdir_drop", "_td_drop"))
        if name not in ("_path_clear_root_prefix", "_mkdir_drop", "_td_drop"):
            index = self.events.index("metadata")
            assert self.events[index - 2:index + 2] == ["unpin", "collector", "metadata", "swap"]


@pytest.mark.parametrize("site", SITES, ids=lambda site: site[1])
@pytest.mark.parametrize("transition", ["shade", "consume", "other"])
@pytest.mark.parametrize("preset", [False, True], ids=["new-bit", "preset-bit"])
@pytest.mark.parametrize("legacy", [False, True], ids=["production", "legacy-control"])
def test_metadata_preserves_concurrent_header(site, transition, preset, legacy):
    runtime = MetadataRuntime(site, transition, preset, legacy)
    runtime.invoke()
    assert len(runtime.updates) == 1
    observed = runtime.updates[0]
    if legacy:
        assert observed != runtime.expected, "negative control must exhibit the lost update"
        if transition == "shade":
            assert observed & COLOR == WHITE and (runtime.credit, runtime.tokens) == (1, 1)
        elif transition == "consume":
            assert observed & COLOR == GRAY and (runtime.credit, runtime.tokens) == (0, 0)
    else:
        assert observed == runtime.expected
        if transition in ("shade", "consume"):
            assert bool(observed & GRAY) == bool(runtime.credit) == bool(runtime.tokens)


def test_all_eleven_metadata_sites_use_relaxed_atomic_or():
    assert len(SITES) == 11
    for filename, name, bit in SITES:
        tree = ast.parse((RUNTIME / filename).read_text())
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        calls = [call for call in ast.walk(node) if isinstance(call, ast.Call)
                 and isinstance(call.func, ast.Name) and call.func.id == "atomic_rmw_i32"]
        assert len(calls) == 1, name
        op, obj, offset, value, order = calls[0].args
        assert ast.literal_eval(op) == "or" and ast.literal_eval(order) == "relaxed"
        constants = {"PYOBJECTHEADER_FLAGS_OFFSET": 12, "PY_FLAG_IMMORTAL": IMMORTAL,
                     "PY_FLAG_GC_PINNED": PINNED}
        assert (constants[offset.id] if isinstance(offset, ast.Name) else offset.value) == 12
        assert (constants[value.id] if isinstance(value, ast.Name) else value.value) == bit
        assert not any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                       and call.func.id == "store_i32" for call in ast.walk(node)), name


@pytest.mark.parametrize("name", ["_path_clear_root_prefix", "_mkdir_drop", "_td_drop"])
def test_alias_pin_restoration_keeps_reverse_release_order(name):
    site = next(site for site in SITES if site[1] == name)
    runtime = MetadataRuntime(site, "shade", False)
    # This test observes two reverse-order aliases without injecting a race.
    runtime.site = "no_interference"
    n, obj = runtime.ns, runtime.target
    slots, pins = runtime.frame(24)
    obj.flags |= PINNED
    obj.pins = 2
    for index, prior in ((0, 0), (1, PINNED)):
        runtime.write(slots, index * 8, obj)
        runtime.write(pins, index * 8, prior)
    if name == "_path_clear_root_prefix":
        n[name](slots, pins, 2)
    else:
        n[name](slots, pins, 1)
        n[name](slots, pins, 0)
    assert runtime.events == ["unpin", "clear", "unpin", "clear"]
    assert obj.pins == 0 and obj.flags & PINNED == 0
    assert (runtime.pin_calls, runtime.unpin_calls) == (0, 2)
    assert runtime.read(slots, 0) is None and runtime.read(slots, 8) is None


@pytest.mark.integration
def test_metadata_atomic_or_executes_owned_runtime_all_collectors(tmp_path, pcc_runtime_archive):
    """Execute the three IMMORTAL sites; eight pin-restore sites are host-only.

    This is native ABI/bit preservation, not a native race reproducer.
    """
    from tests.owned_c_execution import compile_owned_c_with_runtime

    binary = tmp_path / "metadata_flags"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/gc_metadata_flags_atomic.c", binary,
        runtime_archive=Path(pcc_runtime_archive),
    )
    for backend in range(5):
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (run.returncode, run.stdout, run.stderr) == (0, "METADATA_FLAGS_ATOMIC_OK\n", ""), backend
