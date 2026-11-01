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
initialization, floating/complex types, interfaces/generics, escaping pointers, stored
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
fields, blank fields, pointer-bearing fields, recursive layouts, struct comparison and
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
values do not qualify distinct per-iteration address identity: addresses of
per-iteration bindings, captured closures and stored method values remain
explicit gaps, not a claim of escaping-loop-variable support. Slice/pointer/string/map/channel/integer/function range remain
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

## Bounded pointer implementation and acceptance

The bounded implementation provides `*T`, address and indirection, local alias
copy/rebinding, equality (including nil comparisons), indirect value assignment,
struct fields, fixed-array indexing/length, and value-receiver calls through
nonnull pointers. These use ordinary public IR addresses, loads and stores;
there is no noalias assertion, pointer-to-integer erasure or new runtime.
Normative pointer/address/assignment and same-original native/reference tests
are in `tests/go/test_pointers.py`; the listed deterministic cases and complete
repository-Go selections passed in the execution lane recorded below.

Stage A adds nullable locals and direct pointer parameters. Null storage is an
ordinary value, not a panic; indirection requires a source-control-flow nonnil
proof at its evaluated use. Every parameter starts unknown, independently of
callers. A frontend CFG preserves branch joins, short-circuit edges, loop
backedges, continue/post, break and switch fallthrough. Parallel assignments
check all old destination addresses before capturing RHS facts and committing
stores left-to-right. Typed null initialization uses the existing public IR.

Nullness and lifetime are separate proofs. A local pointer's lexical region
conservatively bounds its possible pointees; incoming pointers carry only the
current call's lifetime, never global lifetime. Copying an alias into an unsafe
enclosing region still fails. All original-source callees, including imported
and recursive calls, must be checked before lowering. Calls may mutate aliased
pointees and globals; no pointee-value, noalias or nonnull ABI attribute follows.
No pointee lifetime is extended to make a source program pass. Addresses of
Go 1.22 per-iteration declared for/range bindings fail explicitly until distinct
iteration storage identity exists. Addresses of ordinary loop-body locals are
usable only inside their iteration's region. Existing pointers remain aliases.

Pointer-valued returns and pointer receiver declarations, unchecked or retaining callees, package pointer
storage, pointer-bearing records/arrays/pointees, unproven evaluated indirection,
escaping aliases,
addressed composite literals, representation-changing pointer conversions,
pointer switches and pointer-array range are explicit capability boundaries.
These are valid Go obligations, not invalid-program rejections. No Go heap,
GC, panic, interprocedural escape analysis or native-pcc1 claim follows.

The authored `pointers.go` is retained unchanged after a real Go 1.27.3 main
self-check timeout: it expected a plain receiver load before an argument call,
although Go's order-of-evaluation rule does not prescribe that relative order.
The old reference failure is not relabeled. `pointers_deterministic.go` is a
separate acceptance program with an explicit receiver-value capture statement.
The retained original's 24 helpers have a separately named diagnostic observer;
its unspecified-order result is not original-main or baseline acceptance.

Stage A's listed cases now have ordinary, owned-native and same-input official-Go
acceptance, including complete repository-Go ordinary and integration selections.
`tests/go/test_nullable_pointers.py` retains the adversarial originals, six byte-distinct
previously unsupported nil-storage/parameter originals, a complete deterministic
30-observation fixture and an unchanged two-package native/official-reference
comparison. Prior v5 receipts are not rewritten into Stage A results. Pointer
receiver method-set work is a separate unsupported Stage B, not part of this implementation.


## Feature inventory: implementation is separate from acceptance

This is a feature-by-feature view of the **existing** IDs in
`specs/features/go.json`, not a complete Go inventory. **Implemented** describes
source behavior in the stated bounded domain; **acceptance** describes executed
evidence, not all possible programs. An inventory row with legacy `missing`
does not mean its chapter has no implementation. An explicit capability
rejection is not an invalid-Go classification or a positive runtime pass.

Qualification version: `sha256:f5ea81412a8b8dc70e4ec6159334a366478284ba6b0da079ee8277336a5beaaf` (the frozen tested source manifest).
Execution lane **L** is host **pcc0**, owned **self** backend, **Linux x86_64**,
no applicable GC; official **Go 1.27.3 linux/amd64** is an external reference only.
PCC native-pcc1, Mac execution, full Go/standard library and shared runtime/GC
qualification are not established here. Cross-object tests do not renew Mac
execution evidence.

The current **schema1** `status` remains a legacy qualification field. It has no
independent machine-readable implementation-status axis. The two columns below
make the distinction explicit for readers; they do **not** claim that a schema2
migration or a new machine-enforced dual-axis report has shipped. No ignored
JSON fields are used to simulate such a migration.

| Existing feature ID | Implementation status | Acceptance status / test binding | Legacy schema1 status |
|---|---|---|---|
| `go.expression-switch.scalar` | Implemented: bounded scalar/static-string expression switches | Qualified in L for SW-U / -P / -D only | `supported` |
| `go.array-range.fixed-value` | Implemented: bounded fixed value-array range and len/cap | Qualified in L for AR-U / -P / -D only | `supported` |
| `go.inventory.source` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.lexical` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.constants` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.variables` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.types` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.properties` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.blocks` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.declarations` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.expressions` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.statements` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.builtins` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.packages` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.initialization` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.errors` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.panics` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.inventory.system` | Not fully inventoried; existing admitted behavior is described above | Chapter-wide acceptance not inventoried; no feature-wide pass inferred | `missing` |
| `go.capability.aggregate-comparison` | Explicitly unimplemented boundary: aggregate comparison | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.aggregate-switch` | Explicitly unimplemented boundary: aggregate switch | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.array-bounds-panic` | Explicitly unimplemented boundary: array bounds panic | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.array-element-types` | Explicitly unimplemented boundary: array element types | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.blank-function-declarations` | Explicitly unimplemented boundary: blank function declarations | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.blank-method-declarations` | Explicitly unimplemented boundary: blank method declarations | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.blank-package-variables` | Explicitly unimplemented boundary: blank package variables | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.blank-struct-fields` | Explicitly unimplemented boundary: blank struct fields | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.blank-type-declarations` | Explicitly unimplemented boundary: blank type declarations | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.complex-types` | Explicitly unimplemented boundary: complex types | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.division-panic` | Explicitly unimplemented boundary: division panic | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.dot-imports` | Explicitly unimplemented boundary: dot imports | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.dynamic-string-concatenation` | Explicitly unimplemented boundary: dynamic string concatenation | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.dynamic-string-conversion` | Explicitly unimplemented boundary: dynamic string conversion | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.embedded-fields` | Explicitly unimplemented boundary: embedded fields | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.floating-types` | Explicitly unimplemented boundary: floating types | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.interface-types` | Explicitly unimplemented boundary: interface types | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.limit-array-elements` | Explicitly unimplemented boundary: limit array elements | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.limit-constant-precision` | Explicitly unimplemented boundary: limit constant precision | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.limit-function-arity` | Explicitly unimplemented boundary: limit function arity | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.limit-record-layout` | Explicitly unimplemented boundary: limit record layout | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.limit-string-bytes` | Explicitly unimplemented boundary: limit string bytes | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.limit-struct-fields` | Explicitly unimplemented boundary: limit struct fields | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.limit-type-expansion` | Explicitly unimplemented boundary: limit type expansion | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.local-type-declarations` | Explicitly unimplemented boundary: local type declarations | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.method-values` | Explicitly unimplemented boundary: method values | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.negative-shift-panic` | Explicitly unimplemented boundary: negative shift panic | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.non-array-range` | Explicitly unimplemented boundary: non array range | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.package-initialization` | Explicitly unimplemented boundary: package initialization | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.pointer-receivers` | Explicitly unimplemented boundary: pointer receivers | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.predeclared-shadowing` | Explicitly unimplemented boundary: predeclared shadowing | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.slice-storage` | Explicitly unimplemented boundary: slice storage | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.string-bounds-panic` | Explicitly unimplemented boundary: string bounds panic | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.struct-tags` | Explicitly unimplemented boundary: struct tags | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.pointer-escape` | Explicitly unimplemented boundary: local pointer escape | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.pointer-signatures` | Explicitly unimplemented boundary: escaping pointer results and unchecked call contracts | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.pointer-storage` | Explicitly unimplemented boundary: pointer-bearing storage | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.pointer-nil` | Explicitly unimplemented boundary: unproven evaluated pointer indirection | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.pointer-iteration-identity` | Explicitly unimplemented boundary: per-iteration pointer identity | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.pointer-composite-address` | Explicitly unimplemented boundary: addressed composite literal allocation | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.pointer-conversions` | Explicitly unimplemented boundary: conversions across pointer base types | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.pointer-switch` | Explicitly unimplemented boundary: pointer expression switches | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.local-pointer-regions` | Implemented: bounded local pointer alias/address/value-update semantics | Qualified in L for LP-U / -P / -D only | `supported` |
| `go.nullable-direct-pointer-parameters` | Implemented: nullable locals and source-guarded non-retaining direct parameters | Qualified in L for NP-U / -P / -D only | `supported` |

### Exact acceptance test bindings

U is a unit/IR check; P is an actual owned native main; D is an identical-input
external native reference observation. These distinct stages are not
interchangeable. The switch/range P and D nodes independently assert the same
raw process tuple; the pointer D nodes additionally retain both raw-stream
files and exit receipts. U's ledger stream hash identifies the complete
ordinary test-process log, not invented per-node output.

| Binding | Existing test ID |
|---|---|
| SW-U | `tests/go/test_switches.py::test_retained_original_unreachable_case_still_requires_matching_type` |
| SW-P | `tests/go/test_switches.py::test_original_switch_program_main_executes` |
| SW-D | `tests/go/test_switches.py::test_official_go_receives_identical_original_switch_program` |
| AR-U | `tests/go/test_array_range.py::test_fixed_array_range_grammar_scope_and_empty_values` |
| AR-P | `tests/go/test_array_range.py::test_original_array_range_program_main_executes` |
| AR-D | `tests/go/test_array_range.py::test_official_go_receives_identical_original_array_range_program` |
| LP-U | `tests/go/test_pointers.py::test_nonescaping_pointer_types_and_aliases_lower_without_external_owner` |
| LP-P | `tests/go/test_pointers.py::test_deterministic_pointer_program_main_executes` |
| LP-D | `tests/go/test_pointers.py::test_same_deterministic_pointer_program_matches_official_raw_process_streams` |
| NP-U | `tests/go/test_nullable_pointers.py::test_source_guards_and_unevaluated_operands_admit_pointer_parameters` |
| NP-P | `tests/go/test_nullable_pointers.py::test_complete_nullable_pointer_main_executes_owned` |
| NP-D | `tests/go/test_nullable_pointers.py::test_identical_nullable_pointer_program_matches_official_raw_streams` |
| CAP-U | `tests/go/test_diagnostics.py::test_capability_codes_are_explicit_source_located_and_fail_before_ir` (the existing source-located code-specific parameter cases) |

Additional NP obligations are explicit tests in `tests/go/test_nullable_pointers.py`:
`test_nullable_pointer_full_width_native_observations` has 30 actual 8-byte
observations (including aliasing and internal SysV stack pointer arguments);
`test_identical_graduated_frontiers_match_official_raw_streams` retains six exact
former nil-storage/parameter originals; and
`test_identical_imported_pointer_project_matches_official_raw_streams` compares
the unchanged two-package source graph. Object hashes in the observation records
are recorded from emitted in-memory bytes; retained ELF binaries are separately
hashable. The 24
`test_official_go_typechecks_identical_valid_unsupported_originals` cases build
native official-Go executables **without running them** and do not qualify PCC
nil-panic/escape/receiver support.

The complete selections finished with **708 ordinary test nodes** (31 simulated
reference-protocol checks + 677 other ordinary checks) and **407 integration
nodes** (280 owned-native, 30 positive external-reference, 70 normal-source
rejections, 24 build-only valid-unsupported, two external nil-panic frontiers,
and one diagnostic-only observer). These are test outcomes, not 1,115 supported
features or a whole-language completeness percentage. Both selections retain
all cases; no failed case was skipped or relabeled to obtain these outcomes.

`tests/go/test_pointers.py::test_retained_original_pointer_order_diagnostic`
is excluded from acceptance: it does not execute the retained original main and
admits only spec-permitted observer outcomes. The original v3 main timeout
(1 failed, 26 unfinished) and v4 panic-message expectation failure
(25 passed, one failed, two unfinished) remain historical failures; the early
serial diagnostic configuration deviation remains separate. The old 65 broad
assertion-based negative test passes did not establish normal semantic rejection;
new strict normal-rc1/source-attributed receipts renew the classifications without
altering those historical outcomes. Simulated protocol tests prove the harness,
not Go semantics. Successful official nil panics are not PCC panic support.

Reproduction uses the existing complete selections, bounded official compiler
cache, default six workers and repository resource guards:

```sh
uv run python -m pytest -x --maxfail=0 -c pyproject.toml -m 'not integration' tests/go
uv run python -m pytest -x --maxfail=0 -c pyproject.toml -m integration tests/go
```

The second command requires explicit `PCC_GO_REFERENCE` and
`PCC_GO_REFERENCE_CACHE`. It rebuilds final native/reference outputs; retained
binaries or old process results are never substituted as a new gate. Raw logs,
private artifact paths and recovery receipts are not part of this source README.
