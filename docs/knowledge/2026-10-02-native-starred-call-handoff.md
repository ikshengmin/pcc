# Native starred-call lowering handoff (2026-10-02)

## Confirmed failure and execution owner

A coherent clean Stage1 built from `source-v4-critical-bytes-provider` emitted
434 objects and linked `stage1-critical-clean-v2/compiler/pcc1`, but compiling
its original smoke program failed with `NameError: name '*' is not defined`.
The smoke itself contains no unpacking:

```python
def main() -> int:
    return 0

main()
```

Artifact identities:

- Frozen codegen checksum: `d74a00290b66ea82890da779eefcfffbd0f540f5cc9f53382dd4cd01b94bf78a`
- Native compiler SHA256: `9d4204515eeaa3e9a96f7d59278244cc45e9e93a25bd4f3d3bd780ad0f3ad1c8`
- Matched runtime SHA256: `e1b85fdf48e43fef3e51dbe99328b4e7a89f8f3e28ceb178db8078d30670f92c`

Disabling direct indexed emission moved the same failure from the frontend
worker to the separate self-backend emission worker. Frontend IR generation
therefore completed in that diagnostic. ELF object validation calls
`precise_stackmap.decode_stack_map`, whose `StackMapLocation(*entry)` reaches
a constructor shortcut that emits the unpack marker as an ordinary name.
The retained ELF relocation points directly from
`user_pcc_backend_precise_stackmap_decode_stack_map` to the offending literal.
This is a compiler-language lowering defect, not a `*` in the smoke AST.

## Generic repair family

All 434 retained objects were searched, and relocations identify eight
functions across these five modules:

1. `precise_stackmap`: `StackMapLocation(*entry)`
2. `macho_link`: `DataInCodeRegion(*region)`
3. `llvm_explicit`: `set().union(*(names for ...))`
4. `collections`: `defaultdict.__init__` calls `super().__init__(*args, **kwargs)`;
   this object also contains an emitted `**` NameError
5. `datetime`: four classmethods call `cls(*parts)`

Small frozen-source host-pcc compilations, linked to the explicit matched
runtime and then executed natively, reproduced the distinct failures:

- Two required dataclass fields: expected `12 34`, observed `None None`
- Defaulted stackmap-shaped dataclass: expected `1 3 8 6 -1 -24 8`, observed
  `None None 8 0 -1 0 8`
- Data-region-shaped dataclass: expected `4 8 2`, observed `None None 1`
- Set union with a generator splat: native `NameError: name '*' is not defined`
- Dict-subclass super initialization: native `NameError: name '*' is not defined`
- Defaulted classmethod construction: native `NameError: name '*' is not defined`
- Required-only classmethod construction: incorrect compile-time missing-argument
  diagnostic before execution

Each compile had a 150-second/2-GiB process-tree guard. Native executions had
20-second/2-GiB guards, an empty PATH, and denied host Python/PCC. No runtime
was provisioned, and no frozen source root was edited.

## Implemented compiler slice

`call_expression_lowering.py` now routes expanded known-class calls through
owned native runtime argument binding instead of field/direct-init shortcuts.
It also binds the actual `cls` receiver in classmethods, preserving the
possibility of subclass construction. The ordered keyword stream is rebuilt
from `Call.operand_order` so interleaved mappings and explicit keywords are not
silently reordered. Existing hoisted class captures are still attached.

`call_object_lowering.py::_emit_dynamic_call_kwargs_object` now retains scoped
roots for mapping sources and accumulated keyword dictionaries, retires
owned temporary mappings, and cleans earlier operands if a later evaluation
or merge fails. Its signature is unchanged. Explicit keyword runs keep their
Python evaluation grouping.

These are compiler changes. They do not establish that all native runtime
constructor, descriptor, or concurrent-GC semantics are correct.

## Focused evidence and pending native gate

The exact fixture-free nodes were run using the qualified Python directly,
with `-x -n0 -vv --tb=short`, a 150-second/2-GiB guard, and native provisioning
disabled:

- `test_constructor_unpack_binding.py::test_constructor_unpack_reference`
- `test_constructor_unpack_binding.py::test_constructor_unpack_owned_ir`

All 22 parameter cases passed. The IR cases additionally reached owned x86
ELF emission. Pre/post source identities were stable:

- `call_expression_lowering.py`: `d85a4e21c99b4369d825294b993a9c2eb990d4bcf6a8a8e8185b3cc37221fdc5`
- `call_object_lowering.py`: `93817d89d127e63ad60175693597119e9002aec0136135a4065e0249b71d62bf`
- Test file: `23be35c69a981ebdc002c9d8846e514c8678feee9af07ff2f525c9552938bca9`

Native cases are prepared in `test_constructor_unpack_native`, including
all five GC selections. At 2026-10-02 07:40:20 UTC the integration coordinator
reported the **dataclass constructor case passed native GC0–4 on frozen v6**,
with integration `addopts` cleared so the test actually ran. The same report
confirmed int-text native GC0–4, a separate repair family. The dataclass result
is native evidence for that case only; it does not qualify the other ten
constructor cases, original native-pcc1 smoke, or a self-hosted fixed point.
No live changed compiler was tested with an old runtime. The
`classmethod_meta` case additionally requires the pending runtime metaclass
implementation. Exact native run receipts remain with the integration
coordinator; this handoff preserves that reported result without inventing a
receipt path.

The completed slice was captured in `source-v6-diagnostic-integrated`, source
hash `315691568586607d0205382b1d71b92b3bd7766b67afa05a5abfe5b1d82e7cd4`.
Its matching runtime was built centrally and used for the reported native
dataclass gate above. It excludes the unqualified metaclass prototype described
below. There is no new Stage1 or fixed-point qualification claim in this handoff.

## Unfinished runtime and entry boundary

The live tree contains an unqualified metaclass prototype in `py_class.py`,
`py_obj_ops_dispatch.py`, `runtime_abi.py`, and `py_runtime.h`. It is excluded
from the coherent frozen slice. Do not interpret its presence as proof.

The shared special-call design must start from existing authoritative caller
root slots, reload the receiver under the graph lease, resolve/classify the
method under the same lease, retain managed methods before unlock, and keep
raw native addresses exclusively in untraced scratch. Creating a root around
a previously borrowed raw receiver inside the callee does not close the
pre-publication gap under concurrent promotion.

A separate handled output is needed to distinguish absence from failed
lookup/callback when TLS already holds an exception. The intended shared
slot-based interface remains subject to coordinated caller-ABI integration.
Current `py_protocol_runtime` formals are raw values, so this cannot be fixed
solely by changing its final unary-call helper.

Metaclass binding must cover ordinary functions, static/class methods,
custom descriptors, inherited and differing subclass metaclasses, default
`type` fallback, and callback exceptions. It must never silently fall back
when an invoked callback returns NULL without an exception.

Remaining non-constructor owners also need real implementations:

- `set_lowering.py` treats one splat marker as one ordinary argument. The
  runtime has per-operation set helpers but no equivalent expanded callable
  method binder. Merely rejecting the fastpath into its existing generic
  fallback does not implement the missing semantics.
- The foreign-base `super().__init__` fallback applies exception-args storage
  even to dict subclasses. Expanding the marker alone still fails to
  initialize the dict. `py_protocol_runtime.py` owns the dict-subclass backing
  store and `py_dict_update` supports mapping/iterable updates; an appropriate
  initializer should use those owners rather than change callers.

## Evidence location

Under `/workspace/scratch/ce254c0a0910/pcc-cloud-runs/native-smoke-star-diagnostic/`:

- `native-smoke-identity.json`: exact compiler/runtime identity and phase split
- `star-symbol-inventory.txt`: all five object owners and referencing symbols
- `repair-family.json`: reduced sources, hashes, observations and receipts
- `focused-ir-tests-result.json` and `focused-ir-tests.log`: 22-case receipt
- `constructor-keywords-green/manifest.json`: immutable completed-slice snapshot
- `constructor-keywords-green/constructor-keywords.patch`: separable compiler/tests patch
- `constructor-keywords-green/exclude-metaclass.patch`: exclusion of only the
  unqualified metaclass additions; applied only to the new frozen copy

The separable compiler patch reverse-checked against the live completed
changes; the exclusion patch applied cleanly in check mode before the frozen
copy was made. All original callers were preserved. No commits were made.

## Quiesced handoff

At 07:47 UTC implementation/testing was quiesced for coordinator takeover.
The constructor/kwargs snapshots remain immutable. The live four-file
metaclass prototype is still unqualified and excluded from frozen v6. No
slot-based public binder ABI or set/dict-super repair has been landed.

## Subsequent live integration, 13:01 UTC

The preceding quiescence records the 07:47 source, not the current live tree.
The live follow-on now implements authoritative owning/borrowed root copying
with counted address leases, prepare-under-lock/finish-after-outermost-unlock,
and slot-to-slot result transfer. `py_obj_special_call_slots` and
`py_obj_call_slots` receive caller-owned input/output root addresses; the
sync wrapper preserves deferred-call context. Lookup retains managed methods
under the graph transaction and keeps native code addresses untraced.
Set expanded calls, dict/exception foreign-super initialization, and a bounded
index slot-entry migration have separate coordinated implementations.
The always-reserved instance owner is traced/copied/cleared independently of
public slots-only `__dict__` access. These are model/owned-IR-qualified slices;
their matched archive, threaded native and fixed-point gates remain pending.

The retained ordinary-integer constructor reduction
`Row(*(value,), **{"other": -(1 << 100)})` exposed a further operand-producer
gap. Literal-derived integer trees now publish every result into an existing
empty root before error checks or cleanup. An `int` annotation alone does not
authorize primitive operator dispatch. Ordinary boxed-int parameter roots
were repaired by the integer-ABI owner, rather than manufacturing new roots
around borrowed raw copies inside the consumer.

Exact Boolean leaves inside admitted arithmetic trees become integer 0/1.
Bare Boolean arguments remain Boolean. Boolean-only bitwise trees and direct
`~bool` remain explicit producer boundaries: the qualified CPython 3.15 oracle
confirms that direct bool inversion emits a DeprecationWarning. Prepared native
controls assert exact result types as well as values. A separate runtime owner
is repairing positive arbitrary-precision shift counts; this producer does
not claim to close the existing `py_int_shl`/`py_int_shr` count limit.

The strict raw-lane control also exposed boxed routing for explicit
`pcc.i64`/`pcc.u64`. Seven bounded predicate corrections preserve their machine
arithmetic, signedness and literal/target coercion while ordinary Python ints
retain exact kernels. Raw wrap/shift/high-bit native controls are prepared.

Latest executed evidence under `../pcc-cloud-runs/slot-root-handoff/`:

- `numeric-machine-v6.json`: exact compiler/test/dependency hashes and limits
- `numeric-producer-all20-v6-result.json`: 20 passed, including the unchanged
  constructor reduction, parameter-root replay, result publication and bool
  operand proof; `machine-five-ir-v6-result.json`: five strict IR controls passed
- `bool-producer-red-v1-result.json`: preserved bool-boxing failure, followed
  by `bool-producer-red-replay-v2-result.json`; all final source manifests match
- `literal-integer-slot-producer-v6.patch`: incremental patch over the earlier
  qualified call-object source `0b03d6cddcfa8d766ebbf01121d3f2d0f8c1d6336315b6c5195d363e5ee4a3f3`
- `explicit-machine-arithmetic-v5.patch`: separate four-file correction;
  all incremental patches reverse-check against live source without writes

Current call-object source is
`a9c97c58fbe6ead13df688278bd04f3f5e175b69c0ae8a8192be01c7c363ce13`.
The runtime entry and return-publication probes are prepared and owned-IR
verified but unexecuted natively. `status.json`, the raw-call inventories and
`unary-entry-owner-audit.md` retain public raw compatibility, remaining unary
entries and legacy constructor temporary-owner gaps. The immediate
owned-return-to-registered-slot bridge has a source-bound static review;
that review does not qualify legacy constructor internals or concurrent native
execution. No original failing caller was rewritten, and no commit was made.
