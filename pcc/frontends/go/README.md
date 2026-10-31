# Owned Go frontend

`compile_go`, `compile_go_sources` and `compile_go_packages` accept original Go
source and return `pcc.ir.ir.Module`. Object emission and linking use the shared
PCC self backend and owned linker. There is no Go-to-C/Python source rewrite,
external compiler fallback, independent collector, or scheduler.

## Admitted language

- Main packages and explicitly supplied source-package graphs, with separate
  source files, file-scoped named imports, aliases, exported members and import
  cycle checks. The caller supplies selected files; this API does not resolve
  modules, download dependencies, or provide a substitute standard library.
- Signed and unsigned 8/16/32/64-bit integers; `int`, `uint` and `uintptr`
  are explicitly 64-bit on admitted x86_64/arm64 Linux and arm64 Darwin targets.
  x32/ILP32, unknown ABIs and malformed target triples fail before IR emission.
  Other operating-system routes are not admitted yet. `byte` aliases
  `uint8`, `rune` aliases `int32`; `int` and `int64` remain distinct Go types.
  Boolean/integer constants, representability checks, explicit scalar conversions,
  lexical scopes, zero initialization, parallel assignment and short declarations.
  Runtime conversions extend according to the source signedness and then truncate;
  converting a constant instead requires exact destination representability.
- Named functions, forward calls and recursion, one result, `return`, `if` with
  optional initialization, `for` clauses/conditions/infinite loops, `break`,
  `continue`, and genuinely short-circuiting logical operators.
- Exact untyped integer constant arithmetic up to a diagnosed 4096-bit precision
  limit. Typed runtime signed arithmetic wraps; constants must be representable.
  Integer division truncates toward zero. Constant divisors are required until
  shared-runtime division-by-zero panic support is present; the signed minimum
  divided by `-1` is lowered without LLVM poison. Shift counts may be constants
  or dynamic unsigned integers. Dynamic signed counts still require the shared
  negative-count panic runtime and fail explicitly. Counts are checked/clamped
  before IR shifting; large shifts follow Go semantics rather than an instruction
  mask. Nonconstant untyped shift operands acquire the enclosing Go context type.

Go predeclared-name shadowing, package variables/initializers, grouped
const/var declarations, floating/complex types, aggregate/reference types,
methods/interfaces/generics, closures, multiple results, strings, I/O, panic,
defer/recover, goroutines/channels and standard-library dependencies remain
explicit diagnostics. This is not complete Go, native pcc1 qualification,
C-parity, or a shared-runtime/GC integration claim.

The semantic reference is the [official Go specification](https://go.dev/ref/spec),
particularly semicolons, scope, constant expressions and integer operators.

## Tests and real-source frontier

`tests/go` checks syntax, typing, IR, negative admission and real owned ELF
execution. Its original complete Go fixtures retain their package declarations,
functions and main self-checks unchanged. Failed fixture self-checks loop, so
execution times out rather than accidentally reporting success without the
unsupported panic runtime. A separate ABI observer records all 64 result bits;
it does not implement source-language operations.

The explicitly selected official-Go reference tests receive identical source
bytes and are development oracles only. Set `PCC_GO_REFERENCE` to the official
Go executable and `PCC_GO_REFERENCE_CACHE` to a bounded workspace cache; neither
is a compiler dependency. Resource limits and shared performance locks apply.

The unmodified Go 1.27.3 `test/ken/for.go` and `src/math/bits/bits.go`, including
BSD-3-Clause license and per-file hashes, are in `tests/go/upstream`. They are
frontier tests, **not successful upstream-program qualification**: the former
requires shared-runtime panic support; the latter now reaches its original
package-level lookup tables and still requires arrays/package state. No token removal makes these
programs pass.
