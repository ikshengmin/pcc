# Experimental Rust frontend: owned scalar and aggregate slices

`compile_rust(source, experimental_rustc_checked=True, overflow_checks=False)`
returns a `RustCompilation` containing the public PCC IR module, IR text and
source-function-to-symbol map. The frontend has its own lexer, parser, name
resolver, integer inference, type checker and control-flow lowering. It does not
strip Rust syntax, translate into Python/C, invoke rustc, or substitute a Python
interpreter. The shared PCC self backend emits and links the native program.

**Not a safe primary Rust compiler:** no general owned Rust borrow checker exists.
The legacy experimental flag enables **value-only** inputs; it is never proof
that rustc ran and cannot authorize any reference-bearing program. Unused
reference declarations, shared/mutable receivers, borrowed arguments, nested
reference types and semantically synthesized references all require a genuine
source/options-bound `CheckedRustSource` from the explicit external checker.
The default call without either explicit value-only opt-in or a checked token
rejects compilation. Host-PCC evidence is not native pcc1/fixed-point proof.

### Explicit edition identity

`compile_rust`, `check_rust_source` and `compile_rust_checked` accept the integer
`edition=` option (2015, 2018, 2021 or 2024), retaining 2021 as the compatibility
default. It reaches the existing Parser/crate policy, inline module identities,
and `RustCompilation.edition`; this is context propagation, **not complete
edition conformance**. The source-map parser already requires an edition.

The explicit transitional checker puts the selected edition in its actual
rustc command and source-bound provenance. `checked.compile_owned()` retains
that selection; a direct `compile_rust(..., rustc_check=checked, edition=...)`
must match it, including when the caller omits the option and defaults to 2021.
A genuine 2021 token cannot authorize 2024 parsing of the same bytes. Explicit
marker/local-trait checks revalidate the same edition; caller flags and
provenance-shaped records remain non-certifying. There is no fallback to a
different edition if rustc or PCC rejects the input.

The edition tests keep unchanged source fixtures and separate owned-context,
actual external certification, and native/reference observations. All new
execution obligations are **UNRUN pending CI**. No Cargo dependency graph,
build script, macro expansion, core/alloc/std, or orjson extension qualification
is implied, and edition 2024 selection does not satisfy orjson's Rust 1.95 MSRV.

## Reference lowering requires actual external checking

Use `check_rust_source(...)` followed by `compile_rust(..., rustc_check=checked)`,
or `compile_rust_checked(...)`. The checker actually invokes the audited rustc
on unchanged bytes with exact target/options and records successful metadata.
The owned phase uses only PCC parsing, semantics, IR and backend. It consumes no
MIR and launches no rustc process. A caller-created provenance dataclass or the
legacy boolean cannot substitute for the adapter-minted token.

### Certified crate lint transition

Only crate-root `allow`, `warn`, `deny` and `forbid` lists naming
`missing_docs` can use the actual checked-source route. PCC retains their
immutable syntax, source spans and order. The successful same-source rustc
check owns suppression, warnings, errors and forbidden overrides; this is not
an owned lint implementation. The value-only flag does not authorize it.
`RustCompilation.external_rustc_lints` records the literal owner
`external_rustc_lints`, original attributes, provenance and exact metadata-check
stderr bytes. Those bytes are retained in memory and hash-checked against the
original provenance, not reconstructed from a digest or filtered into invented
PCC warnings. This remains the trusted audited-checker boundary, not signed
attestation.

Other lints/groups, reasons, `expect`, and module/item/member lint attributes
remain explicit unsupported boundaries. General `cfg`, macros and compiler
context attributes are not enabled by the token. The unchanged real-project
object gate remains strict. The authored lint-bearing native
program, real rustc checks and complete affected Rust lanes are **UNRUN pending
CI**. No original-project, owned-lint or native pcc1 acceptance is claimed.

### Authenticated crate cfg_attr policy

The existing successful checker command admits no explicit `--cfg` flags or
dependencies. A source/edition/target-bound `RustConfiguration` therefore has
an empty feature set; Cargo feature defaults are never inferred from a name.
For crate-root `cfg_attr`, PCC evaluates `feature = "..."`, `all`, `any`, `not`
and literal predicates against that closed context, retaining the immutable
original predicate/payload metadata and source spans. An enabled `no_std`
payload uses the existing crate-injection policy; nested cfg_attr is evaluated
in the same context. An inactive payload is retained and not applied. Unknown
active attributes and built-in/target cfg predicates fail explicitly rather
than being discarded or guessed from the host. Other cfg inputs require a
later authenticated adapter; a caller flag or provenance record cannot supply
this context.

Actual CI 41592994473 reached the original crate's cfg_attr at line 17 after
successful rustc certification and lint admission. This new policy and its
original-crate, native/reference and rejection obligations are **UNRUN pending
CI**. The next outer cfg at line 19 is a source prediction, not a project pass.

Supported shared/mutable references lower to target pointers, preserving
mutability, field addressing, receiver/argument evaluation and reference-slot
rebinding. On the checked route the old partial loan/move model is **not** an
admission gate: rustc owns borrowing/lifetime/move validity, while PCC still
checks its own non-borrow language semantics and explicit lowering boundaries.
Unsupported reference representations remain explicit failures; this does not
mean every program accepted by rustc is implemented. The legacy partial loan
model is retained only for internal, non-certifying analysis tests that return
facts, never pointer IR or executable objects. `borrow_checked_by_pcc` stays false.

The foreign boundary is **not** widened: `extern "C"` shared parameters still
require the separately qualified non-generic `repr(C)` integer record shape.
Other reference shapes use PCC's private ABI only. No allocator, external
unwinder, new GC or independent runtime is introduced.

Dependency evidence remains the unchanged pinned Rust 1.90.0 `core_option.rs`
(commit `1159e78c4747b02ef996e55082b704c09b970588`, SHA-256
`53b549b18552144e911416aa2773e8b726da88c190d5cb27007c3c08740894a5`,
MIT/Apache-2.0 pins in `tests/rust/upstream/README.md`). Its original `as_ref`
method at line 737 also needs const functions, dereference/pattern borrowing and
a returned `Option<&T>` with real lifetimes; this bounded slice does not compile
that method or the full crate. The eight `shared_*.rs` programs are clearly
authored tests, not extracted upstream successes. Their spec-linked unit,
owned-native, target-certified Darwin cross-only and actual rustc runtime/rejection checks live
in `test_rust_shared_loans.py`. Missing target std/core is an explicit unavailable
prerequisite, not permission to reuse a Linux token; unrun tests are not support.

## Admitted language

- A crate of functions and named-field structs (including the bounded generic
  declaration domains below), optionally `#![no_std]`, `pub`, and
  `extern "C"` definitions. Forward calls and recursion resolve before lowering.
- `i32`, `i64`, `u32`, `u64`, `bool`, unit; named structs and nested tuples;
  typed parameters/returns; contextual
  inference of unsuffixed integers (default i32 after function constraints).
- Initialized `let`, `let mut`, shadowing and lexical scopes; assignments and
  `+=`, `-=`, `*=`. Unit arguments have no payload at the private Rust boundary.
- Block/tail expressions, `if`/`else`, `while`, `break`, `continue`, `return`;
  short-circuit `&&`/`||`; integer arithmetic/bitwise/comparison operators,
  boolean bitwise/comparison operators; integer casts and bool-to-integer casts.
- Named-field construction (including shorthand), source-order initializer
  effects, field mutation, tuple indexing, and private by-value aggregate
  parameters/returns. Aggregate temporaries are allocated once in function entry.
- Place-sensitive move validation: whole/partial moves, field reinitialization,
  branch/short-circuit joins and loop-backedge fixed points. Scalars/unit are Copy;
  tuples are Copy exactly when all their elements are. Named structs are NOT
  implicitly Copy. User-defined Copy, derive and Drop remain unsupported.
- Nested ordinary comments and located syntax/type errors, including unreachable
  code. Documentation comments and admitted doc metadata retain their original
  attachment and spans; unsupported semantic forms are never discarded.

Arithmetic explicitly matches Rust with overflow checks disabled: dynamic
add/subtract/multiply/negation wrap at the declared width. Out-of-range literals and overflow in foldable literal/operator expressions
(including casts, bitwise operations, division and grouping) are rejected. This
is not rustc lint parity or general constant propagation through local state. Division truncates toward zero; signed
remainder follows the dividend. Until owned panic support lands, division and
remainder require a statically known nonzero divisor (and not signed -1).
Dynamic/panic-capable division is rejected, never emitted as unsafe raw `sdiv`.
Shifts are currently rejected instead of silently using a target's shift mask.

By default symbols are `__pcc_rust_<name>` and returned in `symbols`; this is **not rustc's
Rust ABI or symbol mangling**. The finite `extern "C"` scalar boundary uses
32/64-bit integer parameters and integer/unit return values. All aggregate
foreign arguments/returns remain rejected, including repr(C), pending a separate
cross-language aggregate ABI qualification. No stable Rust
cross-compiler ABI is claimed; no native Rust standard-library objects are linked.

## Boundaries and next owners

This is not C-level language/ecosystem parity. General references/lifetimes/borrowing,
Drop analysis, user-defined Copy, zero-field structs, tuple structs, slices/dynamic array bounds, general patterns/match ergonomics, modules/imports, never-typed tuple operands,
full generics/traits/impls, closures, macros, pointer operations, heap allocation,
`core`/`alloc`/`std`, panic/unwind, and the shared public driver are unfinished.
Unsupported syntax fails closed. Accepted no_std arithmetic does **not** mean
`core` or `alloc` have been compiled. No original input program is rewritten.

The next library path is unchanged `core` source through owned parsing,
monomorphization, lang items and intrinsics, then unchanged `alloc` with the
shared pcc-Python allocator/runtime and specified C-ABI interop. There will be no
independent GC or external-unwinder target dependency. Borrow checking remains
an explicitly missing prerequisite before replacing rustc as primary compiler.

## Tests and references

`tests/rust/programs/*.rs` are immutable inputs to public-IR/self-backend/owned
ELF execution, with full 4-byte/8-byte return observations through an owned
Linux syscall observer rather than truncated exit status. Tests forbid subprocess creation during compilation, assembly and
linking. A separately gated **development-only rustc reference** checks those
same files when installed; its absence is not a passed reference comparison.
These are focused frontend/backend proofs, not upstream-crate or bootstrap gates.

Semantics references: [Rust operator expressions](https://doc.rust-lang.org/reference/expressions/operator-expr.html)
and [block expressions](https://doc.rust-lang.org/reference/expressions/block-expr.html).

### Research input, not implemented borrow checking

The user supplied *Flow Typing: A New Lens on Linearity*, Sky Wilshaw and Graham
Hutton, 30-page manuscript (`flow-typing(1).pdf`). Its first page carries a 2027
POPL template with placeholder DOI; no publication-status claim is made here.
It translates a new flow-typed calculus into linear lenses, not arbitrary Rust
into a substitute borrow checker. Useful future design inputs are explicit
resource-state transitions, stable binding identities, branch-state joins and
preserving effect order. In particular, page 22 §6.3's escaping closure is a
future negative lifetime test, while page 22 §6.5's returned pair of mutable
borrows is a Rust capability the paper's calculus cannot express. Page 22–23
§6.6 supplies higher-ranked-lifetime tests for future trait/closure support.
These examples must remain original Rust tests when the relevant features land;
we must not reject valid Rust merely to fit the paper's different calculus.

## Explicit target selection and owned object output

```python
from pathlib import Path
from pcc.frontends.rust import compile_rust

result = compile_rust(
    source,
    experimental_rustc_checked=True,
    target_triple="arm64-apple-darwin",
)
Path("program.o").write_bytes(result.emit_object())
```

`target_triple` defaults to `x86_64-unknown-linux-gnu`, preserving the existing
Linux path. The admitted target families are x86-64 Linux and arm64 Apple Darwin
(including the owned backend's aarch64/macOS aliases). Linux is explicitly LP64
with gnu/musl or no-environment aliases; x32/other environments and Darwin
environment suffixes are not admitted. Other targets, malformed
triples, and post-compilation manual target/layout changes fail explicitly.
Select Darwin at the entrypoint rather than editing the returned module header.

Target selection sets both the IR triple and a little-endian, 64-bit-pointer
data layout before lowering, then `emit_object()` dispatches through the shared
owned IR/object boundary. ELF and standard arm64 Mach-O bytes are actually
assembled; no rustc, LLVM, C compiler or system assembler is invoked. i32/i64 and
u32/u64 remain explicitly 32/64 bits on both targets; neither usize nor a default
Rust aggregate layout is inferred from the Python host. `symbols` contains IR
names; the Darwin object boundary adds the standard leading underscore.
User-supplied `module_name` is a label, hex-encoded before entering textual IR;
it cannot add IR lines and does not change exported function symbols.

Linux tests execute the owned ELF result and compare every return byte. Darwin
checks run cross-emission on Linux, inspecting Mach-O CPU/filetype, symbols and
branch relocations for unchanged Rust sources. **They do not execute on a Mac**,
certify a Darwin runtime/link path, or replace target-device qualification.

## Aggregate layout and original-source frontier

Named structs retain nominal type identity; tuples have structural type identity.
The current default Rust storage is a PCC-private declaration-order structural
IR layout. This is a permitted implementation choice, **not** a guarantee that
Rust defaults to C layout, not rustc's layout, and not a stable cross-compiler
Rust ABI. `#[repr(C)]` is parsed and retained, but does not by itself authorize an
aggregate foreign signature. No pointer/address/layout reflection is admitted.
General reference types, packed/aligned/transparent representations and arbitrary
field projections through pointers remain unsupported; the qualified readonly
repr(C) parameter boundary below is narrower.

Aggregate tests include 16-byte and 24-byte values, nested Copy tuples, unit
fields and zero-sized values, aggregate-valued branches, source-order
construction and partial moves.
The Linux path observes the full scalar result of each unchanged program;
Darwin uses the same sources for cross-Mach-O emission and header/symbol checks.
Cross-emission is not Darwin aggregate execution or external ABI qualification.

Move states belong to resolved bindings plus field paths, not Python references.
A Copy field can be read without consuming its struct; moving a non-Copy field
invalidates only that subtree, while moving the parent invalidates every field.
Replacing the exact moved field restores it; assigning below a moved ancestor
is conservatively rejected until that owning place is replaced. Joins include
all reachable branches; no constant-condition reasoning is claimed. Unreachable
code still receives name/type checks, but does not create live ownership edges.
This borrow-free move analysis is not a borrow checker or a Drop implementation.

The unchanged existing Meson `rust/15 polyglot sharedlib/adder.rs` is retained as
`tests/rust/upstream/meson_adder.rs` with its original blob and SHA-256 recorded
in the adjacent provenance file. It needs `#[repr(C)]`, a named `Adder` field,
then extern declarations, `&Adder`, `unsafe`, `no_mangle`, and external `zero`
symbols. The first object-boundary integration below now supplies these frontend
capabilities using the original C definitions. The full shared-library project
and its allocation dependencies remain unfinished.

The pinned upstream [Rust 1.90.0 core/cmp.rs](https://github.com/rust-lang/rust/blob/1.90.0/library/core/src/cmp.rs)
contains `Reverse<T>` (line 645), requiring tuple structs, generic parameters,
attributes/derive, `repr(transparent)`, traits, full method resolution and references. Those
unmodified dependencies remain unsupported; neither `core` nor `alloc` compiles
from this batch. Named structs/tuples are only a prerequisite domain, not a
replacement source rewritten to resemble that upstream library.

### Shared compile-time instantiation boundary

The shared `pcc.support.instantiation.InstanceRegistry` now owns session-local
identity registration, placeholders, FIFO scheduling and sticky failure states
for both Rust and C++ consumers. Keys include declaration identity and complete
semantic arguments, never serialized host addresses. A recursively requested
function reuses its in-progress placeholder; this is **not** an invalid layout
cycle. Rust checks finite value layouts separately. Trait obligations, coherence,
associated types, target ABI admission and lifetime constraints remain language
semantic responsibilities, not guessed by the worklist. No generic runtime,
allocator or GC is introduced.

## First original Rust/C object boundary

The edition-2021-compatible slice admits non-unwinding `extern "C"` (also the
implicit `extern` spelling), external function declarations, unsafe functions
and lexical `unsafe {}` blocks. An imported function is unsafe to call and is
rejected outside an unsafe context; unsafe does not disable any type, move,
mutability or reference checks. `#[no_mangle]` is supported only on extern C
definitions and preserves the source name. Other functions retain private PCC
names; imported symbols preserve the external name. External statics, variadics,
link attributes and edition-2024 attribute forms are not yet supported.

`C-unwind` is explicitly rejected: there is no owned Rust panic/unwind or
cross-language exception adapter here. Imported C functions must obey their
non-unwinding ABI and all declared contracts. No external unwinder is linked.

`&Record` parameters are supported only for an explicit `#[repr(C)]` named
record whose fields are i32/i64/u32/u64. The two admitted LP64 targets use their
existing backend's natural scalar struct layout and 64-bit pointer ABI. Field
reads use typed GEP/load, not a copied struct or a Python object. Shared
references are Copy, may be forwarded or rebound as values, and never permit
referent writes. Mutable **foreign** references, reference casts/returns and references stored
inside aggregates remain explicit capability gaps. The checked private-reference
mode below is separate from this foreign ABI. Default Rust aggregates are not an
FFI layout.

Every public reference compilation requires actual unchanged-source checking;
PCC still does not implement a borrow checker. Foreign callers
must additionally provide nonnull, aligned, initialized, live objects and must
not mutate the shared referent during its lifetime. The compiler does not prove
these foreign obligations, add noalias promises, or create a new allocation owner.

The unchanged Meson `adder.rs`, `zero.c` and `zero_static.c` exercise actual Rust
extern calls into objects from the existing C frontend. Linux tests observe
12 full result bytes, including a high-bit result and a negative result; a
separate mixed-width repr(C) reference fixture observes all 8 result bytes.
Darwin is cross-object/relocation evidence only. This does not compile the whole
Meson shared-library/malloc/free project, or core/alloc. See the upstream test
provenance rather than treating a library-shaped fixture as project completion.

### Enums and match

Unit-only nominal enums admit qualified `Type::Variant` constructors, checked
explicit/incrementing discriminants, and integer casts. Explicit integer repr
supports i32/i64/u32/u64; default logical discriminants use the admitted targets'
64-bit isize range; this bounded enum domain still accepts only unsuffixed
constant expressions, separately from ordinary isize source arithmetic. Default storage is a PCC-private i64 tag, **not rustc's
repr(Rust) layout**. Enum values are not automatically Copy or PartialEq. All
foreign enum parameters/results remain rejected, even with integer repr.

`match` supports bool/integer/unit-only-enum/unit and nested tuple scrutinees;
literal, qualified variant, wildcard, tuple/grouped and or-patterns. Pattern
coverage uses typed constructors, integer literal partitions and an other-value
class, rather than assuming a final arm is a default. Matrix rows/coverage work
have explicit implementation limits. The scrutinee is evaluated once and arms
are selected in source order. Binding-free place inspection does not move the
place; value expressions still consume their non-Copy operands. Live arm exits
join move states, including loop backedges, break/continue and early returns.
Tag inspection checks only the owning enum place, while literal patterns and
value bindings check their exact subplaces. Wildcards do not consume fields.

Empty enums, named-field payload variants, ref/match ergonomics, guards/ranges,
trait-derived Copy, macros and general const evaluation remain explicit gaps.
No enum code imports a runtime, allocator or unwinder. The unchanged official
Rust sources in `tests/rust/upstream/` remain frontier inputs, not core/alloc or
upstream-project qualification. PCC still has no complete borrow checker. Value-only opt-in is not a proof;
every reference-bearing public input requires the actual external transition. Darwin is still cross-object emission only.

Specification references: [enums](https://doc.rust-lang.org/reference/items/enumerations.html),
[match](https://doc.rust-lang.org/reference/expressions/match-expr.html),
[patterns](https://doc.rust-lang.org/reference/patterns.html).


Tuple payload variants and by-value match bindings extend that domain. The
private representation is a public-IR struct containing the typed tag followed
by a distinct typed struct slot for each payload variant, not a union or a C
layout promise. Every size, offset and alignment is owned by the existing
selected-target layout/backend path; no host-size arithmetic is used. Inactive
storage is defined zero bits solely for IR storage hygiene, **not initialized
Rust values**. Pattern predicates branch on the tag before inspecting a payload,
including nested enum tags. Foreign enum ABI and integer casts of payload enums
are rejected. Enum storage/Copy behavior is not special-cased for Option/Result.

Constructor arguments evaluate in source order. Match bindings have their own
arm scope; `mut` applies to the new binding, not the scrutinee. Copy fields are
copied, non-Copy fields are moved by variant/field path. Unmoved tuple/record
fields remain readable, but an enum discriminant cannot be inspected after any
of that enum's payloads have moved. A wildcard does not inspect or consume a
place. Whole-owner use after a partial move is rejected, and whole assignment
restores initialization. Move joins retain early-return and loop
break/continue behavior. Bindings inside or-patterns remain an explicit gap;
binding-free or-patterns retain constructor-aware coverage.

Only types without a supported destructor enter this slice: primitive values,
unit, recursively admitted tuples/records/enums. References stored in payloads,
Drop implementations, user-defined Copy/traits, borrowing patterns and general
lifetime checking are rejected, not implemented by discarding destructor calls.
There is no heap allocation or private drop/GC/unwind runtime. Full original
Rust 1.90.0 Option/Result inputs still fail at documentation attributes and need
generics, derives, impls, macros, lang items, references and owned intrinsics;
the PCC payload programs are separate specification fixtures. The unchanged
`payload_partial_moves.rs` is a retained invalid input: newly enabled rustc
reported E0382 for rematching the partially moved enum. Both frontends must now
reject it; `payload_tuple_partial_moves.rs` is a separate valid tuple-partial-move
program, not a rewrite or relabel of that failed original.
# Generic data declaration contract

Named structs and tuple-payload enums may declare unconstrained type parameters.
Type positions use explicit applications (`Wrap<u64>`); constructors use Rust
turbofish syntax (`Wrap::<u64> { value: 7 }`, `Maybe::<u64>::Some(7)`). An enum
pattern may use `Maybe::Some(value)` when the scrutinee already supplies that
exact nominal instance. Constructor inference, `_` arguments, specialized impls/reference methods,
trait bounds, defaults, where clauses, lifetimes and const generics
remain explicit unsupported capabilities, not erased syntax.

This is a declaration-checked type facility, not C++ template-body deferral:
**every** data declaration is resolved and checked, even if unused. Duplicate or
unused parameters, missing types, wrong arity and recursive/growing value layouts
are rejected before lowering. Function declarations use the abstract checking
contract below; an invalid unused body cannot evade checking by never being
instantiated.

The shared `pcc.support.instantiation.InstanceRegistry` owns registration,
placeholder identity, failure state and deterministic FIFO materialization inside
one checker context. C++ uses the same worklist for selected function instances;
its in-progress recursive function placeholders are not rejected as Rust layout
cycles. Rust declaration/finite-layout checks, type identity and resource limits
remain frontend-owned. The key is definition identity plus complete type arguments;
neither in-memory identity keys nor type parameters become IR values.
Substitution creates independent field/payload types; equal machine widths do not
unify different nominal instances. The existing move/partial-move, match coverage,
evaluation order and Copy rules then apply unchanged: a named generic struct/enum
is not implicitly Copy, and unsupported references/Drop are not synthesized away.
Concrete types lower through the existing public IR and selected target layout.
There is no global cross-target layout cache, generic runtime, allocator or new
calling convention. Generic `&repr(C)` instances remain unqualified for foreign
calls; private aggregate values do not promise rustc or C layout.

Implementation limits are explicit: 16 parameters/arguments, 16 nested argument
lists, 128 logical nodes per expanded type, 128 concrete data instances, and 4096
layout steps / 32 declaration-expansion depth. These stop growing recursive types
without turning recursion overflow into acceptance. Trait-bound, lifetime and
method obligations must be added without replacing declaration-time abstract
checking with concrete-body acceptance.

The full pinned Rust 1.90.0 `option.rs` / `result.rs` remain unchanged dependency
evidence (see `tests/rust/upstream/README.md` and original dual licenses). Their
generic enum declarations at lines 593 and 548 motivate this domain, but their
first documentation-attribute frontier, imports, derives, full impl/method obligations, trait and
closure bounds, references, lang items and intrinsics are still unmet. Authored
generic programs are specification fixtures, **not** original core/alloc success.
Development reference checks remain explicitly unavailable when their tool or
target library is absent. The public flag enables value-only experimental input;
reference-bearing programs require actual source-bound external checking.

References: [generic parameters](https://doc.rust-lang.org/reference/items/generics.html),
[expression/pattern paths](https://doc.rust-lang.org/reference/paths.html),
[unused parameters](https://doc.rust-lang.org/error_codes/E0392.html).


## Generic function declarations and concrete instances

Unconstrained type parameters in free functions are checked **abstractly**, even
when the function is unused. A declaration-bound `T` is not an integer, bool or
Copy type merely because current callers pass those types. Name/type checking,
match coverage, partial moves, branch joins and loop moves all run before any
concrete function body is materialized. Invalid unused calls, operators and
repeated moves are errors, not deferred until a favorable instantiation.
Unused function type parameters themselves are legal (unlike unused data-type
parameters). Existing valid generic functions previously rejected as a capability
have their exact original source retained in positive tests.

Calls accept explicit turbofish type arguments (`identity::<u64>(value)`) or the
argument-constrained inference described below.
Concrete signatures/placeholders are registered through the same shared registry
used by C++ functions and Rust data. Bodies discovered by other instances enter
the FIFO once; direct and mutual function recursion reuse in-progress signatures.
A concrete body is cloned from pristine parsed AST, rebound and checked with
its concrete types, rather than replaying source tokens. Primitive, tuple and
admitted record/enum values retain existing private aggregate/move rules.

Generic instances have deterministic private symbols derived from the function
name and complete semantic type structure. Public result metadata uses keys such
as `identity::<u64>`; there is no single foreign export for an abstract template.
Generic foreign/no-mangle ABI and reference type arguments are rejected. The
compiler still rejects type parameters unconstrained after all result/argument context; it does not prove borrow
lifetimes, implement Copy or other trait bounds, or erase destructor obligations. Admitted values still
exclude custom Drop and heap-owning types. Each compilation permits at most 128
concrete function instances; recursively growing specialization is an explicit
resource limit, not a claim that ordinary function recursion is invalid.

Pinned full `core_option.rs` still contains `impl<T> Option<T>` at line 608 and
`unwrap_or` at line 1031, whose body uses payload matching but whose complete
source additionally needs `const`, full method/trait resolution, `~const Destruct`,
attributes, imports and the earlier core dependencies. The authored free-function
`unwrap_or` fixture is a semantic test, **not** an extracted/replaced upstream
method or proof that original core/alloc now compiles. Closures and dispatch must
reuse shared scheduling while retaining their own language obligations.

References: [generic functions](https://doc.rust-lang.org/reference/items/functions.html#generic-functions),
[generic scope](https://doc.rust-lang.org/reference/items/generics.html),
[moves and Copy](https://doc.rust-lang.org/error_codes/E0382.html).


## Inherent value methods

Local nominal structs/enums admit inherent `impl` blocks, associated functions,
`self`/`mut self` methods, `Self` types/constructors/pattern paths, direct qualified
calls, and value receiver calls. Receiver expressions evaluate once before the
explicit arguments. The receiver is an ordinary Rust **move**, not Go's value
receiver copy: calling a consuming method prevents reuse on live paths and loop
backedges. `mut self` changes the owned callee binding, not the caller's object.

An unconstrained `impl<T,U> Pair<U,T>` may match its full nominal owner with
permuted parameters. Each impl parameter must appear exactly once in the owner;
concrete specializations, repeated/unconstrained parameters and overlap/coherence
solving remain explicit capability boundaries. All methods are type/move checked
at declaration time, including unused generic methods; abstract `T` is non-Copy.
Concrete receiver/qualified-owner types select complete impl arguments for the
existing shared function registry. No second method monomorphization cache or
runtime dispatcher is added. Method-local generic parameters have a separately qualified bounded
implementation below. Result-context inference
for ordinary generic calls is described below; truly ambiguous calls still fail.

The initial value-receiver domain is extended by the bounded `&self` loans
described above. `&mut self`, explicit receiver types, general receiver
adjustment, reference-returning methods, traits, Drop, vtables, closures,
const methods and C method ABI remain outside this slice. Method symbols encode
nominal owner plus method and concrete arguments privately; they do not claim
rustc ABI/layout. Static access with receiver-call syntax is diagnosed.

Pinned Option's `impl<T> Option<T>` and consuming payload match motivate this
boundary, but the complete original still requires its attributes/imports,
const/trait/destruction and remaining core obligations. Authored Choice and record
programs are distinct source fixtures, not edits/extractions of that upstream
file or a core/alloc success claim.

References: [inherent implementations](https://doc.rust-lang.org/reference/items/implementations.html#inherent-implementations),
[associated functions/methods](https://doc.rust-lang.org/reference/items/associated-items.html#associated-functions-and-methods),
[method calls](https://doc.rust-lang.org/reference/expressions/method-call-expr.html).

Historical internal loan analysis conservatively rejects reference-parameter
reassignment; it no longer controls public reference admission. Actual checked
input may rebind formal/self or locally inferred reference slots where rustc
accepts it. The original `shared_rebinding.rs` remains an unchanged actual-rustc
lifetime-error input; `shared_local_rebinding.rs` and explicitly lifetime-bound
formal rebinding programs are distinct positives, not edits to that negative.


### Trusted external-check reference mode (not an owned borrow checker)

`check_rust_source(source, toolchain=RustcToolchain(...), evidence_directory=...)`
is a supported **explicit external transition phase**. It runs the audited pinned
installation's direct `rustc`, verifies full `-vV` and actual sysroot (not a
rustup/PATH proxy), writes and checks exactly the supplied UTF-8 bytes with the
closed selected-edition/target/overflow options, and requires successful fresh metadata.
Failures, timeouts, missing metadata or source/tool drift cannot mint a token.
`compile_rust_checked(...)` is the convenience check-then-owned-lower entry.

Only that adapter can normally construct `CheckedRustSource`; its `compile_owned()`
phase or `compile_rust(source, rustc_check=checked_source)` invokes no external
process and consumes no MIR. A caller-constructed `ExternalRustcCheck` provenance
record is explicitly rejected as authorization. The token's private creation seal
is a trusted-Python API boundary, not a cryptographic or reflection-proof sandbox;
the audited tool configuration/local environment must also be trusted. The legacy
`experimental_rustc_checked=True` flag is only a value-only experimental opt-in
and admits **no** reference-bearing source, including previously admitted readonly forms. The transition checker is not a
production dependency of the final owned compiler.

On this certified path, PCC does not run its partial loan/move dataflow pass,
either for ordinary bodies or abstract/concrete generic bodies. Borrow/lifetime
and move invalidity belongs to the external check; own syntax, declaration/type,
mutability, numeric semantics and explicit lowering capability checks remain.
References lower as typed pointers without `noalias` assumptions. Mutable
references retain non-Copy type identity and writable-referent rules; ordinary
reference slot rebinding changes that slot, not its former referent. Named
function lifetime parameters and reference annotations are retained as syntax
metadata, checked externally, and have no runtime storage.

This first address mode admits existing-place local references, scalar/record
read/write, shared/mutable receivers and bounded function lifetime annotations.
Reference returns, nested/unit references, aggregate reference fields, data/impl
lifetime parameters, reference type substitutions into generic parameters,
borrow patterns, temporaries, Drop, traits, raw pointers and foreign mutable
reference ABI remain explicit representation/language capability gaps. No claim
of full Rust, full borrow checking, arbitrary cfg/dependencies, core or alloc.
Actual unchanged-original native rustc comparisons are required before this
source candidate can be called qualified; no new Mac execution is claimed.


### Argument-constrained generic call inference

Direct free-function calls can omit the full turbofish when every generic
parameter is constrained by actual argument types. Tuple, nominal application
and shared-reference parameter structure participates; repeated parameters
must agree. Integer defaulting waits until the entire function body supplies
its constraints (including return types, annotated locals and later calls),
then inferred and explicit calls use the same instance registry. Generic
bodies are still checked abstractly before any specialization; inference does
not make T Copy or defer invalid unused bodies.

Truly unconstrained parameters, generic constructor inference, partial
`_` type arguments, trait constraints, and method/pattern selection on unresolved
generic results remain explicit gaps. No Rust lifetime inference, core/alloc
qualification or production rustc dependency is introduced. The source-owned
fixtures are not upstream successes. The native rustc observer nodes
share one xdist group under the unchanged default six workers, so external
reference linking does not multiply peak RSS unnecessarily; each unchanged
program still compiles and executes independently with full raw observations.


### Result-context generic call inference

Generic calls also use function results, explicitly annotated locals, later
assignments and enclosing call parameters to resolve otherwise unknown type
arguments. Composite formal types use fresh call-local variables, including
nested calls and abstract outer generic contexts; declaration parameters never
leak between calls. Resolution still occurs at the existing whole-body finish
boundary and uses the same instance registry. Arguments evaluate once in source
order. Ambiguous calls and infinite/inconsistent inferred types remain errors.
Seven complete unchanged originals passed actual eight-byte PCC/rustc1.91.1
runtime comparisons. Divergent compile-only obligations are not executed; the
separate runtime example takes terminating branches. Mutable-reference inputs
still require actual same-source external checking before owned lowering.
This accepted result-context slice does not itself qualify method-local generics.
Constructor inference, trait solving, reference substitutions and unresolved
method/pattern dispatch remain outside its scope.

Specification: [inferred types](https://doc.rust-lang.org/reference/types/inferred.html),
[generic parameters](https://doc.rust-lang.org/reference/items/generics.html),
[call expressions](https://doc.rust-lang.org/reference/expressions/call-expr.html).

### Inherent method-local generic inference (qualified bounded slice)

An inherent `impl<T> Cell<T>` may declare its own `fn method<U>`. The impl
parameters form a fixed prefix determined by the known nominal receiver or
qualified owner, including impl-parameter permutations. Only the method suffix
is supplied by `value.method::<U>(...)` / `Cell::<T>::method::<U>(...)` or inferred
from argument structure and the existing result/later-context constraints.
The combined declaration-bound vector goes to the existing function registry;
no second instance cache, trait solver, or runtime dispatcher is introduced.
Unused abstract method bodies still check their non-Copy parameters. Receiver
and argument expressions are checked and evaluated once in source order.

Shared/mutable receiver programs still require a genuine same-input external
rustc check before owned lowering; neither a caller flag nor the internal
partial move model authorizes references. The eight runtime originals among
`tests/rust/programs/method_generic_*.rs` cover owner/method identity, nominal
results, terminating recursion, composite result constraints, operand exits,
`&self` and `&mut self`. Diverging compile-only obligations are not executed.
All eight runtime originals passed actual same-input rustc1.91.1/PCC raw-stream
comparisons in the restored environment. The whole Rust selection passed1,038 ordinary tests plus23 shared-mechanism
tests and9 separate integration nodes;15 Darwin target prerequisites remain unrun. The exact
initial u16 original is retained as a rustc-valid/owned-unsupported control;
a distinct u32 program supplies the runtime result-context case. This bounded
feature is implemented and accepted, not full generic Rust support.

Unknown nominal receivers, traits/bounds/where clauses, const/lifetime arguments,
reference type substitutions, stored function values, and arbitrary autoderef
remain explicit gaps. No core/alloc or full-Rust claim follows.

Specification: [associated functions and methods](https://doc.rust-lang.org/reference/items/associated-items.html),
[method calls](https://doc.rust-lang.org/reference/expressions/method-call-expr.html),
[generic path segments](https://doc.rust-lang.org/reference/paths.html#paths.expr.turbofish).

### Fixed-size arrays and target-sized index types

`usize` and `isize` are distinct source types, not aliases of `u64`/`i64`.
Their widths derive from the admitted target pointer data model. This domain
currently qualifies only the existing 64-bit Linux and Darwin object targets;
it does not extend the narrower scalar foreign ABI whitelist.

Fixed `[T; N]` values retain their element type and use public IR `ArrayType`,
including nested arrays and arrays inside ordinary records/tuples. Private
parameters/results copy or move the entire value according to the element's
Rust Copy eligibility; a named record does not become Copy from its fields,
and even `[NonCopy; 0]` remains non-Copy. No Rust/rustc or C by-value ABI claim
is made. Empty arrays require enough type context to infer their element type.

Literal operands evaluate individually left-to-right. A repeat operand is
evaluated exactly once, even at length zero; a length greater than one requires
an already admitted Copy element. Const-item/block repetition, user-defined
Copy, destructuring and Drop are not emulated. A scalar Copy field can be read
through an indexed record, but moving a non-Copy indexed element/field is
rejected. Whole-array reassignment may reinitialize a moved owner.

Lengths and indices currently require bounded closed scalar constant
expressions with type `usize`; invalid constant indices fail before IR.
Dynamic/effectful index expressions explicitly require unimplemented
bounds/panic support, never an unchecked pointer access. Array `.len()` yields
`usize` while preserving evaluation of its receiver. Each dimension and the
concrete aggregate work budget are bounded to 4096 elements/scalar slots.
Zero-length and unit-element arrays keep their real typed public-IR layout;
known lengths do not erase operand side effects or create destruction support.

Reference reads/stores use the existing actual external-check transition for
borrowed programs, then owned typed-address lowering. This does not introduce
another partial borrow-analysis admission rule. Creating an indexed borrow or
auto-borrowing an indexed method receiver requires that actual external check;
legacy field/index path inequality is not an array-disjointness proof.
Slices/unsizing, const generics,
array matching/equality and general iterator traits remain independent gaps.
The concrete owned-array `for` source slice below does not implement those traits.
Complete original `core::array` is pinned as dependency evidence, not a core or
alloc build success. Authored native/reference fixtures are separate originals.

Specification: [array types](https://doc.rust-lang.org/reference/types/array.html),
[array expressions and repeat evaluation](https://doc.rust-lang.org/reference/expressions/array-expr.html),
[operand evaluation](https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands),
[target-sized integers](https://doc.rust-lang.org/reference/types/numeric.html#machine-dependent-integer-types).


### Owned fixed-array `for` (bounded qualification)

The first iterator-loop domain accepts concrete owned `[T; N]` values only
when the full element graph has proven-empty destruction in the currently
admitted type system. This is not the same as Copy: ordinary non-Copy records
are consumed, while Copy arrays snapshot their values before the first body.
Unknown generic elements, Drop implementations, arbitrary iterator traits,
ranges, borrowed-array iterators and destructuring patterns remain explicit
capability gaps. Generic functions with fully concrete iteration elements may
still use the existing shared instantiation mechanism.

Every new `for` requires a source/options-bound token minted by the actual
external `check_rust_source` transition. The caller-attestation flag is not
sufficient, and the legacy partial move/loan checker is not extended. PCC still
performs its own type/mutability and panic-capability checks: successful rustc
metadata checking does not establish runtime panic absence. No MIR or external
compiler is consumed by the owned lowering phase.

A dedicated standard-array iteration identity cannot be captured by source
names `IntoIterator`, `Iterator`, `Option`, `into_iter` or `next`. The iterable
is evaluated once and its owned storage outlives every iteration. A hidden
bounded cursor advances before the body; per-iteration bindings have separate
semantic identities from outer names. Loop targets retain scope-exit depths:
continue retains the iterator, break retires its remaining range, and return
evaluates its operand before retiring enclosing iterator scopes. Empty cleanup
is eliminated only under the concrete no-Drop proof, never inferred from a
borrow check. Zero-length arrays still evaluate their source/operand effects.
The internal proven-bounded cursor does not admit general dynamic indexing.

`tests/rust/test_rust_array_for.py` ties the source models and fifteen new
original programs to the specification. Its actual public adapter and full
8-byte owned/reference execution lane passed on Linux x86-64 in finalba525; this
is not a Mac execution or cross-target certification claim.

Specification: [iterator-loop identity and desugaring](https://doc.rust-lang.org/reference/expressions/loop-expr.html#iterator-loops),
[drop scopes](https://doc.rust-lang.org/reference/destructors.html#drop-scopes),
[moved/copied values](https://doc.rust-lang.org/reference/expressions.html#moved-and-copied-types).
Pinned dependency audit: Rust commit
`1159e78c4747b02ef996e55082b704c09b970588`,
[`array/iter.rs`](https://github.com/rust-lang/rust/blob/1159e78c4747b02ef996e55082b704c09b970588/library/core/src/array/iter.rs)
and [`array/iter/iter_inner.rs`](https://github.com/rust-lang/rust/blob/1159e78c4747b02ef996e55082b704c09b970588/library/core/src/array/iter/iter_inner.rs).
Their live-range/destruction design was inspected, not compiled; traits,
MaybeUninit, const generics and destructor glue still block original core.

### Public reference-admission regression boundary

`test_rust_reference_admission.py` covers bare-flag refusal before IR, unused and
nested reference declarations, post-semantics synthesis, genuine token context
mismatch, actual checked readonly sources and actual E0382 rejection. Existing
reference program bytes and runtime oracles are retained. `analysis_only.py` is
a test-internal facts-only model, not an unchecked compile API. The shared C++
record-pointer fixture likewise checks the original Rust source before entering
its external-tools-forbidden owned compilation phase; scalar interop does not
acquire a new rustc dependency. The repaired boundary passed63 focused and987 whole-language/shared-core tests
(11 additional Darwin-target cases explicitly unrun). The earlier959-pass run
used the older boundary and remains historical evidence, not qualification of
this repair. The common ABI domain separately passed3 available nodes; its
Darwin record-reference node remains explicitly unrun.

## Implementation and acceptance map (legacy schema1 qualification only)

The public schema1 `status` is a qualification view, not a machine-readable
dual-axis implementation migration. `missing` does not prove absent code.
The tables below preserve implementation and acceptance separately for every
feature ID;119 unaudited chapter gaps are not a completion denominator.
Implemented-but-unverified scopes are not promoted to supported by suite totals.
No common checker/schema2 migration is included.

Fresh method-generic candidate `4bf47dc3fd6d79350d5b8fb85b02446af24ce73b`
passed1,061 whole ordinary tests and9 complete integration nodes (1,070 total;
1,047 Rust plus23 shared mechanism tests). All six workers observed1,085 pooled
identities;15 Darwin core/std prerequisites remain explicitly unselected/unrun.
The81 unchanged-original full-eight-byte native/rustc1.91.1 pairs include56
metadata-matching reference-source identities and25 value-only pairs. Actual
checker processes total143 successes and13 genuine rejections; repeated checks
are not presented as unique programs. The initial90/2/116 focused outcome is
preserved, with its exact valid-but-unsupported u16 original retained separately.

No new common ABI gate was run: shared registry/ABI/IR/backend code and common
fixtures are unchanged. Older common and feature results remain historical,
not new environment qualification. No Mac execution or full Rust claim is made.

Thirty fresh records renew the13-record impl cohort and add17 method-generic
records;143 other historical active records remain unchanged. The retired13
remain exact in prior Git/private history, not relabeled. The reset removed old
private artifacts, so no historical raw-file revalidation is claimed. The
unchanged private reader checks an explicitly scoped30-row/two-feature
projection with14 current raw identities. The public strict file checker still
verifies all four complete maps; diagnostic audit is Rust-only. No fallback or
ignored current-file mismatch substitutes for those checks.

### Audited feature axes

| Feature ID | Implementation | Acceptance | Scope |
|---|---|---|---|
| `rust.impl.value-methods` | implemented | accepted | Checked nongeneric/generic local record and enum impls; self/mut self, Self names, qualified calls, explicit full impl type arguments, declaration-checked non-Copy generic bodies, source-order one-time receiver evaluation and recursion via shared instance registry. Excludes reference receivers, autoderef/autoborrow, trait impls, coherence specialization, method-local generic parameters, arbitrary self types and reference/Drop runtime. Qualified only in host pcc0/self Linux x86-64, with actual unchanged-source native rustc1.91.1 differential; Darwin remains cross-emission only. |
| `rust.references.direct-shared-call-loans` | implemented | accepted | Readonly references and &self receivers to admitted places use actual same-input source/options-bound external checking before owned typed-pointer lowering. All original eight shared-reference programs retain their bytes and full-eight-byte native/reference outputs. The flag alone never authorizes any public reference-bearing input; the checked route bypasses incomplete owned loan/move admission while retaining nonborrow type, addressability and mutability checks. The old partial loan algorithm is only an explicitly nonlowering internal mechanism, not a public admission gate or borrow certification. Reference returns, aggregate reference storage, nested references, temporary lifetime extension, arbitrary traits and Drop remain outside scope. Linux x86-64 runtime qualification only; missing Darwin target core/std leaves reference cases explicitly unrun. |
| `rust.lex.tokens` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. ASCII identifiers, integer/bool tokens, nested ordinary comments, Pattern_White_Space and retained explicit parentheses; doc attributes and unsupported token families fail explicitly. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.functions.scalar-items` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Private typed function signatures, direct calls and forward references in a single parsed crate; no function values, modules or complete item grammar. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.blocks.locals` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Block tails, let/mut bindings and lexical shadowing for admitted types; no arbitrary Rust statement/item grammar. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.control.if-while` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Boolean if/while conditions, break/continue and return for admitted expressions; not for/IntoIterator, labeled loops or full loop-value semantics. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.numeric.fixed-integers` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Current i32/u32/i64/u64 and bool arithmetic/comparisons, explicit casts and short-circuit evaluation with overflow_checks=False; panic-capable paths remain gated. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.inference.integer-context` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Unresolved integer literals share constraints through supported body expressions before i32 defaulting; generic-call argument inference is inventoried separately. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.aggregate.tuple-record` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Named records, tuple values, field access, construction order and private aggregate value ABI; default layout is private PCC layout, not a Rust ABI compatibility claim. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.moves.value-paths` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Place-sensitive non-Copy move facts and live-branch/loop joins for destructor-free value types; no Drop glue, user Copy trait, complete borrow checker or partial initialization of uninitialized locals. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.enums.unit-discriminants` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Unit variants with checked default/explicit integer discriminants; no foreign enum ABI, trait-derived comparisons or empty-enum lowering. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.patterns.finite-match` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Single-evaluation match, admitted integer literals, wildcard and finite constructor matrices; no guards, borrow patterns, ranges or full match ergonomics. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.enums.payload-values` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Private typed payload representation with selected-variant reads, value bindings and precise field consumption; no C/rustc layout or Drop obligations erased. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.generics.data-explicit` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Explicit full type applications, nominal instance identity and finite layout validation even for unused templates; no trait bounds, const generics or inferred constructors. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.generics.functions-explicit` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Complete explicit turbofish applications with abstract non-Copy body checking and recursive placeholders; argument-constrained inference is inventoried separately. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.ffi.scalar-reference-c` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Unsafe extern-C calls and no_mangle exports for admitted scalar signatures; readonly references only to supported nongeneric repr(C) integer records. No C-unwind, by-value aggregates or complete FFI safety guarantee. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.codegen.qualified-targets` | implemented | unverified | Qualification missing, not implementation missing: this mechanism has source-bound implementation but no complete accepted evidence set for this exact ledger feature. README preserves the separate axes and source proofs. Public target_triple drives IR triple/data layout and owned ELF Linux x86-64 or Mach-O arm64 Darwin emission; Darwin cross-emission is not Mac execution. Code is present, but no feature-specific complete runtime raw-reference qualification triplet is migrated for this row; historical tests do not automatically imply schema2 acceptance. |
| `rust.generics.argument-call-inference` | implemented | accepted | Argument-constrained generic function calls infer complete type arguments from scalar, tuple, nominal aggregate and admitted shared-reference operands; body/result constraints precede integer fallback. Arguments are checked/evaluated once; generic declarations remain abstractly checked before concrete calls, and the existing shared registry owns recursive instances. Result-context inference is tracked separately by rust.generics.result-context-call-inference. Inferred constructors, partial underscore arguments, unresolved method/pattern receiver constraints, trait solving and reference type substitutions remain explicit boundaries. Qualified pcc0/self Linux x86-64 with seven unchanged authored-program native rustc1.91.1 comparisons; no upstream core/alloc claim and Darwin cross-emission only. |
| `rust.generics.result-context-call-inference` | implemented | accepted | Per-call inference variables combine argument structure with function results, explicit local annotations, later assignments and outer call-parameter contexts before whole-body resolution and the existing shared instance registry. Composite unknown arguments substitute call-local variables rather than leaking declaration parameters. Generic bodies remain abstractly checked; ambiguity, inconsistent constraints and occurs cycles still fail. Seven unchanged original programs have actual full-eight-byte owned/rustc1.91.1 native comparisons on Linux x86-64, including side effects, terminating recursive/composite paths and actual-certified mutable references. Infinite compile-only obligations are not executed. Method-local generics, trait solving, inferred constructors, reference substitutions and unresolved method/pattern receiver selection remain separate gaps; public references still require actual same-input checking. |
| `rust.references.external-certified-addresses` | implemented | accepted | Actual external-check transition owns borrow/lifetime/move admission for the same frozen UTF-8 input, edition2021, target and closed no-cfg/no-dependency options. Only an adapter-minted source-bound token enables owned pointer lowering for local shared/mutable references, implicit or explicit shared/mutable receivers, inferred generic calls, reborrows, formal/local reference rebinding, primitive and field assignment ordering, and disjoint mutable fields. The owned compiler still checks nonborrow typing and mutability and never invokes rustc or consumes MIR. The Python caller and audited direct local tool installation are trusted; this is not cryptographic attestation, an owned borrow checker, or admission of every Rust form. Reference returns, aggregate reference storage, borrow patterns, nested references, temporary lifetime extension, Drop and traits remain outside scope. Actual Linux x86-64 qualification uses16 unchanged authored originals and independent full8-byte native PCC/rustc comparisons; Darwin reference certification/native execution is not qualified. |
| `rust.types.target-sized-integers` | implemented | accepted | Distinct usize/isize identities derive64-bit width from the admitted pointer layout, never aliases of u64/i64. Signed extension, zero extension, truncation, wrapping/bitnot/comparison, usize patterns and generic instance identity have two unchanged native raw-reference originals. Foreign ABI whitelist stays narrower; Darwin objects are cross-only. No32-bit target, Mac execution or core/alloc claim. |
| `rust.arrays.fixed-values` | implemented | accepted | Real public IR ArrayType retains element type, Copy eligibility, non-Copy whole moves/reinitialization and private parameter/result value copies. Closed usize constant lengths and indices are statically checked; dynamic/effectful bounds explicitly reject instead of unchecked access. Literal operands run left-to-right; repeat operands run once evenN0, and len receiver effects remain. Indexed borrowing requires actual external checking; structural Index inequality is not alias proof. Twelve unchanged authored originals have full8-byte native-reference pairs. No array C ABI, slices/unsizing, const generics, destructuring/match, iterator traits, dynamic panic, Drop, complete core/alloc or Mac execution claim. |
| `rust.loops.owned-array-for` | implemented | accepted | Concrete owned fixed arrays only, with recursive no-Drop proof independent of Copy. Standard iterator identity cannot be captured by source names; source evaluates once, Copy snapshots/non-Copy moves, cursor advances before body, and per-iteration binding/temporary scopes are retired across break/continue/return. Actual source/options-bound external checking is mandatory; PCC does not extend its legacy partial borrow checker. Fifteen unchanged authored originals pass actual rustc1.91.1 and full8-byte owned/reference process comparisons in the final complete987-selected gate (11 additional Darwin-target cases explicitly unrun). Unknown generic element destruction, arbitrary traits/ranges/borrowed iterators/destructuring, Drop, dynamic panic and original core/alloc remain outside scope; existing division/bounds guards stay enforced. Linux x86-64 runtime only, no Mac certification claim. |
| `rust.syntax.blocklike-expression-boundary` | implemented | accepted | Admitted blocklike expression statements and match arms stop ambiguous unparenthesized call/index/infix continuation; dot access and grouped/value contexts stay ordinary expressions. Else-if is parsed as the grammar prefix, so indexing/dot apply to the complete chain. Original failing for-array source is preserved; the distinct native statement-values original observes both branch results and length effects. This is not a claim of all Rust expression or pattern syntax. |
| `rust.references.public-checked-admission` | implemented | accepted | Every reference-bearing public input, including unused/nested/generic declarations and shared/mutable receivers, requires a genuine adapter-minted CheckedRustSource. Structural guards run before semantics and again before lowering; ordinary provenance records, bare flags and token source/target/options mismatches cannot authorize IR. The separate transition actually checks the same frozen bytes with audited rustc1.91.1; owned lowering consumes no MIR and invokes no compiler. Checked input bypasses the incomplete internal MoveChecker, retaining nonborrow language checks. Nine unchanged previously caller-attested originals now execute only after actual checking; real E0382 is rejected by rustc before lowering. No cryptographic attestation or owned borrow-checker claim; supported syntax remains bounded. |
| `rust.capability.ad1be8716230` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/__init__.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.638580601dd4` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/arrays.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.2d9d7a47b49d` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/external_check.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.102e85184e90` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/function_generics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.53efaab821f9` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/function_generics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.05e266c798ff` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/function_generics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.791904d28fad` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/generics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.a1a67307ea48` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/generics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.8b991334a1bf` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/generics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.c7ec835f4a43` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/generics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.b108adcc58fb` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/generics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.635b37d0353a` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/generics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.76ffe4b8b11f` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/iteration.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.6cffe8ef2ad3` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/iteration.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.bcfda66ea41a` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/iteration.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.ca7ab0b6c7b5` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/lowering.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.b29def7fd8da` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/lowering.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.1f486aaf9ee4` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/methods.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.cafda895c2ac` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/methods.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.9feb1bfbec86` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/methods.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.d465b452d5cd` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/methods.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.8a0b20e95290` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/patterns.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.e80fe5661a8b` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/patterns.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.85bfd5395b7c` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/patterns.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.eafc49a3531e` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/patterns.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.f166c52a3112` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/patterns.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.1377c97a7cf9` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/patterns.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.3e384f9d3e80` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.42c0edfbabfa` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.b6ad2f608ff0` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.47e9fc5d6566` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.d0970a5199e9` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.4b9c1fc377df` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.de072a10a17d` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.276015de773c` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.8f374c0f0032` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.c48c5f43d7bb` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.1a8776e6058b` | explicitly_unimplemented | unverified | Internal nonlowering legacy analysis only: Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. This restriction cannot authorize public reference execution and is bypassed by the actual-checked public route. |
| `rust.capability.ad0a8e2940d2` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.d0b554a3b673` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.10557ba208c5` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.a158c8e39022` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.c363280a66c7` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.a8fa7c5d5360` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.2297a7178b8b` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.1fe01c85830b` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.6352bd97ff85` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.1337d5134703` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.4ee292f5718c` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.bdcf31e67972` | explicitly_unimplemented | unverified | Internal nonlowering legacy analysis only: Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. This restriction cannot authorize public reference execution and is bypassed by the actual-checked public route. |
| `rust.capability.7d0c67a57d36` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.709957839ec7` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.cd0812091f1d` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/semantics.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.fe93fd5bf188` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.bd733be37d9b` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.e99bc3baff05` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.d1d178589dd9` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.bba567d753cb` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.7acd3663c2c4` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.e05bbaab24e2` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.80a1daa28726` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.c3e20b9f0176` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.33589f37c948` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.2f56a4aba18c` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.bb5c218be67c` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.23e389b95dff` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.ef2051156beb` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.generics.inherent-method-inference` | implemented | accepted | Qualified bounded inherent method-local type parameters: a declaration-bound impl prefix is fixed from the known nominal receiver/qualified owner; a distinct method suffix uses complete turbofish or argument/result/later-context inference. One shared function instance registry, abstract non-Copy body checks, one-time receiver evaluation, and canonical Self/owner identities survive shadowing. Eight unchanged-original Linux x86-64 PCC/rustc1.91.1 raw runtime comparisons passed, including local shared/mutable receiver aliases with genuine same-input external checks. A separate unchanged u16 original is rustc-valid but explicitly unsupported by owned integer lowering; it is not an invalid Rust case. Traits, bounds, where clauses, const/lifetime arguments, inferred nominal owners, arbitrary autoderef, Drop and reference type substitutions remain gaps. Three method-reference Darwin target cases remain explicitly unrun because target core/std are absent. |
| `rust.capability.75978ad8986b` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.a0caa359be53` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.a8ca231e8795` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.5b94bfcc10f1` | explicitly_unimplemented | unverified | Remaining generic/blanket trait implementations and impl constraints are explicitly unimplemented. Concrete local static trait implementations are separately qualified under rust.traits.static-local-impls; this row no longer means all trait impls are absent. |
| `rust.capability.f49193c1c2ca` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.cb3e74dcd215` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.cc0819c68f78` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.ef1aabaa4f26` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.76572c67a8a9` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.9d2f182c387d` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.9a13e1881e59` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.9c7be91de66a` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.066e96807a59` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.e6d5b211f058` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.551845180510` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.d70994f47d79` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.ed147b2399ee` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.18728c522946` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.1ecc344c3e40` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |
| `rust.capability.e43cffed9774` | explicitly_unimplemented | unverified | Exact message-only fail-closed boundary in pcc/frontends/rust/syntax.py. Not a stable diagnostic code; broad fallback diagnostics do not establish a complete invalid-vs-unimplemented taxonomy. |

### Current immutable implementation observations

| Feature ID | Source / symbol | Revision | SHA-256 | Mechanism |
|---|---|---|---|---|
| `rust.impl.value-methods` | `pcc/frontends/rust/methods.py` / `InherentMethods.prepare` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `5d15493a93285ab8e564c0717d807bae1d2cfbeaa62721bee05982d1abc200fc` | Checks local nominal impl declarations and resolves Self; explicit method selection preserves language-specific identities. |
| `rust.impl.value-methods` | `pcc/frontends/rust/function_generics.py` / `GenericFunctions.select` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `4e98d19ebf43d7b25677b3299c6d4960d70ccc5ab12e2b4f8db1352c2414d6de` | Selected concrete methods use the shared frontend instance registry; abstract declarations remain independently checked. |
| `rust.references.direct-shared-call-loans` | `pcc/frontends/rust/external_check.py` / `check_rust_source` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1c2348712e7bcc46f956118026e08bfe974661c8d0bc6c124f5d0f0547ec2ec5` | Actual external checking owns readonly admission; the old partial conflict model no longer authorizes public references. |
| `rust.references.direct-shared-call-loans` | `pcc/frontends/rust/lowering.py` / `Lowerer.place_pointer` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1ccdafe7b164465b71714825b235132e790e89da793c10b8b86618b3aecab0e1` | Admitted shared references lower to existing-place pointers and typed field addressing. |
| `rust.lex.tokens` | `pcc/frontends/rust/syntax.py` / `lex` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `700d425907dde32d3289fbdfd6d072f65856e311646984dd847869839ce34163` | Scans Rust tokens and nested comments with source positions, rather than stripping into another language. |
| `rust.functions.scalar-items` | `pcc/frontends/rust/semantics.py` / `Checker.check` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `5a8b1d53c1e24998b875e02d5d90fa974ec265ef2cd92409b368a4dc79098cbc` | Checks declarations and bodies before lowering typed direct calls. |
| `rust.blocks.locals` | `pcc/frontends/rust/semantics.py` / `Checker.bind` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `5a8b1d53c1e24998b875e02d5d90fa974ec265ef2cd92409b368a4dc79098cbc` | Serial-identified bindings preserve lexical scope and distinct storage. |
| `rust.control.if-while` | `pcc/frontends/rust/lowering.py` / `Lowerer.expression` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1ccdafe7b164465b71714825b235132e790e89da793c10b8b86618b3aecab0e1` | Builds public IR control-flow blocks and explicit terminating edges. |
| `rust.numeric.fixed-integers` | `pcc/frontends/rust/semantics.py` / `Checker.validate` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `5a8b1d53c1e24998b875e02d5d90fa974ec265ef2cd92409b368a4dc79098cbc` | Validates typed numeric operands and propagates supported constant expressions before IR emission. |
| `rust.inference.integer-context` | `pcc/frontends/rust/semantics.py` / `unify` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `5a8b1d53c1e24998b875e02d5d90fa974ec265ef2cd92409b368a4dc79098cbc` | Unifies inferred integer roots with contextual scalar types and checks literal bounds. |
| `rust.aggregate.tuple-record` | `pcc/frontends/rust/lowering.py` / `Lowerer.type` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1ccdafe7b164465b71714825b235132e790e89da793c10b8b86618b3aecab0e1` | Maps checked nominal/tuple types to typed public IR aggregates and target-owned layout. |
| `rust.moves.value-paths` | `pcc/frontends/rust/moves.py` / `MoveChecker.expression` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `93c6f15d2b7a7cf0d0215a82be87841f8dfdc601eb37564b8af9a45b3d2b499c` | Legacy value-only/internal path-state mechanism; actual-checked reference admission bypasses this incomplete analysis. |
| `rust.enums.unit-discriminants` | `pcc/frontends/rust/patterns.py` / `check_enums` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `94877ffd9367c5b52c26397ddd59373c200e950448a393b9f57ce9d041dea426` | Checks enum representation/discriminant range and variant identities. |
| `rust.patterns.finite-match` | `pcc/frontends/rust/patterns.py` / `validate_match` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `94877ffd9367c5b52c26397ddd59373c200e950448a393b9f57ce9d041dea426` | Checks constructor coverage and rejects non-exhaustive admitted pattern matrices. |
| `rust.enums.payload-values` | `pcc/frontends/rust/lowering.py` / `Lowerer.payload_value` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1ccdafe7b164465b71714825b235132e790e89da793c10b8b86618b3aecab0e1` | Projects payload slots only after variant dispatch in the admitted match lowering. |
| `rust.generics.data-explicit` | `pcc/frontends/rust/generics.py` / `GenericDeclarations.prepare` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `a9d5a0ffa667e4dc31e73048461264c64f6eac3a3da27709d4aa693597c62320` | Normalizes declaration-bound parameters and validates generic declarations before shared instance materialization. |
| `rust.generics.functions-explicit` | `pcc/frontends/rust/function_generics.py` / `GenericFunctions.select` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `4e98d19ebf43d7b25677b3299c6d4960d70ccc5ab12e2b4f8db1352c2414d6de` | Registers concrete function instances using the shared worklist while abstract declarations remain checked independently. |
| `rust.ffi.scalar-reference-c` | `pcc/frontends/rust/semantics.py` / `Checker.valid_type` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `5a8b1d53c1e24998b875e02d5d90fa974ec265ef2cd92409b368a4dc79098cbc` | Checks admitted ABI types and separately rejects unsupported reference/aggregate foreign shapes. |
| `rust.codegen.qualified-targets` | `pcc/frontends/rust/targets.py` / `resolve_target` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `b9e60b36cfedc5279973686d7b00d2b92042dabf75a7f3b434de85b0fb08733e` | Resolves only qualified target/ABI combinations and fails closed on unsupported aliases. |
| `rust.generics.argument-call-inference` | `pcc/frontends/rust/call_inference.py` / `infer` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `02a942413124dfb6b04892f92987a62dfaffdbee82c94d25f2a313bc8669b358` | Collects structural argument constraints while keeping unresolved integer roots shared. |
| `rust.generics.argument-call-inference` | `pcc/frontends/rust/call_inference.py` / `finish` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `02a942413124dfb6b04892f92987a62dfaffdbee82c94d25f2a313bc8669b358` | Selects existing shared-worklist function instances only after whole-body constraints. |
| `rust.generics.argument-call-inference` | `pcc/frontends/rust/semantics.py` / `Checker.check_body` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `5a8b1d53c1e24998b875e02d5d90fa974ec265ef2cd92409b368a4dc79098cbc` | Finishes inferred calls after declaration-result unification and before typed validation. |
| `rust.references.external-certified-addresses` | `pcc/frontends/rust/external_check.py` / `check_rust_source` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1c2348712e7bcc46f956118026e08bfe974661c8d0bc6c124f5d0f0547ec2ec5` | Actually invokes the pinned direct compiler on frozen source and validates version/sysroot/process/output before privately minting a token. |
| `rust.references.external-certified-addresses` | `pcc/frontends/rust/external_check.py` / `CheckedRustSource.validate` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1c2348712e7bcc46f956118026e08bfe974661c8d0bc6c124f5d0f0547ec2ec5` | Binds owned admission to exact source and closed options; ordinary caller-constructed records are not authorization. |
| `rust.references.external-certified-addresses` | `pcc/frontends/rust/__init__.py` / `compile_rust` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `7a2fbf34fcbbcda1a93b281a6462ce597c68dfec570ff44c6a47d1fa32010ef5` | Keeps the owned IR path process-free and bypasses incomplete owned move admission only for the adapter-minted token. |
| `rust.references.external-certified-addresses` | `pcc/frontends/rust/semantics.py` / `Checker.mutable_place` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `5a8b1d53c1e24998b875e02d5d90fa974ec265ef2cd92409b368a4dc79098cbc` | Retains language mutability and addressability checks independent of externally owned borrow checking. |
| `rust.references.external-certified-addresses` | `pcc/frontends/rust/lowering.py` / `Lowerer.place_pointer` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1ccdafe7b164465b71714825b235132e790e89da793c10b8b86618b3aecab0e1` | Lowers actual reference/dereference and field addresses through existing typed IR pointers. |
| `rust.types.target-sized-integers` | `pcc/frontends/rust/types.py` / `integer_bits` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `f0d55554bbc67e706559217bc351cd75162d76ba3a34336bfb162078ab5b6f38` | Requires a qualified pointer width while preserving distinct source names. |
| `rust.types.target-sized-integers` | `pcc/frontends/rust/targets.py` / `RustTarget.pointer_bits` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `b9e60b36cfedc5279973686d7b00d2b92042dabf75a7f3b434de85b0fb08733e` | Reads the explicit pointer data model. |
| `rust.types.target-sized-integers` | `pcc/frontends/rust/lowering.py` / `Lowerer.type` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1ccdafe7b164465b71714825b235132e790e89da793c10b8b86618b3aecab0e1` | Uses the selected target to lower pointer-sized integers. |
| `rust.arrays.fixed-values` | `pcc/frontends/rust/types.py` / `FixedArrayType` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `f0d55554bbc67e706559217bc351cd75162d76ba3a34336bfb162078ab5b6f38` | Keeps nominal element/count shape separate from tuple representation. |
| `rust.arrays.fixed-values` | `pcc/frontends/rust/arrays.py` / `length_value` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `f510bb95035e67f216259ed83bf7086e13e11c0450ad00b5d37e78dac643ef29` | Checks bounded closed usize constants before storage or indexing. |
| `rust.arrays.fixed-values` | `pcc/frontends/rust/semantics.py` / `Checker.copy_type` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `5a8b1d53c1e24998b875e02d5d90fa974ec265ef2cd92409b368a4dc79098cbc` | Derives array Copy from its element, including zero-length arrays. |
| `rust.arrays.fixed-values` | `pcc/frontends/rust/lowering.py` / `Lowerer.type` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1ccdafe7b164465b71714825b235132e790e89da793c10b8b86618b3aecab0e1` | Selects actual public IR ArrayType and target-owned layout. |
| `rust.arrays.fixed-values` | `pcc/frontends/rust/lowering.py` / `Lowerer.expression` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1ccdafe7b164465b71714825b235132e790e89da793c10b8b86618b3aecab0e1` | Preserves array operand, receiver and assignment evaluation while using owned aggregate values. |
| `rust.loops.owned-array-for` | `pcc/frontends/rust/syntax.py` / `Parser.expression` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `700d425907dde32d3289fbdfd6d072f65856e311646984dd847869839ce34163` | Parses owned-array for and explicit bounded binding forms. |
| `rust.loops.owned-array-for` | `pcc/frontends/rust/semantics.py` / `Checker.expression` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `5a8b1d53c1e24998b875e02d5d90fa974ec265ef2cd92409b368a4dc79098cbc` | Requires actual external check and owned-array scope/type semantics. |
| `rust.loops.owned-array-for` | `pcc/frontends/rust/iteration.py` / `prove_no_drop` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `04d3583c2e8d55ce1b76949b7ad0b76823607989259022ef3b529839df7d2ea5` | Recursively proves empty destruction independently of Copy. |
| `rust.loops.owned-array-for` | `pcc/frontends/rust/iteration.py` / `validate_iteration` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `04d3583c2e8d55ce1b76949b7ad0b76823607989259022ef3b529839df7d2ea5` | Assigns standard array identity independent of source spelling. |
| `rust.loops.owned-array-for` | `pcc/frontends/rust/lowering.py` / `Lowerer.array_iteration` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1ccdafe7b164465b71714825b235132e790e89da793c10b8b86618b3aecab0e1` | Snapshots source and advances bounded cursor before each body. |
| `rust.loops.owned-array-for` | `pcc/frontends/rust/lowering.py` / `Lowerer.finish_iteration_scopes` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `1ccdafe7b164465b71714825b235132e790e89da793c10b8b86618b3aecab0e1` | Retires only crossed iterator/binding scopes with proof-empty destruction. |
| `rust.syntax.blocklike-expression-boundary` | `pcc/frontends/rust/syntax.py` / `Parser.expression` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `700d425907dde32d3289fbdfd6d072f65856e311646984dd847869839ce34163` | Carries statement/match-arm completion context while retaining grouped/value and dot continuations. |
| `rust.syntax.blocklike-expression-boundary` | `pcc/frontends/rust/syntax.py` / `Parser.if_expression` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `700d425907dde32d3289fbdfd6d072f65856e311646984dd847869839ce34163` | Parses else-if prefixes before attaching postfix to the whole chain. |
| `rust.references.public-checked-admission` | `pcc/frontends/rust/reference_admission.py` / `contains_references` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `65c5796a5986a6fd47d31686909e8ede5c0340fb340ed5429749a4d8113df0ff` | Structurally detects references in all syntax/type containers without token-text guessing. |
| `rust.references.public-checked-admission` | `pcc/frontends/rust/__init__.py` / `compile_rust` | `ba5252cb01aaae9643a11ff70035e3a850238783` | `7a2fbf34fcbbcda1a93b281a6462ce597c68dfec570ff44c6a47d1fa32010ef5` | Checks genuine tokens and rejects reference-bearing inputs before semantics and again before lowering. |
| `rust.generics.result-context-call-inference` | `pcc/frontends/rust/call_inference.py` / `infer` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `02a942413124dfb6b04892f92987a62dfaffdbee82c94d25f2a313bc8669b358` | Creates call-local type variables and substitutes composite argument constraints without leaking declaration-bound parameters. |
| `rust.generics.result-context-call-inference` | `pcc/frontends/rust/call_inference.py` / `finish` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `02a942413124dfb6b04892f92987a62dfaffdbee82c94d25f2a313bc8669b358` | Resolves combined argument/result constraints at the existing body finish boundary and selects the shared instance registry. |
| `rust.generics.result-context-call-inference` | `pcc/frontends/rust/semantics.py` / `InferredType` | `f4327b8142a71dfc9c173387b34f8eb65b85a209` | `5a8b1d53c1e24998b875e02d5d90fa974ec265ef2cd92409b368a4dc79098cbc` | Carries call-local unresolved result types with occurs and ambiguity diagnostics until enough context is available. |


### Full-chapter coverage gaps (not feature failure counts)

| Feature ID | Implementation | Acceptance | Inventory scope |
|---|---|---|---|
| `rust.chapter.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.2.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.2.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.2.3.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.2.4.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.2.5.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.2.6.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.2.7.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.3.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.3.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.3.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.4.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.5.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.3.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.4.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.5.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.6.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.7.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.8.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.9.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.10.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.11.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.12.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.13.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.14.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.6.15.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.7.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.7.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.7.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.7.3.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.7.4.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.7.5.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.7.6.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.7.7.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.3.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.4.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.5.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.6.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.7.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.8.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.9.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.10.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.11.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.12.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.13.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.14.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.15.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.16.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.17.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.18.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.8.2.19.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.9.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.3.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.4.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.5.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.6.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.7.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.8.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.9.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.10.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.11.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.12.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.13.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.14.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.15.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.16.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.17.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.18.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.1.19.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.3.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.4.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.5.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.6.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.7.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.8.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.9.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.10.10.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.11.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.12.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.12.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.12.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.12.3.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.12.4.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.12.5.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.12.6.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.13.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.13.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.13.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.14.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.15.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.16.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.17.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.17.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.17.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.17.3.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.18.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.19.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.20.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.21.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.21.1.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.21.2.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.21.3.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.21.4.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.21.5.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.chapter.21.6.coverage` | unaudited chapter inventory | unverified | Full chapter feature decomposition and unit/native/reference evidence are not yet complete. This missing coverage row does not negate separately qualified narrow features or claim that every construct in this chapter is rejected. |
| `rust.capability.85368199e941` | explicitly_unimplemented | unverified | Explicit message-only capability boundary; enum-owner arguments remain available. No stable diagnostic code or complete taxonomy is claimed. |

## Qualified bounded static local traits

`rust.traits.static-local-impls` has host pcc0/self Linux x86-64 execution
acceptance for the bounded forms below, not a complete trait solver. Local non-generic traits may declare safe required methods;
concrete local record/enum impls must supply exactly those members with matching
receivers, parameters, results and alpha-equivalent method-local type parameters.
Concrete impl identities include the trait and canonical owner, and duplicate
impls fail before IR. Method-local inference and materialization reuse the
existing function registry; there is no second trait body checker or cache.

Qualified `<Type as Trait>::method` calls select that exact implementation.
Dot calls search the admitted receiver candidates in Rust order, preferring an
inherent method over trait methods *at the same candidate*, rejecting ambiguous
traits and never retrying after a selected method fails its mutability check.
Reference-bearing trait signatures, implementations and calls require genuine
same-input external rustc checking before owned lowering, including unused trait
signatures. Trait signatures themselves emit no function or vtable.

Generic/blanket impls, bounds, generic traits, supertraits, defaults, associated
types/constants, unsafe traits/methods, explicit trait lifetime parameters,
trait objects, arbitrary autoderef and built-in trait/lang-item semantics remain
explicit gaps. A local trait named `Drop` is just that local trait, not a hidden
destructor or permission to implement the standard `Drop` contract. The pinned
core/array source remains an unchanged upstream frontier, not a successful build.

| Feature | Implementation | Acceptance | Boundary |
|---|---|---|---|
| `rust.traits.static-local-impls` | implemented | accepted | Qualified static local non-generic traits with safe required methods, concrete local record/enum impl coherence/completeness and alpha-equivalent signatures, explicit qualified calls and ordered receiver candidates. Trait methods reuse the existing generic inference/instance registry. Nine unchanged-original Linux x86-64 PCC/rustc1.91.1 raw native comparisons passed, including enum constructor namespace priority, concrete generic owners, method type parameters, receiver ordering/effects and local Drop-name identity. The three reference-bearing originals have genuine same-input checks; unused reference signatures also require actual certification. Eighteen complete invalid originals were genuinely rejected by rustc. Blanket/generic impls, bounds, defaults, associated types/constants, dyn, unsafe traits/methods, explicit trait lifetimes, nested Self/Sized obligations and built-in lang-item semantics remain gaps. Three added Darwin target cases remain explicitly unrun. |

Final static-trait candidate `45118c1e175b784e020000779a2e8ff26b3ec8fc`
passed the complete ordinary domain **1,110** (1,087 Rust +23 shared mechanism
tests) and separate integration domain **11**. All six workers observed **1,139**
pooled identities; **18** missing Darwin target prerequisites remain explicitly
unselected/unrun. All **90** original native/reference pairs passed, including
nine new trait originals. **59** runtime sources match actual metadata checking;
**31** value-only pairs are not borrow-certification claims. Repeated checker
observations are reported separately: **146 successes /31 genuine rejections**.

The earlier method-generic and result-context qualification summaries retain
their historical source identities. All **173** prior active evidence rows are
unchanged; **19** new trait rows are added. The unchanged private receipt reader
checks only those19 rows/nine raw identities, not missing historical artifacts.
Strict current-file verification covers all four public maps; diagnostic audit
is Rust-only. No new common ABI run or Mac execution is claimed, and this does
not qualify unrelated later peer code merely because its bytes are preserved.

Implementation proof: `pcc/frontends/rust/traits.py::StaticTraits.prepare` and
`StaticTraits.validate_signatures`, with
`pcc/frontends/rust/methods.py::InherentMethods.lookup`, at the exact tested
`45118c1e` source revision. The generic registry, runtime and backend are unchanged.
