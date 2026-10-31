"""Public target selection with real owned ELF/Mach-O object evidence.

Darwin tests are CROSS-EMISSION on Linux, not execution on a Mac.
"""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.targets import DARWIN_LAYOUT, DEFAULT_TARGET, LINUX_LAYOUT

PROGRAMS = Path(__file__).parent / "programs"
CALLS = 'fn helper(x:i64)->i64 { x+2 } pub extern "C" fn answer()->i64 { helper(40) }'


def compile_source(source=CALLS, **options):
    return compile_rust(source, experimental_rustc_checked=True, **options)


def test_default_target_preserves_linux_and_explicit_integer_widths():
    result = compile_source('fn a(x:i32)->i32{x+1} fn b(x:i64)->i64{x+1}')
    assert result.target_triple == DEFAULT_TARGET
    assert result.module.triple == DEFAULT_TARGET
    assert result.module.data_layout == result.data_layout == LINUX_LAYOUT
    assert "define i32" in result.ir_text and "add i32" in result.ir_text
    assert "define i64" in result.ir_text and "add i64" in result.ir_text
    assert result.emit_object()[:4] == b"\x7fELF"


@pytest.mark.parametrize("triple", ["arm64-apple-darwin", "aarch64-apple-darwin", "arm64-apple-darwin23.0", "arm64-apple-macosx14.0.0"])
def test_target_entry_emits_real_arm64_macho_symbols_and_branch_relocations(triple, monkeypatch):
    from pcc.backend import macho_spec

    def forbidden(*args, **kwargs):
        raise AssertionError("Rust target emission invoked an external tool")

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", forbidden)
        result = compile_source(target_triple=triple)
        assert result.module.triple == triple
        assert result.module.data_layout == DARWIN_LAYOUT
        obj = result.emit_object()
    parsed = macho_spec.parse_object(obj)
    assert obj[:4] == b"\xcf\xfa\xed\xfe"
    assert parsed.header["cputype"] == macho_spec.CPU_TYPE_ARM64
    assert parsed.header["filetype"] == macho_spec.MH_OBJECT
    symbols = parsed.symbols()
    names = {symbol["name"] for symbol in symbols}
    assert "_" + result.symbols["helper"] in names
    assert "_" + result.symbols["answer"] in names
    text = next(section for section in parsed.sections() if section["sectname"].rstrip(b"\0") == b"__text")
    branches = [reloc for reloc in parsed.relocations(text) if reloc["r_type"] == macho_spec.ARM64_RELOC_BRANCH26]
    assert branches
    assert all(reloc["r_pcrel"] == 1 and reloc["r_length"] == 2 for reloc in branches)
    assert any(reloc["r_extern"] and symbols[reloc["r_symbolnum"]]["name"] == "_" + result.symbols["helper"] for reloc in branches)
    assert text["size"] > 0


@pytest.mark.parametrize("name", ["control", "values", "short_circuit", "integer_rules", "grouping", "high_bits"])
def test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite(name, monkeypatch):
    from pcc.backend import macho_spec

    source = PROGRAMS / (name + ".rs")
    original = source.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("cross-emission must use owned tools only")
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", forbidden)
        result = compile_source(original.decode(), target_triple="arm64-apple-darwin")
        parsed = macho_spec.parse_object(result.emit_object())
    assert parsed.header["cputype"] == macho_spec.CPU_TYPE_ARM64
    assert "_" + result.symbols["answer"] in {symbol["name"] for symbol in parsed.symbols()}
    assert source.read_bytes() == original


@pytest.mark.parametrize("triple", [None, 1, "", " arm64-apple-darwin", "arm64-apple-darwin\n", 'arm64-apple-darwin"', "mips-unknown-linux-gnu", "unknown-unknown-unknown", "x86_64-apple-darwin", "arm64-apple-darwinbanana", "arm64--darwin"])
def test_invalid_or_unsupported_targets_fail_before_lowering(triple):
    with pytest.raises(RustError, match="target"):
        compile_source(target_triple=triple)


@pytest.mark.parametrize("triple", ["aarch64-unknown-linux-gnu", "x86_64-pc-windows-msvc"])
def test_registered_but_not_rust_qualified_target_is_explicit(triple):
    with pytest.raises(RustError, match="not yet qualified"):
        compile_source(target_triple=triple)


@pytest.mark.parametrize("field,value", [("triple", "arm64-apple-darwin"), ("data_layout", "E-p:32:32")])
def test_manual_header_retarget_cannot_bypass_target_contract(field, value):
    result = compile_source()
    setattr(result.module, field, value)
    with pytest.raises(RustError, match="target/layout changed"):
        result.emit_object()


def test_target_selection_is_not_stale_between_modules():
    from pcc.backend import macho_spec
    from pcc.backend.elf_x86_64 import EM_X86_64, parse_relocatable
    first = compile_source()
    darwin = compile_source(target_triple="arm64-apple-darwin")
    last = compile_source()
    assert parse_relocatable(first.emit_object()).machine == EM_X86_64
    assert macho_spec.parse_object(darwin.emit_object()).header["cputype"] == macho_spec.CPU_TYPE_ARM64
    assert parse_relocatable(last.emit_object()).machine == EM_X86_64


@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith("linux") and platform.machine() in {"x86_64", "AMD64"} else "requires Linux x86-64 owned execution")
def test_public_object_route_links_and_executes_full_width_linux_result(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    source = (PROGRAMS / "high_bits.rs").read_text()
    def forbidden(*args, **kwargs):
        raise AssertionError("object compilation/link must not call external tools")
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", forbidden)
        result = compile_source(source, target_triple=DEFAULT_TARGET)
        obj = parse_relocatable(result.emit_object())
        start = assemble_file(f'''.intel_syntax noprefix
.text
.globl _start
_start:
  call {result.symbols['answer']}
  sub rsp, 16
  mov QWORD PTR [rsp], rax
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 8
  syscall
  mov edi, 0
  mov eax, 60
  syscall
''')
        image = link_static_executable([obj, start])
    executable = tmp_path / "rust-target-native"
    executable.write_bytes(image)
    executable.chmod(0o700)
    run = subprocess.run([str(executable)], capture_output=True, timeout=3)
    assert (run.returncode, run.stdout, run.stderr) == (0, bytes.fromhex("1032547698badcfe"), b"")


@pytest.mark.parametrize("triple", [
    "x86_64-unknown-linux-gnux32",
    "x86_64-pc-linux-gnu_ilp32",
    "x86_64-unknown-linux-bogus",
    "arm64-apple-darwin-bogus",
    "arm64-apple-macosx14.0.0-simulator",
])
def test_backend_family_match_does_not_silently_select_an_unqualified_abi(triple):
    with pytest.raises(RustError, match="environment is not yet qualified"):
        compile_source(target_triple=triple)


@pytest.mark.parametrize("triple", [
    "x86_64-pc-linux-gnu", "x86_64-unknown-linux-musl",
    "x86_64-unknown-linux", "x86_64-linux", "amd64-linux-gnu",
])
def test_explicit_lp64_linux_environment_and_compact_aliases(triple):
    from pcc.backend.elf_x86_64 import EM_X86_64, parse_relocatable
    result = compile_source(target_triple=triple)
    assert result.data_layout == LINUX_LAYOUT
    assert parse_relocatable(result.emit_object()).machine == EM_X86_64


@pytest.mark.parametrize("triple", [DEFAULT_TARGET, "arm64-apple-darwin"])
@pytest.mark.parametrize("label", ["", "normal", '"\n@injected = global i64 1\n;', "\r\n\x00", "程序", "\ud800"])
def test_module_label_cannot_inject_textual_ir(label, triple, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("owned object emission invoked an external tool")
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", forbidden)
        result = compile_source(module_name=label, target_triple=triple)
        assert result.module.name == "rust_" + label.encode("utf-8", "surrogatepass").hex()
        assert "@injected =" not in result.ir_text
        assert result.emit_object()[:4] == (b"\x7fELF" if triple == DEFAULT_TARGET else b"\xcf\xfa\xed\xfe")


@pytest.mark.parametrize("label", [None, 1, b"rust"])
def test_nonstring_module_label_has_explicit_diagnostic(label):
    with pytest.raises(RustError, match="module_name"):
        compile_source(module_name=label)
