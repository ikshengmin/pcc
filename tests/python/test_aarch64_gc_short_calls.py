"""Execute explicit GC ABI expansions and verify their cold return PCs."""
import os
from pathlib import Path
import platform
import struct
import subprocess
from types import SimpleNamespace

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.macho_spec import parse_object
from pcc.backend.owned_object_emit import emit_owned_object
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
    "pcc_gc_backend0_frame_roots_enabled",
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
    client = ['target triple = "' + TARGET + '"', marker,
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
    for i, state in enumerate(states):
        stop, read, initialized, backend, roots = state
        null_case = i in (5, 8)
        tagged_case = i == 6
        if not marked:
            called = 1
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
@pytest.mark.parametrize("marked", (False, True))
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
