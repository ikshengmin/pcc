"""Function identity: C++17 [dcl.fct.spec]/[basic.def.odr], Itanium ABI §5.2.1.

https://itanium-cxx-abi.github.io/cxx-abi/abi.html#vague-linkage
https://llvm.org/docs/LangRef.html#linkage-types

These are the shared protocol's bounded weak-definition tests, not complete
C++ inline/static-local/COMDAT or native-pcc1 qualification.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import elf_x86_64 as elf
from pcc.backend.definition_linkage import supports_external_odr_function_definitions
from pcc.backend.self_backend_indexed_codec import (
    decode_indexed_module_file,
    encode_indexed_module_file,
)
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_x86_64_linux import emit_x86_64_linux_asm
from pcc.backend.x86_64_asm_driver import assemble_file
from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
from pcc.frontends.c.parse import make_c_parser
from pcc.ir.direct_indexed_kernel import build_direct_indexed_module


TARGET = "x86_64-unknown-linux-gnu"


def _ir(linkage="weak_odr", name="f", value=7):
    return f'''target triple = "{TARGET}"
define {linkage} i32 @{name}() {{
entry:
 ret i32 {value}
}}
'''


def _module(source, marker="external_odr", target=TARGET):
    ast = make_c_parser().parse(source)
    for node in ast.ext:
        if type(node).__name__ == "FuncDef" and node.decl.name == "f":
            node.decl.definition_linkage = marker
    generator = CCodeGenerator()
    generator.set_target_text(target, "")
    generator.generate_code(ast)
    return generator.module


def _encode(path, module):
    for function in module.functions:
        get_indexed_function_kernel(function)
    encode_indexed_module_file(str(path), module)
    data = path.read_bytes()
    magic, length, rest = data.split(b"\n", 2)
    return magic, json.loads(rest[:int(length)]), rest[int(length):]


@pytest.mark.parametrize("prototype", ["", "int f(void); "])
def test_typed_ast_marker_reaches_text_direct_and_indexed(tmp_path, prototype):
    module = _module(prototype + "int f(void) { return 7; }")
    assert module.globals["f"].linkage == "weak_odr"
    text = parse_self_backend_module(str(module))
    direct = build_direct_indexed_module(module)
    for index, parsed in enumerate((text, direct)):
        assert parsed.functions[0].definition_linkage == "external_odr"
        path = tmp_path / f"module{index}.idx"
        _, header, _ = _encode(path, parsed)
        assert header["schema"] == "pcc.self-backend.indexed-module.v3"
        assert header["functions"][0]["definition_linkage"] == "external_odr"
        restored = decode_indexed_module_file(str(path))
        assert restored.functions[0].definition_linkage == "external_odr"


@pytest.mark.parametrize("linkage", ["", "internal", "external"])
def test_ordinary_wire_omits_marker_and_legacy_module_remains_readable(tmp_path, linkage):
    parsed = parse_self_backend_module(_ir(linkage))
    path = tmp_path / "module.idx"
    _, header, _ = _encode(path, parsed)
    first_bytes = path.read_bytes()
    assert header["schema"] == "pcc.self-backend.indexed-module.v1"
    assert "definition_linkage" not in header["functions"][0]
    restored = decode_indexed_module_file(str(path))
    assert restored.functions[0].definition_linkage == ("internal" if linkage == "internal" else "external")
    _encode(path, restored)
    assert path.read_bytes() == first_bytes


@pytest.mark.parametrize("target", [TARGET, "amd64-linux", "arm64-apple-darwin", "aarch64-apple-macosx15.0"])
def test_capability_uses_owned_target_contract(target):
    assert supports_external_odr_function_definitions(target)


@pytest.mark.parametrize("target", ["aarch64-unknown-linux-gnu", "x86_64-pc-windows-msvc", "x86_64-linux-windows-msvc", "arm64-notapple-darwinjunk"])
def test_unsupported_targets_are_not_admitted(target):
    assert not supports_external_odr_function_definitions(target)
    diagnostic = ("unsupported C target ABI" if target == "arm64-notapple-darwinjunk"
                  else "external_odr.*unavailable")
    with pytest.raises(ValueError, match=diagnostic):
        _module("int f(void) { return 7; }", target=target)


@pytest.mark.parametrize("storage", ["static", "inline", "static inline"])
def test_c_static_inline_is_not_an_odr_surrogate(storage):
    with pytest.raises(ValueError, match="external_odr.*conflicts"):
        _module(storage + " int f(void) { return 7; }")


@pytest.mark.parametrize("linkage", ["weak", "linkonce", "linkonce_odr", "available_externally", "private", "internal weak_odr"])
def test_unimplemented_or_conflicting_ir_linkage_is_not_silently_strong(linkage):
    with pytest.raises(BackendUnavailable, match="linkage"):
        parse_self_backend_module(_ir(linkage))


def test_elf_definition_is_weak_but_ordinary_definition_unchanged():
    weak_asm = emit_x86_64_linux_asm(_ir())
    strong_asm = emit_x86_64_linux_asm(_ir(""))
    assert weak_asm.replace(".weak f\n", "") == strong_asm
    weak = assemble_file(weak_asm)
    strong = assemble_file(strong_asm)
    assert next(symbol for symbol in weak.symbols if symbol.name == "f").binding == elf.STB_WEAK
    for value in (weak, strong):
        assert elf.parse_relocatable(elf.emit_relocatable(value)) == value


@pytest.mark.parametrize("symbol_type", [elf.STT_FUNC, elf.STT_NOTYPE])
def test_elf_metadata_rejects_weak_symbol_instead_of_retargeting_body(symbol_type):
    text = elf.ElfSection(".text", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_EXECINSTR, 16, b"\xc3")
    frame = elf.ElfSection(".eh_frame", elf.SHT_PROGBITS, elf.SHF_ALLOC, 8, b"\0" * 8,
                           relocations=(elf.ElfRelocation(0, 1, elf.R_X86_64_64),))
    with pytest.raises(elf.ElfError, match="physical local anchor"):
        elf.ElfObject((text, frame), (elf.ElfSymbol.null(), elf.ElfSymbol("f", 1, 0, 1, elf.STB_WEAK, symbol_type)))


def _tu(suffix, value=7, linkage="weak_odr"):
    return _ir(linkage, value=value) + f'''
define ptr @addr_{suffix}() {{
entry:
 ret ptr @f
}}
define i32 @call_{suffix}() {{
entry:
 %r = call i32 @f()
 ret i32 %r
}}
'''


def _entry(expected):
    return assemble_file(f'''.intel_syntax noprefix
.text
.globl _start
.type _start, @function
_start:
 call addr_a
 mov rbx, rax
 call addr_b
 cmp rax, rbx
 jne failed
 call call_a
 cmp eax, {expected}
 jne failed
 call call_b
 cmp eax, {expected}
 jne failed
 xor edi, edi
 jmp done
failed:
 mov edi, 1
done:
 mov eax, 60
 syscall
''')


def _run(path, payload):
    path.write_bytes(payload)
    path.chmod(0o755)
    result = subprocess.run([str(path)], capture_output=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")


@pytest.mark.integration
@pytest.mark.parametrize("reverse,strong,merged", [(False, False, False), (True, False, False), (False, True, False), (True, True, False), (False, False, True), (True, True, True)])
def test_owned_elf_two_tu_function_address_and_call_identity(tmp_path, reverse, strong, merged):
    from pcc.backend.relocatable_merge import merge_elf_objects
    sources = [_tu("a", 7), _tu("b", 11, "" if strong else "weak_odr")]
    objects = [assemble_file(emit_x86_64_linux_asm(source)) for source in sources]
    if reverse:
        objects.reverse()
    expected = 11 if strong or reverse else 7
    if merged:
        objects = [merge_elf_objects(objects)]
    objects.insert(0, _entry(expected))
    _run(tmp_path / "owned", elf.link_static_executable(objects))


@pytest.mark.integration
def test_same_emitted_assembly_external_gnu_reference_and_owned_execution(tmp_path):
    # Reference covers the owned assembler/linker protocol, not C++ Sema.
    compiler = shutil.which("cc")
    assert compiler, "external GNU assembler/linker reference prerequisite is unavailable"
    sources = [_tu("a"), _tu("b")]
    entry = tmp_path / "entry.o"
    entry.write_bytes(elf.emit_relocatable(_entry(7)))
    reference_objects = []
    for index, source in enumerate(sources):
        assembly = emit_x86_64_linux_asm(source)
        path = tmp_path / f"tu{index}.s"
        path.write_text(assembly)
        output = tmp_path / f"tu{index}.o"
        subprocess.run([compiler, "-x", "assembler", "-c", str(path), "-o", str(output)], check=True, capture_output=True, timeout=30)
        reference_objects.append(str(output))
    reference = tmp_path / "reference"
    subprocess.run([compiler, "-nostdlib", "-static", str(entry), *reference_objects, "-o", str(reference)], check=True, capture_output=True, timeout=30)
    result = subprocess.run([str(reference)], capture_output=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
    objects = [_entry(7), *(assemble_file(emit_x86_64_linux_asm(source)) for source in sources)]
    _run(tmp_path / "owned", elf.link_static_executable(objects))


@pytest.mark.integration
def test_typed_ast_two_translation_units_execute_with_one_function_address(tmp_path):
    modules = [_module("int f(void) { return 7; } void *addr_" + suffix
                       + "(void) { return (void *)f; } int call_" + suffix
                       + "(void) { return f(); }") for suffix in ("a", "b")]
    objects = [_entry(7), *(assemble_file(emit_x86_64_linux_asm(str(module))) for module in modules)]
    _run(tmp_path / "typed_ast", elf.link_static_executable(objects))


@pytest.mark.parametrize("change", ["downgrade", "drop_marker", "unknown", "local_odr"])
def test_indexed_linkage_metadata_cannot_be_silently_downgraded(tmp_path, change):
    path = tmp_path / "module.idx"
    magic, header, payload = _encode(path, parse_self_backend_module(_ir()))
    row = header["functions"][0]
    if change == "downgrade":
        header["schema"] = "pcc.self-backend.indexed-module.v1"
    elif change == "drop_marker":
        del row["definition_linkage"]
    elif change == "unknown":
        row["definition_linkage"] = "weak"
    else:
        row["is_global"] = False
    data = json.dumps(header, sort_keys=True).encode()
    path.write_bytes(magic + b"\n" + str(len(data)).encode() + b"\n" + data + payload)
    with pytest.raises(ValueError, match="linkage"):
        decode_indexed_module_file(str(path))


def test_odr_indexed_schema_retains_debug_graph(tmp_path):
    from pcc.backend.self_backend_debug import DebugMetadata
    parsed = parse_self_backend_module(_ir())
    parsed.type_context.debug_info = DebugMetadata({})
    path = tmp_path / "debug.idx"
    _, header, _ = _encode(path, parsed)
    assert header["schema"] == "pcc.self-backend.indexed-module.v3"
    assert header["source_debug"]["profile"] == "pcc.source-debug.v1"
    restored = decode_indexed_module_file(str(path))
    assert restored.type_context.debug_info is not None
    assert restored.functions[0].definition_linkage == "external_odr"



def test_aarch64_elf_section_api_cannot_silently_drop_weak_definition():
    from pcc.backend.arm64_elf_driver import from_sections
    from pcc.backend.macho_obj import Section, TextSymbol
    from pcc.backend import macho_spec
    section = Section("__text", "__TEXT", b"\xc0\x03\x5f\xd6",
                      flags=macho_spec.S_ATTR_PURE_INSTRUCTIONS | macho_spec.S_ATTR_SOME_INSTRUCTIONS,
                      symbols=(TextSymbol("f", 0, weak_definition=True),))
    with pytest.raises(elf.ElfError, match="external_odr.*unavailable"):
        from_sections([section])
