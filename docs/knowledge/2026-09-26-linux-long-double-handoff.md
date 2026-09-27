# Linux long double: interrupted implementation, 2026-09-26

Source identity at this handoff: HEAD
`390fcac72bf231ff3e1b5ab7b34a910f1a414881`, with extensive staged,
unstaged and untracked shared work. HEAD alone does not identify these edits.
No commit, reset, restore, tests, compiler invocation, bootstrap or generated
program execution was performed by the long-double implementation task.

The user changed direction to validation and supplied a report that Mac GC0
Stage1→2→3 fixed point passed, GC1 Stage2 timed out, and GC1 investigation was
continuing. These are user-provided results, not independently verified here.
All three implementation agents stopped writing. Coordination is pending:
whether another task retains Mac validation or this task should take it over.
Other-platform validation remains ordered after the Mac work.

## Partial source changes

- `pcc/backend/wide_float.py`: new exact integer-rational literal/bit codec for
  16/32/64/80/128-bit floating representations. Not executed or integrated.
- `pcc/codegen/c_float_constants.py`: new typed exact constant wrapper;
  constant folding and initializers are not wired to it yet.
- `pcc/llvm_capi/ir.py`: X86FP80Type/FP128Type classes, constructors, exports and
  constant formatting. Native scaffold constructor handling remains unfinished.
- `pcc/c_abi_layout.py`, `pcc/codegen/c_layout.py`: partial wide float layout.
- `pcc/codegen/c_types.py`: selects wide Linux long-double types. This already
  exposes types whose operations and downstream backend support are unfinished.
- `pcc/backend/x86_64_encode.py`: finite x87 opcodes and TBYTE PTR support;
  no fp80 instruction-selector or SysV call/return integration yet.
- `pcc/backend/self_backend_aarch64_darwin_abi.py`,
  `self_backend_aarch64_darwin_slots.py`, `self_backend_aarch64_darwin_calls.py`,
  `self_backend_aarch64_linux.py`, and `arm64_encode.py`: partial q-register,
  memory, argument and va_arg support. Common TypeDesc still lacks fp128 layout.

These files also contain earlier platform/shared edits. Do not discard them
or revert entire files to recover the pre-long-double state.

## Remaining implementation

1. Common backend TypeDesc/parser/indexed/direct-indexed handling for fp80 and
   fp128, plus native scaffold constructors.
2. Exact C literals, constant expressions, conversions, SSA, initializers,
   preprocessing macros and float.h definitions.
3. Linux x64: fp80 materialization, arithmetic, conversions, comparisons,
   memory/phi/select, stack arguments and varargs, ST0 returns and aggregate ABI.
4. Linux arm64: quad materialization/ABI, scalar operations, conversions and
   helper-call register preservation. The owned quad runtime is not written.
5. Target-specific test source and, once execution is authorized and inputs
   frozen, validation of the actual emitted shape. No long-double test has run.

Planned quad helper ABI: pointer arguments `out,lhs,rhs` for
`__pcc_addtf3`, `__pcc_subtf3`, `__pcc_multf3`, `__pcc_divtf3`,
`__pcc_fmodtf3`; `__pcc_cmptf2(lhs,rhs)` returns -1/0/1/2(unordered).
Conversion helper names were coordinated but not implemented:
`__pcc_extendsftf2`, `__pcc_extenddftf2` (out, integer bits),
`__pcc_trunctfsf2`, `__pcc_trunctfdf2` (src -> integer bits),
`__pcc_floatsitf`, `__pcc_floatunsitf` (out, integer lane),
`__pcc_fixtfdi`, `__pcc_fixunstfdi` (src -> integer lane).

Static locations showing incompleteness can be inspected without executing
the compiler: compare the new types in `c_types.py`/`llvm_capi/ir.py` with
`self_backend_ir.py` floating size/align rules and `self_backend_parse.py`
floating instruction token patterns. Do not treat this partial tree as a
qualified long-double implementation or a frozen Mac measurement input.

## Selected SHA-256 identities

```text
cc28c3cbd0852b8c6e17ac941f6863dc0454660e17999145bcd966a84d0ffb47  pcc/backend/wide_float.py
ced50dbc643c24a3fc91c3548111323e33e041e58f66791473eb53e8a32e52ab  pcc/codegen/c_float_constants.py
9d61200054da3534c718c1496e0fa6af0a72b3d05dd430a7e30fae726b254399  pcc/codegen/c_types.py
e4865f73905d5fed5a8ac49a7bb07ef4a1c694811ab199e589533e9418ea749f  pcc/llvm_capi/ir.py
42acec5104fe4c289d0a308cba818d5e95ebf3550bb40133ac7ea4c2f10b420e  pcc/backend/self_backend_aarch64_linux.py
8fbd570d537c0db571b17ed9e2c8f1bb90099b38992d5e83962d94b98586013c  pcc/backend/x86_64_encode.py
```
