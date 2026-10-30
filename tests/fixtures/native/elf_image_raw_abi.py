"""Stdlib host-pcc0 fixture driver for the Linux x86-64 raw mapper ABI gate.

Run only through test_native_linux_elf_image_raw_abi.py's existing watchdog.
The build process cannot launch external tools; target programs are actual
owned static ELF images, never Python imports or modeled memory/syscalls.
"""

import argparse
import builtins
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent
RUNTIME = ROOT / "pcc/runtime"
MAPPER = RUNTIME / "py/freestanding_linux_elf_image.py"
TARGET = "x86_64-unknown-linux-gnu"
EXPORTS = ("extension_entry", "extension_bump", "extension_counter", "extension_zero")
EXPECTED_OUTPUTS = {
    "mapper-host": b"map-call-data-close result=42 data=16 bss=9\nmalformed status=1 null-owner\n",
    "mapper-empty-host": b"missing-host status=3 null-owner\n",
}


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def _same_inputs(inputs):
    assert all(_digest(path) == digest for path, digest in inputs.items()), "fixture inputs changed"


def _deny_external_build_tools():
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        if name == "llvmlite" or name.startswith("llvmlite."):
            raise AssertionError("owned raw-ABI fixture imported " + name)
        return original_import(name, *args, **kwargs)

    def forbidden_process(*args, **kwargs):
        raise AssertionError("owned raw-ABI build attempted an external process")

    builtins.__import__ = checked_import
    subprocess.Popen = forbidden_process


def build(directory):
    assert sys.implementation.name == "cpython", "this is the explicitly host-pcc0 fixture"
    selected = os.environ.get("PCC_RUNTIME_ARCHIVE")
    assert selected, "set PCC_RUNTIME_ARCHIVE to a source-matched owned archive; no provisioning"
    directory.mkdir(exist_ok=False)
    _deny_external_build_tools()

    from tests.runtime_fixture_provenance import _verified_test_runtime_archive
    from pcc.backend import elf_x86_64 as elf
    from pcc.backend.elf_extension import link_relocatable_extension, parse_relocatable_extension
    from pcc.backend.owned_elf_link import link_inputs
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from pcc.frontends.python import owned_runtime_build
    from pcc.tools.runtime_archive_provenance import codegen_checksum

    archive, manifest = _verified_test_runtime_archive(selected, threads=True, runtime_root=RUNTIME)
    sources = {"extension": FIXTURES / "elf_image_extension.c", "host": FIXTURES / "elf_image_host.c"}
    inputs = {str(path): _digest(path) for path in (
        MAPPER, Path(__file__), *sources.values(), archive,
        Path(str(archive) + ".provenance.json"), Path(str(archive) + ".capi_syms"),
    )}
    config = owned_runtime_build.runtime_build_config()
    receipt = {
        "status": "BUILDING", "compiler_owner": "host-pcc0", "target": TARGET,
        "codegen_checksum": codegen_checksum(), "inputs": inputs,
        "runtime_archive": str(archive), "runtime_members": manifest["member_count"],
        "runtime_config": config,
        "claim": "native raw ABI only; not pcc1, managed import, NumPy, public dlopen, leaks or five-GC",
    }
    _write_json(directory / "build.json", receipt)
    print("source-matched owned runtime admitted; compiling raw mapper", flush=True)
    owned_runtime_build._build_runtime_module(
        str(RUNTIME), MAPPER.stem, TARGET, str(directory),
        owned_runtime_build.runtime_ir_passes(str(RUNTIME)), config,
    )
    mapper = directory / (MAPPER.stem + ".o")
    ir = mapper.with_suffix(".ll").read_text()
    for signature in ("i64 @pcc_linux_elf_image_open(ptr ", "ptr @pcc_linux_elf_image_symbol(ptr ",
                      "i64 @pcc_linux_elf_image_close(ptr "):
        assert signature in ir, signature

    evaluator = CEvaluator(backend="self", target_triple=TARGET)
    for name, source in sources.items():
        units = evaluator.compile_translation_units(
            [TranslationUnit(source.name, str(source), source.read_text())],
            base_dir=str(FIXTURES), use_system_cpp=False, use_compile_cache=False,
            frontend_opt_level=0, jobs=1, cpp_args=["-nostdinc"],
        )
        assert len(units) == 1
        (directory / (name + ".ll")).write_text(units[0][1])
        (directory / (name + ".o")).write_bytes(emit_owned_object(units[0][1], TARGET))
    extension = link_relocatable_extension(
        [elf.parse_relocatable((directory / "extension.o").read_bytes())],
        export_symbols=EXPORTS, host_import_symbols=("host_add",),
    )
    parsed = parse_relocatable_extension(extension, host_import_symbols=("host_add",))
    assert parsed.export_symbols == EXPORTS and parsed.host_import_symbols == ("host_add",)
    (directory / "extension.pcc-elf").write_bytes(extension)
    malformed = bytearray(extension)
    malformed[0] = 0
    (directory / "malformed.pcc-elf").write_bytes(malformed)

    # The reserved undefined anchor in host.o requests the real linker table.
    # The empty inventory deliberately tests missing host ownership, not a fake
    # in-memory table or a process-global symbol lookup.
    for name, exports in (("mapper-host", ("host_add",)), ("mapper-empty-host", ())):
        output = directory / name
        link_inputs(
            target=TARGET, output=str(output), objects=(str(directory / "host.o"), str(mapper)),
            archives=(str(archive),), export_symbols=exports, map_path=str(output) + ".map",
        )
        data = output.read_bytes()
        elf.parse_static_executable(data)
        header = elf._ELF_HEADER.unpack_from(data)
        kinds = [elf._PROGRAM_HEADER.unpack_from(data, header[5] + index * header[9])[0]
                 for index in range(header[10])]
        assert all(kind in (elf.PT_LOAD, elf.PT_TLS) for kind in kinds), kinds
    _same_inputs(inputs)
    receipt.update(status="BUILT", artifacts={path.name: _digest(path) for path in directory.iterdir()
                                              if path.is_file() and path.name != "build.json"})
    _write_json(directory / "build.json", receipt)
    print("owned raw mapper, real C ET_REL and static host binaries built", flush=True)


def execute(directory):
    receipt = json.loads((directory / "build.json").read_text())
    assert receipt["status"] == "BUILT"
    _same_inputs(receipt["inputs"])
    assert all(_digest(directory / name) == digest for name, digest in receipt["artifacts"].items())
    environment = dict(os.environ, PATH="", PCC_HOST_PYTHON="/nonexistent/host-python")
    results = []
    deadline = time.monotonic() + 30
    for name, arguments in (
        ("mapper-host", ("extension.pcc-elf", "malformed.pcc-elf")),
        ("mapper-empty-host", ("extension.pcc-elf",)),
    ):
        command = [str(directory / name), *(str(directory / argument) for argument in arguments)]
        remaining = deadline - time.monotonic()
        assert remaining > 0, "native aggregate deadline expired"
        # The enclosing watchdog owns the whole execution process tree and
        # aggregate 30s/4GiB bound; each target also has the original 10s bound.
        stdout_path = directory / (name + ".stdout.bin")
        stderr_path = directory / (name + ".stderr.bin")
        record = {"command": command, "status": "RUNNING", "target_path": environment["PATH"],
                  "binary_sha256": _digest(directory / name), "stdout_file": str(stdout_path),
                  "stderr_file": str(stderr_path)}
        results.append(record)
        _write_json(directory / "execution.json", {"status": "RUNNING", "results": results})
        # Direct sinks retain partial bytes even if the aggregate guard kills
        # this driver before subprocess.run returns. The RUNNING row above
        # also retains the exact target identity in that case.
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                result = subprocess.run(command, cwd=directory, env=environment, stdout=stdout, stderr=stderr,
                                        timeout=min(10, remaining))
        except BaseException as error:
            record.update(status="TIMEOUT" if isinstance(error, subprocess.TimeoutExpired) else "FAILED",
                          error=type(error).__name__ + ": " + str(error),
                          stdout_hex=stdout_path.read_bytes().hex(), stderr_hex=stderr_path.read_bytes().hex())
            _write_json(directory / "execution.json", {"status": "FAILED", "results": results})
            raise
        stdout_bytes, stderr_bytes = stdout_path.read_bytes(), stderr_path.read_bytes()
        record.update(status="COMPLETE", returncode=result.returncode,
                      stdout_hex=stdout_bytes.hex(), stderr_hex=stderr_bytes.hex())
        actual = (result.returncode, stdout_bytes, stderr_bytes)
        matches = actual == (0, EXPECTED_OUTPUTS[name], b"")
        _write_json(directory / "execution.json", {"status": "RUNNING" if matches else "FAILED", "results": results})
        assert matches, record
    _same_inputs(receipt["inputs"])
    _write_json(directory / "execution.json", {"status": "PASS", "results": results})
    print("native map-call-close, data/BSS and fail-closed controls passed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("build", "execute"))
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    {"build": build, "execute": execute}[arguments.phase](arguments.directory.resolve())
