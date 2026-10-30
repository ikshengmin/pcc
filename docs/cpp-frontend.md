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

The first implementation is independent recursive descent with an operator
precedence expression parser. It reuses PCC's native C lexer for the accepted
token spellings, but does not use the C LALR parser. Identifiers are buffered
without typedef classification; `Scope` resolves their type/value role when
consumed. `T(a);` is recognized tentatively as a declaration only when lookup
finds a type; speculation restores the token cursor and makes no declarations.
A local value hides an outer type, including in function parameter scopes.

Currently accepted semantics are deliberately narrow: `int` free functions,
int aliases, prototypes, single-object block declarations, nested lexical
blocks, int arithmetic, direct calls and returns. Fundamental free-function
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
| Types and names | Remaining fundamental types, cv qualifiers, pointers/references, recursive declarators, namespaces, qualified/dependent lookup, using declarations, ADL and access checks |
| Expressions | Value categories, conversions and sequencing, full Pratt/operator coverage, casts, constant evaluation and constexpr |
| Functions | Overload candidate construction/ranking, defaults, operators, function/member pointers, linkage and complete target ABI |
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

`tests/cpp/test_cpp_frontend.py` labels host-development parser/Sema/C-lowering
tests. This first increment is host-development validated only: native parser,
complete C++17, standard-library and NumPy qualification are all pending.
`tests/cpp/native_parser_probe.py` must be compiled with an actual pcc1
and the emitted binary executed before it provides any native evidence. Merely
running that Python file under CPython does not qualify the native path. A
stage1 compiler or parser-probe binary is not a pcc1→pcc2→pcc3 fixed point.

External C++ compilers may be explicit, separate reference oracles. Acceptance
compares outputs as bytes, exit status and diagnostics, then grows through real
translation units. Successful parsing, emitted Python passing `compile()`, or
declaring a template without executing overload/instantiation semantics does
not establish C++17 conformance or NumPy support.

Reference designs consulted (no third-party source copied in this increment):

- [Clang internals](https://clang.llvm.org/docs/InternalsManual.html),
  [tentative parsing](https://github.com/llvm/llvm-project/blob/main/clang/lib/Parse/ParseTentative.cpp)
  and [Sema lookup](https://github.com/llvm/llvm-project/blob/main/clang/lib/Sema/SemaLookup.cpp):
  keep parsing/name lookup coordinated and semantic lowering separate. Any
  later source port needs pinned provenance, notices and Apache-2.0 WITH
  LLVM-exception license compliance, not a runtime dependency on Clang.
- TagStack's TAG grammar is a syntax checklist; its expression traversal,
  cleanup model and native differential-test structure are design references.
  Its permissive legacy Python text transformations are not C++ semantics.
  PCC already owns target-aware scalar layout/conversion rules; do not copy a
  fixed LP64 type table over them.
- [cxxheaderparser declarators](https://github.com/robotpy/cxxheaderparser/blob/f2759fb6bd8723d70f7fdd99c6d145350f302f45/cxxheaderparser/parser.py#L2763-L2909)
  and bounded streams are useful references, but declaration extraction drops
  function bodies and is not semantic analysis. Source licensing and test
  provenance differ; regressions here are independently authored.
