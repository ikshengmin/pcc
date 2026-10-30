import os
import sys

import pytest

from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.driver.project import collect_translation_units, translation_unit_include_dirs
from tests.parallel_jobs import translation_unit_jobs

PROJECT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
PROJECTS_DIR = os.path.join(PROJECT_DIR, "projects")
ZLIB_DIR = os.path.join(PROJECTS_DIR, "zlib-1.3.1")
ZLIB_TEST_MAIN = os.path.join(PROJECTS_DIR, "test_zlib_main.c")
ZLIB_CPP_ARGS = (
    "-DHAVE_UNISTD_H",
    "-DHAVE_STDARG_H",
    "-U__ARM_FEATURE_CRC32",
)

pytestmark = pytest.mark.xdist_group(name="vendor_builds")


def _zlib_units():
    return collect_translation_units(
        ZLIB_TEST_MAIN,
        dependencies=[f"{ZLIB_DIR}=libz.a"],
    )


@pytest.fixture(scope="module")
def zlib_compiled_units():
    units, base_dir = _zlib_units()
    compiled_units = CEvaluator().compile_translation_units(
        units,
        base_dir=base_dir,
        jobs=translation_unit_jobs(),
        include_dirs=translation_unit_include_dirs(units),
        cpp_args=ZLIB_CPP_ARGS,
    )
    return compiled_units, base_dir


@pytest.fixture(scope="module")
def zlib_compiled_units_self():
    units, base_dir = _zlib_units()
    compiled_units = CEvaluator(
        backend="self",
        allow_unimplemented_backend=True,
    ).compile_translation_units(
        units,
        base_dir=base_dir,
        jobs=translation_unit_jobs(),
        include_dirs=translation_unit_include_dirs(units),
        cpp_args=ZLIB_CPP_ARGS,
    )
    return compiled_units, base_dir


@pytest.mark.pcc_gate(unavailable=None if os.path.isdir(ZLIB_DIR) else "zlib-1.3.1 not found")
def test_zlib_make_goal_dependency_collects_library_sources():
    units, base_dir = _zlib_units()

    names = [unit.name for unit in units]

    assert base_dir == os.path.abspath(PROJECTS_DIR)
    assert names[-1] == "test_zlib_main.c"
    assert "deflate.c" in names
    assert "inflate.c" in names
    assert "zutil.c" in names
    assert "example.c" not in names
    assert "minigzip.c" not in names


@pytest.mark.pcc_gate(unavailable=None if os.path.isdir(ZLIB_DIR) else "zlib-1.3.1 not found")
def test_zlib_runtime_with_mcjit_depends_on(zlib_compiled_units):
    compiled_units, _base_dir = zlib_compiled_units

    result = CEvaluator().evaluate_compiled_translation_units(
        compiled_units,
        optimize=True,
    )

    assert result == 0


@pytest.mark.pcc_gate(unavailable=None if os.path.isdir(ZLIB_DIR) else "zlib-1.3.1 not found")
def test_zlib_runtime_with_system_link_depends_on(zlib_compiled_units):
    compiled_units, base_dir = zlib_compiled_units

    result = CEvaluator().run_compiled_translation_units_with_system_cc(
        compiled_units,
        optimize=True,
        base_dir=base_dir,
    )

    assert (
        result.returncode == 0
    ), f"zlib system-link runtime failed:\n{result.stdout}\n{result.stderr}"
    assert "compress/uncompress: hello, hello!" in result.stdout
    assert "deflate/inflate: hello, hello!" in result.stdout
    assert "OK" in result.stdout


@pytest.mark.pcc_gate(unavailable=None if os.path.isdir(ZLIB_DIR) else "zlib-1.3.1 not found")
def test_zlib_runtime_with_self_backend_system_link_depends_on(
    zlib_compiled_units_self,
    monkeypatch,
):
    import pcc.frontends.c.evaluator.c_evaluator as c_evaluator

    compiled_units, base_dir = zlib_compiled_units_self
    emitter_calls = []
    original_emit_self_asm = c_evaluator.emit_self_asm

    def recording_emit_self_asm(ir_text):
        emitter_calls.append(ir_text)
        return original_emit_self_asm(ir_text)

    monkeypatch.setattr(c_evaluator, "emit_self_asm", recording_emit_self_asm)

    result = CEvaluator(
        backend="self",
        allow_unimplemented_backend=True,
    ).run_compiled_translation_units_with_system_cc(
        compiled_units,
        optimize=True,
        base_dir=base_dir,
    )

    assert (
        result.returncode == 0
    ), f"zlib self backend system-link runtime failed:\n{result.stdout}\n{result.stderr}"
    assert "compress/uncompress: hello, hello!" in result.stdout
    assert "deflate/inflate: hello, hello!" in result.stdout
    assert "OK" in result.stdout
    assert len(emitter_calls) == len(compiled_units)


@pytest.mark.pcc_gate(unavailable=None if os.path.isdir(ZLIB_DIR) else "zlib-1.3.1 not found")
def test_zlib_runtime_with_self_backend_freestanding_libc(
    zlib_compiled_units_self,
    tmp_path,
    monkeypatch,
):
    # Availability and extraction are different archive contracts: a valid
    # zlib program need not reference the constant-null-callee trap. Inspect
    # its real provider before the evaluator removes the temporary archive.
    provider_witnesses = []
    original_link_inputs = CEvaluator._freestanding_link_inputs

    def observe_link_inputs(evaluator, *args, **kwargs):
        inputs = original_link_inputs(evaluator, *args, **kwargs)
        triple = str(evaluator.target_triple or "").lower()
        if kwargs.get("enabled") and "linux" in triple and (
            triple.startswith("x86_64") or triple.startswith("amd64")
        ):
            from pcc.backend.elf_x86_64 import EM_X86_64
            from tests.freestanding_archive_provider import require_elf_archive_function

            archives = inputs[2]
            assert len(archives) == 1
            assert os.path.basename(archives[0]) == "libpcc_freestanding_c.a"
            provider_witnesses.append(require_elf_archive_function(
                archives[0], member_name="freestanding_runtime_traps.o",
                symbol_name="pcc_null_callee_trap", machine=EM_X86_64,
            ))
        return inputs

    monkeypatch.setattr(CEvaluator, "_freestanding_link_inputs", observe_link_inputs)
    compiled_units, base_dir = zlib_compiled_units_self
    link_map = tmp_path / "zlib-freestanding.map"
    map_flag = (
        "-Wl,-map," + str(link_map)
        if sys.platform == "darwin"
        else "-Wl,-Map," + str(link_map)
    )

    result = CEvaluator(
        backend="self",
        allow_unimplemented_backend=True,
    ).run_compiled_translation_units_with_system_cc(
        compiled_units,
        optimize=True,
        base_dir=base_dir,
        link_args=[map_flag],
        freestanding_libc=True,
    )

    assert result.returncode == 0, (
        "zlib freestanding-libc runtime failed:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "compress/uncompress: hello, hello!" in result.stdout
    assert "deflate/inflate: hello, hello!" in result.stdout
    assert "OK" in result.stdout
    ownership = link_map.read_text(encoding="utf-8")
    assert "freestanding_mem_str.o" in ownership
    assert "freestanding_allocator.o" in ownership
    assert "vendor_" not in ownership
    if sys.platform.startswith("linux"):
        assert "libpcc_freestanding_c.a(freestanding_linux_libc.o)" in ownership
        assert "libpcc_freestanding_c.a(freestanding_errno.o)" in ownership
        assert "libpcc_freestanding_c.a(freestanding_gc_state.o)" in ownership
        assert len(provider_witnesses) == 1
        assert "libpy_runtime_pcc_py.a" not in ownership
        assert "libc.a" not in ownership
