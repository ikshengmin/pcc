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
