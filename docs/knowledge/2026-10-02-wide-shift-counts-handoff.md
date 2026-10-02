# Wide Python integer shift counts, 2026-10-02

## Source and scope

Base HEAD: `a5ba92743d991bf07b9dd21b0b074b45065cc47e`, with pre-existing
concurrent work preserved. This tranche changes `py_int_ops.py`,
`py_int_shift.py`, two boxed-shift precheck blocks in `binary_op_lowering.py`
and `exact_int_lowering.py`, and adds `test_wide_integer_shift_counts.py`.
There is no remaining C integer-shift implementation in this checkout.

Exact preimages, final source copies, hashes, independent runtime/compiler/test
patches, combined patch, logs, resource samples, and the native program are in
`../pcc-cloud-runs/wide-shift-counts-astra/`. Its `manifest.json` is the precise
identity record. Compiler patch ordering matters: its binary-op preimage is
`2ba44ee62efe7bbd88b7dcc41bbb2680e08af5de1acd344f8bbeef88cc856fd3`, including
the already-qualified explicit-machine predicate slice; its exact-int preimage
is `438b573b077a227f64d30eb070308d939b3dd6fcea36f459116ad5b09d688496`, including
the existing integer-ABI/root repairs. The narrow compiler patch removes only
the two premature scalar projections. Raw-machine lowering and surrounding
operand evaluation, roots, pins, and post-call error cleanup are unchanged.

Combined patch SHA-256:
`4902f3d0c3586f3f87522dac2eb4f5d2d0035eb050d0d15c98345bade93197c5`.

## Required red and repair

`required-red.log` executes the original production `py_int_shr` body under a
bounded memory model: `1 >> (1 << 100)` returned NULL with no exception,
instead of zero. `model-owned-ir-v3.log` then exposes a separate compiler
failure: an ordinary boxed count is passed through `py_int_to_i64_lane` merely
to check its sign, before the exact shift kernel receives it.

Count decoding now checks sign without narrowing. Huge right shifts saturate
to zero or minus one; zero left shifts return zero; huge negative counts raise
`ValueError("negative shift count")` even with zero on the left. Huge positive
nonzero left shifts raise the appropriate size/allocation error. Dynamic bool
operands are handled without reading an integer payload from a bool object,
and shift results have integer type. Existing heap-zero-count ownership and
input objects are preserved.

## Two different size boundaries

The exact local oracle is CPython 3.15.0rc1, built August 25, 2026, with
30-bit digits. Safe runtime probes verify signed-count overflow, negative
counts, zero operands, saturation, and the provably overflowing side of the
enormous-size boundary; no multi-gigabyte result was allocated.

The [primary CPython 3.15 implementation](https://raw.githubusercontent.com/python/cpython/3.15/Objects/longobject.c)
separately defines an enormous-size limit and allocation failure. On 64-bit
targets, its maximum reference digit count is `(INT64_MAX - 1) // 30`.
Left-shift allocation requires the operand's reference digit count plus
`count // 30`, plus one for a nonzero remainder. Exceeding that limit yields
OverflowError; an unsuccessful allocation yields MemoryError. The exact
rc1 source URL was unavailable, so branch-source evidence is not labeled as
an exact build-source receipt.

PCC instead stores its count of 32-bit limbs in signed i32. That narrower
storage-capacity boundary now yields MemoryError before allocation/header
overflow; it is not used to infer CPython's OverflowError threshold. Tests
check both sides of both boundaries without constructing large buffers.

## Verification and remaining gate

`model-compiler-ir-v6.log` reports **225 passed, 8 deselected**, including
production-body arithmetic/ownership/error modeling, the qualified CPython
fixture, both changed runtime modules through the owned library-IR
parser/verifier, exact and augmented-assignment boxed-count IR, and five
explicit-machine controls. The v6 JSON records stable before/after hashes.
All pytest calls used `-o addopts= -x -n0 -vv --tb=short`, the qualified
interpreter, disabled native provisioning/automatic pcc1/uv synchronization,
and the existing 120-second, 1-GiB process-tree watchdog. Scoped
`git diff --check` passes.

Native execution is **not yet qualified**. The prepared integration node is
`tests/python/test_wide_integer_shift_counts.py::test_wide_shift_counts_native_all_gc`.
It requires a matching runtime archive, compiles the real program with the
self backend and libpython off, and executes it under GC0–4 with
`PATH=/nonexistent`, checking stdout, stderr, and exit status. Native compiler
versus host compiler evidence depends on the selected test compiler and must
be labeled accordingly. No runtime/archive/native/compiler build was started
by this tranche; no frozen v16 input, commit, or push was changed.

This repair does **not** close the separately known raw-formal threaded-entry
lifetime gaps, prove forced-relocation safety, or establish a compiler fixed
point. Those remain distinct gates.
