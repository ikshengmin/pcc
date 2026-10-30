"""Owned x86 constant-lane lowering; object emission only, no native execution."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import coff_x86_64, elf_x86_64
from pcc.backend import self_backend_x86_64_linux as x86
from pcc.backend.self_backend_dispatch import emit_self_asm
from pcc.backend.self_backend_ir import TypeDesc
from pcc.backend.target_objects import encode_assembly_object

TARGETS = ("x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc")
SCALARS = (
    ("i1", "literal", 0, ("1", "0", "1", "0"), "r10b", 1),
    ("i3", "literal", 3, ("0", "1", "2", "-1"), "r10b", 1),
    ("i8", "slot", 0, ("-7", "3", "5", "7"), "r10b", 1),
    ("i16", "slot", 3, ("1", "3", "5", "-7"), "r10w", 2),
    ("i32", "slot", 2, ("1", "3", "5", "7"), "r10d", 4),
    ("i64", "literal", 3, ("1", "3", "5", "-7"), "r10", 8),
    ("float", "literal", 0, ("1.25", "2.5", "3.75", "4.5"), "xmm10", 4),
    ("float", "slot", 3, ("1.25", "2.5", "3.75", "4.5"), "xmm10", 4),
    ("double", "literal", 0, ("1.25", "2.5", "3.75", "4.5"), "xmm10", 8),
    ("double", "slot", 3, ("1.25", "2.5", "3.75", "4.5"), "xmm10", 8),
    ("ptr", "slot", 0, ("@a", "@b", "@c", "@d"), "r10", 8),
    ("ptr", "slot", 3, ("@a", "@b", "@c", "@d"), "r10", 8),
)


def _ir(scalar, source, index, values):
    vector = f"<4 x {scalar}>"
    literal = "<" + ", ".join(scalar + " " + value for value in values) + ">"
    globals_ = ""
    if scalar == "ptr":
        globals_ = "\n".join(f"@{name} = global i32 {value}" for name, value in zip("abcd", (11, 22, 33, 44))) + "\n"
        setup = "\n".join(
            f"  %v{lane} = insertelement {vector} " + ("poison" if lane == 0 else f"%v{lane-1}")
            + f", ptr {value}, i32 {lane}" for lane, value in enumerate(values))
        setup += f"\n  %slot = alloca {vector}\n  store {vector} %v3, ptr %slot\n  %vector = load {vector}, ptr %slot\n"
        value = "%vector"
    elif source == "slot":
        setup = f"  %slot = alloca {vector}\n  store {vector} {literal}, ptr %slot\n  %vector = load {vector}, ptr %slot\n"
        value = "%vector"
    else:
        setup = ""
        value = literal
    argument = "i32 %index" if index == "%index" else ""
    return (globals_ + f"define {scalar} @f({argument}) {{\nentry:\n" + setup
            + f"  %lane = extractelement {vector} {value}, i32 {index}\n  ret {scalar} %lane\n}}\n")


def _capture_loader(monkeypatch):
    calls = []
    original = x86._materialize_vector_lane_to_reg
    original_compute = x86._emit_compute_instruction

    def capture(func, value, vector_type, lane, reg):
        lines = original(func, value, vector_type, lane, reg)
        calls.append({"value": value, "type": vector_type.elem.describe(), "lane": lane,
                      "reg": reg, "source_offset": func.value_slots[value].offset if value in func.value_slots else None,
                      "lines": list(lines)})
        return lines

    def compute(func, kind, data):
        before = len(calls)
        lines = original_compute(func, kind, data)
        if kind == "extractelement" and len(calls) > before:
            assert len(calls) == before + 1
            calls[-1]["compute_lines"] = list(lines)
            calls[-1]["result_offset"] = func.value_slots[data[0]].offset
        return lines

    monkeypatch.setattr(x86, "_materialize_vector_lane_to_reg", capture)
    monkeypatch.setattr(x86, "_emit_compute_instruction", compute)
    return calls


def _owned_object(ir, target, monkeypatch, symbol="f"):
    def forbidden(*args, **kwargs):
        raise AssertionError("finite owned emission must not start an external process")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    assembly = emit_self_asm(f'target triple = "{target}"\n' + ir)
    image = encode_assembly_object(assembly, target)
    if "windows" in target:
        obj = coff_x86_64.parse_object(image)
        definitions = [s for s in obj.symbols if s.name == symbol and s.section > 0]
        assert len(definitions) == 1 and definitions[0].external and definitions[0].function
    else:
        obj = elf_x86_64.parse_relocatable(image)
        definitions = [s for s in obj.symbols if s.name == symbol and s.section_index > 0]
        assert len(definitions) == 1
        assert definitions[0].binding == elf_x86_64.STB_GLOBAL
        assert definitions[0].type == elf_x86_64.STT_FUNC
    assert image
    return assembly


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("scalar,source,lane,values,reg,stride", SCALARS,
                         ids=[f"{row[0]}-{row[1]}-{row[2]}" for row in SCALARS])
def test_constant_lane_real_assembly_and_object(target, scalar, source, lane, values, reg, stride, monkeypatch):
    calls = _capture_loader(monkeypatch)
    _owned_object(_ir(scalar, source, str(lane), values), target, monkeypatch)
    assert len(calls) == 1
    call = calls[0]
    assert (call["lane"], call["reg"]) == (lane, reg)
    assert call["type"] == ("void*" if scalar == "ptr" else scalar)
    memory = {1: "BYTE", 2: "WORD", 4: "DWORD", 8: "QWORD"}[stride]
    op = "movss" if scalar == "float" else "movsd" if scalar == "double" else "mov"
    if source == "slot":
        assert call["source_offset"] is not None
        offset = call["source_offset"] - lane * stride
        assert call["lines"] == [f"  {op} {reg}, {memory} PTR [rbp - {offset}]"]
    else:
        assert call["source_offset"] is None
        if scalar.startswith("i"):
            if scalar == "i3":
                # Existing aggregate storage rounds i3 to one byte. Its lane
                # helper subtracts 2**3 for a signed-spelled stored value;
                # extractelement then keeps exactly the declared three bits.
                storage = int(values[lane]) & ((1 << (stride * 8)) - 1)
                assert stride == 1 and storage == 0xff
                immediate = storage - (1 << 3)
                assert call["lines"] == [f"  mov {reg}, {immediate}"]
                assert immediate & 7 == int(values[lane]) & 7 == 7
            else:
                assert call["lines"] == [f"  mov {reg}, {int(values[lane]) if scalar != 'i1' else -1}"]
        else:
            # Both literal FP rows select 1.25, with its exact IEEE lane bits.
            assert values[lane] == "1.25"
            expected = (["  mov r10d, 0x3fa00000", "  movd xmm10, r10d"]
                        if scalar == "float" else
                        ["  mov r10, 0x3ff4000000000000", "  movq xmm10, r10"])
            assert call["lines"] == expected
    mask = ([f"  and {reg}, {(1 << int(scalar[1:])) - 1}"]
            if scalar in ("i1", "i3") else [])
    store = f"  {op} {memory} PTR [rbp - {call['result_offset']}], {reg}"
    # Observe the actual dispatch result before existing target passes may
    # forward frame stores/loads or register copies. Final ASM is still
    # encoded and parsed above; object validity is not native-value proof.
    assert call["compute_lines"] == call["lines"] + mask + [store]


def _original_parity_source():
    path = Path(__file__).parents[1] / "c" / "test_llvm_self_vector_parity.py"
    data = path.read_bytes()
    assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == "d8d809192f51b9c80a58b0a94cc66dbe3390116d"
    return ast.parse(data)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("name", ("INT_VECTOR_IR", "PTR_VECTOR_IR"))
def test_original_native_fixture_ir_emits_owned_object(target, name, monkeypatch):
    # Source data only: never import or execute the native parity test module.
    node = next(n for n in _original_parity_source().body if isinstance(n, ast.Assign)
                and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name)
    calls = _capture_loader(monkeypatch)
    _owned_object(ast.literal_eval(node.value), target, monkeypatch, symbol="main")
    assert len(calls) == 1 and calls[0]["lane"] == 2


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("index,message", (
    ("%index", "requires constant lane index"),
    ("-1", "lane index out of range"),
    ("4", "lane index out of range"),
    ("poison", "requires constant lane index"),
    ("undef", "requires constant lane index"),
))
def test_declared_unsupported_indices_refuse_before_lane_emission(target, index, message, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unsupported index reached lane emission")
    monkeypatch.setattr(x86, "_materialize_vector_lane_to_reg", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    with pytest.raises(BackendUnavailable, match=message):
        emit_self_asm(f'target triple = "{target}"\n' + _ir("i32", "slot", index, ("1", "3", "5", "7")))


@pytest.mark.parametrize("lane_type,result_type,message", (
    (TypeDesc("int", 32), TypeDesc("int", 64), "lane type mismatch"),
    (TypeDesc("int", 128), TypeDesc("int", 128), "requires i1..i64"),
    (TypeDesc("fp", 80), TypeDesc("fp", 80), "floating lane type not translated"),
), ids=("mismatch", "i128", "fp80"))
def test_scalar_register_contract_rejects_before_materialization(lane_type, result_type, message, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid element reached lane materialization")
    monkeypatch.setattr(x86, "_materialize_vector_lane_to_reg", forbidden)
    func = SimpleNamespace(name="invalid", value_slots={"result": SimpleNamespace(offset=8)})
    vector_type = TypeDesc("array", count=4, elem=lane_type)
    with pytest.raises(BackendUnavailable, match=message):
        x86._emit_compute_instruction(func, "extractelement", ("result", vector_type, "vector", "0", result_type))


def test_dead_result_needs_no_scalar_slot():
    func = SimpleNamespace(name="dead", value_slots={})
    element = TypeDesc("int", 32)
    assert x86._emit_compute_instruction(func, "extractelement", ("dead", TypeDesc("array", count=4, elem=element), "value", "0", element)) == []


def test_original_native_parity_nodes_and_assertions_unchanged():
    module = _original_parity_source()
    functions = {n.name for n in module.body if isinstance(n, ast.FunctionDef)}
    assert {"test_llvm_self_int_vector_lane_matches", "test_llvm_self_ptr_vector_lane_matches", "test_owned_vector_program_result"} <= functions
