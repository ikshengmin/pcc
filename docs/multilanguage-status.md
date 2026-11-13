# Multi-language implementation status

Latest executed project baseline: `2dc72b759676649eecea8057218c55ff54be4e31`.
Implementation is driven by the first failure of each unchanged upstream project,
not by feature-test counts. No target below has passed its complete PCC build and
upstream tests. Mainlines compile source directly to PCC IR.

## Project targets

| Language | Target | Current frontier | Next work |
| --- | --- | --- | --- |
| C++17 | TinyXML2 11.0.0, original `xmltest` | Local Mac and Linux x86-64 container: owned `<cerrno>`/`<ctime>` move `xmltest.cpp` to the non-standard `<sys/stat.h>`. Single non-virtual bases work, including base construction/destruction and a polymorphic primary base with overriding, covariant returns, abstract classes and constructor/destructor table stores (executed against g++). Class templates take a nondependent base, including a polymorphic one, with virtual/override members whose specializations own their tables; unnamed member enums instantiate per specialization (`ITEMS_PER_BLOCK`). Trivial unions and data-only member classes/unions of class templates (`MemPoolT<N>::Item`/`Block`, Itanium nested names under the specialization) now work, as do dependent template-id members (`DynArray< Block*, 10 > _blockPtrs`, constructed and destroyed with `MemPoolT`), deferred member calls on them and `MemPoolT< ITEM_SIZE >::Clear()`. Allocating `new`/`delete` now call the global replaceable allocation functions (sized `operator delete`; trivially destructible arrays without cookies; owned weak defaults for executables, whose execution is still unrun), dependent `static_cast` and ordinary string literals work in member bodies, so all of `MemPoolT` compiles. Polymorphic classes without a user-declared constructor get their implicitly-declared default constructor (defaulted on first declaration: base, own table pointer, members; value-initialization zeroes first), so `XMLVisitor` is constructible. Non-inline static data members (in-class declaration, one `T C::m = init;` definition, class-scope initializer lookup, `x.m`/`p->m`) and string-literal `const char*` static initializers now work (ten programs run natively on the Mac with owned arm64 compile/assemble/link and match Apple clang), so `XMLUtil::writeBoolTrue` compiles; `tinyxml2.cpp` then stopped at `tinyxml2.h:993:25`, `XMLText`'s use of the protected `XMLNode` constructor. A base constructor in a mem-initializer is now access-checked through `*this` ([class.access.base]/5, [class.protected]; five programs run natively on the Mac and match Apple clang), so `tinyxml2.cpp` stops at the same coordinate on the next gap: constructing `XMLNode`, whose `StrPair` member needs destruction, before its out-of-line constructor body gives a closed-construction proof (`cpp.unsupported.destructor-subobjects`; local Mac, cross-compiled for x86_64-unknown-linux-gnu; Linux execution unrun). No executable or upstream test execution. | Base construction through a constructor declared before its body when the base owns members needing destruction (`tinyxml2.h:993:25`); constant/inline static data members; implicit constructors of class-template specializations and of derived classes whose base has a constructor; polymorphic `delete` and class-element arrays (`new`/`delete`, qualified self calls), implicit virtual destructors and member-destruction proofs for destructor bodies with calls; an owned POSIX `<sys/stat.h>`/`<sys/types.h>` surface. Keep the unchanged whole-project gate. |
| Java | Commons Numbers Primes 1.2 | Local Mac and Linux x86-64 container: non-throwing primitive local arrays lower onto the shared GC0 managed envelope; throw statements and class creation parse with explicit sema gaps; the class model retains declaration type arguments, so the complete `Primes.java` parses. Non-constant and primitive-array `static final` fields are now checked class-initialization steps in textual order (JLS 12.4.2; unfolded active-use reads, static slots and the unmet initialization-runtime obligations), so `SmallPrimes.PRIMES`/`PRIMES_LAST` are analyzed. One-dimensional primitive array type arguments now parse (`Entry<Set<Integer>, int[]>`, `SmallPrimes.java:90:38`), so the class model stopped at that blank `static final` field, `SmallPrimes.java:90:45`, which its `static {}` block assigns. Static initializers are now checked step-9 steps and blank static finals are definitely assigned by their top-level `name = value;` statements (JLS 8.7, 16.8; nested assignments stay an explicit gap), so SmallPrimes parses into its static block and stops at `new HashSet<>()`, diamond creation, `SmallPrimes.java:109:54` (local Mac; JDK reference lane unrun). The native domain still stops at the `List<Integer>` result of `primeFactors`, `Primes.java:108:19`. PCC native execution of managed arrays is unrun. All 17 original inputs are intact. | Generic/diamond creation of supplied JDK types (`SmallPrimes.java:109:54`), reference-typed static finals, native class initialization with static roots, then supplied JDK types (`List`, `ArrayList`, `Integer` boxing) and exceptions; an owned runtime link path for managed Java programs. |
| Go | Go 1.27.3 `math/bits`, whole package and tests | Local Mac and Linux x86-64 container: dynamic integer division checks its divisor (terminal `runtime.divideError` panic, signed MIN/-1) and a runtime-owned error is admitted as a terminal panic operand, so every normal production file of the package now lowers to owned IR (previously `bits.go:507:9`). The internal test package (`export_test.go`) also lowers. Slice types now parse everywhere (the checker keeps the slice-storage gap) and array literals take elided element types (owned arm64 Darwin execution matches a local go1.23.6 reference), so the external test package parses past the `TestReverse` table (`bits_test.go:458:23`, `:461:3`). Function types and literals now parse (function values are an explicit checker gap), so it stops at the `defer` statement at `bits_test.go:851:4`. The upstream tests remain unrun: they need closures, slices, `defer`, the `testing`/`fmt`/`os` closure, a test entry, and `defer`/`recover` for the panic tests. | `defer` statements (`bits_test.go:851:4`), closures/function values, then slice storage on the shared managed allocator; owned package-test entry and dependency closure; interface values and runtime error objects beyond panic operands; recoverable panics. Keep the official reference strict. |
| Rust | `fallible-streaming-iterator` 0.1.9 | Local Mac and Linux x86-64 container with an audited rustc 1.91.1 receipt: certification passes; the owned parser retains Fn-family bounds, `Some(e)` patterns and closures (explicit semantic gaps), iterates integer ranges and admits impl lifetime parameters behind the actual check, then stopped at `lib.rs:306:1`, the blanket trait impl for `&'a mut I`. Generic and blanket trait impls now parse (their instantiation is an explicit semantic gap), then stopped at `lib.rs:367:16`, the bound `I: Iterator<Item = Result<&'a T, E>>`. Trait-bound generic arguments and associated item constraints now parse (trait/projection solving is an explicit semantic gap), then stopped at `lib.rs:373:28`, the outlives bound `T: 'a` of `struct Convert`. Outlives bounds on struct/enum type parameters now parse and are erased after the actual check (trait bounds there stay an explicit gap), so the parser stops at `lib.rs:427:23`, the tuple struct `pub struct Empty<T, E>(PhantomData<(T, E)>);` (local Mac, actual check for aarch64-apple-darwin with a local rustc 1.92.0 receipt; the Linux-target lane is unrun). No PCC project object. | Tuple structs and `PhantomData`, generic/blanket trait impls over references with `?Sized`, associated-type projections, then the core Option/Result model and closure traits/types; keep the original project gate strict. |

Package targets pinned for test-driven work (docs/python-native-extension-languages.md): `projects/zxing-3.5.4` (Java Reed-Solomon; with `@Override` parsed and checked, native stops at `GenericGF.java:33:23`, a static field of class type, and the class model at `GenericGF.java:42:17`, final instance fields) and `projects/go-enry-2.9.6` (Go tokenizer slice; first blocker `tokenize.go:16:23`, slice storage), `projects/uv-0.13.7` (Rust executable, the uv package manager: with owned cfg from uv's default features and release profile, three of its four pinned roots stop at an external `extern crate`, `format!` and an `impl Trait` return type, while `crates/uv-version` compiles in full and its owned native build prints `0.13.7` (local Mac; needs the value-only attestation, as PCC has no owned borrow checker); `@` binding patterns, grouped use trees, `pub use` re-exports, restricted visibility, tool-scoped crate lints, owned cfg selection, integer/bool/`&str` const items, `env!` over an explicit Cargo crate environment, `&'static str` literal values, lifetime generic arguments (behind the rustc check), built-in derives, `const fn`, `matches!`, struct-like enum variants and static items are now owned; see its README), alongside the existing orjson (Rust: `$` now lexes and macros are an explicit parser gap, so `src/lib.rs` stops at its first crate attribute, `lib.rs:4:4` `cfg_attr`, which needs a rustc-checked configuration that the unpinned dependency closure cannot yet provide; `build.rs:7:58` runtime strings) and NumPy targets.

Project inputs and tests belong with their runners and source locks. Detailed
feature obligations belong in `specs/features/` and language tests, not this page.

## Shared prerequisites

- The shared managed ABI is an experimental opt-in **GC0-only tuple envelope**,
  not a general type-bitmap object model. Reuse the allocator, roots, slot visitor,
  barriers and relocation owners to design one common object header and type
  descriptor for Go/Java, coordinated with the count-free tracing work. Current
  count-free GC1/2/4 experiments do not establish GC3 or five-GC acceptance.
- Reuse existing virtual threads, carrier threads and channel mechanisms.
  Shared language exceptions/panics need one owned unwinding design; CFI tables
  alone do not establish that design or cross-platform exception support.
- Existing scalar integration is C++ calling Java-source, Go and Rust in one
  owned-linked process. The compiled-Python four-source gate now exists: owned
  provider object checks pass, but all four execution nodes stop at the missing
  verified runtime archive. Same-executable execution remains unrun. Buffers,
  GC handles and C-API extension modules follow this gate. Java bytecode compatibility remains a separate frozen
  experiment.
- Shared runtime/backend changes retain the single-batch Mac/QPS review gate.
  Do not access the user's Mac or edit the separate Python IR/RC performance line.

## Validation

[CI run 41592994473](https://github.com/ikshengmin/pcc/actions/runs/41592994473)
tested `2dc72b7` and ended with 44 jobs: 12 successful, 31 failed and one
cancelled. The C++ reference job was cancelled despite retained failing results;
it is not a passed reference lane. No target above passed its complete PCC build
and upstream tests. The older [CI run 41570071093](https://github.com/ikshengmin/pcc/actions/runs/41570071093)
tested `1e0083ea`; neither run validates later commits. The earlier
[bounded passed-test summary](../specs/conformance/summaries/ci-41550908488-passed.json)
is tied only to its recorded revision, test IDs, versions, platform and hashes.

Published CI-diagnostics checkpoint [`80526e2`](https://github.com/ikshengmin/pcc/commit/80526e2a8d58b16d6ebed98490d6ebfd0b50c96e)
aligns the macOS timeout test with the existing 7200-second limit and supplies
checkout imports and early output capture for temporary worker children. Its
original workload, retry, concurrency and cleanup assertions remain intact.
That checkpoint is included in `2dc72b7`; results remain bound to the executed
revision and do not qualify the newer source changes below.

Newer GC source-test pointer providers, C++ copy-reference/lookup repairs, Rust
configuration policy and reference-inventory repairs remain execution-unrun.
The two exact unused-template C++ cases are ill-formed, no diagnostic required:
a reference compiler may diagnose them or accept them without a diagnostic.
Source-only correction [`0b532d6`](https://github.com/ikshengmin/pcc/commit/0b532d6c80f5ac17c69286013b64e4144e91a332)
retains the original inputs and PCC eager rejections while recording either
reference outcome. Reference acceptance is not semantic acceptance. The next
deliberate non-skip validation must execute the changed source.

NumPy source preprocessing stops at generated `_numpyconfig.h` and `stdarg.h`.
orjson yyjson preprocessing/parsing passes; Rust build/import remains blocked.
Their complete owned builds/imports, native pcc1 and full five-GC acceptance
remain open. Latest user direction puts execution in CI, not local test runs.
Keep original failures and reference disagreements; do not weaken assertions,
resource limits or prerequisites. Interim commits use `[skip ci]`; validation
batches are deliberate, not automatically repeated after every commit or failure.
