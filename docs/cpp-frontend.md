# Owned C++ frontend

The target is complete C++17 and its required standard-library/runtime surface,
with NumPy 2.4.4 as the first real integration corpus. A parser is only the first
step: semantic analysis, object lifetime, ABI lowering and execution must be
implemented and independently tested. The target chain must not invoke an
external C/C++ compiler, Clang/LLVM, host Python or libpython.

## Experimental entry

`pcc.frontends.cpp.CppParser().parse(source, filename)` is an experimental API.
It is **not** the public C++ CLI, a complete C++ compiler, or a NumPy build path.
The public extension/language routing will be integrated separately. Unsupported
C++ must never be handed to the C parser as a fallback.
Cross-target callers must give `CppParser(target_triple=...)` the same target as
the C generator's pass context; do not infer the target's scalar model from the
Python host.

The first implementation is independent recursive descent with an operator
precedence expression parser. It reuses PCC's native C lexer for the accepted
token spellings, but does not use the C LALR parser. Identifiers are buffered
without typedef classification; `Scope` resolves their type/value role when
consumed. `T(a);` is recognized tentatively as a declaration only when lookup
finds a type; speculation restores the token cursor and makes no declarations.
A local value hides an outer type, including in function parameter scopes.

Named declarators now share a recursive syntax component. The public frontend
admits redundant grouping around scalar, function, parameter and typedef names,
without changing lookup or overload identity. The richer syntax model retains
pointer/reference/array/function chunks, per-level cv qualifiers, qualified-name
segments and callback payloads. Those are syntax records, **not** implemented
pointer/reference/lifetime semantics: the public `parse_plain_name` capability
gate still rejects them before C AST output. Six grouped-name programs, including
a two-translation-unit case, have host-owned emitted execution and separate
G++17 byte-oracle tests; this is not a native C++ driver qualification.

Currently accepted semantics are deliberately narrow: arithmetic free functions,
arithmetic aliases, prototypes, single-object block declarations, nested lexical
blocks, arithmetic, direct calls and returns. Types keep canonical C++ identity
and rank, even when `long` and `long long` have the same storage width. Target
width/signedness and ordinary integer-literal candidates reuse the owned C ABI.
By-value arithmetic overloads use per-argument standard-conversion ranks, not a
sum of costs; unresolved or ambiguous calls fail before lowering. Selected calls,
initializers, assignments, returns and arithmetic operands carry explicit C AST
conversions. Pointer/reference, user-defined and full template overload rules remain
unimplemented, not silently approximated by this arithmetic slice.

The function-template increment admits deduced, by-value arithmetic type
parameters. A template retains `TypeParameter` identities, a structured `Pattern`
body, and definition-point name/candidate bindings; it is not source text to
reparse or a C AST with `T` replaced by `int`. Only a selected, successfully
substituted instance is checked through the same conversion/operator/call Sema
and lowered into existing C nodes. Repeated calls reuse one instance. A selected
body error is an error, not permission to fall back to a worse overload.
Non-template functions win equal conversion sequences; more general template
partial ordering and ADL-only dependent calls still fail explicitly.

The emitted fundamental template signatures use Itanium parameter identities
and substitutions, including the dependent return type. Weak/COMDAT ODR
publication is **not** implemented: two translation units instantiating the
same function currently fail at the owned linker's duplicate strong symbol
check. The corresponding G++17 reference succeeds; a reference-only pass is not
owned multi-TU template support. No candidate is silently deleted and no
specialization is silently made TU-local to conceal that missing ABI behavior.

Extended `long double` value/call ABI lowering is still missing on targets whose
format exceeds binary64. An executed x86-64 test retaining a `long double`
overload failed in the owned backend (x86_fp80 storage exists, argument ABI does
not). The frontend now diagnoses this at the source instead of deleting that
candidate or treating it as `double`. A negative test is not long-double support.

Fundamental free-function
names use their Itanium encoding; this is not an implementation of all Itanium
ABI rules or MSVC ABI. `main` has its C++ fallthrough-zero and no-use rules.

Every accepted name is resolved before constructing existing `c_ast` nodes.
The lowering test feeds those nodes to the actual `CCodeGenerator` and owned
`pcc.ir.ir.Module`. This preserves the C frontend's ABI, arithmetic and backend
implementation rather than introducing a second code generator. No source text
is stripped or emitted as Python/C to simulate missing language semantics.

## Remaining language and library requirements

The experimental entry fails explicitly at unimplemented constructs. In
particular, none of these are implemented merely by accepting their syntax:

| Area | Required work before admission |
| --- | --- |
| Types and names | Remaining fundamental types/literals, extended long-double values/call ABI, cv qualifiers, pointers/references, recursive declarators, namespaces, qualified/dependent lookup, using declarations, ADL and access checks |
| Expressions | Value categories, conversions and sequencing, full Pratt/operator coverage, casts, constant evaluation and constexpr |
| Functions | Remaining overload conversions and tie-breaks, defaults, operators, function/member pointers, linkage and complete target ABI |
| Templates | Remaining dependent types/lookup, ADL, explicit/default arguments, parameter packs, NTTPs, template-template parameters, SFINAE, partial ordering, class/variable templates, specialization and complete ODR/COMDAT publication |
| Objects | Layout, initialization, constructors, copy/move, temporaries, destructors, reverse-order scope cleanup, inheritance, virtual dispatch and RTTI |
| Exceptions | Throw/catch matching, unwind cleanup, rethrow and noexcept; never erase handlers or destructors |
| Statements | Remaining control flow, lambdas, captures, structured bindings and statement attributes |
| Input | C++ preprocessing modes, all literal/token forms, headers, diagnostics and language/extension CLI routing |
| Standard library | Owned C++17 headers and runtime semantics, including containers, algorithms, type traits, allocation, strings, threading and exceptions; a NumPy-sized header slice is not the complete library |

Rich C++ nodes must retain semantic identity until these decisions are resolved.
References, `this`, template instances, virtual dispatch and cleanup then lower
to the existing C AST/IR/backend. They must not be implemented by deleting
tokens or borrowing Python garbage collection semantics.

## Evidence and reference discipline

`tests/cpp/test_cpp_frontend.py`, `test_cpp_overload.py` and
`test_cpp_templates.py` are host-development parser/Sema/C-lowering tests.
They exercise actual selected calls, instantiated bodies and conversion
instructions in the existing C generator, not only declarations. Six template
programs separately passed host-owned ELF execution (twice each) and an explicit
G++17 byte/status/stderr oracle, including definition-point overload lookup and
post-instantiation arithmetic promotion. These do not prove the public pcc1 C++
driver or complete template semantics.

The first parser at `287bc4a` was also compiled and executed with the restored
Linux x86-64 Stage1 `pcc1` from `bf8c03e`. The bounded run used that compiler's
real matching runtime archive/source provenance, an empty target `PATH`, no
libpython, and owned object/link settings. Compilation took 208.9 seconds with
925 MB peak tree RSS; the emitted static ELF printed exactly
`cpp-scope-ok\n_Z3addii\n`. The essential commands, with `PCC1` and `OUT` pointing
to that compiler and an isolated output directory, are:

```sh
"$PCC1" tests/cpp/native_parser_probe.py -o "$OUT/parser-probe" \
  --backend self --python-libpython off
"$OUT/parser-probe"
```

Set `PCC_RUNTIME_ARCHIVE`, `PCC_RUNTIME_DIR`, `PCC_SOURCE_ROOT` and `PCC_REPO_ROOT`
to the matching **compiler** artifacts/source, not a fabricated runtime stamp or
the changed program's source identity. Keep the original compile/run/tree-RSS
bounds (300 seconds / 30 seconds / 4 GiB). This proves native execution of that
new parser closure, not that the old compiler contains the new C++ frontend.

`tests/cpp/native_overload_probe.py` is a separate changed-shape gate for the new
overload selection, conversion ASTs and ambiguity diagnostic. This increment's
probe also passed with that bf8 Stage1/compiler-runtime pair and the same owned
settings/bounds: 259.9 seconds compilation, 929 MB peak tree RSS, then an executed
ELF producing exactly `cpp-overload-ok\n_Z1fi\n_Z1fd\n_Z1fx\nambiguous-rejected\n`.
It checks integral and floating promotions, distinct long-long identity,
target-aware arithmetic types, and rejection of incomparable overloads. Use the
same commands above with `native_overload_probe.py` for this changed-shape gate.

Running either probe under CPython does not qualify a native path. Neither probe
compiles the parsed C++ program, and neither is a pcc1→pcc2→pcc3 fixed point.
Separately, five arithmetic-overload programs were compiled by the host frontend
through the owned C generator/object emitter/ELF linker, executed twice each,
and matched a separately invoked G++17 oracle in output bytes, status and stderr.
That is host-compiler/owned-target execution, not a pcc1 C++ compilation claim.
Complete C++17, the standard library and the owned NumPy build remain unqualified.

The first combined `native_template_probe.py` attempt, with one frontend
worker, reached its 300-second compile deadline before producing an ELF.
That failed attempt remains distinct: no template native execution occurred in
it, and generated object files do not change the result.

A separately authorized **two-frontend-worker** configuration then compiled the
reviewed template/declarator closure in 255.2 seconds with 1.60 GB peak tree RSS.
Its static ELF ran in 0.204 seconds and produced the exact expected selected
`tag(long)`, distinct `twice<int>`/`twice<double>`, selected-body-error and
declarator-shape observations. The same bf8 Stage1 compiler/runtime, original
300-second compile / 30-second run / 4-GiB bounds, no-cache settings and owned
chain were retained; only `PCC_PY_FRONTEND_JOBS=2` was selected, while
`PCC_SELF_BACKEND_JOBS=1` prevented nested parallelism. Source identities were
stable during this run. The reviewed source differs from the failed attempt
only in a parser docstring and import wrapping, so this is a scheduling
configuration validation, not an algorithm improvement or strict same-source
speed comparison. It qualifies native execution of this parser/model closure,
not native compilation of the parsed C++ TU, the public CLI or a fixed point.

Both attempts used the strengthened process-name watchdog from `c62f3f36`.
Its sampling checks are an ownership guard, not an exhaustive audit of every
possible exec event. No wall-clock/RSS bound was widened. Later void/control-flow
increments require their own changed-shape native validation.

External C++ compilers may be explicit, separate reference oracles. Acceptance
compares outputs as bytes, exit status and diagnostics, then grows through real
translation units. Successful parsing, emitted Python passing `compile()`, or
declaring a template without executing overload/instantiation semantics does
not establish C++17 conformance or NumPy support.

For development-only corpus reconnaissance, the optional external LibClang
inventory is separate from the owned chain:

```sh
uv run python scripts/cpp_feature_inventory.py --libclang /path/to/libclang.so \
  --source /path/to/unit.cpp --output /tmp/features.json -- -x c++ -std=c++17
```

Pass the real include/generated-header options after `--`. The first NumPy 2.4.4
source-level superset inventory yielded 25 partial units because generated
headers were unavailable. A later explicitly external Linux development
configuration generated the real headers and selected 131 objects for
`_multiarray_umath` (110 C and 21 C++). Adding the production FFT/linalg inputs
gave 23 C++ units; all 23 then parsed with LibClang19. This configuration-specific
observation is not the entire NumPy build or evidence of PCC semantic support.
AST feature counts remain observations, not instantiated-template counts or a
complete requirement list. `parsed`, `partial` and `unavailable` stay distinct;
the inventory/oracle dependencies do not enter the owned target chain.

Reference designs consulted (no third-party source copied in this increment):

- [Clang internals](https://clang.llvm.org/docs/InternalsManual.html),
  [tentative parsing](https://github.com/llvm/llvm-project/blob/main/clang/lib/Parse/ParseTentative.cpp)
  and [Sema lookup](https://github.com/llvm/llvm-project/blob/main/clang/lib/Sema/SemaLookup.cpp):
  keep parsing/name lookup coordinated and semantic lowering separate. Any
  later source port needs pinned provenance, notices and Apache-2.0 WITH
  LLVM-exception license compliance, not a runtime dependency on Clang.
- [Clang 20.1.3 overload comparison](https://github.com/llvm/llvm-project/blob/923a5c4f83d2b3675bb88e9fe441daeaa4d69488/clang/lib/Sema/SemaOverload.cpp#L10532-L10578)
  and [best viable function validation](https://github.com/llvm/llvm-project/blob/923a5c4f83d2b3675bb88e9fe441daeaa4d69488/clang/lib/Sema/SemaOverload.cpp#L10898-L10947),
  alongside its [overload-call regressions](https://github.com/llvm/llvm-project/blob/923a5c4f83d2b3675bb88e9fe441daeaa4d69488/clang/test/SemaCXX/overload-call.cpp),
  are references for candidate viability, per-argument ordering and ambiguity.
  This implementation and its regressions are independently authored; it does
  not port the remainder of Clang's conversion/template machinery by implication.
- [Clang type-parameter deduction](https://github.com/llvm/llvm-project/blob/923a5c4f83d2b3675bb88e9fe441daeaa4d69488/clang/lib/Sema/SemaTemplateDeduction.cpp#L1639-L1744)
  and [function-body instantiation](https://github.com/llvm/llvm-project/blob/923a5c4f83d2b3675bb88e9fe441daeaa4d69488/clang/lib/Sema/SemaTemplateInstantiateDecl.cpp#L5279-L5336)
  distinguish consistent type substitution from body checking. The present
  by-value scalar pattern/instance implementation is not a port of their full
  dependent-language machinery. [Itanium template parameter mangling](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangle.template-param)
  is a separate ABI requirement; scalar storage equality does not erase template
  parameter identity in a specialization's name.
- TagStack's TAG grammar is a syntax checklist; its expression traversal,
  cleanup model and native differential-test structure are design references.
  Its permissive legacy Python text transformations are not C++ semantics.
  PCC already owns target-aware scalar layout/conversion rules; do not copy a
  fixed LP64 type table over them.
- [cxxheaderparser declarators](https://github.com/robotpy/cxxheaderparser/blob/f2759fb6bd8723d70f7fdd99c6d145350f302f45/cxxheaderparser/parser.py#L2763-L2909)
  and bounded streams are useful references, but declaration extraction drops
  function bodies and is not semantic analysis. Source licensing and test
  provenance differ; regressions here are independently authored.
