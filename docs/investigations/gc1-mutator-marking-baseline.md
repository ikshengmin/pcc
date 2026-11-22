# GC1 marker startup and mutator-step baseline

The current worker admission in `pcc_gc_maybe_start_cms_worker` allows
GC1–4 both before and after claiming startup. The GC1-specific
`pcc_gc_incremental_maybe_auto_step` calls that starter, then returns
when the thread kernel is enabled instead of spending allocation debt
on a mark slice. This means removing GC1 from only the starter is not
enough: the allocating mutator still needs a path that makes progress.

A guarded owned-C Linux native baseline uses the real threaded kernel
and reads worker start telemetry across startup, repeated scheduling
entries and two explicit collections. An exact registered list root
survives. Expected: GC0 starts 0, GC1–4 each start 1, with no duplicate
starts. All five cases passed. This is deliberate baseline coverage,
not evidence that automatic mutator stepping works or a pause result.

The first candidate must change the paired conditions: admit only GC2–4
in the common worker starter, including its recheck, and let GC1 spend
debt from a safe application gate. Validate that this does not depend on
the CMS worker to publish debt, seed roots, commit a finish or drain a
sweep. A positive native automatic-progress case must not call
`gc.collect()` to make its progress assertion pass. Verify other
collectors keep their current worker policy and keep the existing
root/weakref/finalizer tests. No runtime or performance change in this
baseline.
