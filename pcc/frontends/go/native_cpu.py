"""Explicit Linux CPUID body for the exact locked Go stdlib inputs.

This builds an owned object, never an upstream archive. It translates the
known ABI0 frame result slots to PCC's SysV register pairs. The source
package compiler must still authorize a native owner before consuming it.
"""
import hashlib

from pcc.backend.elf_x86_64 import emit_relocatable
from pcc.backend.x86_64_asm_driver import assemble_file
from pcc.frontends.go.lowering import function_symbol
from pcc.frontends.go.records import NativeFunctionBinding
from pcc.frontends.go.source_plan import GoSourcePlanError, TRIPLE


CPUID_DECLARATIONS_SHA256 = "395ce0d3004df63df376585519bc1b46303b206415c1915ab80b7bead155e26a"
CPUID_ASSEMBLY_SHA256 = "ee4dca9916b29475e600d85f71f0dfd134496d153c77d20a643b139cc6e18fed"
CPUID_PACKAGE = "internal/cpu"


def emit_locked_cpuid(declarations, assembly, *, target):
    """Return one isolated owned object for the original CPUID declaration."""
    if target != TRIPLE:
        raise GoSourcePlanError("Go CPUID native owner is Linux/amd64 only")
    if (hashlib.sha256(declarations).hexdigest() != CPUID_DECLARATIONS_SHA256
            or hashlib.sha256(assembly).hexdigest() != CPUID_ASSEMBLY_SHA256):
        raise GoSourcePlanError("Go CPUID original declaration/body identity differs")
    # Go ABI0 receives its two words at FP+0/4 and writes EAX, EBX, ECX,
    # EDX at FP+8/12/16/20. PCC's qualified [i32]*4 result instead packs
    # adjacent words into RAX and RDX. RBX is callee-saved under SysV.
    name = function_symbol("cpuid", CPUID_PACKAGE)
    return assemble_file(f""".intel_syntax noprefix
.text
.globl {name}
.type {name}, @function
{name}:
    push rbx
    mov eax, edi
    mov ecx, esi
    cpuid
    mov r9d, eax
    mov eax, ebx
    shl rax, 32
    or rax, r9
    mov r9d, ecx
    shl rdx, 32
    or rdx, r9
    pop rbx
    ret
.size {name}, .-{name}
""")


def source_cpuid_binding(declarations, assembly, *, target):
    """Prove the source has a real owned object, without linking it."""
    data = emit_relocatable(emit_locked_cpuid(declarations, assembly, target=target))
    return NativeFunctionBinding(function_symbol("cpuid", CPUID_PACKAGE), target,
                                 ("uint32",) * 2, ("uint32",) * 4,
                                 hashlib.sha256(data).hexdigest())


def cpuid_binding_from_plan(plan):
    """Use exactly the files selected for the normal package in the plan."""
    package = plan.packages[CPUID_PACKAGE]
    source = "src/internal/cpu/cpu_x86.go"
    assembly = "src/internal/cpu/cpu_x86.s"
    if source not in package.go or assembly not in package.assembly:
        raise GoSourcePlanError("Go CPUID declaration or body is absent from the locked selection")
    return source_cpuid_binding(plan.original_files[source], plan.original_files[assembly], target=plan.target)
