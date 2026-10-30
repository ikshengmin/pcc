# Shared pinned-root return cleanup

## Observed Stage1 shape

Retained macOS Stage1 admission receipts for
`pcc.frontends.python.codegen.call_expression_lowering` record:

| Source / run | Total instructions | Largest function | Call records |
| --- | ---: | ---: | ---: |
| `265d7610` / `39639727188` | 175,271 | 116,078 | 105,315 |
| `9a6f308f` / `40196904770` | 288,224 | 222,511 | 177,925 |

The 47-field backend shape gives call records as `call_scalars / 8`.
Module peak memory rose from 379,797,504 to 1,559,674,880 bytes. The
largest-function statistic has no function name. These receipts contain no
callee histogram, so they do not assign all 72,610 additional calls to one
emitter. The earlier runtime was `threads:false`; host compilation already
used multiple workers.

`a7ffc4eb` / `40183670553` and `9a6f308f` have identical complete backend
shape vectors for this module. Their separate Stage1 time difference is not
evidence of additional IR between those two revisions.

## Exposed repetition

The threaded path in `literal_lowering._enter_container_temp_root` allocates
an entry-block slot, registers its frame for the function lifetime, and adds
its fresh name to `_pinned_gc_rooted_local_names`. Normal temporary cleanup
clears and unpins its value but retains the registration. Other consumers,
including applicable dict operations and `isinstance`, use this same root
entry API. The enter/leave implementation is unchanged between the two
historical sources; enabling threaded runtime behavior activates this path.

`return_lowering._return_through_shared_exit` stores the result first, then
calls `ownership_lowering._emit_owned_local_cleanup`. Previously even shared
returns repeated each eligible pinned slot's `load`, `pcc_gc_unpin`, and
`pcc_gc_store_root(slot, NULL)`; only frame leaves were shared. This is three
instructions and two calls with three total arguments per slot per return,
without corresponding conditional blocks. It is a concrete source of IR
duplication, not a measured attribution of the entire historical increase.

## Exact suffix sharing

Only ordinary shared-return paths use the new chain. Each return still:

1. Stores/roots its result and releases its own valueclass/owned locals,
   retaining its original `skip_name`, flags, environment, and release order.
2. Selects the original reverse-order pinned slot sequence, including aliases
   under distinct names; selection is recomputed for every return.
3. Enters a shared suffix of nodes that each reload, unpin, clear, and branch.
   Nodes are keyed by exact slot and successor identities, strongly held by
   the cache. No loaded pointer or return-site SSA value is cached.
4. Reaches the existing shared frame-leave block and result handoff.

Entry allocas dominate the new nodes. Rooted/unrooted exits have separate
targets. Later roots cannot change an earlier return's cleanup sequence.
Nodes are published only after completion, and builder block/position are
restored. Only the original leave block receives late frame-leave patches.
Nonshared, generator, async, and owned-through-finally paths retain their
existing cleanup route. Thread, five-GC, memory-admission, and timeout policy
are unchanged.

For selected sequences `S_i`, inline cleanup has `2 * sum(len(S_i))` static
calls; the chain has two calls per distinct `(slot, successor)` node. Every
executed return path retains its exact original call sequence; the change
adds only unconditional branches between the shared nodes.

## Host validation

The focused test extracts the actual emission methods into a recording CFG,
compares each return's execution trace with the original inline route, and
checks eligibility, aliases, frame-leave patching, identity lifetime, builder
restoration, and static signature-table parity. It imports no compiler or
runtime and executes no generated/native program.

Focused-contract reproduction in a checkout (not a full-repository fixture run):

```sh
PCC_NO_AUTO_PCC1=1 PCC_PYTEST_AUTO_CLEAN=0 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -x -vv --tb=short --noconftest -p xdist.plugin \
  tests/python/test_shared_pinned_return_cleanup.py
```

The recorded qualification instead used an isolated source directory,
explicit `--rootdir` and `-c` pointing to an unchanged copy of the project
pytest configuration, and the existing CPython 3.15.0rc1 / pytest 9.1.1 /
xdist 3.8.0 environment; it did not use the repository's Python 3.15.0 pin.
Both commands retain the project six-worker/loadgroup defaults and exclude
all conftest scopes deliberately for this host-only contract. The bounded run
passed all 29 cases in 2.44 seconds. In the recorded five-return, 32-slot
case, static cleanup calls decreased from 320 to 64 (160 to 32 unpins and
the same reduction in clears), using 32 shared cleanup blocks. Each executed
return still performed the original 64 cleanup calls in the original order.
This is a synthetic host emission contract, not the measured module's slot
inventory or a native speed result. Full-module IR
reduction, native execution across the five collectors, Stage1 time/memory,
and Stage2/Stage3 fixed-point qualification remain normal CI work.
