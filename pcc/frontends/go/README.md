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
  `continue`, ordered expression `switch`/`fallthrough`, and genuinely
  short-circuiting logical operators.
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

Package defined/alias types, ordinary value structs and direct value-receiver
methods are described below. Go predeclared-name shadowing, dynamic package
initialization, floating/complex types, interfaces/generics, pointers, stored
method values/closures, dynamically allocated strings, I/O, panic,
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
fields, blank fields, pointers, recursive layouts, struct comparison and
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

### Direct value-receiver methods

Local defined types (and non-generic aliases of those types) may declare ordinary
value-receiver methods. Method identity is the receiver's defining type plus its
name, not the underlying machine layout. New defined types do not inherit the
methods of their underlying type; aliases do. Method/field conflicts, duplicate
methods, receiver scope and imported unexported methods are checked before IR.

Direct calls `value.Method(args)` evaluate the receiver expression once and pass
a value copy. Calls inside receiver and argument expressions keep Go's lexical
evaluation order; no particular ordering of unrelated ordinary variable reads
is asserted by the fixtures.
Direct method expressions `Type.Method(receiver, args)` use the same existing
function-call lowering. Receivers may be scalar, static-string descriptors or
value records; aggregate receiver mutation affects only the local value copy.
Sole multiple-result argument calls remain expandable after the implicit receiver,
without treating Go multiple values as first-class tuples. There is no interface
or virtual dispatch and no new calling convention. Stored method values or method
expressions, pointer receivers, generic receiver declarations and escaping bound
receivers remain explicit capability diagnostics pending their shared mechanisms.

[Method declarations](https://go.dev/ref/spec#Method_declarations),
[method sets](https://go.dev/ref/spec#Method_sets), and
[method expressions](https://go.dev/ref/spec#Method_expressions) define these rules.


### Ordered expression switches

Integer, boolean and string selectors (including their defined types) are
converted to a temporary value exactly once. Case expressions may be nonconstant;
dispatch evaluates them left-to-right, stops at the first match, and only selects
the default after every test fails. Case bodies have independent implicit scopes.
A direct final `fallthrough` enters the following body without testing its labels;
it is forbidden in nested blocks or the final clause. Ordinary clause completion
exits the switch. `break` exits the nearest loop/switch and `continue` still targets
the nearest loop. Missing-return checks follow the switch termination rule, not
whether the optimizer can prove one particular selector constant.

Integer/string/bool duplicate constant cases are rejected as an implementation
restriction. Labels, type switches, interface equality and aggregate comparison
remain explicit boundaries. This uses ordinary public-IR branches and the existing
string comparison loop, not C switch semantics or a shared-runtime substitute.
See [expression switches](https://go.dev/ref/spec#Expression_switches),
[termination](https://go.dev/ref/spec#Terminating_statements) and
[fallthrough](https://go.dev/ref/spec#Fallthrough_statements).


The complete authored `switches.go` is retained as an invalid source: its default
`int` selector and a `uint64` case disagree even when fallthrough would bypass the
label at run time. PCC and the official compiler must reject those original
bytes. `switches_valid.go` is a distinct program with an explicitly typed selector,
used for native/reference control-flow observations; it does not relabel the old
input as successful or relax static checking of unreachable case expressions.

## Fixed-array range and array length

Fixed arrays (including defined array types) support zero, one or two iteration
variables, `:=` declarations and ordinary assignment targets. Two-value range
captures an independent array value before the loop; mutation of the original
array or an iteration variable cannot alter that snapshot or the hidden index.
Assignment targets are evaluated once per iteration before stores, using the
ordinary parallel-assignment path. `break`, `continue` and nested switches use
the existing distinct control targets.

With at most one effective iteration variable (a trailing blank is omitted),
the array expression is not evaluated when its length is constant. Runtime
calls anywhere in its evaluated expression, including method receivers and
initializer elements, prevent this omission. Array `len`/`cap` use the same
rule and always have type `int`, even when constant. The array's element loads
are reached only after the hidden induction value is proven within its static
length; this is not a substitute for arbitrary dynamic bounds panics.

Range-declared variables have per-iteration Go semantics. The current admitted
values cannot expose stack-variable identity: pointers, captured closures and
method values remain explicit gaps, not a claim of escaping-loop-variable
support. Slice/pointer/string/map/channel/integer/function range remain
unsupported; no slice backing, UTF-8 iterator, scheduler or managed allocation
is manufactured. Relevant rules are the official Go specification's
[range clauses](https://go.dev/ref/spec#For_range) and
[length and capacity](https://go.dev/ref/spec#Length_and_capacity).

### Structured capability diagnostics

`UnsupportedGo` is a `GoDiagnostic` subclass with stable `code`, `feature_id`,
`kind`, location, and explanatory `message` fields. Its code is selected at the
recognizing parser/semantic boundary, never inferred from message text. The
canonical Go feature map pairs every registered code with one feature row.
Implementation limits have explicit codes as well; they do not assert that the
language specification itself forbids a larger program.

Uncoded `GoDiagnostic` has `kind="unclassified"` and `code=None`: legacy parser
and builtin boundaries are not all classified, so consumers must not infer a
validity error from the absence of a capability code. Known invalid-source
regressions still remain uncoded rather than becoming missing features. In particular,
forbidden string operators, non-iterable values, negative constant shifts and
package type declarations named `init` are not described as missing features.
A capability rejection identifies the first unsupported boundary, not proof
that the rest of the source is valid. Generic parser gaps remain uncoded and are
listed as incomplete inventory rather than assigned fabricated feature codes.
