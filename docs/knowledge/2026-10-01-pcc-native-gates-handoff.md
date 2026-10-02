# PCC native gates handoff — 2026-10-01

The original remediation scope and five EDG-inspired improvements remain active.
Status questions do not cancel work. Preserve all five collectors, Python/C
semantics and owned execution. The user requires one final commit, not split
commits. Nothing has been committed or pushed. HEAD is
ab6f29d08a9ca53034c85c35eda636b135185262; .git is read-only in the managed profile.

## Current frozen input

`build/remediation-20261001/frozen-current-v19-source.json` identifies 1089 files:
`ef49f00fd572a2b579d74ef1ee20c1355bdbe4ff09663d8ee1212ec459c29327`.
Normal runtime19 completed and all15 native identity executions passed. Stage1-v19 compiled/linked but failed the real smoke; threaded19 runtime is building. Frozen inputs are immutable. Do not
edit/reuse results in earlier versioned directories. Native and bootstrap
qualification remain pending for source19.

The private host environment is `build/remediation-20260930/venv`, with no
llvmlite. Use `env -u LC_ALL UV_CACHE_DIR=$PWD/build/remediation-20261001/uv-cache
UV_PROJECT_ENVIRONMENT=$PWD/build/remediation-20260930/venv uv run --no-sync`.
The diagnostic watchdog is
`build/remediation-20261001/stage1-v8-retained/owned_watchdog.py`; it wraps the
existing process-tree watchdog and observes only its own Darwin descendants.
Heavy work holds `build/.pcc-performance.lock`, uses isolated outputs and a
16 GiB tree cap. Every pytest command needs -x; native/archive tests must be
selected deliberately.

## Source17 execution evidence and failed boundaries

Source17 identity:
`82df36c6baa9de1f3feafcf01f8735478303be6ff23a2c5cd76b7623f209b2e9`.
Normal archive SHA `9b6370e387bb84b76c09a1258aea141750a281f10e6e0aa3a26b682e925f46d3`;
thread archive SHA `ab09735be0b654e6620ad5238eb6b7f8a08f1a3af652f8f8751fe2e747e15b22`.

`native-changed-v17` completed 25 host-pcc0/self/no-libpython emitted executions:
regex literals, Context isolation, first-class set factories, shared AST wire
and native optimizer global references, each GC0–4. All stdout assertions and
empty stderr passed. This is component execution, not a qualified pcc1.

`native-threaded-v17`: original selectedGC2 Condition minimum, full Condition
notify/notify_all/timeout semantics, and original capacity workload passed all
selected backends. The 30s program limits and originals were unchanged. Cold
exception cache passed GC0–3 but GC4 failed twice (SIGSEGV); the independent
owned child PID6449 report and exact-binary symbolization are preserved there.
Its path is class deallocation during losing exception-class publication.

`stage1-v17/profiles/stage1.result.json`: compile/link 229.327s, full stage
230.226s, validation returncode1. It produced a 364453672-byte compiler with SHA
`c41fadae24f390e99eab885edeafaf57d5a0473b4ae65608cef17fc7f0306daa` at
`stage1-v17/compiler/pcc1`. The real smoke compile failed, so Stage2 was not
started. With `PCC_DEBUG_NO_CATCH=1`, `native-pcc1-smoke-v17` shows
`self_backend_aarch64_darwin_memory:459 _atomic_width_check` -> silent NULL
signature binding. The helper has four parameters, including the omitted tuple
allowed=(i32,i64), not three. No factory/default exemption is allowed.

The actual native frontend emits the smoke IR successfully in
`native-pcc1-frontend-v17/smoke.ll`, SHA
`b914e711635acf5774e35c32dbf0e9e467d714637313de7c255e9dd0841f62dc`.
It contains a real atomic i32 load for GC4 pending state. Standalone native
atomic helper and exact packed-record controls pass, including cold invocation
before reading __defaults__, and seven-worker AST-wire construction. These are
subset contexts, not proof that full pcc1 binds correctly. A read-only external
debugger could not launch its own compiler child; no slot observation resulted.

`native-pcc1-c-v17`: the exact host_abi.c compiled/linked through public pcc1 CLI,
but execution returned5 at callback(41)!=42. Host pcc0's same source returned0.
O0 native IR drops local, parameter and struct-member callback calls and returns
zero. `native-ir-pointer-v17` proves metadata is present: FunctionType and
pointee print correctly, but isinstance(...,ir.FunctionType) is False, while
PointerType is True. The collision is the builtin types.FunctionType tag being
selected before canonical IR class identity.

## Current changes after source17

Source18 (`43d15b5477e077e50c68df3dd6c4ed11f27ecbd5ed5ff7e145a3d973b0526de9`)
adds two runtime changes:
- py_exc_table: register candidate root in caller before another safepoint,
  reload after safepoints, transfer successful owner to cache, release losers
  normally, then acquire canonical cache after frame leave. No permanent pin or
  disabled relocation. Host14 plus four-target IR/object checks passed.
- py_func: specific internal NULL diagnostics with callable name context,
  preserving pending exceptions and successful binding rules. Host21 plus
  four-target IR/object checks passed.

Matching thread archive18 SHA
`7929d1236f993ab8e7b1fa92427431c49983c8b4313f601c10872ce0d6f5d694`.
The original cold16 GC4 still fails (accepted assertion), and diagnostics can
still SIGSEGV. It is not fixed. Extra-predicate diagnostic exception run passed
once, but changed scheduling and repeated predicates cannot qualify the original.
Use the newer original-predicate record controls prepared in
`list-append-concurrency/cold-cache-original-predicate-v18-receipt.json`.
They preserve short circuit order and every assertion; do not weaken originals.

Source19 additionally fixes canonical class identity in isinstance, roots and
reloads the evaluated operand across class-object creation, and makes C invalid
callable/arity errors explicit instead of dummy zero. Python75 and C60 focused
checks passed. Stable hashes and exact new native programs are in
`c-pointer-v17-diagnosis/source-handback.json`.

## Next gates

1. Repair the actual Stage2-v20 closure/default call-boundary failure. Retained
   worker9 and native public builder IR controls reproduce it in about1s.
   Source20 Stage1 passes actual public pcc1 smoke compilation AND execution;
   no pcc2 was produced and Stage3 must not start yet. Never bypass smoke,
   add stubs, alter defaults or label a failed binary qualified.
2. After stable exception/class producer repairs, freeze a new matching source,
   build its threaded archive, and run the unchanged cold16 GC4 control first.
   Source19 still exits134 with `[BAD_INCREF]`; Condition/capacity shapes were
   not run in that failing group. Stop at the first failed boundary.
3. Re-run public native C/Python controls on the qualified new source, then
   Stage2/3 in dependency order and raw fixed-point comparison. Four actual
   platform CI jobs, five-GC bootstrap and other original scope remain unfinished.

## EDG improvement state

Shared AST field authority has actual component proof; full semantic front-end
representation is unfinished. Field/layout defects were fixed but four layout
producers are not yet one authority. Compile/link/run gates and negative checks
are implemented. Function-lifetime memory work has only source inventory, no
measured redesign. Central C target ABI is implemented, but full native/compiler
qualification remains incomplete. The latest status answers must retain these
limits rather than presenting all five as complete.


## Later source19 results

`native-identity-v19` completed all15 actual executions, including the original
pointer metadata program (FunctionType predicates now True), IR/builtin identity
and attribute-class identity. All five collectors produced expected stdout and
empty stderr. Normal runtime19 SHA:
`ab0c5b89a0cbf2270969057d1e433cb7e7b16c5b726aaa5c924ede73a38ca1a0`.

`stage1-v19` failed at 257.125s after successful compiler emission/linking.
Specific runtime diagnosis now says **native function positional argument entry
is NULL** in `_atomic_width_check`, excluding missing default signature data.
The exact debug traceback is in `native-pcc1-smoke-v19/stderr.log`. The compiler
SHA is `a9e20c71ad2b2db5b4cbfdb102170ad1e698bab1cf9ab6a01dbbcc014c7fab93`.
Stage2 has not started.

The same new native pcc1 compiled and executed original host_abi.c with return0,
empty program stdout/stderr (`native-pcc1-c-v19/behavior.json`). This closes the
exact earlier callback/sizeof/signedness/bool C control; it does not qualify
all C language shapes or a self-host fixed point.

Source20 SHA is
`4ff025da2e19dac738d589f016a5cebf29dad2931ca75498365d20816d4c6d93`.
The prior dense emitter accepted an integer record ID through tuple-only
annotations, allowing `isinstance(data, int)` to fold False and selecting
legacy tuple slicing. Five real receivers and their callback now admit
`int | tuple`; legacy tuple-only consumers remain unchanged. No record/runtime
ABI fields changed. Root re-ran all10 focused checks (four object targets).

The matching normal runtime20 archive is
`f7b2a5557cf4143b5dcb3afe3f815295ce555586ab2c457facc6edcb57a74372`;
owned build completed in91.711s. `native-payload-v20` ran the same int/tuple
forwarding program under all five collectors: five correct outputs and empty
stderr. Compiler/runtime/source receipts are in that directory. This component
proof was followed by the actual pcc1 dense-emission gate. Stage1-v20 passed:
244.030s compilation/linking,8.331s publication/smoke gate,252.361s total.
All1089 frozen source files were rehashed unchanged. Compiler SHA:
`8f32cb999659561d19bb06711f82fb6b7111cdce23e1a70ff29d711a15470f25`.
`native-pcc1-v20` additionally compiled and ran original C ABI control and
Python int/tuple control across all five collectors, six clean executions.

Stage2-v20 stopped at68.079s before producing pcc2. The retained manifest is
`stage1-v20/compiler/pcc2.pcc-codegen-plan.state.65705/manifests/worker_9.manifest`
(index376, C SSA builder). Native one-worker replay reproduces in0.98s.
Public builder IR diagnostic without `--no-cache` at `native-builder-ir-v20-trace`
produced the full traceback: hoist_lowering.py line293's two-positional call
to nested analyze_names reaches the binder with unexpected keyword ast_module.
Current-source diagnosis: hoist rewrite_expr adds capture kwargs, whereas the
opaque-default runtime-call early return precedes capture resolution; its
function signature contains only original Python formals. Minimal native red
and narrow generic repair are pending. Do not rename the helper, accept extra
kwargs, remove tuple defaults or skip C builder to conceal this boundary.

## Source21 component gates and repeated-shape audit

Source21 SHA is
`bf35fa7bf6757977abfbe93891635c4aa628c067842eb9a48062b12b9e6dbbc6`.
It contains the stable first closure-default binding/adapter fix, exception
constructor roots, and owned x86 pointer initializer repair. The in-progress
class/MRO repair is excluded. Matching normal archive SHA:
`b735261570b1951e5306a23d27cbeba40a196e6c595516339463f4467e7e02a2`.
Owned build took90.102s. `native-hoist-v21` completed35 native executions:
seven programs across all five collectors, including the unchanged original
red, defaults evaluated once per definition, keyword-only parameters, recursion,
invalid user keywords and the original literal-dict form. Every output matched
and stderr was empty. This was host pcc0/self/no-libpython, not a new pcc1.

The root's repeated-shape audit also proved a remaining sibling defect:
`native-hoist-call-audit-v21` no-default inner(**mapping) exits1 with unexpected
capture keyword seed. Native source SHA8ce754be... and failure are preserved
in behavior-focused.json. Its no-default explicit invalid capture keyword
also fails during lowering rather than reaching the source TypeError handler.
The expanded shared source-call-shape binding fact is now under focused review;
do not build/qualify source21 Stage1 as a complete fix. Use its successor's
stable handbacks, execute all old/new controls, then resume the actual retained
SSA-builder worker and Stage2/3.

Exception constructor focused gate:23 passed, including forced allocation/
publication/unregistration movement, cleanup and four-target owned objects;
the old frozen20 source fails the same forced-allocation model with a stale
class/value argument. Its live source/test hashes are in
exception-constructor-roots-handback.json. Actual ordinary TypeError construction
also ran in the source21 native controls; the unchanged cold16 GC4 qualification
and producer's forced-movement native boundary remain pending.

x86 pointer initializers:17 focused checks passed, including actual ELF
relocation symbols/addends and numeric word readback, four object formats and
the original literal-dict regression. Receipt is x86-pointer-initializer-handback.json;
x86 Linux/Windows execution still requires its platform gate. The host C API
suite in the private no-llvmlite environment passed31 tests, including full
scalar values, floating results, live pointer ownership and named arguments.

## Source22 qualification in progress (continued October2)

Source22 SHA is
`2bf003d3231863ca717607d1dd93f6ac304935ae4ed3c8a743f74f93f592a024`.
All1089 live compiler/runtime input files matched this frozen source at creation.
It adds the expanded shared source-call binding fact and stable MRO roots to
source21. Root focused checks passed74 with22 integrations excluded.
`native-hoist-v22` ran14 controls under all five collectors:70 clean native
executions, including both unchanged prior red programs and the literal form.
Normal archive SHA57c14ef38b09ea67f5efeaa9241b6045c7ba0e2047e874991bbda42a1af02b67
was built in84.071s. Thread archive SHA15ba869c56cdbafcbdd529e4a0f9ff9b70b134546ff2b34206da975cbeb310ec
was built in86.366s. Both were owned builds, with no external LLVM/cc fallback.

`native-threaded-v22` is running the unchanged cold16 GC4 first, under the
original30s program timeout, then the other collectors/Condition/capacity only
if that passes. Outer watchdog240s,16GiB tree-RSS; owned exec session80170.
Read behavior.json/result.json before using any result. No Stage1-v22 built yet.

Independent review found two more lexical shapes, now confirmed native red:
native-shadow-red-v22 ignores a child parameter named inner and calls the
parent nested function; source144acbf910d41913133545e18d0643bbd43a5df2419d65db7406a8f22df4e74f.
native-block-call-red-v22 defines inner inside if but calls it via mapping in
the enclosing block, leaking capture seed into user kwargs; source
04999385f0275e8794f6e8ffd4690270a6874c69e2a7f0d57a27e4d39a56702e.
The hoist owner is repairing child-body rename barriers and full lexical-body
call-use ownership; its successor must execute these unchanged red sources
before the next Stage1/retained builder worker/Stage2/3.

Review also reproduced class field-table malloc failure returning an incomplete
class. Root added test_class_constructor_field_table_allocation_failure_retires_class,
proved red, then uses existing construction failure cleanup when malloc returns
NULL. The13 class constructor focused checks pass including four object targets
(class-field-allocation-{red,green}.log). This live guard is newer than source22;
include it in the next frozen source. Native injected-OOM qualification is pending.

## Source23 and active binding/import completion

The source22 threaded gate finished green:16 actual executions, including
unchanged cold16 firstGC4 then0..3, minGC2 Condition, full Condition semantics
and original capacity test across all five collectors. All expected outputs
and empty stderr; total22.350s. This closes that exact prior BAD_INCREF control.

Source23 SHA
`1987ba0158ca2d70f70a8de10edfca5182906b10ccdfbad66194ba8b4cb5b8dc`
contains the stable lexical-body/map-barrier fixes and root's field-table
allocation failure guard. Root93 focused checks passed/28 native excluded.
Normal owned runtime23 SHA
`3ba998475ed7372742f05c6c83ba77bf5e5a427e575c900aacde99dcb0306128`
built in83.809s. The29-program all5GC native matrix stopped after80 clean
executions at scope_import: actual function-scope `from builtins import abs`
raises ImportError. Do not call this full matrix green or start qualification
as if the import/boundness gap were closed. Native script/rows are retained in
native-controls-v23. No new pcc1 was built for this source.

Independent repeated-target review/native probe also found AugAssign local
binding missed: original shadow_augassign source12e8afd8... exits1 with TypeError
instead of caught UnboundLocalError. Three original controls and ownership/IR
receipts live in closure-readonly-v23; the native red is at
native-local-target-red-v23. AugAssign/Delete/ListExpr/star/walrus collector
correction is now stable in hoist_boxing.py/hoist_free_names.py. Agent69 focused
and217 sensitive checks passed, root repeated69 focused checks; full hashes in
lexical-target-collector-source-receipt.json. Local slot/read-boundness producer
is still being completed by owned_patterns. It must not reinterpret owner bits
as bound bits or make every local Dyn. It uses authoritative lexical bindings,
existing typed slot plans and separate private `.bound.` i1 flags in existing
flag dictionaries, with actual assign/for/except/del stores and reads.

Active owned_patterns work: real function-scope builtin member imports and
canonical value identity shared with ordinary builtin reads. Existing real
function/class materializers/rooted import store are reused, abs uses py_obj_abs.
New owned ABI entries py_builtin_function_value(entry,name) and
py_builtin_abs_entry(captures,args) have runtime declaration/root-inventory
changes. No fake module, stubs, permanent pin or external owner is allowed.
Runtime signature chunks are being rebalanced without reordering declarations.
Do not freeze a successor until this track's stable hashes/focused receipts.
Compiler_sync's shared collector files are stable and must be included too.

Exact native field-table OOM diagnostic now passed all5 collectors, independently
of the import gap. native-class-field-oom-v23/qualification.json binds the
frozen23 class IR and unchanged runtime members to a test-only replacement of
the single copied_fields malloc call. Owned C hook counts exactly one failure;
managed native main verifies normal field table, NULL on that failure and
successful recovery. No production injection API or changed production source.
Attempt2 passed five outputs CLASS_FIELD_OOM_OK/empty stderr in3.755s;
first fixture attempt failed on ambiguous c_ptr return, fixed to c_rawptr.

Latest qualified Stage1 remains source20:244.030s compile/link +8.331s smoke,
252.361s total. Its Stage2 failed68.079s. Source23 successor still needs all
original/new native cases, actual fresh pcc1, retained SSA builder worker9 and
Stage2/3 raw fixed point. Four actual platform CI jobs/all5 full chains and other
original17 groups/EDG shared-layout+memory work remain incomplete.
