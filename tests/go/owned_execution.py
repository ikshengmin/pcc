"""Original Go source -> PCC IR -> self backend -> owned Linux ELF execution.

Host Python runs the compiler. This is not native pcc1 qualification. The
startup contains no source-language semantics; it calls main or observes the
full int64 Result function. The optional official Go oracle is never a fallback.
"""

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess

from pcc.frontends.go import DEFAULT_TARGET, GO_MAIN_SYMBOL, compile_go_sources, function_symbol


@dataclass(frozen=True)
class Artifact:
    ir: str
    object: bytes
    executable: bytes
    owner: str = "host-python/pcc-go/pcc-self/pcc-elf"


@dataclass(frozen=True)
class Execution:
    returncode: int
    stdout: bytes
    stderr: bytes


def compile_owned(units, *, observe_result=False):
    return compile_module(compile_go_sources(units, target_triple=DEFAULT_TARGET),
                          observe_result=observe_result)


def compile_module(module, *, observe_result=False):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file

    entry = function_symbol("Result") if observe_result else GO_MAIN_SYMBOL
    if observe_result:
        result = module.globals.get(entry)
        if result is None or str(result.function_type.return_type) != "i64" or result.args:
            raise ValueError("Go observer requires func Result() int or int64")
    ir_text = str(module)
    obj = emit_owned_object(ir_text, DEFAULT_TARGET)
    assembly = ".intel_syntax noprefix\n.text\n.globl _start\n_start:\n  sub rsp, 16\n"
    assembly += f"  call {entry}\n"
    if observe_result:
        assembly += (
            "  mov qword ptr [rsp], rax\n"
            "  mov rax, 1\n  mov rdi, 1\n  mov rsi, rsp\n  mov rdx, 8\n  syscall\n"
        )
    assembly += "  mov rax, 60\n  mov rdi, 0\n  syscall\n"
    executable = link_static_executable([assemble_file(assembly), parse_relocatable(obj)], entry="_start")
    return Artifact(ir_text, obj, executable)


def write_artifact(artifact, destination):
    with destination.open("xb") as stream:
        stream.write(artifact.executable)
    destination.chmod(0o700)


def run(binary):
    result = subprocess.run([str(binary)], capture_output=True, timeout=5, cwd=binary.parent)
    return Execution(result.returncode, result.stdout, result.stderr)


def compile_reference(units, directory, *, compiler, cache):
    """Explicit official Go development oracle, receiving identical source bytes."""
    directory.mkdir()
    paths = []
    for filename, source in units:
        if Path(filename).name != filename:
            raise ValueError("oracle source names must be basenames")
        path = directory / filename
        path.write_bytes(source.encode("utf-8"))
        paths.append(str(path))
    env = dict(os.environ, GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off",
               GOWORK="off", GO111MODULE="off", CGO_ENABLED="0", GOCACHE=str(cache),
               GOMAXPROCS="2")
    binary = directory / "oracle"
    result = subprocess.run([compiler, "build", "-p=2", "-o", str(binary), *paths],
                            capture_output=True, timeout=60, cwd=directory, env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert binary.is_file()
    return binary


def compile_reference_packages(packages, directory, *, compiler, cache):
    """Official Go oracle for an unchanged explicit package graph (test only)."""
    directory.mkdir()
    gopath = directory / "gopath"
    main_paths = []
    for import_path, source_units in packages.items():
        parts = import_path.split("/")
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise ValueError("oracle import paths must be relative package paths")
        folder = gopath / "src" / import_path
        folder.mkdir(parents=True)
        for filename, source in source_units:
            if Path(filename).name != filename:
                raise ValueError("oracle source names must be basenames")
            path = folder / filename
            path.write_bytes(source.encode("utf-8"))
            if import_path == "main":
                main_paths.append(str(path))
    env = dict(os.environ, GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off",
               GOWORK="off", GO111MODULE="off", CGO_ENABLED="0", GOCACHE=str(cache),
               GOPATH=str(gopath), GOMAXPROCS="2")
    binary = directory / "oracle"
    result = subprocess.run([compiler, "build", "-p=2", "-o", str(binary), *main_paths],
                            capture_output=True, timeout=60, cwd=directory, env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert binary.is_file()
    return binary
