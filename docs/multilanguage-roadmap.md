# Multi-language architecture and acceptance roadmap

This is the maintained implementation plan, not a claim of implemented features.
Start with [current status](multilanguage-status.md), then inspect current source.
The project remains PCC; unrelated repositories are outside this work.

## Outcome and priority

1. The C++, Rust, Java and Go frontends and their required shared infrastructure are the current highest implementation priority. C++17 remains general; NumPy is one consumer, not its specification.
2. Build and import unmodified NumPy 2.4.4 with the owned PCC toolchain. Close the pure-C extension chain first on Linux; macOS arm64 remains a required validation platform.
3. Develop Go, Java and Rust frontends in parallel toward the same conformance and real-project standard as C. Initial subsets are milestones, not completion.
4. Common infrastructure and correctness work must support these outcomes. Do not replace language implementation with unrelated cleanup or benchmark tuning.

Each language owns parsing, name lookup, typing and language-specific semantics under `pcc/frontends/`. All reuse public IR, the self backend and owned object/link/archive/sign machinery. External compilers are explicitly labeled development oracles, never fallback implementations.

## Convergence contracts

Sharing an IR is necessary but insufficient. Agree and test these boundaries before introducing language-specific runtimes:

- **Code generation:** frontend output has an explicit target triple, data layout, value representation and calling convention. Existing backend ownership checks apply. No external compiler or LLVM in the final pcc1 chain.
- **Interop:** use C ABI entrypoints with explicit scalar/aggregate layouts and ownership. GC references cross through rooted handles, not untracked moving addresses. First test different frontends in one owned-linked process; then add shared-object, GC and exception boundaries. A scalar link test does not qualify the latter.
- **Memory:** one owned allocator and shared GC/root machinery. Rust-owned ordinary allocations are not tracing-GC objects. Language-specific object layouts remain valid; do not impose a universal object header that breaks Itanium layout or Java/Go dispatch semantics.
- **GC metadata:** compiled frames need exact roots and aggregate pointer maps. State when roots become visible and when values are initialized/retired. Moving, concurrent and finalization behavior require actual collector-specific tests, not merely five launches.
- **Scheduling:** use shared blocking/wakeup mechanisms. Define OS/platform threads separately from logical virtual threads. Carrier-local state is not automatically virtual-thread-local. Explicitly specify errno, TLS, exception state, callbacks, pinning and suspension boundaries.
- **Exceptions:** shared unwind-table decoding and frame walking, with language-specific personalities and cleanup rules. C++ exceptions, Java exceptions, Go panic/recover and Rust panic are not interchangeable semantics. Do not unwind through an unrelated carrier stack or silently turn exceptions into error-return protocols.
- **Generics:** reuse instantiation identity, memoization, work queues and cycle handling where correct. C++ overload/SFINAE, Rust traits and Go constraints retain their own rules.
- **Tests:** reuse project materialization, source/manifest integrity, compile/link/run ownership and exact-output checks. Unsupported source is a capability failure, never a semantic pass just because a reference accepts it.

A frontend needing a missing shared capability must state the required contract and tests before creating an alternative allocator, scheduler, handle table or unwinder.

## Language tracks and exit criteria

### C++17

Complete domains rather than rewriting source tokens: namespaces/name lookup; nominal classes and access; constructors/destructors and lifetimes; references/value categories; operators and overload resolution; class/function templates, partial specialization, SFINAE and packs; lambdas; RTTI and exceptions.

Preserve Itanium object layout, mangling, virtual dispatch and RTTI. Implement owned table-driven unwinding and backend CFI/.eh_frame/compact-unwind support. External unwind libraries can be interoperability references, not final dependencies.

The standard library is PCC-owned: template headers shipped by PCC, non-template runtime operations implemented in pcc-Python over shared intrinsics. Do not substitute system or self-built libc++/libstdc++ as the final owned implementation. Their tests may be licensed acceptance inputs.

Order: type_traits/utility/limits/new; string/vector/array/smart pointers/algorithms; associative containers/functional; iostream; threads/atomics/chrono; locale/regex/filesystem. `format` is C++20 work, excluded from C++17 completion.

Conformance: GCC g++ execution tests, LLVM C++ SingleSource, standard-library tests and YARPGen differential programs. Projects, unmodified with own tests: TinyXML2/pugixml/Ninja; Snappy/LevelDB/yaml-cpp/googletest; fmt/nlohmann-json/Eigen; DuckDB. Compile-only compiler diagnostics are a separate layer, not executable project qualification.

### Go

Owned frontend and package graph; preserve Go constants, evaluation order, interfaces, maps, channel/select, defer/panic/recover and goroutine semantics. Use shared scheduling/GC mechanisms with Go-specific runtime contracts. Runtime-coupled packages require owned implementations; compile other standard packages from their original licensed Go sources.

Accept against official go/test, standard-package tests and a pinned project ladder. An integer package-main program does not establish strings/fmt, a standard library or goroutines. Do not replace panic with abort to make an upstream test compile.

### Java

Compile Java **source** through an owned parser, name/type analysis and direct lowering to public PCC IR. Libraries are built from verified sources; inspect source archives and licenses rather than assuming every artifact has a usable sources JAR. Do not introduce JVM bytecode, javac or another compiler's IR as a required production stage.

The existing class-file/JAR reader and static-bytecode AOT work is retained as an independent compatibility experiment, not the implementation mainline. Stop extending that track for its own sake. Reuse its verified integer wrap, shift and narrowing semantics in source-independent IR emission helpers; do not force Java source through a JVM operand-stack representation.

Implement source classes, objects, strings and arrays, then the runtime contracts they require: class initialization, monitors, platform/virtual thread distinctions, configured reflection and native interfaces. Runtime-bound Object/Class/Thread/String internals and native operations are owned pcc-Python implementations over the shared allocator/GC/scheduler. Other class-library functionality must come from suitable original sources or independent implementations. OpenJDK behavior/specifications may be studied, but GPL implementation code is not translated into PCC.

Accept with source-level conformance inputs, relevant licensed tests and unmodified real applications with their own tests. Bytecode-reader tests or one method selected from a library do not qualify Java source compilation or that library. No source availability gap may silently select an external-bytecode fallback.

### Rust

Preserve Rust lexical rules, scopes, typing, moves, references, traits, macro_rules, monomorphization and a suitable typed intermediate form. Without borrow checking, an experimental gate cannot claim to be a safe primary Rust compiler; require independently rustc-checked input and disclose missing proof.

Compile original core/alloc and later std with owned intrinsics/lang items and platform operations. Add owned procedural-macro compilation/execution later. Do not label a no_std integer function as core/alloc support.

Rust-owned Box/Vec/Rc memory uses ordinary allocation and language Drop rules. GC interop needs rooted handles, scoped borrows, pinning for exposed raw pointers, write barriers, native/managed thread transitions and explicit finalizer/Drop behavior. Document cross-runtime cycles; test all five collectors and multithreading.

Use rustc run-pass/UI subsets and unmodified crate projects. Keep arithmetic profile and panic strategy explicit. Research such as Flow Typing can suggest analysis invariants and counterexamples; it is not a replacement for Rust semantics or borrow checking.

## NumPy chain

First execute a small real C extension using native pcc1: executable C-API exports; extension symbols bound to the host; public loading through the owned mapper; package build/link wiring; load, call and release under all five GC modes. The process must have one runtime/heap/GC, without cc or host Python in the target chain.

Then close Python subclassing of C extension types, generation scripts run by PCC, PCC execution of the supplied Meson, Cython and a per-option compiler support inventory. Start with bundled lapack_lite/allow-noblas, no external BLAS/Accelerate. Mixed experiments compiling C++ with an external oracle may expose runtime gaps but never count as owned builds.

Final gate: build extensions and package from unmodified NumPy 2.4.4; native import and core tests agree with CPython plus official NumPy under five collectors without crashes/leaks. Missing capabilities fail explicitly; no package-name special cases.

## Shared optimization track

First audit existing escape/scalar-replacement, ownership/RC and thread-escape analyses. Independently implement generic improvements with semantics preserved. Unknown calls and dynamic loading are conservative; AOT does not automatically imply complete program knowledge.

Identity, weak references, finalizers, resurrection, GC introspection, exceptions and suspension enter the effect/escape model. Prove RC removal and storage reuse across all relevant exits. Logical-thread-private is not carrier-private. Sampling percentages do not establish call counts or predicted end-to-end speedups.

Later directions: local allocation buffers/generations, request arenas, checked owned/borrowed annotations in pcc-Python and Rust hot libraries. Do not turn these into package-specific shortcuts or assume a single collector's behavior applies to all five.

## Source and license discipline

Pin upstream repository, commit, path, file hash and actual license/notice for every adapted input. GraalVM is an architectural reference, not a blanket source license. Graal compiler/SubstrateVM/Espresso implementations are not ported into PCC; use papers/specifications for independent implementations. Audit GraalPy per file: UPL Java files and CPython-derived C files can differ. Retain notices for permitted adaptations. Do not copy Truffle AST-specialization/JIT structure into an AOT compiler merely because it exists upstream.

## Validation and release discipline

- Root publisher alone updates master; integrate fresh complete remote tree and exact path preimages/modes. Never overwrite peers, force-push, rewrite history or create empty progress commits.
- Save independently reviewable real code/test batches promptly with a subject, blank line and accurate body. Until the maintainer installs the separate fast language CI channel, retain `[skip ci]` to avoid triggering the legacy slow suite on every push. After the split, code uses the fast channel and pure documentation keeps `[skip ci]`. Report observed failures and unrun gates.
- Shared runtime/backend/codegen batches are serialized: after the root publisher publishes the selected validated batch, wait for the user's relayed Mac QPS assessment before stacking another shared batch. Independent frontend work, including frontend-specific IR lowering, and tests continue. Preserve candidates; do not revert correct behavior for a score.
- Preserve original time/memory limits, default six-worker pytest execution and thread/five-GC requirements. Use actual shared performance locks; no fake lock-held claims or retries that conceal resource failures.
- Include focused tests for every changed contract and run bounded minute-scale local validation. The latest work split leaves legacy CI/bootstrap/full-suite cleanup to the maintainer; do not let it displace frontend implementation. Preserve earlier full-group results and failures honestly. Shared runtime/backend/linker/GC changes are handed to the maintainer for platform validation. Separate host checks, AST/IR, cross objects, native execution and full bootstrap.
- Missing prerequisites are not selected under the existing collection policy, not pytest.skip or simulated success. Genuine unsupported semantics remain visible failures. Existing failures need fixes or explicit issue tracking.
- macOS arm64 is required, as are Linux x86-64/arm64 results. No access to the user's Mac/M2 is authorized. The maintainer owns the new path-triggered language CI workflow and old CI repairs. Do not modify workflows or start repeated legacy runs; existing manually authorized validation does not authorize user-device access.
- Keep raw logs, receipts, recovery materials and credentials outside Git. These pages contain source-oriented status and reproducible commands, not machine-specific recovery inventories.
