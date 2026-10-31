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


def compile_owned(units, *, observe_result=False, observe_entry=None):
    return compile_module(compile_go_sources(units, target_triple=DEFAULT_TARGET),
                          observe_result=observe_result, observe_entry=observe_entry)


def compile_module(module, *, observe_result=False, observe_entry=None):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file

    if observe_result and observe_entry is not None:
        raise ValueError("select one Go ABI observer mode")
    entry = function_symbol("Result") if observe_result else GO_MAIN_SYMBOL
    arguments, output_bytes = (), 8 if observe_result else 0
    if observe_entry is not None:
        name, arguments = observe_entry
        entry = function_symbol(name)
    if observe_result or observe_entry is not None:
        result = module.globals.get(entry)
        width = getattr(result.function_type.return_type, "width", 0) if result else 0
        if result is None or width not in (8, 16, 32, 64) or len(result.args) != len(arguments):
            raise ValueError("Go observer requires a fixed-width scalar result and matching integer arguments")
        if observe_result and (width != 64 or arguments):
            raise ValueError("Go Result observer requires an int64 result without arguments")
        if len(arguments) > 6 or any(getattr(arg.type, "width", 0) not in (8, 16, 32, 64) for arg in result.args):
            raise ValueError("Go scalar test observer admits at most six integer arguments")
        output_bytes = width // 8
    ir_text = str(module)
    obj = emit_owned_object(ir_text, DEFAULT_TARGET)
    assembly = ".intel_syntax noprefix\n.text\n.globl _start\n_start:\n  sub rsp, 16\n"
    for register, argument in zip(("rdi", "rsi", "rdx", "rcx", "r8", "r9"), arguments):
        # Argument setup conveys bits only. It does not implement any Go
        # conversion; the original typed function performs those operations.
        bits = int(argument) & ((1 << 64) - 1)
        signed_bits = bits if bits < (1 << 63) else bits - (1 << 64)
        assembly += f"  mov {register}, {signed_bits}\n"
    assembly += f"  call {entry}\n"
    if output_bytes:
        assembly += (
            "  mov qword ptr [rsp], rax\n"
            f"  mov rax, 1\n  mov rdi, 1\n  mov rsi, rsp\n  mov rdx, {output_bytes}\n  syscall\n"
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
