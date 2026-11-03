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
  ABI claim). Array lengths are limited to 4096 elements; nested arrays remain an
  explicit gap, while source-only equality support is described below. Assignment evaluates index operands and right-hand
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
initialization, floating/complex types, runtime interfaces/general type sets, generic
data types, escaping pointers, stored
method values/closures, dynamically allocated strings, general I/O and panic unwind,
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
separate project frontiers: the former now compiles and executes unchanged in the
pinned five-original shard. This does not qualify the complete upstream suite.
The latter parses its named/multiple result lists and local grouped constants,
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
fields, blank fields, pointer-bearing fields, recursive layouts and
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
expressions, generic receiver declarations and escaping bound receivers remain
explicit capability diagnostics pending their shared mechanisms. Direct pointer
receivers have a separate bounded implementation described below.

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
restriction. Labels, type switches and interface equality remain explicit
boundaries. Admitted aggregate comparison/switch source support is described
below with acceptance unrun. This uses ordinary public-IR branches and the existing
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
explicit gaps, not a claim of escaping-loop-variable support. Pointer-to-array
and string range have separate bounded source implementations below.
Slice/map/channel/function range remain unsupported; no slice backing,
scheduler or managed allocation is manufactured. Relevant rules are the official Go specification's
[range clauses](https://go.dev/ref/spec#For_range) and
[length and capacity](https://go.dev/ref/spec#Length_and_capacity).

## UTF-8 string range: source implementation, CI acceptance UNRUN

String range now consumes the existing immutable `{byte pointer, byte length}`
descriptor, captured once before iteration-target or body effects. Indices are
byte offsets of type `int`; values have type `rune` (`int32`), independently of
defined string type identity. Zero, one and two targets, blank targets,
ordinary assignment targets and structured `break`/`continue` reuse the existing
source semantics. Index-only and bare range still decode UTF-8 widths.

The decoder is ordinary public IR, with every load dominated by the captured
length. It recognizes Unicode scalar encodings, including embedded NUL, and
consumes one byte with U+FFFD for invalid, overlong, surrogate, out-of-range or
truncated encodings. It neither calls a Go/Python runtime nor adds allocation,
GC, backend, dynamic string construction or arbitrary-index panic support.
Per-iteration escaping addresses remain explicitly unsupported.

`tests/go/test_string_range.py` and its unchanged shared `string_range.go`
fixture declare spec-linked typing/diagnostic checks, 27 full-width native
observations, complete-program execution, two exact graduated rejection inputs
and official-Go same-source comparisons. **All are UNRUN for this change**:
no local tests, imports, compiler calls, probes or collection were performed.
The feature ledger distinguishes source implementation from unrun acceptance;
historical evidence does not qualify these bytes. CI must run the focused and
whole-Go ordinary/integration/reference lanes. Native pcc1, Mac execution,
enry's unmodified core and Python extension end-to-end are not qualified.

The relevant specification contracts are [range clauses](https://go.dev/ref/spec#For_range),
[string types](https://go.dev/ref/spec#String_types) and
[assignments](https://go.dev/ref/spec#Assignments).

## Integer range implementation and acceptance

Integer range uses the existing source range AST, ordinary public-IR loop edges
and local storage. It admits no iteration variable, a newly declared variable,
or one preexisting assignment target. The hidden induction value retains the
bound's signedness, width and defined-type identity. An untyped bound instead
acquires the preexisting target's integer type, or defaults to `int` when the
target is new/blank/absent; constants must remain representable. Typed bounds
do not silently convert to a mismatched target type.

The bound is captured once before target-address or body effects. Targets are
evaluated once per iteration through ordinary assignment lowering; body writes
to the bound or visible iteration variable cannot change the hidden progression.
Signed zero/negative limits have no iterations. Incrementing the last index to
the bound is representable even at the integer type maximum. The frontend CFG
keeps dynamic trip counts conservative and excludes statically nonpositive
body/target edges without skipping evaluation of a dynamic bound. The existing
per-iteration-address restriction remains; no fresh heap, nil-panic substitute,
closure support or runtime contract is introduced.

Specification-linked tests and the unchanged authored `integer_range.go` include
contextual types, narrow maxima, high-bit bounds, nil-flow edges, target/bound
effects and structured control flow. Both former `for range 3{}` rejection
inputs are preserved byte-for-byte as positive obligations. **Acceptance** is bounded Linux pcc0/self execution: all 83 new ordinary
nodes and 58 new integration nodes passed within the complete repository-Go
selections below. The integration obligations include 30 independent full-width
owned observations, a complete program main, three retained same-source raw
reference pairs and 24 normal-source rejections; rejection checks are not
positive language execution. Official Go 1.27.3 is the explicit oracle only. Rules:
[range clauses](https://go.dev/ref/spec#For_range),
[assignments](https://go.dev/ref/spec#Assignments), and
[representability](https://go.dev/ref/spec#Representability).

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

Pointer-valued returns, unchecked or retaining callees, package pointer
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
receiver method-set work is a separate Stage B implementation below; the old
Stage A receipts do not qualify its new source bytes.


### Pointer-to-array range

Range over existing `*[N]E` values and their admitted defined/alias types uses
fixed array bounds without allocating slice backing. Index-only forms, including
a blank second variable, preserve Go's constant-length non-evaluation exception;
real evaluated calls are not erased merely because the operand is a pointer.
Nil index-only ranges therefore generate indices without dereferencing nil.
Pointer-producing function results remain an explicit non-retention frontier.

Element-reading forms capture the pointer once, not an array-value snapshot.
Each iteration loads the current element before the ordinary iteration-variable
assignment; mutations of later elements are visible, but rebinding the source
pointer does not redirect the captured range. A nonempty element-reading range
requires the existing independent live-region and source-nonnil proof. A zero-
length pointer range reads no element; this is distinct from evaluating an
explicit `*p` array expression, whose dereference obligation is unchanged.

Existing parallel-assignment, short-declaration scope and per-iteration address
restrictions apply. The complete `pointer_array_range.go` main and its 19
full-width observers passed owned-native execution. Three captured raw pairs
compare that complete fixture and the two exact graduated originals with
Go 1.27.3. Nine normal invalid-source rejections and nine valid-but-unsupported
build-only controls passed their distinct obligations; building is not execution.
No shared backend/runtime, allocator, slice descriptor or GC change is introduced,
and this does not qualify complete upstream packages.

References: [range clauses](https://go.dev/ref/spec#For_range),
[length and capacity](https://go.dev/ref/spec#Length_and_capacity), and
[assignments](https://go.dev/ref/spec#Assignments).


### Direct non-retaining pointer-receiver methods

Stage B admits a concrete local defined receiver base `T`, with receiver `T` or
`*T` and existing non-generic aliases. Methods remain bound to the defining base:
`T`'s method set contains value methods, while `*T`'s includes both. An addressable
`x.M(args)` may use `(&x).M(args)` for a pointer method; temporaries and constants
do not gain addressability. Field, package ownership and duplicate-name rules
are unchanged. The five exact former receiver-capability inputs are retained in
`test_pointer_methods.GRADUATED` as positive obligations, not deleted negatives.

A pointer receiver is an ordinary first nullable pointer parameter in the
existing private ABI. The source CFG proves every evaluated dereference from the
method body's own guards, never from observed callers. A nil-safe method may be
called on nil; this does not create a nil-panic implementation. Receiver pointer
facts receive the same live-region/non-retention checks as ordinary arguments.
Automatic receiver addresses reuse the explicit address operator's lexical and
per-iteration identity restrictions. Aliased receivers remain aliases; assigning
to the receiver variable itself changes only that method's local parameter.

The receiver expression is evaluated once. Calls within receiver/argument
expressions preserve lexical order; tests do not assert an unspecified ordering
of unrelated plain variable loads. Direct `(*T).PointerMethod` calls pass the
pointer itself. Existing `(*T).ValueMethod` expressions still use their delayed
value-copy load after all source arguments, not the pointer-method path. The
existing pointer IR type, argument slots, address lowering and direct calls are
reused without backend, allocator, scheduler or GC changes.

The authored `pointer_methods.go` and unchanged two-package source graph passed
owned execution and explicit official Go 1.27.3 comparisons. The fixture has
23 independent full-width native observations; the complete main, five exact
graduated originals and package graph also passed same-input raw comparisons.
The 15 normal invalid-source controls and ten valid-but-unsupported build-only
controls passed their distinct obligations; build-only is not execution. Method values, interfaces, generic receivers,
pointer results, escaping storage, unproven nil indirection and addressed
per-iteration bindings remain unsupported. This is not general Go method or
runtime qualification.

Rules: [method sets](https://go.dev/ref/spec#Method_sets),
[method declarations](https://go.dev/ref/spec#Method_declarations),
[calls](https://go.dev/ref/spec#Calls),
[method expressions](https://go.dev/ref/spec#Method_expressions), and
[address operators](https://go.dev/ref/spec#Address_operators).


### Fixed official shard: bootstrap output and terminal panic

1. **Measured frontier:** the unchanged pinned five-source Go 1.27.3 shard now
   passes every original obligation: three native/reference executions, one
   compile-only acceptance and one expected source rejection. `ken/for.go` and
   `helloworld.go` no longer stop at their original panic/print calls. This is
   the selected shard, not the full upstream test suite or standard library.
2. **Next dependencies:** original normal `math/bits` error-interface declarations,
   unsafe/linkname runtime symbol binding, and genuine error-payload panic support.
   No upstream assertion, `main`, source selection or oracle was rewritten.
3. **Gate command:** the guarded `scripts/go_conformance.py run --manifest
   specs/conformance/suites/go1.27.3-representative.json --go-src "$GO_SOURCE_ROOT"
   --go "$GO_REFERENCE_COMPILER" --reference-cache "$BOUNDED_GO_CACHE"
   --pcc-source "$FROZEN_PCC_SOURCE" --compiler-revision "$PCC_REVISION"
   --out "$EXTERNAL_OUTPUT" --timeout 6`; retain the original outer resource bounds.

Linux `print`/`println` lower admitted integer/bool/string values through existing
owned IR and syscall primitives. All argument values are captured before output;
integer formatting handles full unsigned width and signed minima, and writes
handle partial progress and EINTR. No allocator or shared runtime changes are
introduced. Builtin shadowing retains the existing explicit capability boundary,
rather than silently dispatching a shadowed name to the builtin.

`panic` is implemented only as the genuine **terminal uncaught case** in the
already restricted no-defer/no-recover/no-goroutine/no-foreign-callback domain:
evaluate its scalar payload once, report it, terminate the process, and provide
no normal CFG successor. This is **not general panic/recover/unwind support**.
Nil/non-scalar payloads, scalar payloads with value-receiver `Error()`/`String()`
formatting callbacks, and non-Linux I/O remain explicit boundaries. Callback
effects are not silently discarded; their interface/runtime protocol is missing. Authored
triggered-panic checks retain the full official stdout/stderr/exit; implementation-
specific stack-report differences are not normalized into identical-stream PASS.
Actual qualification includes 16 triggered native panics and 16 same-input
Go 1.27.3 status/payload comparisons, two raw-identical complete programs, two
real broken-stderr pairs and five calling-thread signal-state observers. Every
panic reference report differs in complete stderr; these differences remain
explicit, not normalized into identical-stream PASS. The combined legacy feature
status stays `missing`/partial; source implementation and measured semantic
acceptance are separate from raw-report equality and general unwind support.

## Feature inventory: implementation is separate from acceptance

This is a feature-by-feature view of the **existing** IDs in
`specs/features/go.json`, not a complete Go inventory. **Implemented** describes
source behavior in the stated bounded domain; **acceptance** describes executed
evidence, not all possible programs. An inventory row with legacy `missing`
does not mean its chapter has no implementation. An explicit capability
rejection is not an invalid-Go classification or a positive runtime pass.

Qualification source identity: `sha256:e86870884e77d3322d17d7d726bb6f40eaefb8a2be2f13cea165a0deeaf5db18` (1,558 frozen compiler/test inputs, excluding this README and feature map). It binds the tested a6e482c7 base plus this source/test slice; later peer publications are preserved, not silently claimed as compiled in this snapshot.
Execution lane **L** is host **pcc0**, owned **self** backend, **Linux x86_64**,
no applicable GC; official **Go 1.27.3 linux/amd64** is an external reference only.
PCC native-pcc1, Mac execution, full Go/standard library and shared runtime/GC
qualification are not established here. Cross-object tests do not renew Mac
execution evidence.

Legacy **schema1** `status` rows retain their historical qualification meaning.
The pointer-switch, aggregate-comparison/switch and direct-generic-function rows use the existing additive schema1 `implementation` and
`acceptance` objects: implemented source is distinct from acceptance, which is
**unrun** for this change. No schema2 migration or ignored JSON fields are used.
Historical evidence keeps its original revision and source hashes; it is not
rebound to a changed unit file or presented as a new execution.

| Existing feature ID | Implementation status | Acceptance status / test binding | Legacy schema1 status |
|---|---|---|---|
| `go.bootstrap-output-terminal-panic` | Implemented: Linux scalar output/terminal uncaught panic only | Original shard and authored native/reference obligations UNRUN | `missing` |
| `go.capability.builtin-io` | Explicit payload/target boundaries | Stable rejection obligations authored, UNRUN | `explicitly_unimplemented` |
| `go.expression-switch.scalar` | Implemented: bounded scalar/static-string expression switches | Qualified in L for SW-U / -P / -D only | `supported` |
| `go.array-range.fixed-value` | Implemented: bounded fixed value-array range and len/cap | Renewed in L for AR-U / -P / -D on the current unit file | `supported` |
| `go.integer-range.typed-bound` | Implemented: bounded typed/untyped integer range with one-time bounds | Qualified in L for IR-U / -P / -D and the listed full-width cases | `supported` |
| `go.pointer-array-range` | Implemented: bounded live pointer-to-array iteration | Qualified in L for PA-U / -P / -D and 19 full-width observations | `supported` |
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
| `go.capability.aggregate-comparison` | Implemented: admitted value arrays/records, component equality | Authored CI obligations in `test_aggregate_equality.py`; acceptance **unrun** | Replaced by explicit implementation/acceptance |
| `go.capability.aggregate-switch` | Implemented: captured value aggregate selectors and ordered cases | Authored CI obligations in `test_aggregate_equality.py`; acceptance **unrun** | Replaced by explicit implementation/acceptance |
| `go.generics.direct-value-functions` | Implemented: declaration-checked any/comparable direct functions, argument inference and shared instances | Authored CI obligations in `test_generic_functions.py`; acceptance **unrun** | Explicit implementation/acceptance |
| `go.capability.generic-boundaries` | Explicit constraint/storage/function-value frontiers | Diagnostic and official build-only obligations authored, **UNRUN** | `explicitly_unimplemented` |
| `go.capability.limit-generic-instances` | Explicit 128-instance session bound | Diagnostic and official build-only obligations authored, **UNRUN** | `explicitly_unimplemented` |
| `go.capability.array-bounds-panic` | Explicitly unimplemented boundary: array bounds panic | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.array-element-types` | Explicitly unimplemented boundary: array element types | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.blank-function-declarations` | Explicitly unimplemented boundary: blank function declarations | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.blank-method-declarations` | Explicitly unimplemented boundary: blank method declarations | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.blank-package-variables` | Explicitly unimplemented boundary: blank package variables | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.blank-struct-fields` | Explicitly unimplemented boundary: blank struct fields | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.blank-type-declarations` | Explicitly unimplemented boundary: blank type declarations | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.complex-types` | Explicitly unimplemented boundary: complex types | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.division-panic` | Explicitly unimplemented boundary: division panic | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.dot-imports` | File-scoped exported declaration lookup implemented | Authored source/native/reference obligations; all execution unrun | `missing` |
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
| `go.string-range.utf8` | Source implementation: byte-offset/rune UTF-8 iteration on existing immutable strings | All new unit/native/same-source reference obligations UNRUN in CI | Implementation `supported`; acceptance `unrun` |
| `go.capability.package-initialization` | Explicitly unimplemented boundary: package initialization | No positive acceptance; CAP-U checks this diagnostic boundary | `explicitly_unimplemented` |
| `go.capability.pointer-receivers` | Implemented: bounded direct non-retaining pointer-receiver methods | Qualified in L for PM-U / -P / -D and 23 full-width observations | `supported` |
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
| `go.capability.pointer-switch` | Implemented: bounded comparable-pointer expression switches | Authored complete feature obligations **UNRUN** | Explicit implementation `supported`; acceptance `unrun` |
| `go.local-pointer-regions` | Implemented: bounded local pointer alias/address/value-update semantics | Renewed in L for LP-U / -P / -D on the current unit file | `supported` |
| `go.nullable-direct-pointer-parameters` | Implemented: nullable locals and source-guarded non-retaining direct parameters | Renewed in L for NP-U / -P / -D on the current unit file | `supported` |

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
| IR-U | `tests/go/test_integer_range.py::test_integer_range_retains_bound_and_iteration_type` |
| IR-P | `tests/go/test_integer_range.py::test_complete_integer_range_program_main_executes_owned` |
| IR-D | `tests/go/test_integer_range.py::test_identical_integer_range_program_matches_official_raw_streams` |
| LP-U | `tests/go/test_pointers.py::test_nonescaping_pointer_types_and_aliases_lower_without_external_owner` |
| LP-P | `tests/go/test_pointers.py::test_deterministic_pointer_program_main_executes` |
| LP-D | `tests/go/test_pointers.py::test_same_deterministic_pointer_program_matches_official_raw_process_streams` |
| NP-U | `tests/go/test_nullable_pointers.py::test_source_guards_and_unevaluated_operands_admit_pointer_parameters` |
| NP-P | `tests/go/test_nullable_pointers.py::test_complete_nullable_pointer_main_executes_owned` |
| NP-D | `tests/go/test_nullable_pointers.py::test_identical_nullable_pointer_program_matches_official_raw_streams` |
| PM-U | `tests/go/test_pointer_methods.py::test_typed_call_shapes_keep_real_pointer_receivers_and_value_wrappers_distinct` |
| PM-P | `tests/go/test_pointer_methods.py::test_complete_pointer_method_main_executes_owned` |
| PM-D | `tests/go/test_pointer_methods.py::test_identical_pointer_method_originals_match_official_raw_streams` |
| PA-U | `tests/go/test_pointer_array_range.py::test_typed_range_retains_constant_length_call_and_capture_contract` |
| PA-P | `tests/go/test_pointer_array_range.py::test_complete_pointer_array_program_main_executes_owned` |
| PA-D | `tests/go/test_pointer_array_range.py::test_identical_pointer_array_originals_match_official_raw_streams` |
| CAP-U | `tests/go/test_diagnostics.py::test_capability_codes_are_explicit_source_located_and_fail_before_ir` (the existing source-located code-specific parameter cases) |

Additional NP obligations are explicit tests in `tests/go/test_nullable_pointers.py`:
`test_nullable_pointer_full_width_native_observations` has 30 actual 8-byte
observations (including aliasing and internal SysV stack pointer arguments);
`test_identical_graduated_frontiers_match_official_raw_streams` retains six exact
former nil-storage/parameter originals; and
`test_identical_imported_pointer_project_matches_official_raw_streams` compares
the unchanged two-package source graph. Object hashes in the observation records
are recorded from emitted in-memory bytes; retained ELF binaries are separately
hashable. The 23
`test_official_go_typechecks_identical_valid_unsupported_originals` cases build
native official-Go executables **without running them** and do not qualify PCC
nil-panic/escape support.

The prior published complete Go selections passed **900 ordinary nodes** (including31
simulated reference-protocol checks, not executions) and **619 integration nodes**.
Integration categories are376 standalone owned-native checks,45 raw-process
reference comparisons,16 terminal-status/payload comparisons with unequal raw
panic reports,2 real broken-stderr comparisons,125 normal source rejections,
52 valid-but-unsupported build-only controls,2 external nil-panic frontiers and
one diagnostic-only observer. The242 regular official build receipts comprise
117 successful builds and125 normal rc1 rejections; building is not execution.
No skip or expected-failure conversion hid a failure. The earlier ordinary
57PASS/1FAIL/127unfinished and integration53PASS/5FAIL attempts remain failed
history; their corrections were authored IR/ABI-observer tests, not production
semantics or shared-backend changes. The assembler's original memory-immediate
compare remains unsupported; the successful observer uses an equivalent load
and register comparison, not a backend-support claim.

The prior published pointer-array selections passed **863 ordinary nodes** (31 simulated reference-
protocol checks and 832 other checks) and **561 integration nodes** (355 standalone
owned-native, 43 positive external-reference comparisons, 118 normal-source
rejections, 42 build-only valid-unsupported controls, two external nil-panic
frontiers and one diagnostic-only observer). All 205 regular official build
receipts bind the original sources: 87 successes and 118 normal rc1 rejections.
Build successes are not executions. The separate diagnostic Go-test observer
remains excluded from acceptance. These are test outcomes, not 1,424 supported
features or a whole-language completeness percentage; no failed case was skipped
or relabeled to obtain them. Prior pointer-method 833/520 and integer-range
791/465 outcomes remain historical evidence, not substitute results for this slice.

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

The pointer-switch and aggregate-equality source changes have **not** run either
selection locally; the counts above remain historical and do not qualify them.
The second command requires explicit `PCC_GO_REFERENCE` and
`PCC_GO_REFERENCE_CACHE`. It rebuilds final native/reference outputs; retained
binaries or old process results are never substituted as a new gate. Raw logs,
private artifact paths and recovery receipts are not part of this source README.

The Linux builtin writer blocks SIGPIPE only on the calling thread, preserves
preexisting pending signals, consumes its own broken-pipe notification and
restores the exact prior signal mask. It never changes process-wide signal
dispositions. This applies equally to calls through exported Go functions.
Failure to save/restore the mask or drain a new SIGPIPE fails closed with
process status 2. Failure to query pending signals suppresses the write, restores
the original mask and returns; an enclosing terminal panic still exits 2.
These OS-policy boundaries are not described as Go source panics.
Native and official broken-stderr tests use real closed-reader pipes; unavailable
stderr bytes are not fabricated. Named-scalar and multiline panic reports retain
full implementation-specific stderr differences rather than claiming byte identity.
The initial authored rune-literal fixture is retained as a separate valid but
unsupported parser frontier; this output slice does not implement rune syntax.

### Pointer expression switches — source implementation, acceptance unrun

Comparable pointer selectors use the existing typed equality operation and
single captured selector value. Nil and named-pointer cases follow ordinary
assignment/comparison rules; cases retain left-to-right, top-to-bottom evaluation,
first-match dispatch, delayed default and direct fallthrough edges. Pointer
identity is not pointee equality, and no distinctness is inferred from pointer
types or from addresses of zero-size variables.

The existing CFG captures an analysis-only pointer fact alongside the selector.
A nil case refines only its actual dispatch predecessors. Equality with a proven
nonnil pointer can prove the other operand nonnil without replacing its lifetime;
inequality does not. Fallthrough bypasses the next case guard, loop continuations
reach the post statement, and reassignment invalidates the binding's old fact.
No runtime, backend, allocator, new lifetime extension or noalias promise is added.

`tests/go/test_pointer_switches.py` authors positive typing/IR checks, exact former
capability originals, fourteen full-width native observations, the complete
`pointer_switches.go` main, same-input raw PCC/official-Go comparisons, strict
invalid-source rejection controls and valid-but-unsupported build-only controls.
All new execution is **UNRUN**. No old rejection PASS is reclassified as a positive
execution. Historical ledger evidence, including the prior local-pointer unit
file hash, remains tied to its original revision; a single current source tree
cannot replay source hashes from several historical revisions.

Pointer-valued returns, pointer-bearing global/aggregate storage, escaping aliases,
method values, evaluated indirections without a source proof, and addressed
composite allocation remain explicit separate boundaries. General type switches,
interface dispatch, shared GC and native-pcc1 execution are not implied.

Rules: [expression switches](https://go.dev/ref/spec#Expression_switches),
[pointer comparison](https://go.dev/ref/spec#Comparison_operators), and
[evaluation order](https://go.dev/ref/spec#Order_of_evaluation).

### Aggregate equality and switches — source implementation, acceptance unrun

`==` and `!=` now compare the values of currently admitted fixed arrays and
records, including nested records, scalar arrays within records, and static-backed
string fields. Named types retain declaration identity; a named aggregate and an
unnamed value with identical underlying type may compare, but two distinct defined
types require a source conversion. Aggregate ordering/arithmetic and comparison
with nil remain invalid. Empty aggregates still evaluate both source operands.

Lowering captures each operand once, then compares fields in declaration order
and array elements in index order, stopping at the first unequal component.
String fields use the existing bounded unsigned-byte loop, not descriptor identity.
There is no `memcmp`, padding comparison, allocation, new runtime or backend path.
Existing type/layout limits also bound the emitted component-comparison walk.
Aggregate switches use the captured selector value, ordered case evaluation,
first-match/default behavior and genuine fallthrough edges. Repeated aggregate
case expressions are permitted because these values are not Go constants.

`tests/go/test_aggregate_equality.py` supplies normative typing/IR obligations,
15 full-width native observations, an original main, five same-input program
raw pairs (the main plus four exact former frontiers), strict invalid-source
rejections, valid-unsupported build-only controls and an imported-record raw
pair including private fields. These are **authored CI tests, all execution
UNRUN**, not observed acceptance. Old diagnostic test outcomes and every ledger
evidence row keep their original source identity; none is relabeled as a pass
for this implementation. No local imports, tests, compilers or probes ran.

Array-of-record/string/nested-array storage, blank or embedded fields, tagged
records, pointer-bearing aggregates and interface fields remain separate storage
frontiers. Unproven nil/bounds operations remain rejected even when comparing
empty aggregates. Existing locked `math/bits` inputs still have independent
`error`/runtime/division frontiers; the five-source official adapter is not
aggregate coverage. This rule is not a whole-project, full-Go, standard ABI,
Mac, native-pcc1 or shared-GC claim.

Rules: [comparison operators](https://go.dev/ref/spec#Comparison_operators),
[assignability](https://go.dev/ref/spec#Assignability),
[expression switches](https://go.dev/ref/spec#Expression_switches), and
[evaluation order](https://go.dev/ref/spec#Order_of_evaluation).

### Direct generic value functions — source implementation, acceptance unrun

Direct functions can declare `any` and `comparable` type parameters and use
explicit type arguments (including a partial prefix) or argument-constrained
inference. Typed argument information precedes untyped constant defaulting;
normal source assignability and constant representability follow substitution.
Aliases share type identity; distinct defined types do not. Result context alone
does not infer a direct call's otherwise unconstrained type parameter.

Each generic body is checked unconditionally using declaration-bound opaque type
parameters, including unused functions. `any` permits value transport and zero
values, not arbitrary operators; `comparable` additionally permits equality and
expression switches. Constraints resolve to predeclared identities only after
checking signature/package/import shadowing. Existing predeclared-name shadowing
frontiers remain explicit; a shadowed spelling is never silently treated as the
builtin. Invalid generic bodies are not legalized by a favorable concrete call.

One package-graph `pcc.support.instantiation.InstanceRegistry` owns concrete
identity, recursive placeholders, FIFO completion and sticky failures. Existing
C++/Rust consumers and the shared registry are unchanged. Imported generic bodies
retain their declaration's source/import/type scope. All ordinary and abstract
source bodies are typed before instance draining; concrete and abstract closed
call-graph/nullness/lifetime checks finish before IR. Generic templates and
signature-only abstract selections never reach lowering. Concrete instances use
the existing value layouts, ABI, lowering and owned backend, with no erasure,
dictionary runtime, new allocator, noalias assumptions or separate Go runtime.

The concrete type-argument domain is existing admitted nonpointer scalar, array
and record values. Ordinary concrete pointer parameters inside a generic function
still require their own source nil guards and non-retention proof; aliasing is
preserved. Pointer type arguments, dependent pointer/record layouts,
addresses of opaque parameter values, union/underlying/exact/named/method
constraints, generic data declarations/receivers and stored generic function
values remain explicit boundaries. Function type-parameter count is bounded at
256; concrete instances are bounded at 128 per supplied package graph.

`tests/go/test_generic_functions.py` authors unconditional invalid-declaration
checks, identity/inference/scope controls, 15 full-width native observers, a
complete original program and imported package-graph raw pairs, strict actual
reference rejection obligations, and valid-unsupported build-only controls.
The simulated sticky-failure test is mechanism-only, not Go execution evidence.
**All new acceptance is UNRUN**: no local imports, tests, compiler calls or
execution probes ran. Historical evidence rows retain their original revisions
and hashes. This is not complete generics, unchanged-project/stdlib success,
Mac/native-pcc1 or shared-GC qualification.

Rules: [type parameters](https://go.dev/ref/spec#Type_parameter_declarations),
[instantiation](https://go.dev/ref/spec#Instantiations),
[type inference](https://go.dev/ref/spec#Type_inference),
[constraint satisfaction](https://go.dev/ref/spec#Satisfying_a_type_constraint),
and [scope](https://go.dev/ref/spec#Declarations_and_scope).

#### Fixed-array generic signatures — source implementation, acceptance unrun

Fixed `[N]T` parameters/results, local values and composite literals now retain
the abstract element type through declaration checking. Direct calls infer `T`
from array elements; a named outer array can match the unnamed formal, but
element types unify exactly and lengths must agree. Aliases retain identity;
different defined element types never unify by representation. Explicit or
partial type arguments, typed-before-untyped inference, recursive forwarding,
multiple results and imported defining scopes use the same instance registry.

Declaration checking permits transport, indexing, range and constant length
operations on `[N]T`; equality and expression switches still require comparable
elements, even for an unused generic function or a zero-length array. Signature
substitution rechecks concrete array storage before any instance reaches IR.
Only existing integer/bool element representations are admitted. Nested,
string/record/pointer-element arrays and addresses of abstract array values
remain unsupported, rather than obtaining an unchecked layout through generics.

`tests/go/test_generic_arrays.py` preserves the exact former `[2]T` capability
control as a positive source/native/reference obligation. It adds 11 full-width
native observers, same-original raw execution pairs, nominal/length/element
rejections, unused invalid bodies, valid-unsupported build-only controls and an
imported package pair. These are **authored CI obligations, execution UNRUN**;
no local imports, tests, compilers or probes were run. No backend/runtime or
shared-GC changes, complete generics or real-project acceptance is claimed.

Rules: [arrays](https://go.dev/ref/spec#Array_types),
[type unification](https://go.dev/ref/spec#Type_unification),
[assignability](https://go.dev/ref/spec#Assignability),
[comparisons](https://go.dev/ref/spec#Comparison_operators), and
[evaluation order](https://go.dev/ref/spec#Order_of_evaluation).

### Dot imports — source implementation, acceptance unrun

An explicit dot import exposes the dependency's exported package declarations
only in the importing file block. Lookup reuses the existing canonical type,
variable, method and generic-function owners; declarations are not copied into
the package block. Disjoint dot imports coexist. Export collisions with other
file imports or any package declaration, including a sibling file, diagnose.
Local variables/type parameters shadow file imports, private fields retain their
original package ownership, and each actual import has independent use tracking.

`tests/go/test_dot_imports.py` authors source/IR, full-width owned native and
same-input official-Go package-graph comparisons for types, methods, shared
storage, generics, Unicode exports, shadowing and file isolation, plus strict
invalid graph controls. The old import-only diagnostic source is unchanged: it
now requires supplied dependency source and, once supplied, still rejects its
unused import. It is not relabeled as positive execution evidence.

The unchanged original Go 1.27.3 `math/bits/bits_test.go` dot import is retained by
the real parser before a later syntax failure. This is a source-frontier
obligation, not testing/runtime closure or upstream test execution. The original
normal package still rejects `error` at `bits_errors.go:12:19`; interface values,
linkname/runtime globals, blank-import initialization and genuine error-payload
panic support remain explicit missing contracts. No source or test is rewritten
and no runtime/backend changes are included. All new execution is **UNRUN**.

Rules: [imports](https://go.dev/ref/spec#Import_declarations) and
[declarations and scope](https://go.dev/ref/spec#Declarations_and_scope).
