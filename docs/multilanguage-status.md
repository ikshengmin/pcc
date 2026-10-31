# Multi-language implementation status

Status boundary: 2026-10-31, published source through `822ef7c170fcf39db668d0de5bc89ca0aba86cec`. Candidate evidence below is explicitly separate from master.

Maintained entrypoint for a new contributor or AI. Read [the roadmap](multilanguage-roadmap.md), then the current implementation and tests. This is a source-oriented status page, not a running log or evidence archive. Update the relevant section when publishing a capability or changing its acceptance status.

## Read this first

- C++, Rust, Java and Go frontends plus required shared infrastructure have highest implementation priority. NumPy's owned pure-C extension closure proceeds first on Linux. Native self-host/five-GC/CLI remain non-regression contracts, not a reason to divert into legacy CI cleanup.
- None of the four new language tracks is at the existing C project's maturity. No completion percentage is inferred from test/commit/line counts.
- No whole TinyXML2 build by PCC or owned NumPy import is established here. G++ compiling upstream TinyXML2 is reference evidence only.
- Host Python invoking PCC and executing its owned native output is distinct from native pcc1 compiling the frontend and building a project. Do not relabel the former as bootstrap success.
- Shared runtime/backend/codegen changes are single reviewed batches, followed by the user's relayed macOS QPS assessment before another shared batch. Independent frontend work, including frontend-specific IR lowering, and tests may continue.
- macOS arm64 CI is authorized; direct access to the user's Mac/M2 is not. Missing platform execution remains unrun, never replaced by cross-object checks.

## Published source landmarks

- `86489973646ee1135865ff131100187083bcfb4c`: C++ namespace scopes, aliases, using declarations/directives and nominal lookup groundwork.
- `a57a0b3ad0a16296e0ac1b478136d13d25732b91`: nominal class/data-member slice; not complete construction/destruction or inheritance.
- `eec313f0a898091f35607b919c61922ece51242d`: explicit nested aggregate initialization.
- `5a12d1e5c2fa12d1affd0cf94e2e269ff6ee5cef`: typed integral constant facts and initialization checks, not general constexpr evaluation.
- `e9b32227d519094795821e704c276a83a973bdae`: focused Linux/macOS platform execution adapter and manual-only CI.
- `f5467b368d1a0e3df9d6bdc922914abac5a8e974`: namespace enum identities, underlying types, promotions/conversions and ABI observations.
- `0c08c6e0102c62b9bcb3c62047c9972bf62ca89c`: class enums, class-qualified lookup/access and class-prefix nominal ABI.
- `00529a7873bbd72c124fe2b0486635d32eb09bc6`: first owned Java class-file/static-integer AOT frontend.
- `7fcf752aac514a7e99cb9a70ad54cac855456a86`: experimental typed Rust scalar/control-flow frontend.
- `c37e7a6a3636357d8808a308e8c1fe7f484c1787`: typed Go scalar/multi-file/package-graph frontend.

These landmarks identify source changes, not blanket qualification of every later tree. Inspect `git log`, current files and the validation boundary before reuse.

## C++

Source: `pcc/frontends/cpp/`. Public translation-unit selection still enters through `CEvaluator.compile_translation_units` with `.cpp` units; the owned C preprocessor, C AST and existing C lowering are reused where semantics agree. `cpp/driver.py` performs C++ preprocessing; it is not an independent compiler executable.

Implemented bounded domains include namespace lookup, nominal class/data-member handling, explicit aggregate initialization, typed integral constant facts, namespace enums, and existing scalar/operator/function-template subsets. This is not a complete specification coverage list.

Latest namespace-enum evidence: 175 ordinary cases; initial integration 35 passed and one observer assembly failure. The observer was changed to equivalent loads/register comparisons, then the exact failed case passed. Candidate ordinary C++ domain: 1188 passed and 49 subtests. That run predates inclusion of the separate platform-adapter tests in its candidate snapshot; it is not a claim that the merged master received the same whole-domain rerun.

Published as `0c08c6e0` from reviewed candidate `0d5bcbbf67cd60e776fec6f5469aaea4efd86059`: class-scoped named enums, shared `Class::` lookup/access, public aliases of private enum declarations, class-prefix mangling and ADL. Its final host ordinary domain includes the platform adapter: 1329 passed and 49 subtests; 18 integration cases passed. An earlier incomplete whole-domain run failed a model-scope guard; that guard was fixed and the full domain rerun. Object-enumerator base evaluation was subsequently published as `b99804b5`: its own 21 ordinary and nine owned/reference integration pairs passed. These later results do not retroactively extend the whole-domain qualification above.

Published `0cae8e1e` adds real C/C++ language-linkage declarations and blocks, canonical C entities across namespaces and integral static_cast; 43 ordinary and 16 real integration cases passed. This remains a bounded linkage/cast domain.

Published `b0f4e7e5` adds for-init scope, correct continue/step control and typed increment/decrement; its final 49 ordinary and 28 integration cases passed after fixing a range-for diagnostic regression. Published `3cda51fe` adds namespace/global object storage and linkage, const/static/extern identity and source-ordered initialization/definitions; 53 ordinary and 26 integration cases passed. These are separately qualified frontend increments, not complete C++17.

Published `822ef7c1` adds typed fixed local arrays, multidimensional explicit initialization, decay/subscript lvalues and evaluation order, adjusted array parameters and unnamed concrete parameters. The final 79 ordinary and 41 integration cases passed with 82 owned/reference raw executions; an earlier 72-pass/two-failure/two-unfinished run exposed unsupported unnamed parameters and was fixed without replacing its source inputs. Class-array lifetimes, strings, dependent bounds and namespace arrays remain explicit gaps.

A real public-host-frontend attempt at the unmodified TinyXML2 upstream-tests pair failed first in owned preprocessing: `header not found: cctype`. All 19 pinned project files were unchanged. No project object/run or upstream-test pass follows from the array examples.

Next language blockers: original TinyXML2 owned header/class dependencies, construction/destruction and lifetimes, references, richer overload/templates, inheritance/virtual dispatch, lambdas, RTTI/EH and owned standard-library headers/runtime. Keep object-expression evaluation when adding member enumerator access; do not erase side effects to accept syntax.

## Go

Published in `c37e7a6a` from reviewed candidate `a08aaea0`. The frontend owns `pcc/frontends/go/` and `tests/go/`. First candidate covers explicit 64-bit integer/bool semantics, Go semicolon insertion, untyped constants, scopes/functions, control flow, parallel assignment and short-circuiting; outputs the public IR and existing owned objects/linker.

Observed first candidate `b00750c1`: 92 ordinary/native-contract tests passed. Three complete original fixture programs were also independently built/run by the official Go reference; all three matched their owned program result. Separate raw 64-bit observations cover values not representable by an exit status. These fixtures are not standard-library or project qualification.

Real upstream frontier inputs are preserved with license/hashes: `test/ken/for.go` requires panic semantics; `math/bits` requires package and unsigned-type capabilities. Testing that these fail explicitly is a diagnostic-contract test, not a successful upstream compile. Subsequent review found parenthesized short-declaration and cross-file import-alias/package-name collision gaps. Both were fixed. The final published candidate passed 129 cases, including eight owned native executions, four official reference programs and official rejection comparisons; all source/tool identities were checked before and after.

Published `243e383a` adds signed/unsigned 8/16/32/64-bit families and target-sized aliases, typed/untyped constants and conversions, width-correct arithmetic and guarded shift lowering. Its final 241-case gate includes seven official-Go comparison nodes and 34 full-width owned observations. This is not dynamic panic, array or standard-package support.

Next: package globals and fixed arrays required by original math/bits, aggregate/string/slice representations and runtime contracts needed by original standard packages. No goroutine/channel/map/GC or native pcc1 qualification yet.

## Java

**Current mainline is Java source -> owned parsing/Sema -> PCC IR.** The first bounded source frontend is published as `4409a905`; complete Java-project qualification is still absent. It must reuse source-independent verified integer semantics, not javac, generated JVM bytecode or a mandatory JVM stack IR. Libraries must be built from verified source inputs.

Published source scope includes static int/void methods, locals, calls, if/while/for, scope and definite-assignment analysis, direct IR and full-width integer observations. Its 207-case gate separates 95 source cases, 35 reader cases and 77 historical compatibility cases; those are not 207 source-language cases. Fifteen source native observations and arm64 Darwin cross-object checks do not establish macOS execution or native pcc1. Published `5241f2e3` then adds supplied multi-unit source closures, single-type imports, resolved class-qualified static calls and cross-unit access/signature checks. Its 249-case gate comprises 42 new cases plus the previous 207; eight multi-source native programs preserve full i32 observations. Unknown package-qualified roots fail explicitly until implicit-type metadata exists. This is a compiler fixture capability, not an upstream library pass. A real attempt at the original Commons Numbers primes production closure failed at an unsupported string token; no object was emitted and its original tests were unrun.

Historical compatibility work was published in `00529a7873bbd72c124fe2b0486635d32eb09bc6` from candidate `1c73666f`: bounded class-file decoding and selected same-class static integer/void bytecode AOT through the self backend/owned ELF linker. The final gate passed 107 cases, including independent full-width native observations and one actual JVM reference of the same original class. Review-found disconnected-code/method-flag gaps and a diagnostic-order regression were fixed without weakening the old assertion; the initial failure remains historical evidence.

`ac5a9136d5a7bebe5ec6ef1105de8dea0893afa7` subsequently encoded internal module labels while preserving original class identity. Its exact prior-source-plus-two-path view passed five independent copied-source label/object tests. This corrects the previously listed label-escaping gap.

The validated but unpublished JAR follow-up is retained, not promoted into the mainline. A synthetic original JAR reached native execution in that experiment; an existing PDFBox JAR was only structurally read and rejected at missing runtime support. Neither result establishes a real Java source project. Do not continue class/JAR expansion merely because the importer already exists.

Next: source classes and method semantics, objects, strings/arrays and shared runtime contracts. Runtime-bound JDK operations are owned implementations; OpenJDK is behavioral/specification reference, not code to translate into PCC. Preserve the bytecode compatibility code/tests as historical work without using them as source-frontend evidence.

## Rust

Published in `7fcf752a` from reviewed candidate `c742e778`. The frontend owns `pcc/frontends/rust/` and `tests/rust/`. First candidate has an independent lexer/parser, typed lexical scopes, functions/control flow and public IR lowering. Its `experimental_rustc_checked=True` gate is caller attestation, not proof of a rustc invocation. It explicitly lacks borrow checking; overflow profile and unsupported panic/runtime capabilities are declared.

An initial 74-case gate and four original-source native programs passed. Independent review subsequently found grouping/doc-comment/whitespace and constant-analysis edge cases; corrections and full-width observation tests form candidate `c742e778da8c6cc0106d5c9daa196902be0f6dec`: 123 passed with six actual full-width native observations. This is not retroactive qualification of the original one. rustc reference execution is unrun where the tool is unavailable; missing tools are not selected, not fabricated successes.

Published `d2a01555` adds explicit target selection, owned emission and rejection of unsupported ABI combinations. Its 161-case gate includes real arm64 Darwin Mach-O header, symbol and relocation checks; these are cross-generated objects, not Mac executions.

Published `69155020` adds named records/tuples, field access and initialization, value aggregates, partial-move/reinitialization checks and conservative branch/loop move states. Its 259-case gate includes eight original full-eight-byte native aggregate programs and eight Darwin cross-object observations. Fourteen rustc reference cases were unrun because the tool was unavailable. This does not supply general borrow checking, stable aggregate C ABI or core/alloc.

Published `f69ea411` adds bounded extern-C declarations, unsafe-call admission, no_mangle exports and readonly references to qualified repr(C) integer records. The 34-case focused FFI gate and 293-case Rust domain passed. Original Meson Rust adder plus two original C functions were compiled through owned frontends, linked together and executed with full 12-byte results. A separate readonly-reference layout observer produced eight bytes; actual Darwin external relocations were inspected. This is not the complete Meson project/shared-library build or native Rust-to-C reference-argument forwarding. Sixteen rustc references remain unrun.

Next: enum/match semantics, real core/alloc/crate dependencies, traits, full moves/borrows and macros. The complete Meson example additionally needs its allocation/shared-library closure. No claim of core/alloc, safe primary-compiler status or shared-GC interop follows from integer programs.

Research input: *Flow Typing: A New Lens on Linearity* (Wilshaw/Hutton manuscript) suggests explicit resource-state and effect-order checks. Its own language differs from Rust; limitations around returned mutable-borrow pairs and escaping closures must not become semantic shortcuts.

## Shared backend/runtime candidates

Candidate `64893c10aa4831507af6d5434bbde8b995113c3f` for explicit Linux x86-64 CFI/.eh_frame has real external throw-through-two-owned-frames interoperability evidence. Final loaders do not yet register its unwind tables, so generation is opt-in and existing loader rejection remains intact. This is not the owned unwinder, personality/LSDA or language exception implementation.

Its focused/ELF tests passed; three related indexed-bridge negative tests fail identically on the tested parent and candidate. Keep these failures visible. Additional Mach-O coverage completed: 2264 passed and 35 subtests. The ABI/errno/frame expansion was interrupted by the original disk free-space floor: 184 passed, 6 failed, 411 unfinished, with causes not established by parent comparison. It is not a full pass. Under the latest work split, retain this evidence and hand shared-component validation to the maintainer rather than expanding legacy cleanup. Do not publish a second shared runtime/backend/codegen batch before the user's Mac performance assessment of the selected first batch.

GraalPy-informed C-API work has a separate dictionary strong-reference out-parameter candidate `81d5dec527156841afaa616fb4e1d8c03633d16e`, with its source-audit document in `4baf5252`. Its approved three-file host contract gate passed 37 tests with default six workers; it remains unpublished and held behind the runtime batch policy. Audit each upstream file's license: Java UPL and CPython-derived C PSF notices differ. The candidate's host semantic evidence is not native five-GC validation. Existing native archives cannot certify changed runtime sources.

Published `9ef9610f` adds one-process cross-language C ABI tests. Four source frontends emit objects linked into one owned Linux x86-64 executable; C++ actually calls the other exported functions with eight arguments, including register/stack and high-bit observations. The source and historical bytecode nodes each passed separately on earlier qualified input closures, each with two byte-identical builds and two actual runs. A subsequent single focused run against exact published `822ef7c1` production and ABI-test inputs passed both unchanged nodes with the original six workers and resource limits. Each node again produced two byte-identical fresh builds and two successful full-32-byte native observations. This renews only this scalar interoperability boundary, not the whole frontend suites or projects. The source node prohibits bytecode fallback. This qualifies the observed scalar boundary only, not stable public naming, aggregate ABI, callbacks, shared GC handles or exceptions. Rooted handles, precise frame maps, barrier/native-managed transitions and exception/suspension cleanup remain open. No separate language runtime should bypass these contracts.

## Tests and reproduction entrypoints

Use the repository's declared Python/tool environment and original watchdog/resource policy. Do not remove default xdist or enlarge limits. Commands below select tests; they do not waive performance locks or supply missing prerequisites:

- Ordinary C++ domain: `uv run pytest -x tests/cpp -m 'not integration'`.
- Focused real C++ platform execution: `uv run pytest -x tests/cpp/test_cpp_platform_execution.py -m integration`; select and identify the explicit external reference compiler separately.
- Real project fixtures and upstream tests: `tests/cpp/project_cases.py` and `tests/cpp/test_cpp_projects.py`. TinyXML2 `upstream-tests` keeps its original `xmltest.cpp` and resources; smoke and upstream variants are distinct.
- New language domains, once their source is published: `uv run pytest -x tests/go`, `uv run pytest -x tests/java`, `uv run pytest -x tests/rust`. Run their explicitly marked integration lanes separately; a default ordinary run does not establish native pcc1/frontend bootstrap or whole-project qualification. Ordinary tests can execute host-PCC-owned native artifacts; markers and execution ownership are different axes.
- Same-process source interoperability: `uv run pytest -x tests/multilanguage/test_scalar_c_abi.py -m integration`; the source and historical compatibility nodes remain separate evidence.
- Follow existing prerequisite selection. Inspect collected nodes and owner labels; do not infer that a missing reference/native compiler ran.

The C++ upstream-conformance adapter is a pending bounded selection, not the full requested suite. Its adapter contracts passed 2 real object cases and 64 fixture cases; all 11 original upstream translation units failed with explicit unsupported diagnostics. Eleven Clang references were not selected because the prerequisite was absent. These are visible capability gaps, not a passing conformance suite. Original Clang compile-only/diagnostic translation units must remain separate from LLVM SingleSource runtime tests, GCC execution tests, standard-library suites and real projects. A reference rejection is not automatically a matching PCC diagnostic.

Historical manual CI entry: `.github/workflows/cpp17-validation.yml`; it accepts an exact published `expected_sha` and verifies actual Linux x86-64/macOS arm64 identity. It does not run on every push. Its ordinary gate is a named subset, not all C++ tests. Platform CI results must be read for the exact run/SHA; no new macOS CI result is asserted here. Separately, the user reported Mac host tests and bounded Go, Java compatibility and C++ native examples passing on an earlier source snapshot; these are maintainer-supplied evidence, not executions performed by this cloud worker or qualifications of later changes.

## Known open boundaries and takeover checklist

1. Read `AGENTS.md`, this page and the roadmap. Verify remote master and the actual working tree before editing. A local candidate is not a published feature.
2. Assign one remote publisher and non-overlapping path owners. Preserve unpublished candidates; do not reset shared trees or blindly rerun immutable bootstrap jobs.
3. Pick the first unresolved dependency of the next real project. Identify whether it belongs in the frontend, shared IR/backend, runtime, build system or test adapter.
4. Record acceptance before coding: original input/license/hash; expected diagnostic or exact output; execution owner; target/collector; subsystem tests; reference version. Preserve initial failures when fixing them.
5. Complete source review and final-candidate gates. Separate passed/failed/not-selected/skipped/unrun and host/IR/object/native/pcc1 evidence. Re-test changed code paths, not unrelated immutable workloads merely because master advanced.
6. Publish real code promptly with accurate scope/limitations; update this page and the roadmap's next dependency. Do not use commits or raw test counts as a language-completion percentage.
7. Respect the shared runtime/backend/codegen batch hold and required Mac performance review. CPU sample proportions alone do not prove increased call counts or lock causes.
8. Native pcc1 availability/bootstrap, owned NumPy import, five-GC extension end-to-end and real-project language coverage remain open. Record current failures as issues rather than quietly excluding them.

No raw logs, temporary recovery locations or credentials belong in this page. Source/test commands and public commit/issue links should be enough to orient a new contributor; actual current execution is required to renew qualification.
