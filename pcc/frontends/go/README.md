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

Go predeclared-name shadowing, dynamic package initialization, floating/complex types, other aggregate/reference types,
methods/interfaces/generics, closures, dynamically allocated strings, I/O, panic,
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
requires shared-runtime panic support; the latter parses its named/multiple result lists and local grouped constants,
then requires the original `ntz8tab` string-table dependency.
The normal package closure now includes the original string tables and
non-bootstrap error declarations; shared error/panic/linkname support is still
required. The unchanged table file is also tested independently with authored
component observers, not as a substitute for the complete package. This is a capability frontier, not a successful
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


### Declaration groups and iota

Package and function-local `const (...)` and `var (...)` groups preserve their
surrounding scope; a group does not introduce a block. Empty groups are valid.
Each ConstSpec has an independent untyped `iota` value, incremented once per
specification (including blank names), reset by each new const declaration.
Omitted constant expression lists repeat the prior source expressions and type,
not previously computed values. Fresh AST copies retain separate typing and
reevaluate lexical references in the new declaration scope. Package dependencies
save/restore their own iota contexts during recursive constant resolution.

Blank package constants are typechecked without creating a named object or
runtime storage. Typed repetitions keep representability checks; local var
groups execute initializers in specification order. Blank package variables,
predeclared-name shadowing, dynamic package initialization and runtime allocation
remain unsupported. See [constant declarations](https://go.dev/ref/spec#Constant_declarations)
and [iota](https://go.dev/ref/spec#Iota). The complete `declarations.go` fixture
includes separate high-bit observations, nested-scope repetition, typed/blank
constants, independent resets and ordered runtime local initializers.


## Strings and the original table component

String literals preserve Go byte semantics: raw-literal carriage returns are
removed, interpreted carriage returns are retained, byte escapes need not form
valid UTF-8, and Unicode escapes must denote scalar values. Typed and untyped
string constants, constant concatenation, integer-constant conversion (including
U+FFFD replacement), byte length and all six comparisons are admitted. A string
constant has an explicit 1 MiB implementation limit.

Runtime string values are private PCC Go `{pointer, i64 length}` descriptors.
Immutable literal backing and literal descriptors are private constant storage.
Runtime literal uses load descriptor values through the existing static relocation
path, rather than inline pointer-bearing aggregate literals. Copying or assigning a
string copies the descriptor, including parameters and named/multiple results.
Static package string variables and zero/empty strings require no allocation or
package-init runtime. This is not the official Go ABI, a C string ABI, or a GC
qualification. Dynamic comparisons use an owned public-IR loop with unsigned
byte comparisons and both lengths guarding every byte load. `len` evaluates a
nonconstant string operand once; constant-string length is a typed `int` constant.

Indexing a constant-backed string produces a nonconstant `byte` value. Constant
bounds are checked, and dynamic indices require the same generic unsigned/mask/
shift proof as arrays. Other string indexing fails with the missing shared
bounds-panic contract. Dynamic concatenation, nonconstant integer conversion,
slices, string-element arrays, range iteration and dynamic package initializers
remain explicit gaps; no allocator, panic substitute or package-specific rule
is introduced.

The complete original Go 1.27.3 `bits_tables.go` is preserved with its pinned
commit/blob/SHA and BSD license. An independently authored component reader and
main exercise all four 256-entry tables without changing that file. Independent
native observations capture all 1024 result bytes; the optional official Go
reference receives the same complete table and authored sources. This component
is not the full `math/bits` package or its upstream test suite. The normal full
file selection retains `bits_errors.go` and its build tag, rather than selecting
a bootstrap replacement. Its error type and shared runtime dependencies remain
capability frontiers.

References: [string literals](https://go.dev/ref/spec#String_literals),
[string types](https://go.dev/ref/spec#String_types),
[string conversions](https://go.dev/ref/spec#Conversions_to_and_from_a_string_type),
[comparisons](https://go.dev/ref/spec#Comparison_operators), and
[length](https://go.dev/ref/spec#Length_and_capacity).


Source-file declaration separators are not empty statements: an extra top-level
semicolon or empty ImportSpec is rejected, while block EmptyStmt and empty
`import ()`/`const ()`/`var ()` groups remain distinct valid grammar. Authored
unit-source constructors omit a separator only when their optional declaration
list is absent; the previously invalid double-semicolon input is retained as a
negative test and is not relabeled as a successful source program.

### Type declarations and value records

Package-scope defined types retain declaration identity even when their public-IR
representations are identical. Aliases reuse type identity. Assignment permits
identical types or identical underlying types with at least one unnamed type;
explicit integer/bool/string and same-underlying aggregate conversions preserve
Go's separate conversion rules. Constant representability still uses the actual
Go type and target word model, not host Python integers.

Ordinary named fields, keyed/unkeyed literals, zero initialization, nested value
records, scalar-array fields, string fields, package static initializers, field
loads/stores and aggregate parameters/results use the existing public IR. Copies
are values, not C-style pointer decay. Literal operands retain source evaluation
order, parallel assignment captures addresses and RHS values before stores, and
compound field/index targets are evaluated once. Unexported fields carry their
originating package identity. Unnamed function parameters and result type lists
accept these types without converting them into variables or first-class tuples.

This private PCC Go aggregate ABI is neither the official Go ABI nor an assertion
of cross-language C compatibility. Local type declarations, struct tags, embedded
fields, blank fields, methods, pointers, recursive layouts, struct comparison and
arrays of records remain explicit boundaries. Array bounds still require the
existing static proof; no abort replaces a missing recoverable panic. Types have
32 declaration-expansion levels, 256 fields and 4096 expanded scalar slots as
explicit implementation limits. No runtime allocation/GC/initialization mechanism
is added. Complete original math/bits inputs retain their existing shared `error`
frontier; authored record fixtures are not a whole-package success claim.

Rules: [type declarations](https://go.dev/ref/spec#Type_declarations),
[assignability](https://go.dev/ref/spec#Assignability),
[composite literals](https://go.dev/ref/spec#Composite_literals),
[selectors](https://go.dev/ref/spec#Selectors).
