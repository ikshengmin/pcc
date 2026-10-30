"""Small C++ test adapters, with execution owners kept deliberately separate.

The host adapter lowers each unmodified translation unit through CppParser,
CCodeGenerator, the owned object emitter and the owned ELF linker. It is NOT a
native pcc1 invocation. The startup shim only observes the returned int; it
does not implement or interpret any source-language operation.

The reference adapter explicitly invokes the requested external C++ compiler.
Neither adapter is a fallback for the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

TARGET = "x86_64-unknown-linux-gnu"
ENTRY = "cpp_test_entry"


@dataclass(frozen=True)
class Execution:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class HostArtifact:
    ir: tuple[str, ...]
    objects: tuple[bytes, ...]
    executable: bytes
    owner: str = "host-python/pcc-cpp/pcc-self/pcc-elf"


def assert_exact(actual: Execution, expected: Execution) -> None:
    """No decoding, newline normalization, or returncode-only success bucket."""
    for field in ("returncode", "stdout", "stderr"):
        assert getattr(actual, field) == getattr(expected, field), (
            f"{field} mismatch: actual={getattr(actual, field)!r}, "
            f"expected={getattr(expected, field)!r}"
        )


def run_executable(binary: Path, *, timeout: float = 10) -> Execution:
    result = subprocess.run(
        [str(binary)],
        capture_output=True,
        text=False,
        timeout=timeout,
        cwd=binary.parent,
    )
    return Execution(result.returncode, result.stdout, result.stderr)


def entry_definition(symbol) -> str | None:
    """Accept a single function or a semantic overload set, never guess a call."""
    if symbol is None:
        return None
    definitions = [
        item for item in getattr(symbol, "overloads", (symbol,)) if item.defined
    ]
    matches = [
        item
        for item in definitions
        if item.kind == "function"
        and item.arity == 0
        and getattr(item, "dtype", "int") == "int"
    ]
    if definitions and len(matches) != 1:
        raise ValueError("C++ test entry must be an int function with no parameters")
    return matches[0].c_name if matches else None


def compile_host_cpp(units: tuple[tuple[str, str], ...]) -> HostArtifact:
    """Compile separate TUs; fail before publication at every missing boundary.

    The corpus defines ``int cpp_test_entry()``. Its int result is written as
    four little-endian bytes to stdout and is also the process exit status
    (low eight bits on Linux). This exposes more than an exit-code-only test.
    No libc, C++ runtime, host assembler, linker, or external compiler is used.
    """
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
    from pcc.frontends.c.passes.context import PassContext
    from pcc.frontends.cpp import CppParser

    if not units:
        raise ValueError("C++ project has no translation units")
    if len({name for name, _source in units}) != len(units):
        raise ValueError("duplicate C++ translation-unit names")
    ir_texts = []
    object_bytes = []
    entry_symbol = None
    for filename, source in units:
        parser = CppParser()
        ast = parser.parse(source, filename)
        selected_entry = entry_definition(parser.scope.lookup(ENTRY))
        if selected_entry is not None:
            if entry_symbol is not None:
                raise ValueError("C++ project defines its entry more than once")
            entry_symbol = selected_entry
        generator = CCodeGenerator(
            translation_unit_name=filename,
            pass_ctx=PassContext(opt_level=0, target_triple=TARGET),
        )
        generator.generate_code(ast)
        ir_text = str(generator.module)
        ir_texts.append(ir_text)
        object_bytes.append(emit_owned_object(ir_text, TARGET))
    if entry_symbol is None:
        raise ValueError("C++ project does not define int cpp_test_entry()")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", entry_symbol):
        raise ValueError("C++ test entry has an invalid assembly symbol")
    # Linux starts with 16-byte aligned rsp; retain SysV call alignment. The
    # source result survives write's use of rax/rcx/r11 in the local stack slot.
    startup = assemble_file(
        ".intel_syntax noprefix\n.text\n.globl _start\n_start:\n"
        "  sub rsp, 16\n"
        f"  call {entry_symbol}\n"
        "  mov dword ptr [rsp], eax\n"
        "  mov rax, 1\n"
        "  mov rdi, 1\n"
        "  mov rsi, rsp\n"
        "  mov rdx, 4\n"
        "  syscall\n"
        "  mov edi, dword ptr [rsp]\n"
        "  mov rax, 60\n"
        "  syscall\n"
    )
    executable = link_static_executable(
        [startup, *(parse_relocatable(data) for data in object_bytes)],
        entry="_start",
    )
    return HostArtifact(tuple(ir_texts), tuple(object_bytes), executable)


def write_host_executable(artifact: HostArtifact, destination: Path) -> None:
    """Publish only a completed compile; no stale executable after a rejection."""
    with destination.open("xb") as handle:
        handle.write(artifact.executable)
    destination.chmod(0o700)


def compile_reference_cpp(
    units: tuple[tuple[str, str], ...],
    directory: Path,
    *,
    compiler: str,
    timeout: float = 30,
) -> Path:
    """Development oracle only: same source bytes, explicit C++17 options."""
    if not units:
        raise ValueError("C++ project has no translation units")
    directory.mkdir()
    paths = []
    for filename, source in units:
        path = directory / filename
        if Path(filename).name != filename:
            raise ValueError("reference fixture names must be plain basenames")
        with path.open("x", encoding="utf-8", newline="") as handle:
            handle.write(source)
        paths.append(str(path))
    driver = directory / "pcc_test_observer.cpp"
    driver.write_text(
        "#include <cstdio>\n"
        "int cpp_test_entry();\n"
        "int main() {\n"
        "  int result = cpp_test_entry();\n"
        "  unsigned value = static_cast<unsigned>(result);\n"
        "  unsigned char bytes[4];\n"
        "  for (unsigned i = 0; i < 4; ++i) bytes[i] = value >> (8 * i);\n"
        "  if (std::fwrite(bytes, 1, 4, stdout) != 4) return 125;\n"
        "  return result;\n"
        "}\n",
        encoding="utf-8",
    )
    binary = directory / "oracle"
    result = subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-pedantic-errors",
            "-O0",
            *paths,
            str(driver),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=False,
        timeout=timeout,
        cwd=directory,
    )
    assert result.returncode == 0, (
        f"explicit C++17 reference compilation failed ({result.returncode}): "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert binary.is_file(), "reference compiler did not produce an executable"
    return binary
