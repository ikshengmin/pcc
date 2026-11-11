"""Execute explicit GC ABI expansions and verify their cold return PCs."""
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
from types import SimpleNamespace

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.macho_spec import parse_object
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.native_object import NativeObject, encode_native_object
from pcc.backend.self_backend_aarch64_darwin import (
    _emit_prepared_aarch64_darwin_module,
    emit_aarch64_darwin_asm,
    emit_aarch64_darwin_indexed_lines,
    emit_aarch64_darwin_indexed_module,
    emit_aarch64_darwin_indexed_transport,
)
from pcc.backend.self_backend_aarch64_darwin_abi import (
    aggregate_returned_indirect,
    aggregate_returned_indirect_indexed,
)
from pcc.backend.self_backend_ir import PARSED_INSTRUCTION_KIND_CALL
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_prepare import prepare_parsed_module_for_target
from pcc.backend.target_objects import encode_assembly_object
from pcc.ir import ir
from pcc.backend.precise_stackmap import (
    ARCH_AARCH64,
    SAFEPOINT_CALL,
    SAFEPOINT_EXCEPTION,
    decode_stack_map,
    function_id,
)


TARGET = "arm64-apple-darwin"
HELPERS = (
    "pcc_gc_load_ptr", "pcc_gc_load_borrowed_ptr",
    "pcc_gc_frame_enter", "pcc_gc_frame_leave",
    "pcc_gc_frame_enter_lifo", "pcc_gc_frame_leave_lifo",
    "pcc_gc_frame_enter_mapped", "pcc_gc_frame_leave_mapped",
    "pcc_gc_frame_enter_lifo_mapped", "pcc_gc_frame_leave_lifo_mapped",
    "pcc_gc_note_slot_write_barrier", "pcc_gc_note_write_barrier",
)
FLAGS = (
    "pcc_thread_stop_requested", "pcc_gc_read_barrier_enabled",
    "pcc_gc_config_initialized", "pcc_gc_backend_selected",
    "pcc_gc_backend0_frame_roots_enabled", "pcc_gc_frame_registry_inactive",
)


def test_gc_call_admission_rejects_foreign_definitions_and_wrong_abis():
    from pcc.backend.self_backend_aarch64_gc_calls import GC_ABI_MARKER, gc_short_call_lines
    from pcc.backend.self_backend_kernel import TYPE_KIND_INT, TYPE_KIND_PTR, TYPE_KIND_VOID
    from pcc.backend.self_backend_module_symbols import PreparedModuleSymbols

    def symbols(defined=(), marked=True, target=TARGET):
        marker = {GC_ABI_MARKER} if marked else set()
        return PreparedModuleSymbols("module_", frozenset(marker | set(defined)),
                                     frozenset(marker), frozenset(), target)
    function = SimpleNamespace(name="probe")
    for module, result, arguments in (
        (symbols(marked=False), TYPE_KIND_PTR, (TYPE_KIND_PTR, TYPE_KIND_PTR)),
        (symbols(defined=("pcc_gc_load_ptr",)), TYPE_KIND_PTR, (TYPE_KIND_PTR, TYPE_KIND_PTR)),
        (symbols(target="aarch64-unknown-linux-gnu"), TYPE_KIND_PTR, (TYPE_KIND_PTR, TYPE_KIND_PTR)),
        (symbols(), TYPE_KIND_VOID, (TYPE_KIND_PTR, TYPE_KIND_PTR)),
        (symbols(), TYPE_KIND_PTR, (TYPE_KIND_PTR,)),
        (symbols(), TYPE_KIND_PTR, (TYPE_KIND_PTR, TYPE_KIND_INT)),
    ):
        assert gc_short_call_lines(function, "pcc_gc_load_ptr", 0, result, arguments, module) is None
    labels = []
    for name in ("a.b", "adotb"):
        lines = gc_short_call_lines(SimpleNamespace(name=name), "pcc_gc_load_ptr", 0,
                                    TYPE_KIND_PTR, (TYPE_KIND_PTR, TYPE_KIND_PTR), symbols())
        labels.append({line for line in lines if line.endswith(":")})
    assert labels[0] and labels[1] and labels[0].isdisjoint(labels[1])


def _out_of_order_gc_module(monkeypatch, *, direct, fuse_uses):
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1" if direct else "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1" if direct else "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_FUSE_USES", "1" if fuse_uses else "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK", "1")
    module = ir.Module(name="gc-label-order")
    module.triple = TARGET
    byte, bit, void = ir.IntType(8), ir.IntType(1), ir.VoidType()
    pointer = byte.as_pointer()
    marker = ir.GlobalVariable(module, byte, "__pcc_owned_gc_short_paths_v1")
    marker.linkage = "internal"
    marker.global_constant = True
    marker.initializer = ir.Constant(byte, 1)
    barrier = ir.Function(
        module, ir.FunctionType(void, [pointer, pointer]), "pcc_gc_note_write_barrier",
    )
    for name in ("first", "second"):
        function = ir.Function(module, ir.FunctionType(void, [bit, pointer]), name)
        function.args[0].name = "condition"
        function.args[1].name = "object"
        entry, left, right, dead = [
            function.append_basic_block(label) for label in ("entry", "left", "right", "dead")
        ]
        # Allocate a discarded payload first, then construct live calls in
        # reverse block order. Repeated void calls have no distinct SSA names.
        for block in (dead, right, left, entry):
            builder = ir.IRBuilder(block)
            for _ in range(1 if block is dead else 2):
                builder.call(barrier, [function.args[1], function.args[1]])
            if block is entry:
                builder.cbranch(function.args[0], left, right)
            else:
                builder.ret_void()
    return module


def _live_gc_call_ids(function):
    kernel = get_indexed_function_kernel(function)
    assert kernel.block_names == ["entry", "left", "right"]
    result = []
    for block_id in range(len(kernel.block_names)):
        start = kernel.block_fact(block_id).first
        for index in range(kernel.instruction_count(block_id)):
            if kernel.instruction_kind_id(block_id, index) == PARSED_INSTRUCTION_KIND_CALL:
                result.append(kernel.instruction_payload_id_by_id(start + index))
    return result


@pytest.mark.parametrize("optimize", (False, True))
@pytest.mark.parametrize("fuse_uses", (False, True))
def test_gc_labels_follow_emission_not_call_construction(monkeypatch, optimize, fuse_uses):
    text = str(_out_of_order_gc_module(monkeypatch, direct=False, fuse_uses=fuse_uses))
    source = _out_of_order_gc_module(monkeypatch, direct=True, fuse_uses=fuse_uses)
    direct = source.direct_indexed_module()
    parsed = parse_self_backend_module(text)
    assert source._direct_indexed_fallback_records == 0
    for function in source.functions:
        assert all(record._direct_record_id >= 0 and not record.text
                   for block in function.blocks for record in block._instrs)
    for actual, expected in zip(direct.functions, parsed.functions, strict=True):
        assert _live_gc_call_ids(actual) == [5, 6, 3, 4, 1, 2]
        assert _live_gc_call_ids(expected) == list(range(6))

    assembly = emit_aarch64_darwin_asm(text, optimize=optimize)
    # With optimization, the last void call in each return block remains a
    # sibling transfer and never admits a GC expansion or reserves a serial.
    expected_expansions = 4 if optimize else 6
    assert assembly.count("  b _pcc_gc_note_write_barrier") == (4 if optimize else 0)
    # Public emitters consume their kernel tables. The existing retained-table
    # entry lets us check repeated preparation of the same module, without
    # disabling cleanup or resetting the serial in the test.
    try:
        for _ in range(2):
            prepared = prepare_parsed_module_for_target(
                direct,
                aggregate_returned_indirect=aggregate_returned_indirect,
                aggregate_returned_indirect_indexed=aggregate_returned_indirect_indexed,
                materialize_legacy_slots=False,
                source_debug=True,
            )
            assert [function.platform_intrinsic_labels for function in direct.functions] == [0, 0]
            assert assembly == _emit_prepared_aarch64_darwin_module(
                prepared, optimize=optimize, close_native_tables=False,
            )
            assert [function.platform_intrinsic_labels for function in direct.functions] == [expected_expansions] * 2
            assert [function.aarch64_tail_call_ids for function in direct.functions] == [[4, 2] if optimize else []] * 2
            assert [_live_gc_call_ids(function) for function in direct.functions] == [[5, 6, 3, 4, 1, 2]] * 2
        assert assembly == emit_aarch64_darwin_indexed_module(direct, optimize=optimize)
    finally:
        for function in direct.functions:
            get_indexed_function_kernel(function).close_native_tables()
    assert assembly == emit_aarch64_darwin_indexed_module(parsed, optimize=optimize)

    def fresh_module():
        return _out_of_order_gc_module(monkeypatch, direct=True, fuse_uses=fuse_uses).direct_indexed_module()

    assert assembly == emit_aarch64_darwin_asm(text, optimize=optimize)
    assert assembly == "\n".join(emit_aarch64_darwin_indexed_lines(fresh_module(), optimize=optimize)) + "\n"
    fresh = _out_of_order_gc_module(monkeypatch, direct=True, fuse_uses=fuse_uses).direct_indexed_module()
    assert assembly == emit_aarch64_darwin_indexed_module(fresh, optimize=optimize)
    definitions = re.findall(r"^(L[^:\n]+):$", assembly, re.M)
    assert len(definitions) == len(set(definitions))
    expected_labels = {
        "L_" + name + ".gc.short." + str(serial) + "." + suffix
        for name in ("first", "second") for serial in range(1, expected_expansions + 1)
        for suffix in ("slow", "done")
    }
    assert {label for label in definitions if ".gc.short." in label} == expected_labels
    references = re.findall(r"^  (?:b(?:\.[a-z]+)?|cbn?z|tbn?z) [^\n]*?(L\S+)$", assembly, re.M)
    assert expected_labels <= set(references)
    assert all(definitions.count(label) == 1 for label in references)
    expected_object = encode_assembly_object(assembly, TARGET)
    sections, undefined = assemble_file(assembly)
    expected_macho = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    for structured in (False, True):
        transport = emit_aarch64_darwin_indexed_transport(
            fresh_module(), optimize=optimize, structured_instructions=structured,
        )
        try:
            sections, undefined = transport.assemble_sections()
            actual_object = NativeObject.from_sections(sections, undefined=undefined)
            assert encode_native_object(actual_object) == expected_object
            assert actual_object.to_macho() == expected_macho
        finally:
            if transport.encoded_line_records is not None:
                transport.encoded_line_records.close()
    assert [function.platform_intrinsic_labels for function in direct.functions] == [expected_expansions] * 2


@pytest.mark.parametrize("rejection", ("signature", "definition", "exclusion", "walked"))
def test_declined_gc_calls_do_not_reserve_label_serials(rejection):
    marker = "@__pcc_owned_gc_short_paths_v1 = internal constant i8 1\n"
    declaration = "declare void @pcc_gc_load_ptr(ptr, ptr)\n"
    invocation = "call void @pcc_gc_load_ptr(ptr null, ptr null)"
    if rejection == "definition":
        declaration = "define ptr @pcc_gc_load_ptr(ptr %owner, ptr %slot) {\nentry:\n  ret ptr %slot\n}\n"
        invocation = "%loaded = call ptr @pcc_gc_load_ptr(ptr null, ptr null)"
    elif rejection == "exclusion":
        marker += "@__pcc_owned_gc_short_paths_v1.exclude.pcc_gc_load_ptr = internal constant i8 1\n"
        declaration = "declare ptr @pcc_gc_load_ptr(ptr, ptr)\n"
        invocation = "%loaded = call ptr @pcc_gc_load_ptr(ptr null, ptr null)"
    elif rejection == "walked":
        marker += "@__pcc_owned_gc_walked_frames_v1 = internal constant i8 1\n"
        declaration = "declare void @pcc_gc_frame_leave_mapped(ptr)\n"
        invocation = "call void @pcc_gc_frame_leave_mapped(ptr null)"
    text = ('target triple = "' + TARGET + '"\n' + marker + declaration
            + "declare void @pcc_gc_note_write_barrier(ptr, ptr)\n"
            + "define void @probe(ptr %object) {\nentry:\n  " + invocation
            + "\n  call void @pcc_gc_note_write_barrier(ptr %object, ptr %object)\n  ret void\n}\n")
    module = parse_self_backend_module(text)
    assembly = emit_aarch64_darwin_indexed_module(module, optimize=False)
    probe = next(function for function in module.functions if function.name == "probe")
    assert probe.platform_intrinsic_labels == 1
    assert set(re.findall(r"L_probe\.gc\.short\.(\d+)\.", assembly)) == {"1"}
    assert ("  bl _pcc_gc_load_ptr" in assembly) == (rejection != "walked")
    assert "  bl _pcc_gc_frame_leave_mapped" not in assembly


def test_linux_declined_gc_calls_preserve_aggregate_vararg_serials():
    text = '''target triple = "aarch64-unknown-linux-gnu"
@__pcc_owned_gc_short_paths_v1 = internal constant i8 1
declare void @pcc_gc_note_write_barrier(ptr, ptr)
define void @probe(ptr %ap, ptr %object, ...) {
entry:
  call void @pcc_gc_note_write_barrier(ptr %object, ptr %object)
  %first = va_arg ptr %ap, {i64, i64}
  call void @pcc_gc_note_write_barrier(ptr %object, ptr %object)
  %second = va_arg ptr %ap, {double, double}
  call void @pcc_gc_note_write_barrier(ptr %object, ptr %object)
  ret void
}
'''
    module = parse_self_backend_module(text)
    assembly = emit_aarch64_darwin_indexed_module(module, optimize=False)
    assert module.functions[0].platform_intrinsic_labels == 2
    assert "L_vaagg_probe_first_1_stack:" in assembly
    assert "L_vaagg_probe_second_2_stack:" in assembly
    assert ".gc.short." not in assembly
    assert assembly.count("  bl pcc_gc_note_write_barrier") == 3
    assert assembly == emit_aarch64_darwin_indexed_module(parse_self_backend_module(text), optimize=False)
    assert assembly == emit_aarch64_darwin_asm(text, optimize=False)


def _fixture(name, marked):
    load = name in HELPERS[:2]
    frame = "frame_" in name
    peer = name.replace("frame_enter", "frame_leave") if "frame_enter" in name else name.replace("frame_leave", "frame_enter")
    helpers = list(dict.fromkeys([name] + ([peer] if frame else []) +
                                ["pcc_gc_frame_enter_lifo", "pcc_gc_frame_leave_lifo"]))
    def signature(helper):
        count = 1 if "frame_leave" in helper else 3 if "slot_write_barrier" in helper else 2
        return ("ptr" if helper in HELPERS[:2] else "void"), count
    result, count = signature(name)
    marker = "@__pcc_owned_gc_short_paths_v1 = internal constant i8 1" if marked else ""
    # Program code whose executable publishes the stack-map table.
    walked = ("@__pcc_owned_gc_walked_frames_v1 = internal constant i8 1"
              if marked == "walked" else "")
    client = ['target triple = "' + TARGET + '"', marker, walked,
              "@frame_map = internal constant i32 1"]
    for helper in helpers:
        ret, argc = signature(helper)
        client.append("declare " + ret + " @" + helper + "(" + ", ".join(["ptr"] * argc) + ")")
    client.extend(["declare void @configure(i32)", "declare i64 @call_count()", "declare void @touch()",
                   "define i32 @main() {", "entry:", "  %slot = alloca ptr", "  %keep = alloca ptr",
                   "  store ptr null, ptr %keep", "  store ptr inttoptr (i64 17 to ptr), ptr %slot",
                   "  call void @pcc_gc_frame_enter_lifo(ptr @frame_map, ptr %keep)"])
    states = [(0, 0, 1, 0, 0), (0, 1, 1, 1, 1), (1, 0, 1, 0, 0),
              (0, 0, 0, 0, 0), (0, 1, 1, 4, 1), (0, 1, 1, 4, 1),
              (0, 1, 1, 4, 1), (0, 0, 1, 0, 1), (1, 1, 1, 4, 1)]
    # The runtime publishes the frame-registry word with its three inputs.
    states = [state + (int(state[2] == 1 and state[3] == 0 and state[4] == 0),)
              for state in states]
    for i, state in enumerate(states):
        stop, read, initialized, backend, roots, _inactive = state
        null_case = i in (5, 8)
        tagged_case = i == 6
        if not marked:
            called = 1
        elif marked == "walked" and frame and name.endswith("_mapped"):
            # The stack walk roots mapped frames on every collector, so marked
            # program code emits no code for their entries and leaves.
            called = 0
        elif load:
            called = int(bool(stop or (read and not null_case)))
        elif frame:
            called = int(bool(stop or not initialized or backend or roots))
        else:
            called = int(not null_case and not tagged_case and (not initialized or backend != 0))
        client.append("  call void @configure(i32 " + str(i) + ")")
        if frame:
            enter = name if "frame_enter" in name else peer
            leave = peer if "frame_enter" in name else name
            client.extend(["  call void @" + enter + "(ptr @frame_map, ptr %slot)",
                           "  call void @touch()",
                           "  call void @" + leave + "(ptr %slot)"])
            called *= 2
        else:
            args = ["ptr %slot"] * count
            if load:
                args[0] = "ptr null"
                if null_case:
                    args[1] = "ptr null"
            else:
                args[-1] = ("ptr null" if null_case else "ptr inttoptr (i64 17 to ptr)"
                            if tagged_case else "ptr inttoptr (i64 18 to ptr)")
            client.append(("  %value" + str(i) + " = " if load else "  ") +
                          "call " + result + " @" + name + "(" + ", ".join(args) + ")")
        client.extend(["  %count" + str(i) + " = call i64 @call_count()",
                       "  %count_ok" + str(i) + " = icmp eq i64 %count" + str(i) + ", " + str(called)])
        if load:
            client.extend(["  %value_ok" + str(i) + " = icmp eq ptr %value" + str(i) +
                           (", null" if null_case else ", inttoptr (i64 17 to ptr)"),
                           "  %ok" + str(i) + " = and i1 %count_ok" + str(i) + ", %value_ok" + str(i)])
        else:
            client.append("  %ok" + str(i) + " = or i1 %count_ok" + str(i) + ", false")
        client.extend(["  br i1 %ok" + str(i) + ", label %next" + str(i) + ", label %bad" + str(i),
                       "bad" + str(i) + ":", "  call void @pcc_gc_frame_leave_lifo(ptr %keep)",
                       "  ret i32 " + str(10 + i), "next" + str(i) + ":"])
    client.extend(["  call void @pcc_gc_frame_leave_lifo(ptr %keep)", "  ret i32 0", "}"])
    support = ['target triple = "' + TARGET + '"', "@calls = internal global i64 0"]
    support.extend("@" + flag + " = global i32 0" for flag in FLAGS)
    support.extend(["define void @configure(i32 %case) {", "entry:", "  store i64 0, ptr @calls"])
    for k, flag in enumerate(FLAGS):
        value = str(states[-1][k])
        for i in reversed(range(len(states) - 1)):
            cond = "%case_" + str(k) + "_" + str(i)
            selected = "%selected_" + str(k) + "_" + str(i)
            support.extend(["  " + cond + " = icmp eq i32 %case, " + str(i),
                            "  " + selected + " = select i1 " + cond + ", i32 " + str(states[i][k]) + ", i32 " + value])
            value = selected
        support.append("  store i32 " + value + ", ptr @" + flag)
    support.extend(["  ret void", "}", "define i64 @call_count() {", "entry:",
                    "  %n = load i64, ptr @calls", "  ret i64 %n", "}",
                    "define void @touch() {", "entry:", "  ret void", "}"])
    for helper in helpers:
        ret, argc = signature(helper)
        support.extend(["define " + ret + " @" + helper + "(" + ", ".join("ptr %a" + str(i) for i in range(argc)) + ") {",
                        "entry:", "  %n = load i64, ptr @calls", "  %next = add i64 %n, 1", "  store i64 %next, ptr @calls"])
        if helper in HELPERS[:2]:
            support.extend(["  %null = icmp eq ptr %a1, null", "  br i1 %null, label %empty, label %read",
                            "empty:", "  ret ptr null", "read:", "  %value = load ptr, ptr %a1", "  ret ptr %value"])
        else:
            support.append("  ret void")
        support.append("}")
    return "\n".join(client) + "\n", "\n".join(support) + "\n"


def _check_return_pcs(data):
    obj = parse_object(data)
    sections = obj.sections()
    text = next(section for section in sections if section["sectname_str"] == "__text")
    table = next(section for section in sections if section["sectname_str"] == "__pcc_stackmaps")
    starts = {function_id(symbol["name"]): symbol["n_value"] - text["addr"]
              for symbol in obj.symbols() if symbol["n_sect"] == sections.index(text) + 1}
    decoded = decode_stack_map(data[table["offset"]:table["offset"] + table["size"]], expected_arch=ARCH_AARCH64)
    checked = 0
    return_pcs = set()
    for function in decoded.functions:
        assert function.function_id in starts
        for record in function.records:
            if record.kind in (SAFEPOINT_CALL, SAFEPOINT_EXCEPTION):
                checked += 1
                return_pcs.add(starts[function.function_id] + record.instruction_offset)
                offset = text["offset"] + starts[function.function_id] + record.instruction_offset - 4
                word = struct.unpack_from("<I", data, offset)[0]
                assert word & 0xFC000000 == 0x94000000 or word & 0xFFFFFC1F == 0xD63F0000
    assert checked
    # Frame operations are recognized separately by root planning. Every
    # other emitted GC call with our active keeper must retain its cold PC;
    # unrelated configure/call_count records cannot hide a missing GC record.
    symbols = obj.symbols()
    for relocation in obj.relocations(text):
        if relocation["r_extern"] and relocation["r_type"] == 2:
            target = symbols[relocation["r_symbolnum"]]["name"]
            if target in ("_pcc_gc_load_ptr", "_pcc_gc_load_borrowed_ptr",
                          "_pcc_gc_note_slot_write_barrier", "_pcc_gc_note_write_barrier"):
                assert relocation["r_address"] + 4 in return_pcs, target


@pytest.mark.parametrize("name", HELPERS)
@pytest.mark.parametrize("marked", (False, True, "walked"))
def test_gc_short_paths_execute_fast_and_cold_states(tmp_path, name, marked):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        pytest.skip("native execution targets Darwin AArch64")
    client, support = _fixture(name, marked)
    data = emit_owned_object(client, TARGET)
    _check_return_pcs(data)
    binary = tmp_path / "gc_short_paths"
    binary.write_bytes(link_executable([data, emit_owned_object(support, TARGET)]))
    binary.chmod(0o755)
    run = subprocess.run([str(binary)], capture_output=True, timeout=10,
                         env=dict(os.environ, PATH="/nonexistent"))
    assert (run.returncode, run.stdout, run.stderr) == (0, b"", b""), (name, marked)
