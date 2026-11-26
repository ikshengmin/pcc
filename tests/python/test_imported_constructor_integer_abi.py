"""Imported-class constructors share the physical method-argument ABI."""
import re

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.pipeline_context import build_closed_world_context
from pcc.frontends.python.type_infer import infer_module


def _emit(module, exports, output):
    typed = infer_module(module, external_exports={key: value for key, value in exports.items() if key != module.name})
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    codegen._native_module_exports = exports
    emitted = str(codegen.generate(typed))
    output.write_text(emitted)
    assert "define " in emitted
    assert emit_owned_object(emitted, "x86_64-unknown-linux-gnu")[:4] == b"\x7fELF"
    return emitted


# Out-of-range values are not machine-i64 inputs for the runtime-port provider.
@pytest.mark.parametrize("value,machine_provider", [
    (value, machine_provider)
    for machine_provider in (False, True)
    for value in ("len(values)", "17", "18446744073709551615", "-(1 << 100)")
    if not (machine_provider and value in ("18446744073709551615", "-(1 << 100)"))
])
def test_imported_constructor_matches_provider_int_abi(tmp_path, monkeypatch, value, machine_provider):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    owner = "pcc.runtime.py.count_provider" if machine_provider else "provider"
    provider = tmp_path / "provider.py"
    provider.write_text(("__pcc_runtime_port__ = True\n" if machine_provider else "") + '''class Box:
    def __init__(self, count: int):
        self.count = count
''')
    entry = tmp_path / "entry.py"
    entry.write_text("from pcc.unsafe import null\nimport " + owner + " as provider\ndef main(values: list[str]):\n    return provider.Box(" + value + ")\n")
    modules, exports, _ = build_closed_world_context([str(provider), str(entry)], [owner, "entry"])
    method = next(item for item in exports[owner]["Box"]["methods"] if item["name"] == "__init__")
    assert method["box_int_abi"] is (not machine_provider)
    defining = _emit(modules[0], exports, tmp_path / "provider.ll")
    calling = _emit(modules[1], exports, tmp_path / "entry.ll")
    symbol = method["symbol"]
    for prefix, text in (("define", defining), ("declare", calling), ("call", calling)):
        matches = re.findall(r"(?:^|\s)" + prefix + r" [^\n]*@" + symbol + r"\(([^\n]*)\)", text, re.M)
        assert len(matches) == 1, (prefix, symbol)
        lane = matches[0].split(",")[1].strip().split()[0]
        assert lane == ("i64" if machine_provider else "ptr")
    # Follow the concrete constructor result, not an unrelated take-slot
    # declaration or another function's metadata result. The sole collector
    # consumes this ordinary owner without the retired pin protocol.
    main = re.search(r"define[^\n]*@user_entry_main\([^\n]*\{\n(.*?)\n\}", calling, re.S)
    assert main is not None
    body = main.group(1)
    registered = re.search(
        r"call[^\n]*@pcc_gc_frame_enter_lifo_local\(ptr [^,]+, ptr "
        r"(%native\.constructor\.result\.operand\.[^)]+)\)", body)
    assert registered is not None
    slot = re.escape(registered.group(1))
    allocated = re.search(r"(%[^ ]+) = call[^\n]*@py_instance_new\([^\n]+\)", body)
    assert allocated is not None
    published = re.search(r"store ptr " + re.escape(allocated.group(1)) + r", ptr " + slot + r"$", body, re.M)
    receiver = re.search(r"(%native\.constructor\.receiver\.[^ ]+) = load ptr, ptr " + slot + r"$", body, re.M)
    assert published is not None and receiver is not None
    initializer = re.search(r"call[^\n]*@" + re.escape(symbol) + r"\(ptr " + re.escape(receiver.group(1)) + r",", body)
    taken = re.search(r"(%[^ ]+) = call[^\n]*@pcc_gc_take_slot\(ptr " + slot + r"\)", body)
    assert initializer is not None and taken is not None
    assert registered.start() < allocated.start() < published.start() < receiver.start() < initializer.start() < taken.start()
    # A rejected/taken result must not leak its registered local or lose
    # ownership between the local take and the shared return root.
    assert re.search(r"call[^\n]*@py_cleanup_two_roots_preserving_exception\(ptr " + slot + r",", body)
    assert re.search(r"@pcc_gc_frame_leave_lifo_local\(ptr " + slot + r"\)\s+%[^ ]+ = call[^\n]*@pcc_gc_take_slot\(ptr " + slot + r"\)", body)
    returned = re.search(
        r"call[^\n]*@pcc_gc_store_return_root\(ptr (%ret\.shared\.[^,]+), ptr " +
        re.escape(taken.group(1)) + r"\)", body)
    assert returned is not None and taken.end() < returned.start()
    ret_slot = re.escape(returned.group(1))
    return_frame = re.search(
        r"call[^\n]*@pcc_gc_frame_enter_lifo_local\(ptr [^,]+, ptr " + ret_slot + r"\)",
        body[returned.end():])
    assert return_frame is not None
    tail = re.search(
        r"(%[^ ]+) = call[^\n]*@pcc_gc_load_ptr\(ptr null, ptr " + ret_slot + r"\)\s+"
        r"call[^\n]*@pcc_gc_store_return_root\(ptr " + ret_slot + r", ptr null\)\s+"
        r"call[^\n]*@pcc_gc_frame_leave_lifo_local\(ptr " + ret_slot + r"\)", body)
    assert tail is not None and returned.end() + return_frame.end() < tail.start()
    assert re.search(r"ret ptr " + re.escape(tail.group(1)) + r"$", body, re.M)
    assert "@pcc_gc_take_pinned_slot(" not in calling
    assert not re.search(r"\bcall [^\n]*@py_cpy_", calling)


@pytest.mark.parametrize("call", ["provider.Box()", "provider.Box(count=len(values))", "provider.Child(len(values))", "provider.Varargs(len(values))"])
def test_imported_constructor_binding_paths_remain_valid(tmp_path, monkeypatch, call):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    provider = tmp_path / "provider.py"
    provider.write_text('''class Box:
    def __init__(self, count: int = 18446744073709551615):
        self.count = count
class Child(Box):
    pass
class Varargs:
    def __init__(self, count: int, *extra, **named):
        self.count = count
''')
    entry = tmp_path / "entry.py"
    entry.write_text("from pcc.unsafe import null\nimport provider\ndef main(values: list[str]):\n    return " + call + "\n")
    modules, exports, _ = build_closed_world_context([str(provider), str(entry)], ["provider", "entry"])
    for module in modules:
        text = _emit(module, exports, tmp_path / (module.name + ".ll"))
        assert not re.search(r"\bcall [^\n]*@py_cpy_", text)
