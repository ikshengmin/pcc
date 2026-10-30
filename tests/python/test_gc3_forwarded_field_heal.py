"""A GC3 read barrier moves a container field's reference when it heals it.

GC3 copies a young scalar (int, float, str, complex, bytes, bytearray) out
of the minor heap and forwards the old shell to the copy. The forwarding
node holds one reference on the copy, and every owning slot keeps its
reference counted on the object it names: collector promotion heals a
container field by retaining the copy and releasing the shell
(pcc_gc_generational_promote_owned_slot_mode), and pcc_gc_release of a
shell releases the shell. pcc_gc_load_ptr healed a field it read with a
plain store instead, so the field's count stayed on the shell and the
field's later release took the forwarding node's reference on the copy.
The copy then died while other fields still named the shell, and
pcc_gc_forwarding_remove_target retired the shell under them: a dict
deallocated afterwards aborted `[DEBUG-dict-slot]` (sparse dict values,
PCC_GC_MINOR_HEAP_SIZE=4096 PCC_GC_MINOR_ALLOC_MAX=128).
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from pcc.frontends.python.pipeline import compile_python
from pcc.runtime.py import py_abi_constants as abi


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_obj.py"
BARRIERS = ("pcc_gc_load_ptr", "pcc_gc_load_borrowed_ptr")


class Obj:
    def __init__(self, name, rc):
        self.name, self.rc = name, rc

    def __repr__(self):
        return self.name


class Slot:
    def __init__(self, value):
        self.value = value


class Heap:
    def __init__(self, backend=3):
        self.words = {"pcc_gc_read_barrier_enabled": 1, "pcc_gc_config_initialized": 1,
                      "pcc_gc_backend_selected": backend}
        self.forward = {}
        self.events = []
        # Runs before the field is swapped: another reader heals it first.
        self.before_swap = None
        self.addresses = {}

    def address(self, value):
        if value is None:
            return 0
        return self.addresses.setdefault(id(value), (len(self.addresses) + 1) * 16)

    def swap(self, slot, offset, expected, desired, success, failure):
        assert offset == 0 and (success, failure) == ("acq_rel", "acquire")
        if self.before_swap is not None:
            hook, self.before_swap = self.before_swap, None
            hook(slot)
        observed = self.address(slot.value)
        if observed == expected:
            slot.value = next(value for value in (self.forward.get(id(slot.value)), slot.value)
                              if self.address(value) == desired)
        self.events.append(("swap", observed == expected))
        return observed

    def barrier(self, name):
        tree = ast.parse(RUNTIME.read_text())
        wanted = {name, "_heal_forwarded_field"}
        nodes = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in wanted]
        assert {node.name for node in nodes} == wanted
        for node in nodes:
            node.decorator_list = []
        namespace = {key: getattr(abi, key) for key in dir(abi) if key.isupper()}
        namespace.update({
            "global_addr": lambda word: word,
            "load_i32": lambda base, offset: self.words[base],
            "load_ptr": lambda slot, offset: slot.value,
            "store_ptr": self.store,
            "null": lambda: None,
            "ptr_is_null": lambda value: int(value is None),
            "is_tagged_int": lambda value: int(isinstance(value, int)),
            "ptr_eq": lambda left, right: int(left is right),
            "ptr_to_int": self.address,
            "pcc_gc_backend": lambda: self.words["pcc_gc_backend_selected"],
            "_gc_backend_fast": lambda: self.words["pcc_gc_backend_selected"],
            "_gc_forwarding_population": lambda: len(self.forward),
            "_gc_relocation_candidate": lambda value: int(id(value) in self.forward),
            "pcc_gc_note_load": lambda: None,
            "pcc_gc_note_relocation_read": lambda value: self.forward.get(id(value), value),
            "atomic_cas_i64": self.swap,
            "py_incref": self.incref,
            "py_decref": self.decref,
        })
        code = compile(ast.Module(nodes, type_ignores=[]), str(RUNTIME), "exec")
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                namespace[const.co_name] = types.FunctionType(const, namespace)
        return namespace[name]

    def store(self, slot, offset, value):
        assert offset == 0
        self.events.append(("store", value))
        slot.value = value

    def incref(self, value):
        self.events.append(("incref", value))
        value.rc += 1

    def decref(self, value):
        self.events.append(("decref", value))
        value.rc -= 1

    def forwarded(self, shell_rc=2):
        shell, copy = Obj("shell", shell_rc), Obj("copy", 1)
        self.forward[id(shell)] = copy
        return shell, copy


@pytest.mark.parametrize("name", BARRIERS)
def test_a_container_field_moves_its_reference_to_the_copy(name):
    heap = Heap()
    shell, copy = heap.forwarded()
    field = Slot(shell)
    assert heap.barrier(name)("dict", field) is copy
    assert field.value is copy and (shell.rc, copy.rc) == (1, 2)
    assert heap.events == [("incref", copy), ("swap", True), ("decref", shell)]


@pytest.mark.parametrize("name", BARRIERS)
def test_a_field_another_reader_healed_first_moves_nothing(name):
    heap = Heap()
    shell, copy = heap.forwarded()
    field = Slot(shell)

    def other_reader(slot):
        slot.value = copy
        copy.rc += 1
        shell.rc -= 1
    heap.before_swap = other_reader
    assert heap.barrier(name)("dict", field) is copy
    assert field.value is copy and (shell.rc, copy.rc) == (1, 2)
    assert heap.events == [("incref", copy), ("swap", False), ("decref", copy)]


@pytest.mark.parametrize("name", BARRIERS)
def test_a_root_slot_without_an_owner_keeps_the_plain_heal(name):
    heap = Heap()
    shell, copy = heap.forwarded()
    root = Slot(shell)
    assert heap.barrier(name)(None, root) is copy
    assert root.value is copy and (shell.rc, copy.rc) == (2, 1)
    assert heap.events == [("store", copy)]


@pytest.mark.parametrize("name", BARRIERS)
def test_gc4_keeps_its_own_heal(name):
    heap = Heap(backend=4)
    shell, copy = heap.forwarded()
    field = Slot(shell)
    namespace_barrier = heap.barrier(name)
    # GC4 decides through its own slot and pointer lookups.
    namespace_barrier.__globals__["_gc_backend4_should_check_slot"] = lambda slot: 1
    namespace_barrier.__globals__["pcc_gc_backend4_slot_needs_resolve"] = lambda value: 1
    assert namespace_barrier("dict", field) is copy
    assert field.value is copy and (shell.rc, copy.rc) == (2, 1)
    assert heap.events == [("store", copy)]


@pytest.mark.parametrize("name", BARRIERS)
def test_unforwarded_values_are_returned_unchanged(name):
    heap = Heap()
    plain = Obj("plain", 1)
    heap.forwarded()
    field = Slot(plain)
    assert heap.barrier(name)("dict", field) is plain
    assert field.value is plain and plain.rc == 1 and heap.events == []


PROGRAM = r'''
def views(d):
    return list(d.keys()), list(d.values()), list(d.items())


def main():
    print(views({}))
    print(views({"name": "Ada", "plan": "pro"}))
    print(sorted({"profile": {"name": "Ada", "plan": "pro"},
                  "notifications": ["Your report is ready"]}.items()))
    sparse = {}
    for index in range(40):
        sparse[index] = str(index)
    for index in range(0, 40, 3):
        del sparse[index]
    print(views(sparse))
    mixed = {"small": 7, "big": 10 ** 30, "float": 2.5, "none": None, "flag": True,
             "bytes": b"x", "tuple": (1, "a"), "list": [1, [2]], "dict": {"k": "v"}}
    print(views(mixed))
    print(views({(1, "a"): "first", (2, "b"): ["second"]}))
    print(views({"before": 1, "dots": ..., "after": NotImplemented, "end": [3]}))


main()
'''

# Minor heap settings that aborted before the fix (4096 with 64/128/256 and
# 8192 with 64), around them, and the defaults.
MINOR_HEAPS = ((None, None), ("2048", "64"), ("4096", "64"), ("4096", "128"),
               ("4096", "256"), ("8192", "64"), ("16384", "128"))


@pytest.mark.integration
def test_healed_dict_values_match_cpython_under_small_minor_heaps(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "healed_fields.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "healed_fields"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    runs = [(backend, None, None) for backend in (0, 1, 2, 4)]
    runs += [(3, heap, limit) for heap, limit in MINOR_HEAPS]
    for backend, heap, limit in runs:
        env = dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend))
        if heap is not None:
            env.update(PCC_GC_MINOR_HEAP_SIZE=heap, PCC_GC_MINOR_ALLOC_MAX=limit)
        ran = subprocess.run([str(executable)], capture_output=True, text=True,
                             timeout=120, env=env)
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), (backend, heap, limit)
