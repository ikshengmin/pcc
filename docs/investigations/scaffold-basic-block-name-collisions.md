# Investigation: scaffold block creation duplicates C control-flow labels

## Status

Resolved locally on 2026-09-16. Native emitted execution is verified; the
420-second cold-build gate and broader toolchain qualification remain open.
This closes the C probe symptom recorded under
[#171](https://github.com/allstoalls/pcc/issues/171#issuecomment-5693390381),
not the entire compiler-capability parity issue.

## Problem Description

The header-free GC probe emitted by pcc1 hung even when linked by the system
linker against either runtime. Host/native IR comparison localized the failure
before linking: native `main` repeatedly defined `then`, `else`, and `ifend`,
while host pcc assigned distinct suffixes. Disassembly showed a branch back to
an earlier block, with later source statements absent from the resulting flow.
A small repeated-if program also exited incorrectly under old pcc1 without any
GC API or runtime archive dependency.

The native scaffold lowering calls `scaffold_Function_append_basic_block` (or
its IRBuilder forwarding helper). That helper constructed Block directly and
never invoked Function's name registry. Its anonymous blocks also all used
`bb`. The ordinary Function method handled explicit deduplication but did not
reserve generated `bbN` names, allowing collisions with explicit names.

## Repro

Base HEAD: `5b318560c9d30a45a291d633058d439e051550c9`, plus earlier worktree
repairs. Evidence directory: `/private/tmp/pcc-c-round5-agr4vl2k`.
Before source identity:
`4c4c259c079b0cedd975ef4d65ae60bd40cd634005c53e2880bf7bda4ccf1852`.
Candidate source identity:
`870314ee4e19b4b44feeefb6c5b58dfeaf94aa8bac23236271503aec56f35faf`.
The sole compiler-source change is `pcc/llvm_capi/ir.py`.

Control pcc1 SHA-256:
`9cc6ebc04367f40a7c6cb9cfed13abd1ec6a95280fa386a1c9745f392dabe745`.
Candidate pcc1 SHA-256:
`bb31ef326eaf3eaf62005555b8f6c5aaaff83f20772719e04c2b6fbdf4ba2e72`.

```bash
gtimeout 90s env -u LC_ALL PCC_NO_AUTO_PCC1=1 PCC_CURRENT_PCC1="$PCC1" \
  uv run pytest -x -n0 -vv --tb=short -m 'integration or not integration' \
  tests/python/test_native_c_block_names.py
```

The test checks raw emitted IR labels before the backend can coalesce blocks,
then compiles with `-o` and runs repeated if/for/while constructs. Its native arm
disables host compiler/Python delegation. Both arms explicitly use self/-O0;
this is a semantic gate, not an optimization benchmark.

## Test [CONFIRMED]

- `native.ll` vs `host.ll`: repeated labels occur only in the native control.
- `branches-before`: old pcc1 compiles the reduced program but it exits 1.
- `native-blocks-red.stdout`: new regression catches duplicate `then` in old pcc1 IR.
- `naming-red.stdout`: generated/explicit names collide even through the old
  ordinary host entry (`bb0` / `bb2`).

## Proposals

Share a single canonical append implementation across Function, IRBuilder and
both scaffold routes. Reserve every name, including generated ones, using the
function-local registry. Do not rename C constructs individually or hide the
bad CFG through optimizer changes.

## Update: 2026-09-16

`_function_append_basic_block_canonical` owns name generation, reservation and
block publication. Ordinary and scaffold functions both delegate to it. Tests
cover explicit suffix collisions, generated/explicit collisions, function-local
isolation, mixed entry points and parent identity.

Executed validation:
- `scaffold.stdout`: **40 passed in 28.83s**, including provider compilation and
  scaffold symbol resolution.
- `native-blocks-green.stdout`: **2 passed in 1.64s**, actual pcc0/pcc1 IR checks
  and owned native `-o` compile/link/run.
- `original-receipt.json`: candidate pcc1 emits the original GC probe object;
  linking against corrected C and unchanged pcc-Python runtimes both prints
  `gc3 method healed` and exits 0 within the original 10-second bound. Archive
  linking here is host pcc's owned linker; the C archive is a reference artifact,
  not proof of native C runtime construction or native archive-link CLI parity.
- `adjacent.stdout`: **246 passed in 5.15s**, C control flow, IR parity and
  scaffold surfaces. LLVM checks in the parity file are reference experiments.

## Report

The scoped hang/wrong-branch failure is fixed. The cold stage1 attempt timed out
at the unchanged 420 seconds during final linking, so its manifest remains
ERROR. All 389 generated PCO inputs were retained, hashed and linked separately
in 81.03 seconds to obtain `diagnostic-link/pcc1`. The relink receipt's inherited
scratch metadata initially referenced the baseline source manifest; it was
corrected to the actual stage1 snapshot identity with an explicit note. The
linker import root and all PCO inputs were the candidate throughout.

Full pytest/integration qualification, fixed point, 30-second compile target,
complete dependency ownership and gateway performance are not established.
No commit, push or installation occurred. Work pauses after this requested round.

Remaining observed IR differences include an extra ASCII `0` after native C
string terminators and function attributes; their semantic impact was not
investigated here. The fact that duplicate-label IR reached object emission also
needs a separate malformed-input boundary check. These observations were left
on #171 rather than folded into this block-naming repair.
