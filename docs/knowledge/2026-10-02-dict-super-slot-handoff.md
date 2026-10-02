# Dict-base super initialization: slot boundary handoff (2026-10-02)

## Status and execution owner

The generic `super().__init__(*args, **kwargs)` repair is implemented in the
live dirty tree and has **host CPython reference, production-body model, and
owned-IR evidence only**. No native object, matching runtime archive, emitted
program, native-pcc1 smoke, or fixed-point gate was executed for this slice.
The original `pcc/stdlib/collections.py::defaultdict.__init__` is unchanged.
The integration coordinator owns any subsequent coherent source freeze and
runtime/native gate; do not combine this compiler with an older runtime.

See [the original starred-call handoff](2026-10-02-native-starred-call-handoff.md)
for the actual native compiler `NameError: '*'` evidence and the other repair
families. Its old quiescence status predates this implementation.

## Changed owners

- `method_call_expression_lowering.py` now classifies the terminal builtin
  base rather than treating every foreign initializer as exception-args
  storage. Dict and exception initialization use separate four-root status
  entries. Explicit `super(From, receiver)` evaluates the actual From operand
  before the receiver and validates the binding before initializer operands.
  Unknown foreign initializers and mixed builtin-base MROs fail explicitly.
- `py_protocol_runtime.py` implements `py_builtin_super_validate_slots`,
  `py_dict_subclass_init_slots`, and `py_exception_subclass_init_slots`.
  Inputs are preexisting authoritative caller-owned slots. Scratch roots are
  registered empty and independently leased. The instance's reserved env
  slot and its existing hidden dictionary backing remain the storage owners.
  Both publication layers preserve any owner created by an allocation
  callback. Reinitialization updates existing contents; it does not clear
  them. Errors survive cleanup and preexisting exceptions survive success.
- `py_dict.py` owns reusable `py_dict_set_slots`, `py_dict_setdefault_slots`,
  and `py_dict_update_slots`. Collision candidates are copied from owning
  entry slots under the lookup graph transaction and leased across equality
  callbacks. Replacement revalidates table and candidate identity. Finishes
  and finalizers occur after the outer graph unlock. Exact-dict updates use
  cached hashes; mapping updates perform the ordinary observable keys lookup
  twice and materialize non-list keys results before the first getitem.
  Pair conversion retains the complete materialized sequence, including
  invalid trailing values, until conversion finishes. Exhausted entry storage
  reports failed growth rather than retrying forever. The old raw update
  entry remains an explicitly unqualified admission adapter to this owner.
- `freestanding_gc_object_slots.py` now includes the always-reserved hidden
  owning slot in both whole and bounded visitors. The payload relocation
  extent check in `freestanding_gc_relocation_payload.py` requires this slot
  for instances and valueboxes regardless of public `__slots__` visibility.
- Coordinated `py_class.py` changes separate physical reserved-owner
  addressing from public dynamic-attribute visibility, clear that owner on
  teardown, copy it in both replace variants, and clamp negative field counts
  before reserving the extra slot. The shared ABI/header declarations and
  both new L1 method contract/static-export entries were added by their owner.

The reserved-slot change was necessary: the allocator already reserved and
zeroed `n_fields + 1` slots, but the visitors, deallocator and copy helpers
skipped the final owner for slots-only classes. `py_valuebox_new` delegates to
`py_instance_new`. Public `__dict__` visibility was not widened.

## Evidence and exact selected gates

Evidence directory:
`/workspace/scratch/ce254c0a0910/pcc-cloud-runs/dict-super-static/`

All successful commands used
`/workspace/scratch/ce254c0a0910/pcc-qualified-venv/bin/python` (3.15.0rc1),
`env -u LC_ALL`, bytecode writes disabled, `PCC_TEST_NO_NATIVE_PROVISIONING=1`,
`PCC_NO_AUTO_PCC1=1`, and `UV_NO_SYNC=1`. This direct interpreter selection was
explicitly required by the coordinator; no environment sync occurred.
`run_gates.py` uses the existing `scripts/platform_process_watchdog.py` to
supervise only its own process tree. Final stages each had a 120-second,
1-GiB cap and no performance lock. Overlap with a central Stage1 was authorized
for **correctness only**, so elapsed times are not performance evidence.

1. `model --no-performance-lock`: 52 cases passed in the named-node log.
   - `tests/python/test_dict_super_slot_roots.py`: 28 actual-body model cases
   - `tests/python/test_reserved_instance_slots.py`: 24 whole/bounded visitor
     and relocation-extent cases
   - `model-receipt.json`, `model-pytest.log`, before/after manifests
2. `binding --no-performance-lock`: 23 cases passed.
   - `test_dict_super_reference`: 11 CPython cases
   - `test_dict_super_owned_ir`: 10 non-defaultdict owned-IR cases
   - unknown-foreign-base diagnostic and native-export registry checks
   - `binding-receipt.json`, `binding-pytest.log`, before/after manifests
3. `runtime --no-performance-lock`: exactly `py_dict`, `py_protocol_runtime`,
   `freestanding_gc_object_slots`, and `freestanding_gc_relocation_payload`
   compiled to owned IR and passed `tests.owned_ir_validation.verify_ir_text`.
   - `runtime-receipt.json`, four named runtime logs, four `.ll` artifacts

Every final stage's whole-Python-source manifest was unchanged before/after.
The first mapping oracle assertion was intentionally preserved as
`gate-a-red-v1.log`: it expected one property access. The bounded qualified
CPython oracle recorded `MAPPING_EVENTS [1, 1, 2, 3, 3]` in
`mapping-oracle.log` / `mapping-oracle-receipt.json`, after which both code and
expectation were corrected. CPython's matching source is
[dict_update_arg](https://github.com/python/cpython/blob/v3.15.0rc1/Objects/dictobject.c#L3710-L3722)
and
[method_output_as_list](https://github.com/python/cpython/blob/v3.15.0rc1/Objects/abstract.c#L2274-L2312).
An earlier uv cache setup failure ran no tests and is separately preserved.

The coordinated py_class owner also reported its own 38-case hidden-owner
model and verified py_class runtime IR, separately recorded in
`pcc-cloud-runs/slot-root-handoff/instance-reserved-owner-v1-result.json` and
`instance-reserved-owner-v1-stdout.log`. Those 38 cases are not part of this
52-case result.

## Frozen identities at the successful light gates

| File | SHA256 |
| --- | --- |
| method_call_expression_lowering.py | c89d15de17447558d86814b146ac4c6576fb191c3977155de36e0267ca53449c |
| py_dict.py | 387b45628b0fb098676cf8cdb8cf9d1a3ebdde4e530b9091d2431e8f2bede5b4 |
| py_protocol_runtime.py | d802cdb4a0e5e01cec4f83208730f56cbff01bbde46d62bba24673c9ae73591c |
| freestanding_gc_object_slots.py | f3b9c822a82214d9700683bc8a7e1e27e9042fcabfc7868ec0b35e91c750c226 |
| freestanding_gc_relocation_payload.py | 8d94b9fdfffb528e6707e2e7b0bdb4ab6d1f04fbd5866f83238ecbbb5656c78a |
| py_class.py (coordinated) | 3a90cd7dc87574b1cf1b66c9b1e8730158db9fab7eabfdc8d4c5c33978b11e37 |
| runtime_abi.py (coordinated) | 85fa9ec5d80f45fdd46df0a8681abfa622af7d5173bf4a4047791b194519b533 |
| py_runtime.h (coordinated) | 89c478a9c665c4fb94a2f14bb06e318f374eb9a0e6920fdc1b66cc6714e1f538 |
| _l1_codegen_static_methods.py (coordinated) | 97f835b968bd39d45dd2b5308b396070f42dc005e25580d5580eb1f36b113913 |

Full compiler manifests, including slot producer and shared root machinery,
are in the receipt directory. These listed hashes are navigation, not a
standalone dependency closure. Prior unrelated dirty edits were preserved;
no commit, push, deployment or user-computer access was performed.

## Next gate and remaining boundaries

The prepared native entry is
`tests/python/test_dict_super_init_binding.py::test_dict_super_native`.
It has eleven programs and runs each emitted binary under GC0–4 with an empty
PATH and denied host Python/PCC. Begin with its `defaultdict` parameter on a
new coherent matching runtime, then the dict/exception, callbacks, operand
order, partial update, explicit-super, pair-owner and slotted-owner controls.
The test does not provision a runtime independently of coordinator approval.
After these focused native gates, replay the original native compiler smoke;
a new Stage1 and full self-host chain remain separate qualification.

Explicit remaining boundaries:

- Mixed builtin-base MRO initialization requires real builtin-base descriptor
  representation/lookup; this patch diagnoses that gap rather than choosing a
  later user method or inventing exception behavior
- Slots-only exception argument **storage lifetime** is covered by the owner
  repair, but `.args` visibility still lacks a dedicated native exception
  getter. General dynamic-attribute visibility was deliberately not widened
- Existing raw getter, iterator, equality, constructor/default-call and raw
  replacement entry internals are not fully migrated by leasing these outer
  operands. This slice does not certify their concurrent-GC entry handoffs
- Whole-source exact-dict snapshots predate this patch and can differ from
  CPython when destination equality callbacks mutate later source values;
  that old semantic gap remains open
- No all-GC native execution, original pcc1 smoke, no-libpython full self-host
  qualification or fixed-point claim is established by these light gates
