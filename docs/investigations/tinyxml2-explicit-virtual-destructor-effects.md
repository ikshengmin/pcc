# TinyXML2 explicit destruction: effect graph and next slice

The unchanged Linux host pcc0 attempt at `a04b1db` still stops at
`tinyxml2.cpp:1199:9` with `cpp.unsupported.placement-constructor-effects`.
The source is the original TinyXML2 11.0.0 fixture, not a modified copy.
The translation-unit SHA-256 is
`95f64e4cb06c9e147d5b12e8860c88898944858dfbc38fed92ebf918f1436554`.

## Observed edges

- `XMLNode::~XMLNode` (811) calls `DeleteChildren` and conditionally
  `_parent->Unlink(this)`. Both direct nonvirtual member bodies appear
  later (878 and 888). `Unlink` only adjusts tree pointers outside its
  assertions; it does not call a destructor.
- `DeleteChildren` loops on the first child and calls `DeleteChild` (912).
  That unlinks it, then calls `DeleteNode` (1188). `DeleteNode` can call
  virtual `ToDocument`, `XMLDocument::MarkInUse` (2211), and the explicit
  virtual destructor (1199) before virtual `MemPool::Free` (1200).
  Destruction can therefore re-enter the same graph through a child;
  absence of a finite call tree does not itself imply unwinding.
- The final overrider is not fixed by the static `XMLNode*`. In the
  fixture's header it has `XMLText`, `XMLComment`, `XMLDeclaration`,
  `XMLUnknown`, `XMLElement`, and `XMLDocument` subclasses. Element
  destruction can call `DeleteAttribute` (2018), which destroys the
  attribute and calls its pool's virtual `Free`. Document destruction
  calls `Clear` (2224), which invokes the child/unlinked-node paths and
  releases the character buffer. Generated `StrPair` destruction is
  another edge, not a reason to assume the full graph closed.
- The existing direct D1 effect gate uses already-bound bodies and exact
  generated call identities. A class method declaration or the static
  destructor symbol cannot establish the effects of a late body or every
  dynamic final overrider. Preserve that distinction for callers outside
  this project as well.

## Reproduced boundary

The [focused fixture](../../tests/cpp/test_cpp_virtual_explicit_destruction_effects.py)
still has successful pcc0/g++ native controls for closed virtual dispatch
and bound TU-local leaf calls. A new independent witness uses a nonvirtual
`O::cleanup` declared in the class and defined after its destructor; g++
executes it and returns 42, while pcc0 emits the same effect diagnostic.
It does not require a recursive or virtual helper. Five focused guarded
integration nodes passed; an expected rejection is not a PCC execution.

## Implementation order

First make the exact late, direct nonvirtual member edge carry a pending
obligation to its same-TU definition. Resolve before object emission;
missing definitions and unknown, escaping or indirect effects remain
rejected. Do not qualify the upstream destructor from the leaf witness.
Then model recursive effects by finite semantic graph components rather
than accepting a cycle or any unbound symbol. Add virtual final-overrider
evidence only when the complete accepted dynamic domain is proved at its
owning boundary. Do not rewrite `node->~XMLNode()` as a qualified base
call, assume every open virtual call is local, or disable the effect gate.


## Late direct member leaf

The focused leaf case now explicitly destroys the object before the
member definition appears in source order. Linux pcc0 checks the exact
same-class nonvirtual selected member at TU end; the program then returns
42 with the same streams as g++ 14.2. Absent members and helpers whose
bound body calls an unresolved function still fail before object emission.
This deliberately does not certify generated automatic cleanup, recursive
members or open virtual final overriders. Five integration witnesses and
153 adjacent tests passed. Unchanged TinyXML2 still stops at `1199:9`.


## Closed direct recursive component

A same-class direct nonvirtual call cycle is now checked as an identity
graph at TU end. A repeated body avoids repeated work, not proof
obligations: every reachable selected member must have a bound body whose
calls are all represented. Both self-recursive and mutually recursive
native controls match g++ 14.2. A missing member or external outgoing
call still rejects. Seven guarded integration nodes and 155 adjacent
checks passed. This is not a termination proof. The unchanged TinyXML2
still has an open-virtual effect diagnostic at `1199:9`.


## Cross-TU dynamic target boundary

The call-site TU now has an executable control with no declaration of
`Child`: it compiles `destroy(Base*)` and the base destructor.
Another TU constructs `Child` and invokes `destroy`; the overrider in
that other TU contributes 4 before the base contributes 2. Both owned
Linux pcc0 and g++ 14.2 produce exit 42, stdout `2a000000` (hex), empty
stderr. Eight guarded integration nodes passed. Checking only the
classes visible to the current parser demonstrably misses live targets.

Maintainer choice: (a) a project-only, source-bound dynamic effect domain
checked over the exact linked inputs, with unknown targets failing
closed; or (b) a general C++ exception and ABI owner before broadening.
The agent recommends (a) for the original fixture, not for library or
ordinary separately compiled ABI acceptance. Neither is implemented by
this audit; do not change general-mode semantics or drop the current gate.


## Exception-specification route (2026-11-25)

The earlier effect-domain recommendation above is superseded by
[#14](https://github.com/ikshengmin/pcc/issues/14), which rejects a
project-only exception domain and approves the language rule. The relevant
source rule is N4659 [except.spec]/8 (implicit member/base destructor
specifications), /5 (non-throwing virtual overrides), and /4 (matching
declarations). The new source does not infer the specification from a
bound body or from TinyXML2's currently linked overrides.

Linux host pcc0, CPython 3.15.0, G++ 14.2.0; starting master
`46a048a2` plus the destructor exception-specification patch:

- `tests/cpp/test_cpp_destructor_exception_specifications.py` and
  `tests/cpp/test_cpp_virtual_explicit_destruction_effects.py` jointly:
  69 passed. The second TU can define the selected virtual overrider,
  late helper or recursive component's outgoing call. The exact
  cross-TU virtual D1 witness returns 42 on both owned ELF and G++,
  stdout `2a000000` (hex), empty stderr. The G++ reference explicitly
  force-includes its genuine `<new>`; TU bytes are identical, not the
  header environments. A potentially-throwing D1 still fails on unknown
  effects, and a looser override is diagnosed in the declaring TU.
- Entire C++ ordinary suite:
  `uv run python -m pytest -q -n0 --override-ini=addopts= tests/cpp -m 'not integration'`:
  6009 passed, 49 subtests passed, 47.97 s, guarded process-tree peak
  RSS 197169152 bytes (1 GiB cap).
- Related C++ destruction/placement corpus: 511 passed, one unchanged
  external G++ rejection
  `outer-alias-despite-different-class-alias`. Its original source and
  external oracle assertion remain unmodified; not a green integration
  suite. The feature ledger structure and its 129 unit tests pass; the
  pre-existing diagnostic inventory audit has exactly the same unmapped
  codes as the base tree (no new unsupported code).
- Original project test:
  `uv run python -m pytest -q -n0 --override-ini=addopts= tests/cpp/test_cpp_projects.py::test_cpp_tinyxml2_upstream_tests_explicit_host_pcc_project -m integration`.
  Actual host command used `-m pcc --backend self --separate-tus --no-cache
  -O0 --cpp-arg=-std=c++17 --cpp-arg=-D_FILE_OFFSET_BITS=64` on the
  unchanged upstream project. It advances to
  `tinyxml2.cpp:2310:1: cpp.unsupported.function-storage: function storage
  specifiers require function linkage semantics` on
  `static FILE* callfopen(const char*, const char*)`. The TU hash is still
  `95f64e4cb06c9e147d5b12e8860c88898944858dfbc38fed92ebf918f1436554`.
  No executable or upstream test result.
- Runtime termination is not exercised: the frontend explicitly rejects
  throw/catch, including throw in a non-throwing destructor. The measured
  owned static ELF witness has no exception/unwind sections, but that is
  not a tested exception search or termination result. C++ specifies
  termination when an exception attempts to leave a non-throwing
  function ([except.terminate]). Keep the actual throw/terminate test
  unrun until the frontend owns such an exception path; no general
  external EH interoperability, especially other platform unwind ABIs,
  follows from this normal-exit evidence.

Next: generic file-scope static function linkage at `2310:1`; constructor
and deallocation exception specifications and native pcc1 parser execution
remain independent obligations. Keep the existing potentially-throwing
effect graph where it is needed.


## Next independent namespace-linkage slice (2026-11-25)

Starting master `775384f6` plus the ordinary static-function patch,
the source owner now resolves linkage from the parsed declaration
specifiers and canonical overload, rather than the previous literal
`static inline` prefix. Names inside namespaces retain the Itanium
local-source-name marker. No external ODR publication is added to
internal inline functions; the existing non-template out-of-class
inline and external-inline boundaries remain, as does the existing
member-template ODR owner.

Linux host pcc0, CPython 3.15.0, G++ 14.2.0: 6033 ordinary C++ tests
and 49 subtests pass (47.14 s; process-tree peak 197144576 bytes, under
90 s / 1 GiB). Eleven `test_cpp_file_static_functions.py` integration
originals match the independently compiled G++ outputs; same-name
functions in different TUs have distinct local ELF symbols. An unresolved
used static declaration does not link to another TU's definition.

The unchanged TinyXML2 11.0.0 host project test advances beyond
`2310:1` (`static FILE* callfopen`) and reaches
`tinyxml2.cpp:2580:1: cpp.unsupported.destructor-subobjects: unknown
construction effects require partial member exception cleanup` in
`XMLPrinter::XMLPrinter`, which constructs `_stack` and `_buffer`
DynArray subobjects and calls `_buffer.Push(0)`. TU SHA256 is unchanged
(`95f64e4cb06c9e147d5b12e8860c88898944858dfbc38fed92ebf918f1436554`).
Reproduce with the exact host project test above, not a modified fixture.
This is not a new passed upstream executable. The next blocker requires
the generic constructor/partial construction exception owner; a
user-provided constructor has no implicit non-throwing guarantee.
