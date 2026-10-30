# NumPy 2.4.4 scalar source-function fixture

`halffloat_scalar.cpp` contains five **unchanged function definitions** from
[NumPy v2.4.4 `numpy/_core/src/npymath/halffloat.cpp`](https://github.com/numpy/numpy/blob/v2.4.4/numpy/_core/src/npymath/halffloat.cpp):
`npy_half_iszero`, `npy_half_isinf`, `npy_half_isfinite`, `npy_half_signbit` and
`npy_half_copysign`. NumPy's BSD license is retained in `LICENSE.txt`.

The test supplies `typedef unsigned short npy_half;` for its explicitly selected
Linux x86-64/LP64 target, whose `unsigned short` is 16 bits. This corresponds to
the selected `npy_uint16` / `npy_half` aliases in NumPy's `npy_common.h`; it is
fixture setup, not a replacement NumPy header implementation. The test driver
checks all 65,536 half-precision bit patterns. For every pattern, a generic
test-only `cpp_emit(int)` ABI function writes six little-endian 32-bit results:
zero, infinity, finite, signbit, and copysign with negative/positive sign inputs.
The complete 1,572,864-byte sequence (plus the entry-result word) is compared
with independent integer/range expectations and the separately built C++17
reference. Per-input errors cannot cancel out in aggregate counts. A small
signed/full-width witness checks the stream adapter. All five functions execute.

This is a **source-function semantic regression**, not compilation of the
original translation unit, its headers or its C-linkage declarations. The
original TU also needs preprocessing, namespaces and class operations; those
are not removed by the production frontend and remain unimplemented boundaries.
The owned compiler and external G++17 oracle receive the same fixture/driver
source bytes. No compiler code recognizes NumPy's name or these functions.
