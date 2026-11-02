"""Default direct object emission for owned self-backend targets.

`--backend self --emit-obj` sends AArch64 Darwin and x86_64 Linux assembly
through pcc's owned object writers unless `PCC_SELF_OBJ=system-as` explicitly
selects the system assembler oracle.  This suite pins the fail-closed route
policy and proves the default Darwin path on a real self-backend compile plus
the Linux route without requiring the host platform:

- default emission does not resolve or run the system assembler
- explicit system-as selection reaches only the oracle route
- invalid selection is rejected before asm emission
- default output has the same section payloads, relocations, and symbols as
  the system-as object of the same compile, then links and runs
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import (
    arm64_asm_driver,
    macho_obj,
    native_object,
    relocatable_merge,
)
from pcc.backend import macho_spec as spec
from pcc.backend import x86_64_asm_driver
from pcc.backend import elf_x86_64
from pcc.frontends.c.evaluator import c_evaluator
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator, _select_self_object_emitter

_CC = shutil.which(os.environ.get("CC", "cc"))
_IS_ARM64_DARWIN = hasattr(os, "uname") and os.uname().sysname == "Darwin" and os.uname().machine == "arm64"
_GATE = None if (_CC and _IS_ARM64_DARWIN) else "needs cc and Darwin arm64"
_DARWIN_GATE = pytest.mark.pcc_gate(unavailable=_GATE)
_IS_X86_64_LINUX = (
    os.sys.platform.startswith("linux")
    and platform.machine().lower() in {"x86_64", "amd64", "x64"}
)
_LINUX_GATE = pytest.mark.pcc_gate(
    unavailable=(
        None if (_CC and _IS_X86_64_LINUX) else "needs cc and Linux x86_64"
    )
)

SOURCE = r"""
static int fib(int n) {
    if (n < 2) return n;
    return fib(n - 1) + fib(n - 2);
}

static const char tag[] = "self-obj";

int checksum(void) {
    int total = 0;
    for (int i = 0; i < 10; i++) total += fib(i);
    return total + tag[0];
}
"""


def _repo_root() -> Path:
    cur = Path(__file__).resolve().parent
    while cur != cur.parent:
        if (cur / "AGENTS.md").exists():
            return cur
        cur = cur.parent
    raise RuntimeError("AGENTS.md not found above " + __file__)


REPO = _repo_root()


def _emit(tmp_path: Path, tag: str, env_extra: dict) -> Path:
    src = tmp_path / "prog.c"
    src.write_text(SOURCE, encoding="utf-8")
    obj = tmp_path / f"prog_{tag}.o"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env.pop("PCC_SELF_OBJ", None)
    env.update(env_extra)
    run = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--emit-obj", str(obj),
         str(src)],
        capture_output=True, text=True, timeout=560, env=env, cwd=str(REPO),
    )
    assert run.returncode == 0, run.stderr[-2000:]
    return obj


def _shape(path: Path):
    obj = spec.parse_object(path.read_bytes())
    sections = obj.sections()
    names = [s["name"] for s in obj.symbols()]

    def relocation_target(relocation):
        if relocation["r_type"] == spec.ARM64_RELOC_ADDEND:
            return relocation["r_symbolnum"]
        if relocation["r_extern"]:
            return names[relocation["r_symbolnum"]]
        # For a local relocation r_symbolnum is a one-based section ordinal,
        # not a symbol-table index.  Normalize it to section identity so pcc
        # and system-as objects compare semantically even when their private
        # temporary-symbol inventories differ.
        target = sections[relocation["r_symbolnum"] - 1]
        return target["segname_str"], target["sectname_str"]

    payloads, relocs = {}, {}
    for sec in sections:
        key = (sec["segname_str"], sec["sectname_str"])
        payloads[key] = obj.data[sec["offset"]:sec["offset"] + sec["size"]]
        relocs[key] = [
            (r["r_address"],
             relocation_target(r),
             r["r_type"], r["r_pcrel"], r["r_length"], r["r_extern"])
            for r in obj.relocations(sec)
        ]
    symbols = {
        s["name"]: (s["n_type"], s["n_sect"], s["n_value"])
        for s in obj.symbols() if not s["name"].startswith("ltmp")
    }
    return payloads, relocs, symbols


def _elf_shape(path: Path):
    obj = elf_x86_64.parse_relocatable(path.read_bytes())

    def relocation_target(relocation):
        symbol = obj.symbols[relocation.symbol_index]
        if 1 <= symbol.section_index <= len(obj.sections):
            return (
                "defined",
                obj.sections[symbol.section_index - 1].name,
                symbol.value + relocation.addend,
            )
        return ("external", symbol.name, relocation.addend)

    sections = {
        section.name: (
            section.type,
            section.flags,
            section.align,
            section.data,
            section.mem_size,
            tuple(
                (
                    relocation.offset,
                    relocation.type,
                    relocation_target(relocation),
                )
                for relocation in section.relocations
            ),
        )
        for section in obj.sections
        if section.size or section.relocations or section.name == ".note.GNU-stack"
    }
    symbols = {
        symbol.name: (
            obj.sections[symbol.section_index - 1].name
            if 1 <= symbol.section_index <= len(obj.sections)
            else symbol.section_index,
            symbol.value,
            symbol.size,
            symbol.binding,
            symbol.type,
            symbol.visibility,
        )
        for symbol in obj.symbols
        if symbol.name
    }
    return sections, symbols


def _route_evaluator(monkeypatch, asm_text="pcc-direct-asm"):
    evaluator = object.__new__(CEvaluator)
    monkeypatch.setattr(
        evaluator,
        "_self_backend_asm_text",
        lambda _compiled_units: asm_text,
    )
    # The direct Darwin NativeObject route assembles each IR unit separately
    # so precise stack-map tables are not concatenated.  Stub that per-unit
    # owner as well as the combined-assembly helper used by Linux/system-as.
    monkeypatch.setattr(c_evaluator, "emit_self_asm", lambda _ir: asm_text)
    return evaluator


def _darwin_unit():
    return [("unit", 'target triple = "arm64-apple-darwin"\n', None, ())]


def _linux_unit():
    return [("unit", 'target triple = "x86_64-unknown-linux-gnu"\n', None, ())]


def test_darwin_default_uses_pcc_writer_without_system_assembler(
    tmp_path, monkeypatch,
):
    evaluator = _route_evaluator(monkeypatch)
    output = tmp_path / "default.o"
    seen = {}

    monkeypatch.delenv("PCC_SELF_OBJ", raising=False)
    monkeypatch.setattr(
        evaluator,
        "_system_cc",
        lambda: pytest.fail("default Darwin object emission resolved system cc"),
    )

    def fake_assemble(asm_text):
        seen["asm"] = asm_text
        return [
            macho_obj.Section(
                sectname="__text",
                segname="__TEXT",
                data=bytes.fromhex("c0035fd6"),
                align_log2=2,
                flags=macho_obj.TEXT_SECTION_FLAGS,
                symbols=(macho_obj.TextSymbol("_main", 0),),
            )
        ], ["_ext"]

    def fake_emit(sections, *, undefined, weak_undefined=()):
        assert len(sections) == 1
        assert sections[0].sectname == "__text"
        assert sections[0].data == bytes.fromhex("c0035fd6")
        assert sections[0].symbols == (macho_obj.TextSymbol("_main", 0),)
        assert undefined == ["_ext"]
        assert weak_undefined == ()
        return b"pcc-macho-object"

    monkeypatch.setattr(
        arm64_asm_driver,
        "assemble_file",
        fake_assemble,
    )
    monkeypatch.setattr(
        native_object,
        "emit_object",
        fake_emit,
    )

    evaluator._emit_compiled_units_self_backend(
        _darwin_unit(), emit_obj=str(output), optimize=False,
    )

    assert seen["asm"] == "pcc-direct-asm"
    assert output.read_bytes() == b"pcc-macho-object"


@pytest.mark.parametrize("weak", [False, True], ids=["ordinary", "weak"])
def test_native_macho_writer_preserves_definition_and_reference_metadata(
    monkeypatch, weak,
):
    assembly = ".section __TEXT,__text,regular,pure_instructions\n.globl _main\n"
    if weak:
        assembly += ".weak_definition _main\n"
    assembly += "_main:\nbl _ext\nret\n"
    sections, undefined = arm64_asm_driver.assemble_file(assembly)
    weak_undefined = ("_ext",) if weak else ()
    obj = native_object.NativeObject.from_sections(
        sections, undefined=undefined, weak_undefined=weak_undefined,
    )
    real_emit = native_object.emit_object
    observed = []

    def record_emit(actual_sections, *, undefined, weak_undefined=()):
        assert actual_sections == sections
        assert undefined == ["_ext"]
        assert weak_undefined == (("_ext",) if weak else ())
        assert actual_sections[0].symbols == (
            macho_obj.TextSymbol("_main", 0, weak_definition=weak),
        )
        observed.append(weak_undefined)
        return real_emit(
            actual_sections, undefined=undefined, weak_undefined=weak_undefined,
        )

    monkeypatch.setattr(native_object, "emit_object", record_emit)
    result = spec.parse_object(obj.to_macho())
    symbols = {symbol["name"]: symbol for symbol in result.symbols()}
    assert observed == [weak_undefined]
    assert symbols["_main"]["n_desc"] == (spec.N_WEAK_DEF if weak else 0)
    assert symbols["_main"]["n_sect"] == 1
    assert symbols["_ext"]["n_desc"] == (spec.N_WEAK_REF if weak else 0)
    assert symbols["_ext"]["n_sect"] == 0


def test_explicit_system_as_uses_only_the_oracle_route(tmp_path, monkeypatch):
    evaluator = _route_evaluator(monkeypatch, asm_text="system-oracle-asm")
    output = tmp_path / "oracle.o"
    seen = {}

    monkeypatch.setenv("PCC_SELF_OBJ", "system-as")
    monkeypatch.setattr(evaluator, "_system_cc", lambda: "/oracle/cc")
    monkeypatch.setattr(
        arm64_asm_driver,
        "assemble_file",
        lambda _asm_text: pytest.fail("system-as selection reached pcc writer"),
    )

    def fake_run(command, *, capture_output, text, timeout):
        seen["command"] = command
        seen["asm"] = Path(command[2]).read_text(encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(c_evaluator.subprocess, "run", fake_run)

    evaluator._emit_compiled_units_self_backend(
        _darwin_unit(), emit_obj=str(output), optimize=False,
    )

    assert seen["command"][0:2] == ["/oracle/cc", "-c"]
    assert seen["command"][3:] == ["-o", str(output)]
    assert seen["asm"] == "system-oracle-asm"


def test_invalid_selection_fails_before_asm_or_system_tool(tmp_path, monkeypatch):
    evaluator = object.__new__(CEvaluator)
    monkeypatch.setenv("PCC_SELF_OBJ", "typo")
    monkeypatch.setattr(
        evaluator,
        "_self_backend_asm_text",
        lambda _units: pytest.fail("invalid selection reached asm emission"),
    )
    monkeypatch.setattr(
        evaluator,
        "_system_cc",
        lambda: pytest.fail("invalid selection resolved system cc"),
    )

    with pytest.raises(
        BackendUnavailable,
        match="PCC_SELF_OBJ must be 'pcc' or 'system-as', got 'typo'",
    ):
        evaluator._emit_compiled_units_self_backend(
            _darwin_unit(), emit_obj=str(tmp_path / "invalid.o"), optimize=False,
        )


def test_linux_target_uses_owned_object_emitter_by_default():
    target = "self-x86_64-linux-v0"
    assert _select_self_object_emitter(None, target) == "pcc"
    assert _select_self_object_emitter("pcc", target) == "pcc"


def test_linux_default_uses_pcc_encoder_without_system_assembler(
    tmp_path, monkeypatch,
):
    evaluator = _route_evaluator(monkeypatch, asm_text="owned-linux-asm")
    output = tmp_path / "linux.o"
    sentinel = elf_x86_64.ElfObject(
        sections=(elf_x86_64.ElfSection(
            ".text", elf_x86_64.SHT_PROGBITS,
            elf_x86_64.SHF_ALLOC | elf_x86_64.SHF_EXECINSTR, 1, b"\xc3",
        ),),
        symbols=(elf_x86_64.ElfSymbol.null(),),
    )
    real_merge = relocatable_merge.merge_elf_objects
    real_emit = elf_x86_64.emit_relocatable
    original_bytes = real_emit(sentinel)
    seen = {}

    def record_merge(objects):
        objects = tuple(objects)
        assert len(objects) == 1 and objects[0] is sentinel
        assert "merged" not in seen
        merged = real_merge(objects)
        assert merged == sentinel
        assert merged is not sentinel
        seen["merged"] = merged
        return merged

    def fake_emit(obj):
        assert obj is seen["merged"]
        assert obj == sentinel
        return b"pcc-elf-object"

    monkeypatch.delenv("PCC_SELF_OBJ", raising=False)
    monkeypatch.setattr(
        evaluator,
        "_system_cc",
        lambda: pytest.fail("owned Linux object emission resolved system cc"),
    )
    monkeypatch.setattr(
        x86_64_asm_driver,
        "assemble_file",
        lambda text: sentinel if text == "owned-linux-asm" else pytest.fail(text),
    )
    monkeypatch.setattr(
        relocatable_merge, "merge_elf_objects", record_merge,
    )
    monkeypatch.setattr(
        elf_x86_64,
        "emit_relocatable",
        fake_emit,
    )
    evaluator._emit_compiled_units_self_backend(
        _linux_unit(), emit_obj=str(output), optimize=False,
    )
    assert output.read_bytes() == b"pcc-elf-object"
    assert real_emit(sentinel) == original_bytes


def test_linux_default_real_merge_preserves_weak_symbols_and_relocation_owners(
    tmp_path, monkeypatch,
):
    evaluator = _route_evaluator(monkeypatch)
    output = tmp_path / "merged.o"
    elf = elf_x86_64
    text_flags = elf.SHF_ALLOC | elf.SHF_EXECINSTR
    data_flags = elf.SHF_ALLOC | elf.SHF_WRITE
    first = elf.ElfObject(
        sections=(
            elf.ElfSection(".text", elf.SHT_PROGBITS, text_flags, 1, b"\xc3"),
            elf.ElfSection(
                ".data", elf.SHT_PROGBITS, data_flags, 8, b"\0" * 16,
                relocations=(
                    elf.ElfRelocation(0, 1, elf.R_X86_64_64),
                    elf.ElfRelocation(8, 2, elf.R_X86_64_64),
                ),
            ),
        ),
        symbols=(
            elf.ElfSymbol.null(),
            elf.ElfSymbol("answer", 1, 0, 1, elf.STB_WEAK, elf.STT_FUNC),
            elf.ElfSymbol(
                "optional", elf.SHN_UNDEF, 0, 0, elf.STB_WEAK, elf.STT_NOTYPE,
            ),
        ),
    )
    second = elf.ElfObject(
        sections=(
            elf.ElfSection(".text", elf.SHT_PROGBITS, text_flags, 1, b"\x90\xc3"),
        ),
        symbols=(
            elf.ElfSymbol.null(),
            elf.ElfSymbol("answer", 1, 0, 2, elf.STB_GLOBAL, elf.STT_FUNC),
        ),
    )
    expected = elf.ElfObject(
        sections=(
            elf.ElfSection(
                ".text", elf.SHT_PROGBITS, text_flags, 1, b"\xc3\x90\xc3",
            ),
            elf.ElfSection(
                ".data", elf.SHT_PROGBITS, data_flags, 8, b"\0" * 16,
                relocations=(
                    elf.ElfRelocation(0, 2, elf.R_X86_64_64),
                    elf.ElfRelocation(8, 3, elf.R_X86_64_64),
                ),
            ),
        ),
        symbols=(
            elf.ElfSymbol.null(),
            elf.ElfSymbol(
                "answer.__pcc_object_0_1", 1, 0, 1, elf.STB_LOCAL, elf.STT_FUNC,
            ),
            elf.ElfSymbol("answer", 1, 1, 2, elf.STB_GLOBAL, elf.STT_FUNC),
            elf.ElfSymbol(
                "optional", elf.SHN_UNDEF, 0, 0, elf.STB_WEAK, elf.STT_NOTYPE,
            ),
        ),
    )
    inputs = (first, second)
    real_merge = relocatable_merge.merge_elf_objects
    real_emit = elf.emit_relocatable
    original_bytes = tuple(real_emit(obj) for obj in inputs)
    seen = {}
    triple = 'target triple = "x86_64-unknown-linux-gnu"\n'
    units = [
        ("first", triple + "; first\n", None, ()),
        ("second", triple + "; second\n", None, ()),
    ]
    assemblies = {units[0][1]: "first-asm", units[1][1]: "second-asm"}

    def record_merge(objects):
        objects = tuple(objects)
        assert len(objects) == 2
        assert all(actual is original for actual, original in zip(objects, inputs))
        assert "merged" not in seen
        result = real_merge(objects)
        assert result == expected
        assert all(result is not original for original in inputs)
        assert tuple(real_emit(obj) for obj in inputs) == original_bytes
        seen["merged"] = result
        return result

    def record_emit(obj):
        assert obj is seen["merged"]
        assert obj == expected
        return real_emit(obj)

    monkeypatch.delenv("PCC_SELF_OBJ", raising=False)
    monkeypatch.setattr(
        evaluator, "_system_cc",
        lambda: pytest.fail("owned Linux merge resolved system cc"),
    )
    monkeypatch.setattr(c_evaluator, "emit_self_asm", lambda ir: assemblies[ir])
    monkeypatch.setattr(
        x86_64_asm_driver, "assemble_file",
        lambda text: {"first-asm": first, "second-asm": second}[text],
    )
    monkeypatch.setattr(relocatable_merge, "merge_elf_objects", record_merge)
    monkeypatch.setattr(elf, "emit_relocatable", record_emit)
    evaluator._emit_compiled_units_self_backend(
        units, emit_obj=str(output), optimize=False,
    )
    assert output.read_bytes() == real_emit(expected)
    assert tuple(real_emit(obj) for obj in inputs) == original_bytes


@_LINUX_GATE
def test_linux_default_object_matches_explicit_system_as_semantics(tmp_path):
    via_pcc = _emit(tmp_path, "linux_pcc_default", {})
    via_as = _emit(tmp_path, "linux_system_as", {"PCC_SELF_OBJ": "system-as"})

    pcc_sections, pcc_symbols = _elf_shape(via_pcc)
    as_sections, as_symbols = _elf_shape(via_as)
    assert pcc_sections == as_sections
    assert pcc_symbols == as_symbols


@_LINUX_GATE
def test_linux_default_object_links_and_runs(tmp_path):
    obj = _emit(tmp_path, "linux_run", {})
    main_c = tmp_path / "linux_main.c"
    main_c.write_text(
        "extern int checksum(void);\n"
        "int main(void) { return checksum() == 203 ? 0 : 1; }\n",
        encoding="utf-8",
    )
    binary = tmp_path / "linux_prog"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    link = subprocess.run(
        [_CC, str(main_c), str(obj), "-o", str(binary)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert link.returncode == 0, link.stderr[-2000:]
    run = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=30, env=env
    )
    assert run.returncode == 0, (run.stdout, run.stderr)


@_DARWIN_GATE
def test_default_object_equals_explicit_system_as_structurally(tmp_path):
    via_pcc = _emit(tmp_path, "pcc_default", {})
    via_as = _emit(tmp_path, "system_as", {"PCC_SELF_OBJ": "system-as"})

    p_as, r_as, s_as = _shape(via_as)
    p_pcc, r_pcc, s_pcc = _shape(via_pcc)

    assert set(p_pcc) == set(p_as), (set(p_pcc), set(p_as))
    for key in p_as:
        assert p_pcc[key] == p_as[key], f"{key}: payload differs"
    assert r_pcc == r_as
    assert s_pcc == s_as


@_DARWIN_GATE
def test_default_object_links_and_runs(tmp_path):
    obj = _emit(tmp_path, "run", {})
    main_c = tmp_path / "main.c"
    # fib(0..9) sums to 88; 's' is 115 -> checksum() == 203
    main_c.write_text(
        "extern int checksum(void);\n"
        "int main(void) { return checksum() == 203 ? 0 : 1; }\n",
        encoding="utf-8",
    )
    binary = tmp_path / "prog"
    env = os.environ.copy(); env.pop("LC_ALL", None)
    link = subprocess.run(
        [_CC, str(main_c), str(obj), "-o", str(binary)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert link.returncode == 0, link.stderr
    rc = subprocess.run([str(binary)], capture_output=True, timeout=60).returncode
    assert rc == 0, f"checksum wrong under the pcc-routed object (rc={rc})"
