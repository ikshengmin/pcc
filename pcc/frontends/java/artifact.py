"""Java compilation artifacts and owned target emission (source-independent)."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from pcc.ir.compat import ir
from .diagnostics import UnsupportedJava


def method_symbol(class_name: str, name: str, descriptor: str) -> str:
    """Injective symbol encoding; independent of host hash randomization."""
    return "pcc_java_" + "_".join(value.encode("utf-8", "surrogatepass").hex()
                                 for value in (class_name, name, descriptor))


@dataclass(frozen=True)
class JavaCompilation:
    module: ir.Module
    class_name: str
    entry: tuple[str, str]
    admitted_methods: tuple[tuple[str, str], ...]
    # Source projects retain class-qualified identities; compatibility callers
    # keep their existing four-argument construction and single-class field.
    emitted_class_methods: tuple[tuple[str, str, str], ...] = ()

    @property
    def entry_symbol(self) -> str:
        return method_symbol(self.class_name, *self.entry)

    def emit_object(self) -> bytes:
        from pcc.backend.owned_object_emit import emit_owned_object

        return emit_owned_object(str(self.module), self.module.triple)

    def write_executable(self, output: str | Path) -> Path:
        """Owned Linux ELF link for an explicit ``()I`` process-exit entry.

        This is not Java's ``main(String[])`` protocol. There is no JVM or C
        runtime in the resulting tiny executable; the OS exit status is the
        low eight bits of the selected method's int result.
        """
        from pcc.backend.owned_elf_link import link_inputs
        from pcc.backend.self_backend_target_match import (
            is_aarch64_linux_triple,
            is_x86_64_linux_triple,
        )

        if self.entry[1] != "()I":
            raise UnsupportedJava("executable entry must have descriptor ()I", code="java.unsupported.entry-descriptor")
        target = self.module.triple
        if is_x86_64_linux_triple(target):
            start = (".intel_syntax noprefix\n.text\n.globl _start\n_start:\n"
                     "  and rsp, -16\n  call " + self.entry_symbol
                     + "\n  mov edi, eax\n  mov eax, 60\n  syscall\n")
        elif is_aarch64_linux_triple(target):
            start = (".text\n.globl _start\n_start:\n  bl " + self.entry_symbol
                     + "\n  mov x8, #93\n  svc #0\n")
        else:
            raise UnsupportedJava("Java executable startup is currently limited to Linux ELF targets", code="java.unsupported.executable-target")
        output = Path(output).absolute()
        with TemporaryDirectory(prefix="pcc-java-") as directory:
            obj = Path(directory) / "methods.o"
            startup = Path(directory) / "start.s"
            obj.write_bytes(self.emit_object())
            startup.write_text(start, encoding="utf-8")
            link_inputs(target=target, output=str(output), objects=[str(obj)], assembly=[str(startup)])
        return output
