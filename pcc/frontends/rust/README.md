# Experimental Rust frontend: owned scalar and aggregate slices

`compile_rust(source, experimental_rustc_checked=True, overflow_checks=False)`
returns a `RustCompilation` containing the public PCC IR module, IR text and
source-function-to-symbol map. The frontend has its own lexer, parser, name
resolver, integer inference, type checker and control-flow lowering. It does not
strip Rust syntax, translate into Python/C, invoke rustc, or substitute a Python
interpreter. The shared PCC self backend emits and links the native program.

**Not a safe primary Rust compiler:** no borrow checker exists. The mandatory
experimental flag is a caller attestation that rustc checked the *unchanged*
source; it is not proof or an automatically verified receipt. The normal/default
call rejects compilation. Host-PCC evidence is not native pcc1/fixed-point proof.

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
  code. Documentation comments are attributes and are explicitly unsupported,
  never discarded as ordinary whitespace.

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
Drop analysis, user-defined Copy, zero-field structs, tuple structs, arrays, general patterns/match ergonomics, modules/imports, never-typed tuple operands,
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
referent writes. Mutable references, reference creation/dereference/casts,
reference returns, references stored inside aggregates and lifetime syntax
remain explicit capability gaps. Default Rust aggregates are not an FFI layout.

This remains **caller-attested compilation, not borrow checking**. The caller
must supply independently rustc-checked unchanged Rust source. Foreign callers
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
64-bit isize range but only unsuffixed constant expressions are accepted until
isize is a source type. Default storage is a PCC-private i64 tag, **not rustc's
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
upstream-project qualification. Caller attestation and missing borrow-checker
limitations are unchanged. Darwin is still cross-object emission only.

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
Optional rustc checks remain unavailable when the tool is absent; the public flag
is only caller attestation, never proof that PCC checked ownership or lifetimes.

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

Calls require explicit Rust turbofish type arguments (`identity::<u64>(value)`).
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
compiler does not infer type arguments, prove borrow lifetimes, implement Copy
or other trait bounds, or erase destructor obligations. Admitted values still
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
runtime dispatcher is added. Method-local generic parameters and general call
argument inference remain unsupported.

Only value receivers are admitted. `&self`, `&mut self`, explicit receiver types,
autoderef/autoborrow, reference-returning methods, traits, Drop, vtables, closures,
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
