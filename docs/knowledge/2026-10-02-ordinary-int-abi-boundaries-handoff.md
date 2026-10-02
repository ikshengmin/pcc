# Ordinary Python integer ABI and tuple boundaries, 2026-10-02

## Scope and source identity

The unchanged native Linux packed-stackmap corpus failed before its builder
ran: `PlannedSafepoint(safepoint_id=18446744073709551615, ...)` was narrowed
through a signed-i64 constructor ABI. A separate diagnostic exposed tuple
iteration/unpacking narrowing. The corpus imports `pcc.unsafe`, which selected
the module-wide raw-int scaffold policy even for ordinary Python annotations.

Base: `pcc-cloud-runs/source-v13-packed-cell-astra` (immutable, never edited).
Focused candidate: `pcc-cloud-runs/ordinary-int-boundaries-candidate-v5/source`.
The candidate contains eight source changes relative to that base. Its
`call_expression_lowering.py` contains only the coordinated missing-type ABI
fallback hunk, not the separate unqualified slot-call tranche in the live tree.

Exact hashes and base/candidate/live distinctions:
`pcc-cloud-runs/ordinary-int-boundaries-candidate-v5/change-manifest.json`.
Patch: `ordinary-int-boundaries-candidate-v5/integer-boundaries.patch`, SHA256
`d847a07e53f3dd94ec1e3fddc3925b4f2d3a54d6b9daa1bbdcb0a0be7d7da603`.

## Generic changes

- Ordinary-int constructor, function and method signatures use the existing
  boxed/finite-range-proof policy regardless of module name or unsafe imports
- Declaration, body, exported method/function descriptors, bounded-proof
  export metadata and the direct-call missing-type fallback agree
- Explicit `pcc.i64`/`pcc.u64`, explicit C exports, valueclass payloads and
  manual runtime-port/freestanding machine contracts stay distinct
- Tuple unpacking does not unbox an ordinary-int element before a planned
  object store; unproven tuple/list loop targets use object storage
- Already-proven bounded list loops retain their native lane
- Existing conversion overflow/range checks and original packed-builder
  assertions are unchanged

The direct-call fallback fails explicitly if imported authoritative parameter
metadata is missing. No caller-module ABI guess is permitted on that path.

## Executed evidence

Only `/workspace/scratch/ce254c0a0910/pcc-qualified-venv/bin/python` was used;
native provisioning was disabled. All pytest invocations used `-x -n0 -vv
--tb=short`, with the repository process-tree supervisor, shared performance
lock, 120-second watchdog and 1 GiB tree-RSS cap. Exact commands are in each
`result.json`.

- `ordinary-int-boundaries-red`: two CPython references passed; live slot
  code rejected `Row(*(value,), **{"other": -(1 << 100)})` with an authoritative
  UnaryOp ownership error. This independent red is preserved for its owner
- `ordinary-int-boundaries-frozen-red`: three references passed, then the
  constructor IR test proved `Box.__init__(ptr self, i64 value)` on frozen13
- `ordinary-int-boundaries-candidate-v5`: 13 passed, three native cases
  deselected; 3.554841 seconds wall, 98,062,336 bytes peak tree RSS
- Coverage includes ordinary dataclass/plain constructors, keyword/starred
  bindings, large integer function/method arguments and returns, tuple
  destructuring/iteration, explicit machine/valueclass controls, runtime-port
  private helpers, freestanding rejection of private unexported functions,
  export wire roundtrip, and bounded-list ABI/export agreement with and
  without an unsafe import

Intermediate reds remain in candidate, candidate-v2 and candidate-v3. They
caught an empty-string versus None predicate mistake, an invalid freestanding
private-helper control, and an overbroad new IR assertion respectively. The
last now permits only the checked literal shift-count 100 conversion (tagged
201); tuple-value and native runtime assertions were not relaxed.

## Required next gates

No native execution or bootstrap qualification is claimed for this patch.
Rebuild a matched runtime from the final selected source; do not bypass source
provenance with runtime-v13.

1. `tests/python/test_ordinary_int_object_boundaries.py` native cases
   `constructors`, `unpack`, and `parameters`, pcc0 compiler selection, each
   executing GC0 through GC4
2. The original unchanged
   `tests/python/test_x86_64_packed_stackmaps_native.py::test_production_packed_builder_executes_with_native_arenas_under_all_collectors[pcc0]`
3. Relevant existing typed-int, annotation, C-ABI, class/import and native
   bootstrap gates before claiming compiler qualification

The central coordinator owns runtime rebuilding and the heavy execution slot.

## Matched v14 execution and narrow follow-through

The coordinator's matched v14 runtime built successfully. The three native
ordinary-int cases (`constructors`, `unpack`, `parameters`) passed GC0–4 in
`native-v14-integer-packed-astra`. The unchanged full-uint64 packed builder
then reached `_check_uint(record.safepoint_id, 64, ..., nonzero=True)` and
raised signed-i64 overflow while producing its argument, before validator
entry. This later red is preserved independently of the constructor repair.

`unary_call_lowering._emit_arg_for_abi_param` selected the object's projection
only for expressions accepted by the local exactness predicate. That omitted
ordinary typed field/subscript reads. The existing exact-object producer now
gets first refusal for ordinary-int expressions when the physical formal is
an object pointer; existing machine conversion checks remain intact. A
valueclass field explicitly declines that producer before evaluating its
receiver, retaining its payload lane and single evaluation.

The same boxed ABI exposed missing borrowed formal roots at the three
function/static-method/instance-method binding sites: semantic IntType had
been excluded even when the formal's physical representation was a pointer.
Admission now recognizes precisely ordinary `int` plus pointer IR, under the
existing auto-root suppression conditions. Reassigned parameters use the
selected function/method ABI in the existing forced-exact-local planner, so
their incoming value receives an independent owner, owning map and flag.
This is an after-entry/in-body lifetime correction, not a general threaded
raw-formal entry or prologue-movement proof.

Narrow immutable candidate:
`pcc-cloud-runs/boxed-projections-roots-candidate/source`, based on v14 with
four changed source files. `change-manifest.json` records every hash. Patch
`boxed-projections-roots.patch` SHA256:
`efd121493b5ecb43d7b4d8613efb39a9c022dde2bbee545a69ed0d2a388cc218`.

Executed fixture-free gate: 22 passed, eight native cases deselected,
4.823192 seconds wall, 101,720,064 bytes peak tree RSS. Required-red receipts
are `boxed-argument-v14-red` (field getter narrowed before pointer-ABI call)
and `boxed-parameter-roots-v14-red` (no formal root before body collection).
New native controls cover field/subscript/addition/negation arguments and
forced collection after entry for function/static/instance parameters plus
reassignment. Their execution remains pending a matching source/runtime.

The unchanged production `record_id = record.safepoint_id; ... record_id -=
2**64` path is already protected by its augmented-assignment exact-local seed.
Actual emitted builder IR proves a pointer slot, direct field-object transfer
through `pcc_gc_store_root_take`, exact `py_int_sub`, and a pointer `append4`
argument. Evidence: `packed-builder-v14-projection-ir/verified-flow-receipt.json`
and `builder.ll`; original source SHA256
`04749a7dc14764163d268ceb152e9a077c8572dcda3d62609a060b3bce3fb66a`.
That check was IR-only (3.309860 seconds, 123,723,776 bytes peak), not another
native result.

## Independent unresolved ordinary-int admission gaps

Preserved in `tests/python/test_ordinary_int_unproven_boundaries.py`, without
changing the green narrow candidate:

- An ordinary field assigned to a local and then used in non-augmented
  arithmetic still takes an i64 slot in a no-int-signature scaffold function.
  The exactness planner does not seed a typed Attr read without another seed
- Provider-local bounded-list calls do not account for a consumer module
  calling that same exported function with `2**80`. The current export remains
  raw. `compute_bounded_int_abi_function_names` is module-local, not a
  whole-closure range proof; updating its export metadata did not supply that
  missing external-call proof

Both focused negative tests ran and failed as required. Receipts are under
`unproven-int-boundaries/<test-name>/result.json`. They must not be presented
as fixed or silently absorbed into the protected production AugAssign case.

Packaging supersession: all native functions in the two focused test files
are explicitly marked `pytest.mark.integration`; default broad suites must not
provision native fixtures implicitly. Assertions and mandatory native gate
scope are unchanged. Use `boxed-projections-roots.integration.patch`, SHA256
`6efd20eb4429041d917cd99a614b900778aaf7d5612475d27b321fa373f7e5c0`, and
`change-manifest.integration.json` in the same candidate directory. The four
source hashes are unchanged from the 22-pass receipt; only test classification
was added afterward.

## Matched v15 native qualification

`native-v15-projection-status-astra/result.json` confirms return code zero,
38.292900 seconds supervised wall time (37.03 seconds pytest), 440,238,080
bytes peak tree RSS. All ten selected native tests passed GC0–4: four argument
projections, after-entry function/static/instance/rebound parameter survival,
three original ordinary-int cases, the related subprocess control, and the
unchanged full-uint64 production packed-stackmap builder. That is 50 native
executions. The original full-uint64 red is therefore closed for this bounded
tranche; threaded raw-entry/prologue behavior is still a separate proof.

## Field-local and cross-module proof repair candidate

The two independent remaining negative controls now pass in a separate
v15-based candidate. Ordinary typed Attr reads seed exact local storage;
explicit machine annotations and valueclass payload fields retain scalar
storage. Multi-module compilation conservatively declines the existing
module-local finite-int ABI proof. Export workers receive that global closure
fact even when their assigned shard contains only one module. The resulting
`box_int_abi` travels through existing export wire, and defining codegen
workers receive an authoritative boxed-function map; they cannot independently
reselect raw based on only their local literal callers. A library entry also
declines the local-only proof. Truly closed, proven single-module examples
retain the original fast path.

Use `int-proof-closure-isolated/int-proof-closure.isolated.patch`, SHA256
`5bac70e669a3df44aa36910529e0e07d6cb6ed2a164c9411cb6da4a9000f3ba4`, and its
`change-manifest.json`. Every Python source was compared with immutable v15:
exactly nine intended source files differ. `host_contract.py` adds only
`_native_boxed_int_functions`. The preceding `int-proof-closure-candidate`
package accidentally copied unrelated live slot-call metadata into that file;
it is superseded, and its evidence must not qualify the isolated source.

Corrected isolated gate: 19 passed, five native cases deselected; 4.323408
seconds wall, 108,539,904 bytes peak. Tests execute the real export-worker entry
for an eight-module shard plus a singleton provider tail, merge and index the
native wire, and execute provider/caller codegen workers. Export metadata,
provider definition, caller declaration and caller invocation all carry the
pointer ABI. Library IR includes object list loads and `py_int_add`, and
excludes `py_list_get_i64_nonnegative`; this checks loop body representation as
well as the signature. Separate controls retain explicit machine/valueclass
field-local scalar storage.

Required matched-runtime native gates for this newer candidate:
- `test_ordinary_int_unproven_boundaries.py::test_cross_module_large_integer_native`
- `test_int_proof_boundary_controls.py::test_ordinary_field_local_native[pcc0]`

These are integration-marked and retain GC0–4, unsigned-over-i64 and negative
bigint assertions. Native qualification for this newer candidate is pending;
the v15 native verdict does not certify the subsequent nine-file patch.

## Corrected isolated follow-through package

The final isolated follow-through package is
`int-proof-closure-isolated/int-proof-closure.isolated.patch` (SHA256
`5bac70e669a3df44aa36910529e0e07d6cb6ed2a164c9411cb6da4a9000f3ba4`). The
current live `host_contract.py` still legitimately contains the separate slot
worker's changes; do not copy that entire live file into this patch. The
qualified candidate reconstructs it from v15 plus the one new boxed-export
map field. Both newly fixed negative shapes remain natively unqualified until
the coordinator runs the two prepared integration nodes with a matching
runtime. All source and focused-gate hashes are in the isolated manifest.

## Matched v16 follow-through qualification

The coherent v16 source identity is
`1e3796f454abc5daf2c7ef2e96869ff0d7ff9c3f14e44ca72d54838c83e3417b`.
With its matched runtime, the coordinator's 13-control native gate passed
46.96 seconds pytest, covering 65 GC0–4 executions. This includes the
cross-module huge-list sum and both positive and negative ordinary field
locals, together with the prior integer controls and unchanged production
packed builder. The two prepared follow-through native nodes are therefore
qualified on that exact coherent source/runtime. Full Stage1 and subsequent
self-host-chain qualification remain owned by the coordinator; these bounded
native results are not a fixed-point or threaded raw-entry proof.
