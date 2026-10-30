"""Test-only scalar ABI observer; never a compiler/source-language shortcut.

The owned assembly and independent reference TU provide ordinary extern
functions recording the last argument and call count. Source under test must
perform its own calls, casts, returns and control flow. It is never rewritten.
"""

DECLARATIONS = "void cpp_observe(int); int cpp_observed(); int cpp_count(); "
RESULT = "cpp_count()*1000+cpp_observed()"

OWNED_ASSEMBLY = """\
.intel_syntax noprefix
.data
.p2align 2
cpp_test_observer_value:
  .long 0
cpp_test_observer_count:
  .long 0
.text
.globl _Z11cpp_observei
_Z11cpp_observei:
  mov dword ptr cpp_test_observer_value[rip], edi
  mov eax, dword ptr cpp_test_observer_count[rip]
  add eax, 1
  mov dword ptr cpp_test_observer_count[rip], eax
  ret
.globl _Z12cpp_observedv
_Z12cpp_observedv:
  mov eax, dword ptr cpp_test_observer_value[rip]
  ret
.globl _Z9cpp_countv
_Z9cpp_countv:
  mov eax, dword ptr cpp_test_observer_count[rip]
  ret
"""

REFERENCE_UNIT = (
    "pcc_abi_observer.cpp",
    "static int observed_value; static int observation_count;\n"
    "void cpp_observe(int value) {\n"
    "  observed_value = value; observation_count = observation_count + 1;\n"
    "}\n"
    "int cpp_observed() { return observed_value; }\n"
    "int cpp_count() { return observation_count; }\n",
)
