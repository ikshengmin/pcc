"""Execute the real registry bodies with owned native-memory operations modeled.

These component tests are not native qualification. The matching emitted
program gate exercises ordinary module-object attribute lookup separately.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest


PORT = Path(os.environ.get("PCC_MODULE_REGISTRY_MODEL_SOURCE", str(
    Path(__file__).resolve().parents[2] /
    "pcc/runtime/py/py_compiled_module_runtime.py"
)))


class _Block:
    def __init__(self, size=0, kind="raw"):
        self.bytes = bytearray(size)
        self.fields = {}
        self.kind = kind
        self.refs = 1
        self.pin = False
        self.alive = True
        self.text = ""


class _Memory:
    def __init__(self, *, phase="", fail=""):
        self.globals = {}
        self.attrs = {}
        self.objects = []
        self.raw = []
        self.error = None
        self.cleanup_callback = None
        self.phase, self.fail = phase, fail
        self.moves = 0
        self.decoy = self.object("decoy")
        self.ns = {
            "PYOBJECTHEADER_FLAGS_OFFSET": 12, "PY_FLAG_GC_PINNED": 64,
            "py_current_exception": lambda: self.error,
            "py_clear_exception": self.clear_error,
            "py_tls_exc_set": self.set_tls_error,
            "c_abi_export": lambda _name: lambda fn: fn,
            "null": lambda: None, "ptr_is_null": lambda p: p is None,
            "malloc": self.malloc, "calloc": lambda n, s: self.malloc(n * s),
            "free": self.free, "memcpy": self.memcpy,
            "cstr": self.cstr, "strlen": lambda p: len(self.text(p)),
            "ptr_add": self.add, "load_i8": self.byte,
            "store_i8": self.setbyte, "load_i32": self.read,
            "store_i32": self.write, "load_ptr": self.read,
            "store_ptr": self.write,
            "global_load_ptr": self.globals.get,
            "global_store_ptr": self.globals.__setitem__,
            "py_class_new": lambda *_args: self.object("class"),
            "pcc_gc_pin": self.pin, "pcc_gc_unpin": self.unpin,
            "py_err_occurred": lambda: int(self.error is not None),
            "py_module_attrs_dict": self.attrs_dict,
            "py_module_attr_set": self.attr_set,
            "py_instance_new": lambda _cls: self.new_module(),
            "pcc_gc_store_ptr": self.store_ref,
            "py_str_new": self.string, "py_instance_setattr": self.setattr,
            "py_incref": self.incref, "py_decref": self.decref,
            "py_exc_new": lambda *_args: "out of memory",
            "py_raise_owned": self.raise_error,
            "call_void_ptr0": lambda fn: fn(),
        }
        parsed = ast.parse(PORT.read_text(), filename=str(PORT))
        functions = [node for node in parsed.body if isinstance(node, ast.FunctionDef)]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(PORT), "exec"), self.ns)

    def object(self, kind):
        value = _Block(kind=kind)
        self.objects.append(value)
        return value

    def gate(self, phase):
        if phase != self.phase:
            return
        for old in list(self.objects):
            if not old.alive or old.pin or old.kind not in ("module", "decoy"):
                continue
            new = self.object(old.kind)
            new.fields, new.refs = old.fields.copy(), old.refs
            # Heap edges are traced; unregistered raw C registry/local pointers
            # deliberately are not, which exposes missing module pins.
            for owner in self.objects:
                if owner.alive:
                    owner.fields = {k: new if v is old else v for k, v in owner.fields.items()}
            if old is self.decoy:
                self.decoy = new
            old.alive = False
            self.moves += 1

    def malloc(self, size):
        if self.live_modules() and (
            (self.fail == "index" and size == 4096)
            or (self.fail == "node" and size == 32)
            or (self.fail == "name_copy" and size == 4)
        ):
            return None
        value = _Block(size)
        self.raw.append(value)
        return value

    def free(self, value):
        assert value.alive and value.kind == "raw"
        value.alive = False

    def pointer(self, value):
        return value if isinstance(value, tuple) else (value, 0)

    def add(self, value, offset):
        obj, base = self.pointer(value)
        return obj, base + offset

    def read(self, value, offset):
        obj, base = self.pointer(value)
        assert obj.alive, "stale registry/module address"
        if obj.kind != "raw" and base + offset == 12:
            return 64 if obj.pin else 0
        return obj.fields.get(base + offset, 0 if offset == 16 else None)

    def write(self, value, offset, item):
        obj, base = self.pointer(value)
        assert obj.alive, "stale registry/module address"
        obj.fields[base + offset] = item

    def byte(self, value, offset):
        obj, base = self.pointer(value)
        assert obj.alive
        return obj.bytes[base + offset]

    def setbyte(self, value, offset, byte):
        obj, base = self.pointer(value)
        obj.bytes[base + offset] = byte

    def memcpy(self, dest, source, count):
        d, db = self.pointer(dest)
        s, sb = self.pointer(source)
        d.bytes[db:db + count] = s.bytes[sb:sb + count]

    def cstr(self, text):
        value = _Block()
        value.bytes = bytearray(text.encode() + b"\0")
        return value

    def text(self, value):
        obj, base = self.pointer(value)
        return bytes(obj.bytes[base:]).split(b"\0", 1)[0].decode()

    def pin(self, value):
        assert value.alive
        assert not value.pin
        value.pin = True

    def unpin(self, value):
        assert value.alive and value.pin
        value.pin = False

    def incref(self, value):
        if isinstance(value, _Block):
            assert value.alive, "stale owned module/value"
            value.refs += 1

    def decref(self, value):
        if isinstance(value, _Block):
            assert value.alive, "stale cleanup address"
            value.refs -= 1
            assert value.refs >= 0
            if value.refs == 0:
                assert not value.pin, "leaked pin after last owner"
                value.alive = False
                for item in value.fields.values():
                    self.decref(item)
                if value.kind == "module" and self.cleanup_callback:
                    self.cleanup_callback()

    def attrs_dict(self, name, create):
        key = self.text(name)
        if create and key not in self.attrs:
            self.attrs[key] = self.object("dict")
            self.attrs[key].pin = True
        return self.attrs.get(key)

    def new_module(self):
        self.gate("allocation")
        return None if self.fail == "module" else self.object("module")

    def store_ref(self, owner, slot, value):
        self.gate("store")
        assert owner.alive
        obj, offset = self.pointer(slot)
        self.incref(value)
        old = obj.fields.get(offset)
        obj.fields[offset] = value
        self.decref(old)

    def string(self, text, _size):
        self.gate("string")
        if self.fail == "string":
            return None
        value = self.object("str")
        value.text = self.text(text)
        return value

    def setdict(self, attrs, key, value):
        self.incref(value)
        old = attrs.fields.get(key)
        attrs.fields[key] = value
        self.decref(old)

    def setattr(self, module, name, value):
        self.gate("setattr")
        assert module.alive
        if self.fail == "name":
            return -1
        self.setdict(module.fields[24], self.text(name), value)
        return 0

    def attr_set(self, name, key, value):
        self.gate("parent")
        assert value.alive
        if self.fail == "publication":
            self.raise_error("publication failed")
            return -1
        self.setdict(self.attrs_dict(name, 1), self.text(key), value)
        return 0

    def raise_error(self, error):
        old = self.error
        self.error = self.object("exception")
        self.error.text = error
        self.decref(old)

    def clear_error(self):
        old, self.error = self.error, None
        self.decref(old)

    def set_tls_error(self, error):
        self.error = error

    def register(self, name, init=None):
        if init is None:
            init = lambda: self.attrs_dict(self.cstr(name), 1)
        assert self.ns["py_compiled_module_register_init"](self.cstr(name), init) == 0

    def import_module(self, name):
        return self.ns["py_compiled_module_import_by_name"](self.cstr(name))

    def namespace(self, module):
        assert module.alive
        return module.fields[24].fields

    def live_modules(self):
        return [obj for obj in self.objects if obj.kind == "module" and obj.alive]


def test_first_import_publishes_live_child_and_cached_import_preserves_override():
    m = _Memory()
    order = []
    for name in ("pkg", "pkg.child"):
        m.register(name, lambda name=name: (order.append(name), m.attrs_dict(m.cstr(name), 1)))
    child = m.import_module("pkg.child")
    parent = m.import_module("pkg")
    assert m.namespace(parent)["child"] is child
    assert child.refs == 3  # registry, parent namespace, returned reference
    assert order == ["pkg", "pkg.child"]
    replacement = m.object("value")
    m.setdict(m.attrs["pkg"], "child", replacement)
    cached = m.import_module("pkg.child")
    assert cached is child and m.namespace(parent)["child"] is replacement
    assert order == ["pkg", "pkg.child"]
    m.decref(cached)
    m.decref(child)
    assert child.refs == 1 and child.pin


def test_deep_package_publishes_each_edge_with_single_identity():
    m = _Memory()
    for name in ("pkg", "pkg.middle", "pkg.middle.leaf"):
        m.register(name)
    leaf = m.import_module("pkg.middle.leaf")
    parent = m.import_module("pkg")
    middle = m.namespace(parent)["middle"]
    assert m.namespace(middle)["leaf"] is leaf
    assert m.import_module("pkg.middle") is middle
    assert len(m.live_modules()) == 3


def test_parent_initializer_importing_requested_child_reuses_its_object():
    m = _Memory()
    seen = []
    m.register("pkg", lambda: seen.append(m.import_module("pkg.child")))
    m.register("pkg.child")
    child = m.import_module("pkg.child")
    assert child is seen[0]
    assert m.attrs["pkg"].fields["child"] is child
    assert len(m.live_modules()) == 2


def test_circular_import_observes_same_partial_object_before_parent_publication():
    m = _Memory()
    seen = []
    m.register("pkg")
    def init_child():
        assert "child" not in m.attrs["pkg"].fields
        again = m.import_module("pkg.child")
        assert "child" not in m.attrs["pkg"].fields
        m.setdict(m.attrs["pkg.child"], "progress", 42)
        seen.append(again)
    m.register("pkg.child", init_child)
    child = m.import_module("pkg.child")
    assert child is seen[0]
    assert m.namespace(child)["progress"] == 42
    assert m.attrs["pkg"].fields["child"] is child
    assert len(m.live_modules()) == 2


def test_exception_removes_registry_owner_without_publishing_failed_child():
    m = _Memory()
    escaped = []
    m.register("pkg")
    def failing():
        escaped.append(m.import_module("pkg.child"))
        m.raise_error("original error")
    m.register("pkg.child", failing)
    assert m.import_module("pkg.child") is None
    assert m.error.text == "original error"
    assert "child" not in m.attrs["pkg"].fields
    child = escaped[0]
    assert child.alive and child.refs == 1 and not child.pin
    assert m.ns["_lookup_module_node"](m.cstr("pkg.child")) is None
    assert m.ns["_lookup_init_node"](m.cstr("pkg.child")).fields[16] == 0
    m.decref(child)
    assert not child.alive


def test_parent_exception_never_starts_child():
    m = _Memory()
    called = []
    m.register("pkg", lambda: m.raise_error("parent failed"))
    m.register("pkg.child", lambda: called.append(True))
    assert m.import_module("pkg.child") is None
    assert not called and m.error.text == "parent failed"
    assert not m.live_modules()


@pytest.mark.parametrize("fail", ("module", "string", "name", "index", "node", "name_copy"))
def test_creation_failure_releases_module_owner_and_pin(fail):
    m = _Memory(fail=fail)
    m.register("mod")
    assert m.import_module("mod") is None
    assert not m.live_modules()


def test_failure_unlinks_middle_nodes_in_both_colliding_registry_chains():
    m = _Memory()
    target = "failing"
    bucket = m.ns["_cstr_hash_bucket"](m.cstr(target))
    other = next("later" + str(i) for i in range(10000)
                 if m.ns["_cstr_hash_bucket"](m.cstr("later" + str(i))) == bucket)
    m.register("earlier")
    m.register(other)
    escaped = []
    def failing():
        escaped.append(m.import_module(other))
        m.raise_error("initializer failed")
    m.register(target, failing)
    earlier = m.import_module("earlier")
    assert m.import_module(target) is None
    assert m.ns["_lookup_module_node"](m.cstr(target)) is None
    m.error = None
    assert m.import_module(other) is escaped[0]
    assert m.import_module("earlier") is earlier
    node = m.globals["pcc_compiled_modules"]
    chain = []
    while node is not None:
        chain.append(m.text(node.fields[0]))
        node = node.fields[16]
    assert chain == [other, "earlier"]


def test_failed_parent_publication_retains_only_the_completed_cache_owner():
    m = _Memory()
    m.register("pkg")
    m.register("pkg.child")
    parent = m.import_module("pkg")
    m.fail = "publication"
    assert m.import_module("pkg.child") is None
    assert m.error.text == "publication failed"
    assert "child" not in m.namespace(parent)
    node = m.ns["_lookup_module_node"](m.cstr("pkg.child"))
    module = node.fields[8]
    assert module.refs == 1 and module.pin


@pytest.mark.parametrize("phase", ("allocation", "store", "string", "setattr", "parent"))
def test_registry_module_addresses_survive_each_relocation_gate(phase):
    m = _Memory(phase=phase)
    m.register("pkg")
    m.register("pkg.child")
    child = m.import_module("pkg.child")
    parent = m.import_module("pkg")
    assert m.namespace(parent)["child"] is child
    assert len(m.live_modules()) == 2
    assert all(obj.pin for obj in m.live_modules())
    assert m.moves > 0  # independent live object actually moved at this gate


def test_unknown_module_does_not_materialize_a_namespace():
    m = _Memory()
    assert m.import_module("missing.child") is None
    assert m.attrs == {} and not m.live_modules()


@pytest.mark.parametrize("prior_pin", (False, True))
@pytest.mark.parametrize("callback_error", (False, True))
def test_failed_init_preserves_exact_exception_across_cleanup_callbacks(prior_pin, callback_error):
    m = _Memory()
    original = []
    def failing():
        m.raise_error("initializer failure")
        m.error.pin = prior_pin
        original.append(m.error)
    m.register("mod", failing)
    def cleanup():
        assert m.error is None
        if callback_error:
            m.raise_error("callback failure")
    m.cleanup_callback = cleanup
    assert m.import_module("mod") is None
    assert m.error is original[0]
    assert m.error.refs == 1 and m.error.pin == prior_pin
    assert not m.live_modules()
