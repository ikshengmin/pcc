"""Linux AArch64 workers keep final records through the real ELF boundary."""

from dataclasses import replace
import json
import os
import subprocess
import sys

import pytest

from pcc.backend import arm64_asm_driver, arm64_elf_driver, target_objects
from pcc.backend.elf_x86_64 import (
    EM_AARCH64,
    ElfError,
    SHF_TLS,
    STB_LOCAL,
    STT_TLS,
    emit_relocatable,
    parse_relocatable,
)
from pcc.backend.precise_stackmap import ARCH_AARCH64, decode_stack_map
from pcc.backend.self_backend_aarch64_darwin import (
    emit_aarch64_darwin_indexed_transport,
)
from pcc.backend.self_backend_parse import parse_self_backend_module


TARGET = "aarch64-unknown-linux-gnu"

_GLOBALS_IR = '''target triple = "aarch64-unknown-linux-gnu"
@own_tls = thread_local global i64 37, align 8
@external_tls = external thread_local global i64, align 8
@values = global [2 x i64] [i64 11, i64 29], align 8
@second = global ptr getelementptr ([2 x i64], ptr @values, i64 0, i64 1)
@previous = global ptr getelementptr (i8, ptr @values, i64 -8)
@llvm.global_ctors = appending global [1 x { i32, ptr, ptr }] [{ i32, ptr, ptr } { i32 101, ptr @initialize, ptr null }]
@llvm.global_dtors = appending global [1 x { i32, ptr, ptr }] [{ i32, ptr, ptr } { i32 102, ptr @finalize, ptr null }]
declare i64 @external_call(i64)
define internal void @initialize() {
entry:
  ret void
}
define internal void @finalize() {
entry:
  ret void
}
define i64 @read_tls() {
entry:
  %own = load i64, ptr @own_tls, align 8
  %external = load i64, ptr @external_tls, align 8
  %sum = add i64 %own, %external
  %result = call i64 @external_call(i64 %sum)
  ret i64 %result
}
'''

_ROOTS_IR = '''target triple = "aarch64-unknown-linux-gnu"
@frame_map = internal constant i32 1
declare void @pcc_gc_frame_enter(ptr, ptr)
declare void @pcc_gc_frame_leave(ptr)
declare void @pcc_thread_safepoint()
declare void @opaque_call(ptr)
define void @refresh(ptr %obj) {
entry:
  %root = alloca ptr, align 8
  store ptr %obj, ptr %root, align 8
  call void @pcc_gc_frame_enter(ptr @frame_map, ptr %root)
  %before = load ptr, ptr %root, align 8
  %derived = getelementptr i8, ptr %before, i64 24
  call void @pcc_thread_safepoint()
  call void @opaque_call(ptr %derived)
  call void @pcc_gc_frame_leave(ptr %root)
  ret void
}
'''


def _forbid_text_assembly(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("finalized Linux transport used a text assembler")

    for owner, name in (
        (target_objects, "emit_indexed_assembly"),
        (target_objects, "encode_assembly_object"),
        (arm64_elf_driver, "assemble_file"),
        (arm64_elf_driver, "assemble_sections"),
        (arm64_asm_driver, "assemble_file"),
        (arm64_asm_driver, "assemble_lines"),
    ):
        monkeypatch.setattr(owner, name, forbidden)


@pytest.mark.parametrize("source", [_GLOBALS_IR, _ROOTS_IR], ids=["tls-relocations", "managed-roots"])
def test_linux_transport_matches_same_ir_text_object(monkeypatch, source):
    expected = target_objects.encode_assembly_object(
        target_objects.emit_indexed_assembly(parse_self_backend_module(source), optimize=False),
        TARGET,
    )
    _forbid_text_assembly(monkeypatch)
    transport = emit_aarch64_darwin_indexed_transport(
        parse_self_backend_module(source), optimize=False, structured_instructions=True,
    )
    assert transport.native_finalized
    assert transport.line_chunks == [] and transport.encoded_line_records is None
    assert transport.fallback_instruction_count == 0
    assert transport.fallback_instruction_lines == ()
    sections, undefined = transport.assemble_sections()
    encoded = emit_relocatable(arm64_elf_driver.from_sections(sections, undefined))
    assert encoded[:4] == b"\x7fELF"
    assert encoded == expected
    obj = parse_relocatable(encoded)
    assert obj.machine == EM_AARCH64
    assert obj == parse_relocatable(expected)
    assert not any("compact_unwind" in s.name or "tls_refs" in s.name for s in obj.sections)
    stackmap = next(s for s in obj.sections if s.name == ".pcc_stackmaps")
    decoded = decode_stack_map(stackmap.data, expected_arch=ARCH_AARCH64)
    assert decoded.functions
    assert stackmap.relocations and all(r.type == 257 for r in stackmap.relocations)
    if source == _GLOBALS_IR:
        symbols = {s.name: s for s in obj.symbols}
        assert symbols["own_tls"].type == symbols["external_tls"].type == STT_TLS
        assert symbols["own_tls"].section_index != 0
        assert symbols["external_tls"].section_index == 0
        assert symbols["external_call"].section_index == 0
        assert symbols["read_tls"].section_index != 0
        assert "_read_tls" not in symbols
        assert any(s.name.endswith("initialize") and s.binding == STB_LOCAL for s in obj.symbols)
        assert any(s.name == ".tdata" and s.flags & SHF_TLS for s in obj.sections)
        assert {".init_array.0000000101", ".fini_array.0000000102"} <= {s.name for s in obj.sections}
        kinds = {r.type for s in obj.sections for r in s.relocations}
        assert {257, 283, 541, 542} <= kinds
        assert any(r.type == 257 and r.addend == 8 for s in obj.sections for r in s.relocations)
        assert any(r.type == 257 and r.addend == -8 for s in obj.sections for r in s.relocations)
    else:
        assert any(record.locations for function in decoded.functions for record in function.records)


def test_linux_transport_preserves_mixed_aggregate_varargs(monkeypatch):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator, postprocess_ir_text
    from pcc.frontends.c.parse.c_parser import CParser

    generator = CCodeGenerator()
    generator.module.triple = TARGET
    generator.generate_code(CParser().parse('''
        struct pair { double real; long long integer; };
        extern long long take(int marker, ...);
        long long caller(void) {
            struct pair value = {1.5, 29};
            return take(0, value, 41LL, 61.5);
        }
    '''))
    source = postprocess_ir_text(str(generator.module))
    expected = target_objects.encode_assembly_object(
        target_objects.emit_indexed_assembly(parse_self_backend_module(source), optimize=False),
        TARGET,
    )
    _forbid_text_assembly(monkeypatch)
    transport = emit_aarch64_darwin_indexed_transport(
        parse_self_backend_module(source), optimize=False, structured_instructions=True,
    )
    assert transport.native_finalized and transport.fallback_instruction_count == 0
    sections, undefined = transport.assemble_sections()
    actual = emit_relocatable(arm64_elf_driver.from_sections(sections, undefined))
    assert actual == expected
    obj = parse_relocatable(actual)
    take = next(index for index, symbol in enumerate(obj.symbols) if symbol.name == "take")
    assert any(r.type == 283 and r.symbol_index == take for s in obj.sections for r in s.relocations)


@pytest.mark.parametrize("shape", ["section", "relocation", "subtractor", "section-reference"])
def test_linux_transport_rejects_unproven_elf_shapes(shape):
    transport = emit_aarch64_darwin_indexed_transport(
        parse_self_backend_module(_GLOBALS_IR), optimize=False, structured_instructions=True,
    )
    sections, undefined = transport.assemble_sections()
    if shape == "section":
        sections[0] = replace(sections[0], segname="__UNSUPPORTED")
        message = "unsupported AArch64 ELF section"
    else:
        index = next(i for i, section in enumerate(sections) if section.relocations)
        section = sections[index]
        relocation = section.relocations[0]
        if shape == "relocation":
            relocation = replace(relocation, type=255)
            message = "unsupported AArch64 transport relocation"
        elif shape == "subtractor":
            relocation = replace(relocation, minuend="other")
            message = "named, non-subtractor relocations"
        else:
            relocation = replace(relocation, section=("__TEXT", "__text"))
            message = "named, non-subtractor relocations"
        sections[index] = replace(section, relocations=(relocation,) + tuple(section.relocations[1:]))
    with pytest.raises(ElfError, match=message):
        emit_relocatable(arm64_elf_driver.from_sections(sections, undefined))


_WORKER = r'''
import json
import sys
from pcc.backend import arm64_asm_driver, arm64_elf_driver, target_objects
from pcc.backend import self_backend_aarch64_darwin as emitter
from pcc.frontends.python import pipeline
from pcc.frontends.python.codegen.layer1 import L1CodeGen

manifest, target, mode, receipt_path = sys.argv[1:]
original_generate = L1CodeGen.generate
original_transport = emitter.emit_aarch64_darwin_indexed_transport
original_assembly = target_objects.emit_indexed_assembly
observed = {"transport": [], "assembly": 0}

def generate(self, module=None):
    self._target_triple = target
    return original_generate(self, module)

def transport(*args, **kwargs):
    result = original_transport(*args, **kwargs)
    observed["transport"].append([kwargs.get("structured_instructions"), result.native_finalized])
    return result

def assembly(*args, **kwargs):
    observed["assembly"] += 1
    return original_assembly(*args, **kwargs)

def forbidden(*args, **kwargs):
    raise AssertionError("Linux direct object worker reached module text assembly")

L1CodeGen.generate = generate
emitter.emit_aarch64_darwin_indexed_transport = transport
target_objects.emit_indexed_assembly = assembly
if target == "aarch64-unknown-linux-gnu" and mode == "direct":
    target_objects.emit_indexed_assembly = forbidden
    target_objects.encode_assembly_object = forbidden
    arm64_elf_driver.assemble_file = forbidden
    arm64_elf_driver.assemble_sections = forbidden
    arm64_asm_driver.assemble_file = forbidden
    arm64_asm_driver.assemble_lines = forbidden
status = pipeline.run_python_multi_codegen_worker(manifest)
with open(receipt_path, "w", encoding="utf-8") as stream:
    json.dump(observed, stream)
raise SystemExit(status)
'''


@pytest.mark.integration
@pytest.mark.parametrize("target,mode", [
    (TARGET, "direct"),
    (TARGET, "validate"),
    (TARGET, "text-control"),
    (TARGET, "assembly"),
    (TARGET, "sidecar"),
    ("arm64-apple-darwin", "direct"),
    ("x86_64-unknown-linux-gnu", "direct"),
    ("x86_64-pc-windows-msvc", "direct"),
])
def test_worker_uses_structured_linux_elf_and_preserves_other_routes(tmp_path, target, mode):
    from pcc.frontends.python import pipeline

    source = tmp_path / "probe.py"
    source.write_text("def add(a: int, b: int) -> int:\n    return a + b\nprint(add(20, 22))\n")
    manifest = tmp_path / "worker.manifest"
    result = tmp_path / "result.tsv"
    receipt = tmp_path / "route.json"
    pipeline._write_python_frontend_worker_manifest(
        str(manifest), str(result), str(tmp_path), "", "", [str(source)], ["probe"], [0],
        entry_module="probe", sibling_inits=(), libpython_mode="off",
        ir_scaffold_mode="on", verbose=False,
    )
    env = {name: value for name, value in os.environ.items() if not name.startswith("PCC_")}
    env.pop("LC_ALL", None)
    env.update({
        "PCC_BACKEND": "self",
        "PCC_HOST_CODEGEN_BATCH": "1",
        "PCC_HOST_INDEXED_PROCESS_SPLIT": "0",
        "PCC_DIRECT_INDEXED_KERNEL_CAPTURE": "1",
        "PCC_DIRECT_INDEXED_KERNEL_EMIT": "1",
        "PCC_DIRECT_INDEXED_KERNEL_FUSE_USES": "1",
        "PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK": "1",
        "PCC_DIRECT_INDEXED_KERNEL_RELEASE_FRONTEND": "1",
        "PCC_DIRECT_INDEXED_NATIVE_OBJECT": "0" if mode == "assembly" else "1",
        "PCC_DIRECT_INDEXED_KERNEL_VALIDATE": "1" if mode == "validate" else "0",
        "PCC_TEXT_INDEXED_KERNEL_EMIT": "1" if mode == "text-control" else "0",
        "PCC_DIRECT_INDEXED_SIDECAR": "1" if mode == "sidecar" else "0",
        "PCC_PYTHON_IR_PASSES": "mem2reg,sroa",
    })
    completed = subprocess.run(
        [sys.executable, "-B", "-c", _WORKER, str(manifest), target, mode, str(receipt)],
        env=env, capture_output=True, text=True, timeout=120,
    )
    detail = result.read_text() if result.exists() else "missing worker result"
    assert completed.returncode == 0, completed.stderr + "\n" + detail
    observed = json.loads(receipt.read_text())
    structured = mode == "direct" and target in (TARGET, "arm64-apple-darwin")
    assert observed["transport"] == ([[True, True]] if structured else [])
    assert observed["assembly"] == (0 if structured or mode == "sidecar" else 1)
    if mode == "sidecar":
        from pcc.backend.self_backend_indexed_codec import decode_indexed_module_file

        module = decode_indexed_module_file(str(tmp_path / "module_0.direct.pidx"))
        assert module.triple == TARGET
        assert not (tmp_path / "module_0.direct.pco").exists()
    elif mode == "assembly":
        assert (tmp_path / "module_0.direct.s").read_text()
        assert not (tmp_path / "module_0.direct.pco").exists()
    else:
        payload = (tmp_path / "module_0.direct.pco").read_bytes()
        if target == TARGET:
            obj = parse_relocatable(payload)
            assert obj.machine == EM_AARCH64
            assert any(s.name == ".pcc_stackmaps" and s.data for s in obj.sections)
            source_ir = (tmp_path / "module_0.ll").read_text()
            expected = target_objects.encode_assembly_object(
                target_objects.emit_indexed_assembly(parse_self_backend_module(source_ir), optimize=False),
                target,
            )
            assert payload == expected
        elif target == "arm64-apple-darwin":
            from pcc.backend.native_object import decode_packed_native_object

            assert decode_packed_native_object(payload)
        elif "windows" in target:
            from pcc.backend.coff_x86_64 import parse_object

            assert parse_object(payload).sections
        else:
            assert parse_relocatable(payload).machine == 62
