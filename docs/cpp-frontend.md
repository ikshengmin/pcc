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

Currently accepted semantics are deliberately narrow: arithmetic free functions,
arithmetic aliases, prototypes, single-object block declarations, nested lexical
blocks, arithmetic, direct calls and returns. Types keep canonical C++ identity
and rank, even when `long` and `long long` have the same storage width. Target
width/signedness and ordinary integer-literal candidates reuse the owned C ABI.
By-value arithmetic overloads use per-argument standard-conversion ranks, not a
sum of costs; unresolved or ambiguous calls fail before lowering. Selected calls,
initializers, assignments, returns and arithmetic operands carry explicit C AST
conversions. Pointer/reference, user-defined and template overload rules remain
unimplemented, not silently approximated by this arithmetic slice.

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
| Templates | Dependent AST, two-phase lookup, deduction/substitution, specialization and instantiation; tests must execute selected specializations |
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

`tests/cpp/test_cpp_frontend.py` and `test_cpp_overload.py` are host-development
parser/Sema/C-lowering tests. The latter exercises actual selected calls and
conversion instructions in the existing C generator, not only declarations.

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

Pass the real include/generated-header options after `--`. The NumPy 2.4.4
source-level superset inventory currently yielded 25 partial translation units
and zero complete units because required generated headers were unavailable.
Its AST counts are lower bounds, not a complete feature list, semantic support
or an owned build result. `parsed`, `partial` and `unavailable` stay distinct.

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
- TagStack's TAG grammar is a syntax checklist; its expression traversal,
  cleanup model and native differential-test structure are design references.
  Its permissive legacy Python text transformations are not C++ semantics.
  PCC already owns target-aware scalar layout/conversion rules; do not copy a
  fixed LP64 type table over them.
- [cxxheaderparser declarators](https://github.com/robotpy/cxxheaderparser/blob/f2759fb6bd8723d70f7fdd99c6d145350f302f45/cxxheaderparser/parser.py#L2763-L2909)
  and bounded streams are useful references, but declaration extraction drops
  function bodies and is not semantic analysis. Source licensing and test
  provenance differ; regressions here are independently authored.
