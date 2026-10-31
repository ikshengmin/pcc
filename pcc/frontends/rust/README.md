# Experimental Rust frontend: first owned scalar slice

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

- A scalar crate of monomorphic functions, optionally `#![no_std]`, `pub`, and
  `extern "C"` definitions. Forward calls and recursion resolve before lowering.
- `i32`, `i64`, `u32`, `u64`, `bool`, unit; typed parameters/returns; contextual
  inference of unsuffixed integers (default i32 after function constraints).
- Initialized `let`, `let mut`, shadowing and lexical scopes; assignments and
  `+=`, `-=`, `*=`. Unit arguments have no payload at the private Rust boundary.
- Block/tail expressions, `if`/`else`, `while`, `break`, `continue`, `return`;
  short-circuit `&&`/`||`; integer arithmetic/bitwise/comparison operators,
  boolean bitwise/comparison operators; integer casts and bool-to-integer casts.
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

Symbols are `__pcc_rust_<name>` and returned in `symbols`; this is **not rustc's
Rust ABI or symbol mangling**. The finite `extern "C"` scalar boundary uses
32/64-bit integer parameters and integer/unit return values. No stable Rust
cross-compiler ABI is claimed; no native Rust standard-library objects are linked.

## Boundaries and next owners

This is not C-level language/ecosystem parity. References/lifetimes/borrowing,
move/drop analysis, structs/enums/arrays, patterns/match, modules/imports,
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

Linux tests execute the owned ELF result and compare every return byte. Darwin
checks run cross-emission on Linux, inspecting Mach-O CPU/filetype, symbols and
branch relocations for unchanged Rust sources. **They do not execute on a Mac**,
certify a Darwin runtime/link path, or replace target-device qualification.
