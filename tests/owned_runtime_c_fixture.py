"""Whole-runtime C controls consume only explicitly admitted owned artifacts."""
from __future__ import annotations

import os
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

from pcc.backend.elf_x86_64 import read_archive_payloads
from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.frontends.python import owned_runtime_build, pipeline
from pcc.frontends.python.pipeline_targets import host_target_triple
from pcc.ir.optimization.driver import optimize_ir
from tests.runtime_fixture_provenance import _verified_test_runtime_archive

ROOT = Path(__file__).resolve().parents[1]


def admitted_runtime(explicit: str | Path | None = None) -> Path:
    if explicit is None:
        explicit = os.environ.get("PCC_RUNTIME_ARCHIVE")
    assert explicit, "whole-runtime control needs an explicitly admitted PCC_RUNTIME_ARCHIVE"
    archive, receipt = _verified_test_runtime_archive(explicit)
    assert all(member.get("object_emitter") == "pcc-self-backend-object-writer"
               and member.get("uses_host_cc") is False for member in receipt["members"])
    return archive


def runtime_member(directory: Path, module: str) -> Path:
    archive = admitted_runtime()
    name = module + ".o"
    matches = [payload for member, payload in read_archive_payloads(archive.read_bytes()) if member == name]
    assert len(matches) == 1, (name, len(matches))
    output = directory / name
    output.write_bytes(matches[0])
    return output


def runtime_ir(source: Path, output: Path, target: str | None = None) -> Path:
    target = target or host_target_triple()
    if not pipeline._is_py_runtime_library_source(str(source)):
        directory = output.parent / "py_runtime_owned_fixture/py"
        directory.mkdir(parents=True, exist_ok=True)
        local = directory / source.name
        local.write_bytes(source.read_bytes())
        source = local
    assert pipeline._is_py_runtime_library_source(str(source))
    assert pipeline._source_declares_freestanding_module(source.read_text())
    owned_runtime_build._compile_runtime_module(source.stem, str(source), str(output), target)
    output.write_text(optimize_ir(output.read_text(), owned_runtime_build.runtime_ir_passes(pipeline._PY_RUNTIME_DIR)))
    return output


def object_symbols(path: Path, *, undefined: bool = False):
    """Keep original inventory assertions, inspecting bytes with owned parsers."""
    data = path.read_bytes()
    rows = []
    if data.startswith(b"\x7fELF"):
        from pcc.backend.elf_x86_64 import parse_relocatable, STB_GLOBAL, STB_WEAK, STT_FUNC
        for symbol in parse_relocatable(data).symbols:
            if not symbol.name or symbol.binding not in (STB_GLOBAL, STB_WEAK):
                continue
            if symbol.section_index == 0:
                if undefined:
                    rows.append(symbol.name)
            elif not undefined:
                rows.append("0 " + ("T" if symbol.type == STT_FUNC else "D") + " " + symbol.name)
    elif data.startswith(b"\xcf\xfa\xed\xfe"):
        from pcc.backend import macho_spec as spec
        obj = spec.parse_object(data)
        for symbol in obj.symbols():
            flags = symbol["n_type"]
            if not flags & spec.N_EXT or flags & spec.N_STAB:
                continue
            kind = flags & spec.N_TYPE
            if kind == spec.N_UNDF:
                if undefined:
                    rows.append(symbol["name"])
            elif not undefined and kind in (spec.N_SECT, spec.N_ABS):
                sections = obj.sections()
                section = sections[symbol["n_sect"] - 1] if kind == spec.N_SECT else None
                is_code = section is not None and (section["flags"] & 0x80000000) != 0
                rows.append("0 " + ("T" if is_code else "D") + " " + symbol["name"])
    else:
        raise AssertionError("fixture needs a supported owned ELF or Mach-O object")
    return SimpleNamespace(returncode=0, stdout="\n".join(rows), stderr="")


def link_c_harness(
    harness: Path,
    output: Path,
    *,
    defines=(),
    extra_include_dirs=(),
    extra_sources=(),
    runtime_archive: str | Path | None = None,
):
    """Original C text -> owned frontend/emitter/linker -> real runtime closure."""
    archive = admitted_runtime(runtime_archive)
    target = host_target_triple()
    evaluator = CEvaluator(backend="self", target_triple=target)
    def forbidden(*args, **kwargs):
        raise AssertionError("owned C fixture attempted an external build process")
    # CEvaluator also admits an implicit runtime on Linux. Pin that admission
    # to this exact verified archive, including a caller's threaded override.
    # Restore the caller's environment even when compilation or linking fails.
    with patch.dict(os.environ, {"PCC_RUNTIME_ARCHIVE": str(archive)}), patch(
        "subprocess.Popen", forbidden,
    ):
        units = evaluator.compile_translation_units(
            [TranslationUnit(source.name, str(source), source.read_text())
             for source in (*extra_sources, harness)],
            use_system_cpp=False, use_compile_cache=False,
            include_dirs=[str(ROOT / "pcc/runtime/include"),
                          *extra_include_dirs, str(ROOT / "utils/fake_libc_include")],
            cpp_args=["-D" + value for value in defines],
        )
        # Darwin also needs the explicit runtime archive. Linux's implicit
        # runtime admission is the same path, and archive selection is exact.
        evaluator.emit_executable(units, str(output), link_args=[str(archive)])
    assert output.is_file()
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def link_c_harness_bounded(
    harness: Path,
    output: Path,
    *,
    runtime_archive: str | Path,
    extra_include_dirs=(),
    extra_sources=(),
    timeout: float = 60,
):
    """Keep a per-probe deadline while the child uses only the owned builder."""
    from tests.python.process_timeout import run_process_group_timeout

    payload = {
        "harness": str(harness.resolve()),
        "output": str(output.resolve()),
        "runtime_archive": str(Path(runtime_archive).resolve()),
        "extra_include_dirs": [str(Path(path).resolve()) for path in extra_include_dirs],
        "extra_sources": [str(path.resolve()) for path in extra_sources],
    }
    # This is the host-pcc0 test driver, not an external C compiler/linker.
    # The child re-admits the archive and forbids build subprocesses inside
    # link_c_harness. The supervisor cleans up its process group on timeout.
    return run_process_group_timeout(
        [sys.executable, "-m", "tests.owned_runtime_c_fixture", json.dumps(payload)],
        timeout=timeout,
        cwd=ROOT,
        stderr_path=Path(str(output) + ".compile.stderr"),
    )


def _link_c_harness_request(payload: str):
    arguments = json.loads(payload)
    arguments["harness"] = Path(arguments["harness"])
    arguments["output"] = Path(arguments["output"])
    arguments["extra_sources"] = tuple(Path(path) for path in arguments["extra_sources"])
    return link_c_harness(**arguments)


if __name__ == "__main__":
    _link_c_harness_request(sys.argv[1])
