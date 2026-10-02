# Explicit raw-pointer ownership handoff (2026-10-02)

## Candidate and claim level

The candidate is held in the current shared worktree. Exact source/evidence
hashes are in `../pcc-cloud-runs/raw-pointer-ownership/source-manifest.json`.
This is host-pcc, owned-frontend, no-libpython semantic/IR evidence. No native
execution, runtime archive construction, object emission or fixed-point claim
is made for this candidate.

## Contract

`RawPointerType` is a compiler-unmanaged C ABI **view**. It is not proof that
an allocation is foreign: runtime-port C APIs deliberately expose manually
managed PyObject addresses through `c_ptr`. The marker is canonicalized only
from verified `pcc.extern.c_ptr` / `c_rawptr` import bindings, including aliases;
ordinary user classes with those names remain managed. Native and host parsers
preserve qualified annotation identity.

Normal application unsafe-address inference remains its existing integer
projection. Runtime-port/freestanding unsafe pointer results retain their
existing unknown-pointer inference. In particular, `load_ptr` and
`global_load_ptr` can load managed objects and are not globally relabeled raw.
An initial attempt to globally classify pointer intrinsics produced 90 runtime
IR failures and was corrected; the retained earlier receipts record that
rejected approach, not the final implementation.

Explicit annotation flow propagates raw views through parameters, helper
calls, locals and returns. Unmanaged pointers are not automatically retained,
rooted, resolved as owned objects or pinned as direct-call arguments.

Dyn-to-explicit-raw call/local boundaries require unsafe-expression or local
assignment/alias provenance. Mixed stores, deletions, loop bindings, lexical
shadowing and unknown ordinary parameters fail closed. Known managed storage,
container boxing, class fields and module-object publication reject raw views.
Dynamic Python function adapters for raw signatures fail explicitly.

Runtime-port/freestanding explicit raw return contracts select a manual pointer
view. Manual pointer equality uses address comparison; Dyn/raw pointer joins
remain unmanaged in those explicit modes. Ordinary managed joins/comparisons
remain strict. A legacy unannotated runtime C-export raw return is distinguished
from explicit `-> object` by `FuncDef.has_return_annotation`.

Call-only Raw-to-legacy-Dyn pointer transfer requires a resolved callee whose
actual declaration does not automatically root that parameter. The
`manual_pointer_abi` defining-module metadata alone is insufficient: the
codegen registry checks its effective declaration policy. Imported entries
additionally require a verified C-export symbol matching the declared symbol;
typed overrides must match the supported declaration projection. A private
runtime-port function that still roots its Dyn parameter remains rejected.
No new GC suppression was introduced.

## Evidence

- `held-focused.log`: 70 exact fixture-free checks passed in 2.30 seconds,
  including both parsers, aliases/shadowing, wire/export/static mirrors,
  source-provenance negatives, five manual-call boundary shapes, and existing
  annotation and typed-ABI controls. Exact IDs: `final-node-ids.txt`.
- `runtime-inventory-manual-abi/`: all 182 production Linux runtime modules
  emitted IR in one bounded process, 39.954 seconds, 91,533,312-byte peak tree
  RSS. The pass had no compiler or runtime-source hash drift. It used the
  production module inventory and frontend IR compile helper, without object
  emission or archive construction.
- `gui-receipt.json` and `held-gui-ir.log`: the actual CoreGraphics module's
  three private helpers and six exported functions contain no pointer-GC
  operations on both x86-64 Linux and arm64 Darwin IR. Safepoints and unrelated
  managed module-initialization/adapter bookkeeping are not foreign-pointer
  ownership operations.
- The freestanding memory/string old/new IR signatures match, including
  `memcpy(ptr, ptr, i64) -> ptr`; separate legacy and current receipts are kept.

## Integration drift after the inventory

The inventory itself had stable inputs. Before sealing the handback, shared
updates changed `builtin_type_attr_lowering.py`, `call_expression_lowering.py`,
`exception_lowering.py`, `port_abi_exports.py`, and `string_method_lowering.py`.
`handback.json` records this difference. Replay the 182-module inventory on the
final integrated freeze; do not substitute the earlier receipt for that source.

## Required next gates

1. Freeze the integrated source and build its matching runtime without bypassing
   provisioning or provenance guards.
2. Execute
   `tests/python/test_raw_pointer_ownership.py::test_opaque_raw_pointer_executes_on_all_gc_backends`
   with `-x -n0 -vv --tb=short`. It checks opaque addresses 1 **and 4096** through
   raw helper/local/return flow plus managed/string and scalar controls on GC
   backends 0–4. Expected stdout: `1 4096 1 2.25\n`.
3. Build a fresh stage1 and run the new-source pcc1 → pcc2 → pcc3 gate. The new
   type and appended FuncDef fields are an AST layout change; retained old
   native compiler/runtime artifacts are diagnostic references, not qualification.

The compiler pointer annotations do not establish foreign resource lifetime
policies such as CGContext release; those remain the relevant library's job.
