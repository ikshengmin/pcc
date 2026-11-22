# GC3 remembered barrier: ownerless publication coverage

Reviewed for [GC3 #8](https://github.com/ikshengmin/pcc/issues/8#issuecomment-7075091903).
This is a source audit, not a runtime bug report: no nursery is enabled.
A raw initialization is not necessarily a root, and a name ending in
`_gc0` is not a collector dispatch restriction.

| Writer family | Destination and publication | Activation witness still required |
| --- | --- | --- |
| `pcc_gc_store_ptr` nonmoving branch | Receives the managed owner but hands only slot, value and backend to `_store_root_nonmoving_tracing`. Its active-major and legacy object-index gates must not gate the minor insertion barrier. Actual root-store siblings deliberately have no heap owner. | Store into an old reachable container with major idle; remove the young value's independent roots; verify two minors, with no unrelated clean-card visit. Include a real frame-root store as a distinct control. |
| `py_gen_frame_set` raw list-backed frame | Direct list items store under frame/container lock; the value-only shade has no frame or item-array owner. The inline path is separate. | Force raw frame selection and verify two minors, slot overwrite and card retirement. |
| `pcc_gc_try_init_ptr_locked`: list append | `_append_existing_capacity_gc0` and `_append_grow_gc0` allow nonmoving count-free mode. The destination is off-object; the list length makes the slot visible after the store. Growth can replace the backing, so deriving the owner from its raw address is not safe. `_list_get_gc0` instead writes a temporary result. | Old list, both spare-capacity and replacement-array paths; retain through two minors, overwrite/clear and observe retirement. |
| `pcc_gc_try_init_ptr_locked`: dictionary insertion | `_dict_gc0_admitted_locked` can admit GC3. `_dict_set_gc0_locked` fills the off-object entry's key and value before publishing them through count/index fields; partial initialization can roll back. `_dict_get_gc0_locked` instead copies into a result slot. | Isolated young key and value under old dict; check insertion and failed second slot, then replacement and removal. Verify no card leak from rolled-back publication. |
| Private sequence materialization and sort | `_dict_gc0_materialize` builds tuples and a list, including a separate raw list-pointer store for items. `_sorted_gc0` fills an off-object result array. Both admit nonmoving count-free mode. Private backing is not an old edge until its owner is old and published; late publication or old/black allocation during major GC needs an explicit owner boundary. | Force old/black construction or promotion only once that policy is chosen. Verify tuple/list child graphs and no intermediate publication on refusal. |
| Persistent C roots / memoryview export | C-owner shade in `pcc_gc_cpy_handle_change_c_owners` accompanies persistent root/pin ownership. A GC3 address lease alone is a no-op; the memoryview export uses a separate registered root. These are root/handoff boundaries, not dirty old-card stores. | Hold exports and C handles across two minors, then release them and prove reclamation. If excluded from nursery, prove promotion before raw address exposure. |

Many other value-only notifications cover temporary roots or completed native
results, not owning heap writes. Do not turn every such notification into a card;
audit the actual destination and root lifetime. The table does not close the
alternate raw C, inline/off-object tables, detach or bulk-writer inventory.

The focused source test runs the dictionary admission helper and initializer
from the real runtime source with GC3 flags and a simulated old dictionary entry.
It observes the destination store and a value-only shade. An independent graph
oracle sees the young object, whereas the hypothetical sparse minor without the
owner barrier does not. It does not run a dictionary insertion in a native
nursery or demonstrate a failure of today's collector.

## Collector-entry root baseline

`freestanding_gc_conservative_roots._capture_thread_words` refreshes the
stopped owner before enumerating the snapshot.
`freestanding_thread_kernel_pthread._thread_snapshot_roots` captures
current registers and copies from the SP of that capture, not from a saved
collection-entry boundary. The test runs both existing capture/refresh bodies
with supplied machine-state fixtures. A fake object word below the entry SP
and a current collector register enter the result; a caller stack word is
retained. The independent entry oracle also includes a saved caller-register
word, illustrating why just trimming the stack is insufficient. This is not
a native register probe or a claim that the modeled register was lost in a
specific production collection.

Before activating a GC3 minor, qualify entry capture at the public and
automatic collection boundaries, preserving the outermost capture through
nested entries. Keep existing other-owner polling snapshots unchanged.
Check collector temporaries that really must become roots through existing
explicit slots/leases, including callbacks and weakref/finalizer queues.
The native gate needs both a stale collector-only candidate that dies and
independent caller-stack, callee-saved-register and exact-slot controls
that survive, with a supported stack ABI. An integration pass on only one
host does not qualify Darwin.

The targeted new baseline and two zero-forwarding groups pass (51 cases).
The wider existing forwarding fixture was not green: on the unchanged
source/test preimage, GC3 case `[order0-0-3]` again refused forwarding
installation before reaching the scan. This is not a result of the new
tests. No native collector or performance gate was run in this round.

The Linux x86-64 live-register test now links an ELF using PCC-owned objects
and executes the same capture intrinsic as the runtime. Six caller
callee-saved register sentinels must appear in the captured registers or the
copied stack (where the compiler may save them). Both register-allocation
settings passed, checking the SP and buffer canaries. Four emitted-object
targets and the host-only intrinsic guard also passed. No production
collection, callback-acquired root, or macOS execution was tested here.

Include a positive root first acquired by a callback or reentrant
collection; it cannot be inferred from the outermost saved SP. In the later
scaling gate, report old index visits and payload-span enumeration
separately from stack words and clean-card visits.
