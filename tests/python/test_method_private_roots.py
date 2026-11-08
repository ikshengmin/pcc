"""Method roots retain legacy registration, paired exits, and five-GC coverage.

Specification: docs/compiler-contract.md; docs/project-intent.md shared
slot/root, ordinary-class identity, value-payload, and suspension contracts.
An exact private address and bounded map do not prove equivalent conservative
collection: changing registration calls can expose stale pointer words in GC
implementation frames and change finalizer timing. Method parameter cells
keep the legacy protocol until that collector boundary is qualified. These
contracts do not claim to fix the existing finalizer-count failure.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


METHOD_PROGRAM = '''import gc

class Probe:
    def __init__(self, value):
        self.value = value

    def receiver(self, value):
        gc.collect()
        assert self.value is value
        return value

    @staticmethod
    def static(value):
        gc.collect()
        return value

    @classmethod
    def klass(cls, value):
        gc.collect()
        result = cls(value)
        gc.collect()
        return result

    def boxed(self, value: int) -> int:
        gc.collect()
        return value

    @staticmethod
    def capture(value):
        def replace(other):
            nonlocal value
            value = other
        def read():
            gc.collect()
            return value
        gc.collect()
        return read, replace

    def capture_receiver(self):
        def replace(other):
            nonlocal self
            self = other
        def read():
            gc.collect()
            return self
        gc.collect()
        return read, replace

    def exits(self, value, early: bool, fail: bool, events):
        try:
            gc.collect()
            if early:
                return value
            if fail:
                raise ValueError("method root failure")
            return self.receiver(value)
        finally:
            gc.collect()
            events.append("finally")

def main():
    events = []
    for index in range(8):
        value = [index]
        probe = Probe(value)
        assert probe.receiver(value) is value
        assert Probe.static(value) is value
        other = Probe.klass(value)
        assert other.value is value
        assert probe.boxed(1 << 80) == 1208925819614629174706176
        read, replace = Probe.capture(value)
        gc.collect()
        assert read() is value
        replacement = [index + 1]
        replace(replacement)
        gc.collect()
        assert read() is replacement
        receiver_read, receiver_replace = probe.capture_receiver()
        gc.collect()
        assert receiver_read() is probe
        receiver_replace(other)
        gc.collect()
        assert receiver_read() is other
        assert probe.exits(value, True, False, events) is value
        assert probe.exits(value, False, False, events) is value
        try:
            probe.exits(value, False, True, events)
        except ValueError as error:
            assert str(error) == "method root failure"
        else:
            raise AssertionError("method exception lost")
        gc.collect()
    assert len(events) == 24
    print("METHOD_PRIVATE_ROOTS_OK")
main()
'''

VALUE_PROGRAM = '''import gc
import pcc

@pcc.valueclass
class Packet:
    values: list
    label: str

    def receiver(self):
        gc.collect()
        assert self.label == "packet"
        return self.values

class Consumer:
    def instance(self, packet: Packet):
        gc.collect()
        return packet.values

    @staticmethod
    def static(packet: Packet):
        gc.collect()
        return packet.values

    @classmethod
    def klass(cls, packet: Packet):
        gc.collect()
        return cls.static(packet)

def main():
    for index in range(8):
        values = [index]
        packet = Packet(values, "packet")
        consumer = Consumer()
        gc.collect()
        assert packet.receiver() is values
        assert consumer.instance(packet) is values
        assert Consumer.static(packet) is values
        assert Consumer.klass(packet) is values
        values.append(index + 1)
        gc.collect()
        assert packet.values == [index, index + 1]
    print("METHOD_PAYLOAD_ROOTS_OK")
main()
'''

SUSPEND_PROGRAM = '''import asyncio
import gc

class Probe:
    def __init__(self, value):
        self.value = value

    def keep(self, value):
        gc.collect()
        assert self.value is value
        return value

    def stream(self, value, events):
        try:
            yield self.keep(value)
            gc.collect()
            yield self.keep(value)
        finally:
            gc.collect()
            events.append("closed")

    async def resume(self, value):
        await asyncio.sleep(0)
        gc.collect()
        first = self.keep(value)
        await asyncio.sleep(0)
        gc.collect()
        assert first is value
        return self.keep(value)

def main():
    events = []
    for index in range(4):
        value = [index]
        probe = Probe(value)
        stream = probe.stream(value, events)
        assert next(stream) is value
        gc.collect()
        assert next(stream) is value
        try:
            next(stream)
        except StopIteration:
            pass
        else:
            raise AssertionError("generator did not finish")
        stream = probe.stream(value, events)
        assert next(stream) is value
        stream.close()
        gc.collect()
        assert asyncio.run(probe.resume(value)) is value
    assert len(events) == 8
    print("METHOD_SUSPENDED_ROOTS_OK")
main()
'''

FACTORY_PROGRAM = '''import gc
import pcc.virtual_thread as vt

def slow(value):
    vt.yield_now()
    gc.collect()
    return value

def failing(value):
    vt.yield_now()
    gc.collect()
    raise ValueError("factory root failure")

class Gate:
    @vt.continuation_factory
    def choose(self, value, wait: bool):
        gc.collect()
        if wait:
            return vt.continuation(slow, value)
        return vt.completed(value)

    @vt.continuation_factory
    def fail(self, value):
        gc.collect()
        return vt.continuation(failing, value)

def parent():
    gate = Gate()
    for index in range(8):
        value = [index]
        assert gate.choose(value, False) is value
        gc.collect()
        assert gate.choose(value, True) is value
        try:
            gate.fail(value)
        except ValueError as error:
            assert str(error) == "factory root failure"
        else:
            raise AssertionError("factory exception lost")
        gc.collect()
    return "METHOD_FACTORY_ROOTS_OK"

task = vt.spawn(parent)
vt.run(1, 512)
print(vt.result(task))
'''


def test_method_allocations_keep_legacy_provenance_and_initialization_order():
    from pcc.frontends.python.codegen import class_gen

    tree = ast.parse(Path(class_gen.__file__).read_text(encoding="utf-8"))
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "_emit_method_body")
    assert not any(isinstance(node, ast.ImportFrom)
                   and any(alias.name == "record_private_root_address" for alias in node.names)
                   for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Call)
                   and ast.unparse(node.func).rsplit(".", 1)[-1] == "record_private_root_address"
                   for node in ast.walk(method))
    records = []
    for owner in ast.walk(method):
        for _field, statements in ast.iter_fields(owner):
            if not isinstance(statements, list):
                continue
            for position, statement in enumerate(statements):
                if not (isinstance(statement, ast.Assign)
                        and isinstance(statement.value, ast.Call)
                        and ast.unparse(statement.value.func) == "parent.builder.alloca"):
                    continue
                allocated = ast.unparse(statement.value.args[0])
                slot = ast.unparse(statement.targets[0])
                store = statements[position + 1]
                assert isinstance(store, ast.Expr) and isinstance(store.value, ast.Call)
                assert ast.unparse(store.value.func) == "parent.builder.store"
                expected = ("cell" if allocated == "_CSTR" else
                            "recv_arg" if allocated == "recv_ir_ty" else "ir_arg")
                assert ast.unparse(store.value.args[0]) == expected
                assert ast.unparse(store.value.args[1]) == slot
                records.append((allocated, slot))
    assert sorted(records) == sorted([
        ("_CSTR", "slot"), ("ir_ty", "slot"),
        ("recv_ir_ty", "self_slot"), ("ir_ty", "slot"),
    ])


def _emit(tmp_path, monkeypatch, source_text, mapped):
    from pcc.frontends.python.pipeline import compile_python
    from tests.owned_ir_validation import verify_ir_text

    monkeypatch.setenv("PCC_PRECISE_STACKMAP_TABLES", "1" if mapped else "0")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "method_private_roots.py"
    output = source.with_suffix(".ll")
    source.write_text(source_text, encoding="utf-8")
    compile_python(str(source), str(output), backend="self", python_library=True,
                   emit_llvm_only=True, ir_scaffold_mode="on")
    text = output.read_text(encoding="utf-8")
    return verify_ir_text(text), text


def _roots(module, suffix):
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel
    from tests.python.root_slot_contract import RootSlotContract

    matches = [fn for fn in module.functions if fn.name.endswith("_" + suffix)]
    assert len(matches) == 1, suffix
    function = matches[0]
    blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
    return RootSlotContract(blocks, module.globals_)


def _assert_borrowed_pointer(roots, parameter, mapped):
    cells = {base for base, (ty, _block) in roots.allocas.items()
             if ty.is_ptr and re.fullmatch(re.escape(parameter) + r"\.addr(?:\.\d+)*", base.lstrip("%"))}
    entries = [(block, index, ins) for block, index, ins in roots.rows
               if roots.called(ins, "pcc_gc_frame_enter")
               and roots.slot(roots.args(ins)[1]) in {(base, 0) for base in cells}]
    # A boxed receiver also has its original unused alloca. Check the cell
    # actually registered as borrowed, which must retain the legacy protocol.
    assert len(entries) == 1, (parameter, cells)
    block, index, enter = entries[0]
    assert block.name == "entry"
    suffix = "_mapped" if mapped else ""
    assert enter.data[2] == "pcc_gc_frame_enter" + suffix
    frame_map, address = roots.args(enter)
    descriptor = roots.globals[roots.symbol(frame_map).lstrip("@")]
    assert descriptor.is_constant and descriptor.type.describe() == "i32"
    assert int(descriptor.initializer) == -1
    identity = roots.slot(address)
    assert any(owner.name == block.name and position < index
               and ins.kind == "store" and ins.data[0].is_ptr
               and ins.data[1] != "null" and roots.slot(ins.data[3]) == identity
               for owner, position, ins in roots.rows), "borrowed value must precede registration"
    leaves = [ins for _, _, ins in roots.rows
              if roots.called(ins, "pcc_gc_frame_leave")
              and roots.slot(roots.args(ins)[0]) == identity]
    assert leaves and all(ins.data[2] == "pcc_gc_frame_leave" + suffix for ins in leaves)
    roots.assert_frame_exits()


@pytest.mark.parametrize("mapped", (False, True))
def test_method_borrowed_pointer_cells_keep_paired_legacy_scope(tmp_path, monkeypatch, mapped):
    module, _text = _emit(tmp_path, monkeypatch, METHOD_PROGRAM, mapped)
    expected = {
        "Probe_receiver": ("self", "value"),
        "Probe_static": ("value",),
        "Probe_klass": ("cls", "value"),
        "Probe_boxed": ("self", "value"),
        "Probe_capture": ("value",),
        "Probe_capture_receiver": ("self",),
        "Probe_exits": ("self", "value", "events"),
    }
    for method, parameters in expected.items():
        roots = _roots(module, method)
        for parameter in parameters:
            _assert_borrowed_pointer(roots, parameter, mapped)
    exits = _roots(module, "Probe_exits")
    assert any(roots_ins.kind == "call" and roots_ins.data[2].startswith("py_exc_new")
               for _, _, roots_ins in exits.rows), "must exercise an exceptional exit"


@pytest.mark.parametrize("mapped", (False, True))
def test_value_payload_fields_keep_conservative_registration(tmp_path, monkeypatch, mapped):
    module, _text = _emit(tmp_path, monkeypatch, VALUE_PROGRAM, mapped)
    for method, parameter in (("Packet_receiver", "self"),
                              ("Consumer_instance", "packet"),
                              ("Consumer_static", "packet"),
                              ("Consumer_klass", "packet")):
        roots = _roots(module, method)
        aggregate = {base for base, (ty, _) in roots.allocas.items()
                     if ty.is_struct and re.fullmatch(re.escape(parameter) + r"\.addr(?:\.\d+)*", base.lstrip("%"))}
        assert len(aggregate) == 1, method
        fields = [ins for _, _, ins in roots.rows
                  if roots.called(ins, "pcc_gc_frame_enter")
                  and roots.slot(roots.args(ins)[1])[0] in aggregate]
        assert len(fields) == 2, "both pointer fields must remain rooted"
        for enter in fields:
            assert enter.data[2] == "pcc_gc_frame_enter" + ("_mapped" if mapped else "")
            descriptor = roots.globals[roots.symbol(roots.args(enter)[0]).lstrip("@")]
            assert descriptor.is_constant and int(descriptor.initializer) == -1
        roots.assert_frame_exits()


@pytest.mark.parametrize("mapped", (False, True))
def test_factory_methods_keep_legacy_cells_without_resume_wrapper(tmp_path, monkeypatch, mapped):
    module, text = _emit(tmp_path, monkeypatch, FACTORY_PROGRAM, mapped)
    for method in ("Gate_choose", "Gate_fail"):
        assert method + "__gen_resume" not in text
        roots = _roots(module, method)
        for parameter in ("self", "value"):
            _assert_borrowed_pointer(roots, parameter, mapped)


@pytest.mark.integration
@pytest.mark.parametrize("case,program,marker,reference", [
    ("methods", METHOD_PROGRAM, "METHOD_PRIVATE_ROOTS_OK", True),
    ("payload", VALUE_PROGRAM, "METHOD_PAYLOAD_ROOTS_OK", True),
    ("suspended", SUSPEND_PROGRAM, "METHOD_SUSPENDED_ROOTS_OK", True),
    ("factory", FACTORY_PROGRAM, "METHOD_FACTORY_ROOTS_OK", False),
])
def test_native_method_private_roots_five_gc(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
    case, program, marker, reference,
):
    # Force the runtime registration route even on a stack-walking host.
    monkeypatch.setenv("PCC_PRECISE_STACKMAP_TABLES", "0")
    source = tmp_path / ("method_" + case + ".py")
    source.write_text(program, encoding="utf-8")
    expected = marker + "\n"
    if reference:
        oracle = subprocess.run([sys.executable, str(source)], capture_output=True,
                                text=True, timeout=20)
        assert oracle.returncode == 0, oracle.stdout + oracle.stderr
        assert oracle.stdout == expected
    binary = source.with_suffix("")
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_runtime_archive),
                            ir_scaffold_mode="on")
    for backend in range(5):
        ran = subprocess.run([str(binary)], capture_output=True, text=True,
                             timeout=20, env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert ran.returncode == 0, f"GC{backend}: {ran.stdout}{ran.stderr}"
        assert ran.stdout == expected, f"GC{backend}: {ran.stdout}{ran.stderr}"
