# Investigation: an owned rebind consumes a borrowed local's source reference

## Status
active

## Problem Description
Native optimizer execution on the real py_gen IR crashes in pcc_gc_unpin.
The exact generated caller IR and LR at the failure locate unpin after
simplify_module_text returns. That function initially borrows ir_text into
current, then assigns an owned local copy to current. The fast
pcc_gc_store_root_take path releases current's previous pointer even though
that slot never owned the borrowed value. A sufficiently large input string
is unmapped, exposing the premature release at the caller's unpin.

Predecessor: runtime-module-optimizer-throughput.md. This is a generic compiler
ownership defect, not a reason to rewrite the optimizer's accumulator or to
skip large inputs. The distinct IfExpr ownership case is documented in
pcc1-owned-ifexpr-local-transfer.md.

## Repro
tests/python/test_borrowed_local_owned_rebind.py retains a 200,000-character
caller string, borrows it into current in a callee, then rebinds current from
an owned replacement local. CPython prints `200000 200000 a b`. Native pcc1
2b08f3a7aac1 emits an executable that exits 139 without output.

## Test [CONFIRMED]
The minimized native executable fails. Exact optimizer LR evidence:
/tmp/pcc_owned_optimizer_runtime_probe_20260907/lldb-lr.log. Actual linked
compiler IR (not a standalone emit-only stub):
/tmp/pcc_owned_optimizer_actual_ir/self_backend_input_1.ll, function
user_pcc_native_ir_instsimplify_simplify_module_text.

## Proposals
- No.1 restrict ownership-transferring replacement to slots that cannot hold
  borrowed values [pending]

## No.1 preserve borrowed slots on first owned replacement
### Code Change
Keep the established flag-guarded release/store protocol for a local that can
hold a borrowed root. The unconditional take operation may replace only a
slot whose old pointer is owned or null. Preserve the existing exact-int
protocol fast path for qualified owned slots. Gate all five GC backends and
the native optimizer before resuming optimization experiments.

### pending
No compiler implementation change yet. Both repositories remain uncommitted
at the maintainer's request.

## Update: focused fix verified
assignment_statement_lowering excludes a potentially borrowed local from the
unconditional store_root_take fast path. Its existing ownership flag controls
the replacement instead. The regression first failed with native returncode
-11 (2.73 s); after the fix it passes all five GC modes. The exact-int loop
protocol/promotion packet also passes, including its C runtime reference:
4 tests, 8.61 s. This preserves the existing qualified integer-loop fast path.

The owned optimizer now passes this original unpin site. It exposed a separate
regex-result ownership defect, documented in
native-re-sub-owned-result-raw-scaffold.md. The previously built pcc1 binary
still contains the old compiler source; fresh pcc1 verification remains open.

## Update — 2026-09-19: the first borrowed loop store also needs the ownership flag

A native Stage2 worker compiling `pcc.backend.self_backend_precise_stackmaps`
exits with SIGBUS. Its exact replay with provenance audit mode 3 and known-ref
checks aborts at `pcc_gc_release_known`, called from
`emit_function_prologue` after `copy_address_to_value_slot(regs[0], func,
arg.name)` (line 94). The callee chain includes `copy_address_to_address`, whose loop borrows
`src_addr_reg` into `src_reg` and subsequently rebinds `src_reg` to the static
string `"x16"`. The reduction below reproduces that ownership pattern; the
original native worker remains the integration confirmation gate.

The ordinary-program reduction is:

```python
def chunks(source: str) -> list[str]:
    result = []
    for offset in range(4):
        current = source
        if offset:
            current = "x16"
        result.append(current)
    return result
```

A heap source such as `"x" + str(index)` exposes the invalid reference count.
This fails under the current host compiler as well; it is not an LLVM/backend
ABI disagreement. The generated IR stores the borrowed source into `current`
without clearing an ownership flag, because the flag does not yet exist when
that first statement is emitted. The later branch creates and sets the flag.
On the next loop iteration the borrowed store overwrites the slot while
leaving that runtime flag true; the next owned assignment releases the
caller's reference. The prior fix above guarded owned replacement but did not
handle this backedge into an earlier emitted borrowed assignment.

`assignment_statement_lowering` now emits the flag-guarded replacement protocol
for managed object local stores inside loops from their first assignment,
including a borrowed RHS. The existing protocol releases a prior owned value
and clears the flag before storing a borrowed value. Parameter/global slots,
raw unsafe pointers, suppressed-root contexts and scalar integer-loop paths
retain their separate rules.

Two executed regressions in `test_borrowed_local_owned_rebind.py` cover the
heap-string case with provenance abort enabled, and fresh object replacements
with exact finalizer counts. Both run on GC0–GC4. The focused packet reports
**12 passed**, including prior borrowed-local replacement, tuple assignment,
walrus ownership, integer-loop protocol and C-runtime mirror checks.
The initial focused packet above was host-only. The rebuilt-pcc1 confirmation
below separately exercises the new backedge fix; neither packet establishes
full Stage2 completion.

Evidence: `/private/tmp/pcc-stage2-native-ayfmxjgz/`. The failing compiler is
`0373254d264bae2d461f481ed084124c6ccbdace262b15e8407511b4a5703ffe`, from frozen
source `81a16324935f9dc06d7320512d0fd69202a716c92db6d560598b26838a77cca4`.
`resume-stage2/receipt.json`, `debug-stackmaps-bt.stdout`,
`prologue-release-context.asm`, `borrowed-alias.ll`, `borrowed-loop-red.log`
and `loop-ownership-focused.log` preserve the failing module, actual native
stack, emitted release, minimal failure and scoped verification.

### Native confirmation — 2026-09-19

`loop-stage1/manifest.json` records a successful Stage1 in 584.4 seconds, with
source SHA256 `92fc1c1b13ec5627bae8ced72dda2a03c385d1de00a8ea85f8852e709ca4e437`
and compiler SHA256
`425b242ecd77fd5c6caaccc8f6cfd1ec11eef98cab9d0de4810d339f5df2a6af`.
The consumed runtime SHA256 is
`d8f82e49001889450c2d6a7a084397889e0f638e609fec7a9a0ed9932a3aeef6`.
The compiler's native function smoke emits and executes a program printing `42`.

Replaying the original `worker_5.manifest` with this compiler and
`PCC_GC_REFCOUNT_PROVENANCE_PROBE=3 PCC_GC_KNOWN_REF_CHECKS=1` now succeeds:
`self_backend_precise_stackmaps` produces `module_86.direct.pco`, with neither
libpython nor native-extension exports required in the worker result flags.
`stackmaps-fixed-small.result.json` records exit 0 in
76.295 seconds and a 2,039,119,872-byte peak process-tree RSS. This is the
original failing frozen AST/export input with the new compiler, explicitly
using direct PCO emission. It is a local integration replay, not a fresh-source
Stage2 or an old/new performance comparison (the old run crashed).

The exact emitted-program regressions also pass through this new native pcc1:
the two loop tests above execute all five GC modes and check the borrowed heap
string and exact finalizer counts. They ran alongside four script-filename
regressions in `loop-native-tests.stdout`: **6 passed, 7 deselected, 146.01 s**.
`loop-native-test-inputs.json` records compiler/runtime/test identities;
`loop-native-tests.nodes.jsonl` retains the individual results.
The invocation selects `-m integration -x -n0 -vv --tb=short`, explicitly sets
`PCC_CURRENT_PCC1` and `PCC_RUNTIME_ARCHIVE` to the Stage1 artifacts, and disables
automatic pcc1 rebuilding. Full fresh-source Stage2 remains a separate gate.
