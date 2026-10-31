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

- A crate of monomorphic functions and named-field structs, optionally `#![no_std]`, `pub`, and
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
generics/traits/impls, closures, macros, pointer operations, heap allocation,
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
attributes/derive, `repr(transparent)`, traits, methods and references. Those
unmodified dependencies remain unsupported; neither `core` nor `alloc` compiles
from this batch. Named structs/tuples are only a prerequisite domain, not a
replacement source rewritten to resemble that upstream library.

### Proposed generic-instantiation boundary (not implemented)

A later shared compile-time contract should identify definitions by crate/module
and definition identity, bind semantic type/constant arguments, record a target
and ABI/layout identity, and cache/deduplicate instantiated public IR while
rejecting recursive instantiation cycles. Rust name resolution, trait obligations,
coherence, associated types and lifetime constraints remain frontend-owned input
to that contract. It must not imply a separate language runtime, allocator or GC.
No private generic engine or runtime has been introduced here.

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
copied, non-Copy fields are moved by variant/field path. Unmoved sibling payloads
remain readable, whole-owner use after a partial move is rejected, and whole
assignment restores initialization. Move joins retain early-return and loop
break/continue behavior. Bindings inside or-patterns remain an explicit gap;
binding-free or-patterns retain constructor-aware coverage.

Only types without a supported destructor enter this slice: primitive values,
unit, recursively admitted tuples/records/enums. References stored in payloads,
Drop implementations, user-defined Copy/traits, borrowing patterns and general
lifetime checking are rejected, not implemented by discarding destructor calls.
There is no heap allocation or private drop/GC/unwind runtime. Full original
Rust 1.90.0 Option/Result inputs still fail at documentation attributes and need
generics, derives, impls, macros, lang items, references and owned intrinsics;
the six original PCC payload programs are separate specification fixtures.
