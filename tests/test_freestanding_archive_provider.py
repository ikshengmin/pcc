"""Finite archive-format/selection controls; no C compiler, link, or execution."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from pcc.backend import elf_x86_64 as elf
from tests.freestanding_archive_provider import require_elf_archive_function

MEMBER = "freestanding_runtime_traps.o"
SYMBOL = "pcc_null_callee_trap"


def _object_bytes(*, symbol_name=SYMBOL, section_index=1, binding=elf.STB_GLOBAL,
                  symbol_type=elf.STT_FUNC, machine=elf.EM_X86_64, executable=True,
                  duplicate_symbol=False):
    section = elf.ElfSection(".text", elf.SHT_PROGBITS,
        elf.SHF_ALLOC | (elf.SHF_EXECINSTR if executable else 0), 4, b"\xc3\x90\x90\x90")
    symbol = elf.ElfSymbol(symbol_name, section_index, 0, 0 if section_index == 0 else 1,
                           binding, symbol_type)
    symbols = (elf.ElfSymbol.null(), symbol) + ((symbol,) if duplicate_symbol else ())
    return elf.emit_relocatable(elf.ElfObject((section,), symbols, machine=machine))


def _member(raw_name, data):
    header = (raw_name.ljust(16) + "0".ljust(12) + "0".ljust(6)
              + "0".ljust(6) + "100644".ljust(8) + str(len(data)).ljust(10) + "`\n")
    assert len(header) == 60
    return header.encode("ascii") + data + (b"\n" if len(data) % 2 else b"")


def _archive(members, *, style="gnu"):
    result = bytearray(b"!<arch>\n")
    if style == "gnu":
        names = b"".join(name.encode() + b"/\n" for name, _ in members)
        result.extend(_member("//", names))
        offset = 0
        for name, payload in members:
            result.extend(_member("/" + str(offset), payload))
            offset += len(name.encode()) + 2
    else:
        assert style == "bsd"
        for name, payload in members:
            encoded = name.encode()
            result.extend(_member("#1/" + str(len(encoded)), encoded + payload))
    return bytes(result)


def _witness(tmp_path, archive):
    path = tmp_path / "libpcc_freestanding_c.a"
    path.write_bytes(archive)
    return require_elf_archive_function(path, member_name=MEMBER, symbol_name=SYMBOL,
                                        machine=elf.EM_X86_64)


@pytest.mark.parametrize("style", ["gnu", "bsd"])
def test_real_archive_provider_is_available(tmp_path, style):
    payload = _object_bytes()
    archive = _archive([(MEMBER, payload)], style=style)
    assert _witness(tmp_path, archive) == {
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "member_sha256": hashlib.sha256(payload).hexdigest(),
        "member": MEMBER, "symbol": SYMBOL, "machine": elf.EM_X86_64,
    }


@pytest.mark.parametrize("demanded", [False, True])
def test_availability_does_not_require_unreferenced_extraction(tmp_path, demanded):
    archive = _archive([(MEMBER, _object_bytes())])
    _witness(tmp_path, archive)
    members = elf.read_archive(archive)
    selected_names = []
    selected, unresolved = elf.select_archive_members(
        members, {SYMBOL} if demanded else set(), selected_names=selected_names)
    assert unresolved == set()
    assert selected_names == ([MEMBER] if demanded else [])
    assert selected == ([members[0].object] if demanded else [])


@pytest.mark.parametrize("case, message", [
    ("missing-member", "exactly one required provider member"),
    ("duplicate-member", "exactly one required provider member"),
    ("wrong-symbol", "exactly one required symbol"),
    ("duplicate-symbol", "exactly one required symbol"),
    ("undefined-symbol", "section-defined"),
    ("local-binding", "strong global definition"),
    ("weak-binding", "strong global definition"),
    ("notype", "must be a function"),
    ("object-type", "must be a function"),
    ("wrong-machine", "ELF machine differs"),
    ("non-executable", "executable object code"),
    ("malformed-archive", "not an ar archive"),
    ("malformed-object", "ELF"),
])
def test_wrong_provider_is_rejected(tmp_path, case, message):
    options = {
        "wrong-symbol": {"symbol_name": "not_the_trap"},
        "duplicate-symbol": {"duplicate_symbol": True},
        "undefined-symbol": {"section_index": elf.SHN_UNDEF},
        "local-binding": {"binding": elf.STB_LOCAL},
        "weak-binding": {"binding": elf.STB_WEAK},
        "notype": {"symbol_type": elf.STT_NOTYPE},
        "object-type": {"symbol_type": elf.STT_OBJECT},
        "wrong-machine": {"machine": elf.EM_AARCH64},
        "non-executable": {"executable": False},
    }.get(case, {})
    payload = b"not ELF" if case == "malformed-object" else _object_bytes(**options)
    members = [("other.o" if case == "missing-member" else MEMBER, payload)]
    if case == "duplicate-member":
        members *= 2
    archive = b"not ar" if case == "malformed-archive" else _archive(members)
    exception = elf.ElfError if case.startswith("malformed-") else AssertionError
    with pytest.raises(exception, match=message):
        _witness(tmp_path, archive)


_ORIGINAL_ZLIB_FUNCTION = '@pytest.mark.pcc_gate(unavailable=None if os.path.isdir(ZLIB_DIR) else "zlib-1.3.1 not found")\ndef test_zlib_runtime_with_self_backend_freestanding_libc(\n    zlib_compiled_units_self,\n    tmp_path,\n):\n    compiled_units, base_dir = zlib_compiled_units_self\n    link_map = tmp_path / "zlib-freestanding.map"\n    map_flag = (\n        "-Wl,-map," + str(link_map)\n        if sys.platform == "darwin"\n        else "-Wl,-Map," + str(link_map)\n    )\n\n    result = CEvaluator(\n        backend="self",\n        allow_unimplemented_backend=True,\n    ).run_compiled_translation_units_with_system_cc(\n        compiled_units,\n        optimize=True,\n        base_dir=base_dir,\n        link_args=[map_flag],\n        freestanding_libc=True,\n    )\n\n    assert result.returncode == 0, (\n        "zlib freestanding-libc runtime failed:\\n"\n        f"{result.stdout}\\n{result.stderr}"\n    )\n    assert "compress/uncompress: hello, hello!" in result.stdout\n    assert "deflate/inflate: hello, hello!" in result.stdout\n    assert "OK" in result.stdout\n    ownership = link_map.read_text(encoding="utf-8")\n    assert "freestanding_mem_str.o" in ownership\n    assert "freestanding_allocator.o" in ownership\n    assert "vendor_" not in ownership\n    if sys.platform.startswith("linux"):\n        assert "libpcc_freestanding_c.a(freestanding_linux_libc.o)" in ownership\n        assert "libpcc_freestanding_c.a(freestanding_errno.o)" in ownership\n        assert "libpcc_freestanding_c.a(freestanding_gc_state.o)" in ownership\n        assert "libpcc_freestanding_c.a(freestanding_runtime_traps.o)" in ownership\n        assert "libpy_runtime_pcc_py.a" not in ownership\n        assert "libc.a" not in ownership\n'
_ORIGINAL_ZLIB_FUNCTION_SHA256 = 'e8dfd989807fb4e678df2fb5eb6fd48c324615f98a8e2c09f4c174144da9e746'
_EXPECTED_OBSERVER_SOURCE = "provider_witnesses = []\noriginal_link_inputs = CEvaluator._freestanding_link_inputs\n\ndef observe_link_inputs(evaluator, *args, **kwargs):\n    inputs = original_link_inputs(evaluator, *args, **kwargs)\n    triple = str(evaluator.target_triple or '').lower()\n    if kwargs.get('enabled') and 'linux' in triple and (triple.startswith('x86_64') or triple.startswith('amd64')):\n        from pcc.backend.elf_x86_64 import EM_X86_64\n        from tests.freestanding_archive_provider import require_elf_archive_function\n        archives = inputs[2]\n        assert len(archives) == 1\n        assert os.path.basename(archives[0]) == 'libpcc_freestanding_c.a'\n        provider_witnesses.append(require_elf_archive_function(archives[0], member_name='freestanding_runtime_traps.o', symbol_name='pcc_null_callee_trap', machine=EM_X86_64))\n    return inputs\nmonkeypatch.setattr(CEvaluator, '_freestanding_link_inputs', observe_link_inputs)"


def test_zlib_preserves_original_runtime_and_platform_contract():
    # Read source only: importing the vendor test would collect native fixtures.
    module = ast.parse((Path(__file__).parent / "c" / "test_zlib.py").read_text())
    fn = next(n for n in module.body if isinstance(n, ast.FunctionDef)
              and n.name == "test_zlib_runtime_with_self_backend_freestanding_libc")
    original = ast.parse(_ORIGINAL_ZLIB_FUNCTION)
    assert hashlib.sha256(_ORIGINAL_ZLIB_FUNCTION.encode()).hexdigest() == _ORIGINAL_ZLIB_FUNCTION_SHA256
    # Pins the observer's original call, exact target boundary and unchanged return.
    assert ast.dump(ast.Module(body=fn.body[:4], type_ignores=[])) == ast.dump(ast.parse(_EXPECTED_OBSERVER_SOURCE))
    assert fn.args.args[-1].arg == "monkeypatch"
    fn.args.args.pop()
    del fn.body[:4]
    replacement = ast.parse("assert len(provider_witnesses) == 1").body[0]
    matches = [node for node in ast.walk(fn) if isinstance(node, ast.If)
               and any(ast.dump(stmt) == ast.dump(replacement) for stmt in node.body)]
    assert len(matches) == 1
    branch = matches[0]
    indices = [i for i, stmt in enumerate(branch.body) if ast.dump(stmt) == ast.dump(replacement)]
    assert len(indices) == 1
    branch.body[indices[0]] = ast.parse(
        'assert "libpcc_freestanding_c.a(freestanding_runtime_traps.o)" in ownership').body[0]
    # The original body, decorators, outputs and other ownership assertions are exact.
    assert ast.dump(ast.Module(body=[fn], type_ignores=[])) == ast.dump(original)
