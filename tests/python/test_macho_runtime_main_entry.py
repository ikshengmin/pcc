"""The owned Darwin executable links the real runtime entry only when needed."""
import struct
import sys
from pathlib import Path

import pytest

from pcc.backend import macho_exec, macho_obj, macho_spec
from pcc.backend.ar_writer import write_archive
from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.macho_archive import RUNTIME_MAIN_ENTRY
from pcc.backend.macho_exec import link_executable, link_prepared_executable
from pcc.backend.macho_host_prepare import prepare_executable_from_paths
from pcc.backend.native_object import NativeObject, encode_native_object
from pcc.frontends.python.owned_runtime_build import (
    _build_runtime_module,
    runtime_build_config,
    runtime_ir_passes,
)
from tests.python.test_macho_owned_output import _factory_budget


def _unit(name, call=None, *, packed=False):
    instructions = (
        f"stp x29, x30, [sp, #-16]!\nmov x29, sp\nbl {call}\nldp x29, x30, [sp], #16"
        if call else "movz w0, #0"
    )
    sections, undefined = assemble_file(
        ".section __TEXT,__text,regular,pure_instructions\n"
        f".p2align 2\n.globl {name}\n{name}:\n{instructions}\nret\n"
    )
    if packed:
        return encode_native_object(NativeObject.from_sections(sections, undefined=undefined))
    return macho_obj.emit_object(sections, undefined=undefined)


@pytest.fixture(scope="module")
def runtime_entry_archive(tmp_path_factory):
    output = tmp_path_factory.mktemp("macho_runtime_main")
    runtime_dir = str(Path(__file__).resolve().parents[2] / "pcc/runtime")
    members = []
    for name in ("freestanding_darwin_main", "freestanding_process_main"):
        data = _build_runtime_module(
            runtime_dir, name, "arm64-apple-darwin", str(output),
            runtime_ir_passes(runtime_dir), runtime_build_config(),
        )
        members.append((name + ".o", data))
    members.append(("root_scan.o", _unit("_pcc_thread_visit_conservative_words")))
    return write_archive(members)


def _entry_symbol(image):
    obj = macho_spec.parse_object(image)
    text = next(c for c in obj.commands if c.cmd == macho_spec.LC_SEGMENT_64
                and c.body["segname_str"] == "__TEXT")
    main = next(c for c in obj.commands if c.cmd == macho_spec.LC_MAIN)
    address = text.body["vmaddr"] + struct.unpack_from("<Q", main.raw, 8)[0]
    return [s["name"] for s in obj.symbols() if s["n_value"] == address]


@pytest.mark.parametrize("windowed", [False, True])
@pytest.mark.parametrize("managed", [False, True])
def test_macho_root_runtime_selects_process_entry(
    runtime_entry_archive, tmp_path, monkeypatch, managed, windowed,
):
    if sys.platform != "darwin":
        # Cross-link verifies LC_MAIN and the selected objects, not libSystem.
        monkeypatch.setattr(macho_exec, "_libsystem_exports_symbol", lambda name: name == "__tlv_bootstrap")
    main = _unit("_main", "_pcc_thread_visit_conservative_words" if managed else None, packed=windowed)
    if windowed:
        input_path = tmp_path / "main.pco"
        input_path.write_bytes(main)
        budget = _factory_budget()
        prepared = prepare_executable_from_paths(
            [str(input_path)], archives=[runtime_entry_archive], compact_budget=budget,
        )
        image = link_prepared_executable(prepared, compact_budget=budget)
    else:
        image = link_executable([main], archives=[runtime_entry_archive])
    assert (RUNTIME_MAIN_ENTRY if managed else "_main") in _entry_symbol(image)


def test_custom_macho_entry_does_not_pull_runtime_main(runtime_entry_archive):
    receipt = {}
    image = link_executable(
        [_unit("_main"), _unit("_custom", "_pcc_thread_visit_conservative_words")],
        archives=[runtime_entry_archive], entry="_custom", link_receipt=receipt,
    )
    assert "_custom" in _entry_symbol(image)
    assert [m["member"] for m in receipt["archive_members"]] == ["root_scan.o"]
