"""Test-only int ABI observer for exact per-call bytes, not language lowering."""

DECLARATION = "void cpp_emit(int);\n"

OWNED_ASSEMBLY = """\
.intel_syntax noprefix
.text
.globl _Z8cpp_emiti
_Z8cpp_emiti:
  sub rsp, 16
  mov dword ptr [rsp], edi
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 4
  syscall
  cmp rax, 4
  jne cpp_test_emit_failure
  add rsp, 16
  ret
cpp_test_emit_failure:
  mov eax, 60
  mov edi, 125
  syscall
"""

REFERENCE_UNIT = (
    "pcc_abi_stream.cpp",
    "#include <cstdio>\n#include <cstdlib>\n"
    "void cpp_emit(int value) {\n"
    "  unsigned int bits = static_cast<unsigned int>(value);\n"
    "  unsigned char bytes[4];\n"
    "  for (unsigned i = 0; i < 4; ++i) bytes[i] = bits >> (8*i);\n"
    "  if (std::fwrite(bytes, 1, 4, stdout) != 4) std::exit(125);\n"
    "}\n",
)
