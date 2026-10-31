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
- Named functions, forward calls and recursion, `return`, `if` with
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

- Fixed arrays of scalar integers/bools, explicit constant lengths and `[...]`
  composite literals, positional/keyed elements, zero-filled holes, ordinary
  indexing and mutable package/local storage. Arrays retain value-copy semantics
  in assignment, parameters and returns through the private PCC Go ABI (not a C
  ABI claim). Array lengths are limited to 4096 elements; nested arrays and array
  equality are explicit gaps. Assignment evaluates index operands and right-hand
  values before stores; compound assignment evaluates its target only once.
- Package variables with zero initialization or statically evaluable scalar/array
  initializers. Variables are storage, not constants. Initializers that read
  variables or invoke runtime functions are rejected until the shared package
  initialization protocol is available; `init` is still unsupported.
- Array indexing checks constant indices and admits dynamic indices only with a
  conservative generic bound from an unsigned type, a nonnegative constant mask,
  or an unsigned right shift by a constant. Unproved bounds fail with the missing
  shared-runtime bounds-panic contract; no unchecked access, abort-as-panic, or
  package-name/table-specific rule substitutes for that contract.

Go predeclared-name shadowing, dynamic package initialization, grouped
const/var declarations, floating/complex types, other aggregate/reference types,
methods/interfaces/generics, closures, strings, I/O, panic,
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
the original local grouped `const` declaration in `Div64` after parsing its
named and multiple result lists.
The remaining package closure needs original string tables and shared error/
panic/linkname support. This is a capability frontier, not a successful
compile/run of `math/bits`. No token removal makes these programs pass.

Array and assignment semantics follow the official specification's
[array types](https://go.dev/ref/spec#Array_types),
[index expressions](https://go.dev/ref/spec#Index_expressions),
[assignments](https://go.dev/ref/spec#Assignments), and
[package initialization](https://go.dev/ref/spec#Package_initialization).
The authored `arrays.go` is an unchanged complete program, exercised through
its own main and independent full-width native observers; the optional official
Go oracle receives those same bytes. Darwin object emission is cross-only.

## Function result lists

Unnamed and named results (including blank names), grouped result declarations,
zero-initialized result variables, explicit return lists and bare returns are
typed before IR. Bare returns reject a shadowed named result. A sole multi-value
call expands only in assignment/function-local declaration, return or argument-list contexts;
Go has no first-class tuple value here. Source calls are evaluated once, and
return expressions are all evaluated before named result stores. Multiple
results use a private public-IR struct and the existing aggregate backend path,
not the official Go ABI or a qualified C ABI. Deferred functions are still
rejected rather than dropping their required pre-return behavior. The result
count has an explicit 256-element implementation limit.

The complete authored `results.go` checks these rules with its own main, a
same-source official-Go oracle and independent full-width native observers.
The semantic references are [return statements](https://go.dev/ref/spec#Return_statements)
and [calls](https://go.dev/ref/spec#Calls).
