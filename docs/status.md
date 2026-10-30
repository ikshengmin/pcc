# PCC current status

Updated 2026-10-30. Source: `ikshengmin/pcc` master (`shenming` remote).
Durable receipts for this period (patches, profiles, A/B scripts, repros):
`/Users/jiamo/pcc-recovery-archive/2026-10-13/README.md`; the 2026-10-21
handoff is `/Users/jiamo/pcc-recovery-archive/2026-10-21/README.md`. The 2026-10-07 status
(source preservation, the remote agent's backups) is in git history at
`38357808:docs/status.md`. All ten goals below remain open.

## Native throughput versus CPython 3.15.0: active, acceptance unmet

The maintainer requires Python compatibility and native performance together:
PCC must beat CPython 3.15.0 asyncio at one worker and four workers, using at
most four workers. Four-worker asyncio was previously reported at about 268k
requests/s. Small gains do not close this target. Only after demonstrated
performance and compatibility should README gain the requested "Why PCC?"
claims. All five collectors, arbitrary-precision integers, identity, exceptions,
finalizers, weakrefs and resurrection remain required.

Fresh frozen Darwin host-pcc0/self/no-libpython/threaded-atomic measurements:
one-worker PCC/asyncio medians 22,414/80,834 requests/s; a separate matched
four-worker packet gives 26,742/215,139 (8.05x gap). Both use 40k validated
requests, total concurrency 100, zero artificial delay and 200 warmups.
Asyncio uses four processes/event loops; PCC uses four persistent carrier
threads. These are handler batches, not HTTP server throughput. Historical
268k and current 215k are different packets, not a PCC improvement.

Fresh samples of those binaries complete 400k validated requests. JSON dumps
occupies about 37%/36% of one-/four-carrier captured stacks; four-carrier graph
lock waiting is substantial. Inclusive samples overlap. TLS/prologue trials
failed throughput acceptance and were withdrawn through `17a1a0036`; their
53 focused checks pass and peers' Linux-linker changes are preserved.

`cd1c356cc`'s publication trial did not establish a material throughput gain;
`81f82b175` restores the original generated publication sequence. The helper
remains an unused, tested runtime capability. `d4f45ac95` reuses construction
leases for list/generator-frame handoff; its 52 models and GC0-4 executions
pass, but the controlled A/B does not establish a material speedup.

Raw-buffer thread caching (`1d9969641`, completed by `090ecc86a`/`95f008951`)
reuses the existing registered-thread cache page for bounded 16-2048-byte raw
buffers. Cached cells reserve their slab counts; same-slab affinity bounds
retained mappings; remote frees, explicit trim and thread exit preserve
accounting. Three native raw/object-cache/standalone-C tests pass, including
live remote-cache trim and a mapped-memory plateau. Both original request
programs execute correctly on GC0-4. Independent conformance, integration,
pattern and build checks found no remaining scoped correctness findings.

One allocator object was rebuilt; 182 other objects are byte-identical to the
control and pass source/config/compiler admission. Five alternating pairs
show four-carrier median QPS 30,393 -> 35,040, with median paired gain **10.4%**
and all five pairs positive. One carrier is effectively unchanged (23,810 ->
23,732, paired -0.24%). Same-packet CPython 3.15.0 asyncio is 88,062 / 293,005
QPS at one / four workers: the remaining gap is about 3.7x / 8.4x. This is an
interim scalability improvement, not completion of the acceptance target.

Permanent main-image scalar origins (`01d61fd3c`, restricted by `ee9f39502`,
strict ABI admission completed by `ce977a602`) can omit counted address leases.
Unloadable library literals and allocator-backed immortals keep the checked
protocol. Six frontend checks and 145 focused checks pass, including native
registration/collection/mode transitions; both request programs pass GC0-4.
An isolated control disables only admission and its two consumers, reusing
181 byte-identical runtime objects. The five-pair result is a 0.67% instruction
reduction and small paired QPS changes of +1.73% / +1.80%, insufficient evidence
of a material throughput improvement. Same-packet medians are PCC 23,428 /
34,449 versus asyncio 89,930 / 272,906 QPS. The main acceptance remains open.

Private operand copies (`2a9afc807`, corrected by `e00726da1`) transfer one
additional owner without a temporary address lease before the actual
consumer's lease. Both endpoints must have private-root proof; shared sources
and destinations retain the prior protocol. Independent review found and fixed
the initial missing destination check. Eleven focused checks pass, including
native GC0-4 ownership/callback/exception/finalizer execution, actual GC3/4
movement and deterministic production-body borrowed healing after lock waits.
The expanded compiler/ownership checks pass 273 selected cases; 26 remain
deselected. Both original request programs pass GC0-4 on frozen `rootcopy2`
(`cfbcf8e8f`); native-pcc1 qualification is still outstanding.

The `rootcopy0` compiler control disables only private-copy selection and
rebuilds runtime plus applications. Five pairs show instruction reductions
of 1.78% / 5.07% at one / four workers, but paired QPS gains are only 1.08% /
1.56%, with 3/5 positive pairs each. This does not establish a substantial
throughput gain. Same-packet PCC medians are 24,195 / 36,246 versus asyncio
87,245 / 284,429 QPS: gaps remain about 3.6x / 7.8x. Actual hot request IR
changes only nine copy sites. Further isolated helper tuning cannot be
treated as a plan to close this gap.

The first current-IR census was rejected after 15 call-PC mismatches in
instrumented `py_list.o`; the ordinary object passed 1,002 records. The cause
was variable probe width when counter IDs crossed 8,192 between layout and
emission. `current-census-fixed` uses fixed-width probes and the current
decorated function IDs: 101,694 call records pass, and 1,000/2,000 executions
return the expected 86,000/172,000 checksums. Selected functions account for
294,727 instructions/request and 6,481 calls. List/cache helpers are included;
unselected functions and system libraries remain explicit residual cost.

Owned GC short paths (`90e9c046b`, completed by `e82932590`/`b007f47a9`) now
expand eligible root reads, inactive frame operations and GC0 empty barriers
in Darwin AArch64 machine emission, after precise-root planning. Original IR
calls and cold-call return PCs remain intact. Stop requests, initialization,
active root registries and moving collectors preserve the full runtime route.
Explicit ABI metadata survives both sharding routes; local/sibling overrides
are excluded. Unmarked C calls and other targets retain ordinary calls.
Sixty-six focused checks pass, including native fast/cold matrices, precise
return PCs, frontend/shard propagation and exclusions. Eleven ownership checks
pass, including marked native GC3/4 movement. Both original request programs
execute correctly on GC0-4 in frozen `gcshort1` at `b007f47a9`.

`gc-short-comparison.json` compares that artifact with `gcshort0`, a full
runtime/application rebuild with only short-call admission disabled. Five
alternating pairs improve complete-request QPS by **4.63% at one worker and
5.79% at four**, with **5/5 positive pairs in both cases**. Whole-process
instructions fall 5.17% / 5.54%. Separate synchronous N/2N medians fall from
309,943 to 292,171 instructions/request. Same-packet QPS medians are PCC
25,297 / 37,167 and CPython 3.15.0 asyncio 88,867 / 296,330. The remaining
gaps are about 3.5x / 8.0x, so acceptance and README performance claims remain
open. These are host-pcc0/self/no-libpython results, not a new-source native
bootstrap or fixed-point qualification.

The native control also exposed a pre-existing parser ambiguity: SSA names
such as `%null` lost their reference namespace and could be folded as literal
constants. `11cc25742` preserves those names; twelve decoding cases and a
native two-edge branch regression pass. Four existing name-decoder/record
inventory checks also pass. This is a correctness fix, not a claimed speedup.

The subsequent synchronous-generator-statement trial (`2a014beee` through
`2f2e69a25`) reduced the actual request heap frame from 38 to 35 cells. Both
frozen arms (`genroot0`/`genroot1`) passed the original requests on GC0-4, and
the candidate passed native lifetime/exception/finalizer/yielding-finally
checks. However, five paired runs showed only +1.01% / +0.62% QPS, each with
3/5 positive pairs, while instructions increased 0.92% / 0.36%. This is not an
accepted execution speedup. `bc2164679` leaves the candidate explicitly
diagnostic and restores the default prior policy. Seventy-two focused checks
pass, including byte-identical default IR; the matching prior-policy native
control passes both lifetime programs on GC0-4. Tests and experiment receipts
remain available; no history was discarded.

Fresh samples of the accepted `gcshort1` native image complete 400k validated
requests at both one and four carriers (`gc-short-runtime-profiles.json`).
Single-carrier store_root and descendants occupy 22.72% of sampled stacks;
four-carrier graph-wait acquisition is 11.13% self and graph acquisition 4.70%
self. Inclusive costs overlap and do not establish an eliminable fraction.
Runtime investigation remains the active performance workstream. The later
identifier-validation cache (`ba0592a38`) is compiler-side work and has no
execution-QPS claim.

Generator/private-frame coallocation is enabled by `725e9b8db`. GC0 uses one
allocation for the generator and its fixed slots; GC1-4 construction retains
the list representation. Existing inline frames remain usable after collector
switches. The implementation also repairs pre-switch container owner retirement
and preserves completed-generator values and suspended coroutine throw.
Forty-two focused checks pass, including default/explicit-on IR equality,
GC0-4 native lifetimes/protocol/cycles, all four GC0 mode transitions, small and
large container owner counts, cache identity and generated ABI metadata.
Both original request programs pass GC0-4 on frozen `inline_default`.

`inline-frame-comparison.json` records five rotating 40k-request pairs at
one/four workers. Against the prior accepted `gcshort1` artifact, paired QPS
changes are +2.67%/+4.40%, with 5/5 positive pairs each; instructions change
-1.68%/-0.77%. Same-packet median QPS is 24,700/33,613 versus CPython 3.15.0
asyncio 84,331/270,901, leaving about 3.4x/8.1x gaps. A same-source off/on
control isolates coallocation at +5.22%/+4.03%, but its four-worker comparison
has only 3/5 positive pairs. These are incremental host-pcc0/self/no-libpython
handler results, not completion or new-source native-bootstrap qualification.
Evidence is under
`/Users/jiamo/pcc-recovery-archive/2026-10-29/runtime-vs-asyncio/`; frozen arms
are in the sibling `2026-10-28/callee-save-pairs/` archive.

Fresh default-image profiles complete 400k verified requests at one and four
workers (`inline-runtime-profiles.json`). JSON dumps occupies 37.54%/34.86% of
captured stacks; four-worker graph waiting is 11.74% self and graph acquisition
4.48% self. Next is a diagnostic-only bound on list global synchronization,
preserving no-park/STW participation. Removing mutator synchronization is not a
production implementation or semantic proof; per-object synchronization still
requires a complete reader/writer and collector-transition contract.

The list-only diagnostic replaced 30 hot graph-lock call sites with no-park
entry (the remaining 39 are module initialization); its matched five-pair QPS
changes are +5.55%/+5.99%, 4/5 positive each. It removes list mutator exclusion,
so these are an experimental bound, not production gains. No list-lock change
was published. Receipts: `list-nopark-bound-comparison.json` and
`list-nopark-call-owners.json` in the archive above.

Known-object root stores (`289bc7d78` with follow-up domain/effect fixes) remain
opt-in and are rejected as a speedup. Both matched arms pass the original
programs on GC0-4; candidate native alias/transfer/lifetime checks pass all five
collectors, and 34 current compiler/effect/reuse checks pass. However, five-pair
QPS changes are +0.50%/-5.28% (3/5 and 1/5 positive), with instruction changes
+0.08%/+1.06%; synchronous instructions rise 292,437 to 293,579 per request.
`known-root-comparison.json` records this result. Ordinary refcount helpers
already have atomic fast paths; replacing them with another checked helper
did not remove net cost. `PCC_KNOWN_ROOT_STORES` stays disabled by default.

The next owner comparison is an external LLVM-O2 application-code oracle
using identical captured post-PCC-pass IR, the same self-built runtime and
the owned linker. LLVM-O2 performs additional optimization, so this is not
a production backend or an isolated machine-emitter claim. GC0 execution and
matching outputs must pass before any throughput conclusion.

Corrections to that diagnostic: replacing runtime emission initially showed
+11.46%/-57.52% one/four-worker QPS, but that comparison is invalid as a code
quality result. Both configurations matched, while LLVM-O2 reported ordinary
list/string/dict objects as unknown granules. A null-based byte GEP was being
used to reconstruct an integer address; LLVM's pointer provenance rules let
the uncached radix lookup fold into a miss. Replacing 19 such allocator IR
expressions with `inttoptr` restored kind-1/LIVE results. `765abb0fb` makes the
corresponding 22 source reconstructions explicit across allocator, identity
and index owners, without adding an LLVM dependency. The owned and labeled
LLVM-O2 cold-lookup executions pass, as do 23 focused checks and both original
programs on GC0-4. A fresh oracle now requires matching configuration and
granule results before timing; old raw oracle timings must not be cited as
the remaining compiler gap.

The intervening direct queued-lock handoff (`1f200dd8c`) passed its native
GC0-4 safety checks but failed throughput: +1.87%/-19.03% paired QPS, with all
five four-worker pairs slower and instructions +41.71%. `11b2a6aa1` restores
the original permission-to-contend policy; 15 model/production-body checks
pass, including added delayed-tail and deferred-tripwire coverage. The prior
barge explanation was a hypothesis, not a proved cause, and the handoff is
not an accepted optimization. Receipts: `graph-handoff-comparison.json`,
`graph-handoff-restored.stdout`, `llvm-application-oracle/`, and the corrected
`llvm-corrected-oracle/` under the same external archive.

Native-compiler qualification remains incomplete. The direct stage1 launch
was rejected on startup forecasts; the correctly guarded 4-GiB text route
timed out at 420 seconds after 114 module IR outputs (about 2.04 GB). The
standard indexed route also failed to finish within 420 seconds; its peak
tree RSS was about 2.11 GiB and the outer guard cleaned the remaining child.
A bounded in-process profile attributed its post-pass window to IR reparsing.
These compiler-path observations are retained for separate qualification work,
not counted as progress in request execution speed. Details are in the
recovery archive's `gc-short-stage1*` and `gc-short-worker-profile` receipts.

The native allocator probe also exposed unsigned-pointee loss through C
pointer arrays/dereferences and casts. `a18818183`/`97b47796e` fix the generic
lowering; 23 C checks pass, including four new native cases. Two pointer-atomic
checks are IR-only because owned pointer-atomic emission remains an existing
gap. Native-pcc1 bootstrap/fixed point, other-platform execution and broad
CPython conformance are still unqualified. New captures of the optimized
binary are being used to select the next single-worker cost to remove.

Source hashes, exact commands, outputs, frozen artifacts, rejected preimages,
profiles, bounded incomplete gates and the active acceptance contract are in
`/Users/jiamo/pcc-recovery-archive/2026-10-29/runtime-vs-asyncio/README.md`.
The underlying frozen build/measurement packets remain under
`/Users/jiamo/pcc-recovery-archive/2026-10-28/callee-save-pairs/`.

The boolean register trial (`20841ba75`..`add568ccb`) failed acceptance at
-2.02%/+1.40% paired QPS and is default-off since `5e336c7d5`. Five further
single-change rounds (2026-10-29 evening) used the same packet with seven
rotating pairs and process cycles from `time -l`, because host load reached
22 on 12 cores and wall-clock pairs alone swung by +/-10%:

- Ordering-aware LL/SC lowering (`92b62f468`): neutral (-0.34%/-0.70% QPS).
  One-worker IPC is about 4.2; the isolated LDAXR premium does not show. Kept
  as the exact LLVM mapping, not counted as a gain.
- Inline atomic counts in `pcc_gc_store_root`: +0.30% instructions, -3.4%
  one-worker QPS; the function grew from 279 to 420 instructions. Unpublished.
- ASCII `str.encode` copies the stored bytes (`78412a148`): one worker -1.4%
  instructions and -2.3% cycles per request (+3.2% QPS, 7/7, quieter run);
  four workers within noise.
- Bounded retry before yielding in `pcc_mutex_lock` (`9546b807d`): four
  workers +9.0% and +7.4% QPS (11/14 pairs); one worker runs the same
  instructions.
- An empty `py_tls_exc_swap_slot` returns before the graph lock
  (`b7e87fdee`): four workers +5.0% median QPS over 21 pairs (16 positive),
  -3.1% instructions; one worker neutral QPS (+0.4% median) with -0.7%
  instructions in all 21 pairs.

Cumulatively, the handoff state (`4bb8b2270`, arm `ctl0`) against the accepted
state (`b7e87fdee`, arm `swap5`) over two direct seven-pair packets: one worker
+1.45% median QPS (10/14), -2.1% instructions and -2.8%/-3.1% cycles; four
workers +3.23% median QPS (9/14), -2.5%/-3.2% instructions and -3.3%/-5.5%
cycles. Same-packet medians stay about 3.2x/7.4x behind asyncio
(25,080/33,376 versus 81,292/248,570 QPS in the first packet). Small gains;
the acceptance target is still unmet.

A per-operation instruction census locates the remaining gap in per-operation
runtime protocol, not the JSON algorithm: a trivial compiled int call costs 992
instructions (CPython 929), and in its samples graph-lock return pinning is
about a third; `sorted(d.items())` of two items costs 42,272 (CPython 2,975);
the payload's `json.dumps` 265,481 (29,257 with CPython's C encoder).

The 2026-10-29/30 rounds R1-R20 (same README) implemented the direct return
of a parking callee (`c3fafb59a`), static and tagged returns without an owner
protocol (`02eb053e7`) and NULL-result call edges (`8d730d79a`), and removed
the largest four-worker cache-line and lock-hold costs: the allocator lock line
(`e28b1bcec`, four workers +12.4%), scheduler wakeups after unlock
(`412a08d83`, +5.5%/+6.6%), a 1024-retry mutex budget (`85edff824`, +6.8%)
and lease-free builtin isinstance (`dc082721a`, +6.3%). Base runs of
`2c794fc02`: one/four workers 29,661-29,850 / 61,198-65,303 QPS versus
same-run asyncio 88,951-90,741 / 280,643-296,980 (about 3.0x / 4.5x). Open
leads: four workers are bound by the global graph lock (about 14% of worker
time, list operations over half of it; per-list locking needs an audit of
every list-internal access and a backend-transition protocol) and by
scheduler-mutex acquisitions (isolating its read-mostly words made waiters
spin more and was rejected); one worker by per-operation protocol (store_root,
refcounts, leases, TLS). Semantic gaps found on the way and still open:
instances of user subclasses of int/list/dict are not instances of their
builtin base (tag path and py_obj_isinstance), and non-ASCII `str.upper()` is
unchanged (`'é'.upper()` returns `'é'`). Commands, receipts, rejected patches
and the pre-existing failures met on the arms are in
`/Users/jiamo/pcc-recovery-archive/2026-10-29/claude-rounds/README.md`.

The 2026-10-30 continuation (rounds R21-R30, same README, receipts
`r31`-`r46`) measured directly from `2c794fc02` to `59e6a4cfa`: one worker
28,415 -> 31,953 QPS (+12.2% paired median, 7/7, cycles -10.2%), four workers
42,853 -> 45,424 (+6.0%, 5/7, cycles -4.8%), sync payload 254.7k -> 213.5k
instructions per request (-16.1%); same-packet asyncio 80,593 / 259,217 QPS
(2.5x / 5.7x ahead). Accepted: GC0 dict keys/values/items under one mode
reservation (`afdf69ae2`), threaded GC0 dict get/set in one graph transaction
(`a74cde431`), GC0 sorted() of plain values (`98c4204cf`), vthread
cancel/state reads without the scheduler lock (`c4086b07a`), module
namespaces published in their name-cache entry (`7c63b4f01`) and
py_err_occurred beside its TLS slot (`09b554212`). Rejected: thread-block
padding, lock-free empty timer/I/O checks and an 8-byte first-byte scan in str
find (an extra call per `in`; most payload strings are shorter than a word).
Correctness fixed on the way: the threaded GC0 dict/list/set fast paths read
and rewrote tables without the graph lock, and list insert, remove, reverse
and extend were not atomic on any collector (`6f66abc5a`, `20009e1ac`,
`a73f1e283`); contended list growth raised MemoryError after eight retries;
str.join of a shared list could copy past its allocation (`9499e09f6`);
`"".join` with a non-str item returned NULL instead of raising TypeError
(`59e6a4cfa`). Open, recorded and not fixed: GC3 with
PCC_GC_MINOR_HEAP_SIZE=4096 and PCC_GC_MINOR_ALLOC_MAX=128 aborts
`[DEBUG-dict-slot]` at exit, already on `2c794fc02` (repro in the README);
`pcc_gc_store_ptr` on threaded GC0 is an unlocked read-modify-write (two
concurrent stores into one field release the old value twice); a str
subclass key equal to an existing str key adds a second dict entry; the
general sorted() orders a list containing NaN unlike CPython's timsort.
Profile leads for the next rounds: teardown through root stores is about 23%
of the sync payload (list and dict deallocation), pin/unpin cost two atomics
each (4.7%), and an exact-list parameter guard query checks the TLS
exception twice around `py_obj_type_tag`, which cannot raise.

Fixed afterwards (2026-10-30, on the squashed history `93f1a039f`): shared
instance fields rebound by one carrier while others read them failed on all
five collectors (GC0 aborted `[BAD_INCREF]` 5/5, GC4 readers saw reused
objects 5/5); readers now load and retain inside a graph transaction, GC0
`pcc_gc_store_ptr` takes the store-plan transaction with threads enabled,
and delattr clears inside one (`5797d612f`). A str subclass hashes its
payload, so dict and set keys agree with an equal str, and a class with
`__eq__` but no `__hash__` raises CPython's unhashable TypeError
(`236c9d2df`). sorted(), list.sort() and py_obj_sorted run one port of
CPython 3.15.0's listsort, so NaNs land where CPython puts them
(`909160712`). Still open, with repros: module globals and class
attributes rebound while other carriers read them crash or misread like
the fields did (`tests/python/test_shared_slot_concurrent_access.py` has
the `global` and `class` shapes; a reader loads `@.modvar` raw and retains
after, and its borrowed value can cross a safepoint, so the fix needs owned
reads of rebindable globals in codegen, not a writer-side change only);
the GC3 `[DEBUG-dict-slot]` abort under
PCC_GC_MINOR_HEAP_SIZE=4096 PCC_GC_MINOR_ALLOC_MAX=128 is a forwarding
target reference released early or twice relative to the dict slot that
names its shell or copy, not a plain unhealed slot (both runs recorded in
the README's `open-gc3-dict-slot/NOTE.md`); int, float, bytes and frozenset
subclasses keep no builtin payload (`I(5) == 5` is False); `random.Random(5)`
and `random.seed` produce a different stream from CPython's MT19937;
unhashable dict and set keys raise hash()'s message instead of 3.14's
"cannot use 'X' as a dict key (...)"; `{}.pop([], 0)` raises instead of
returning the default; `set().add([])` inside a lambda fails to compile
("slot-call operand has no authoritative source or owned result: Call").

Rounds R31-R40 (2026-10-30, same README, receipts `r47`-`r59`) took back
what the shared-field fix cost, then reached the per-operation floor.
Accepted: only instance-field stores take the store-plan transaction and
GC0 field reads use one plain transaction (`af0b6cca0`; locking every GC0
store had cost 1w -7.8%, 4w -16.5%), lease-free exact-list guard tags
(`5dd9f7148`), a borrowed parameter searched for a literal (`efa97eb67`),
full-list growth inside the append transaction (`baf50e8eb`) and GC0
call-root pins for a take without the graph lock (`a10582529`). Rejected,
patches archived: lease-free tag reads in scalar slot calls, inline atomic
counts in `pcc_gc_store_root` (the self backend grew it from 259 to 400
instructions, 1w -3.0%), and an append's list type checked before its
transaction (neutral). Direct cumulative from `899c4fb79` to `d9c427060`:
one worker 29,066 -> 31,660 QPS (+9.4%, cycles -8.2% 7/7), four workers
37,801 -> 48,044 (+23.3%, cycles -19.3% 7/7); from the previous batch's
`59e6a4cfa` the net is +2.0% / -2.0% (noise), sync payload 213.8k ->
198.3k instructions per request; same-run asyncio 80,080 / 254,545 QPS
(2.5x / 5.3x ahead). Reassessment: per request pcc retires 487k
instructions at one worker against asyncio's 168k, and at four workers
spends 263k cycles against 105k at one (IPC 4.6 -> 2.5; asyncio 41k ->
55k). The sync payload (dict and json, no virtual threads) is 198k, so the
TaskScope/virtual-thread layer and coroutine frames carry about 290k of
the 487k. Of the four-worker inflation the graph lock is about 23%, the
virtual-thread scheduler mutex 18% (trylock spins and sched_yield), GC0
tracking 8% (global acq_rel counters `py_gc_tracked_count` and
`pcc_gc_tracked_node_pool_count`, plus 32 address-hashed shard locks every
carrier touches), leases and shared refcounts 5% each. Racy reference
builds without graph transactions on the GC0 container fast paths (append,
field load and store, dict get and materialize, list get) gain only 1w
+3.5% to +6.4% and 4w +10.5%, which bounds privatizing non-escaping
containers on this request. Leads by measured share: the per-request cost
of the virtual-thread layer and coroutine frames, per-carrier scheduler
queues, GC0 tracking counters and shard locality, then container
privatization.

Rounds R41-R50 (2026-10-30, same README, receipts `r60`-`r70`) followed the
GC3 heal (`4118634a1`) and GC4 direct-step (`26e754e70`) fixes. Accepted: a
dying object untracks only when tracked (`6c160920a`), collector
bookkeeping off the death and allocation path (`9f08463ff`), a carrier
queues the work it readies on its own queue instead of round-robin
(`a1c23bcb1`; 4w +5.1% / +3.5%, 5/7 both), and a GC0 field read of NULL, a
tagged int or static None/True/False skips the graph transaction
(`0e3e14a22`; 1w +0.4% / +1.9%, 4w +2.3% / +1.9%). Rejected, patches
archived: gated scheduler bookkeeping, fewer transactions in dict views
and `str.encode`, a scheduler ticket lock (4w -1.0% / -6.9%: FIFO handoff
convoys; it also needed its idle wait kept no-park, since a carrier leaving
an exited wait holds the idle mutex until a stop resumes), a per-carrier
run-next slot (4w -2.1% / -10.5%), a GC0 immediate-over-immediate field
store as one CAS (4w -3.5% / -8.3%) and per-shard tracked node pool budgets
(1w +0.8% over five runs, 4w median -1.2%). Direct cumulative from the
`26e754e70` runtime to the `185944a19` runtime: one worker 33,851 -> 34,907
(+2.5%, cycles -2.6% 7/7) and 33,566 -> 34,116 (+0.8%, cycles -1.6% 7/7),
instructions -2.1%; four workers 69,770 -> 69,513 (paired +1.2%, 4/7) and
66,452 -> 66,939 (paired +9.7%, 6/7); same-run asyncio 85-88k / 268-289k
(2.5x / 4x ahead). Scheduler lock acquisitions per request (one worker,
lldb by call site, 29.4 total): step finish 6.2, dequeue 6.2, start,
complete, join and join_result 3 each, sleep 2, timer and fd polls 1 each.
Four-worker profile after the locality change: graph-lock waiting 12.6%
inclusive (`_graph_wait_acquire` 9.5% self), by caller list append 23%,
field load 20%, dict get 12%; scheduler mutex 5.6%; idle carriers 5%. Every
GC0 container transaction takes the one lock word
`g_pcc_py_gc_minor_graph_lock`, which serializes all carriers. The
remaining routes are multi-week: per-object container locks or
privatization, per-carrier scheduler state, and P0 packages 4 and 5
(protocol-free compiled code, biased counts). GC0 pins and mode/slot
leases (about 343 and 281 calls per one-worker request, about 7% of self
time) exist for a runtime switch into a moving collector; removing them
changes that contract and is a maintainer decision (decided 2026-10-30, see
the structural program below).

Found in these rounds and fixed since by `8078b230f`: `a, b = vt.recv(r)`
and `s, r = vt.mpsc(n)` failed to compile ("slot-call Call lacks an
immediate owned-result handoff"). Failing identically
on unmodified arms: `test_gc_tracked_node_pool.py` expects `pool:4096` on
GC4, which tracks nothing under threads (GC0-3 print it);
`test_native_init_field_rhs_type.py::test_class_self_call_argument_types_reach_literal_dispatch_target`
(no `@py_list_get` in the IR); `test_freestanding_runtime_no_c_closure.py`
run from an arm (provisioning, and a `py_cleanup_runtime.o` record whose
source is missing). GC1 and GC2 do not collect every cyclic object with
`__del__` within 20 `gc.collect()` calls, so
`test_dead_object_untrack_skip.py` uses acyclic nodes. Repro: the archive's
`freeze.py <arm> <rev>`, `guard_build.py <arm>`, `check_workers.py <arm> 2`,
`guard_measure.py <base> <cand> <tag>` and `summarize.py <tag>.json`.

Structural program (2026-10-30, maintainer direction: do all structural
items, correctness first). Order: virtual-thread result unpacking, a fixed
collector, per-object container locks (P0 plan package 9: lists, then
instances, then dicts), per-carrier scheduler state (package 10), then P0
packages 4 and 5.
- `8078b230f`: owned virtual-thread results (channel, oneshot, receive,
  select, join, result, exception, sender clone, TCP receive) are published
  into the operand root waiting for them, so tuple unpacking and method
  operands compile; `test_vthread_owned_results_as_operands.py` runs GC0-4.
- `4bba76893`: the collector is fixed at the first managed allocation
  (`pcc_gc_fix_backend`: `pcc_gc_alloc`, or registering a raw
  `PY_FLAG_GC_MALLOC_ALLOC` object); data-segment images do not fix it, since
  C-ABI image constructors register literals before a C program's `main`
  selects. GC0-2 address leases are the no-op token 0 unless the provenance
  probe is on or the configuration is uninitialized; GC0 pins stay (cycle
  collector roots). In-process backend switching is no longer a contract
  (maintainer direction): switch-only tests were removed and switching
  probes now run one process per collector. r72 (hd53 -> fz56, host load
  8-10): one worker +0.15% QPS (cycles -1.40%, instructions -2.77%), four
  workers +0.33% (cycles -1.05%); same-run asyncio 80,836 / 259,450.
  Validation: the focused 32-file contract set, run from sealed arms with
  `PCC_TEST_NO_NATIVE_PROVISIONING=1`, fails identically on base and
  candidate; most of those base failures are `NativeProvisioningError`
  (tests that provision their own runtime), the rest stale IR/source-shape
  assertions (`test_gc_root_precision.py`, `test_gc_backend_generational.py`).
- In progress, uncommitted: container locks. The kernel has 512 striped
  0/1 words (`pcc_container_lock`, and `pcc_container_lock_pair`, which takes
  two stripes in index order for `list.extend(list)` and `a + b`); holders
  are no-park, never take the graph lock or a second container lock (both
  abort) and run no user code. `py_list.py` routes every list transaction
  through `_list_lock`, still the graph lock until `_lists_lock_themselves()`
  is switched on after the other modules that touch list internals
  (`sorted`, `str.join`, `bytes.join`, `pcc_list_snapshot_commit_slots`,
  `gc.callbacks`, tuple splat, generator frames) are converted and the
  checked-lease path (graph lock under the provenance probe) is kept out of
  container regions. Pre-existing GC0 races fixed on the way: `in`, `index`
  and `count` compared borrowed items with no lock, `copy`, `*` and slicing
  read items unlocked, and sanity checks read length and capacity before
  locking (false negatives under concurrent growth). Open: GC1-4 slicing
  still reads source items without a lock.

## Live 8082 TCP: arm1 preferred; normal traffic is silent (2026-10-29)

Current route, at the user's explicit request: **arm1:8084 first, us1:8089
backup**. The prior us1 preference described below is historical. Normal
transfers, EOF, resets and broken pipes now produce no connection records and
default logging no longer wraps stream read/write/drain/close or collects
per-chunk state. Assertions and other unexpected errors retain source traces;
upstream handshake failures remain errors even when the peer closes the socket.
The quiet-mode change was published as `f6445b1e0615922d22d8f70263851bc66785b096`;
116 related tests passed, and 44 logging tests cover the final failure-phase
filter. One optional long soak was not selected.

The 21:13 check found 89 log records / about 102 KB in 15 minutes, mostly client
resets. An actual-process sample spent 1504/1669 main-thread samples in kevent.
Same-route no-logging instances were also slow: two 8 MiB downloads timed out
at 25 seconds, about 0.057 MiB/s body throughput. Logged controls were
0.063–0.082 MiB/s; this does not support logging as the cause of the network
slowdown. Direct SOCKS tests then received 4 MiB via arm1 in 2.265 seconds,
while us1 timed out at 20 seconds with 2.38 MB. Both Tailscale peers were direct.
After switching actual 8082 back to arm1, 8 MiB downloads completed in 2.696
seconds (HTTP CONNECT) and 2.466 seconds (SOCKS5), with verified TLS and zero
normal-connection log records. A controlled malformed request still logged its
source frame. Config, source and run receipts are in
`/Users/jiamo/pcc-recovery-archive/2026-10-29/pproxy-8082-arm-first/`; final
logging activation and source identity are in the adjacent `pproxy-quiet-final/`.
The original October 25 assertion remains unresolved in #202.

A separate localization brackets the switch. Before it, 25 MB downloads via
8082 and via direct `t-us1:8089` ran at 0.029–0.042 MiB/s, direct arm1 at
6.9 MiB/s. us1's own egress fetched the same 25 MB at 20.9 MiB/s, but us1-to-Mac
SSH bytes over the direct Tailscale peer ran at about 0.018 MiB/s, and that peer
lost 8/200 ICMP echoes at 193 ms RTT (arm1: 0/200 at 42 ms). The us1 slowdown is
therefore in the Mac-to-us1 leg, not local pproxy or us1 egress; where in that
leg the loss occurs is unknown. HTTPS first byte took about 1.0 s on both routes.
After the switch (PID 53951), five alternating 10 MB pairs gave 8082
2.76/1.41/0.14 (timeout)/3.80/2.29 MiB/s and direct arm1 3.08/2.56/2.98/3.80/0.12
(timeout) MiB/s: occasional single-flow stalls also occur on direct arm1 and
remain unexplained. 8082 used 4–7.5% CPU during a 5.3 MiB/s transfer. Receipts:
`/Users/jiamo/pcc-recovery-archive/2026-10-29/pproxy-8082-us1-loss/README.md`.

On October 26 the user reported slow sustained TCP downloads and requested a Mac-to-us1 path
comparison before preferring `t-us1:8089` over arm1. The old 8082 process (PID
99560, about 33 days old) responded to local SOCKS greetings in 0.5–10 ms, used
about 30 MiB RSS, and had no CLOSE_WAIT pile-up. A 2-second sample spent 183/185
main-thread samples waiting in kevent. Sequential 8 MiB Cloudflare downloads via
old 8082 timed out twice at 25 seconds, at 0.138–0.154 MiB/s of body throughput.
A fresh process using the same arm1-first configuration also timed out, at
0.267 MiB/s. A fresh us1-only local process completed in 4.839 and 3.726 seconds,
at 2.059 and 2.867 MiB/s. This favors a path bottleneck over stale process state.

The LaunchAgent's two `-r` arguments were swapped, with all other settings
preserved: `socks://t-us1:8089` first, `socks://t-arm1:8084` as fallback. A scoped
bootout/bootstrap applied the change; new listener PID 8167. Actual 8082 downloads
then completed in 3.840 and 3.154 seconds, at 2.796 and 3.693 MiB/s body throughput.
Sockets confirmed new connections to us1; arm1 health checks remain enabled.
No process-rules capture, UDP mode, ProxyBridge or SIP change was made. Tests used
temporary local ports and bounded sequential downloads; temporary proxies exited.

Tailscale confirmed a direct Mac-to-us1 peer path, 172–182 ms, without DERP.
The visible underlay hops map to AS9269 (HKBN) and AS13335 (Cloudflare), with no
visible AS174/Cogent hop. Later hops did not respond and the return path was not
measured: a completely Cogent-free path is **not established**. The routing change
is a measured trial, not that stronger claim or a guarantee for every download.
Raw timing/path/ASN data, the live-process sample, original LaunchAgent backup,
applied configuration and rollback/reload receipts are archived at
`/Users/jiamo/pcc-recovery-archive/2026-10-26/pproxy-8082-us-first/README.md`.

Follow-up on 2026-10-29 at source `5551d9cf94f2ebcb945d940227d5938aa367af93`:
the reported 20:33:27–30 disconnect/refusal burst on October 26 coincides with
that controlled reload: SIGTERM at 20:33:27.575624, old-process exit at
20:33:27.646149, new-process start at 20:33:29.412658 (UTC+08:00; listener-ready
time was not logged). PID 8167 was still running at that check and its loaded
source hashes matched the proxy files. The earlier 18:10:55 failure was an arm1 SOCKS handshake
timeout, mapped to reply 4 / Host unreachable. The October 25 23:43:44 client
handshake AssertionError remains **unresolved** because the old log lacks its
exception location and request shape; track [#202](https://github.com/allstoalls/pcc/issues/202).
Nine current HTTPS probes across the three reported domains and live8082/us1/arm1
completed SOCKS and verified TLS; 22 focused handshake/error/lifecycle tests
passed. These do not qualify authenticated applications or prove the old
assertion fixed. No runtime/configuration change or restart was made during this
initial diagnosis. Receipts, source identity and reproduction commands:
`/Users/jiamo/pcc-recovery-archive/2026-10-29/pproxy-8082-errors/README.md`.

At the user's subsequent request, source `7a5fd07e7c834c1011383cb2a212e9cfdb3897ab`
adds the tracked `python -m pproxy.diagnostic` entrypoint and safe exception
tracebacks: phase, file/function/line, bounded cause/context chains and startup
source hashes, without messages, source snippets, locals or credentials. The
formerly ignored logging helper is now committed. Thirty logging tests passed,
including real SOCKS assertion/authentication failures followed by a healthy
tunnel; a clean export of the published commit passed 103 related TCP/TLS/HTTP2
tests (one opt-in soak deselected). Live 8082 was switched to that entrypoint at
17:01:38 UTC+08:00 and was SOCKS-ready 1.548 seconds later, PID 80773. Listener
and upstream arguments are unchanged; 8083 was not restarted. A controlled
malformed CONNECT logged the expected phase and exact source frame on actual
8082; subsequent SOCKS5 and HTTP CONNECT HTTPS probes both completed with TLS
verification. The original assertion remains open in #202 pending reproduction.
Source snapshots, tests, original/applied LaunchAgent plists and deployment/log
readback receipts: `/Users/jiamo/pcc-recovery-archive/2026-10-29/pproxy-diagnostic-logging/`.

## python-proxy process routing: implementation landed, installed gate open (2026-10-26)

The user broadened the requirement to general Proxifier-style process rules,
including `Antigravity.app`, `Antigravity`, `com.google.antigravity`,
`language_server`, and `agy`. ZeroOmega is independent; python-vpn is deferred.
Implementation is published in `shenming/master` through
`d5df54f0936bbcb9829e37dbdba51e9b5332b79e` with ikshengmin author/committer identity.
`--process-rules` binds the bundled native provider to the actual local SOCKS5
listener and preserves `-r` / `-ur` selection. Ordered process/path/app/Bundle-ID,
host/port and TCP/UDP rules support proxy/direct/block; UDP destination decisions
are per datagram. The controller owns only its network configuration and follows
the Python process lifecycle. There is no global DNS proxy or ZeroOmega dependency.

Final CPython 3.15.0/aioquic 1.7.0 validation: **177 passed**, zero failures/skips,
one optional soak deselected; process-group watchdog COMPLETE/0, peak tree RSS
431046656 bytes under a 1 GiB cap. New tests execute the native rule engine,
audit-token-to-process-path resolution, native SOCKS5 handshakes/packets through two
Python proxies, and the real Python CLI with a doubled OS controller. A native
controller check also reads a rules document larger than 64 KiB. These checks do
not execute NE flow interception inside the installed provider.

The arm64/x86_64 development package includes Python sources, its own controller,
system extension and example rules. Build/help/config/archive checks pass. It has
no NE activation entitlement and is deliberately refused by the activation path.
The current Mac has SIP enabled and zero valid signing identities; the new provider
has not been installed/activated. Real process coverage, WebRTC and HTTP3 through
this provider remain **unqualified**, tracked in
[issue #201](https://github.com/allstoalls/pcc/issues/201).
The original provider validation did not change live 8082/remote deployments,
Proxifier/ProxyBridge configuration, SIP, or system HTTP proxy settings. The later
8082 TCP upstream trial is recorded above. No native compiler/bootstrap claim.

Actual no-Apple-certificate activation probes were then run on macOS 26.5.1
(25F80), arm64, SIP enabled. Three unique apps under `/Applications` used ad-hoc
signatures: no app entitlements, installer-only entitlement, and installer plus
Network Extension entitlements. The real native controller was invoked directly,
bypassing the Python development-package guard. No-entitlement activation returned
`Missing entitlement com.apple.developer.system-extension.install`. Both entitled
variants were killed by SIGKILL before even `--help`; `amfid` logged
`AppleMobileFileIntegrityError -424: The file is adhoc signed but contains restricted
entitlements`. `xattr -cr` succeeded on the full variant and did not change either
failure. All variants passed cryptographic `codesign --verify --deep --strict`.
The temporary apps were archived and removed; system-extension inventory was
unchanged. SIP/AMFI settings and certificate trust were not modified. This proves
failure under the current configuration, not the outcome of a reconfigured
development system. Commands, PIDs, outputs, AMFI logs, entitlement files and
archived test apps are in the recovery archive's `no-apple-signing/README.md`.

Commands and operating boundaries: [macOS README](../projects/python-proxy/macos/README.md).
Durable source snapshots, hashes, test logs, the earlier CLI coverage gap and
the final development package are in
`/Users/jiamo/pcc-recovery-archive/2026-10-26/python-proxy-process-routing/README.md`.

## Earlier ZeroOmega WebRTC path and release-browser boundary (2026-10-26)

The requested scope includes Chrome TCP and UDP, WebRTC and QUIC/HTTP3, through
the existing local and remote python-proxy processes, without another local
proxy application. Full coverage is **not yet qualified**.

The ZeroOmega checkout at `/Users/jiamo/my/ZeroOmega` adds an opt-in Chrome
TURN/UDP page configuration hook on `jiamo/codex/webrtc-turn-udp`, published
through `cf2d8ba18209f0d81f3841e29f4a5e20b47593ad`. Eight Node tests and the
normal extension build pass. A real Chrome
156.0.8078.48 run with a separate temporary profile and random loopback ports
relayed generated audio/video and 128 KiB of data through the local UDP tunnel,
a second SOCKS5 proxy, and the new remote TURN service. Selected candidates and
TURN transport were UDP; the original page STUN detector saw zero packets.
Strict-CSP and wrong-credential controls also pass: invalid credentials produce
no allocation and no direct-STUN fallback. Receipts, source snapshot/hashes,
commands and limitations are together in
`/Users/jiamo/pcc-recovery-archive/2026-10-25/zeroomega-turn-udp/README.md`.
This proves that local WebRTC path, not arbitrary website/NAT compatibility.

The python-proxy changes based on `17ecf318fcb48029cc14c5f24cdf5f1c197d7049`
add authenticated TURN/UDP, configurable UDP association idle time, and an
experimental HTTP/3 CONNECT/CONNECT-UDP listener using the existing optional
aioquic dependency (tested with 1.7.0). TURN and QUIC can share one UDP port.
Published proxy/test code through `9bc98c740f1702f8f0a12980c25b8fd606055e97`
passes **157 tests**, no failures/skips, one optional soak deselected, using
CPython 3.15.0 and aioquic 1.7.0. Exact bounded invocation and live logs are in
the archive's `qualification/` directory.
The MASQUE TCP test exposed the existing FIN/half-close response truncation;
generic channel completion now preserves the opposite direction. Reset tests
explicitly send RST, and completion tests close their remaining write half.
Release Chrome 156.0.8078.48 does not expose general QUIC proxy routing:
`scheme: quic` reads back as HTTP, and startup-proxy controls produced HTTP/1.1
with zero MASQUE requests. Matching Chromium revision
`d0c02bfad08ec92a6ffcf989e53cd995e6381a4c:net/features.gni` explicitly sets
`enable_quic_proxy_support = is_debug`. HTTP fallback is not HTTP3 evidence.
ZeroOmega now restores the previous route on this downgrade and refuses
unsupported generated QUIC PAC directives. Its WebRTC feature remains opt-in.

The user subsequently authorized bundling a native macOS provider into
python-proxy and requested general process rules; see the implementation and
remaining installed-provider gate above. The earlier TURN/browser result is
independent of that work and does not qualify system interception.

## python-proxy SOCKS5 UDP (2026-10-25)

Validation input: `1a63d2b72edaa9bee782d069453e6633ce1a243e` plus the
`projects/python-proxy` patch preserved in the archive below. Standard TCP-controlled UDP ASSOCIATE now
supports direct and standalone SOCKS5 upstream routes, IPv4/IPv6/domain
destinations, authentication, response source addresses, and per-association
stable outgoing ports. TCP EOF releases the relay. Configuring `-r` without
`-ur` rejects UDP instead of accidentally using a direct exit; other upstream
protocols and `__` jump chains are explicitly rejected for these associations.
[Configuration and limits](../projects/python-proxy/README.rst) describe the
ProxyBridge setup and the retained standalone UDP listener mode.

CPython 3.15.0 local validation: **137 passed**, no failures/skips, one optional
soak deselected. This includes 43 UDP tests, independent SOCKS5 wire peers,
STUN mapping responses, 32 concurrent associations, API/CLI cleanup, and the
existing TCP/TLS/HTTP2 tests. Repro from `projects/python-proxy`:
`gtimeout 120s uv run --no-sync python -m pytest -c tests/pytest.ini -x -n0 -vv --tb=short tests`.
Durable source hashes/snapshot, bounded-run logs and scope:
`/Users/jiamo/pcc-recovery-archive/2026-10-25/python-proxy-socks5-udp/README.md`.
Actual ProxyBridge/browser WebRTC calls, remote upstream/NAT traversal, and
pcc1-native execution remain unverified. Existing proxy processes were not
restarted.

## Bounded synchronous tuple operands (2026-10-24)

Against `206553ae8732c91b35ed9d022a0708863cad41fe`, 1–8 element synchronous
tuple operands reuse the existing fixed call-tuple constructor instead of a
temporary list. Empty/wide/splat/generator paths and the runtime ABI are
unchanged. [Mechanism, guarded scope, tests and remaining qualification](research/fixed-tuple-literal-materialization.md)
are recorded together: 189 host checks pass on CPython 3.15.0rc1, with a
failing old-code negative control. This is host/model/IR evidence, not final
3.15.0 host, native-performance, five-GC or bootstrap qualification.

## P0 handoff triage and active follow-up (2026-10-23)

Source: published through `c74ed2ca8`, initially based on
`8b35e1e1c667dedfcbdcd385b2810f537ab29f8f`. Active worktree:
`/Users/jiamo/.codex/worktrees/p0-handoff-triage/pcc`. Pre-publication evidence:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/README.md`.
Subsequent completed-run receipts:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/followup-through-af72b15f6/README.md`.
Unpack execution and the first Stage1 timeout (including partial native objects):
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/unpack-through-46d994978-and-stage1-timeout/README.md`.
Large-integer unpacking and the byte-matched worker profile:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/bigint-through-b772b28be-and-worker-profile/README.md`.
Accepted arena work, both earlier denials, current Stage1 diagnostics and gateway evidence:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/arena-da4626652-gateway-f260a0c/README.md`.
Successful Stage1 and the first native integration memory failure:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/stage1-da4626652-native-memory/README.md`.
Generic augmented-assignment repair and integrated native execution:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/augassign-931933512/README.md`.
Generated enumerate-counter repair and exact bootstrap-worker replay:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/enumerate-732b7a9b0/README.md`.
Successful native Stage1, matching runtime and publication audit:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/native-stage1-732b7a9b0/README.md`.
Completed scoped native compiler integration:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/native-integrations-732b7a9b0/README.md`.
Nested unpack lifetime repair:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/nested-unpack-33a9f6823/README.md`.
Unpack arity, ordinary nested boundaries and exact iterator/exception ownership:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/unpack-through-6aa05f1af/README.md`.
Current threaded gateway baseline, measured binary, backend IR and caller profile:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/performance-baseline-60396e192/README.md`.
Successful updated Stage1 and matching runtime:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/native-stage1-8c53ef3c7/README.md`.
Accepted threaded lease improvement, native concurrency tests and current profiles:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/atomic-lease-1d43db0ac/README.md`.

The maintainer has now directed work back to the performance priority. The
latest unpack code and regression tests are published; continue measured
runtime/codegen optimization while retaining the unfinished qualification,
C lifetime and conformance tasks below.

The active goal is the full handoff/P0 scope, not this completed test subset.
The maintainer now explicitly authorizes timely commits and pushes of validated
work: commit working code, fetch/rebase and integrate `shenming/master`, push
normally to `shenming`, then commit tests and subsequent fixes separately.
Use direct Git commands, not the `do_commit` skill. Keep the `ikshengmin`
worktree identity, English `[skip ci]` messages, no Co-Authored-By, no force
push and no changes to `origin` or other people's uncommitted work. This
authorization supersedes the original goal text's wait-for-permission clause.
The maintainer explicitly requires local issue records only: do not write to
allstoalls repositories, including issues or comments. The attempted issue
creation was rejected before publication; no issue was created. That request
is resolved and must not be asked again.
Preserve both our unfinished changes and peers' work. Integrate conflicts by
their required behavior, test the resolved paths, and never replace an entire
conflicted file with ours/theirs to discard either side.

For future `pcc-gateway` changes, the maintainer specified the Git author name
`ikshenming` (not `jiamo`) and the only push destination
`https://github.com/ikshengmin/pcc-gateway`. Apply the same isolated-worktree,
timely commit, fetch/rebase/integrate, normal-push and no-force-push rules.
The PCC worktree retains its separately configured `ikshengmin` identity.

Incoming goal task received from chat `01a164c1-e0ec-7c72-996c-939ad5ccf05c`
("定位 pcc 与 C 的交互"): implement user-facing C interoperability with long-lived
C ownership and reentrant Python callbacks/GC, preserving object identity and
shared references. Retain the original P0 scope and priority order. Proposed
handle/scope/field/weak-handle/address-lease/buffer-lease/gateway API names are
design suggestions, not individually approved user requirements. First prove
an independent C object/library plus a user's Python program through the public
CLI; then address lease mode guards, stable handles, traced fields, buffers and
callback/thread/cancellation lifetimes through the five-GC slot contract.
Use the pcc-Python runtime and owned toolchain. The source chat inspected
`0df9b0b6a`; its IR/model findings and external-library probe are evidence to
recheck against this newer worktree, not current native qualification.

The maintainer also added integration-test success as an explicit goal
acceptance task. Run the integration scope in dependency order on frozen
sources after focused failures are fixed. Unexecuted, skipped or failing
integration cases remain unfinished; unit/IR-only passes do not replace
native pcc1 execution, bootstrap or five-GC evidence where those are required.

Goal clarification from the maintainer: "0.6%" means the native **pass rate**
on CPython Lib/test, not a 0.6% mismatch remainder. The only full run is
`/Users/jiamo/pcc-conformance/runs/2026-10-21-10ab6f7d0/summary.md`, source
`10ab6f7d0`, identity `4982cd2d7ae59935`: 166 / 29,398 CPython-passing cases
(0.56%), from 34,270 collected cases across 857 modules. There were 371 modules
without a baseline, 403 compile failures and 37 incomplete native runs. The
current full rate is unknown. The later 190-module decorator subsets are not
comparable denominators. Continue generic conformance fixes using
`scripts/cpython_conformance/run.py --modules ...`, the pinned source tree
`/Users/jiamo/pcc-conformance/cpython-v3.15.0`, and a source-matched runtime;
preserve baseline policy, case plans and exact run identities. This corrects
the original goal text's ambiguous "0.6% semantic inconsistency" wording.

All 15 failures listed in the handoff reproduce on both `d5ccb2404` and
`8b35e1e1c`, with `utils/` included and `PYTHONPATH` fixed to each snapshot.
The final frozen candidate passes 126 focused tests (3 integration cases
deselected), including all four handoff files:

- Direct, statically recognized `dict.keys()` set operators now materialize
  their operands through owning slots before generic binary dispatch. The
  early slot route had bypassed the existing keys-view conversion. The new
  emitted-program test covers all four operators, both operand positions,
  two views, evaluation order, RHS exceptions and rejection of ordinary lists.
  This restores direct-expression lowering; it does not implement live runtime
  view objects or view aliases.
- Set method and typed-int IR checks now follow the slot method dispatcher,
  ownership-transferring root stores, generic arithmetic slow paths, rooted
  parameter reloads and raw-i64 induction before or after memory promotion.
  The related collection-input checks validate immediate publication by the
  loaded-method protocol, which was already present in the base revision.
- The explicit `PCC_SELF_LINK=cc` oracle no longer asks `main` to publish the
  table synthesized only by the owned linker. Without publication, mapped
  frames retain their registered slots. Its original C-extension in-place
  division/fallback program now links and executes. The publication and table
  options participate in the frontend IR cache identity. This remains an
  external-link reference experiment, not native pcc1 ownership evidence;
  owned native-extension export anchors remain unsupported.

Subsequent published changes and executed evidence:

- Conditional value operands (`and`, `or`, if-expression arms) now spill the
  selected evaluation path into owning generator-frame roots (`ef98d1069`,
  test `6cd9611a4`). Chained assignments evaluate the RHS once and store targets
  in Python order, including parking subscript targets; the parser separates
  assignment chains from real walrus expressions, and temporary root aliases
  retain the value before owner cleanup (`0a5c7b4d2`, test `eea5ba690`).
- Nested keyword operands spill at their actual evaluation boundary; earlier
  `**` expansion keeps its mutation, duplicate-key and error timing
  (`fa74d662e`, test `6e88d9f3a`). The corrected generator/async/coroutine/vthread
  snapshot survey passes 442 distinct nodes across 50 files at `6e88d9f3a`.
  All 4,449 tracked snapshot inputs matched that revision; `utils/` was present
  and every child inherited the snapshot's `PYTHONPATH`. Integration variants
  were deselected. This result is not a current-head native compiler gate.
- Parked unpack assignment destinations now receive values from persistent
  frame owners before target evaluation (`43ecd4fd1`, test `46d994978`). The
  exact emitted program proves source values survive both parks, later targets
  observe earlier stores, and a discarded value is finalized before the next
  target's side effects. It passes with the matching runtime; 19 related
  chain/conditional/keyword/unpack/ownership checks also pass (3 native variants
  deselected). Broader nested/starred unpack shapes still need execution proof.
- Unpack element loads retain arbitrary-precision Python int objects until the
  destination requests an explicit machine projection (`779ae5285`, tests
  `b772b28be`). The large parked case previously raised OverflowError during
  int64 conversion. List/list-target, tuple, set and starred prefix/suffix
  cases now execute correctly, alongside float controls. Three compiled
  programs pass all five collectors (15 executions) with host Python/pcc
  unavailable during execution. These remain host-pcc-produced artifacts;
  their native-pcc1 compiler variants have not run.
- The C build API accepts independent owned object inputs (`143bf762a`, test
  `cc9ed4233`); the public module/console Python commands pass explicit `.o` and
  `.obj` files to the owned linker (`77901d162`, test `f8cd10ffd`). Emitted
  executables call independent C and print 42 with host Python/pcc unavailable.
  Replacing an object without changing its mtime invalidates the module run
  cache and produces -4. CLI contract and sensitive checks: 27 passed,
  3 deselected. Native pcc1 and archive/dynamic-library surfaces remain open.
- Both GC index tests now use the owned C path (`879dab905`, `af72b15f6`).
  The standalone concurrency harness links its index object and stubs, with
  external subprocess builds and full-runtime provisioning forbidden. The
  separate retirement probe intentionally uses the real pcc-Python runtime,
  initializes and reports GC0/GC4, and checks selected-backend output. The final
  exact-source run passes both tests (`logs/index-retired-v4.stdout`).
- The received C-lifetime task now has a native runtime control at `af72b15f6`:
  GC0 acquires token 0 and permits a same-mode reset while a foreign address
  lease is live; GC4 acquires token 1 and correctly returns BUSY. The owned C
  build forbids external processes and runtime provisioning. This confirmed a
  mode-reset guard gap, not a native use-after-free. Collaborator `65b810d32`
  now counts initialized nonmoving leases; it was integrated intact before
  our publication. Replaying the independent owned-C probe at `931933512`
  gives GC0 token 2 and GC4 token 1, both BUSY during the lease and successful
  reset after release. This closes that reset probe; broader C lifetimes,
  callbacks and the performance of the changed protocol remain open.
- Generic name augmented assignment retains both operands and publishes the
  replacement through owning roots (`4c16d3cb1`, test `931933512`). Later
  inferred-Dyn `bytearray +=` operations previously overwrote the pointer
  without releasing the old buffer. Entry parameter analysis now also sees
  AugAssign, so a conditional update cannot leave the non-updating edge with
  an empty local. The new regression checks replacements, aliases, RHS result
  identity, exceptions, finalizers, both parameter branches and bounded GC0
  live memory; its emitted program runs on all five collectors. Focused checks
  pass 14 cases (4 pcc1 variants deselected), plus the existing five-collector
  inplace-alias integration. The integrated peer/runtime snapshot repeats the
  new exact regression. Native-pcc1 compiler variants still await qualification.
- Generated enumerate counters now use normal assignment ownership
  (`a967618af`, tests `732b7a9b0`). The prior alloca/store did not register a
  boxed counter; strict root-based augmented assignment exposed that omission
  during bootstrap. Seven focused checks pass (3 pcc1 variants deselected),
  including both scaffold modes and all five collectors for continue, loop
  re-entry, nested/whole-pair targets and suspended generator resumes.

- Nested private unpack containers are consumed before their targets run
  (`fa074fe61`, tests `33a9f6823`), preserving finalizer order when inner or
  outer destinations park. User-held aliases and later target failures remain
  correct. The scoped packet passes 8 tests (7 native compiler variants
  deselected), including five-GC execution of the two lifetime programs.
- Managed sequence unpack now checks arity before any target in that boundary
  (`f546f38e6`, tests `2110127e2`), using exact list/tuple loads or the ordinary
  iterator protocol with at most N+1 values. Error text matches CPython 3.15;
  custom iterator exceptions keep identity. Static helper signatures were
  synchronized in `6a47cdf0b` after the existing ABI check found the omission.
  Ordinary nonparking nested boundaries also use the checked helper
  (`60396e192`, tests `6aa05f1af`). The final packet passes 5 tests, each
  executing all five selected collectors (5 native compiler cases deselected).
- Adjacent checks passed 69 cases before an older lifetime test's fixed TLS
  raise-count assertion failed. The same failure reproduces before this repair.
  `71b8cc5f8` checks each of 50 complete allocation/raise/clear/free histories
  instead, allowing balanced internal TLS restoration: all 3 tests pass.
  Four iterator integration programs also pass all five collectors through
  host pcc (4 native compiler variants deselected). These checks cover exact
  errors, default evaluation, aliases, finalizers, filter and generator paths.
  A separate repeated-error probe has zero live-byte growth on GC0-3 and
  +21,408 bytes on GC4 after its first large batch. Bounded collector capacity
  versus a leak is still unclassified; its failed observation is retained.

These are host-pcc/self-backend emitted execution and focused IR/cache checks.
The first native pcc1 integration build used a frozen `af72b15f6` snapshot:
4,457 tracked inputs matched, with the runtime hash in `integration-source.json`.
Its shared-lock/process-tree-guarded Darwin Stage1 run reached its original
1,800-second limit after producing 323 direct objects, without producing pcc1.
Peak tree RSS was 3,367,763,968 bytes, below its admitted cap. The run is in
`/private/tmp/pcc-p0-followup/bootstrap-darwin`; live log:
`/private/tmp/pcc-p0-followup/logs/bootstrap-darwin.stdout`. Timeout cleanup
removed the shared export file and some worker manifests; the partial state
alone cannot replay the original context. The normal coordinator's export/AST
context was reconstructed and its `method_call_expression_lowering` worker
replayed under cProfile. The result matches the original module 308 PCO byte
for byte. Instrumented time was 201.05 seconds, peak RSS 3,173,892,096 bytes;
`_native_managed_liveness` takes 85.45 seconds inclusive and `append_zeros`
40.65 seconds inclusive. Total list.append calls are 346,136,083. These are
one instrumented worker's costs, not whole-build wall shares. An initial bounded host
zero-fill candidate completed three alternating uninstrumented A/B pairs at
`/private/tmp/pcc-p0-followup/arena-worker-ab`: all objects are byte-identical,
median wall speedup is 1.083x and instructions decrease about 10.5%, but one
candidate peak is 13.9% above its paired baseline, so the strict result is
DENY. A similar peak occurs in a different baseline run. The predeclared
fixed-`PYTHONHASHSEED=0` repeat also remains DENY; both packets are preserved.
The revised implementation (`c0f96bdd4`, tests `da4626652`) initializes an empty
host plane by in-place list repetition, retaining aliases and avoiding repeated
growth; nonempty spans use bounded scratch and custom containers retain their
append callbacks. The native memset path is preserved. Three new alternating
pairs pass the unchanged thresholds: paired median speedup 1.094x, about 10.6%
fewer instructions, all PCOs byte-identical, and worst paired RSS ratio 1.014
under the 1.02 limit. This is a host-worker component result. Final boundary
and PCO controls pass 24 tests; the real production arena source runs on all
five collectors and explicitly reports each selected backend. After correcting the
new probe to compile the actual private class source (ordinary app discovery
does not bundle compiler internals), native arena execution, record inventory
and liveness controls on the earlier prototype passed 132 tests, with one pcc1
variant deselected.

The second full Stage1 at frozen `da4626652` also timed out at 1,800 seconds,
after 220 direct objects, without pcc1. Its peak tree RSS was 3,201,253,376 bytes
under a 6,059,958,272-byte cap. The full 482-worker AST/export context was
retained before cleanup. The admission audit shows about 1.77 GB startup
reservations even for small modules. A bounded probe of the existing host
indexed-process split reproduces one heavy and five smaller modules with
byte-identical PCOs under no larger budget. A new Stage1 uses that explicitly
selected process topology, with the same source/runtime and 1,800-second limit:
`/private/tmp/pcc-p0-followup/bootstrap-host-split`, live log
`/private/tmp/pcc-p0-followup/logs/bootstrap-host-split.stdout`. It completed:
compile 1,350.455 s; total including native smoke execution/refcount audit
1,411.759 s. The `pcc1` SHA256 is
`27e2e0472732949c31b43392920761aefe51a8a75869560842db8af2e891d287`.
This proves Stage1 at `da4626652`, not a fixed point.

The resulting native integration packet failed on its first keys-view set
operator compile at the 8-GiB memory cap; no case passed. Sampling a lower-cap
replay located growth in `transcode_runtime_table`'s output concatenation.
The real 2,868,008-byte table reproduced independently: the unchanged native
transcoder crossed 2 GiB in 3.072 s. Live-byte measurements and emitted IR
isolated the generic augmented-assignment ownership omission above, rather
than a blanket allocator or bytearray-growth leak. With the fix, the original
transcoder source produces all 951,588 bytes and 6,472 address offsets exactly
like the host reference, at about 32 MB native RSS. The same proof passes with
collaborator `65b810d32` included and a freshly matching runtime.

The frozen `931933512` Stage1 stopped at 64.729 seconds: codegen of
`self_backend_parse` rejected an unregistered generated `enum_i` root. No pcc1
was produced. The minimal string/list loop reproduced it with scaffold on
and off. After the counter repair above, the actual current bootstrap AST and
export context was captured and that exact module replayed through the host
process split. It now emits a valid owned PCO, SHA256
`e85b6fa155d691a8e4e4f80e8a39789583567e029cc9660a591646ac0e49948a`.

The fresh `732b7a9b0` Stage1 completed successfully in
`/private/tmp/pcc-p0-followup/bootstrap-enumerate`: compile 1,471.704 seconds;
native publication smoke compilation/execution and refcount audit 64.342
seconds; total 1,536.046 seconds. It retained the 1,800-second stage limit and
6,059,958,272-byte cap; the outer watchdog also covered publication and observed
peak tree RSS 4,314,382,336 bytes. The 769,243,768-byte pcc1 SHA256 is
`3bf2ae625fac980e0d92769b25fa50bf4b1d429c527008c8ea28dcda750d5140`.
All 30,732 tracked inputs match the published commit; source and runtime hashes
are in `enumerate-published-source.json`. This is Stage1, not a fixed point.

Updated Stage1 at `8c53ef3c7` also passes: 4,476 scoped tracked inputs;
compile 1,754.307 seconds, native publication smoke compilation/execution and
refcount audit 68.785 seconds, both return codes zero. The compiler retains
its original 1,800-second limit, and the outer 1,980-second watchdog also
covers publication. Lowering the admission ceiling to 5,000,000,000 bytes
allowed the run while preserving the 8 GiB host reserve; observed outer peak
RSS is 2,929,623,040 bytes. The preceding admission refusal launched no compiler.
The 839,638,296-byte pcc1 SHA256 is
`1ef48c0d2652413cfa4db5935008947457b0d06f0ba69382a174f262c0a342a1`;
its only dynamic dependency is libSystem. This includes the unpack and lexical
call-root changes, but not the subsequent atomic-lease runtime change. It is
Stage1 and its publication gate, not a new-source fixed point or replay of the
14-case integration packet below. Source/compiler/runtime receipts are archived.

The scoped integration packet completed at `732b7a9b0`: 14 passed, 77
deselected in 948.10 seconds. Thirteen cases explicitly use that native pcc1
and one is a host collection-input control; every emitted program executes.
Host fallback/provisioning is disabled for native cases. It covers the original
keys-view memory failure, collection inputs, conditional/chain/keyword parking,
small and big integer unpack, compiler arena, augmented assignment, enumerate,
and public CLI linking of an independent owned-C object. This does not certify
native C compilation itself, newer unpack fixes, Stage2/Stage3, or all integration
cases. Durable source/compiler/runtime receipts are linked above.

Gateway work is isolated at `/private/tmp/pcc-p0-gateway-task/pcc-gateway`.
Code `13cd35b`, tests/fixture `f260a0c` and documentation `ca9a012` use
`ikshenming <shenming2020@gmail.com>` and the authorized `shenming/main` fork.
Active handler/layer/lifetime runners and the default test fixture select the
threaded atomic runtime. Host controls pass 34 and 55 tests, and real archive
admission succeeds. The earlier one-repeat C100/zero-wait smoke validates 1,000 requests
per arm: host-pcc 11,932.6 req/s versus CPython 3.15 asyncio 94,803.6 req/s.
The pcc1 arm is explicitly omitted; these are handler counts, not HTTP QPS or
a qualified multi-repeat comparison. Caller-attribution sampling of the same
native binary validates 500,000 requests. Its only dynamic dependency is
libSystem. Refcount/root/graph-lock/TLS costs are spread across runtime calls;
the encoding helper is only about 3.6% inclusive, so it cannot explain the
whole gap. No gateway throughput win is claimed.
The new performance baseline uses core `60396e192` (test-only head
`6aa05f1af`), gateway `ca9a012`, the source-matched threaded atomic runtime and
CPython 3.15.0. Five rotating repetitions each validate 20,000 requests plus
200 warmups at C100/zero wait, one carrier/event loop: host-pcc median
8,776.873 req/s (8,668.982-8,926.499), asyncio 79,708.930
(74,987.639-85,852.263), a roughly 9.1x gap. This is handler throughput,
without HTTP/socket I/O, not a native-pcc1 compiler or fixed-point result.
The measured binary SHA256 is
`0c8be1aaddb18277636f52340a11fd1d75256bb58741b66aa1d4f7eb95131022`.
Recompiling with actual backend IR capture produces that identical binary and
runs another 1,000 verified requests. The selected pipeline is owned
mem2reg+sroa, owned object emission, and in-process owned Mach-O linking.

A five-second sample of that binary completes 200,000 requests: 4,187 samples;
graph-lock acquire/release self time 13.93%, TLS lookup self time 5.16%, JSON
dumps 37.38% inclusive. Foreign lease paths cover 16.93%, root stores 16.34%,
and scheduler root registry paths only 3.30%. Inclusive shares overlap.
Current registry nodes are pooled, so the historical malloc-per-registration
explanation is not current evidence. Continue with the measured generic
ownership/call costs and controlled pass effects; no performance win is claimed.
The explicit six-pass owned application experiment (adding instsimplify,
instcombine, dce and inline-defined) completed 21 rotating baseline/candidate/
asyncio runs. Candidate median throughput is only 1.50% higher and process
instructions fall 0.064%; median user CPU is unchanged. Keep production pass
selection unchanged. Receipts:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/owned-tier-experiment-60396e192/README.md`.

The immediate-value lease experiment uses the same `117f59596` baseline on
both sides, including peer cache fixes `daa2dd2b3` and `8f62fb3e3`. It skips
lease locking for NULL/tagged words while preserving the heap path. Correctness
passes 123 focused checks, native immediate execution on five GCs, threaded
GC3/GC4 overlapping leases/movement, and independent owned-C heap mode guards
on GC0/GC4. Seven paired gateway repetitions show no meaningful throughput
benefit (median paired ratio 1.00235; instructions -0.325%; user CPU unchanged).
The experiment is fully recoverable in the local archive and is not landed as
a performance repair. Continue with heap-object call/ownership boundaries:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/immediate-lease-experiment-117f59596/README.md`.

Synchronous call scratch roots now stay lexical inside generator regions proven
not to suspend (`3b01d4f5c`, tests `27d8da7be`). Existing caller result sinks and
values crossing parks remain persistent, and an unexpected emitted resume edge
fails compilation. The new program passes five GC executions; nine adjacent
parking/ownership cases pass, followed by a 32-case focused packet (two native
compiler variants deselected). Two independently reproduced stale IR assertions
were corrected without dropping their ownership/order or adapter checks.

The gateway request's actual backend IR has 166 -> 116 frame-set sites,
269 -> 217 allocas and 1118 -> 745 stores; static counts include error paths.
Two measurements reduce instructions about 2.5-2.6% and cycles about 2.2-3.9%,
with no observed RSS regression. Wall throughput is provisional: the first
seven-run packet is highly variable and has a lower candidate median; a short
repeat has positive pairs but its first baseline overlaps a short independent
IR-only check. Do not call this a qualified throughput win. This accepted
structural reduction does not close the roughly 9x gap. Source/runtime/binary
identities, both timing packets and exact execution logs are preserved in:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/lexical-call-3b01d4f5c/README.md`.

Threaded nonmoving address leases now reserve an atomic mode guard before
reading their object, recheck the mode after reservation, and retain LIVE
allocation provenance checks (`1d43db0ac`, tests `c74ed2ca8`). Backend transitions
reserve zero -> -1 atomically; exact-table/malloc owners and tracked object-node
counts retain their graph-lock protocol. The executed two runtime files match
the published change after preserving peer commits `2a15c9d1e` and `6774b7488`.
Validation passes 184 model/IR checks, 120,000 real threaded lease cycles per
build, independent owned-C mode guards and call/finalizer programs on all five
GCs, plus threaded GC3/GC4 movement/overlap. The permanent native integration
test passes another 120,000 cycles.

Seven rotating full-workload repetitions (21 runs) use the same frozen
`8c53ef3c7` baseline, candidate runtime patch, gateway source, C100/zero wait,
one carrier/event loop, 20,000 measured requests and 200 warmups each.
Baseline median is 9,729.311 req/s, candidate 10,816.734 (+11.18%), and asyncio
91,767.184. Every paired native improvement is positive (1.0864-1.1223).
Process instructions fall 9.31%, cycles 10.19%, user CPU 2.07 -> 1.86 seconds;
RSS medians are 10,207,232 -> 10,240,000 bytes. The same-packet gap is still
about 8.48x (baseline 9.43x); the older 9.1x packet is a separate measurement.
This is a qualified handler improvement, not the required asyncio win or HTTP
throughput. A subsequent actual native pcc1 run (compiler built from `8c53ef3c7`,
SHA256 `1ef48c0d2652413cfa4db5935008947457b0d06f0ba69382a174f262c0a342a1`)
compiles this full gateway workload against the matching optimized runtime in
212.250 seconds with host fallback and external PATH tools disabled. Its output
executes 1,000 verified requests plus 200 warmups and is byte-identical to the
measured candidate: `d1286296a8ecc0d794e3dbfd64c516f2cff37b4bd513a6654ac237158c29b568`.
Only libSystem is dynamically linked. This establishes native-compiler production
of the measured binary; it does not establish a new-source compiler fixed point.

Ordinary programs reproduce the overhead without async: two warmed N=1,000,000
operation packets show instruction ratios versus CPython of 9.13–9.14x for
object-return, 8.82–8.87x for tuple construction/unpack and 5.54–5.55x for list
append/pop. Integer addition uses 0.54–0.55x CPython's instructions. Outputs
match for all eight operation shapes. The initial N=100,000 wall deltas include
first-execution effects and are explicitly rejected as timing evidence. A
50,000,000-iteration object-return profile completes its checksum: construction
20.40%, append 20.84%, root stores 39.59% inclusive (30.74% through release and
deallocation); these shares overlap. Native-pcc1 and ordinary-operation receipts:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/atomic-lease-1d43db0ac/operation-costs/README.md`.

Diagnostic ablations complete 35 runs on the candidate: no JSON with direct
payload validation reaches 26,010 req/s versus asyncio 108,272; no child tasks,
but retaining result construction/JSON and outer batch tasks, reaches 15,434
versus 258,715. These change validation and continuation shapes and are not
additive phase timings or replacement acceptance results. A new five-second
sample completes 200,000 verified requests (3,828 working samples): root stores
18.18% inclusive, lease paths 10.61%, graph-lock self time 9.07%, TLS self time
3.74%, JSON object encoding 34.04% inclusive. Inclusive shares overlap. Continue
into generic string/container/ownership costs while retaining the scheduler gap.

The included C1 event census (one versus eleven rounds, two warmups each)
observes about 96 allocations/frees, 357 retains, 453 releases and 1,968
root/field stores per request. Instrumented counts are not C100 timing evidence.
The earlier three small candidates produced limited gains; the subsequent
atomic lease change above addresses a measured shared ownership cost. Further
runtime/call and continuation work remains necessary for the end-to-end goal.

Two generic machine-integer lowering defects found while preparing the frame
constructor are now fixed. `8c8e16818` restricts exact-int local planning and
assignment boxing to ordinary Python int; tests `63acfc429` cover raw augmented
parameters, local loops, destructuring and signed/unsigned wrap boundaries.
The prior binary raised OverflowError at explicit i64 max + 1 on GC0–4; the
fixed emitted program passes all five. Three module-context IR checks plus the
native node pass, followed by 67 related integer/ownership checks and the two
original native literal/arithmetic cases on GC0–4. Ordinary bignum values retain
their exact results. Evidence:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/machine-locals-8c8e16818/README.md`.

`bc095095a` prevents ordinary-module boxed comparison dispatch from consuming
explicit i64/u64 operands before the existing primitive comparison path. The
baseline natively miscompares u64(0) < u64(2**63). Tests `cc801fef6` cover all six
operators over 25 signed and 16 unsigned boundary pairs, raw loops and ordinary
bignum controls on GC0–4; all 246 boundary comparisons pass. The three context
IR tests contain direct signed/unsigned comparisons and no integer helper calls;
11 sensitive comparison checks pass. The test fixture now uses the central
provenance-checked owned runtime and models the existing NotImplemented global.
Evidence:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/machine-comparisons-bc095095a/README.md`.

These integer checks used frozen ec1a76a3e plus the named patches, host pcc and
the owned self backend; emitted programs ran natively without libpython.
Conflict-free peer backend changes were preserved during rebase, not claimed
as re-tested by those snapshots. New-source pcc1 parameters remain deselected.
No end-to-end gateway speedup is claimed for either integer fix alone. The
rooted bulk frame candidate subsequently passes 48 failure/ownership models,
43 coroutine/frame/lifetime checks (1 pcc1 parameter deselected), and both full
handler artifacts execute correctly on GC0–4. Seven rotating C100/zero-wait
pairs improve median 10,014.571 -> 10,735.338 req/s (+7.20%); instructions fall
6.32% and cycles 6.28%. All seven pairs improve. Same-packet asyncio is
83,392.693 req/s: gap 8.33x -> 7.77x, not a direct comparison with the earlier
8.48x packet. This is host-pcc/owned-backend native emitted execution. The
candidate used the explicit bulk-init flag; production default remains unchanged.
Default activation was prepared only in the isolated experiment and has not run.
Evidence:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/bulk-frame-ec1-v4/README.md`.

The maintainer then prioritized ordinary synchronous primitives before further
combined-workload optimization. Keep the frame candidate and original P0 scope;
continue from object return and tuple construction/unpack, with per-operation
allocation/ownership counts, actual caller/IR inspection and one proved change
at a time. The first PCC_LOG census reuses the exact previously measured native
operation binaries, GC0, N=100/110. Per extra iteration, object-return logs one
managed allocation/free, 14 root/field stores and 3/4 heap incref/decref events;
tuple construction/unpack logs one allocation/free, 19 stores and 2/3 refcount
events. The integer-loop control logs two stores and no managed allocation.
Loop/control work is included; raw buffer allocations and all lease/pin/provenance
calls are not comprehensively covered. These are event counts, not CPU shares
or timing evidence. Trace the repeated maintenance around each object before
assuming excessive object allocation or building an escape-analysis framework.
Evidence:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/ordinary-ownership-census/README.md`.

Boxing/unboxing is now an independent ordinary-program performance item.
Code `a56892162` preserves an already-emitted i64 argument even when its AST
still says Dyn; previously `i64(value)` could box a machine integer only to
immediately unbox it. Small values paid tagged conversion calls; wide i64
values also allocated an unreleased bigint temporary. Object/CPython pointers
retain their checked conversions. Tests `bf16b4241` exercise signed/unsigned
extremes, object conversion, bigint overflow and user exceptions on GC0–4.
Three context IR checks pass. The existing mapped-root assertions were updated
separately in `f50ac3682`; 40 distinct sensitive checks pass across the recorded
packets, including five boxed-argument native programs across GC0–4.

Independent frozen A/B at cbb95dc0a, host pcc0/owned self/native emitted execution,
threaded atomic GC0, three rotating warmed N/2N pairs: small roundtrip instruction
cost falls 605.216 -> 565.251 per iteration (-6.60%); wide-i64 roundtrip falls
2009.884 -> 564.937 (-71.89%). Direct-call controls stay at about 565 instructions.
Wide baseline peak RSS grows 56.2 -> 105.8 MB between 500,000 and 1,000,000 calls;
the fixed program remains about 6.6 MB. The actual emitter IR removes both
integer helpers. No list-construction patch is present in these arms. There
are no virtual-thread spawn/join/await operations: GC safepoints and roots must
not be conflated with virtual-thread task scheduling. These are conversion-loop
results, not a new gateway speedup or new-source pcc1/fixed-point qualification.

The sensitive sweeps are not fully green. Baseline and candidate both reproduce
an invalid `__int__` result being accepted (strict xfail in the new test),
`test_cast_of_dynamic_tuple_element_preserves_container_owners[pcc0-cast-False]`
failing `freed == 2`, `test_typing_cast_preserves_raw_pointer_domain[False]`
failing the return boundary, and a scaffold constant test failing its old
`call.slot.take.current` assertion. Preserve these local actionable failures;
no upstream issue/comment was posted. Complete commands, source/runtime/binary
identities, all successful and failed checks, and read-back-verified receipts:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/boxing-a56892162/README.md`.
Continue measuring required boxing and object-to-machine conversion separately,
then ordinary container ownership, before the full-request acceptance rerun.

The maintainer explicitly added virtual-thread scheduling performance and
multi-thread scaling as separate performance items. Ordinary primitive and
boxing work remains active. Current gateway ca9a012's run_until_complete calls
`virtual_thread.run(1, 4096)`: C100 means request concurrency, not 100 native
carrier threads. Core `py_virtual_thread_run_carrier_pool` selects the idle
driver at count<=1; larger counts start/join native carriers, while a separate
persistent-pool API exists. Future scheduler comparisons must separate pool
startup from steady execution and use equal actual OS-thread counts, covering
spawn/join, yield/resume, park/wake, queue contention and cancellation/errors.

A first ordinary-thread packet reuses the existing `mt_fib.py` and the frozen
boxing candidate's exact binary/runtime. Small 1/2/4-thread output checks pass.
Three rotating, warmed fixed-work repetitions (48 fib(24) jobs), GC0, host pcc0/
owned-self/native emitted execution, measure 0.8689 / 1.0504 / 1.5833 / 2.4204 s
at 1/2/4/8 native threads. User CPU increases 0.85 -> 16.30 s and instructions
16.05 -> 49.66 billion from 1 to 8 threads. Every result matches the GIL-enabled
CPython 3.15 reference. This demonstrates degradation for this corpus; it is
not a virtual-thread benchmark, a whole-gateway result or new-source pcc1 proof.

Five-second samples of the same binary attribute 12.67% / 54.68% self samples
to object-graph lock acquisition at 1/8 threads; inclusive shares are 16.76% /
74.44%. The 8-thread lock subtree includes safepoint spinning, and its major
caller is foreign-lease acquisition (63.91% of all working samples). Source
uses CAS retry plus safepoints on contention. Classify actual lease operands
and hot IR next, preserving moving-GC/mode-transition and ownership contracts.
The earlier immediate-value lease experiment had no gateway benefit; that
historical result is unchanged by this different multi-thread workload.

Eight-thread raw capture succeeded and printed a complete workload checksum,
but profiler postprocessing timed out; its original receipt remains unvalidated
and the sampled run's final exit code was not retained. Existing uninstrumented
measurement exits/checksums all passed. Tool `df66a6636` removes quadratic
call-tree suffix copying, with tests `96a974cf1` (27 pass); offline recovery
validates binary/image identity and exactly reproduces the earlier single-thread
folded counts. The 8-thread raw tree folds in 0.413 s. Durable source/artifact
identities, raw capture, recovery limitations, all runs and the expanded tasks:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/threading-scaling-boxing/README.md`.

`5d47d3bed` now bypasses address leases for NULL/tagged root words; tests are
`d4eaa406c`. A byte-identical replay of the measured mt_fib binary establishes
the actual hot IR: each non-leaf call leases its two tagged recursive results.
The old entry reserved/released the atomic mode counter and then took the
object-graph lock just to return token 0. The new entry checks only the scalar
representation; heap provenance, mode guards and post-parking reloads remain.
This change is accepted for measured ordinary-thread benefit. The historical
no-gain gateway result for the earlier immediate-lease experiment is unchanged.

209 models pass. Three native tests cover 60,000 immediate acquire/release
pairs per run on two threads across GC0–4, real GC3/4 movement/overlap and GC0
heap leases racing mode resets. The full original request integration also
executes 1,000 requests plus 200 warmups correctly on each GC0–4. Frozen host
pcc0/self-produced native artifacts were used; current-source pcc1 and fixed
point remain outstanding. No new gateway QPS result is claimed.

Five rotating repetitions at 1/2/4/8 threads, fixed 48 fib(24) jobs, all outputs
checked: baseline medians 0.8613/1.0374/1.4441/2.0567 s become
0.6579/0.4352/0.4709/0.7203 s (1.309x/2.384x/3.067x/2.855x). Instruction counts
fall 21.31%/27.82%/43.25%/57.93%; all 20 native pairs improve. Same-packet
CPython medians are 0.1476/0.1457/0.1447/0.1437 s. Application hot IR is
identical across native arms, whose runtime differs at this one entry only.
Native execution remains slower and scaling still regresses past two threads.

Both post-change profile executions finish with correct checksums. The lease
lock hotspot disappears; remaining 8-thread graph-lock time is 68.78% inclusive,
called by generated return cleanup (36.16% of working samples) and root_move
(32.62%). Inspect/prove scalar root transfers and return cleanup independently
next; do not conflate a similar profile percentage with unchanged absolute work.
Full provenance, A/B data, checks, actual IR and before/after attribution:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/tagged-lease-5d47d3bed/README.md`.

The first scalar root_move prototype is rejected and was never published.
It bypasses the public graph transaction based only on NULL/tagged source bits.
Although 84 models, private-slot native GC0–4 stress and heap movement/handoff
checks pass, a new deterministic shared-destination interleaving loses a value:
baseline returns [0,-4], preserving the second source; prototype returns [0,0]
and clears both sources while the destination contains only one result. The
public registered-root contract does not prove exclusive destination access.
Production root_move therefore keeps its existing atomic transaction.

The prototype's five-repeat 1/2/4/8-thread medians (0.6552/0.4499/0.5128/0.8171 s
baseline versus 0.5761/0.3833/0.4011/0.5143 s candidate) are diagnostic evidence,
not shipped performance. They identify useful work to remove only with a valid
slot-ownership proof. The shared-root regression and two pre-existing test
harness repairs are published in `3e85204eb`: include the real constructor-tag
helper in the model, and pass the owned native object through object_inputs.
All 48 affected checks pass on the unchanged general root_move, including
native GC3/4 handoff. The failed preimages and full prototype are retained:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/scalar-root-move-v1-rejected/README.md`.

Compiler-private scalar transfers are now implemented in `8321df970`, with
independent tests `370fea2d7`, both pushed to shenming/master. Twenty emitter
sites share the proof check: lexical LIFO operand cells and hidden
non-resumable return owners may transfer NULL/tagged words directly; heap
values, locals/output parameters, global/shared and persistent generator roots
keep the atomic runtime path. Public root_move is unchanged. 111 focused host
checks pass; three native integration programs run on GC0–4. Post-push shared
destination atomicity plus owned-IR/native GC3/4 movement checks pass (3 nodes).
The return-cleanup checker now follows all CFG predecessors and rejects four
missing/cleared/unknown/late-flag counterexamples.

Frozen base 2ca5106a2, host pcc0/owned self/native atomic threaded GC0: five
rotating fixed-work pairs improve 2/4/8-thread medians by 1.133x/1.447x/1.568x.
The first single-thread wall packet is noisy; a retained seven-pair longer
confirmation measures 2.8293 -> 2.4709 s (1.145x), user CPU 2.72 -> 2.35 s.
Scalar-return instruction cost falls 2567.675 -> 2213.819 (-13.78%); heap-return
control is 21359.426 -> 21376.997 (+0.082%), with 1140 -> 1160 ns wall medians.
Early cold-start N/2N wall outliers remain in the evidence. An earlier unpinned
script accidentally imported the shared editable install; only corrected
source-pinned IR/tests/builds support the published claim.

Original full-handler integration passes GC0–4 (1,000 requests plus 200
warmups each). Seven rotating 20,000-request comparisons measure median
9241.649 -> 9651.811 requests/s versus asyncio 70488.536: about 7.30x behind,
with substantial timing dispersion. Native instructions remain 33.139/33.143
billion. This is not an established material full-handler gain, nor proof that
the previous native-pcc1 8.5x gap improved by this amount. Current-source pcc1,
fixed point and full performance acceptance remain unfinished.

New exact-artifact profiles are completed and output-validated. A synchronous
list-concatenation/return loop still costs about 16.2x CPython's instructions;
list concat accounts for 60.31% inclusive samples, its fill for 40.45% (these
overlap). Actual runtime IR already has pointer/i64 lanes and PHIs. It still
re-enters public get/append and pin/root protocols despite outer rooted cells.
The full-handler profile puts JSON dumps at 36.06% inclusive, list append at
10.39%, its rooted core at 9.61%; graph acquire/release leaf shares sum to
9.11%. Scheduler outer frames contain user execution, not scheduler self time.
Next inspect shared rooted container operations, retaining capacity/growth,
post-parking reloads, write barriers, callbacks, exceptions and all five GCs;
the overall sevenfold cause is not yet quantitatively closed.

Modular reference: current Mojo compiler source is available. Saved ArgPromotion,
LowerArgConventions and SROA blobs are verified against upstream Git blob IDs.
Their ownership/effect proofs, register argument projection and scalar
replacement are relevant concepts; wrapping native-width Int and default value
semantics must not replace PCC Python semantics. No Mojo timing claim is made.
All commands, failures/corrections, source/runtime/binary identities, measurements,
native outputs, raw profiles and reference sources are read-back verified at:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/private-root-8321df970/README.md`.

Follow-up native semantic tests found an existing leak before accepting the
list optimization: `joined[0].value = 11` retained the computed receiver even
after its enclosing function returned. Four neighboring minimal concat/identity
controls matched CPython; only the attribute-write variant leaked. The first
repair fixed direct writes, then the native test exposed the scalar-literal
unpack route. `4b36e60d9` now uses owning roots for computed native attribute
receivers/RHS on success and error, including unpack targets; independent tests
are `44d85dd0e`. No failed assertion was dropped. Fourteen host checks and three
native programs across GC0–4 pass on an isolated semantic-fix snapshot that
excludes the performance candidate. Native-pcc1 qualification remains pending.

`20e295460` reuses the existing rooted append transaction for concatenation;
tests are `a5135ebe5`. The transaction borrows caller root/handle cells, reloads
after lock waits, keeps capacity/growth revalidation and the same barrier
commit/finish, and leaves root teardown to its caller. Concat avoids redundant
item pin/restoration and root-pair setup; result address protection remains.
123 focused checks pass. A stale threads-off IR fixture was reproduced on the
unchanged baseline and updated to the current threads-only configuration with
its assertions intact. Three native programs pass GC0–4, including concurrent
concat/collection, four-thread append/growth/finalizers and attribute error
cleanup. Original full-handler response integration then passes 1,000 requests
plus 200 warmups on each GC0–4.

Both new performance arms include the semantic fix and differ in only one pcc
source, py_list.py. Five rotating warmed N/2N repetitions (90 validated timed
processes), host pcc0/owned self/native threaded atomic GC0: concat/return falls
21365.601 -> 20469.008 instructions/iteration (-4.20%), 1060 -> 1020 ns; scalar
return stays about 2215 instructions/110 ns. Public append/pop increases
4497.669 -> 4528.800 instructions (+0.69%) with a 200 ns wall median in both.
The heap loop still costs about 15.56x CPython's instructions. This modest
local result does not explain the full sevenfold request gap. Reassess broader
ownership/call/allocation costs and their measured ceilings before another
small protocol change. Full-handler QPS and new-source pcc1 have not been
remeasured after these two commits. All failed preimages, corrected runs,
source/runtime/binary identities and successful checks are read-back verified:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/rooted-append-20e295460/README.md`.

A subsequent exact-input emission audit separates another substantial cost:
19 runtime modules cover 90.20%/87.20% of synchronous heap/full-handler sampled
leaf work. LLVM 20.1.8 emission of their identical post-PCC IR reduces measured
instructions by 40.32%/38.94%, with fixed application objects and remaining
runtime. Both codegen-only reference programs pass focused GC0–4 execution.
This external reference is not a production route or a pcc1 ownership claim.
LLVM retains/signs every frame; owned ordinary leaves may be frameless, so
frame policies are not identical. Wall dispersion prevents a certified QPS
gain. The reference still costs far more than CPython; emission overhead is
one quantified contributor, not a complete explanation of the request gap.

Additional LLVM O2 IR optimization fails concurrent GC root scanning; the
exact lifetime cause is unresolved. Disabling tail calls still fails GC1–4.
Do not use those failed arms as performance evidence. A guarded owned inline
budget increase from 32 to 96 passes focused execution but saves only about
1.2% of instructions while expanding py_list IR about 30%; it is rejected.
An extra owned memory-cleanup pass leaves major modules unchanged. Full TLS
materialization has only a 6.63%/4.69% sampled ceiling, so a speculative TLS
save/restore optimization is not pursued. These experiments reassess the
pipeline and owner costs without weakening the runtime contract.

The audit found a separate correctness defect: Darwin TLS lowering inserts a
hidden resolver call into IR-classified leaves. `56bdfa6c4` gives protected TLS
functions the existing signed frame; `b29981229` adds defined/external TLS,
text/indexed IR, actual emitted instructions and owned-C threaded execution
regressions. 21 focused and 31 stable-source context checks pass. Frozen
b29981229 then passes four native integration nodes (leases GC0–4, root handoff
GC3/4 and two owned-C TLS drivers), plus the original handler's 1,000 requests
and 200 warmups on each GC0–4. Runtime SHA256:
`3ed320441f513af24de4f5344db3b7a41a5c4db9285d1e5f0dbe487964956509`.
This remains host-pcc0/owned-self/native evidence. New-source pcc1 and fixed
point are pending. All failed arms, commands, profiles, source snapshots,
machine-code comparisons and successful receipts are read-back verified at:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/pipeline-owner-audit-c4f9f8850/README.md`.

The earlier v3 list-construction experiment is superseded by the published
implementation below. Its old 21.28% instruction reduction remains historical
evidence, with rejected predecessors retained at:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/ordinary-list-construction-v3/README.md`.

Current-source concurrency qualification exposed a GC0 correctness defect in
both the unchanged baseline and the first list candidate. A last decref can
park after reaching zero but before untrack. GC0 subtracted that tracked
parent's fields, excluded the zero-count parent from garbage, and failed to
retain its still-owned children. `5b46ff560` marks the pending owner's graph
reachable; tests are `5e7c7ec5e`. A deterministic production-body model and
owned-C native boundary probe fail before and pass after the fix. The native
probe also confirms the child cycle is collected after the parent releases it.
The repair is verified independently of the list optimization. Both fixed
arms pass the same four-thread program on GC0–4, then 20 default and 20 strict
provenance repetitions each (80 successful processes total). Earlier SIGBUS,
owned-result lease and BAD_INCREF failures are preserved as failed evidence.

`8b26131a9` transfers distinct owning operand roots into a fresh unpublished
list, preserving source order, lock/reload/barrier semantics and allocation
failure cleanup. The completed list is published after every input owner has
moved. Tests `f85254b7a` cover large-int/object identity, callable/float elements,
later-operand global mutation, exceptions/finally, finalizers, weakrefs,
resurrection, public list() and concurrent collection. Default and disabled
IR-pass modes both execute the semantic program on GC0–4. 54 focused
post-push context checks pass; the separate native receipts cover execution.
These commits were rebased without conflict over peer 6c35c6326 and pushed
normally to shenming/master.

Frozen 70676fd2a plus explicit overlays, host pcc0/owned-self/native atomic
threaded GC0: five rotating warmed N/2N repetitions reduce the two-element
list-return loop 11611.444 -> 9091.126 instructions (-21.71%), 620 -> 480 ns.
The public factory control is +0.37% instructions with both wall medians
460 ns. Concat is +0.18% instructions; a seven-pair longer confirmation gives
1050 -> 1040 ns, so its initial 1060 -> 1100 ns packet does not establish a
material timing regression. The improved literal loop still costs about
7.24x CPython's instructions and 8x its wall median.

Both arms pass the original handler's 1,000 requests plus 200 warmups on each
GC0–4. Seven rotating 20,000-request comparisons give 10520.103 -> 10617.678
requests/s versus asyncio 89927.064: still about 8.47x behind, with substantial
candidate/asyncio wall outliers. Native instructions are 33.178 -> 33.201
billion (+0.07%); there is no established material full-handler gain. Both
retain the recorded PCC_DISABLE_BULK_GENERATOR_FRAME_INIT=1 comparison setting.
Continue at ordinary runtime costs shared with the handler. New-source pcc1,
fixed point and the full performance target remain unfinished. Failed and
successful runs, source/runtime identities, patches and native outputs are
read-back verified at:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/ordinary-list-construction-8b26131a9/README.md`.

The next ordinary-program diagnostic preserves the original payload, JSON
encoding and byte validation while removing tasks and per-request timers.
It passes GC0–4 and measures 52 us/957646 instructions per operation versus
CPython 2 us/31783. Existing runtime logging counts 37 managed allocations
per synchronous operation, with no generators. These logged runs are a
diagnostic census, not timing or escape evidence. The full handler separately
counts about 79.17 allocations per request. Caller attribution identifies
generic keyword merging and signature binding as substantial shared work.

`31b06432e` removes one such concrete redundancy: the first explicit keyword
group already constructs a private owning dictionary with syntax-proven
distinct string names, so it directly seeds the output instead of being
copied through an empty dictionary. Later mappings/groups retain their
checked merge, error timing and value order. Tests are `f5a32dbf9`. Both
source-frozen arms execute ordinary keyword ownership/error scenarios and
first-group suspension/recovery on GC0–4. Five rotating N/2N repetitions
measure ordinary dynamic keyword calls 221555.900 -> 141431.560 instructions
(-36.2%), 11.8 -> 7.6 us; the synchronous payload is 956082.028 -> 884500.779
instructions (-7.5%), 52 -> 49 us. This is host-pcc0/owned-self/native evidence.

Post-push checks also reproduce an old cold error-reporter leak: the normal
inline error path releases py_exc_new's owner after TLS retains it, while the
shared reporter omitted the release. `01bf809d3` restores that operation;
`5c65dc20e` verifies exact error traces and a real owned-C native driver that
fails before and passes after the repair. The driver checks GC0's TLS owner
count and executes new/existing-error paths on GC0–4. `539dcb9df` updates the
old three-merge assertion to check the remaining two merges and their actual
root transfers. 143 focused checks pass. Frozen final source 539dcb9df with
its newly matched runtime then passes three native pytest nodes across GC0–4.
Failed baseline/model/context receipts are retained; no failures were dropped.

Both full-handler arms pass 1,000 requests and 200 warmups on each GC0–4.
Seven rotating 20,000-request runs (C100, delay 0, the same retained bulk-frame
comparison setting) give 10612.213 -> 11109.118 requests/s (+4.7%), versus
asyncio 88488.413. Instructions fall 33.220 -> 31.730 billion (-4.5%), user CPU
1.89 -> 1.80 s. Every final result exceeds every baseline result in this
packet. The same-packet gap narrows about 8.34x -> 7.97x; it remains far from
the target. Final-source counting confirms 37 -> 32 synchronous allocations:
two fewer dicts and one fewer list, iterator and exception. Final runtime:
`5eafcc9a6352b5e76db411984bab02aee151e5f9563832a3ab248dcee15a459b`.
Next inspect generic parameter binding and internal argument metadata;
native-pcc1, fixed point and full performance acceptance remain unfinished.
All source identities, failed preimages, native outputs, timing and census
receipts are read-back verified at:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/keyword-root-seed-31b06432e/README.md`.

A follow-up ordinary-call comparison corrects the binding diagnosis.
Dynamic keyword/positional/direct calls cost about 141260/48058/3348 native
instructions per operation. In the keyword capture, dict.update accounts for
42.66% inclusive samples beneath the binder's keyword-dictionary copy;
py_tuple_get beneath binding is only 1.03%. The positional binder is 1.41%
of its capture. Some short CPython wall differences round to zero and cannot
be treated as zero execution time. All profile children finish with validated
checksums. This directs work toward copying and root protocols rather than an
unmeasured signature-layout rewrite.

`c27d1d05d` keeps exact-dict update snapshots of up to six pairs in twelve
unused cells of the already registered operation frame. Cached hashes use a
bounded stack buffer; larger snapshots keep the existing implementation.
Snapshots still complete before destination callbacks, child leases end after
capture, later reads use authoritative roots, and cell order preserves the
old snapshot tuple's retirement order. Tests `2bd3745f9` and `6deeaee4b` cover
the cutoff, relocation, copy failures, source clearing during equality,
comparison errors and cleanup. A pre-existing model omitted the production
sequence diagnostic; `d2d7a7c93` fixes its loader and checks exact error text.
81 models/reference checks pass. Both native arms pass cached-hash, identity,
callback/collection, finalizer and two-thread scenarios on GC0–4; seven
post-push sensitive native nodes also pass. All commits are on shenming/master.

Frozen 38eda5eb5 plus the runtime change, host pcc0/owned-self/native atomic
threaded GC0: five rotating N/2N repetitions measure two-entry dict update
72991.999 -> 59475.586 instructions (-18.52%), 3800 -> 3000 ns; ordinary
keyword calls 141907.734 -> 127953.852 (-9.83%), 7600 -> 6800 ns. Synchronous
payload instructions fall only 1.04%, with both wall medians 48 us. The
seven-entry control is +0.24% instructions with both wall medians 9500 ns.
After accounting only for exact frozen-root path globals and their widths,
the runtime IR differs in py_dict alone; actual IR/provenance was not changed.
The new helper has no unintended integer boxing calls.

Both original handler arms pass GC0–4. Seven 20,000-request comparisons give
median 11148.831 -> 11227.383 requests/s, with overlapping native ranges;
instructions fall 31.719 -> 31.519 billion (-0.63%). This is not an established
material whole-handler speedup. Same-packet asyncio is 89140.562 requests/s,
leaving about a 7.94x gap. Counting confirms one fewer internal snapshot tuple
per keyword call and synchronous request (six -> five and 32 -> 31 managed
allocations respectively). This comparison motivated the grouped-root
follow-up below, targeting registration shared by ordinary operations.
Native-pcc1, fixed point, conformance and the broader P0 scope remain open.
Read-back verified evidence, failed preimages, source identities and outputs:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/dict-root-snapshot-c27d1d05d/README.md`.

`0cfdb9cd2` groups the temporary owning roots in ordinary special calls and
dictionary operations using the existing checked continuation-node API.
The 26 special-call and three dictionary callers still initialize every root
before registration, retain all leases/barriers and exception cleanup, and
clear the saved node before removal. GC0 marking, GC3 promotion/cache/cursor
handling and common relocation visitors already cover these owning spans;
no collector algorithm, object layout or public ABI changes. `e67c0760b`
adds failure/relocation/cleanup checks. `cfbae7dc8` updates related models for
group registration and fixes baseline-reproduced stale class flags and
constructor names. All three commits are published on shenming/master.

The selected model/reference scope has 523 passing nodes across documented
shards. Two focused native programs pass GC0–4, as does the original handler;
eight post-push native nodes additionally cover keyword ownership, parking,
temporary-source finalization and class-method collection. Failed preimages
and two bounded incomplete context attempts are preserved, not counted as
passes; their unfinished cases subsequently pass with the right runtime or
separate shards. Frozen production files match the committed source.

Five rotating N/2N repetitions (150 timed processes), host pcc0/owned-self/
native threaded atomic GC0: keyword calls 128177.707 -> 109184.616 instructions
(-14.82%), positional calls 48008.004 -> 41713.973 (-13.11%); two-/seven-entry
dict.update falls 20.71%/13.33%. Synchronous payload/JSON improves only 3.23%
instructions, 50 -> 48 us. Seven original-handler comparisons give median
10510.550 -> 11124.454 requests/s (+5.84%), and 31.5234 -> 30.3861 billion
instructions (-3.61%). Six pairs favor the candidate; one is much slower,
and the ranges overlap. All observations remain in the receipt. Same-packet
asyncio is 82234.954 requests/s: a remaining 7.39x gap, versus 7.82x for this
packet's baseline. This is not a controlled comparison with the older 7.94x
packet. Full throughput acceptance remains unfinished.

Fresh captures of the measured artifacts finish verified outputs and outlive
their five-second sampling windows (4147 synchronous / 4156 handler samples).
Root store/release is 17.46% / 19.13% inclusive; foreign lease acquisition is
13.67% / 8.59%. These shares overlap and cannot be added. Root retirement,
lease checks and pointer provenance are the next measured owners to decompose;
no blanket trust of Dyn values, weakened GC contracts or JSON-only shortcuts.
Latest-source native pcc1, fixed point, broader conformance and full P0 goals
remain open. Read-back verified code, artifacts, profiles and all run receipts:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/scratch-root-groups-0cfdb9cd2/README.md`.

`81d4fa630` extends nonmoving address leases to positive results from the
existing published managed-pointer index. The atomic mode reservation and
post-reservation mode check remain; an uncertain result still drops that
reservation, locks and reloads the original authoritative slot. Existing
reader lifetime/retired-table contracts are preserved. `238dcf30e` recognizes
only the five exact image-static singleton header addresses before any header
read or mode protocol. Allocated immortals remain guarded. A finite raw-data
ABI declaration names those five headers; arbitrary managed globals remain
rejected. Tests are `68ea620eb` and `665ef8337`; all four commits are on shenming.

An instrumented, untimed census explains the selected mechanism: a synchronous
payload performs 258 non-NULL/non-tagged address lease attempts, of which 108
enter the lock, and 37 return the unregistered-static no-op result. Registered
string literals instead take the exact-index/mode-only route. A same-value
literal/heap string control confirms this distinction. These are repeated
lease calls, not allocation counts. The census's floating-point accounting
assertion was corrected using integer deltas; native outputs were already
validated, and the failed log/preimage is retained.

Final source passes 338 focused model/ABI-boundary checks, native singleton
collection on two threads across GC0–4, allocator/exact-index concurrent mode
reset stress, and four sensitive native nodes covering GC3/4 movement, GC0–4
immediates and immortal streams, and keywords surviving parking. The existing
owned standalone C index-growth and retired-table tests also pass again.
Both intermediate and final original handler artifacts pass GC0–4. The first
new literal fixture expected allocator result zero instead of documented -1;
it was corrected and re-executed. An initially undeclared static symbol was
rejected by the strict boundary until the exact data declarations were added.
No rejected run is counted as passing. A pcc1 fixture parameter could not run
without a current-source compiler; its pcc0 parameter and remaining scoped
checks pass, but this does not establish native-pcc1 qualification.

A three-native-arm/CPython comparison (five rotating N/2N repeats, 200 timed
processes) measures final vs this phase's original baseline: synchronous
payload 844791.561 -> 754986.851 instructions (-10.63%), 48 -> 42 us;
keywords -4.30%, positional calls -3.47%, literal strings -14.44%, heap
strings -10.25%. The indexed-only intermediate had raised positional/heap
string costs; the final version removes those regressions against baseline,
while adding 1.0% instructions to indexed-only keywords. Short wall deltas
remain limited by timer resolution.

Seven original-handler comparisons give median 11468.830 -> 12241.093
requests/s (+6.73%), 30.3663 -> 28.4632 billion instructions (-6.27%), and
user CPU 1.76 -> 1.65 seconds. Every paired final/baseline run is faster;
ranges overlap, and one final/indexed-only pair regresses. Same-packet asyncio
is 85543.443 requests/s: gap 7.46x -> 6.99x. This is a controlled change within
this packet, not a comparison against the older 7.39x packet. These are
validated handler batches, not HTTP server measurements. Full performance
acceptance, latest-source native pcc1, fixed point and the full P0 goal remain
open. All commands, identities, failed preimages and receipts are read-back
verified at:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/lease-provenance-81d4fa630/README.md`.

A current-source Stage1/native-program verification operation is prepared for
665ef8337 and requested asynchronously, not executed: the goal requires
explicit bootstrap approval. Its reviewed script/plan are in that archive.
The planned build keeps LC_ALL, uses the shared lock, 5 GB RSS cap, 8 GiB
reserve, 1800-second bound and an isolated checkpoint/output. Historical
8c53ef3c7 Stage1 took 1754 seconds with a 2.87 GB peak; those figures size the
request and do not qualify the current source. Continue independent runtime
work while that approval is pending; do not infer approval from elapsed time.

A fresh sample of the lease-optimized artifacts finds append at 10.46% of
synchronous / 11.95% of handler samples, but its GC0 callee does not register
moving roots. The proposed duplicate-registration explanation was therefore
not supported. Root store/release remains 20.30% / 21.85% inclusive; individual
removable store-plan pieces are small. No speculative append-plan edit landed.

Preparing a generic call-construction change exposed a baseline semantics
mismatch: failed argument evaluation, list destruction and tuple destruction
retired children forward, while the actual CPython controls retire them in
reverse. `dfd4b5dc5` fixes the general deallocators and reorders small dict
snapshot roots to match heap-tuple retirement. `f174f1eba` adds regression
coverage. 83 model/reference checks, sequence/snapshot native programs across
GC0–4 and four further native concat/append/construction nodes pass, including
finalizer collection, weakrefs and resurrection. A later pcc1 parameter cannot
run without a current compiler and is not counted as a pass.

`ebb34e1f7` removes the internal list from calls with up to eight ordinary
positional arguments: evaluate once left-to-right into owning roots, allocate
the tuple after all user evaluation, fill it and then release captured roots.
Wide calls and splats keep streaming construction, avoiding unbounded growth
of prefix cleanup IR. Exception cleanup retains one scope across all completed
arguments. Tests `8858f2be2` and `53c556e9b` cover actual dynamic functions,
callable objects, module calls, aliases, big integers and parking on GC0–4.
The parking resume IR contains the changed tuple factory and py_obj_call_slots.
A precise owned-IR injection at that factory proves GC0–4 MemoryError cleanup
after both arguments execute, with reverse release and no target invocation.
37 initial IR/reference checks, 185 related host checks and five sensitive
native nodes pass. Test corrections preserve exact error/cleanup checks: the
owner is now a direct argument root; a baseline fixture had only two of three
intended dynamic calls, and now forces the third through a callable parameter;
mapped LIFO entry/leave names must form a consistent ordered pair. All five
code/test commits in this follow-up are published on shenming/master.

Five rotating N/2N repeats (150 timed processes), both arms carrying the same
retirement fix: two positional args 40252.960 -> 27560.622 instructions
(-31.53%), 2100 -> 1400 ns; eight args -48.28%; nine-arg streaming control
-0.03%; keyword calls -6.84%. Synchronous payload improves only 1.47%
instructions. Untimed logging confirms one fewer list per optimized call:
positional/eight-arg managed allocations 2 -> 1, keyword 5 -> 4, synchronous
31 -> 30; nine-arg control is unchanged. Tuples and all semantic owners remain.

Both original handler arms pass GC0–4. Seven comparisons give median 12572.346
-> 12906.956 requests/s (+2.66%), 28.4618 -> 27.6844 billion instructions
(-2.73%) and user CPU 1.60 -> 1.56 seconds. All paired native runs favor the
candidate, although ranges overlap. Same-packet asyncio is 88906.308 requests/s:
gap 7.07x -> 6.89x. This is host-pcc0/owned-self emitted-native evidence, not
HTTP throughput, a comparison with an older denominator, or current native
pcc1 qualification. The whole-handler target remains unmet. The earlier
bootstrap proposal is still unexecuted/pending approval and targeted 665ef8337;
this newer compiler lowering is not covered by that proposal. Full P0 scope,
latest-source pcc1, fixed point and conformance remain open. Read-back verified
source, failed preimages, native outputs, profiles, allocation and timing receipts:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/fixed-call-tuples-ebb34e1f7/README.md`.

`18ba7881e` makes the private UTF-8 byte decoder's bounded intermediates explicit
i64 values. The int-shaped ABI, offset bounds, truncation, signed-byte masking
and surrogate/raw-payload behavior remain unchanged; the assembled payload is
at most 21 bits. Ordinary Python arithmetic is unaffected. The former cold
multibyte expressions generated boxed-int calls and six root slots entered
even on ASCII. Actual runtime helper IR shrinks from 1141 to 154 lines, with
no boxing/root calls and the original thread safepoint retained. Tests are
`a7e0618b0`; both commits are published on shenming.

174 focused model/IR checks pass, excluding the preserved raw-entry red below.
Owned-C native execution covers all 1,114,112 codepoints, continuation positions
and truncations on GC0. A managed native control constructs and verifies scalar
inputs before checking 120 codec/error-mode combinations on GC0–4: exact bytes,
UnicodeError positions/reasons/args and original-object identity agree with
CPython. Finalizer/rebinding and Unicode constructor-cleanup controls also pass
GC0–4. The original full handler passes GC0–4. Test models now supply the
previously missing stack allocation/codec dependencies and distinguish input
from result-publication leases without removing error or lifetime checks.

Several pre-existing failures are explicitly retained, not certified green:
raw encoder entry may park before publishing its borrowed frame; the forced
GC3 finalizer probe expects 16 disposals but both versions observe zero;
UnicodeEncodeError repr emits raw surrogate bytes rather than escapes; and a
nested constant input table combines an adjacent high/low surrogate pair before
encoding on both versions, while an isolated function-local literal is correct.
The latter's precise root cause is still open. Codec tests construct from and
verify codepoints to isolate encoding from that input defect. The original
187-line codec/formatting program is byte-identical before/after on GC0–4;
only its 14 surrogate-repr lines differ from CPython. Original failures, raw
bytes and paired repros are preserved. An initial post-check selected a stale
runtime and was rejected by provenance before execution; the corrected check
used the matching archive.

Five rotating N/2N repeats (150 timed processes) show ASCII encoding
87049.806 -> 31659.076 instructions (-63.63%), Unicode encoding -67.28%,
non-ASCII ord -62.47%, and synchronous payload 742370.644 -> 692797.782
(-6.68%). The unrelated positional-call control is +0.004% instructions.
Short wall differences remain timer-resolution limited. Seven full-handler
comparisons give median 12654.095 -> 13232.554 requests/s (+4.57%),
27.6993 -> 26.6302 billion instructions (-3.86%), and CPU 1.59 -> 1.53 s.
Six of seven pairs favor the candidate; ranges overlap and the slower pair
remains recorded. Same-packet asyncio is 84291.801 requests/s: gap 6.66x ->
6.37x, not a comparison with an older packet's denominator. These are host-pcc0/
owned-self handler batches, not HTTP or current-pcc1 performance proof. The
throughput objective, baseline failures, native pcc1/fixed point and full P0
scope remain open. Read-back verified receipts, code and diagnostics:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/utf8-machine-18ba7881e/README.md`.

`12763ef8b` reuses unchanged borrowed parameter roots for selected
non-consuming list-append inputs and string-method receivers. A dedicated
borrowed address lease retains mode/movement protection while relocation
updates the address without transferring refcounts. Closure cells, rebound
locals, globals and generator operands keep the existing owning route; cleanup
never clears the caller-backed parameter. Tests are `1af950363`. Post-push
checks exposed an older handoff model missing grouped-root registration and
using overlapping mock allocations; both were reproduced/verified and repaired
in the separate test commit `f084854e5`. The resulting focused scope passes
413 model/IR nodes (15 integration variants deselected). The actual input
lifetime program, concurrent two-thread append/collection, existing static
lease/thread control and original full-handler outputs pass GC0–4.

The new original Unicode assertion remains failed: both preimage and candidate
produce `CAFé` for `"café".upper()` on GC0–4. This is the already recorded
ASCII-only case-mapping gap, not a changed expectation. A separate ownership
program completes with ASCII upper and Unicode replace/prefix checks. The
required failing Unicode test is retained; the whole suite is not green.

Five rotating N/2N comparisons show append 6416.611 -> 5552.863 instructions
(-13.46%), replace -4.31%, and synchronous payload 691756.405 -> 670663.275
(-3.05%). The positional control is +0.51% instructions with unchanged median
wall difference; synchronous wall difference also remains unchanged in this
packet. Seven full-handler pairs give 13321.788 -> 13535.234 requests/s
(+1.60%), 26.624 -> 26.271 billion instructions (-1.33%), CPU 1.52 -> 1.49 s.
All seven pairs favor the candidate, with overlapping ranges. Same-packet
asyncio is 89138.328: gap 6.69x -> 6.59x. This denominator must not be compared
to the preceding packet's 6.37x.

A separate same-source diagnostic runs the identical synchronous request and
86-byte output using normal CPython json, CPython interpreting the actual
`pcc/stdlib/json.py`, and the PCC native candidate. Provider identity and
`dumps`/`_encode_object` code filenames are checked. Five rotating repetitions
give 2000 / 2500 / 37500 ns per request, respectively, and 31764.711 /
47438.846 / 672806.816 instructions. Thus the ordinary native program remains
about 15x slower even with the same JSON source; the C JSON accelerator alone
cannot explain the gap. This diagnostic directs work back to generic object,
ABI and lifetime costs and does not replace the normal-Python target.

These remain host-pcc0/owned-self/no-libpython native measurements with atomic
threaded GC0; latest-source pcc1, fixed point and the full P0 target remain
unfinished. All 949 frozen production files match the published code. The
349-file archive, including failed prototypes, outputs, profiles, source and
runtime identities, was read back and hash-verified:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/borrowed-input-roots-12763ef8b/README.md`.

`d2459e106` disposes a discarded expression's final owning root directly,
without a raw result handoff followed by another retirement. Nested arguments,
used results and producers without an explicit final-root contract keep their
existing paths. `2095ad8b5` also declares the borrowed-operand metadata in the
native codegen host layout; `36ef4a85a` adds regression coverage. Follow-up
`0f51790fb` corrects three older format/f-string IR expectations: both preimage
and candidate call `py_builtin_format`, while immediate-root-publication and
callback model assertions remain unchanged. The expanded context run passes
419 model/IR nodes (47 integration variants deselected). Three selected native
nodes execute and pass on each GC0–4, including disposal, retained/nested results,
weakrefs, finalizers, resurrection, descriptor error order and parking. Original
full-handler output checks also pass on GC0–4. Existing unrelated semantic
failures above remain open; this is not a claim that the whole suite passes.

The discarded-call control now has an executed CPython comparison: CPython
3.15.0 with its GIL enabled warms to `LOAD_FAST_BORROW` / `CALL_PY_EXACT_ARGS`,
which moves stack references into the new interpreter frame without an argument
tuple for this shape. PCC still constructs an argument tuple, copies callable/
args/kwargs into a 14-slot generic-call frame, and retains them again in the
11-slot function-call frame before binding/dispatch and layered cleanup. Exact
positional binding can reuse the argument tuple; it does not always copy it.
Both 28439.383 PCC and 884.455 CPython instructions/iteration are hardware-count
N/2N differences, not machine instructions compared with bytecode counts.

Fresh exact-PID profiles separate wrapper cost from callee work. In the micro
control, generic-copy plus close accounts for 24.41% of samples; function-call
wrapper work excluding its body is about 11.19%. In the real synchronous
request, however, `py_obj_call_slots` is 85.27% inclusive but its direct dispatch
body is 83.48%: only 1.80% belongs to the outer wrapper and its other children.
The function-call wrapper excluding its body is another 0.45%; keyword binding
is 7.54%. Parent/child shares overlap and must not be added indiscriminately.
Disjoint assignment to the deepest JSON user frame identifies sorted 8.77%,
dict materialization 8.27%, object-encoder root store/retirement 6.43%, and
string/object-encoder list append 5.22%/2.86%. These are operation costs, not
proved removable fractions. Source confirms that sorted snapshots to a private
values list, sorts indices, then allocates and fills a second result list and
retires the first. That observation motivated the generic construction change recorded below;
this earlier packet did not establish its implementation or gain. Stable
ordering, callbacks, exceptions, identity and all five GC slot protocols remain required.

The last published disposal change remains a small local gain. Longer seven-
repetition append controls give 5503.185 -> 5160.491 instructions (-6.23%) and
260 -> 240 ns; the shorter, contradictory wall observation is preserved.
Synchronous payload instructions fall 673281.429 -> 661841.818 (-1.70%), with
39 -> 38 microsecond wall differences. Seven original full-handler pairs give
13319.792 -> 13271.867 QPS (-0.36%), only two pairs favoring the candidate and
overlapping ranges; instructions fall 0.85%. There is no established handler
throughput improvement. Same-packet asyncio is 86666.738 QPS, a remaining 6.53x
gap; changing denominators must not be presented as progress from 6.59x.
These results belong to frozen 36ef4a85a production code, before later peer
linker commits integrated by rebase, and use host-pcc0/owned-self/no-libpython,
atomic threaded GC0. They do not qualify current native pcc1.

Mojo ArgPromotion/LowerArgConventions/SROA, Go escape analysis, HotSpot escape/
scalar-replacement eligibility and Rust Arc borrow-versus-clone implementation
were read as source-bound references. No benchmark of those languages was run.
Their relevance is effect/provenance proof and avoiding unnecessary ownership
materialization, without changing PCC bigint, object identity or moving-GC
contracts. The user authorized one self stage1 build with a 5 GB tree cap and
1800 s limit; later performance-priority steering defers it until an effective
version stabilizes. Its old 665ef8337 label must be replaced with the actual
frozen source identity when executed. Full P0, integration, conformance,
concurrent collectors, throughput and fixed-point goals remain unfinished.
The 290-file archive was read back and SHA256-verified, including negative
results, exact sources/runtime/binaries, reference excerpts, profiles and
reproduction scripts:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/discarded-results-d2459e106/README.md`.

`984f67457` now reuses sorted's private working list as its result. After
stable index sorting, its scratch allocation becomes the list payload and its
order allocation becomes ascending old-slot/new-slot pairs. Existing GC4
payload retargeting updates remembered/store-buffer slots and spans under the
graph lock; write barriers, original capacity, input identity and element owner
counts are preserved. This removes the second managed list and its per-element
get/adopt/append/drop cycle. Tests are `4313f8819`.

The initial original reverse-variable program failed on both baseline and
candidate. At the user's direction, `66d384e41` fixes `sorted(..., reverse=flag)`
through a new rooted runtime entry, with tests `b042a8bb9`. Keyword expressions
retain source order; the input snapshot precedes reverse truth conversion,
which precedes key callbacks. CPython v3.15.0 source and executed references
confirm iterator-error precedence, __bool__/__len__, invalid bool results and
callback mutation behavior. The original failure and both positive control
programs now pass as three native nodes, each running GC0–4. This does not claim
all other sorted **kwargs/unknown-keyword surfaces are complete.

The initial 70 ownership models, subsequent 108 context/model checks (4
integration variants deselected), six ABI checks and 25 post-push checks (15
deselected) pass. The latter include sorted/input/result cleanup, list.sort
mutation/ordering and relocation contracts. Two relocation object-closure
checks explicitly use clang/nm as external references; they are not owned
emitter or pcc1 evidence. All actual handler and changed-shape native programs
use the owned self backend and explicit atomic threaded runtime. Original
full-handler outputs pass GC0–4 for both baseline and final candidate. A test
reference correction releases its saved exception only after identity checks,
before asserting finalization, because CPython traceback locals retained the
objects; the original failed reference remains in the archive.

Final five-repetition N/2N comparisons, baseline 20649d13b against the combined
change: three-item sorting 48122.974 -> 35278.946 instructions (-26.69%),
2400 -> 1900 ns; synchronous request 661535.051 -> 641256.154 (-3.07%), with
unchanged 38 microsecond median wall difference. The unchanged positional
control is +0.09% instructions, 1400 -> 1500 ns. Seven full-handler pairs give
13235.663 -> 13469.029 QPS (+1.76%), six pairs faster with overlapping ranges;
instructions 26.055 -> 25.649 billion, user CPU 1.51 -> 1.48 s. Same-packet
asyncio is 88523.549 QPS: gap 6.69x -> 6.57x. The earlier sorting-only packet
had only +0.28% median handler QPS; both packets and all negative/noisy rows are
preserved. This remains a small gain and does not meet the performance goal.

Fresh final-artifact exact-PID profiles assign each stack to its first matching
primitive family without overlap: root ownership 24.96% sync / 25.33% handler,
lease/pin 10.11% / 7.14%, root registration 6.44% / 5.23%, and refcount outside
those primitives 6.25% / 7.22%. Root ownership includes necessary retirement:
sync store_root's direct decref child alone is 14.90% of total samples, and
its downstream deallocation is substantial. These are not all removable root
bookkeeping. List append as a separate operation view is 10.80% sync / 13.67%
handler, dominated by growth, store-plan, memset, locking and validation inside
append_rooted_slots. Sync dict materialization remains 8.15%, sorting 5.97%.
These operation views overlap the primitive-family table and cannot be summed.
The next priority is shared ordinary-object/container lifetime and repeated
ownership/protection work, rather than more isolated sort microbenchmarks.

TSRS was additionally checked at e04b62f205a11c70cc45c1fca9dc560efc6f8d80:
mapper escape propagation, MapperCell barriers and recycling, arena size-class
free lists and checkpoint epochs. Region::note_escape is a separate lifetime
mechanism from mapper's object escape bit. Its recorded recycling win is memory,
not a demonstrated instruction reduction; its historical census tools have
since been removed. Its protected-borrow/free-list correction reinforces that
lifetime effects and alias attributes must be true. Java/Go/Rust/Mojo source
references remain mapped to PCC's ownership/provenance requirements; none of
this is a claim of implemented general escape analysis or benchmarked gains
from those languages. Ordinary synchronous performance stays the main task.

All 949 frozen production files match published b042a8bb9. This is still
host-pcc0/owned-self/no-libpython, not latest-pcc1 or fixed-point qualification.
The approved single stage1 remains deferred under the performance-priority
steering; full P0 and the broader semantic/concurrency/conformance scope remain
active. The 446-file source/runtime/binary/test/reference/profile archive was
read back and hash-verified:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/sorted-reuse-984f67457/README.md`.

### Active ten-round runtime performance work (updated 2026-10-26)

Ten code optimization rounds now have positive paired performance evidence.
The overall Python-speed target remains unmet. All ten rounds and follow-up
tests are now on shenming/master: 591245726 -> 76a47eae7 was pushed normally
after fetch/rebase, following the maintainer's explicit destination confirmation.
`shenming/codex/p0-handoff-triage` retains the published recovery snapshot.
The master-target clarification is resolved. No origin/allstoalls write or
force push occurred.

| Round | Production | Full-handler QPS gain against preceding accepted arm |
| --- | --- | --- |
| 1 | ed452ca3d | 7.16%: default owned bulk generator-frame construction |
| 2 | a159e27e6 | 3.69%: admitted immortal refcount no-op |
| 3 | 5d70d17bb | 2.05%: smaller common refcount entries |
| 4 | 9a8c0e0ff, 7bfd22dc2 | 16.43%: guarded live module calls avoid argument packing |
| 5 | d3bba8085 | 1.67%: atomic integer loads stay in allocated registers |
| 6 | ffcba6a99 | 2.29%: consumed atomic results project directly to scalar storage |
| 7 | ca5969f9f, 562985dfd | 2.93%: stable parameter predicates and proven nonmoving literal inputs |
| 8 | 99db558c4 | 1.10%: discard exact-list append None without an output owner |
| 9 | 4cce0f1e9 | 3.96%: bounded immutable threaded name keys with registered roots |
| 10 | 6b271f6d6 | 2.84%: append directly from caller source slots |

Final same-packet medians: preceding arm 20,500.16 QPS, candidate 21,082.99.
All 7 paired handler repetitions improve. Candidate instructions fall 1.59%;
ordinary synchronous payload instructions fall 428,220 -> 414,870 (3.12%),
median 23.0 -> 22.5 us. Against the same-packet original control, full-handler
QPS rises 48.10% and the asyncio gap narrows 6.3910x -> 4.3152x. These are
host-pcc0 / owned-self / no-libpython / threaded-atomic GC0 emitted programs,
not HTTP, current native pcc1 or fixed-point qualification. Ratios from separate
asyncio packets are not subtracted: round 9's 4.29x is a different packet.

Round 7 passes 51 focused checks; round 8 passes 14; round 9 passes 15,
including four actual native threads, checked joins, name-buffer reuse,
collisions, live namespace rebinding and GC0-4. Round 10 passes 38, including
concurrent list growth, source-root relocation models, owned/borrowed lifetime,
finalizers, resurrection, dynamic method fallback and the existing barrier
contract. Both original sync and full-handler programs execute successfully
on GC0-4 for each accepted round. Tests acbafb828 accompany round 10.

Two root-entry experiments did not establish throughput gains (-0.39% and
-0.32% medians); e04b7820f withdraws those performance changes. They do not
count as rounds. The first type-cache-only trial and NULL-tail frame trial
also remain rejected intermediate evidence. The empty ownership-transfer
reentrancy correction remains: NULL transfers must not release a replacement
installed by the old owner's finalizer (a4b23d648 / f66339cd3).

Discovered correctness work is separate from the ten performance rounds.
8d230121 / 2dcb1ebd7 add rooted dynamic generator send/throw/close;
61f42277e / a643e7b81 preserve executed local imports across suspension;
5c14a8490 / 1164bac7b retire explicitly deleted module-function caches without
invalidating surviving aliases/default owners. The original namespace bindings,
scopes, calls, lifetime and operations scenarios now pass on GC0-4.
01db3d92e / 5b8acaeb5 fix Darwin's owned C pthread_t width and create/join/detach
declarations: the old 32-bit handle made join return ESRCH and left workers
running. The independent owned C join test and threaded cache test execute
the actual corrected ABI. Other Darwin pthread layout capabilities are not
certified by this narrow fix. The old dataclasses.replace alias/module-attribute
failures remain recorded in the round-4 controls, not claimed repaired.

Additional qualification is incomplete. The lightweight bootstrap-baseline
selection passes 2 checks with 2 deselected; it does not execute a new compiler.
Thirty distinct fallback/IR checks pass across the frozen runs, including the
per-module codegen gate and all three independent ir.py count ratchets. The
initial 1 GiB attempt was memory-limited; bounded 2 GiB runs then exposed two
native-mode canaries configured in compatibility mode and obsolete class/root
IR assertions. dab149a4a / 53bf8d621 / 1f8656539 correct those tests; the computed
subprocess capture/starred-argv/timeout case also executes on GC0-4.
The complete multi-file closure hits the 300 s watchdog. The static Function.name
field-index assertion remains failing: the rooted argument path uses dynamic
getattr instead of field index 12, also reproduced on the pre-round8 control.
That optimization constraint remains unchanged; 16 fallback nodes still lack
qualification. This is not a full green baseline, current native pcc1, five-GC
bootstrap or a fixed point. Wider conformance and five-concurrent-GC goals stay
open. Detailed qualification receipts are in the archive's qualification-final
subdirectory, with both original failures and corrected test sources.

Exact frozen source/runtime/binary identities, commands, node logs, failed
attempts and paired results are retained in the read-back-verified archive:
`/Users/jiamo/pcc-recovery-archive/2026-10-25/codex-p0-handoff-triage/performance-five-rounds/README.md`.
Accepted newer receipts are `round7-static-inputs`, `round8`,
`round9-name-cache-v2` and `round10-source-slots`; earlier rounds are siblings.
The working ledger is `/private/tmp/pcc-p0-performance-five-rounds/rounds.json`.
The round-8 sampled processes and their verified outputs are archived with
round8; they are attribution evidence before the last two optimizations.

### Active runtime target and measurement correction (2026-10-27)

The maintainer fixed the target at **PCC >268,000 requests/s** for the same
handler workload/resource scope. Do not rerun asyncio; future timing packets
compare PCC baseline/candidate against this fixed reference. The wider semantic,
concurrency and self-host scope remains open.

The native flame graphs below use macOS `sample` with known sleeping leaves
filtered. Their percentages describe filtered stack samples, not exact on-CPU
time or an exact Amdahl bound. In particular, the filter did not remove
`__psynch_mutexwait`. Original raw captures are retained and the two new archive
READMEs carry this correction. Throughput and process user/system CPU counts
come from independent `/usr/bin/time` receipts and remain valid.

Same-artifact PCC isolation diagnostic (three repetitions, no post-warmup
interprocess barrier, so not a performance acceptance packet): four carriers
16,700 QPS / 8.86 CPU s; four one-carrier processes 77,799 QPS / 1.80 CPU s,
for 40,000 requests and total C100. This isolates a substantial same-process
multithread penalty in addition to single-request cost; do not attribute all
of it to one lock without further evidence. The thread design remains the
optimization target. A direct blocking-mutex candidate is unaccepted: four
carriers regress about 10% (0/7 faster), system CPU rises 0.22 -> 0.99 s.
The bounded-spin-then-block candidate passed 17 checks and ten original GC4 runs, but four-carrier gain was only 1.62% with 4/7 faster pairs and large adverse outliers. It is not accepted or shipped.

A new Time Profiler capture explicitly excludes waiting threads and selects
only the target PID's Running samples. 17,374 samples attribute 14.92% self CPU
to graph-lock acquisition and 8.01% combined to pin/unpin; about 94.9% of
samples ran on P cores. Pin/unpin's dominant instruction PCs retry updates to
the same global pin-balance statistic. Free-metadata calls are the largest
graph-lock caller group. These stronger observations replace the earlier
filtered-wall-stack interpretation. Exact source, timing, target-emission and
CPU evidence (with environment metadata excluded from the exported bundle):
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/mutex-attempts-and-cpu-attribution/README.md`.
Terminal-free, hot-state isolation and intrinsic singleton pin changes below
are published. The latest on-CPU capture (before singleton pin removal) puts
pin/unpin at 10.49% self CPU, including 6.51% on atomic retry branches. A
hardware-qualified two-loop LSE diagnostic gave only +2.96% paired four-carrier
QPS and was not adopted. Two allocator size-class locking attempts also failed
performance acceptance and were not committed. Detailed rejected experiments
remain in the archives; public scalar counters and movable-object protection
were not removed to manufacture gains.

### Tracked tables sharded; snapshot lifetime fixed; graph contention remains (2026-10-28)

Published code/tests: `503d32958`/`69968b3bd` for address-sharded tracked lists,
`fd464f1d8`/`ebf4cc20a` for GC snapshot ownership and terminal admission,
`a01ae4afb`/`48442f778` for sharded primary-index fallback and its tests.
The maintainer explicitly accepted phase1's four-worker +6.93% paired gain
with its single-worker -3.66% cost; an independent seven-pair recheck found
single-worker -4.30% (0/7 faster), with instructions +3.99%. Single-worker
recovery remains required. Phase2 removes an extra allocator-origin lookup
by partitioning the fallback index with the same address hash. Its paired
single-worker gain versus phase1 is +1.63%, with instructions -1.59%; against
the original baseline it remains -3.50%. Four-worker gain versus the original
baseline is +10.36% (7/7); against phase1 it is -0.76% (3/7), not a reliable
additional four-worker improvement. Keep batch identities: absolute QPS
changes with host load. The 268,000 four-worker target remains far away.

The snapshot failure was real and reproduced before sharding: native GC
snapshot results were treated as borrowed and retained again. Their roots
were cleared, but the extra owner kept snapshots and resurrected objects alive.
All three snapshot results now carry owned-result evidence. A separate
four-thread BAD_INCREF exposed snapshots admitting terminal objects whose
release thread could be parked during partial teardown. Snapshot admission
now matches collector traversal: exclude DEALLOCATING/zero-owner objects,
while retaining live FINALIZED and resurrected objects. Two native regression
nodes cover GC0–4 x 1/4 workers (20 executions), and a deterministic admission
node adds five collector executions. All pass with the final-death assertion
retained. The original request's 18-case collector/worker matrix also passes.

Focused follow-up: 57 checks passed before the GC4 side-slot case stopped the
run; hot atomic layout and concurrent unlocked growth then passed separately.
Two GC4 failures remain unchanged and reproduce on the exact original
`fbe13c5df` C probes with the production-identical pre-change runtime:
side-slot tracking exits4, retired-index reclaim exits3. GC0 controls pass.
Tests retain those assertions; they are not green GC4 qualification.
The production index test was corrected to account for its already initialized
hash capacity while retaining exact growth, lookup, deletion and retirement
counts. External LLVM reference cases are not owned-runtime evidence.

A registered-thread no-park candidate passes 10 models and two native entries,
but versus phase2 changes single-worker QPS only +0.005% (4/7) despite -0.678%
instructions; it is not independently published or counted as a speed win.
Current post-phase2 profile has 19,938 Running samples: graph acquire/release
call-chain union 17.66%; scheduler `pcc_mutex_lock` chain 6.75% separately.
`swtch_pri`/`pthread_mutex_trylock` are scheduler waits, not graph descendants.
Graph acquire's LL/SC retry-branch PCs account for 5.05% of all samples.
A GC-aware queued-contention prototype is active in an isolated snapshot;
its 11 models pass, but native execution and throughput are not yet qualified.

Claude's GC3 poll/gate correction was also independently executed on the
matching frozen runtime: poll promotes in place, gate copies (2/2). Evidence
here is host pcc0 / owned self / no-libpython / threaded atomic / Mac ARM64,
not current pcc1 or a fixed point. No asyncio was rerun in this follow-up.
Read-back verified durable source, runtimes, measurements, failed controls,
LLDB evidence and profiles:
`/Users/jiamo/pcc-recovery-archive/2026-10-28/codex-p0-handoff-triage/tracked-shards-and-snapshot-lifetime/README.md`.

### Shared lease counters published; ordinary request costs still dominate (2026-10-28)

Code `b4d4b1839` and tests `cba984d4f` are pushed to `shenming/master`.
Nonmoving address guards use 32 static address-selected counters while keeping
public tokens 0/1/2, cross-thread release and root moves. Backend transitions
freeze every counter and roll back partial freezes before reopening admission.
Indexed leases and tail reservations remain central. The diagnostic total is
explicitly quiescent-only; no exact concurrent total is promised.

Seven alternating pairs: 4-worker QPS +8.45% (7/7 faster), 2-worker +6.02%,
1-worker -1.28%; single-worker instructions +1.46%. The preceding independent
packet also shows 4-worker +8.56% (7/7). Publication retains the small single
worker regression as unfinished work. Latest packet medians are 22,237 /
22,804 / 23,730 QPS for 1/2/4 workers. The 268,000 four-worker target remains
unmet. Exact new wrappers: 79 passed, including 20 native GC0–4/probe0/2 cases;
original request integration: 18/18; post-push focused lease/root/list scope:
22 passed. Mode is host pcc0 / owned self / no-libpython / threaded atomic on
Mac ARM64, not current pcc1 or fixed-point qualification.

The ordinary synchronous request is now measured on this runtime rather than
reusing round15 receipts: **321,245 instructions and 19.5 microseconds per
request** (five N/2N pairs), with correct output on GC0–4. JSON encoding is
81.39% of its inclusive CPU samples. A checked custom census accounts for
288,203 instructions, about 89.7% of the uninstrumented estimate: at least
5,741 call instructions, 3,790 cross-object calls, 187 root stores and 133
graph-lock pairs per request. Exclusive name-based GC/ownership/call-protocol
buckets total 187,318 instructions; these are not all removable. Stack memory,
register moves and return protection account for another view of the same
work, not an additive category. The instrumented list object had six invalid
call-map PCs; the census retains the original list object and explicitly
reports its own instructions as unmeasured, with all map checks retained.

The generic exact-tuple output-slot candidate is not published. Its runtime,
52 production-body models and four native/IR wrappers pass. The completed
five-pair synchronous measurement reduces instructions by 1.82% paired median
with no time improvement; seven full-request pairs regress four-worker QPS
by 5.13% (only 3/7 positive). This rejects the candidate as a performance fix.
The broad call/ownership protocol remains unresolved.
No asyncio was rerun and no semantic contract was relaxed.
A bounded cross-object import/owned-inliner diagnostic subsequently stopped
before executable qualification: precise stack-map analysis rejected stale
managed SSA `gen.current.2637.90` in
`user_os__EnvironView___iter____gen_resume`. No check was disabled and there
is no throughput result for that experiment. Its script, failing IR and
receipt are in the archive's `inline-diagnostic/` supplement. This remains an
open optimization/GC-root analysis boundary, not a delivered speedup.
A bounded continuation retained the original OS module in both arms. The
remaining control/candidate images passed GC0–4 (10/10). Five paired N/2N
runs reduced instructions by 2.25% versus the same-pass control, but both
medians remained 19.5 microseconds/request (only 1/5 faster). There is no
reliable time improvement and the experiment was not published. Compared
with the original artifact, the 3.54% instruction reduction also includes the
extra pass; it is not solely cross-object importing. Completed receipts are
in `inline-diagnostic/pair3/` in the same durable archive.
A bounded 128-instruction follow-up also retains all safety checks and the
original generator/resume bodies. Both arms pass GC0–4 (10/10). Five pairs
show original / same-object control / cross-object candidate medians of
320,990 / 309,579 / 303,568 instructions and 19.5 / 20.0 / 20.0 microseconds.
The candidate reduces instructions 1.76% against its paired control, but has
no reliable time gain; it is not published.

Claude's direct-call argument root reuse (`61a7995c0`, tests `c36474989`) is
now integrated without conflict. Stable local/parameter roots avoid duplicate
pins, while later complex arguments, owned temporaries, globals, resumable
bodies and may-park calls retain protection. Fresh matching runtimes from
`1de8d7a0d` and `c36474989` compile and execute both original request programs
under GC0–4 (20 checks total); the two exact test files pass 10 tests, including
GC4 movement. Five synchronous pairs give 321,603 -> 314,545 instructions
(-2.19% by median ratio), with median times 20.0 -> 19.5 microseconds.
Seven full-request pairs give single-worker QPS +0.95% (5/7 positive), medians
22,556 -> 22,754. Four-worker paired QPS is +1.45% (4/7), while medians are
22,924 -> 22,547 and instructions/CPU do not improve: no reliable four-worker
gain. JSON IR loses pin/unpin call sites but keeps its lease and root-store
counts. These are host-pcc0 native results, not current-pcc1 qualification.

A GC3 proof boundary remains open before extending this optimization. A native
protocol probe with `PCC_GC_MINOR_ALLOC_MAX=4096` confirms that a rooted object
can move while another thread is stopped at an ordinary runtime poll: the
un-pinned raw address then cannot heal (exit10), while the pinned control
passes. Thus the new comment's gate-only relocation premise is not true for
all collectors. This is not an end-to-end direct-call regression: actual
Python controls with a later large integer or dynamically allocated bytes
literal both pass; movement of that specific argument was not witnessed.
No GC3 policy or peer optimization was changed based only on this probe.
Completed measurement, source/artifact identities, validation and failed
setup receipts are read-back verified in:
`/Users/jiamo/pcc-recovery-archive/2026-10-28/codex-p0-handoff-triage/sharded-leases-and-request-costs/claude-direct-arguments-followup/README.md`.

Durable source, runtimes, tests, profiles, failures and current census:
`/Users/jiamo/pcc-recovery-archive/2026-10-28/codex-p0-handoff-triage/sharded-leases-and-request-costs/README.md`.

### Threaded list lifetime repairs published; shared contention remains (2026-10-28)

Published code/tests: `b37af48b7`/`7ca6e15d4` for GC0 list stores,
`8220d8cad`/`e8a8732d5` for owning getters, and
`e5f618c6b`/`5d8d13cf4` for scalar getters and their call effects.
Threaded GC0 now uses the existing graph-protected store/load-retain paths.
The store race reproduced duplicate release; owning reads failed in four
preimage cases; scalar replacement/growth returned invalid values in five of
eight preimage cases. Corrected native cases all pass, including concurrent
public writers, finalizers, weakrefs, resurrection and GC0–4 owner lifetime.
Scalar consumers actually select the changed helper and execute on GC0–4;
stale readonly/willreturn/nounwind declarations were removed and all 183
runtime objects rebuilt. Published pytest checks: ten store checks, two owning
getter wrappers, three scalar wrappers; three CLI index checks also pass after
retaining the frozen environment with UV_NO_SYNC=1. The initial nested uv
setup failure is preserved in the receipts, not reported as a semantic pass.

On the corrected frozen source, seven rotating 40,000-request/C100/delay0
comparisons give baseline medians **23,928 / 28,844 / 25,369 QPS** for 1/2/4
workers. Four workers consume about 3.80 CPU cores over the complete run.
The fixed four-worker asyncio target remains **268,000 QPS**, with no asyncio
rerun. Execution is host pcc0 / owned self / no-libpython / threaded atomic,
on Mac ARM64; current pcc1 and fixed-point qualification remain open.

A root-copy candidate using the existing admitted retain/store helper passed
its native ownership/concurrency/lifetime checks and emitted-IR plan-bypass
check, but was **rejected**: paired single-worker +1.38%, two-worker +1.69%,
four-worker -11.23% (only 2/7 faster). It was never applied to production.
The next bounded candidate shards nonmoving address-lease mode guards while
preserving token values, cross-thread release and complete backend-transition
exclusion. It is not yet validated or measured. Both single-worker efficiency
and four-worker scaling remain unfinished goals.

Source manifests, separate later test-input overlays, before/after native
artifacts, runtimes, failures and timing receipts are durably preserved at:
`/Users/jiamo/pcc-recovery-archive/2026-10-28/codex-p0-handoff-triage/list-lifetime-and-root-copy/README.md`.

### Worker-local object reuse published; scaling still open (2026-10-28)

Code `cd92812f2` and tests `ed8155fcf` are pushed to `shenming/master` as
ikshengmin, after preserving and rebasing over peers' commits. Registered
worker threads cache bounded batches of FREE object-slab cells, refill/drain
centrally in batches, and drain on thread exit. The allocator owns its enable
flag, so standalone C allocation does not import the managed thread kernel.
Raw-slab reclamation, refcounts, provenance and all five GC protocols remain.

Seven rotating 40,000-request/C100/delay0 pairs, host pcc0 / owned self /
no-libpython / threaded atomic on Mac ARM64: median PCC throughput is **22,500
QPS with 1 worker, 22,119 with 2, and 22,315 with 4**. Four-worker paired QPS
improves **12.26%, 7/7 faster**, and process CPU falls 10.70%. Single-worker QPS
regresses 2.68% paired; the maintainer explicitly approved publishing this
tradeoff and continuing both targets. The fixed asyncio four-worker reference
is 268,000 QPS: PCC remains about 12 times slower and still scales almost flat.
No asyncio rerun, current-pcc1, or fixed-point claim is made.

Two publication tests pass, including the actual warmed cache path while the
central lock is held, all 11 classes, cross-thread free, exit cleanup, exact
accounting, duplicate-free checks, and standalone C execution/import closure.
Original GC0–4 × 1/2/4 requests plus three longer GC4 repeats pass, 18/18.
The raw-slab trim fixture passes through owned C. Post-push finalizer, weakref,
resurrection and duplicate-free scope: **37 passed, one pcc1 deselected**.

Fresh target-PID Time Profiler attribution has 19,692 Running samples, about
97.5% on P cores. Self CPU: graph-lock acquisition 11.41%, lease acquire/release
7.28%, pin/unpin 5.73%, malloc 2.87%, free 2.69%, object allocation 1.05%.
Do not add inclusive parent shares or turn these into claimed gains.

A later cache hotpath variant was rejected (single worker -3.24%). A data-only
read-mostly configuration isolation also failed acceptance (four workers
-12.36%, 1/7 faster) and was not applied. The allocator-established object-slab origin cache was subsequently published
as code `76f83054b` and test `6d9785b61`; raw/unmarked cells retain the
authoritative fallback. Seven paired comparisons show four-worker +4.64%
(5/7 faster), single-worker +0.59% and instructions -0.64% for one worker.
The original request matrix passes 18/18; focused allocator/pin/lease tests
pass 122 with one pcc1 case deselected. Owned object emission covers Mac ARM64,
Linux ARM64/x86-64 and Windows x86-64; foreign objects were not executed.
Detailed evidence:
`/Users/jiamo/pcc-recovery-archive/2026-10-28/codex-p0-handoff-triage/allocator-origin-and-isolation/README.md`. Further work prioritizes four-worker scaling while tracking and
repairing single-worker cost, in service of write Python, run native.

Durable source, binaries, runtime archives, native tests, raw paired timings,
fresh profile and rejected variants:
`/Users/jiamo/pcc-recovery-archive/2026-10-28/codex-p0-handoff-triage/object-thread-magazines/README.md`.

### Canonical ASCII cache race fixed; batch allocator work open (2026-10-28)

Code `6157936f0` and tests `1aee75d3f` are pushed to `shenming/master`, after
integrating `605cce4d7`. Cold four-thread use reproduced changing ASCII cache
identity within one worker (old native exit 106). The cache now contains 128
image-initialized objects; acquire/release bits publish successful idempotent
managed-pointer registration. Existing pin/lease paths remain unchanged.
Three new tests pass: image layout, three cold native four-thread processes
with all-ASCII identity/provenance/backend-switch/lease checks, and emitted
Python ASCII/Unicode/NUL indexing/iteration/collection under GC0–4. The original
GC0–4 × 1/2/4 request matrix and three longer GC4 repeats pass, 18/18.

This is a correctness fix, **not a performance win**. Seven alternating
40,000-request/C100/delay0 PCC-only pairs give paired QPS deltas of -2.79%
(one carrier, 3/7 positive), -0.26% (two, 3/7), and +0.36% (four, 4/7).
Four-carrier paired CPU is +1.66%, instructions +2.25%; one-carrier instructions
are flat (-0.026%). A separate generic ASCII address-range check in pin/lease
was rejected at -8.39% four-carrier QPS, 0/7 positive, and was never committed.
These frozen packets use `c3323eb6c` plus the identified candidate; a newly
rebuilt `1aee75d3f` baseline also passes ASCII GC0–4 and ten full requests.
No current-pcc1 or fixed-point claim is made.

The maintainer's local `Downloads/3.md` report usefully separates shared-process
contention from single-request work, corrects the Mac ARM topology, and separates
root-store inclusive (19%) from self (2.6%) cost. Its proposals remain hypotheses:
per-carrier leases require a store/load ordering proof plus cross-thread release
and thread-exit handling; TLS pin counters must preserve read/reset behavior.
The worker-local cache follow-up is now published as recorded above.
The fixed >268,000 QPS target remains open.

Native artifacts, raw paired results, failed attempts, source identities and
both local reports are preserved at:
`/Users/jiamo/pcc-recovery-archive/2026-10-28/codex-p0-handoff-triage/ascii-cache-and-report3/README.md`.

### Intrinsic singleton pin writes removed (2026-10-27)

Code `4c64427a7` and tests `2f74e6559` are pushed normally to `shenming/master`.
Only exact image-defined None/True/False/NotImplemented/Ellipsis addresses are
admitted: these objects cannot move or die, so pin/unpin now leave their flags
and pin-balance metric unchanged. The public header states this physical no-op
contract, consistent with their existing foreign-lease admission and
compiler-known literal elision. An arbitrary heap object's IMMORTAL bit does
not admit it; ordinary and runtime-immortalized heap objects retain their pin
flags/count protocol. No per-module optimization or unknown-Dyn trust shortcut.

Seven alternating PCC-only pairs (40,000 requests/C100/delay0, 200 warmups,
no affinity): four-carrier paired median +15.03%, 7/7 faster, same-packet
median 18,786 -> 21,610 QPS, CPU 7.62 -> 6.86 s (-9.97%). Two-carrier paired
+5.50%, 7/7. Single-carrier time is effectively unchanged (+0.70% paired,
4/7; median 23,580 -> 23,464), with a **1.13% instruction increase** from
exact-address checks on ordinary heap objects. A cheap negative filter is
being tested separately to reduce that overhead without admitting heap
immortals. Absolute QPS varies by packet: do not compare the earlier 25,298
directly with this 21,610 as a code regression. The fixed 268,000 target remains
unmet, 12.4x this packet's four-carrier median. Asyncio was not rerun.

The owned native preimage exits 20 on the singleton counter check. Fixed
native checks cover all five singleton headers/counts/refcounts under GC0-4,
ordinary and runtime-immortalized heap controls, and four native threads.
The fixture intentionally retains five immortalized controls. A native Python
program additionally verifies dynamic parameter/return identity while forced
collection and allocation churn run under all five GCs. Post-push **28 passed,
one pcc1 case deselected** include pin ABI, static strings, finalizers, weakrefs
and resurrection. Original GC0-4 x 1/2/4 requests pass 15/15 plus three GC4
four-carrier 10,000-request runs and ten initial GC0 runs. Scope remains
host pcc0 / owned self / no-libpython / threaded atomic, not current pcc1,
full conformance or fixed point.

Read-back-verified frozen sources/runtime, tests and paired timing:
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/intrinsic-singleton-pins/README.md`.
That archive also retains both rejected allocator attempts: class locks alone
regressed four-carrier paired QPS 3.06% (2/7 wins), isolated list heads too
regressed 10.57% (1/7), despite passing their native correctness probes.
The instruction diagnostic and detailed pin caller/IR analysis are preserved at
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/pin-counter-instruction-diagnostic/README.md`.

### Hot atomic storage isolation improves four carriers (2026-10-27)

Code `5e332d544` and tests `963291af8` are pushed normally to `shenming/master`.
The graph-lock word, foreign-lease guard and public scalar pin-balance statistic
have 128 bytes of reserved storage before and after them. Types, atomic
operations, pin flags and the GC protocol are unchanged; this removes sharing
with unrelated neighbouring writes, not contention on each scalar itself.
The reference concept is [Go's cache-line padding](https://go.dev/src/internal/cpu/cpu.go),
including its [128-byte ARM64 choice](https://go.dev/src/internal/cpu/cpu_arm64.go).

Two PCC-only packets use the same frozen binaries, 40,000 requests/C100/delay0,
200 warmups and no affinity. Initial seven pairs: four-carrier paired median
+11.25%, 6/7 faster, median 18,194 -> 20,330 QPS. Small single/two-carrier
regressions prompted nine confirmation pairs: one 24,173 -> 24,173 QPS,
paired -0.19%; two 24,957 -> 26,059, paired +6.45%, 7/9; four 20,824 -> 25,298,
paired +17.83%, 8/9. Four-carrier CPU 7.22 -> 5.99 s (-17.0%), instructions
31.59 -> 30.66 billion (-2.94%). Absolute throughput varies between packets;
these are within-packet comparisons. The fixed 268,000 target remains about
10.6x higher than the latest median. No asyncio rerun or current-pcc1 claim.

The actual native test reads live neighbour addresses (not padding symbols)
and executes scalar pin counts, nested pins and slot transfer under GC0-4.
It passes, along with ten original GC0 four-carrier request runs, GC0-4 x
1/2/4-carrier matrix (15/15), and three GC4 four-carrier 10,000-request runs.
Four post-push focused checks pass after correcting a test invocation that
mixed worktree sources with frozen compiler imports; the failed receipt is
retained. Existing exact-object checks use self assembly plus clang as a
labeled assembly/link oracle; actual native and cross-target checks use owned
compilation/emission independently. The three changed modules produce all
12 objects across Mac ARM64, Linux ARM64/x64 and Windows x64. Foreign execution
and foreign-platform throughput remain unqualified.

A fresh Time Profiler capture has 20,156 target Running samples: graph-lock
acquire 8.75%, pin/unpin 10.49%, free 4.59%, lease acquire 4.51%, object allocation
3.53% self CPU. The prior free-metadata graph callers no longer dominate.
Different captures are attribution evidence, not an isolated speed comparison.
Read-back-verified frozen source/runtime, both timing packets, actual ABI
executions, failed/corrected checks and sanitized CPU evidence:
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/hot-atomic-storage/README.md`.

### Terminal GC0 metadata transactions reduced (2026-10-27)

Code `b21e27ac6` and tests `6daa51469` are pushed normally to `shenming/master`.
Only zero-reference, DEALLOCATING, structurally proven object-family allocations
in stable GC0 mode can bypass an empty identity/tracing metadata transaction.
The authoritative identity-list head is acquire-read and release-published;
identity presence, diagnostics, transitions and other origins/collectors keep
existing paths. Free-watch evidence, pointer retirement and address protection
remain. An owned native two-thread test fails before the change and passes
after it; it also checks that an identity still requires the graph lock.

Both performance arms include source `dbfd50518`, with the separately published
unsafe global-pointer-width repair (`7ba306630`, tests `dbfd50518`). Seven
alternating PCC-only pairs at 40,000 requests/C100/delay 0 give one-carrier
median 22,507 -> 23,548 QPS (paired median +4.68%, 7/7 faster), two-carrier
18,779 -> 19,667 (+5.42% paired, 6/7). Four-carrier medians are 17,608 -> 17,514;
paired median is +6.98% but only 4/7 are faster: **no stable four-carrier QPS
gain is accepted**. Instructions decline 3.58% / 4.42% / 6.84% for 1/2/4
carriers; four-carrier CPU 8.28 -> 8.32 seconds has no reduction. The fixed
268,000 QPS target and negative scaling remain open. Asyncio was not rerun.

Post-push focused verification: **55 passed, one native-pcc1 case deselected**,
including last-decref finalizers, weakrefs, resurrection and five-backend
finalizer/resurrection execution. Original requests pass GC0-4 x 1/2/4 carriers
(15/15), plus three GC4 four-carrier 10,000-request runs; ten initial GC0
four-carrier runs also pass. This is host pcc0 / owned self / no-libpython /
threaded atomic runtime on Mac ARM64, not current pcc1 or a fixed point.
Linux/Windows performance remains unmeasured. Read-back-verified source,
runtime, preimage, timing and test receipts:
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/gc0-terminal-free/README.md`.
The global-pointer repair has independent actual-native/zero-build-subprocess
proof in the sibling `unsafe-global-pointer-access/README.md`.

### GC4 allocation overlap resolved; carrier polling increment (2026-10-27)

The original four-carrier GC4 request failure now passes after a second,
independently demonstrated repair: code `032bc2597`, native tests `f5615b536`,
normally pushed to shenming/master. A pre-zero-fill tripwire caught a new
40-byte allocation at page offset 608 overlapping a refcount-1 exception at
640–704. Three payload-growth APIs extended an old virtual reservation across
later live allocations; disposal then rewound the page bump through them.
The owned-C preimage overwrites a live neighbour through all three APIs. Only
current-tail reservations may now grow in place; other growth uses the existing
external payload representation, preserving owner tracing and barriers.
Nine native growth scenarios pass (three test cases), followed by 63 focused
checks and three existing relocation/card probes executed through owned C.
The original request passes all GC0–4 x 1/2/4-carrier combinations and three
longer four-carrier GC4 runs at 10,000 requests, as well as ten initial GC4
repetitions at 1,000 requests. The earlier unresolved-failure reports below
are historical preimages, not the status of this repaired artifact.

Accepted throughput increments are now 16, with the new increment explicitly
scoped to multiple carriers. `80bd15569` and tests `8b6735041` batch persistent
carrier timer/IO polling over at most 64 ready steps, preserving per-step stop
checks and the idle wakeup protocol. Four models and a native timer-progress
program under GC0–4 pass. The optimized artifact repeats the 15 request-matrix
cases, three longer GC4 runs, and initial ten GC4 repetitions successfully.
Seven alternating matched runs give four-carrier 15,336.86 -> 17,157.74 QPS
(+11.87%; paired median +10.55%; 6/7 faster), CPU 8.92 -> 8.41 seconds for
40,000 requests (-5.72%). Two carriers improve 17,791.23 -> 18,651.37 (+4.83%).
No reliable single-carrier gain is accepted. Same-packet asyncio process
workers give 82,810 / 157,014 / 268,180 QPS at 1/2/4; PCC gives 22,470 / 18,651 /
17,158. The single-worker 3.69x gap, four-worker 15.63x gap and PCC negative
scaling remain P0. Do not fold this multicarrier gain into prior single-carrier
cumulative ratios. Whole-run CPU includes startup/warmup/barriers; no affinity.

Post-change profiling still attributes 17.30% of filtered stack samples to graph
lock acquisition and 7.94% to mutex acquisition. Per-carrier queues still take
the shared scheduler mutex. Current graph-lock contention repeatedly attempts
CAS and enters the full safepoint path. A read-wait candidate preserved CAS admission, no-park and stop progress
(21 checks plus ten original GC4 requests pass), but seven paired runs show
only +0.0123% four-carrier QPS, 4/7 faster, and no CPU reduction. It was not
accepted or shipped. Exact evidence is archived at
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/graph-read-wait-unaccepted/README.md`.
The next target is the shared mutex wrapper's trylock/yield loop. All three
platforms use the same kernel; Linux provides owned futex waits, Windows SRW
locks/CVs, and Darwin pthread waits. Actual Linux/Windows performance remains
unmeasured. Blocking acquisition must release an acquired application mutex
before waiting for an in-progress stopped-world epoch to finish.
Relevant source comparisons are Rust's
[futex mutex](https://doc.rust-lang.org/src/std/sys/sync/mutex/futex.rs.html),
Go's [runtime spin-bit lock](https://go.dev/src/runtime/lock_spinbit.go), and
OpenJDK's [ObjectMonitor](https://github.com/openjdk/jdk/blob/master/src/hotspot/share/runtime/objectMonitor.cpp)
and [ForkJoinPool](https://github.com/openjdk/jdk/blob/master/src/java.base/share/classes/java/util/concurrent/ForkJoinPool.java).
PCC still needs reduced central state and GC-safe contention handling, not
just different names for per-carrier queues.

Exact source/runtime identities, before/after native output, failed diagnostic
attempts, paired measurements and member-readback SHA-256 verification:
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/gc4-payload-growth-and-carrier-batch/README.md`.
Evidence remains host pcc0 / owned self / no-libpython / threaded atomic native
handler execution. Current pcc1, fixed point, broader integration/conformance,
five-GC pause/concurrency qualification and performance parity remain open.

### Multicarrier P0 and GC4 store-buffer removal (2026-10-27)

A real reentrant deletion defect is fixed and normally pushed to
`shenming/master`: code `1b0b9b7a5`, regression `e200c0043`, existing test-fixture
repair `2158592f8`. GC4 owner removal previously decrefed buffered children
while retaining queue predecessor/successor pointers; recursive owner removal
could free those nodes. It now detaches all matching entries and updates the
indexes before disposing the private chain in original order. The original
owned-C/native-runtime repro returned 20 with a dangling queue head; the fixed
runtime passes. 34 nonduplicated focused checks pass, including native GC0-4
explicit collection and finalizer/resurrection. Direct, nested, and child-join
four-carrier probes at 10,000 tasks pass 9/9. Their zero relocation counters
limit that evidence. No current native-pcc1/fixed-point qualification is claimed.

**The full four-carrier GC4 request remains P0 and still fails.** A second
freeze/build of merged source `2158592f8` repeats the five new regression passes
but the original request fails with a NoneType iterator error. Fixed-runtime
LLDB also catches a corrupt dictionary entry table and dead result decref during
virtual-thread destruction. A same-binary automatic-GC toggle fails 3/3 enabled
and 2/3 disabled; this rejects attributing the whole failure solely to automatic
tracing, without establishing the next root cause. A result watchpoint diagnostic
timed out at 45 seconds and is not passing evidence. Unproven handoff, TLS,
delegation and lock-spin experiments are preserved outside the repository and
were not shipped. GC4 still has STW seed/finish/remap/full-collection paths;
five-GC concurrency and pause qualification remain open.

Latest matched throughput measurements precede this correctness repair: 40,000
requests, total C100, delay 0, three rotating repetitions. PCC uses one process
with 1/2/4 carriers; asyncio uses 1/2/4 Python 3.15.0 GIL-enabled processes, each
with one loop and divided work. PCC QPS is 22,525 / 18,153 / 16,151; asyncio is
85,251 / 159,731 / 252,932. Whole-run average cores are 0.987 / 1.916 / 3.654
versus 0.888 / 1.609 / 2.850, including startup/warmup/barrier time; no affinity
is pinned. Single-worker gap 3.78x and four-worker gap 15.66x remain unacceptable.
Four-carrier filtered stack samples attribute 17.22% inclusive to graph-lock acquisition
and 9.53% to mutex acquisition, mostly scheduler callers; these overlap and do
not constitute the whole gap. No new throughput increment is accepted here.

Source, runtime IR, matched measurements, native outputs, failure receipts,
commands, and SHA-256 member-readback verification are archived at
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/multicarrier-and-gc4-buffer-remove/README.md`.
Scope remains host pcc0 / owned self / no-libpython / threaded-atomic native
handler programs, not HTTP or latest-source pcc1. Continue the remaining GC4
lifetime failure first, then multicarrier lock contention and ordinary runtime
costs; retain the broader integration/conformance/self-host goals.

### Runtime performance continuation: borrowed roots and CPU resources (2026-10-27)

Accepted increments are **15/15**. Latest code 38080b33c and tests c9e72a01c
are normally pushed; old test corrections are separate in 7a8e4ffd1. The latest
stable gain is +0.90% full-handler QPS, with a remaining same-packet gap of
3.841x. Overall performance parity is unfinished.

Earlier memset code a7dc2c0a4 and tests b50cdc66a are normally pushed to
shenming/master. `unsafe.memset` now reaches the existing
owned memory-intrinsic lowering, retaining its original pointer return, byte
truncation, argument order and fallback. 54 frozen focused checks, original
sync/handler GC0–4, and both small-nursery GC3 checks pass. Seven paired handler
runs give 23,370.03 -> 23,697.86 QPS (+1.40%, 7/7 faster); sync instructions
334,495 -> 328,539 (-1.78%), median 20 us unchanged. Same-packet asyncio/PCC gap
is **3.880x**, still open. Evidence remains host pcc0 / owned self / no-libpython,
not current native pcc1, HTTP or fixed-point qualification.

Resource clarification: C100 is logical request concurrency. This benchmark
calls `virtual_thread.run(1,4096)`. The earlier seven-run resource packet used
0.26 CPU seconds / 0.28 process-wall seconds for asyncio (0.93 core), versus
0.91 / 0.92 for PCC (0.99 core), including startup/warmup. Separate sampling
of the exact zero-branch PCC artifact found one OS thread in all 40 samples;
asyncio had one in all 11. A three-thread control checked the counting method.
Sampling perturbs timing; those sampled times are not QPS evidence. Physical
affinity was not pinned, and multicore scaling remains unqualified.

Sources, runtime IR, raw results, initial failures and resource observations
are member-readback/SHA-256 verified at
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/memset-and-resource/README.md`.
The root-registration grouping trial is now pushed as fd67eff42/e00505b98.
148 frozen checks and original requests on GC0–4 plus small-nursery GC3 pass.
Both root roles execute in native tests with precise maps on and off. Actual
json._encode entry registrations fall from 15 to 2. Sync instructions fall
328,648 -> 320,662 (-2.43%), but full-handler +0.106% QPS with only 4/7 faster
pairs is not an accepted throughput increment; the count stays 14/15. Its
same-packet asyncio/PCC ratio is 3.829x. Full evidence, including the initial
changed-shape coverage failure, is readback verified at
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/function-root-groups/README.md`.
Entry/leave self instructions were about 5.7% of the preceding sync census,
not the remaining gap. The following isinstance trial reuses unchanged borrowed
parameter roots while keeping counted address leases across classinfo callbacks.
Owned/rebound/captured/generator operands retain independent roots. Related
validation passed in three non-repeated phases (120 + 12 + 14 nodes), including
native GC0–4 callbacks, exceptions, weakrefs, module class rebinding, concurrent
collection, root-group map modes, original requests and small-nursery GC3.
Two older failures remain explicit: pcc.ir class/method linking fails with the
same missing symbols before/after, and the documented ASCII-only upper() gap
still fails its retained Unicode contract test. Whole-suite green is not claimed.

Paired full handler: 23,748.79 -> 23,962.43 QPS (+0.90%, 7/7 faster), instructions
-1.08%. Sync 320,547 -> 316,016 instructions/request (-1.41%), median 20.0 ->
19.5 us. This is a small stable increment, not a solution to the remaining gap.
Same-packet process CPU/wall: asyncio 0.24/0.26 s (0.923 cores), PCC 0.84/0.85 s
(0.988 cores); process counters include startup/warmup. The sample remains
one-carrier GC0, host-pcc0-produced native code. Overall parity, current native
pcc1 validation and the wider P0 goals remain open. Detailed sources, receipts,
known failures and CPU accounting are member-readback/SHA-256 verified at
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/isinstance-borrow/README.md`.
Latest native artifacts were resampled successfully (3,868 sync / 3,831 handler
samples). Sync dictionary materialization is 11.09% inclusive, list append from
slots 11.07%, sorted body 8.38%; handler shares are 5.95%, 4.67%, 4.15%.
Inclusive shares overlap. Root-store inclusive time includes necessary terminal
deallocation and is not all removable bookkeeping. Current dictionary methods
materialize lists before the caller's list/sorted construction; live-view and
iterator semantics need direct validation before eliminating those containers.
No new view implementation is claimed. Hash-verified profile evidence is at
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/runtime-profile-after-round15/README.md`.
Further root-registration tuning is not justified by these small gains.

### Runtime performance continuation: instruction census and zero branches (2026-10-27)

Accepted increments are now **13/15**. Zero-branch code bf8bb2a2e and tests
c8f4e6163 are pushed: sole-use terminal i32/i64/pointer eq/ne-zero conditions
use CBZ/CBNZ, including correct X-register label retention/retargeting. Other
comparisons preserve their value path. 224 focused checks and both original
applications pass on GC0–4 and the 4096-byte GC3 nursery case.
Matched sync: 358,747 -> 334,912 instructions/request (-6.64%), median
21.0 -> 20.0 us. Full handler: 23,014.03 -> 23,319.84 QPS (+1.33%), 6/7 faster
pairs, instructions -6.91%. The instruction saving is not claimed as equal
throughput improvement. The same-packet asyncio/handler gap is 3.890x.

The benchmark explicitly uses `virtual_thread.run(1, 4096)`: one execution
thread carrying C=100 logical requests, compared with one asyncio event loop.
Median process CPU/wall ratios are PCC 0.989 cores and asyncio 0.923 cores;
startup/warmups are included in these process counters. No physical affinity
was pinned. These are not multicore scaling results. Native execution remains
host pcc0 / owned self / no-libpython on macOS ARM64; no new pcc1 qualification.

A bounded sampled-function census accounts for 350,293 original instructions
per request, about 97.6% of the ~358,873 uninstrumented control. It identifies
~24,066 zero-compare/branch pairs and 12,573 instructions in memset. The probes
preserve atomic windows, registers and return protection; actual call-return
PCs match the precise stack-map records. Default GC0/3/4 outputs pass. Its first
small-heap GC3 run failed in both original and instrumented programs and is
retained as failed evidence, not folded into the passed default-mode receipt.

That failure is fixed in 8f3b7bf7e, with tests d3bd6a880, both pushed. Full
nurseries use the established non-nursery allocation fallback inside no-park
regions. Frame promotion resets its registry cursor under the graph lock,
then drops the lock before requesting the stopped-world stack walk. Protection
checks remain. 29 focused checks, GC0–4 original requests and the original
small-heap synchronous/full-handler repros pass. Before-TLS binaries reproduce
the bug. The preceding TLS explicit-call follow-up (c2d20c350/90f1da6f9) has
80 passing focused checks, but +0.34% QPS with 5/7 faster pairs is not counted
as another throughput increment; it reduced instructions by 1.63%.

Exact frozen sources/runtime IR, binaries, CPU resource accounting, debugger
stacks, counter validation, failures and successful checks are member-readback
and SHA-256 verified at
`/Users/jiamo/pcc-recovery-archive/2026-10-27/codex-p0-handoff-triage/census-zero-and-gc3/README.md`.
The memset follow-up and later resource observations are recorded above.
Overall Python-speed parity, multicore scaling and current pcc1 validation remain open.

### Earlier object initialization and TLS values (2026-10-26)

The TLS address-value increment advanced the accepted count to **12/15**. Code 7db29dae4 and tests
d93aaced4 preserve an explicit `global_addr(TLS)` result as a current-thread
address value instead of rematerializing a TLS symbol at every use. Owned
AArch64 and x86-64 emitters lower the address intrinsic through their existing
platform ABIs. Ordinary globals, locking, stop polls and collector protocols
remain intact. The native hot-path audit found three resolver invocations on
normal graph-lock acquisition and two on release despite one source binding.

Matched full handler: 20,672.39 -> 22,083.64 QPS (+6.83%), **7/7 pairs faster**,
instructions -3.55%. Ordinary sync: 376,229 -> 363,954 instructions/request
(-3.26%), median 21.5 us in both arms. This packet's asyncio/handler ratio is
3.831x; parity remains open and cross-packet ratios must not be subtracted.
54 focused checks and original sync/handler executions on GC0–4 pass.
743b53f39 corrects a legacy substrate fixture to admit explicit archives through
the existing strict provenance validator; its 73 admission checks pass.
All three commits are normally pushed to shenming/master. Native execution is
host-pcc0/owned-self/no-libpython on macOS ARM64. Other targets have real object
emission checks; no new pcc1, HTTP or fixed-point claim.

Exact frozen source/runtime/IR, binaries, fresh profiles, commands, all first
failures and successful results are readback/SHA-256 verified at
`/Users/jiamo/pcc-recovery-archive/2026-10-26/codex-p0-handoff-triage/runtime-incremental/tls-address-increment/README.md`.
The subsequent Darwin explicit-call ABI change (c2d20c350/90f1da6f9) is pushed;
its completed results and the newer accepted increment are recorded above.

The first of the five additional throughput increments is verified and normally
pushed to shenming/master: code a6c199f99, tests c36e9e532. Existing-capacity
append now initializes an unowned field from the caller's authoritative root in
one graph-lock transaction on admitted GC0 inputs. It retains before publishing
length, keeps source owners, and rejects configuration transitions, diagnostic
modes, dying/exceptional reference states and a lost CAS. Growth and GC1–4 keep
the original protocol. The deallocation flag is generated from the C header;
that development ABI probe is an external oracle, not runtime construction.

Same-packet controls: ordinary sync 402,358 -> 376,962 instructions/request
(-6.31%), median 23.0 -> 21.5 us. Full handler 19,235.50 -> 20,089.74 QPS
(+4.44%), with 6/7 pairs faster and instructions -3.19%. Thirty-one focused
checks pass, including the owned-C initialization/retention witness, both
normal and provenance-probe concurrent appends, finalizer resurrection and
root replacement. Both original applications execute with correct output on
GC0–4. Scope: host pcc0 / owned self / no-libpython / threaded atomic; no new
native pcc1, HTTP, broad suite or fixed-point claim. That increment left four additional rounds
open; the newer TLS result above completes one of them. Ratios from different
asyncio measurement packets must not be subtracted.

The subsequent root-read leaf-routing trial is **withdrawn**, not a completed
throughput increment. Its initial result was -1.19% QPS (2/7 faster pairs).
Restoring slot-load order yielded +3.75% in a noisy batch and +0.45% on the
unchanged-artifact repeat, both only 5/7 faster. Code a4387cce9 restores the
original public readers; tests 66f0ac944 retain null/immediate/relocation,
thread-poll/return-protection and cross-target object coverage, and correct
the exact root-operations import inventory. Both are normally pushed to
shenming/master. The withdrawal passes 31 focused checks and the original
sync/handler outputs on GC0–4. The generic AArch64 forwarding improvement
remains; cross-target Linux/Windows emission is not native execution evidence.
Frozen source/runtime/IR, both measurement batches, failed checks and the
withdrawal receipts are readback/SHA-256 verified at
`/Users/jiamo/pcc-recovery-archive/2026-10-26/codex-p0-handoff-triage/runtime-incremental/root-read-trial/README.md`.
Post-push checks add seven list-argument/root-replacement/finalizer passes and
18 configuration-guard passes. Two stale source assertions were corrected to
follow cached allocation configuration and the refcount checked/prepare path;
ten executable models cover both initialization states on GC0–4. No production
source changed in that follow-up. Failures and successful checks are archived.
At that withdrawal the accepted-round count stayed at 11/15; the newer TLS
result above advances it to 12/15. The overall parity target remains open.

The preceding a3872b3bd address-register change and 031e3669a/e8bfba850 tests
are also pushed. Its 196 focused checks and original GC0–4 applications pass.
It removes four address moves from a zero-offset atomic-read witness and cuts
sync instructions by 2.02%, but +1.08% handler throughput with only 4/7 faster
pairs was not accepted as a stable throughput win. The bulk-retirement trial
(5064d65be/6964d959c) likewise did not establish a throughput gain; 85b0e08cd
repairs a separately reproduced missing ABI constant in the free-watch fixture.

A scoped dynamic machine-block census reports py_obj itself at 114,691 original
instructions/request: 275 root stores, 617 root reads and 307 frame enter/leave
pairs. Probes preserve registers, flags, BTI landing pads and exclusive atomic
windows; the census is in supported registered-root diagnostic mode and is not
a complete production instruction bill. The full census exceeded its 1 GiB cap.
External LLVM comparisons retain explicit CPU-feature and frame/protection
limitations; their differences are not claimed as removable PCC costs. Fully
owned postpass/inliner alternatives did not establish a useful throughput win
and were not promoted. These results identify costs, not permission to remove
required ownership, collector or callback behavior.

Exact source/runtime/IR identities, commands, node logs, measured binaries,
failed attempts and the usable census are member-hash/readback verified at:
`/Users/jiamo/pcc-recovery-archive/2026-10-26/codex-p0-handoff-triage/runtime-incremental/README.md`.

### 2026-10-25 GC3/GC4 background marking and cleanup recovery

The maintainer requested multiple further rounds; work is active. Production
1efc543c4 fixes moving mark barriers/colors/count ownership; a8358435e admits
GC3/GC4 to the shared background marker, separates completed-mark cleanup from
moving-work requests, and parks completed worker batches at relocation-safe
polls. Four owned-native probes show GC1–GC4 mark progress interleaved with
real heap mutations across an unchanged stop epoch. An overlap witness needs
two separate observed worker advances; missing an OS scheduling window may
retry at most three times, while runtime/corruption errors fail immediately.

Post-push focused qualification: 123 checks passed. The automatic finalizer
program (50,000 cycles in each enabled/disabled/threshold-zero phase), ordinary
synchronous payload and full handler outputs pass on GC0–GC4. A later review
found a cleanup wakeup lost across a disabled gate. The prior runtime fails
the exact native recovery probe; 2cfce937c fixes it. Recovery and overlap native
scope: 6 nodes passed (both recovery nodes run GC1–GC4); 80 model/IR checks
passed. The same three integration scenarios pass GC0–GC4 again after the fix.
Tests and fixture follow-ups are dc028657b, 992077b1f and 89e76a6e2.

These are host-pcc0 / owned-self / no-libpython / threaded-atomic executions.
Some strict-object ABI controls use the external clang/nm oracle and do not
prove execution ownership. The final rebase also preserves peer a7ffc4eb3's
gray forwarding-source retirement fix; that peer file is newer than the
frozen binaries in these packets. No latest-native-pcc1/fixed-point claim.
Source/runtime/binary/log archives were read back and SHA256 verified:
`/Users/jiamo/pcc-recovery-archive/2026-10-25/codex-p0-handoff-triage/moving-mark-workers-a8358435e/README.md`
and
`/Users/jiamo/pcc-recovery-archive/2026-10-25/codex-p0-handoff-triage/moving-mark-wakeup-2cfce937c/README.md`.

Unfinished: GC0 concurrent cycle discovery; moving phases and mutator-owned
sweep/finalization still have stopped-world/application-gate boundaries. Threaded
or concurrent marking does not certify five fully concurrent collectors. The
paired full-handler packet at that point was 6.42x behind asyncio;
this GC packet claims no speedup. The active performance section above records
later same-packet measurements. Continue the ordinary-object/ownership cost
work alongside collector concurrency; do not trade semantics for throughput.

### 2026-10-25 tuple ownership packet and continued concurrent-GC work

User direction now supersedes the earlier pause: “请完成五种 GC 并发化，优化六倍的性能！” and “可以多来几轮”. Continue the full scope; a small local speedup or five-backend thread-safety run is not completion.

The tuple packet began from shenming/master 5990ee1ee. Construction-only tuple
owner transfer landed as cd55b80cd, with 5ea54776d tests and 1a63d2b72's
nested-operand fixture update. Temporary owners move into fresh tuple fields;
shared objects, identity, GC barriers, cycle tracking, exceptions and finalizers
keep their contracts. Empty/wide/splat/generator routes retain their previous
paths. 4d3e2893c / fc388891e reuse the output lease and bound transfer batches.
3a3312c73 / a3bc1e766 fix an introduced scratch-index loop that generated boxed
Python arithmetic: its actual runtime IR grew to 810 lines with py_int_mul/sub,
conversions and root frames, then fell to 294 without those calls after the
ABI indices/stride were explicitly i64. This is bounded internal arithmetic,
not narrowing ordinary Python integers.

All positive and adverse packets remain recorded. The first variant increased
dict-items instructions by 17.23%, with essentially unchanged full-handler QPS;
the lease/batch variant still regressed dict-items and full-handler timing.
The final same-source packet is: tuple-call instructions -4.08% (coarse wall
1800 -> 2000 ns, retained as adverse); dict-items -2.39%, 2600 -> 2400 ns;
ordinary synchronous request -0.40%, 34.0 -> 33.5 microseconds. Full handler:
14095.307 -> 14173.481 QPS (+0.55% median), 5/7 pairs faster with overlapping
ranges, instructions 25.065B -> 25.013B (-0.21%), user CPU 1.43 -> 1.41 s.
Same-packet asyncio 91019.262 QPS leaves 6.42x. This is not a significant overall
win and does not meet the performance goal. Different asyncio denominators do
not describe a trend against the previous 6.26x packet. These are host-pcc0 /
owned-self / no-libpython / threaded-atomic GC0 handler batches, not HTTP or
latest-pcc1 qualification.

Validation includes six native nodes each running GC0–4 (aliases, finalizers,
reachable cycles, two-thread construction/collection, tuple literals, call
arguments, parking and forced OOM); final native packet 6 passed / 66 deselected.
Host/model/IR scopes passed 154, then 129, then 76 nodes; related follow-up scope
214 passed / 2 deselected. These scopes overlap and must not be summed as unique
coverage. Original sync and handler outputs pass GC0–4. Failed compilation,
stale assertions, a deselected OOM retry and a driver syntax failure are retained
and were not reported passing. The final measured snapshot predates peers'
ARM emission/stackmap retirement changes in two backend files; our edited
runtime/codegen paths match published source. No conflict-free peer suite was
repeated merely due to rebase. Current pcc1, full bootstrap/fixed point and full
conformance remain unqualified.

Evidence is compressed and every archive member was read back and hash-checked:
`/Users/jiamo/pcc-recovery-archive/2026-10-25/codex-p0-handoff-triage/tuple-root-transfer-cd55b80cd/README.md`.
This archive closes only that packet; work continues.

Before worker admission, the concurrent-GC audit found one threaded runtime. Background
collector marking is still admitted only for GC1/GC2; GC0 cycles still use STW.
The moving-backend marking prerequisites now have a verified production fix
(8fb59c63 before rebase): GC3/GC4 root and black-to-white insertion barriers,
black allocation during active marking, and exact gray ownership across
oldification, relocation and terminal release. 59 model/atomic/contract checks
passed (1 deselected); owned-native C probes passed for white, gray and black
sources, each executing GC3 and GC4. Two existing GC4 sweep-verdict probes also
passed. Receipts are currently in
`/private/tmp/pcc-p0-concurrent-collectors/native-v4/receipt.json` and
`native-v3/receipt.json`; these establish transitions, not full concurrent
collector progress or a new-source pcc1.

Worker admission, lifecycle and native mutator-progress checks remain next.
GC4 relocation still requires other mutators at application gates. GC0's
separate tracked-node index and immediate reference-count/finalizer semantics
require their own concurrent-cycle design. These gaps do not explain the
measured GC0 sixfold slowdown by themselves. Root ownership/protocol and
shared-lock costs remain the larger performance targets; further isolated
constructor tuning is not enough.

### 2026-10-24 round closeout: nullable exhaustion, live list iteration, pause request

User instruction for this task: “这一轮结束暂停”. This round is closed;
remaining P0 scope is retained for resume. No overall completion is claimed.

The previously pushed e902c8d25 / 373dd6b38 nullable-iteration change now has
completed follow-up evidence: 59 model/IR checks (12 deselected), six native
nodes each executing GC0–4, original handler outputs on GC0–4, and 28 further
regression nodes (26 deselected). Internal native exhaustion avoids creating
StopIteration; explicit next(), user/generator errors, generator return values
and the separate tuple-wrapper path retain their existing contracts. Lifecycle
counts show 28 -> 25 managed allocations per ordinary sync request, exceptions
3 -> 0; iterator objects remain 3 and strings remain 4. These are logging counts,
not timing or escape-analysis proof. A longer same-binary synchronous packet
confirms 641740.343 -> 611913.977 instructions and 37.6 -> 35.0 microseconds;
the earlier adverse short wall result is retained. The corresponding original
handler gain was only +1.46%, with a same-packet 6.31x gap. Detailed verified
source/runtime/native artifacts and failures are archived at:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/nullable-iteration-e902c8d25/README.md`.

While checking a possible indexed-iteration optimization, the existing typed
list loop reproduced a real semantic error on GC0–4: an appended list returned
[1] instead of [1, 2, 3]. Code 0bf5b4e9b and tests 25c85f39a / 2394b5618 make
list steps observe live contents, while preserving independent source ownership,
relocation, false values and explicit machine-int behavior. Initial post scope
passed 22 / 4 deselected. A redundant tuple-header pointer load introduced by
that change was removed in 5f5ee5c65, with IR regression 1c9f9a418. Final focused
file: 11 passed; original handler outputs pass GC0–4. The explicit i64 program
both executes and emits py_list_get_i64_nonnegative. All 949 final frozen
production files match 1c9f9a418. All five commits are normally pushed to
shenming/master; peers' bounded signature-verification commits were integrated
without conflicts. The initial runtime-build worker setting exceeded the 1 GiB
cap; the guard stopped its children. The successful retries correctly used two
runtime workers, with unchanged cap/reserve/timeouts. The failed run is retained.

This semantic fix is not a claimed performance win. Its first 20,000-request
packet was -2.25% median QPS; a longer initial packet was -1.37%. After removing
the redundant tuple load, five pairs of 100,000 requests give 13576.737 ->
13514.920 QPS (-0.46%), 2/5 pairs faster with overlapping ranges, instructions
124.171B -> 124.207B (+0.028%), cycles 24.457B -> 24.606B and user CPU 7.33 ->
7.37 seconds. Residual performance uncertainty stays open. Same-packet asyncio
84661.866 QPS leaves a 6.26x gap. Different asyncio denominators must not be
combined into an improvement claim. These are host-pcc0/owned-self/no-libpython
handler batches, not HTTP, latest pcc1 or fixed-point evidence.

The finished diagnostic now identifies all twelve list/tuple allocations in one
unchanged ordinary request by actual native call stacks: six lists (notification,
output buffer, two dict materializations, two sorted working lists), six tuples
(one single-argument call tuple, one six-argument signature-binding tuple, four
dictionary key/value pairs). External LLDB was used only for this diagnostic;
the program still prints the verified 86-byte total and exits zero. This does
not establish an escape fraction. There is no empty tuple allocation per sync
request. Actual emitter IR shows the hot ten-parameter recursive call uses the
direct ABI with ten pins/unpins, not the wide-call list-to-tuple fallback. Old
matching profiles put pin/unpin/borrowed loads below 4% of sync work; rewriting
only that layer would not address the remaining gap. Larger container lifetime,
root ownership and retirement costs remain the performance focus on resume.

Final source, both runtimes, tests, adverse/final timing packets, raw emitter IR,
all allocation stacks and commands were read back and hash-verified in:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/list-live-iteration-0bf5b4e9b/README.md`.
Run binaries are compressed to avoid repeated large temporary copies. The prior
requested cleanup removed about 8.1 GB of this task's verified duplicate and
rebuildable artifacts; source worktrees, active inputs and the native pcc1
baseline remained intact. Its manifest is in
`/Users/jiamo/pcc-recovery-archive/2026-10-23/codex-p0-handoff-triage/cleanup-2026-10-24-temporary-builds/README.md`.

Latest-source native pcc1 and full bootstrap/fixed point, complete conformance,
concurrent collector equality, virtual-thread scaling and the throughput target
remain unfinished. The already authorized single stage1 has not been run in
this round. No new optimization or broad gate is launched after closeout.

Historical wins require their recorded configuration: the gateway's September
10 clean C100 reproduction (86,862 vs 84,456 req/s) explicitly used external
LLVM runtime merge/emission. The September 26 owned report wins at C1/C10 but
remains behind at C100 (pcc1 66,778.9 vs asyncio 86,504.3); its compiler receipt
explicitly records PCC_WITH_THREADS=0. These differences do not quantify the
entire later regression. Current threaded ownership cost and emission quality
still need direct, source/artifact-bound comparisons rather than date ratios.

Direct integration replay of newer unpack and synchronous-root programs with
the new pcc1, compiler qualification of the later atomic-lease runtime change,
full fixed point and five-GC bootstrap remain unfinished. The separate fallback-baseline check was stopped when its complete
compiler-closure work was identified, to avoid competing heavy builds; do not
count that partial run as a pass. Additional parked unpack shapes, public C
lifetime/callback
contracts, CPython Lib/test conformance and runtime/gateway performance
remain unfinished. The historical pause notes below describe their recorded
revision; completed follow-up shapes above supersede those failure claims.

## P0 follow-up: virtual-thread call semantics and generator frames (2026-10-23, Claude)

Source: `shenming/master` after 00e17faeb (commits named below).

- Imported may-park callees (78194474f, test 828f476d7): a call to a live
  `from m import f` binding took the binding's dynamic call even when `f`
  may park, so a virtual thread got the callee's partial result (None). The
  call now reads the binding when it runs; while it is still the provider's
  function its native body creates the child continuation, any other
  callable is called deferred, and a continuation is driven through the
  caller. Rebinding the import, keywords, star arguments and exceptions are
  covered by `test_cross_module_may_park_import_keeps_call_semantics`.
- Operands evaluated before a park: in a resumable function a parking call
  inside a larger expression split the statement, and every SSA value
  computed before the park was undefined on the resume edge. 8 of 15 shapes
  failed the IR verifier (`items.append(fetch(1))`, `plain(a, fetch(1))`,
  `[a, fetch(1)]`, `xs[fetch(0) + 1]`, ...), and `seen.add(fetch(1))` added
  to an entry-alloca receiver root that the resumed activation found empty,
  so the set stayed empty. Statements (expression, assignment, `return`,
  `raise`, `for` iterable, `x op= ...` on a plain local) and `if`/`while`
  truth tests now evaluate the operands Python evaluates before the park,
  and the parking operand, into generator frame roots in evaluation order
  and lower the rest from them (`plan_generator_statement_spills`,
  faf9982d6). Not covered yet (they still fail the IR verifier):
  `obj.a op= ...`, `d[k] op= ...` and `global` targets, a park inside a
  conditional operand (`and`/`or` right side, `x if c else y` arms) or an
  assignment target, and a star argument before the park. Test:
  `tests/python/test_vthread_parking_operands.py`.
- Generator frames kept values after resuming: a resumed body copied its
  locals and operand roots out of the heap frame, which kept each value
  saved at the last suspension alive until the next suspension or
  completion. A local rebound after a `yield` was finalized late (CPython
  prints `['drop x']` for the probe in
  `test_resumed_generator_frame_does_not_keep_rebound_locals`, pcc printed
  `[]` on all collectors). Resume now moves each owner out of the frame
  (`py_gen_frame_take`, 6c719282f); the frame holds values only while
  suspended.
- Found, being fixed next: augmented assignment on builtin mutable types
  does not mutate in place. `py_obj_inplace_op` falls back to the binary
  operator for every non-instance, so `lst += [2]` (Dyn locals, attribute
  and subscript targets), `lst *= 2`, `ba += b"b"`, `d |= {...}` and
  `holder["s"] |= {...}` build new objects and aliases keep the old value;
  `|=`, `&=`, `^=`, `<<=`, `>>=`, `**=` and `@=` never reach `__i*__` at
  all. Only a local set's `|=` (its own path) matches CPython.
- Ownership-contract tests (890956af1, 00e17faeb) model the two-root
  cleanup helper, the slot-status reporter and the `_mapped` frame entry
  points; 39 of their failures were stale models of correct IR.
- Performance is unchanged since the 2026-10-22 sections below: st_alloc
  GC4 5.03 s / GC0 0.87 s; the gateway has not been re-measured.

Open at the 2026-10-23 pause (maintainer asked to pause). Handoff:
`/Users/jiamo/pcc-recovery-archive/2026-10-23/session-77600949-uncommitted/HANDOFF.md`
(with repro scripts, profiling and survey tools, and test logs beside it).

- Committed at the pause: `@=` in pcc's own lexer and parser (605072731);
  augmented assignment in place as CPython does, through
  `py_obj_inplace_op` for all thirteen operators and every list, dict, set,
  dyn and instance target (a4f9e4ba7); the remaining parking-operand spills
  for `obj.a`, `d[k]` and `global` `op=` targets, a parking assignment
  target and a star operand before the park (cda11fcfd); tests 22c578d1d.
  `test_inplace_operator_aliasing` and `test_vthread_parking_operands` pass
  on all five collectors.
- Untriaged at that pause; subsequently reproduced on d5ccb2404 and repaired
  in the published handoff-triage commits above:
  `test_py_set_type::test_dict_keys_view_union_with_set_is_set_operation`
  (a runtime traceback; `keys() | set` is the first suspect),
  `test_native_set_inplace_update::test_inplace_update_uses_native_runtime`
  (6 IR-shape cases), `test_py_typed_int_unboxed` (7 IR-shape cases) and
  `test_cext_inplace_number_dispatch::test_cext_inplace_true_divide_and_fallback`
  (`PCC_SELF_LINK=cc` oracle output).
- Still failing the self IR verifier: a park inside a conditional operand
  in value context (`and`/`or` right side, `x if c else y` arms), a `**m`
  operand before the park, and a parking target in a multi-target
  assignment.
- `tests/python/test_gc_index_unlocked_readers.py` compiles its C harness and
  the index module's assembly with external `clang`; move it to the owned
  C path (`tests/owned_c_execution.py`).
- The bb4f27eff snapshot run (46 generator/async/coroutine/vthread files,
  all green) did not set `PYTHONPATH=$SNAP`, so a subprocess started outside
  the snapshot directory may have imported the worktree; rerun with it
  before relying on that run. Its 4 owned-C probe failures (`header not
  found: stdint.h`) came from leaving `utils/` out of the snapshot and pass
  on the worktree.
- Next performance target: in the earlier gateway C100 profile, runtime
  ports register 14-16 scheduler-root handles per dict/call operation, each
  a graph-locked register/unregister pair with a malloc; the 2026-09-26
  single-thread build measured 65,914 req/s against about 10,000 now.

## P0 packages: what landed, measurements, method operands (2026-10-22, Claude)

Source: `shenming/master` 92c9daf56. Package state against
`docs/plans/p0-threaded-concurrent-gc-runtime.md`, read from the code:

- 1 (threaded runtime only): done (`tests/test_threaded_only_runtime.py`).
- 2 (lock-free rooting): partial. GC0/1/2 foreign leases return the no-op
  token; pin/unpin are still calls, and each one also adds to the shared
  `pcc_gc_metric_pin` counter atomically.
- 3 (stack walks for every thread): the walk covers every registered
  thread (`pcc_thread_collect_stopped_frames`), but compiled code still
  calls `pcc_gc_frame_{enter,leave}{,_lifo}_mapped` for every frame; on
  GC1..GC4 each call asks `pcc_gc_frame_slots_walked`, a thread-local
  lookup (a `_tlv_get_addr` call on Darwin).
- 4 (protocol-free compiled code): not done; frame, lease, pin and
  `store_root` calls remain.
- 5 (biased reference counts): not started.
- 6 (collector threads): GC1 and GC2 only.
- 7 (measurement): landed in 5c1386b4a. Programs in
  `tests/benchmarks/threading/programs`; `scripts/run_threading_benchmarks.py`
  records a time only for a run whose result fields equal CPython's;
  `tests/benchmarks/threading/test_threading_benchmark_programs.py` checks
  every program at check sizes on all five collectors.

Baseline at 22e184a61 (Darwin arm64, default sizes, median of 3 runs, the
program's own seconds): st_fib CPython 0.054, GC0 0.284, GC4 1.012;
st_alloc CPython 0.387, GC0 3.27, GC4 39.3; st_objects CPython 0.617, GC0
26.3. The run was stopped there to profile; the threaded programs and the
other collectors are not measured yet. Repro:
`uv run python scripts/run_threading_benchmarks.py --out /tmp/t.jsonl --backends 0,4 --threads 1,4 --repeats 3`.

Method operands (4a49cb029). A method call whose result an expression
consumes as an object (`s += v.dot(acc) % 7`) took the runtime-bound route:
getattr built a bound method (signature tuple, captured defaults,
fourteen scheduler-root handles) and `py_obj_call_slots` called it. In
st_objects that was 39% of GC0 time in method binding and 23% in the
generic call. A known class's method on an instance receiver now calls
directly and publishes into the operand slot at the return instruction.
st_objects: GC0 26.3 s -> 6.4 s (CPython 0.61 s); at 600,000 iterations GC4
35.5 s -> 6.4 s. Direct dispatch also ignored a method rebound in another
function (`A.f = g` in main, `a.f()` in g); a method name the module
assigns or deletes anywhere is now looked up at run time everywhere. Tests:
`tests/python/test_method_operand_direct_calls.py`,
`tests/python/test_class_method_rebinding.py`.

C-API threads (25c2540cb): `PyGILState_Ensure`/`Release` attach and detach
a raw thread, `PyEval_SaveThread`/`RestoreThread` put a thread in the
blocking state, as CPython does. Test: `tests/python/test_capi_thread_attach.py`.

Single-mutator mode (graph lock elided and plain GC0 counts until a second
thread exists) measured 10.9k -> 13.2k req/s on the gateway C100 benchmark
but was not committed: the maintainer's 2026-10-21 direction is no
single-thread bridge; packages 4 and 5 are the route. Its patch is in the
session scratchpad only.

Gateway C100 (GC0, native 10.9k req/s against asyncio 86k on one thread):
graph-lock protocol 12.5% of samples, `_tlv_get_addr` 7.2%, atomic
incref/decref about 11.5%, frame enter/leave with slot zeroing 8.3%,
`store_root`/store plans/`load_ptr` about 7.5%, pins and leases 3.4%.

Open, in the order they are being taken:

- Receivers of unknown class in operands (`o.m() + 1`) still bind and call
  through `py_obj_call_slots`. The non-operand path `_emit_loaded_method_call`
  avoids binding but holds the callable and receiver as raw values across
  argument evaluation, which no moving collector updates (the precise
  stack-map planner follows registered slots only); it needs pins or slots
  before the operand path can reuse it.
- `import mod; mod.f()` is a live module lookup plus `py_obj_call_slots` on
  every call; `from mod import f` calls directly.
- GC4 st_alloc is 12x GC0; not profiled yet.
- Darwin thread-locals: each access calls the descriptor thunk. On macOS
  26.5 the descriptor holds a 32-bit key at +8 and a 32-bit offset at +12
  (older dyld: 64-bit fields at +8 and +16), so compiled code cannot inline
  dyld's layout. Candidate: one pthread-key thread block read through
  `tpidrro_el0` after a startup self-check, as Go does on darwin/arm64.
- Failing on 92c9daf56's parent as well, to be fixed in this work:
  `test_virtual_thread_park_effect::test_cross_module_may_park_uses_generator_abi_and_resumes`
  (prints None, CPython 42); `test_exception_constructor_owned_result`,
  `test_generator_operand_root_groups`, `test_super_call_result_roots`
  (`PCC_WITH_THREADS=0` case), `test_virtual_thread_spawn_slot_ownership`,
  `test_virtual_thread_lowering_ownership`, `test_gc_update_referents`,
  `test_method_dyn_parameter_abi`, and `test_pcc_native_extension_loader`
  (its `PCC_SELF_LINK=cc` links lack `_pcc_precise_stackmap_table`).
- The C probes of the GC substrate tests still compile with the host `cc`
  (test harness only, `tests/python/_gc_substrate_common.py`); pcc's own C
  frontend could build them.

## P0 follow-up: lock-free GC4 provenance, one thread-state word, virtual-thread carrier races (2026-10-22, Claude)

Source: `shenming/master` 00f6b6238.

- GC4 object provenance without the graph lock (087be68b3, 3a0e8f0c4):
  `pcc_gc_pointer_is_managed` asks the GC1-4 object index and the
  managed-pointer set with an unlocked probe (`pcc_gc_index_py_seen`)
  before taking the lock, so a refcount operation on a zpage object no
  longer takes it. The tables of the four indexes read without the lock
  (object, managed-pointer, forwarding, forwarding target) are published
  with their capacity as one record and retired, not freed, when replaced;
  collections free them with the world stopped, and a one-thread process at
  once. This also fixes the GC4 load barrier, which already probed the
  object and forwarding indexes without the lock while a rehash freed the
  table under it: `tests/python/test_gc_index_unlocked_readers.py` (its
  find-only harness failed every run on the old index module).
- One thread-state word (be58921a7): the no-park depth, the object-graph
  lock depth, the scheduler-lock marker, registration, the parked mark and a
  deferred-tripwire flag share `pcc_tls_thread_state_py`, so a graph lock
  and unlock look up one thread-local each (were seven) and a safepoint
  poll one (were four).
- st_alloc (60000, interleaved runs, medians): GC4 6.65 s at 8362f0f34 ->
  5.76 s with the provenance change -> 5.03 s with the state word; GC0
  0.98 s -> 0.87 s.
- Virtual threads on several carriers (00f6b6238): a thread that
  parked or yielded could be woken and run by a second carrier before its
  first carrier had returned from the resume; a thread could be queued
  twice, and the stale pop cleared the running carrier's mark; and the
  step, after re-queueing the thread, read its state and completed it with
  None when another carrier was already running it. A thread is now marked
  as running from its dequeue until the step's last action
  (`_step_finished`); wakeups in that window only make it READY and the
  step queues it. The vthread lock program
  (`tests/python/test_virtual_thread_lock_mechanisms.py`) lost counts or
  results in about 8% of runs on GC1-GC4 and passes 500/500 now;
  `tests/python/test_vthread_carrier_migration.py` (twelve threads, four
  carriers, no shared data) failed about every other run before.
- Test fixes: the granule-growth probe passed an untyped address to `c_ptr`
  parameters, which pass an untyped int on as the int object (c1d2b70ff);
  the super-call result-root test still selected the removed
  `PCC_WITH_THREADS=0` runtime.
- Remaining GC4 lock costs in st_alloc: `pcc_gc_store_root` (24% of the
  lock's time), foreign leases (26%) and scheduler-root registration (14%).
  GC3's generational root scan runs between stops and relies on the graph
  lock and the registry revision, so scheduler-root registration cannot drop
  the graph lock on GC3/GC4 without redesigning that scan. On Darwin each
  thread-local lookup is still a `_tlv_get_addr` call with register saves.

## P0 follow-up: GC4 costs and a GC4 virtual-thread fault (2026-10-22, Claude)

Source: `shenming/master` 6b5a484e9.

- Stopped-world polls (27839a001): the owner of a stopped world polled the
  slow path on every loop of the runtime code it ran (31% of GC4 st_alloc).
  The poll flag is now cleared once every other mutator is parked or
  exited; `pcc_thread_world_stop_py` publishes the stopped world. Test:
  `tests/python/test_stop_the_world_poll_flag.py`.
- GC4 remembered set (7d8bc0f6c): a dying remembered owner walked the whole
  set twice; entries now chain per owner (head in the member index) while
  the index is exact. GC4 st_alloc 39.3 s -> 32.1 s (GC0 3.29 s). Test:
  `tests/python/test_gc_remembered_membership_lifecycle.py`.
- Operand method calls on receivers of unknown class (80699cbbb) load the
  method with `py_obj_load_method` into registered roots instead of
  binding it.
- GC4 virtual threads (6b5a484e9): the scheduler step kept the running
  thread as a raw pointer across its resume; when GC4 moved the thread the
  step completed it with None, so the gateway benchmark failed on GC4 at
  C100 from the second round. The step now leases the thread and its
  continuation. Test: `tests/python/test_vthread_scheduler_holds_running_thread.py`.
- Gateway C100 (20 rounds, one carrier) after these: GC0 9,936, GC1 2,450,
  GC2 2,461, GC3 2,227, GC4 1,646 requests/s; CPython asyncio 85,731.
- Remaining GC4 cost in st_alloc: the graph lock around
  `pcc_gc_pointer_is_managed` (zpage objects miss the granule fast path, so
  every refcount operation takes the lock) and object-index probes in the
  load barrier (`pcc_gc_backend4_slot_needs_resolve`).
- Found and not yet fixed: a parking call nested in an expression in a
  resumable function (`totals.append(batch(n))`) fails the self IR
  verifier with an SSA-dominance error; `test_gc_abstraction_surface::test_colored_relocating_gc_read_barrier_clears_candidate`
  fails on the parent commit too.

## P0 threaded runtime: threading locks and virtual-thread locks (2026-10-22, Claude)

Plan: `docs/plans/p0-threaded-concurrent-gc-runtime.md`. This change makes
the threading primitives follow CPython 3.15 on the threaded runtime and
blocks waits in the exited state:

- Lock/RLock `acquire(blocking, timeout)`, `locked()`, Event
  `wait(timeout)`, Semaphore and BoundedSemaphore `acquire(blocking,
  timeout)`/`release(n)` and Thread `join(timeout)` convert their arguments
  with CPython's rules (`_PyTime_FromSecondsObject`, ROUND_TIMEOUT) and raise
  its errors. The static lowering (`native_threading.py`) and the runtime
  bound methods (`py_obj_ops_dispatch.py`, through the native signature
  binder) call the same `py_threading_*_args` entries.
- The waits are exited kernel condition waits: no 5 ms polling, a notify
  wakes at once. Joins block on the handle's condition variable instead of
  spinning (`pcc_thread_join`), and `time.sleep` runs exited
  (`pcc_thread_blocking_enter/leave`), so a collection does not wait for a
  sleeping thread.
- An RLock belongs to a virtual thread, not its carrier
  (`PyVirtualThreadObject.identity`); `RLock.acquire()` in a resumable frame
  parks and the release hands the lock over. A release skips a parked waiter
  that can no longer resume.
- Runtime bound methods of locks no longer leak a receiver reference per
  call, and report release errors; they pin receiver and captures across
  their allocations, like `_py_sync_method_bound`.

Tests: `tests/python/test_native_threading_lock_semantics.py` (API program
and four-thread contention program, byte-equal to CPython),
`tests/python/test_virtual_thread_lock_mechanisms.py` (three carriers),
`tests/python/test_threading_condition_stw.py` (the oracle now models exited
kernel waits).

GC4 with several threads (fixed on top of 6cc663bd4). Two zpage races
handed one block to two live objects or dropped live objects from GC4's
accounting:
`pcc_gc_backend4_zpage_register_owner_payload_span` (and unregister,
retarget) moved a page's bump offset (page+64) and used bytes without the
object-graph lock, so a list/dict/set constructor and another thread's
zpage allocation claimed one offset and the span's tail rewind gave the
live block to a third allocation; and a zpage death's delayed freeing note
ran when `pcc_gc_pointer_is_managed(o)` still held after the deallocator,
which in a threaded program could be another thread's new object at the
freed address (noted freeing: never released, so its subgraph's finalizers
were lost, or its block reused while live). The span entry points now take
the re-entrant graph lock; `_py_decref_finish`, `py_instance_dealloc` and
`pcc_gc_tracing_finalize_unreachable` record whether the death noted the
object (`pcc_gc_free_watch_begin/end`) and probe the address only when it
did not. Four threads, `PCC_GC_BACKEND=4`: private `[i, i]` churn died
with [BAD_INCREF] in most runs on 6cc663bd4 and is 10 of 10 exact with the
span fix; with the span fix alone, 400-deep `__del__`/list chains were
wrong in 4 and 5 of 6 runs and `with lock:` in resumable functions in 6 of
6; with both fixes each is 8 of 8 exact, as is the lock contention program.
Tests:
`tests/python/test_gc4_threaded_payload_spans.py` (new: fails with
[BAD_INCREF] on 6cc663bd4), the GC4 cases of
`test_threaded_dealloc_cascades.py` and `test_native_threading_lock_semantics.py`
(no longer xfail).

GC collector thread (GC1/GC2) and the GC substrate suite. The collector
thread that d58306406 added for GC1 advanced any open cycle and ignored
`gc.disable()`; it now pauses while collection is disabled (explicit
`gc.collect()`/`pcc_gc_step` still run), and so do mutator assists.
`pcc_gc_set_backend` joined it only when leaving GC2 and counted it as a
mutator, so a one-thread program could not switch from GC1 to GC3/GC4;
leaving GC1 or GC2 now joins it before the single-mutator check (restarting
it if the switch is refused). Tests:
`tests/python/test_gc_background_collector_honors_disable.py`,
`tests/python/test_gc_collector_thread_backend_switch.py` (both fail on
82070e303). In `test_gc_threading_substrate.py` every node that passed at
195cba989 (before the P0 series) passes again: the two tracing-window probes
that hung now disable automatic collection, and the contracts that named
the threads-off kernel, `pcc_thread_safepoint`, the plain color store or
`pcc_cond_wait` follow the threaded-only runtime. Still failing there, as at
195cba989: the scheduler-queue and C root-store contracts (4), the backedge
safepoint shape, the colored relocation selector, generational referent
promotion, set add/remove/contains (4), strict cext decref, colored
remembered drain, collect-during-update/delete under all five collectors
(the update callback's collect sweeps nothing) and the backend-4 dict update
snapshot (the probe exits -11). Also failing: `test_gc_backend_generational`
(owner-slot walker contract, minor bump arena run) and
`test_gc_granule_map::test_granule_grow_preserves_all_sixteen_keys_per_slab`
(every slab registration refused); not yet compared with 195cba989.

GC0 automatic cycle collection. GC0 (the default backend) collected cycles
only on an explicit `gc.collect()`. Now `py_gc_track` schedules a collection
once tracking has grown by max(threshold0, survivors of the last
collection) and, as CPython 3.12+ does at its eval breaker, the next
application gate of compiled code runs it (`pcc_gc_safepoint`, through the
automatic-step flag the gates already poll); `gc.disable()` and
`gc.set_threshold(0)` keep it off. The default threshold is CPython 3.15's
(2000, 10, 10). GC0 marked reachable objects recursively, so a collection
with a 50,000-deep live list overflowed the C stack (also on 82070e303); it
now marks with an explicit worklist. With 2,000,000 self-referencing
`__del__` objects in a loop, GC0 now finalizes 1,999,995 before the explicit
collect and peaks at 5.7 MB (2.2 GB before; CPython 18 MB); it still takes
18 s against CPython's 0.3 s, almost all in finalizer dispatch. Tests:
`tests/python/test_gc0_automatic_cycle_collection.py`,
`tests/python/test_gc0_deep_structure_collect.py` (both fail on 82070e303).

Backend resets and the collector thread. Since bcdd222c2 a backend switch
owns the collector-thread lifecycle pause (joining a running thread), so it
is refused inside no-park scopes, graph-lock scopes and while owning the
stopped world. A same-backend reset of GC1 or GC2 no longer takes that
pause: the thread keeps running, the graph lock orders the reset against
its batches and the cycle epoch retires a stale finish claim, so a bounded
no-park scope may reset again (the tracing finish-claim probe's window 2,
which returned 19 after bcdd222c2, passes). Such a reset is refused when a
concurrent switch changed the backend first and when the collector thread
itself calls it. Tests: `tests/python/test_gc_cms_worker_lifecycle.py`
(same-backend reset, lost race, collector self-reset).

The kqueue/epoll kernel wait of the vthread IO waitset
(`pcc_io_waitset_wait_block`) now runs exited, as condition waits, joins and
`time.sleep` do. It can block forever, and a stop-the-world waited for the
carrier inside it: with the GC1/GC2 collector thread seeding a cycle, the
gc_production_contract vthread IO waitset probe deadlocked intermittently
(also on 82070e303). Test: `tests/python/test_vthread_io_wait_runs_exited.py`
(hangs on 82070e303).

GC1..GC4 automatic cycle reclamation. GC1..GC4 reclaimed cyclic garbage
only on an explicit `gc.collect()`: their automatic work only marked
(docs/investigations/gc-backend1-auto-step-sweep-debt.md, now corrected),
and GC1/GC2's collector thread began one marking cycle after another. The
reclamation now runs where compiled code reaches an application gate
(`pcc_gc_safepoint`), with the world stopped as for an explicit collect, one
cycle at a time: GC1/GC2's collector thread begins a cycle once debt reaches
the pacing threshold or tracking has grown by max(threshold0, survivors),
never while a completed mark waits for its sweep, and the next gate sweeps
each mark it completes; GC3 runs one cycle from tracking growth, as GC0
does; GC4's automatic step continues a mark to completion within its budget
and the gate sweeps it. `gc.disable()` and `gc.set_threshold(0)` stop all of
it. The pacing threshold applies the pause to the bytes the last mark found
reachable (`pcc_gc_tracing_marked_bytes`), not to the current heap with its
garbage (with the default pause of 1000, GC4 stopped stepping for good), and
a completed cycle pays the debt. GC4's own evacuation relocates only objects
a mark has reached (it copied churned garbage, which its forwarding holds
kept alive), and an explicit GC4 collect marks again after retiring
forwarding edges (it left 4,006 of 50,000 such objects unfinalized). With
2,000,000 self-referencing `__del__` objects, finalized before the explicit
collect, loop time and peak RSS: GC0 1,999,945, 19.5 s, 7.4 MB; GC1
1,944,220, 49 s, 105 MB; GC2 1,951,986, 47 s, 106 MB; GC3 1,999,945, 47 s,
9.2 MB; GC4 1,999,516, 61 s, 606 MB; CPython 0.29 s, 18 MB (before: GC1 and
GC2 over 300 s, GC3 2.9 GB, GC4 4.3 GB). Tests:
`tests/python/test_tracing_gc_automatic_cycle_collection.py` (fails on
2ec86d297), the pacing cases of `test_freestanding_gc_public_collection.py`
and `test_gc_backend_concurrent.py`, `test_gc4_idle_step_debt.py`; five GC4
probes that relocate through the automatic path now first mark their objects,
the state a completed mark leaves. Stale contracts retargeted on the way:
`test_freestanding_gc_barrier_dispatcher.py`'s closure pin (missed
095b286f0's gate-rule externs), the decref death pin in
`test_gc_backend4_production.py` (809d6bf67's atomic or), and
`test_gc_byte_accounting_i64.py`, whose probe returned before any check on
every threaded runtime.

Open:

- Automatic cycle reclamation, what remains: finalizer dispatch dominates
  the time on every backend (about 8 us per `__del__` through
  `py_user_del_dispatch` -> `py_func_call_kwargs`); GC4 peaks at 606 MB on
  the 2,000,000-object loop although it finalizes 1,999,516 automatically
  (not investigated: reclaimed zpage memory is not reused); a GC4 object
  relocated and then dropped keeps its forwarding hold until the edge
  retires, which automatic steps do only when idle; the default pause of
  1000 (the next cycle after nine times the marked heap) only now takes
  effect, unmeasured against other values. The tracing collectors take more
  than a minute (GC1, GC2, GC4) and 30 s (GC3) on a program that builds a
  50,000-deep linked list and a 50,000-deep nested list
  (`tests/python/test_gc0_deep_structure_collect.py`'s program), as on
  82070e303; not remeasured.
- Classes created at run time are never collected: the untracked
  `tests/python/test_native_class_collection.py` (the five-GC run
  c91b89809 left open) creates and drops 300 local classes; CPython collects
  300, pcc 0 on every collector. Cause: `py_class_new` marks every class
  `PY_FLAG_IMMORTAL`, the 2026-07-03 fix of
  docs/investigations/pcc1-tuple-unpack-self-host-str-counter-corruption.md
  (instances hold an uncounted class pointer at offset 16, and a frontend
  over-release had freed pcc1's codegen class). Planned fix, CPython's
  ownership: classes mortal and GC-tracked; `py_instance_new` takes a counted
  reference to the class (valueboxes too, routed to `py_instance_dealloc`),
  released last in `py_instance_dealloc`; a class owns its bases, released
  in `py_class_dealloc`; the MRO array becomes role-3 metadata (GC0 and the
  tracing trial deletion subtract role-2 edges, so every role-2 edge must be
  counted); the force-free loops of `py_gc_collect` and the tracing sweep's
  PASS 2 skip classes, which die by refcount once their holders do; the
  runtime's cached builtin type classes (`pcc_type_cls_*`, most of about 44
  `py_class_new` sites) become explicitly immortal; then user type tags
  (104..65535, never reused, so a process can create at most about 65,000
  classes) are recycled. Exceptions already own their class. Risk: class
  reference imbalances that immortality hid (the July over-release) become
  use-after-free; it needs the pcc1 self-host gate.
- Failing on 82070e303 as well, not addressed:
  `test_freestanding_gc_tracking::test_production_archive_uniquely_owns_tracking_gc0_to_gc4`
  (with every runtime threaded, GC4 skips `py_gc_track`, so its harness
  sees nothing tracked; check `gc.is_tracked` under GC4) and
  `::test_production_archive_tracking_lock_survives_real_pthread_contention`
  (times out after 30 s); `test_gc_effectiveness::test_generator_referencing_self_collected`
  expects `gc.collect() > 0` where CPython 3.15 prints `False` too;
  `test_py_io_waitset::test_live_waitset_retries_eintr_against_absolute_deadline`
  (probe exits 16) and `tests/vthread/test_io_waitset_mirror.py::test_c_abi_io_waitset_semantics_and_capability`
  ("kq-timeout").
- Failing on 2ec86d297 as well, not addressed:
  `test_gc_backend_relocating::test_colored_relocating_selects_unpinned_relocation_set`
  (a second explicit selection takes two objects created by the prints in
  between), `test_gc_backend4_production::test_backend4_thread_start_loads_forwarded_args_slot`
  (exits 12: the started thread's args slot is not the moved tuple) and
  `gc_production_contract/test_valueclass_pointer_payload.py[4]` (the box is
  not in the explicitly selected relocation set). Also failing:
  `gc_production_contract/test_extension_module_state_roots.py` (self link
  refuses native-extension export anchors),
  `test_gc_backend_concurrent::test_concurrent_backend_nonthread_barrier_stores_drain_to_black`
  (needs a threads-off runtime, which no longer exists) and
  `test_gc_byte_accounting_i64::test_native_gc12_byte_counters_cross_32_bit_boundaries`:
  with its guard fixed, the debt and threshold checks pass, but a fresh
  `py_list_new(0)` on GC1/GC2 adds 40 bytes to `pcc_gc_live_bytes` while
  `pcc_gc_object_known_size` reports 0 and its release subtracts nothing.
- `with lock:` in a resumable function enters through `py_context_enter`,
  which blocks its carrier instead of parking; a parking call nested in a
  larger expression (`items.append(lock.acquire())`) fails the self IR
  verifier with an SSA-dominance error (the `vt.join() + 1` class).
- `Condition(lock)` still ignores its lock and the default Condition lock is
  not reentrant; Condition ownership errors, `notify(n)`, `wait_for`,
  `Barrier`, `Timer`, `threading.local` attributes and a `current_thread()`
  object remain to do. `time.sleep` in a virtual thread blocks its carrier.
- Object layouts (for example `PyVirtualThreadObject`) are still defined by
  `pcc/runtime/src/py_internal.h`, and `scripts/gen_port_abi_constants.py`
  reads them with the host `cc`; the layout authority should move to a
  pcc-owned spec.

## IDE Python source-debug objects (in progress)

- Starting source: `fac3c11af` from `shenming/master`. The owned object route
  rejected a debug-bearing return as SSA text (`%sum, !dbg !4`), while the
  same ordinary function emitted a Mach-O object. Existing Python `-g`
  tests used clang for their final DWARF object oracle.
- `self_backend_debug.py` now preserves original function/file/line/column
  metadata separately from semantic operands, with lexical scopes, cached
  identities, dense reachable-block mapping and multi-line switch locations.
  Original filename/directory now come from the selected parser input or
  original spans, rather than an import module name without its `.py` suffix.
- Own checks: 28 focused host-PCC/frontend/parser/owned-object regressions pass.
  This is **not native PCC1 debugging acceptance**. The installed compiler
  still points at a v84 baseline; no current-source bootstrap, native debugger
  session or full GC gate is claimed. The owned lifter currently has line-only
  spans, so precise token columns remain open.
- Indexed transport now retains the original metadata graph/IDs and validates
  each function/block/instruction binding. Debug modules use schema v2 so v1
  readers reject them; ordinary v1 payloads stay unchanged. 33 focused host
  checks pass, including byte-identical debug round trips and corrupted binding
  refusals. Receipt: `/Users/jiamo/pcc-recovery-archive/2026-10-21/ide-debug-codec-69bv8ogd/README.md`.
- AArch64 Darwin now emits owned DWARF4 function/line sections bound to final
  assembler offsets. Structured/text and indexed-cache routes retain identical
  code/metadata; packed addresses reuse local section relocations. Debug modules
  disable code-moving target optimizations; other native targets still refuse.
  58 focused checks pass. LLDB resolves a real Python frontend object source
  breakpoint and maps its address back to the original file/line. This is an
  object inspection, not a running native breakpoint. Source/artifact/consumer
  evidence: `/Users/jiamo/pcc-recovery-archive/2026-10-22/ide-debug-dwarf-2pgwiavx/README.md`.
- Ordinary and compact/staged merges now rebase original DWARF4 section offsets
  and retain local code-address relocations. Two/three-source and repeated
  partial links resolve the correct files/addresses in dwarfdump and LLDB.
  62 focused checks pass, including prior descriptor-retirement and ordinary
  relocation regressions. Receipt: `/Users/jiamo/pcc-recovery-archive/2026-10-22/ide-debug-link-sg4yqjss/README.md`.
- Owned executable output now keeps a read-only DWARF segment with resolved
  code addresses, valid BSS/file layout and owned signing. Debug leaf functions
  retain FP/LR frames. 64 focused checks pass: native scalar IR fixtures hit
  source breakpoints, step over and show caller frames, then exit with the
  expected result. Ordinary/windowed/staged output bytes match. This backend
  fixture is not a real Python-frontend/native-PCC1 integration; LLDB also warns
  that Python variable inspection lacks a language plugin. Receipt:
  `/Users/jiamo/pcc-recovery-archive/2026-10-22/ide-debug-executable-qbzg7kp6/README.md`.
- The first real Python integration exposed application `-g` leaking into the
  shared runtime's owned build, where runtime-only optimization corrupted PHI
  operands with `!dbg`. The owned builder now matches run_runtime_make's scoped
  option isolation and restores application debug settings on success/failure.
  104 checks pass, 1 deselected; a real runtime member emits without debug and
  the application still emits DWARF. Full integration replay remains pending.
  Receipt: `/Users/jiamo/pcc-recovery-archive/2026-10-22/ide-debug-runtime-policy-7_1g4v95/README.md`.
- The next real Python replay passed runtime construction and exposed the
  in-process emitter leaving an unknown IR target after selecting its host
  backend. It now reuses the existing selected-target normalization before
  backend admission. Explicit targets stay unchanged; absent declarations
  still refuse. 70 focused checks pass. Receipt:
  `/Users/jiamo/pcc-recovery-archive/2026-10-22/ide-debug-target-z4of_fdx/README.md`.
- Real Python integration now passes at compiler source `d6adf16e5`: the
  unchanged add/print program compiles through host PCC's owned backend/linker
  with libpython off, and GC0-GC4 all match CPython (`7`, exit 0). LLDB launches
  the actual native artifact, hits source line 2, shows its caller, steps to
  line 3 and exits normally. Only libSystem is linked; source hashes stayed
  unchanged. The guarded run took 33.387s, peak tree RSS 1,131,511,808 bytes,
  and the checked-in integration regression passes. No owned processes remain.
  Receipt/artifact: `/Users/jiamo/pcc-recovery-archive/2026-10-22/ide-real-python-debug-zor6_5sq/README.md`.
  This uses a host PCC compiler, not a rebuilt native PCC1. Module-entry source
  coordinates, locals/types/watch/evaluation, exceptions and full debugger
  protocol/IDE integration remain open; LLDB lacks a Python language plugin.
- Native-build preparation now registers all seven debug record classes in
  the compiler inventory and binds the indexed CompilerInt4 block fact before
  lane access. 49 focused inventory/source checks pass. Peer `8fbdfda18` was
  integrated before publication; its committed checks were not repeated.
  Receipt: `/Users/jiamo/pcc-recovery-archive/2026-10-22/ide-debug-native-records-1kwco11c/README.md`.
- Next: build a current-source native pcc1 and repeat the unchanged acceptance;
  add complete unwind coverage and module-init/local/type/value locations, then validate
  pcc1 break/step/frame/locals/watch/exception behavior. Host CPython debugging
  and translated IntelliJ/PCC-GUI integration remain independent requirements.
- Source snapshots, baseline objects/IR, patch and exact gate output:
  `/Users/jiamo/pcc-recovery-archive/2026-10-21/ide-debug-source-liq3q2lh/README.md`.
  No owned matching test process remains. This is the `pcc-ide.pcc1-python-debug`
  task in the TAG execution queue; the complete IDE/product goal stays open.

## 2026-10-17 Mac local continuation

### 2026-10-21 Claude session

- #82 landed in `ac063182a` (stale pins retargeted in `89e36b24d`):
  zero-argument `super()` and `__class__` read a
  `__class__` cell, one per execution of a class statement, filled after the
  body and before decorators; module classes keep `.class.cell.<mod>.<Class>`.
  An empty cell raises CPython's NameError (`super().x`, `__class__`) or
  RuntimeError (bare `super()`). Local-class methods are closures: enclosing
  bindings and `__class__` are trailing capture parameters carried by the
  function objects the class statement publishes (the hidden
  instance-attribute approximation is gone); capture sources are read in the
  enclosing function's scope, never the class namespace. Bases that are run-time
  values get a run-time composed layout. `super()` takes its receiver from the
  first positional parameter; staticmethods and explicit `super(T, expr)` go
  through the runtime `py_super_getattr` (CPython supercheck and descriptor
  binding); names no compile-time class defines are found in the MRO when the
  call runs; `super().name` works as a value; `super().count()` is no longer
  treated as `str.count`.
- Tests (native, five collectors, compared with CPython 3.15):
  `tests/python/test_local_class_method_closures.py`,
  `tests/python/test_super_runtime_resolution.py`,
  `tests/python/test_function_local_class_execution.py`. Validation: the
  77-file focused regression plus the new files (default selection and the
  pcc0 integration subset). Not run: pcc1-parametrized cases (needs a pcc1
  rebuilt from this source), bootstrap, full five-GC gate.
- Failures already on master, fixed: `test_live_property_lookup.py`'s stub
  host lacked `_property_accessor_takes_protocol_args` (added by `d26f61b06`;
  18 cases, separate commit); `test_local_class_receivers.py` required an
  unbound-local guard that `439d8274e` correctly omits for definitely bound
  class names (`class_values`); `_l1_codegen_static_methods.py` was stale for
  two signatures (`test_l1_codegen_static_method_table_matches_host_contract`)
  and is regenerated with #82.
- Open, found while verifying #82 (native runs, CPython 3.15 compared):
  - Fixed in `9cad5966e` (#82 stage 3a): `object`'s namespace (CPython 3.15
    `vars(object)`) is read where CPython's lookup reaches `object` (the
    super walk at the root, instance getattr before `__getattr__`, the class
    getattr miss), not from the root's attrs dict; a 23-name table filters
    those lookups (`__getattr__` dispatch measured unchanged). `__reduce__`,
    `__reduce_ex__`, `__dir__`, and `__getstate__` for a `__slots__`
    instance raise NotImplementedError. Test:
    `tests/python/test_object_namespace.py`.
  - `super()` through a builtin base: a name the base's CPython namespace
    defines raises NotImplementedError naming the base
    (`BaseException.__str__`, `dict.__setitem__`, `str.upper`); a dict
    subclass reaches get/keys/values/items/pop/setdefault/clear. Next:
    native BaseException, KeyError and dict namespaces. `class X(list)`
    silently drops the `list` base (instances are not lists; `len()` raises
    a bare TypeError); the explicit "no implemented initializer" error stays
    for its `super().__init__`.
  - Fixed in `0fc9543ba`: a known-class receiver whose MRO does not declare a
    method called the one other class declaring that name
    (`Q().__setattr__("v", 5)` ran `P.__setattr__`). Test:
    `tests/python/test_known_class_method_lookup.py`.
  - Fixed in `2420ba524`: the host CPython 3.15.0 final (installed 2026-10-20,
    replacing rc1) moved cProfile into `profiling.tracing` (PEP 799); the
    port follows, and `test_py_stdlib_devtool_closure.py` passes again,
    including its native integration case.
  - Fixed in `a9305caed`: `db1830cf8` writes every class's `__doc__` at
    creation, so exposing the namespace no longer bumped the field-cache
    epoch (two `test_class_lookup_cache_runtime.py` cases failing on
    master).
  - Fixed in `cb40b6988`: two stale test pins (a strict xfail that now
    passes in `test_hasattr_getattr_probe.py`; the `class.captures.instance`
    label `ac063182a` retired in `test_constructor_unpack_binding.py`).
  - Fixed in `8e8aed2f0`: exception classes constructed by name or in
    `raise Cls(...)` take the runtime class call, so `args` survive an
    `__init__` without `super().__init__`, an indirect exception base, and
    `raise Cls(..., key=v)` (which skipped `__init__`); keywords with no
    `__init__` raise CPython's TypeError. Test:
    `tests/python/test_exception_constructor_args.py`.
  - Fixed in `12951e8d3`: `sorted(s.copy())` on a set failed to compile (the
    copy was an unrooted `py_set_new` result filled by `py_set_update`);
    a set-typed `copy()` now takes the slot-owned set algebra call as
    `union()` with no arguments. `test_native_set_methods.py` failed on
    master.
  - Validation for `9cad5966e`..`12951e8d3` (default selection, native, five
    collectors where the tests run them): the 77-file #82 regression, 52
    stage-3a files, 75 dispatch files, 55 exception/constructor files and
    the new tests, 7963 passed, 2 xfailed, deselecting only the master
    failures listed below (each re-run on `9dcf40aec`); the devtool native
    integration case passed. Not run: the rest of the integration subset,
    pcc1-parametrized cases, bootstrap, the full five-GC gate.
  - Open: dict-subclass `pop(k)` and `d[k]` raise
    `KeyError('pop(): key not found')` and `KeyError('key not found')`
    instead of `KeyError(k)`, and `clear()` replaces the backing dict.
  - Measured (1M iterations, five collectors): `__getattr__` dispatch costs
    0.88-4.5 us per access and a `getattr(o, name, default)` miss 0.87-3.7
    us, against CPython's 0.05 and 0.03 us.
  - `super(T, obj)` outside a class body falls back to the CPython builtin
    (strict stub).
  - Fixed in `ae6ecaae4`: `x = v ** 2` with a dynamic `v` returned 0 for float,
    complex and user operands, and `v < 1.0` read a dynamic `v` as a float;
    `56f0fb594` fixed the inlined `map`/`filter` lambda (`test_native_map_filter_lambda`).
  - Still open, work saved in
    `/Users/jiamo/pcc-recovery-archive/2026-10-21/power-typing-wip/`:
    inference types `int ** -1` as int (a function computing `(2 ** -1) + 1`
    prints nothing), `1.5 if c else 2` yields `2.0`, and a type-changing
    augmented assignment (`value = 3; value /= 2`) fails to compile.
    Visible float-capable powers with a dynamic peer still fail closed.
  - pcc's owned `pow` is up to 2 ulp from CPython's libm result
    (`4 ** 1.5` gives `7.999999999999998`, `0.5 ** 2.5` gives
    `0.17677669529663687`); a comparison returns a truth value even when a
    user `__lt__` returns another object (CPython returns that object).
  - #82 stage 3: `__set_name__`/`__init_subclass__` in CPython order,
    metaclass `__classcell__`, per-execution cells for class statements in
    module-level loops. Stage 3a drafts (the `object` namespace and builtin
    base tables) are in the archive directory above.
  - Failing on master, unrelated to these commits (seen in their gates):
    `test_py_lambda_cpython_calls.py::PyLambdaCpythonCallTests::test_lambda_returning_cpython_object_stays_tagged`
    (`libpython_mode="auto"` under the owned runtime: "owned platform
    runtime does not include libpython"),
    `test_owned_descriptor_module_bindings.py` (6 of 9 when run alone),
    `test_exception_constructor_owned_result.py` (6 of 9: the builtin
    exception producer is pinned and passed directly instead of published
    into a root), and, stopping at their first failure,
    `test_exception_raise_lifetime.py`,
    `test_native_exception_context_chaining.py`,
    `test_native_subscript_raise.py`,
    `test_valueclass_constructor_argument_abi.py`,
    `test_virtual_thread_frontend.py::test_virtual_thread_join_parks_and_propagates_outcomes`
    (the same "slot-call Call lacks an immediate owned-result handoff"
    compile error, from a call operand in a generator's return), 12 of 26
    `test_virtual_thread_lowering_ownership.py` IR-model cases (an
    unmodeled `pcc_slot_call_status_report` call on a protected cleanup
    path; an allocated owner not cleared on its NULL error path),
    `test_virtual_thread_sequential_readable_writable_use_platform_reactor`
    (the native program times out), 29 of 36
    `test_virtual_thread_spawn_slot_ownership.py` cases (the same NULL
    error-path owner contract), two `test_virtual_thread_park_effect.py`
    cross-module cases (a parked result prints None),
    the two `attribute` cases of
    `test_native_bytearray_receiver_ownership.py::test_bytearray_receiver_does_not_retain_old_buffers`
    (GC0 retains 1,065,005 bytes over 64 `buffer.data.extend(b'')` calls),
    seven `test_py_stdlib_*_closure.py` integration tests,
    `test_typed_property_replacement_native_unresolved[pcc0]`,
    `test_host_emitted_global_barrier_and_nonlocal_cells`; the pcc1
    compile-only cases need a current pcc1 binary.
- Continuation (same session), each native on five collectors and compared
  with CPython 3.15:
  - `1bf8df737`: NotImplemented has its own type tag (34) and type object
    (`NotImplementedType`); `bool(NotImplemented)` raises as in 3.14+.
  - `7b9e026d6`: `super()` and lookups reach native BaseException, KeyError
    and dict namespaces (the builtin-base item above).
  - `87076e422`: `Cls.method = f`, a newly assigned dunder and `del Cls.m`
    reach every lookup; the class method table (a borrowed alias of the
    attrs dict) kept the compiled original (SIGSEGV on GC0-3). Test:
    `tests/python/test_class_method_rebinding.py`.
  - `76ea24e9c`: a class body whose metaclass defines `__prepare__` and
    `__new__` read globals and builtins through a silent-NULL getitem, so a
    dataclass `field(default_factory=list)` died with "no active exception
    to reraise" (`test_dataclass_inherited_default_identity[prepared_overrides]`).
  - `7dc4627f3`: any()/all() run CPython's iterator protocol (generators,
    sets, strings and user iterables were answered from len/getitem);
    iter() supports the `__getitem__` sequence protocol; hasattr() (crash
    on a non-str name, swallowed every exception), format(), chr(), abs(),
    divmod() and the zero-division text match CPython. Test:
    `tests/python/test_builtin_call_semantics.py`.
  - `d058fe6d8`: GC4's explicit `gc.collect()` never drained the store
    buffer, whose entries own their recorded value, so a value overwritten
    after the last background step never finalized. Test:
    `tests/python/test_gc4_explicit_collect_store_buffer.py`.
  - `08f59c286`: three GC test harnesses failing on master (a stale import
    closure, a CMS-worker harness on the pre-seed mark protocol, a lease test
    linking through `link_args` and running its threaded case unthreaded).
- Open, found by the same differential runs (next, in this order): builtin
  functions as first-class values (`f = len` raises NameError, `builtins.len`
  needs libpython; `repr(abs)` and `type(f)` of every function report
  `object`, so `types.FunctionType` accepts any object); lazy
  `zip`/`map`/`filter`/`enumerate`/`reversed` objects (`zip`/`enumerate`
  build lists; a bare `map(...)` is a NameError; `zip([1], 5)` returns
  `[]`); a statically inherited metaclass whose `__new__` returns another
  type (`class B(A)` calls `A`'s metaclass); `type(cls)` for a class built
  by `super().__new__` in a metaclass; `__module__` from a prepared
  `__name__`; `__prepare__` must return a mapping.
- Next (my ordering, open to redirection): the builtin-base namespaces
  above, #82 stage 3, then the gateway regression (handoff below) and the
  10-20 queue.
- Maintainer decisions (2026-10-21): GC0 class collection (item 10) was left
  until it came up; runtime classes are collected since 2026-10-22. The
  quota-checkpoint rule was removed from AGENTS.md (`8e4cd818e`), and the
  bisect-wt worktree was removed.

### CPython Lib/test conformance and open work (2026-10-21, Claude)

- Harness (task 6): `scripts/cpython_conformance/run.py` (`fdeab9357`,
  `10ab6f7d0`). Every Lib/test module (top-level, packages and their
  `test_*` modules: 857 in v3.15.0) runs under CPython with a minimal
  `test.support` and natively from a replay of the CPython case plan;
  rate = cases succeeding natively / cases succeeding under CPython. Run:
  `uv run python scripts/cpython_conformance/run.py --cpython-src
  /Users/jiamo/pcc-conformance/cpython-v3.15.0 --out <dir outside the repo>
  --runtime-archive <archive> --jobs 3 --resume`; `--modules m1 m2 ...`
  reruns a subset; `summary.md` there ranks compile and case failures by
  cause. Needs a CPython v3.15.0 source tree (the path above on this
  machine) and a runtime archive built from the same tree (`uv run python
  -c "import sys; sys.path.insert(0,'.'); from tests.runtime_build_cache
  import cached_pcc_python_runtime;
  print(cached_pcc_python_runtime()/'libpy_runtime_pcc_py.a')"`); a stale
  one fails every compile with "codegen checksum differs".
- First full native run, source `10ab6f7d0` (identity `4982cd2d7ae59935`,
  93 minutes at `--jobs 3`), receipts in
  `/Users/jiamo/pcc-conformance/runs/2026-10-21-10ab6f7d0/`: **0.56%** --
  166 of the 29,398 cases that succeed under CPython (34,270 collected in
  857 modules) also succeed natively. 371 modules have no baseline (skipped
  by policy: sockets, subprocesses, fork, CPython test extensions,
  resources, GIL-only, doctest-only); 403 of the other 486 do not compile,
  37 stop early.
- The 190 modules whose first-run compile stopped on a method decorator
  (13,556 CPython-passing cases): none compiled at `10ab6f7d0`; 48 compile
  and 67 cases pass at `b3acc095b`; 71 compile and 77 pass at `281ff86f9`
  (identity `41db5aaeb9d4f8be`, receipts
  `/Users/jiamo/pcc-conformance/runs/2026-10-21-281ff86f9-decorators/`).
  Only 9 of the 71 run to completion: 28 stop at import on "No module
  named" (8 more raise SkipTest for one), 9 on a missing module attribute.
  First blockers of the 119 that do not compile (modules, the
  CPython-passing cases they hold -- an upper bound, a module can stop at
  the next blocker): Thread target kwargs 18 (1,678; fixed below),
  multi-file libpython fallback 28 (1,460), slot-call operand without an
  owned result 10 (1,244), StrType to float in test_array 1 (1,043),
  owned-result handoff 6 (990), native class base unavailable 6 (965),
  `__neg__` on a class instance 1 (365), `super().__init__` through a
  foreign base 4 (362), compiled module attribute mutation 4 (339),
  `super().__new__` through a foreign base 5 (228).
- Pushed today: builtin call shapes through the wrappers (`02893728f`),
  arithmetic protocols and CPython 3.15 TypeError texts at object
  boundaries (`11b3c8c3b`), `round()` (`1a8237cb5`), `sum()` (`191639575`),
  complex negation (`9164f6bec`), the `f(*instance)` crash (`8f616f95c`),
  method decorators at class creation (`b3acc095b`); class decorator
  expressions at class creation, with pcc unittest class-level skip marks
  and a static `unittest.main()` that honours them (`8d92407b0`);
  class-body expression statements (`2d0eb4e53`); a nested def's imported
  and builtin decorators applied where it runs (`5b235400d`); the strict
  stub for a function whose CPython value reaches a slot-call operand
  (`97bf453b8`); `raise C` for an exception class held in a variable
  (`c6b7b1cd1`); `import glob` (`d12559992`); two stale infrastructure
  test pins (`de3d4f26d`); a stubbed function's result stays native under
  strict callers (`3defe7403`); natively lowered int results record their
  owner (`c53f3473f`).
- This batch (each commit names its test; native on five collectors against
  CPython output): pwd lookups no longer crash natively (`c9f8d4cdc`);
  threading.Thread kwargs reach the target (`991948c29`); class-body
  compound statements run when the class is created (`7b29a753a`); the class
  module-init function is stubbed under strict no-libpython (`0d196c9d5`); a
  missing module raises ModuleNotFoundError (`683314832`); unpacked method
  arguments on a dynamic receiver (`927646af5`). The class, decorator,
  hoist, threading, str-method, import and strict-stub test files are being
  rerun against them (no new failure so far; the ones found are listed under
  failing on master). Not run: pcc1-parametrized cases, bootstrap, the
  five-GC gate; the harness rerun of these modules is next.
- In progress (Claude, do not duplicate): slot-call operands without an
  owned result and the owned-result handoff, producer by producer. The
  first one each of the 17 modules stops at (traced in the compile
  workers): `slice(...)` as an operand (test_deque, test_list, test_tuple,
  test_userlist, test_userstring), `dict(x)` of a non-dict source
  (test_collections, test_ordered_dict), `str(bytes, encoding)` as a
  receiver (test_codecs, test_zlib), `threading.current_thread()`
  (test_threading) -- these four are fixed in the next batch; still open:
  `pickle.loads(...)` (test_str), a method call on a closure-cell receiver
  in a lambda (test_mailbox), `__class__` in a class attribute initializer
  (test_super), a BinOp keyword value in a decorator call (test_tcl),
  `s.rstrip().splitlines()` as a binary operand (test_tokenize),
  `".".join(map(str, ...))` (test_ipaddress). Also: the recursive stdlib
  probe (`_stdlib_module_compiles`) codegens a
  dependency without the compile's strict no-libpython setting and
  swallows the reason, so a module that compiles strictly is excluded --
  CPython's `genericpath.py` fails the probe (its `map` becomes a CPython
  builtin call) yet compiles as a strict sibling module, and
  test_genericpath, test_ntpath and test_posixpath stop at `import
  genericpath`.
- Open, independent (free to take; native, found by the harness, each with
  a one-line repro):
  - native providers missing at import, by the CPython-passing cases of the
    modules they stop: `array` (4 modules, 348 cases), `doctest` (5, 232),
    `_sqlite3` (3, 100), `tokenize` (81), `pyexpat` (79), `urllib.request`
    (77), `plistlib` (71), `fractions` (2, 67), `_interpreters` (65),
    `timeit` (42), `string` (41), `_multibytecodec`, `dbm.dumb`, `_pyrepl`,
    `_opcode`, `_dbm`, `_ctypes`, `resource`, `atexit`, `select`, `fcntl`,
    `_locale`, `_imp`;
  - module attributes missing natively: `ctypes.create_string_buffer`,
    `ctypes.LittleEndianUnion`, `os.stat`, `os.fsdecode`,
    `contextlib.closing`, `platform.platform`, `math.nextafter`,
    `urllib.parse.urldefrag`, `zoneinfo._tzpath`,
    `collections.abc.KeysView`; `sys.getfilesystemencoding()` has no
    native lowering;
  - str and bytes methods are not attributes natively: a str exposes only
    `count`/`startswith`/`endswith` (`py_str_getattr`, `_str_type_fill`),
    a bytes none, so `getattr(s, "split")`, `f = sep.join`, `s.join(*xs)`,
    `m.format(x)` on a dynamic str raise AttributeError and
    `hasattr("", "join")` is False; `str.join(sep, xs)` through the type
    needs libpython;
  - `threading.current_thread()` returns the thread id (an int), not a
    Thread object (`py_threading_current_thread`), so `.name`, `.ident` and
    identity differ from CPython;
  - `map`/`filter` exist only as compile-time shapes inside `list()` /
    `tuple()` with a lambda, a user function, `str` or `chr`;
    `list(map(len, xs))`, `tuple(map(os.fspath, m))`, `sorted(map(abs,
    xs))`, `next(iter(map(...)))` raise "NameError: name 'map' is not
    defined"; builtin functions are not values (`f = len`); `zip` and
    `enumerate` build lists;
  - native class bases: `collections.abc.MutableMapping` (shelve),
    `io.RawIOBase` (test_io), `unittest.TestCase` in test_random and
    test_selectors ("native class base is unavailable");
    `super().__init__`/`super().__new__` through a stdlib or builtin
    exception base (test_bdb, test_exception_group, test_pathlib);
    `owned __new__ lookup does not implement this builtin allocator` at
    test_copy import;
  - `compiled module attribute mutation requires proven managed provider
    storage` (test_bz2, test_lzma: `compression._common._streams.BUFFER_SIZE`)
    and `... assignment requires a value export` (test_fileinput
    `gzip.open`, test_locale, test_threading_local);
  - test_array: "Layer N cannot coerce StrType to float"; test_statistics:
    `__neg__` for unary minus on a class instance; test_warnings: class
    keyword arguments (metaclass/kw); test_descr: non-literal `__slots__`;
    test_re/test_iter: tuple unpacking of a non-tuple RHS;
  - deep recursion in the runtime's protocols (repr of a deeply nested
    container) overflows the native stack (SIGSEGV, which also loses every
    later case of the module run) where CPython 3.15 raises RecursionError
    from its C-stack bound (`_Py_ReachedRecursionLimit`); the 1,000-level
    Python counter (`py_recursion_enter`) is the wrong limit. Repro:
    test_dictviews `test_deeply_nested_repr`;
  - exception chaining on user exception classes: implicit `__context__` is
    recorded only for builtin `PY_TYPE_EXC` objects
    (`py_exc_set_implicit_context_slots`), and `exc.__context__` on an
    instance of a user `Exception` subclass raises AttributeError;
    `ImportError.name`/`.path` are not set;
  - a zero-parameter function in a class body (hypothesis strategies in
    test_base64): class_gen requires a `self` receiver; a class, walrus,
    `global` or `nonlocal` inside a compound class-body statement raises
    NotImplementedError ("class-body statement ...");
  - method decorator stacks mixing a class_gen decorator with a general one
    (`@classmethod @contextlib.contextmanager`, `@DynamicClassAttribute
    @abc.abstractmethod`) are still refused;
  - `contextlib.contextmanager` is a compiler-owned static `with` lowering;
    pcc's `contextlib.contextmanager` raises NotImplementedError, so a
    decorated function used outside `with`, or under another decorator,
    fails; `functools.wraps` compiles as a no-op on functions (no
    `__name__`/`__wrapped__`); `functools.cache` on a method compiles
    uncached; `abc` is a compile-time-only import, so `abc.abstractproperty`
    or any `abc` attribute without compile-time handling is a NameError;
  - `dir(cls)` raises RuntimeError "native default dir requires complete
    class and builtin namespaces" (49 failing cases in the decorator
    rerun); reflective unittest discovery (`loadTestsFromTestCase` and
    result-driven runs) is not runtime-owned (23);
  - test_bool natively (17/31): `bool(x=1)` does not raise TypeError,
    `(True).real` AttributeError, `bool` is subclassable, `0j != 0j`, an
    isinstance case, bool identity after some operations, no `eval`, no
    `marshal`;
  - failing on master `281ff86f9` (checked on the unchanged tree):
    `test_py_stdlib_unittest_closure.py::test_unittest_and_mock_match_cpython_strict_self_no_libpython`
    (after `mock.patch.object` exits, `Target().value` raises
    AttributeError: the original attribute is not restored);
    `test_py_function_decorators.py::test_imported_bare_decorator_is_metadata_noop`
    (`singledispatch` refused) and `::test_same_module_bare_decorator_still_applies`
    ("slot-call Call lacks an immediate owned-result handoff");
    `test_callback_sync_context.py::test_all_direct_method_adapter_roles`
    (`expose_unbound`, which returns `Worker.parked`, no longer sets
    `PY_FLAG_FUNC_AUTO_PARK` on the published function; `expose` and
    `expose_class` do);
    `test_native_sysconfig.py::test_sysconfig_get_config_var_dispatches_to_compiled_stdlib_provider`
    (the IR reaches `get_config_var` through the generic import method
    call, not `@user_sysconfig_get_config_var`);
    `test_class_lookup_error_cleanup_native.py::test_native_class_lookup_cleanup_restores_error`
    (every case: "pcc self-link mode does not support link arguments:
    .../probe.o");
    `test_gc_threading_substrate.py::test_scheduler_queue_root_transfer_plans_finish_after_outer_graph_unlock`
    (expects one `stack_alloc(128)` in `pcc_gc_store_root`),
    `::test_c_root_store_debug_off_invalid_new_preserves_benign_update` and
    `::test_c_root_store_debug_on_invalid_value_traps_after_expected_boundary`
    (the C probe exits -11);
    `test_hoist_global_cell_boundaries.py::test_host_emitted_global_barrier_and_nonlocal_cells[on]`
    needs `PCC_RUNTIME_ARCHIVE` set to an archive of its own build
    configuration. `test_callback_sync_context.py`'s
    `test_host_frontend_native_callback_context` and
    `test_native_compiler_callback_context` need
    `PCC_CALLBACK_RUNTIME_ARCHIVE` naming a threaded archive (one with
    `freestanding_thread_kernel_pthread.o`).

### 2026-10-21 handoff (session stopped by the maintainer)

- Full handoff with profiles, patches, repros and worktree list:
  `/Users/jiamo/pcc-recovery-archive/2026-10-21/README.md`.
- Gateway throughput regression, open: HEAD 13,155 req/s (77 µs per request)
  against 65,764 (16 µs) at `983254526`. `json.dumps(..., sort_keys=True)`
  reaches the generic dynamic call and the compiled `pcc/stdlib/json.py`
  since `168e4d069`; generic per-operation costs added from `1a59d09df` on
  (foreign-lease slow path for immortal literals, a malloc per scheduler root
  handle, scratch-frame memset, frame notes, TLS). Fix the generic costs; no
  per-module fast paths (see AGENTS.md).
- Uncommitted: GC0 lease guard for immortal objects (`json-wt`, model tests
  only); str Unicode whitespace (`push-wt2`, native run fails at a float error
  path).
- Runtime classes are collected on GC0-GC4 (2026-10-22): classes are mortal
  and tracked, instances own their class, classes own their bases, and a
  method call releases a receiver its expression created. Repro:
  `uv run pytest -x -q -n0 tests/python/test_native_class_collection.py`.
  Class markers live on their own header bits (PY_FLAG_CLASS_SLOTS_ONLY,
  PY_FLAG_CLASS_DICT_SUBCLASS) since `77b30de47`. Open, in order:
  - Builtin subclasses: `isinstance(D(), dict)` is False because
    `emit_builtin_runtime_isinstance_impl` compares exact type tags for every
    builtin but str/tuple/type; `class D(dict)` called with arguments never
    reaches `py_dict_subclass_init_slots` (declared, not called), so
    `D(a=1)` is empty. Repro: a module-level `class G(dict): pass` and
    `G(a=1)`, on GC0 and GC4.
  - `tests/python/test_threaded_exception_class_cache.py`: its model lacks
    `py_class_new_builtin` (10 nodes).
  - `tests/python/test_py_value_class_unboxed.py`: 5 IR-shape nodes fail
    (e.g. `value.Point.unbox.typeerror` missing).
  - `tests/python/test_dict_super_slot_roots.py`: model lacks
    `_dict_raise_sequence_element` (2 nodes).
  - About ten IR tests still match only unmapped `pcc_gc_frame_enter_lifo(`
    (test_slot_call_exception_cleanup, test_numeric_return_owners,
    test_owned_cleanup_helper_ir, test_imported_constructor_binding,
    test_lambda_adapter_scope_lifetime, class_method_ir_checks,
    test_typed_attr_store_fast_path, ...).
  - DynType native method intercepts (bit_length, is_integer, the
    dict/list/set/str via-dyn emitters) do not release an owned temporary
    receiver.
  - User type tags are not recycled at class dealloc; pcc1 self-host with
    mortal classes is not yet run (gate needs the maintainer's go-ahead).
- Local-class `super()` segfaults on GC0-GC4 (repros in the archive).
- `pcc/stdlib/json.py` differs from CPython json (escapes, key conversion,
  messages, circular check, missing parameters); the module needs CPython's
  semantics in its general implementation.
- Release dry run 39453533690: sdist and four native wheels green.

### 0.1.8 wheel size (2026-10-20)

- `PCC_BUILD_PCC1=<smac5 pcc1> uv build --wheel` at `5a3414452`:
  `python_cc-0.1.8-py3-none-macosx_12_0_arm64.whl` is 126,479,187 bytes
  (deflate; 451.1 MB unpacked, of which pcc1 403.9 MB -> 115.9 MB). The
  maintainer requested a 300 MB PyPI file limit on 2026-10-18, so size is
  not a 0.1.8 blocker once granted. The 430 MB wheel below predates the
  pcc1 size reductions (pcc1 was 1.69 GB then).

### Host CPython 3.15.0 (2026-10-20)

- Host and oracle are CPython 3.15.0 on every platform (uv 0.13.3;
  3.15.0rc1 stays installed locally). Pushed to shenming: `d3c15423d`
  (subprocess capture), `024bfe68a` (host/oracle 3.15.0, gate identity,
  replacement contract), `1e5c7cec5` (netrc/configparser follow 3.15.0;
  configparser line splitting and `read_file`; sysconfig schemes and
  `prefix`; `os.fspath` messages; native `open(PathLike)`; native
  `sys.prefix` no longer runs `python3 -c`), `fa61609b5` (three stale test
  pins), `c2fd2d5fc` (linkage scan of non-ASCII artifacts), `1bc90dde1`
  (every CI job runs the interpreter `.python-version` names; the Docker
  wheel checks stay on the requires-python floor, 3.13), `ed822fc88` (the
  artifact-export test still pinned `--python 3.13`; CI 39451814729 failed
  it on Linux x86_64). Record:
  `docs/investigations/cpython-315rc1-host-migration.md` No.2.
- Same-source runtime archive under 3.15.0 equals the rc1 build
  (`d0c1738c...`). Stage1 of `406c1321a` (the content of `1e5c7cec5`
  without upstream `1c77ecb72`) under 3.15.0, snapshot `smac7`: rc 0,
  22 m 46 s, pcc1 `bf8773cd...`, runtime `a4617931...`; worker-prior gate
  node passes in 96.7 s; macOS package set 166 passed, 3 failed (two stale
  pins and the linkage regex, fixed in `fa61609b5`/`c2fd2d5fc`; the three
  pass with that pcc1). CI 39452230891 at `ed822fc88` in progress: the first
  run with 3.15.0 on all four platforms.
- Dict/list methods bind on receivers the compiler cannot type
  (configparser continuation lines), and `list.remove` raises ValueError
  where it raised exception tag 12 (ArithmeticError) unchecked. Stage1 of
  that change under 3.15.0, snapshot `smac8`: rc 0, 21 m 57 s, pcc1
  `556578fd...`, runtime `92b1b796...`; gate node 100.8 s; macOS package set
  170 passed (the three earlier build-exec failures included); C3, oracle
  and `open(PathLike)` tests 49 passed. Main checkout commits `95ad2e244`
  (README rewrite, `docs/c-howto.md`, runtime archive notes) and
  `e7a442247` (user `//` dispatch test, GC0-4 green).
- GC0 never frees classes: `py_class_new` marks every class
  `PY_FLAG_IMMORTAL` because instances hold an uncounted class pointer.
  Programs that create classes repeatedly (local classes, `type(...)`,
  `namedtuple(...)` in a loop) grow memory under GC0; GC1-4 reclaim them.
  Known limitation, deliberately not changed now (maintainer 2026-10-21);
  the untracked `test_native_class_collection.py` in the main checkout
  asserts the GC0 case and stays uncommitted.
- A class defined inside a function whose method calls `super()`, used
  from that function, segfaults on GC0-4 (`class_with_super` probe); next.
- Gateway: the newest recorded comparison is 2026-09-26 (pcc1 C1 4.93x,
  C10 1.33x, C100 0.77x of asyncio TaskGroup, a fifth of its RSS); a fresh
  run on the smac8 pcc1 against CPython 3.15.0 is next.
- Gaps found while validating (open, all fail the same before these
  commits): native `str` whitespace is the six ASCII characters (CPython
  also treats `\x1c`-`\x1f`, `\x85`, `\xa0`, `\u1680`, `\u2000`-`\u200a`,
  `\u2028`, `\u2029`, `\u202f`, `\u205f`, `\u3000` as whitespace) in
  `isspace`, argument-less `split`/`strip` and int/float parsing; native
  `repr` does not escape non-printable characters (`'\xa0'` prints raw);
  `str.strip(chars)` compares UTF-8 bytes, so a non-ASCII `chars` can cut a
  character in half;
  provider calls lower as dynamic `import.method` calls instead of direct
  `@user_*` calls since `168e4d069` made imports live (bisected with
  `test_native_sysconfig` dispatch; `test_native_path_protocol` 2 show the
  same shape); the fix is the guarded direct call `f1bcf01e8` gave
  from-imports, applied to `module.function(...)`;
  `native_system._emit_native_sysconfig_call` still routes to
  `py_sysconfig_get_config_var`, which runs `python3 -c`;
  `sys.exec_prefix` forces the libpython fallback;
  `test_intent_constraints::test_every_obligation_has_constraints`
  (`TestCrossBackendDeterminism`); `test_runtime_substrate_spike` 32;
  `tests/test_ci_native_object_regressions.py` 13 fail locally (green in
  CI).
- CPython conformance `Lib/test`: `/Users/jiamo/pcc-conformance/cpython-v3.15.0`
  (tag `v3.15.0`, `d220409`).

### Task queue (2026-10-20, maintainer-directed)

1. CI green on all four platforms with the 400 s prior-worker budget
   (39423549944 at `dd263e9ea`: all gates green, macOS build-exec failed on
   subprocess capture; 39441439531 at `d3c15423d`: macOS build-exec failed
   on `open(PathLike)`; 39451814729 at `1bc90dde1`: Linux x86_64 failed a
   pinned `--python 3.13` argv; 39452230891 at `ed822fc88` in progress),
   then keep optimizing.
2. Fix the 22 failures that fail with and without `2c5326898` (and the four
   unreported `test_compiled_repo_main_*` nodes of the same file):
   `test_py_multi_file_bootstrap_shim` (13: mocks of the removed
   `_link_with_clang`, fake pools without `admission_width`, removed
   libpython mode, hand-written assembly the owned assembler now rejects —
   "label '_main' before any .section", "instruction outside __text",
   "directive '.text' not proven" — and a `pcc_multi` timeout);
   `gcsubstrate_d_store_ptr_capi_pins` (2, pin the retired
   `pcc/runtime/src/py_internal.h`); `test_exception_chaining_wiring` (2);
   `test_native_obj_compare_float::test_runtime_object_comparison_has_one_behavior_owner`;
   `test_native_hashability_contract`; `test_port_abi_constants`
   (dispatch-table counts); `test_native_enum_stdlib` (copies the deleted
   `pcc/ir/support/enum.py`); `test_native_ordering_semantics` (requires
   `PCC_RUNTIME_ARCHIVE`). Each keeps its contract, retargeted to the
   current owner. The earlier pre-existing lists below (IR-shape nodes,
   native samples, GC4 leak counts, `test_gc_root_precision` 13) are part
   of this item.
3. Integration tests entirely on the owned toolchain: postgres (8 of 10
   tests link through `run_translation_units_with_system_cc` or
   `--system-link`; libpgport/libpgcommon, zlib and the server binaries
   come from configure/make with the host cc; the promotion gate's
   postgres-zlib/postgres-cli point at the same tests), Lua
   (`tests/c/test_lua.py` links every pcc-built Lua with `cc`; the
   Makefile and native oracles are cc builds), and the zlib/SQLite
   `freestanding_libc` variants (host cc link). The owned route exists:
   `CEvaluator.run_translation_units_owned` -> `emit_executable`.
4. x86 codegen time (1.5-2.1x aarch64) and the x86 structured-record
   pipeline (the AArch64 concept); first make `pcc1 --target x86_64-...`
   report its real error (`--diagnostic-format sarif` avoids the Enum crash).
5. Native Enum/metaclass semantics (below), subprocess capture, other
   recorded gaps.
6. Semantic compatibility of at least 90% (maintainer goal 2026-10-20),
   measured by a CPython `Lib/test` conformance harness (built 2026-10-21, see
   "CPython Lib/test conformance" above): pcc1 compiles each
   `test_*.py` against pcc's `unittest` and a minimal `test.support`, runs
   it, and compares per-case results with CPython. Known gaps it will
   count: runtime generic aliases (`list[int]`, `Callable[[str], str]`,
   `__class_getitem__` all raise "'type' object is not subscriptable"),
   module globals read inside lambdas (`typing` NameError), Enum/metaclass,
   int/float/list subclasses. Plan: CPython 3.15 `Lib/test` outside the
   repository; pcc1 compiles each module with pcc's `unittest` and a minimal
   `test.support`; a result printer reports every case; pass rate = cases
   passing natively / cases passing under CPython with the same shim.
   Generic aliases as one feature: class subscription falls back to
   `__class_getitem__` (classmethod binding) after the metaclass
   `__getitem__`; a real `types.GenericAlias` (origin, args, parameters,
   repr, eq/hash, call, substitution, `|`, `__mro_entries__`); builtin
   generics; `collections.abc`/`typing` providers that subscribe like
   CPython (both are approximations today: `typing.List` is a marker whose
   repr is "List"). Compiling a file under `pcc/stdlib` as a top-level
   module resolves its `import sys` to the sibling `pcc/stdlib/sys.py`
   (`test_platform_provider_compiles_as_top_level_module_no_libpython`);
   CPython's built-in modules (`sys`, `builtins` and the
   `Modules/Setup.bootstrap` set) must not be shadowed by sibling files.
   pcc's own parser parses 1057 of 1098 CPython
   3.15 stdlib files (96.3%, 88.3% of lines); the rest use PEP 810 `lazy
   import`, PEP 695 `type`/`def f[T]`/`class C[T]`, PEP 750 t-strings, and
   one lexer case (`dataclasses.py:469`, backslash).
7. CI 39423549944 (`dd263e9ea`): worker-prior gates PASS on all four
   platforms; Native wheel jobs PASS on Linux aarch64, Linux x86_64 and
   Windows x86_64 (their native pcc1 gates within the 400 s budget); macOS
   native pcc1 gate PASS (165.7 s) but
   `test_pcc1_build_exec_does_not_need_host_python` fails on
   `subprocess.run(capture_output=True)` returning stdout None. Fix in
   progress: `pcc.unsafe.spawn_process_redirect` (child stdin/stdout/stderr
   on given descriptors plus a working directory: Darwin posix_spawn file
   actions, Linux raw fork/chdir/dup2/execve, Windows CreateProcessW with
   inherited handle duplicates) and `py_subprocess_capture_*` (output to
   O_EXCL|O_CLOEXEC temp files, so a child never blocks on a full pipe);
   the provider's run() handles capture_output/stdout/stderr (PIPE,
   STDOUT, DEVNULL, descriptors), text decoding with universal newlines,
   cwd, env with timeout, FileNotFoundError and TimeoutExpired, and
   CalledProcessError keeps the output. The native `run(..., check=True)`
   statement lane now declines capture_output and fractional timeouts,
   which it used to drop or truncate.

### Gate regressions from the 10-19 pushes and their fixes (2026-10-20)

- Prior-worker native gate budget is 400 s (maintainer decision 2026-10-20,
  was 300 s; "pass first, then keep optimizing"): the gate watchdog
  (`NATIVE_GATE_TIMEOUT_S`, compile plus ten executions) and the native
  compile timeout in `tests/python/conftest.py`. The 300 s figures below
  are the earlier budget.
- CI 39400019947 (`1f6bfccf0`): all four worker-prior gates PASS, both
  x86-64 within the 300 s watchdog. Failed: the three Native wheel jobs on
  `test_ci_native_object_regressions` (workflow contract; peer `79b0d0ec7`)
  and macOS pip dry-run (`package_level` None: inspect reported a freed
  name, so `get_package_target` found nothing; fixed by `61177cb9e`).
- CI 39407829397 (`57110e01a`): every worker-prior preflight failed in
  `test_runtime_abi_chunking` (`0d8586233` made signature chunk 27 hold 51
  entries; `e636ddfd5`), and every native-pcc1 gate failed: pcc1's export
  worker for `os` crashed (SIGSEGV in `py_obj_type_name` from
  `pipeline_ast_wire._closed_world_node_kind`, a freed AST node). Cause:
  `61177cb9e` left a local that may hold a borrowed alias registered only as
  an owned root, so a later `x = other_local` or exact-int store took
  `pcc_gc_store_root_take`, which releases the previous slot value
  unconditionally. `5a3414452` registers it as a borrowed root too
  (`test_native_owned_local_alias_store`, second test). A pcc1 at
  `b4e25509e` reproduced the crash locally; one at `168bd6a39` did not.
  Local Stage1 at `5a3414452` (smac5, 19:35, 403,899,144 bytes, `__text`
  342.8 MiB): `test_size_prior_native_processes_five_collectors[pcc1]`
  passes under the CI guard (90.0 s for the compile and all ten runs, peak
  tree RSS 2.41 GB), and `test_package_inspect.py` passes (pip dry-run
  reports `requests` at `nolibpython_python`).
- Native Enum is a compile-time shortcut, not CPython's: members are the
  raw values (`Plain.A` is the int 1, `Sev.ERROR` the str "error"), so
  `.name`/`.value` work only in the literal form `Cls.MEMBER.value`;
  `repr`, `isinstance`, `Plain.A == 1` (True natively), `Cls(value)`
  (a new instance) and `Cls[name]` differ. In pcc1 this breaks
  `Diagnostic.format_text` (`self.severity.value`), so every diagnostic on
  the full-CLI path (`--target`, `-O`, `--emit-obj`, ...) dies with
  AttributeError and hides the real error: `pcc1 --target
  x86_64-unknown-linux-gnu` on the gate fixture fails this way, while host
  pcc0 compiles it. Generic metaclass gaps underneath: a metaclass
  inherited from a base in another module is ignored (`type(C)` is
  `type`); after a metaclass `__new__` the class body's attributes are
  evaluated again and written over what the metaclass set;
  `super().__new__(mcs, ...)` drops `mcs`; `len(cls)` returns 0 and
  `cls[k]` raises TypeError instead of calling the metaclass's
  `__len__`/`__getitem__`.
- Local parity gate with a pcc1 at `b4e25509e` (smac4): 7 failed, 162
  passed, all in build-exec. Next blocker there: `subprocess.run(...,
  capture_output=True, text=True)` returns stdout/stderr None (the owned
  provider's `run()` never captures; the platform intrinsic has one pipe).
  `os.pathsep.join` and module str constants (`501d71254`), multi-argument
  min/max (`a45edbc01`) and str-subclass equality (`d78e5da22`) were fixed
  on the way. smac4 pcc1: 403,257,016 bytes, `__text` 342.2 MiB, Stage1
  28:25.

- CI dispatch 39281274740 at `297c86729`: Linux aarch64 worker-prior gate
  passed. Failed: macOS native pcc1 (Stage1 below); macOS worker-prior gate
  (`aarch64-regalloc-host` nodes, stale monkeypatch; `63879cf8a`); Linux
  x86_64 `native-pcc0` (x87 tables and prior worker programs print nothing,
  rc 0); Windows `windows-exit-native` (the tar program prints
  `TAR_OPEN_BEGIN`/`TAR_FAILURE OPEN`, rc 0, no traceback; the 0xC0000005
  is gone since `68d379245`). Redispatched at `4950fb833`: run 39299993677.
- Stage1 at `297c86729` (local, 397 s): "branch to '..._parse_instruction_err
  dotframedotlanddot257178dotedgedot1' out of range". Inline error edges
  (`a372d3b58`, default) are `cbnz` to cold stubs after the blocks; that
  function exceeds 1 MiB and the AArch64 text finalizer had no relaxation.
  `053729315` relaxes out-of-range b.cond/cbz/cbnz in `_finalize_text_entries`;
  `73a154cba` (peer) finalizes the relaxed layout before PC-based metadata
  (stack maps, compact unwind) and `d057e51b1` validates raw fixups first.
- Stage1 at `63879cf8a` (2302 s) linked pcc1 but the smoke compile failed the
  ownership audit (rc 97): `py_decref` of an unregistered static int under
  `pcc_gc_store_root` in `codegen.literal_lowering`'s module top
  (`PCC_GC_REFCOUNT_PROVENANCE_PROBE=3` + lldb). `1c0a63ab5` registered only
  the str/float literal pools; `0fe1fba3e` registers the int pool.
- Stage1 at `0fe1fba3e`: rc 0, 2194 s (a contending host test run overlapped
  it), pcc1 422,138,776 bytes. Gate fixture compile with that pcc1, gate
  environment (`measure_prior.py`): 123.6 s, rc 0, peak tree RSS 2.77 GB
  (147.6 s at `827f157d6`, 251 s before). The gate node's executions were not
  rerun.
- x86-64 dropped every inline error edge: the shared `emit_function_blocks`
  never read the kernel's edge table, so errors stopped propagating on Linux
  and Windows x86-64. `4950fb833` emits them (test/jne, cold payload stubs)
  and refuses a target without an edge emitter. Native x86-64 (docker,
  passes off, inline edges on): an exception probe prints CPython's output and
  traceback with rc 1; at `0fe1fba3e` it skipped a `KeyError` handler,
  printed `chain <null>` and `unreachable`, rc 0.
- Windows under Wine (`pcc-wine64:local`, persistent prefix, D: mapped; run
  with `LANG=C.UTF-8`, or Wine fails every non-ASCII file name with ENOENT):
  the uploaded CI `metadata.exe` reproduces `TAR_FAILURE OPEN` rc 0. A
  cross-built text-route exe (no inline edges) opens and preflights the tar,
  then fails `utime` on the read-only member (errno 13); Wine's metadata
  semantics differ from Windows here, so the tar result needs real Windows CI.
- CI 39299993677 at `4950fb833` (first run past Stage1 on every platform):
  pcc1 prior gate passed on macOS arm64 (`[pcc1]` node 260.6 s) and Linux
  aarch64 (278.8 s). Linux x86_64 and Windows x86_64 both hit the 300 s
  budget with the four codegen workers still running (x86 pcc1 is ~4.5-5x
  slower than macOS pcc1; codegen workers started at 44 s and none finished).
  Windows preflight: the tar tests passed; the worker-size prior GC4 growth
  case timed out (one stop-the-world step plus full relocation ranking per
  safepoint; fixed in `0a8806592`). macOS: the native-wheel install passed;
  the pcc1 package parity hard gate stopped at
  `test_pcc1_pip_install_tilelang_extension_tree_direct` (180 s timeout;
  from the repo root, `Requires-Dist: numpy` resolves to
  `projects/numpy-2.4.4`, so pcc1 first compiles its vendored Meson and builds
  numpy; at `8958403fc` the same step failed earlier, on a Meson parse error
  fixed by `d7ed7d143`). Open: x86 register allocation; whether
  cpython-compat installs should build local dependency sources.
- x86-64 code quality (`worker_resource_plan`, same module on both targets):
  AArch64 216,584 instructions with ~20,000 frame accesses; x86-64 257,687
  -> 209,834 after `22b4e1d9a` (frame store->load forwarding), `7230bf1cf`
  (%al only for variadic calls) and `15970f04d` (compare-branch fusion), but
  still 69,084 frame-operand instructions. A block-local write-through cache
  in r12-r15 was tried and dropped: it only replaced already-forwarded
  reloads (slot loads 17,063 -> 18,556, +29k moves); the remaining reloads are
  cross-block values, pointers live across calls and loop-carried PHIs, which
  need function-level allocation as AArch64 has.
- x86 assembler (`x86_64_asm_driver.py`, Linux ELF and Windows COFF): the
  two-pass driver (parse into records, measure, emit) encoded every
  instruction twice, and most of its native time was its own record transport
  (struct-packed spans, generator unpacking, re-slicing). It is now one pass:
  x86 branches are always rel32, so each line is encoded once at its final
  offset; local branches and `.long a-b` are patched at the end and large zero
  fills stay lengths until every reference validates. Objects are
  byte-identical to the frozen driver
  (`tests/python/data/x86_64_asm_driver_two_pass.py.txt`) on the test corpus,
  real Linux/Windows backend output and the 11 MB x86 `worker_resource_plan`
  module. pcc-compiled driver on that module: 19.8 s -> 10.9 s (CPython
  1.39 -> 0.72 s). Docker amd64: 283 host tests, an executed ELF, the
  exception probe (CPython output) and both native-pcc0 nodes pass. The first
  diagnostic is now the first failing line; the old driver reported every
  directive error before any layout error.
- In that native run 36% was the call into `encode_instruction` itself: a
  module-level `from m import f` is a live binding, so every call read it
  through `py_module_attr_get` (two str allocations, two hash lookups) and
  went through `py_obj_call_slots` with an args tuple and a merged kwargs
  dict, for every cross-module function call in pcc1. A sampled coordinator
  IR pass phase spent 25% in the same binder, 20% of it from
  `Instruction.from_text`'s `cls(...)`.
- `f1bcf01e8`: such a call now reads the binding, evaluates each argument
  once into a root, and calls f's native body when the binding is f's plain
  function object (`py_func_slot_has_native_entry` against
  `<f>_native_adapter`); otherwise the same roots feed the dynamic call.
  Providers always define the exported adapters (`pcc.package.install` stops
  at an import pcc1 cannot bind and published none, which broke the first
  Stage1 link). `15449630e`: `cls(...)` constructs the owner directly when the
  closed-world export graph proves it has no subclass. `e87970617`: x86
  operands are split with native searches (5% of the assembler).
  Stage1 with all of them (local macOS, rc 0, 1430 s, pcc1 427,739,208 bytes,
  +1.4%) compiles the gate fixture in 100.6 s instead of 123.4 s.
- CI 39331126169 at `1829e26d8` (single-pass assembler): all four worker-prior
  preflights pass (Windows previously failed its GC4 growth case and timed
  out). The Linux x86_64 and Windows pcc1 gates still time out at 300 s:
  codegen workers start at ~74 s and the longest is still running at 295 s.
  x86 pcc1 runs x86 code without register allocation (~3.5x AArch64's frame
  traffic), so every phase is slower; in the old 11 MB module 4,491
  non-address-taken, single-width, non-SSE slots carry 40k of 45k slot
  accesses.
- x86-64 register passes (2026-10-20, `85e2cd325`..`8b9f8392f`), run over each
  emitted function's text, Linux and Windows alike:
  `x86_64_frame_registers` keeps scalar slots in callee-saved registers
  (rbx/r12-r15, plus rsi/rdi on Win64; interference from the text's own
  liveness; no stack-map root, `lea`'d or mixed-width slot) and drops stores
  nothing reads; `_thread_x86_jumps` branches past jump-only blocks;
  `x86_64_register_copies` forwards register copies and constants and drops
  moves nothing reads (strong liveness, flags tracked); scalar phi edges copy
  without stack temps when no destination overlaps a source.
  `pcc_gc_load_ptr`: 481 -> 238 instructions (AArch64 193).
  CI (pcc1 canary compile, mostly link; Linux x86 / Windows):
  `f1bcf01e8` 46.6 s / 112.0 s, `85e2cd325` 42.2 / 68.5, `4faef9195` 15.9 /
  37.1 (AArch64 24.3). Linux x86 pcc1 gate: timeout at `f1bcf01e8` and
  `85e2cd325`, **288.4 s PASS at `4faef9195`** (CI 39351778017); Linux
  AArch64 231-233 s. Windows at `4faef9195`: Stage1 platform qualification
  failed, the Windows pcc1 crashed (0xC0000005 reading 0x17da5b9003c, after
  codegen of the GC4 canary; gc0-gc3 passed, and GC4 passed at `85e2cd325`),
  so the pcc1 gate did not run. Not reproduced or symbolized yet (no Windows
  pcc1 artifact; see the Windows crash recipe in the memory notes).
  `1c49f0ddf` broke every Win64 frame of 4096+ bytes (the copy pass dropped
  `mov eax, SIZE` before `call __pcc_chkstk`); fixed in `8b9f8392f`.
  CI 39357267830 runs `8b9f8392f`.
- Open: the passes are cheap in CPython (1.2 s for `worker_resource_plan`'s
  50 functions) but ~9 s natively compiled (pcc1 `function_emit` for
  `pipeline_frontend_workers` on Linux x86: 41.6 s -> 61.4 s with the frame
  pass alone): list elements and dict values reach `&`/`|` through
  `py_obj_bitwise_dispatch`, `CompilerIntArena.get_unchecked` pays a frame
  per call. Moving the per-line facts and bitsets into arenas read through
  `native_address()` (as the stack-map liveness does) is the next step; the
  Linux x86 gate passes with 11.6 s to spare.
- Where the Linux x86 pcc1 gate spends its 288 s (CI 39351778017,
  `4faef9195`, 4 vCPU): export+summary 13 s; codegen 13 -> 242 s with four
  workers packed back to back (~920 worker-s); link 31.3 s serial (ELF stage
  inputs 4.4, select 4.9, layout 5.4, resolve/relocate 15.4 for 500,261
  relocations). Worker totals over 16 modules: frontend 184 s, backend 521 s
  (prepare verify 53, layout 49, stack-map plan 56, function emit 354),
  assembly 95 s (parse+encode 79.5). The tail is `hashlib` (93 s) starting
  at 149 s.
- pcc1 runtime costs seen in a natively compiled PE link of the Windows
  canary under GC0 (macOS, 2026-10-20): `struct.unpack_from` 29% (its format
  cache's `dict.get` alone 7%), `py_bytearray_extend` 12% (fresh object and
  a byte-at-a-time copy per append), GC root/lease bookkeeping ~20%
  (`pcc_gc_store_root`, `pcc_gc_foreign_lease_acquire`,
  `pcc_gc_root_copy_lease`), `memset` 6%. Every `dict.get`/set through
  `_dict_slot_set_bound` registered, leased and dropped 16 scratch roots
  while it names 7 (now 7: `3d1ea4b21`; bytes copies use memcpy:
  `bb610f00b`).
- In a pcc1 AArch64 codegen worker (sregs1 pcc1, 2026-10-20) generic runtime
  paths dominate: `py_obj_getattr` 19%, `py_module_attr_get` 11%, GC
  lease/root bookkeeping ~22%, dict slot frames 7%. `module.NAME` reads went
  through both: fresh name strs per lookup, and a class MRO walk on every
  read because the attribute cache recorded ABSENT only when the instance
  dict missed. `8a1ac3e35` caches pinned name keys, `f76ae0b18` records
  ABSENT on dict hits when no class attribute/field/method/`__getattr__`/
  dict-subclass fallback exists: a loop of three imported-module constant
  reads per call went 59.7G -> 45.8G -> 26.0G instructions retired, and
  22.2G once a GC0 foreign lease on an ordinary object only counts.
- Backend routes are not one concept yet. The pcc1 default route (host
  target, frontend workers, IR passes off) emits AArch64 as
  `indexed-native-sections`: hot instruction families become machine records
  at selection, the remaining lines become records one by one, and sections
  are built from records. x86-64 (Linux and Windows) emits
  `indexed-assembly`: every instruction is a text line, the store/load and
  compare-branch peepholes and the three register passes parse those lines,
  the module is joined into one text, and the owned assembler splits,
  parses and encodes it again. Same commit/fixture (`4faef9195`, CI
  39351778017): target-independent phases are 1.4-2.4x slower on x86, but
  object assembly is 94.7 worker-s on x86 against 13.8 on AArch64
  (`worker_resource_plan`: 17.5 s against 2.4 s). Direction: x86 gets the
  same record transport as AArch64 (records from selection, passes on
  records, sections from records; text only for residual lines and the
  oracle), differing only in encodings and machine passes. Step 1 (this
  session): the worker streams each finished x86 function into
  `X86ModuleBuilder` (route `indexed-native-sections`, Linux and Windows),
  byte-identical to the text route; selection still formats lines and the
  builder still parses them. CI 39365361651 (`5e22565c4`): Linux x86 pcc1
  gate PASS 274.7 s (288.4 at `4faef9195`, >300 at `8b9f8392f`); worker
  object assembly 94.7 -> 14.0 s, emit+assembly 449.1 -> 396.7 worker-s,
  all 16 modules `indexed-native-sections`; macOS gate step ~240 s.
  Next: hot families (frame load/store/lea, calls, jumps, immediates) as
  records from selection, passes on records.
- Windows GC4 canary crash is intermittent and predates this session's
  runtime changes: `4faef9195` and `5e22565c4` crashed (CI 39351778017,
  39365361651), `8b9f8392f` passed. Both crashes read +0x3c of the first
  object of a 64 KiB-aligned block (`0x17da5b9003c`, `0x238caa5003c`) in
  pcc1.exe's coordinator after the canary's codegen worker retired, with the
  same 15-frame stack shape. CI's Windows Stage1 is a host-target compile
  with the default Python IR passes, hence the text route, the same route
  as a local `--target x86_64-pc-windows-msvc` cross build. A macOS cross
  build of `5e22565c4` (35 min) does not reproduce CI's layout: 13 of the
  14 CI return RVAs do not follow a call in it, and its `.text` ends before
  one of them. CI uploads no Windows pcc1.exe, so the crash stays
  unsymbolized. The cross-built Windows pcc1 at `892687d08` compiled and
  ran the GC4 canary under Wine once. Under Wine the explicit runtime's
  codegen checksum differs from the host-built archive because
  `bootstrap_source_sha256` hashes `str(path.relative_to(base))`, which
  uses `\` on Windows. Set `PCC_SOURCE_ROOT` and replace every recorded
  `codegen_checksum` with
  `sha256(<bootstrap_source_sha256 over \-separated relative paths> + ":" +
  _runtime_emitter_source_identity(root))`; this formula reproduces both
  recorded values of the `892687d08` snapshot.
- CI 39370398888 (`1d2f48a3f`: name-key cache, ABSENT cache, GC0 lease):
  pcc1 prior gate PASS on Linux x86_64 (node 226.9 s; codegen workers
  187.6 s, link 26.8 s) and Linux aarch64 (202.6 s; 136.7 + 50.1 s);
  macOS gate PASS. `59fd6537a` adds GC0 shortcuts for root copies and
  `dict.get` (a `dict.get` loop 16.54G -> 10.11G instructions). Windows:
  Stage1 PASS (3499 s), canary GC0-GC4 PASS (compile 39.6/95.1/95.2/107.0/
  180.0 s), pcc1 gate TIMEOUT. From its process samples: export+summary
  ended at 33.7 s (Linux 10.6 s), the first codegen worker finished 127 s
  after codegen began (Linux 68 s), and workers were still starting at
  289 s. Linux split: export 0-6.9 s, summary 7.6-10.6 s, codegen
  11.3-187.6 s (643.6 worker-s; `worker_process_pool` 122 s,
  `worker_resource_plan` 109 s), link 26.8 s. The fixture kept pcc1's
  stderr in memory, so the killed Windows run left no worker timing;
  `fb4fb0a91` streams it to `<output>.compile.stderr`. `3018b104f` counts
  GC0 root stores in place. CI 39376986157 runs `fb4fb0a91`.
- CI 39376986157 (`fb4fb0a91`): **Windows pcc1 gate PASS** (node 258.2 s;
  export 0-6.5 s, summary 6.9-10.8 s, codegen 11.4-212.5 s, 737.2
  worker-s; five-collector canary incl. GC4 PASS). Linux aarch64 and macOS
  gates PASS. Linux x86_64 FAIL: codegen workers ran ~30% faster than at
  `1d2f48a3f` (`os` 49.8 s vs 71.6 s), but the `hashlib` worker died at
  `class _SHA256` with "pcc re: native regex engine limit reached" (the
  matcher's recursion passed 8192, `freestanding_re_engine._match`). The
  same CI pcc1 under Docker amd64 compiled `import hashlib` alone fine;
  a full gate compile in Docker is the next repro. Cause not known yet.
- CI 39383157605 (`9e953a1fb`): Linux aarch64 and macOS pcc1 gates PASS
  (codegen ends at 130.6 s and 104.5 s; 415.4 and 274.4 worker-s, against
  428.8 and 400.0 at `1d2f48a3f`). Both x86-64 gates FAIL on the 300 s
  watchdog: Linux codegen ran 14.4-256.1 s (888.8 worker-s; `os` 97.7 s
  against 71.6 s at `1d2f48a3f` and 49.8 s at `fb4fb0a91`) and the ELF link
  was at 207,794 of 500,591 relocations when killed; Windows hit the same
  deadline. Every module slowed by the same 36-40% with no x86 change in the
  range, so runner speed is the likely cause (the receipts record no CPU
  model). Either way x86 codegen has no margin: it is 1.5-2.1x aarch64's.
  macOS `__text` 362.6 MiB at `1d2f48a3f`, 363.0 MiB at `9e953a1fb`; a local
  Stage1 at `168bd6a39` is 402,781,000 bytes (`__text` 341.8 MiB, 26:25).
- Ordering comparisons follow CPython: an unorderable pair (int < str,
  None, complex, dict, set < list, instances whose methods are missing or
  return NotImplemented) raises TypeError instead of comparing equal;
  tuples/lists find the first unequal item with `==`; str/tuple subclasses
  order by payload; list.sort/sorted/min/max propagate the error, and a
  failed list.sort keeps every item (`test_native_ordering_semantics`).
  Found alongside, still open: int/float/list subclasses have no payload
  ordering or arithmetic (`MyInt(5) + 1` raises TypeError, `MyFloat(1.5) +
  1` does not compile); `.count` on a dyn tuple resolves to the str method
  and on a lambda-captured list crashes in `py_str_count_range`; two- and
  three-argument min/max lower dyn operands through the i64 lane
  (`min(1, "a")` returns 0), refuse two str operands at compile time, and
  return float for `min(1, 2.5)`. Failing at `168bd6a39` before these
  changes: GC4 leak counts in `test_min_max_key_ownership` (callable,
  collect, factory, items) and `test_sort_key_ownership` (six shapes);
  `test_runtime_object_comparison_has_one_behavior_owner` since
  `d0b8b6720` (a second runtime-symbol table in the exact-compare path).
- Local parity gate with a pcc1 at `168bd6a39` (smac3): 13 failed, 156
  passed. inspect named every package "os"/"darwin" and pip dry-run lost
  its package level: `package_name = package_name or "package"` in a
  raw-scaffold module released the owned string before storing the same
  pointer back as borrowed (fixed in `61177cb9e`). build-exec stopped at
  `toolchain._find_tool`, a stub because `shutil.which(path=...)` fell back
  to CPython (`0d8586233`). array-core printed a 2-D repr on one line: the
  bootstrap CLI keeps its own array-core copy (`cli_bootstrap_array_core`,
  kept separate because closure calls lower to getattr bridges, see
  `tests/fallback_baseline.json`); its repr now mirrors
  `array_model.array_core_repr`. Still open in that copy: a 3-D literal
  parses as shape [2, 0]. The real `pcc.package.array_core` compiled
  natively by pcc0 matched the host on 15 commands, 3-D included.
  Failing before these changes at `168bd6a39`: 38 IR-shape nodes in
  `test_raw_scaffold_exact_container_subscript_ownership` (18),
  `test_py_for_target_representation_join` (6),
  `test_py_multi_file_compile` (5), `test_owned_ifexpr_local_transfer` (4),
  `gcsubstrate_b_root_store_scheduler_queue` (4) and
  `test_raw_scaffold_regex_ownership` (1); and `test_native_set_inplace_update`
  (6), `test_native_module_attr_pointer_storage` (2),
  `test_native_dyn_tagged_int_binop` (1).
- macOS package parity gate (`-x`): after tilelang (`343bf5a0b`) the
  suite had 12 more failures with a HEAD pcc1, all hidden by `-x`. Fixed:
  the owned `Path` lacked expanduser/mkdir/iterdir/... (`6648deec3`; it
  also fixes `with (expr).attr() as f:` parsing), `==` ignored
  NotImplemented for typed instances and `-> bool` dunders, `key == 1`
  did not compile (`28dcc9fa5`), and inspect named `demo_pkg-0.1`
  (`d49aafd8c`). The owned `ast.literal_eval` accepted only ints and flat
  int tuples (array-core printed `array(None)`); it now parses CPython's
  literal grammar, which exposed native gaps fixed alongside: PEP 515
  underscores in `int()`/`float()` text, `int("010", 0)`, dynamic
  `int - complex`, compile-time complex promotion against non-numbers,
  and `isinstance(x, complex/memoryview)`. The native inspect JSON now
  comes from the host's `inspect_package`/`inspect_artifact`
  (`fc4e97285`). build-plan/build-exec then stopped at
  `metadata._read_meson_boolean_options`, a no-libpython stub: module
  `re.search/match/fullmatch/finditer` with `re.I | re.S` or `flags=` fell
  back to CPython, and native text files refused `errors="ignore"`
  (only strict/surrogateescape: tell()'s offset map was inferred from the
  decoded text). The decoder now reports handler spans, files accept every
  handler and latin-1, and `ascii()` escapes non-ASCII inside any repr
  (`test_native_text_file_error_handlers`, `test_native_ascii_builtin`).
  Still open: ordering comparisons return
  False instead of raising TypeError when both sides answer
  NotImplemented; a complex-typed BinOp passed straight to a call
  (`repr(a - b)`, or returned from a lambda: "slot-call operand has no
  authoritative source or owned result") does not compile, though
  dynamic BinOps as arguments do; a pcc1 built before
  `28dcc9fa5` printed True for `key == 1`. pcc1 also cannot build a missing runtime archive
  (`emit_owned_object` unavailable natively), so local runs need the
  archive at the default path, as CI's Stage1 leaves it.
- Stale source-shape tests, failing at HEAD before this session's changes
  (their pinned text left in `983254526`/`bae3546e2`):
  `gc/test_gc_backend_config_fastpath.py` (2),
  `test_fresh_native_instance_append.py` (2),
  `test_gc_abstraction_surface.py::test_colored_relocating_gc_read_barrier_clears_candidate`.
- Windows is slow mostly in the PE link, not in the workers. Canary at
  `8b9f8392f` (CI 39357267830, GC0): codegen workers done at 3.2 s
  (Linux 2.8 s), then the coordinator alone ran 55 s (Linux 21.3 s, of
  which runtime verify 1.75 s). PE sections were grown by
  `bytearray.extend` per input section and `.reloc` per word; pcc's native
  bytearray copies itself on every extend, so that was quadratic in pcc1.
  `23b4d4a8c` joins the pieces once (budgeted CPython hosts unchanged): a
  natively compiled PE link of the canary from captured inputs went
  9.81 -> 8.06 s on macOS, image byte-identical. What remains there is GC
  protocol: `pcc_gc_store_root` 8.3% (called by compiled code),
  `pcc_gc_load_ptr` 6.8%, leases 6.1%, frame enter/leave 6.6%,
  `pcc_threads_enabled` calls 3.3%.
- Failing at HEAD's runtime ports (2026-10-20, pcc0, macOS):
  `test_native_namespace_projection_behaviors` rebinding/module-override
  (`globals()['sys'] = x` not seen by a later `sys.attr` read) and
  `test_live_property_lookup` five-selector/unresolved-replacement;
  with HEAD's worker, `test_direct_indexed_syscall` zero-capture-fallback
  (both targets), `test_direct_indexed_owned_passes` memory promotion and
  `test_stage1_checkpoint` frontend release (stale frontend stubs);
  `test_live_os_environ_provider` (mapping), `test_owned_os_path_provider`;
  model tests `test_gc_codegen_write_barrier` (4),
  `test_exception_chaining_wiring` (2), `test_class_lookup_cache_runtime`
  (2), `gcsubstrate_d_store_ptr_capi_pins` (1); ~150 cases in 38 model
  tests that touch leases (`test_virtual_thread_*`, `test_buffer_factory_*`,
  `test_bytes_decode_result_roots`, `test_owned_descriptor_module_bindings`,
  `test_freestanding_gc_root_operations` raw closure, ...), identical with
  HEAD's root operations. None runs in CI.
- macOS fails only `test_pcc1_pip_install_tilelang_extension_tree_direct`:
  `Requires-Dist: numpy` resolves to the repository's `projects/numpy-2.4.4`
  through `install._resolve_spec_with_origin`, so the install builds numpy and
  hits the 180 s timeout; the test also expects the tree itself as
  `installs[0]`, which holds only when no dependency resolves. Open decision:
  whether installs build local source copies of transitive dependencies.
- Failing on master, seen while running neighbouring files:
  `test_precise_stackmap_abi.py::test_aarch64_label_offsets_skip_normalizing_ordinary_instructions`
  (`.p2align 3` in `__text` rejected by `measure_text_layout`, since
  `73a154cba`); `test_self_obj_pcc_route.py::test_darwin_default_uses_pcc_writer_without_system_assembler`
  (its fake `emit_object` lacks the new `weak_undefined` keyword);
  `test_py_literal_splat_source_position.py` (asks for libpython, which the
  owned runtime does not include); and on a clean `1829e26d8` checkout:
  `test_container_emitted_ownership.py::test_literal_unwind_consumes_dynamic_string_temporary`
  (GC4 heap grows 26,296 bytes over 200 unwinds, limit 16,384; also at
  `b4cff8885`, before `0a8806592`);
  `test_slot_call_operand_roots.py::test_literal_integer_tree_publishes_every_result_before_cleanup`
  (expects `py_int_from_cstr`; over-lane literals are static since `1c0a63ab5`);
  `test_py_multi_file_compile.py::MultiFileCompileTests::test_borrowed_object_local_rebind_keeps_gc_root`;
  `test_py_class_kwargs_no_init.py::test_no_init_class_call_accepts_keyword_attrs_without_libpython`
  (expects keyword attributes on a class without `__init__`; CPython raises
  TypeError); `test_cross_module_none_return_abi.py::test_cross_module_declarations_match_definitions`
  (no `Helper_reset` definition in the combined IR);
  `test_test_infrastructure_efficiency.py::test_pcc_gate_linux_execution_markers_respect_simulated_platform[linux-Linux-x86_64-True]`.
- Not done: reading live bindings through a cached module dict and a static
  key (`py_module_attr_get` allocates both keys each time; a dozen tests pin
  its name); `ir_mutator._parse_function` matches `_BLOCK_LABEL_RE` per line
  through the generic call (3.6% of that pass phase; passes are off on the
  gate route).
- Code size: a call statement such as `total += check(i, fail_at)` is about
  116 AArch64 instructions with ~20 runtime calls (store_root 4, unpin 2.6,
  frame_leave_lifo 2.3, load_ptr 2, pin/err_occurred/release 1.5 each).
- Open, failing on master and not from these changes: GC4
  `test_dict_literal_temp_release.py[4]` (the splat branch loses the objects
  live across one GC4 cycle: `B21`, `B22`, `B22` at n=50; each shape alone is
  clean; n=10 none lost, n=200 two);
  `test_direct_indexed_syscall.py::test_ordinary_unsafe_write_worker_has_zero_capture_fallback`
  (no `syscall6` record when the test overrides `_target_triple` on a Darwin
  host); `test_direct_indexed_owned_passes.py::test_direct_worker_promotes_memory_before_pco_emission`
  (19 allocas before and after the default passes; independent of inline
  edges).

### Precise stack maps, step 3 (2026-10-19, `e1ec9b422`..`4441a1f48`)

Order chosen: 3 (consumer, differential first) -> 2 (safepoint pruning) -> 1
(compact encoding, shaped by the consumer's lookup) -> 4 (remove the
registered-slot protocol). Step 3 state, AArch64 Darwin:

- A table-enabled (`PCC_PRECISE_STACKMAP_TABLES=1`) Mach-O executable defines
  `_pcc_precise_stackmap_table`; its `main` publishes it to the runtime
  (`pcc_gc_precise_stackmap_table`). ELF (`elf_x86_64.py`, SHA-pinned: needs
  the maintainer's preimage approval) and PE links do not define it yet, so
  `main` publishes only on Darwin.
- `pcc.unsafe.frame_address()` reads x29/rbp. Call safepoints are now recorded
  at the return address (`cc81b34c1`; they followed the result moves before).
- The first walk validates the table once and builds an address index;
  `pcc_gc_visit_precise_frame` binary-searches a return address.
- `PCC_GC_STACKMAP_DIFF=1`: GC3/GC4 explicit collections and GC4 automatic
  steps compare mapped and registered roots both ways (`mismatches`,
  `unmapped`); all zero on the four-deep, root-shape and allocation-churn
  programs.
- `PCC_GC_STACKMAP_ROOTS=1|report`: GC3/GC4 mark and rewrite this thread's
  frame roots through the walk and skip the registered slots in the walked
  stack; the same programs print CPython's output (GC4 moves objects).
- Not covered yet: GC0-GC2, GC3 promotion (mode 2 uses registered stable
  values), pins/leases, other threads' stacks, compiler-sized workloads (needs
  a table-enabled Stage1), ELF/PE. Tests: `tests/python/test_precise_stackmap_table_policy.py`
  (Darwin-gated native cases use `pcc_stackmap_runtime_archive`).
- Step 2 (`4d35eb241`): tables omit call/loop records with no location,
  flag or exception edge (the walk skips such frames anyway): a table-enabled
  churn executable went from 223,637 records / 7.48 MB to 163,822 / 5.57 MB,
  same 385,966 location references. Further pruning by callee (calls that
  cannot collect) is left until step 4: the most frequent callees in the
  runtime are the registered-slot protocol itself (`pcc_gc_store_root` 15k,
  lease acquire/release 12k each, pin/unpin, write-barrier notes), which step
  4 removes with their records.
- Step 1 (`99472792d` format, then the link/runtime commit after it):
  `PCC_PRECISE_STACKMAP_FORMAT=3` makes the final Mach-O link (both the
  generic and the CPython staged route) transcode the merged version 2 table
  into the version 3 runtime table (`precise_stackmap.transcode_runtime_table`:
  8-byte records, deduplicated live sets, int32 slots, a rare table for
  exception/continuation fields). Objects, relocatable merges and semantic
  layout stay version 2; semantic layout refuses a version 3 table. The
  runtime indexes and walks either version. Root-shape program with the
  table-enabled runtime: table 5,674,120 -> 1,719,884 bytes, image 14.9 ->
  10.9 MB (166,700 records share 3,192 live sets); format 2 and 3 pass the
  differential and stack-maps-alone tests. Default stays 2. ELF/PE links keep
  version 2. Incremental Mach-O link images are keyed by the format, and
  inputs with a stack-map section are never layout-patched (the merged table
  is rebuilt, not concatenated). Not run: pcc1/bootstrap with the new linker
  code, a format-3 Stage1.

### pcc1 prior-worker gate: IR size (2026-10-19)

- `Verify the new pcc1 on native prior worker shapes` (300 s) has not passed
  on any platform since it first ran (`c09bf73e7`, 10-15); the 10-14 pass was
  the pcc0 preflight. With pcc0 the fixture is 16 modules, 51.5 MB of IR
  (passes off), 559k instructions, 49% calls; protocol calls dominate
  (`frame_leave_lifo` 34k, `store_root` 22k, foreign leases 29k, pin/unpin
  18k). Module tops were 10.8 MB; the largest function was
  `_pcc_py_module_top_pcc_unsafe` (4.53 MB, 216 trapping defs that never run).
- Capture-free definitions with a static signature (constant defaults,
  literal docstring) are now built by one runtime call,
  `py_func_new_static_def`, instead of ~80 inline calls per `def`: fixture
  IR 51.49 -> 43.68 MB, module tops 10.84 -> 4.40 MB. Five-collector native
  test: `tests/python/test_static_function_definitions.py`.
- Where a pcc1 compile of that fixture spends its time (98422a5b6 Stage1,
  local, contended run of 618 s; uncontended 458 s): frontend codegen 71 s of
  worker CPU (pcc0: ~5 s); emit 322 s in three sequential lanes (oversized
  119 s, medium 80 s, small 123 s); link and codesign ~190 s. Profiles
  (`scripts/pcc_flamegraph.py cpu`, symbols fixed in a5bdde1d8):
  - emit worker: 91% in the pcc-Python regex engine under
    `self_backend_parse._resolve_declaration_type_attributes`; replaced by
    str.find in cf25cbc26;
  - link tail: `_prepare_executable_image` 32%, codesign SHA-256 24%,
    `MachOStruct.pack` 21% (positional nlist packing, 5917b29bf),
    `_Out.__eq__` via per-symbol `list.index` 8% (identity map, d8feef130);
  - other workers: ~40% in GC protocol calls (store_root, leases, frame
    enter/leave, minor-graph lock), 5% `_tlv_get_addr`;
  - cross-module attribute reads through an `Attr` receiver
    (`self.parent.env`) lower to by-name `py_obj_getattr` even when the
    receiver's class is known: the field fast path in attr_load_lowering
    handles only `Name` receivers. L1CodeGen has 227 fields, scanned linearly.
- Local Stage1 of `0272b8f4f` (snapshot `/Users/jiamo/pcc-snapshots/s0272b8f4f`,
  `stage1_driver.py`, passes off): rc 0 in 1531 s; pcc1 424,850,744 bytes
  (98422a5b6: 459,829,544). Includes static defs (510b5e094), the emit and
  link fixes above, the Attr receiver field path (ed25f93d8), static str
  literals without pins (90138fd9b) and `IRBuilder_call9` (362e27bae: the
  nine-argument static def call made every Stage1 since 510b5e094 fail to
  link pcc1 on `_user_pcc_ir_ir_IRBuilder_call9`; this was also the cause of
  the passes-on link failure, not the passes). That pcc1 compiles the prior
  fixture (`measure_prior.py`, uncontended) in 251.5 s, rc 0 (458 s before):
  emit lanes oversized 45.5 s, medium 27.4 s, small 34.5 s (were 119/80/123).
  The gate's own node (10 native executions of the output) was not rerun.
  Not in that Stage1: omitted str defaults bound directly (72f03f44a).
- Still open: `pcc.unsafe`/`pcc.extern` are compiled although their
  from-imports are consumed at compile time (5.8 + 1.5 MB); per-operand
  temporary frames and leases at every call; the x86_64 backend has no
  register allocator (x86_64 pcc1 ~5.4x slower than aarch64); emit throughput
  (assembly text round trip). Local Stage1 of `98422a5b6` (snapshot
  `/Users/jiamo/pcc-snapshots/s98422a5b6`, 2144 s, pcc1 460 MB) is the
  baseline for timing the gate locally.

### pcc1 prior-worker gate: time (2026-10-19 evening)

- Record: `docs/investigations/pcc1-prior-gate-compile-time.md`. Profiler:
  `scripts/profile_compile_phases.py` (guarded compile, worker + link
  samples, `/usr/bin/time` CPU; finds the compiler by executable path).
- Landed: native workers share the phase priors (5a9d434dc; codegen tasks
  were admitted one at a time), shared return exit (14315bddf), direct
  indexed route default for host executables with IR passes off (116c8cfce;
  the gate runs passes off), per-declaration name scan without regex
  (3a334e1e5, fix 7ab406f25), radix relocation order (fd804086c), one-reader
  Mach-O symbol tables (04dd3fdce), emission sink constants (827f157d6),
  relocation records as a list via `macho_obj.new_relocation` (c452f9f90).
- Local prior compile, gate environment, quiet Mac (12 cores): text route
  251-260 s -> v4 Stage1 164 s / 512 s CPU -> v5 (through 827f157d6)
  147.6 s / 450 s CPU: 16 s export/summary, 63 s parallel codegen, 68 s
  serial link (snapshots `/Users/jiamo/pcc-snapshots/sv4`, `sv5`).
- Self time of a pcc1 worker/link is ~45-49% GC roots/barriers/locks, 11-13%
  allocation, 10-11% refcount; the slot-call lease/root-handle protocol is
  21-25% inclusive. Imported constants are live module bindings
  (`py_module_attr_get`, ~14% of a worker).
- CI dispatch 39256241026 at c452f9f90 is the first run with these. Open:
  ELF/PE link not profiled (`elf_x86_64.py` is SHA-pinned by
  `tests/test_elf_*_source_controls.py`); the x86_64 backend has no register
  allocator, so x86_64 Linux/Windows remain the hardest gate platforms.
- CI 39256241026 (c452f9f90), native pcc1 prior: macOS arm64 TIMEOUT (3
  codegen workers until ~230 s, then the coordinator still linking at 300 s;
  tree cap 2.97 GB); Linux aarch64 killed at 300 s in ELF relocation (745k
  relocations, 19/515 sections), codegen done at 246 s with 800 s summed
  worker time; Linux x86_64 also timed out.
- Same hashlib codegen manifest, same Mac, pcc1 vs CPython: 42.3 s vs 4.68 s
  (9x, roughly uniform across phases). A sampled pcc1 worker: 45.7% GC
  roots/allocator index, 7.9% refcount, ~9% the compiler's own code; foreign
  leases 10.7% and `pcc_gc_store_root` 10.1% inclusive. Codegen order follows
  the memory prior, so hashlib (105 s on CI arm) started at 139 s.
- Linux-only: `object_encode` is 181.7 s of the CI arm backend (hashlib 27.2 s
  vs 0.14 s under CPython). Mach-O (`native_object`) and COFF encoders join
  chunks; ELF (`elf_x86_64.emit_relocatable`, `arm64_elf_driver`) appends
  `bytearray += pack(...)` per relocation/symbol. In pcc runtime a bytearray
  has inline storage, so `+=`/`extend` allocate a new object and copy byte by
  byte: O(N^2), and `a += b"y"` rebinds (an alias keeps the old bytes;
  `a.extend` on a dynamic receiver raises AttributeError). Editing
  `elf_x86_64.py` needs approval of its SHA pins; the in-place bytearray
  (separate growable buffer) is the root fix.
- Windows preflight `test_windows_tar_attrs_native` 0xC0000005 (since
  2026-10-15): 8958403fc reports unhandled faults (code, RVA, RBP frames) and
  uploads the test's exe; map RVAs with `.pdata` size alignment against a
  local cross-build. Cause: a program inside a pcc tree compiles both
  `pcc.stdlib._compression_stream` and `_compression_stream`; the
  `except ImportError` alternative won the static binding, so gzip's class
  read an uninitialized base (NULL+0x28 in `py_class_new`); macOS in-tree
  segfaults the same way. Fixed in 68d379245 (`import_alternatives`).
- Native ELF encoding (host-pcc0-compiled `emit_relocatable`, Mac): the
  CI-hot hashlib Linux object (63,411 relocations) took 13.7 s; joined
  chunks (this change, pins repinned with approval) 2.06 s; 191 small
  runtime objects 18.9 s -> 10.2 s with the struct change. CPython: ~0.3 s
  for all 191, so pcc stays ~30x slower there: `Struct.pack` was 47.7%
  (2**64 bigint literal reparsed per field, bigint bounds, a bound method
  per call). Struct now packs without bigints; int literals outside the
  tagged lane become static immortal objects.
- Code size of pcc1 (60 sampled functions): a `bl` every 5.6 instructions;
  calls are GC frame enter/leave 11.2k, store_root 5.5k, leases 5.7k and
  ~13k error-path calls (traceback source, exception swap, cleanup); 4.1%
  `nop` separators from stack-map terminator routes, 7% unconditional `b`.
  Unbound checks on definitely-bound locals are gone (439d8274e); next:
  shared error-path blocks and leave/cleanup exits.
- Pre-existing failures at ed28d685f (same on the baseline snapshot), open:
  `test_exception_constructor_owned_result.py` (6),
  `test_exception_raise_lifetime.py` (3),
  `test_exception_chaining_wiring.py` (2),
  `test_native_exception_context_chaining.py::test_handler_codegen_scopes_active_context_to_function_boundary`,
  `test_ir_py_with_struct_port.py::test_ir_py_shared_float_bits_helpers_are_compiled`.
- Pre-existing failures at ed28d685f, unrelated to these changes:
  `test_dynamic_import.py` (import_module 'math', reload),
  `test_cpython_compat_cext_import.py`,
  `test_native_local_from_import_values.py::...real_ir...`,
  `test_native_package_star_import.py::..._all_dunder_names`,
  `test_recursive_stdlib_import_codegen.py` (3),
  `test_isinstance_imported_type_name_shadows_builtin.py::test_the_guard_reads_import_bindings`.

### 0.1.8 release readiness (2026-10-18)

- macOS arm64 Stage1 at `85650ac4b` (local, `scripts/bootstrap.py --stage 1`,
  auto cap 9.78 GB): rc=0 in 2730 s; the owned link passed `fixups`, so
  `10be0ed89` holds on the real image. pcc1 is 1,686,784,024 bytes (gzip -6
  385 MB; the 10-13 build was 674 MB before precise stack maps), above PyPI's
  default 100 MB per-file limit. It runs `--help`, links only libSystem, and
  compiled a function-bearing program with no host Python/compiler; the
  program prints 42 under GC0-GC4.
- That compile took 108 s, 88 s of it in the owned Mach-O link (host pcc0:
  3.8 s on the same input). `012ec3265` trims the per-relocation work there:
  its Stage1 (2133 s locally) links the same program in 68.6 s (70.6 s total,
  GC0-GC4 print 42). In that pcc1, `_iter_folded_relocation_rows` is 61% of
  the link and ~45% is `Relocation(...)` built through `py_module_attr_get` +
  `py_obj_call_slots` + kwargs merging, while the closed-world compile of the
  same module constructs it statically (`py_instance_new` + direct
  `Relocation.__init__`).
- Root cause of that dynamic construction: in multi-module compiles (CLI
  multi-file programs and Stage1 workers) an imported name or module attribute
  is a live import binding, so `R(...)` / `mod.R(...)` / `mod.f(...)` go
  through `_emit_slot_call_object` (`call_expression_lowering._emit_call`, and
  the `live_import_expr_binding` branch of
  `native_modules._maybe_emit_native_module_alias_call`) before any static
  binding rule runs. Probe built by host pcc0 at `012ec3265` (1M
  constructions of a frozen slots dataclass): imported + keywords 17 s,
  imported + positional 5 s, same-module 1 s; CPython ~1 s. The closed-world
  check (`compile_contextual_per_module_fallback_counts`) compiles the same
  calls statically, so it does not represent Stage1 lowering here. Binding an
  import statically needs a proof that the importer never rebinds the name
  (and an answer for import cycles); not changed. The proposed proof (a
  closed-world write-escape analysis with program-wide disabling) is section 7
  of `docs/research/reconstructed-runtime-research-directions.md`, which also
  ranks the other concepts taken from ahead-of-time compilers (sections 5-12;
  none implemented).
- macOS wheel from `012ec3265` (`PCC_BUILD_PCC1=<that pcc1> uv build --wheel`):
  `python_cc-0.1.7-cp315-cp315-macosx_26_0_arm64.whl`, 430,773,781 bytes.
  `scripts/ci_native_wheel_gate.py` passes. In a fresh venv `import pcc`,
  `python -m pcc` (4 s compile) and `pcc1` (75 s compile) each build a
  class/dict/exception/big-int program that matches CPython on GC0-GC4 and
  links only libSystem. The wheel exceeds PyPI's default 100 MB file limit
  (open release decision). Its tag was inferred from the build interpreter
  and host (`cp315`, `macosx_26_0`); the hook now writes `py3-none-<platform>`
  (`macosx_12_0` from pcc1's minos, `manylinux_2_17` for the static ELF,
  `win_amd64`), and the rebuilt `py3-none-macosx_12_0_arm64` wheel passes
  `ci_native_wheel_gate.py` under Python 3.13 and 3.15.
- Windows at `169bcfca1`: GC0-GC3 compiled and ran the canary; GC4's canary
  compile hit the stage deadline. Linux x86_64 and aarch64 built pcc1 and
  timed out in the pcc1 prior-worker gate (aarch64: one oversized emit worker
  ran 291 s of the 300 s budget).
- CI run 39058771517 at `191984ced`: macOS arm64, Linux x86_64 and Linux
  aarch64 each built Stage1 pcc1 (Linux also passed the five-collector
  canary), then each hit exit 124 at the 300 s budget of
  `test_size_prior_native_processes_five_collectors[pcc1]` (pcc1 compiles the
  driver, then runs it under GC0-GC4; locally a one-function pcc1 compile is
  70 s, mostly the owned Mach-O link). Windows failed inside Stage1/canary
  (`bootstrap_platform.py --gc all --stage 1`, rc=1) and in the preflight
  `tests/python/test_owned_windows_exit.py` run.
- Where that 300 s goes (compile logs in the run's `bootstrap-*` artifacts,
  `PCC_PY_FRONTEND_WORKER_TIMING=1`): the gate compiles
  `tests/fixtures/native/worker_size_priors.py` plus 15 imported modules (os,
  subprocess, hashlib, warnings, pcc worker modules, ...). Linux x86_64 spent
  232 s in the frontend alone; Linux aarch64 finished the frontend in 67 s and
  spent the rest in emit/link. Per module, x86_64 pcc1 is uniformly ~5.4x
  slower than aarch64 pcc1 on the same inputs (`worker_process_pool` codegen
  96.0 s vs 17.9 s; infer 1136 ms vs 208 ms; local macOS arm64 10.4 s), so the
  x86_64 gap is the native compiler as a whole, not one function. Host pcc0
  frontend is linear in a `+` chain (8/16/32/64 terms: 13/24/49/103 ms);
  pcc1 is ~20x that per term (199/412/868/2052 ms).
- The x86_64 self backend has no register allocator:
  `pcc/backend/self_backend_x86_64_linux.py` keeps every SSA value in a stack
  slot ("Every value lives in a stack slot") and `_materialize_value` reloads
  it at each use, while aarch64 (Darwin and Linux) uses
  `self_backend_aarch64_darwin_regalloc.py` (2253 lines). Windows x86_64
  emits through the same `_emit_x86_64_module`. x86_64 pcc1 is itself built
  by this backend, which is consistent with the uniform ~5.4x gap and the
  Windows GC4 canary deadline; how much an x86_64 allocator recovers has not
  been measured.
- The IR is the other half: this program lowers to 65 MB of IR in 16 modules
  (`worker_size_priors` alone, 278 lines: 5.9 MB). `run_case` (90 lines) is
  25,538 instructions: 9,894 calls, of which slot-call/GC protocol dominates
  (`pcc_gc_frame_leave_lifo` 1304, `pcc_slot_call_status_report` 897,
  `pcc_gc_store_root` 895, lease acquire/release 1017, exception frame source
  430) while the Python operations themselves are tens (`py_obj_subscript`
  40, `py_list_append` 40); 6135 bitcasts and 4365 blocks. Locally (M-series,
  wheel pcc1 from `012ec3265`) the frontend finished in ~1 min and the emit
  lane ran > 5 min: emit samples show 58% in the assembly text round trip
  (`AArch64ModuleBuilder._append_line` 35%, `_finalize_text_entries` 20%),
  and shards >= 2 MB (single huge functions) run in the width-2 oversized lane.
  That whole local compile took 884 s wall / 1249 s user and the output runs.
- Native emit width: every compiled (pcc1) emit worker lane was fixed at
  `COMPILED_NATIVE_SAFE_JOBS = 2` regardless of cores or budget, set in
  August when one compiled worker could take several GiB. Replayed shards with
  the `012ec3265` pcc1 (`pcc1 --pcc-self-backend-emit-batch-worker`,
  `/usr/bin/time -l`): 0.82 MB 10.5 s / 149 MB, 0.99 MB 12.7 s / 160 MB,
  1.86 MB 24.2 s / 197 MB, 2.2 MB 27.5 s / 218 MB, 7.3 MB 112 s / 593 MB
  (the 0.82/1.86 MB shards are from the Stage2 module
  `pcc.frontends.python.codegen.call_object_lowering`). That is ~80 MB plus
  <= 0.1 GB per input MB, about a tenth of the 1.3-1.4 GB/MB the oversized
  lane's 7 MB byte cap was calibrated on, and ~78 KB of IR per worker-second.
  `0b8875c79` admits sub-threshold compiled emit workers at 512 MiB each under
  a declared `PCC_WORKER_TREE_BUDGET_BYTES` (after the resident coordinator,
  capped by the CPU width); without a budget the fixed two stays. At ~830
  CPU-s of emit for this program (local speed; ~1.7x more on the aarch64
  runner) a 4-wide lane still cannot fit the 300 s gate by itself.
  Local Stage1 of that change (pre-rebase `804a39ce9` on `28ff7ca97`): rc=0
  in 1992 s. Same pcc1, same fixture compile, byte-identical output: no
  budget 826.2 s, 16 GiB budget
  (`scripts/run_process_tree_sample.py --max-tree-rss-bytes 17179869184`)
  623.7 s, peak tree RSS 1.75 GB. In the budgeted run the frontend took
  ~72 s, the coordinator ~55 s alone, the oversized lane ~256 s with one
  worker, the safe lane 85 s eight-wide, and the link ~150 s; frontend lanes
  also read the budget, so not all of the 202 s is the emit change.
- `739492c54` stops emitting identity pointer casts (`bitcast ptr %x to
  ptr`): they were 148,767 of 722,369 instructions (20.6%) and 19.2% of the
  65 MB of IR in that compile, and the x86_64 backend lowered each to a slot
  load/store. `59cf47f2b` widens oversized compiled emit waves under a
  declared budget (128 MiB plus 128 bytes per input byte per worker). Local
  Stage1 of both (pre-rebase `5fde2ac2d` on `804a39ce9`): rc=0 in 1424 s,
  user 4914 s (previous Stage1 1992 s / 6926 s, partly overlapping other
  local load), pcc1 1,560,845,496 bytes (-7.5%). That pcc1 compiles the
  class/exception/big-int program with no flags in 73 s; it matches CPython
  under GC0-GC4 and links only libSystem. The 16-module fixture under the
  16 GiB budget: 410.1 s (was 623.7 s), peak tree RSS 1.35 GiB: frontend
  61 s, coordinator alone 46 s, oversized lane ~80 s three-wide, safe lane
  ~70 s eight-wide, link ~137 s.
- Tests already red before the identity-cast change (snapshot of
  `804a39ce9` = `28ff7ca97` plus the emit-width config, local CPython
  3.15.0rc1): `tests/python/test_class_definition_defaults.py`
  9/18, `test_py_typed_int_unboxed.py` 7/47,
  `test_virtual_thread_spawn_slot_ownership.py` 27/36,
  `test_py_multi_file_compile.py` 5/46,
  `test_globals_owned_result.py::test_globals_owned_result_ir` (both targets),
  and three native executions in `test_virtual_thread_park_effect.py` where a
  may-park method chain prints `None` instead of `42` (a semantic bug).
  Also red before it (snapshot of `0b8875c79`):
  `test_raw_scaffold_exact_container_subscript_ownership.py` 18/19,
  `test_py_class_export_schema.py` (method registration and cross-module
  schema), `test_generator_result_lexical_root.py` (cleanup predecessors 2 vs
  >= 4), `test_callback_sync_context.py::test_callable_roles_in_actual_threaded_ir`
  ("no matching callable constructor"),
  `test_class_attribute_owner_lifetime.py::test_scalar_class_read_keeps_owner_until_conversion`,
  `test_owned_ifexpr_local_transfer.py::test_owned_ifexpr_ir_normalizes_branches_and_tracks_target`.
- pcc1 under `PCC_GC_BACKEND=4`: the `012ec3265` wheel pcc1 compiling the
  IfExpr canary from `test_owned_ifexpr_local_transfer.py` fails in 5 s with
  "'dict' object has no attribute 'values'"; GC0 compiles in 73 s and GC3 in
  242 s. The current pcc1 GC4 case exceeds that test's 300 s compile budget.
  The `5fde2ac2d` pcc1 under GC4 did not finish that compile in 1200 s.
  `sample` put 77% of it in `pcc_gc_backend4_sweep_deferred_recycles` and
  15.6% in `pcc_gc_backend4_evacuation_page_find` (a linear walk of the
  evacuation list). An lldb walk of the live page list (scratchpad
  `r12/gc4/walk.py`) read the deferred counter at 28 then 287 within 30 s,
  equal to the flagged pages, every one still carrying forwarding entries
  (`page+96 > 0`) and none completable, over 6834-7334 live pages: the sweep
  could never reach zero and walked the whole list on every top-level
  cascade. `7f7f2cd5d` counts only pages the sweep can complete (flag 1) and
  leaves forwarding-blocked pages (flag 2) to forwarding retirement. With
  that runtime, host-compiled programs match CPython under GC0-GC4 and the
  GC4/relocation test files pass apart from tests already red before it
  (`test_freestanding_gc_state.py` global count 201 vs 200,
  `test_gc_backend4_production.py` quarantine source contract). Local Stage1
  with it: rc=0 in 1478 s. Its pcc1 still does not finish the GC4 IfExpr
  compile in 1200 s (it reached the Mach-O link this time); the sweep is gone
  from the profile and the time is now `pcc_gc_backend4_evacuation_page_find`
  59.6% and `pcc_gc_backend4_retarget_mutator_payload_locked` 25.2%.
- Open GC4 issue, measured in that run with lldb (scratchpad `r13/lists.py`,
  `r13/evac.py`): the evacuation page list held 18,108 nodes (18,093
  distinct pages, so 15 duplicates; 266 of them no longer on the live page
  list; 5 live pages on it without `page+108`) against 56,566 live pages; the
  store buffer held 8,384 entries and the remembered set 18,837.
  `evacuation_page_find` walks that list on every allocation fast path, and
  each list/set payload growth walks the whole store buffer and remembered
  set (`pcc_gc_remembered_member_maybe(owner) == 0` could skip the latter).
  Stale nodes naming pages that left the live list are a correctness risk
  for evacuation, not only a cost; replacing the walk with the `page+108`
  flag needs that membership invariant fixed first.
- The next commit after `98c814750` keeps both walks' answers exact and
  skips them: `evacuation_page_find` reuses its single-threaded answer while
  `pcc_gc_backend4_reseed_page_revision` (bumped at every list push/unlink)
  is unchanged, and payload retarget skips both remembered walks when
  `pcc_gc_remembered_member_maybe(owner) == 0`. The GC state count pin is 204
  (the source already held 201 against its 200; three memo globals). With
  that runtime the five-GC programs match CPython and the GC4/relocation
  files pass except `test_gc_backend4_production.py::test_backend4_relocation_stress_stable_ids_and_no_old_addresses`
  (relocation set stuck at 8 for 16 rounds), which fails the same way on a
  snapshot of `0b8875c79`.
- Root cause of that stall (probe in scratchpad `r14/stress*.c`): the eight
  stuck members were empty lists whose copies all failed in
  `pcc_gc_relocation_payload_raw_validate_locked`, because
  `pcc_gc_backend4_zpage_payload_span_preflight_locked` required the 32-byte
  payload to fit after the copy target's page tail, and each retry allocated
  the target into the same nearly full page. The set never emptied, so
  `pcc_gc_backend4_remap_and_retire_stopped_world` never advanced the remap
  epoch and no forwarding retired - the same shape as the pcc1 GC4 compile's
  forwarding-blocked pages and growing evacuation list. The span-placement
  commit admits such copies and publishes their spans as external payload
  (offset -1), as `pcc_gc_backend4_zpage_register_owner_payload_span` does;
  the probe then forwards all 64, empties the set at round 0 and retires all
  forwardings by round 1 (remap epoch 2). `fbd569dee` is that change;
  `6f1e72f76` the find memo and retarget skip. Local Stage1 at `fbd569dee`:
  rc=0 in 1487 s; its pcc1 compiles the class/exception program at GC0 in
  74 s and the output matches CPython under GC0-GC4. Per-node comparison of
  the 162 `test_gc_backend4_production.py` nodes against a `0b8875c79`
  snapshot: identical except the stress test (now passing); 6 fail on both.
- The `fbd569dee` pcc1 still did not finish the GC4 IfExpr compile in 1200 s
  (on a host also running the default suite). A sample at 5 min showed the
  previous hot spots gone and `_backend4_store_buffer_remove` at 60.8%
  (every object free walks both store-buffer queues) plus
  `_backend4_store_buffer_owner_count` at 21.7% (a quadratic distinct-owner
  count on every enqueue, for high-water telemetry). The next change keeps
  an exact owner index for store-buffer entries
  (`pcc_gc_store_buffer_member_*` in `freestanding_gc_index_table.py`):
  frees skip owners it proves absent and the distinct count is the index
  size; a failed index allocation or a dropped medium-flush entry taints the
  epoch, which falls back to the walks until the queues are cleared. With it
  the five-GC programs match CPython and the 162 GC4 production nodes match
  the previous run (same 6 failing on both); `tests/python/test_gc_store_buffer_membership_native.py`
  covers the index (`eb3af24c1`). Local Stage1 and the pcc1 GC4 timing are
  not yet rerun.
- vthread may-park methods: `self.leaf(v) + 1` inside a may-park method
  resumed to `None` (the 42-vs-None failures above). The consumer's output
  slot made `_emit_method_call` require runtime binding, and that dynamic
  call handed the consumer the continuation object instead of driving it.
  Local and external may-park methods in a resumable caller now take the
  static path, whose delegation publishes into the consumer's slot.
  `test_concrete_may_park_method_chain_resumes_to_python_result` passes
  (`160340f19`) and
  the program prints 42 under GC0-GC4. The two cross-module variants still
  print `None`/`41`: the imported class is bound at run time, so the call is
  fully dynamic; fixing that needs a run-time continuation check on dynamic
  calls in resumable callers (as `pcc.virtual_thread.call` does). Among the
  35 vthread/generator test files the remaining failures are red on the
  HEAD snapshot too, including `test_virtual_thread_frontend.py` join
  ("slot-call Call lacks an immediate owned-result handoff").
- Publish workflow dispatch 39099696332 on `db7c8e812`: the macOS job
  failed at once because `scripts/ci_worker_size_prior_gate.py` requires
  the platform baseline Python (3.15.0rc1 on macOS) and the job passed
  `--python 3.13`; the matrix now selects it per platform.
- Publish workflow dispatch 39101937576 on `52bdc0917`: all four wheel jobs
  passed (Stage1, wheel, `ci_native_wheel_gate.py` on the runner and in
  Docker): macos-arm64 72 min, linux-aarch64 53 min, linux-x86_64 102 min,
  windows-x86_64 130 min. The sdist job failed: hatch ran the build hook for
  the sdist target, which built the runtime and pcc1 and hit the 10-minute
  self-compile limit. `f4f06c9a9` returns early for `target_name == "sdist"`
  (`tests/test_wheel_tag.py::test_sdist_target_builds_neither_runtime_nor_pcc1`;
  local `uv build --sdist`: 13.5 MB in 24 s). Not yet re-run in CI.
- Qualification run 39101921646 on `52bdc0917`:
  - macOS arm64 `--phase pcc1`: `test_size_prior_native_processes_five_collectors[pcc1]`
    hit the 300 s guard (status TIMEOUT, rc -15; the largest process was an
    oversized emit worker at 193 s). Same open gap as above.
  - Linux aarch64 five-collector canary: pcc1 compiling the GC4 canary
    finished `artifact_link` (223.5 s) and `artifact_cleanup`, then died
    with SIGSEGV; GC0-GC3 compiled and ran. In the publish run at the same
    commit, the same `--phase stage1` command passed the GC4 canary (link
    224.8 s). So the GC4 crash is intermittent and comes at the end of a
    completed compile. Not reproduced locally.
  - Windows preflight: `test_windows_tar_attributes.py` (0xC0000005, empty
    stdout), the same failure as run 39082284048 on `6f3f42bc6` (before
    `739492c54`). The Windows worker-prior job has failed in every completed
    run since 2026-10-16.
- Mac -> Linux x86_64 cross-compile at `f4f06c9a9`:
  - Runtime: `python -m pcc.frontends.python.owned_runtime_build --target
    x86_64-unknown-linux-gnu`.
  - A two-module program built with `--target x86_64-unknown-linux-gnu`
    prints 42 on a real x86_64 Linux host under GC0-GC4.
  - The cross-built pcc1 is a 1.74 GB static ELF that took 2004 s to build
    locally. On that host it runs `--help`. With no host Python or compiler it
    compiled a one-function program in 190 s, and that program prints 42
    under GC0-GC4.
  - A pcc1 outside a wheel needs `PCC_SOURCE_ROOT` naming a tree with
    `pcc/`, `scripts/bootstrap.py` and `scripts/pcc_link_macho.py`. Those
    two scripts are inputs to the codegen checksum (Mach-O linker identity
    included even for Linux targets). Without them the only diagnostic is
    "current codegen checksum is unavailable", because diagnostics
    deliberately omit paths.
- Default-suite harness bug: `test_bootstrap_performance_manifest.py::test_bootstrap_stage_result_declares_metric_scope_and_pairing_contract`
  ran `run_stage` with the default out dir. That overwrote the checkout's
  `build/bootstrap/pcc1` with the 12-byte text `stage binary`, and every later
  pcc1-gated test in that checkout then failed with "Exec format error".
  The test now uses `tmp_path`.
- Executable size: the precise stack-map table, not code, is most of pcc1.
  - Local macOS Stage1 pcc1 (`stage1_gc4c`, 1,560,831,224 bytes): `__TEXT`
    430 MB, `__pcc_stackmaps` 1,074,289,080 bytes (69%), other data 17 MB.
  - Cross-built x86_64 ELF pcc1 (1,744,596,576 bytes): a 653 MB executable
    segment and a 1,086,307,936-byte read-only segment that holds only
    `.pcc_stackmaps` (`elf_x86_64.py` places it there).
  - Inside the macOS table (scratchpad `smap/parse.py`): 32,326 functions;
    18,776,484 safepoint records of 32 bytes each, 601 MB (13.8M call, 4.2M
    loop, 0.7M exception, 36 continuation); 29,525,446 interned location
    entries of 16 bytes each, 472 MB, holding only 7,716 distinct values.
  - The location slices are interned per object and then concatenated at
    link. The 735,916 slices are already distinct by content (average 40
    entries), so link-time dedup cannot shrink them. The cost is the
    encoding: a per-function slot table plus one bitmask per distinct live
    set, with varint records, would describe the same roots in about a tenth
    of the space (estimate, not built).
- No production code reads that table. `pcc_gc_consume_precise_stackmap` has
  no caller outside its definition and header declaration. Its docstring and
  `self_backend_precise_stackmaps.py` both state that the registered-slot
  protocol is authoritative. Project intent still names stack maps as part
  of the target runtime.
  - Change: objects carry the table only under
    `PCC_PRECISE_STACKMAP_TABLES=1`. Safepoint planning still runs, so the
    emitted code is identical: assembly differs only by the section on
    aarch64-darwin, aarch64-linux and x86_64-linux.
  - The setting is part of the object-cache identity.
  - `tests/python/test_precise_stackmap_table_policy.py` covers it. The
    default and opt-in executables match CPython under GC0-GC4, and only the
    opt-in one contains the `PCCSMAP1` magic.
  - The runtime archive built at this source is 19.8 MB (macOS).
  - Local macOS Stage1 at this change (`scripts/bootstrap.py --stage 1`, git
    snapshot): rc=0 in 1898 s. pcc1 is 478,153,704 bytes, down from
    1,560,831,224. `__TEXT` is still 430.7 MB, `__DATA` 17.1 MB, and there is
    no stack-map section. gzip -1 gives 158 MB, down from 416 MB. With no
    flags, that pcc1 compiled a class/exception/big-int program in 66.6 s;
    the output links only libSystem and matches CPython under GC0-GC4.
    Stage2/Stage3 were not run.
  - Before precise roots become the production walk, the compact encoding
    and pruning of safepoint records for calls that cannot collect are still
    needed.
- `50b8b0979` (remote agent) fixes a defect in the table opt-in: the skipped
  `build_aarch64_stack_map_section` was also where AArch64 packed plans were
  closed, so they were never retired when tables were off. The fix adds
  `tests/python/test_aarch64_stackmap_plan_ownership.py`.
- Module initialisers are 103 MB of the 430 MB pcc1 text: `module_top` is
  73.3 MB across 557 modules and `module_init` 29.7 MB across 371. Symbol
  sizes come from the Stage1 pcc1 `nm` map (scratchpad `smap/textsize.py`).
  - A module-level `def f(a, b=1)` with a docstring cost 373 IR instructions
    (289 calls) in its initialiser. The cause is the five-tuple signature:
    four `py_tuple_new`, a `py_tuple_set_item` per field, and the
    pin/lease/status protocol around each.
  - `8b8b41c0b` builds a signature whose defaults are all str, small int,
    None or bool from one read-only descriptor plus
    `py_static_aggregate_build`. This gives 254 instructions (194 calls) per
    def. `py_static_aggregate_build` gains None/False/True leaves (markers
    10/12/14).
  - The macOS runtime archive drops from 19.8 MB to 17.2 MB.
  - `tests/python/test_static_function_signature.py` matches CPython on
    GC0-GC4. The failing nodes of `test_class_definition_defaults.py` are the
    same nine as on the HEAD snapshot.
  - The remaining ~112 calls per def are nearly all slot-call protocol around
    captures, construction, metadata and `__doc__`.
  - Local macOS Stage1 at `8b8b41c0b`: rc=0 in 2381 s. pcc1 is 458,587,352
    bytes (was 478,153,704); `__TEXT` is 407.1 MB (was 430.7 MB); gzip -1
    gives 150 MB. With no flags it compiles the class/exception/big-int
    program in 60.4 s (was 66.6 s); the output links only libSystem and
    matches CPython under GC0-GC4.
  - Local macOS Stage1 at `99f696d50` (complete git snapshot, runtime built
    inside it): rc=0 in 2041 s. pcc1 is 458,780,328 bytes. It compiles the
    same program in 67.6 s under default-suite load, and GC0-GC4 match
    CPython. The default suite is running against this pcc1 and an archive
    from the same snapshot, with tests refreshed to `e190cc4f3`. `pcc/` and
    the checksum scripts are unchanged since `99f696d50`.
- Default-suite reds fixed on 2026-10-18 (each compared with a snapshot of
  the pre-change commit first):
  - A real GC-safety gap. A fully supplied keyword call of a method with
    definition defaults went through `py_obj_call_method_kwargs`, with an
    unrooted callable, args tuple and kwargs dict. `1cfcf6f98` calls it
    directly when `_statically_bindable_call` holds.
  - Tests left stale by refactors:
    - `test_class_definition_defaults` (9 nodes): function objects now come
      from `py_func_new_signature_slots`, and inherited defaults are copied
      into rooted slots. The error edges are now checked by walking the CFG.
    - divmod owner release.
    - six identity-cast IR shapes.
    - `fcntl` and `pcc_errno_get` added to the stdio boundary pins.
  - Platform assumptions:
    - The APFS surrogate `mkdir` case now expects the same `EILSEQ` as
      CPython.
    - Stage1 checkpoint tests that call `build_identity` or hash the real
      interpreter are gated to x86_64 Linux. The route rules run everywhere
      through a modeled host, and so do the platform producer/consumer tests.
    - The guard CPU-accounting test now gives its worker a large enough CPU
      margin over the guard and launcher overhead.
  - Harness:
    - A test overwrote the checkout's `build/bootstrap/pcc1`.
    - Manifest evidence now has the same schema on every platform.
    - The `mkdir` invalid-integer case no longer uses `pytest.skip`.
  - Still red and understood:
    - `test_layer1_constants_cross_module_static_imports_stay_native`: the
      live-import item below.
    - The per-module fallback ratchet: seven ceilings are stale. The increases
      are cross-module `from pcc... import` and pcc-native stdlib imports,
      and they need the documented recapture once the multi-file totals are
      confirmed 0.
    - 30 `pytest.skip` calls remain in tests.
- Snapshot suite runs: a runtime archive built in the worktree does not
  validate in a snapshot. `codegen_checksum` hashes every
  `.py`/`.c`/`.h`/`.sh` file under `pcc/`, including untracked ones (the
  worktree has `pcc/ir_passes` and `pcc/runtime/_native`). Snapshots also
  need `AGENTS.md`, `run.py` and `UV_NO_SYNC=1`. Without the last, an inner
  `uv run` re-pointed the shared venv's editable install at the snapshot.
- Imported names are always live module lookups. In every managed
  (non-port) module, `_emit_name` checks `live_import_name_slot` before the
  native constant and export bindings. So each read of an imported function,
  class or constant is a `py_module_attr_get` wrapped in the slot-call
  protocol. This is the same cost as the 17x imported-constructor gap above.
  - `test_fallback_baseline.py::test_layer1_constants_cross_module_static_imports_stay_native`
    has been red since `983254526` for this reason: the class attribute
    initializer reads `IR_RUNTIME_COMPAT_MODULE` dynamically instead of
    storing the static `"pcc.ir.compat"`.
  - The live binding is needed in general, because another module may write
    `a.X`. Binding statically needs a closed-world proof that:
    - the importer never rebinds the name (assignment, `global`, `del` or
      star import);
    - no module writes it (attribute store, `setattr` or `globals()`).
  - Not changed yet.
- `cli_bootstrap` had 340 `py_cpy_*` calls in its standalone probe
  (`test_cli_bootstrap_package_schema_static_imports_stay_native`) from the
  function-local `pcc.package.build_exec` / `build_plan` imports. Static
  exports matching the closed-world exports field for field bring it to 0:
  return and parameter types, keyword-only kinds and `box_int_abi`.
  `_function_export` gains a `box_int_abi` argument.
- `L1CodeGen._initial_ir_available` (from `88cb6a02c`) is now in the host
  contract (`test_l1_codegen_host_contract_covers_constructor_state_fields`).
  `test_bootstrap_trace_filter.py` had a prefix mangled by the layout rename.
- Runtime archive builds now run in parallel. The owned build compiled its 181
  modules serially in one process (184 s at 97% of one core).
  - Workers (`--pcc-runtime-module-worker`, through the shared compiler worker
    pool) compile batches, largest sources first. The parent assembles
    members in canonical order. `PCC_RUNTIME_BUILD_JOBS` defaults to up to
    eight.
  - `tests/test_runtime_build_parallel.py` checks that the archive,
    `.capi_syms` and provenance are byte-identical to the serial build, and
    that a failed worker publishes nothing.
  - Full macOS runtime at `eb5ee441f` with the default width: 44.0 s wall at
    597% CPU, under load average 17-30. The serial build took 184 s. All 181
    objects are byte-identical to the serial build of the same compiler
    output.
  - Tests that patch builder internals in the parent pin
    `PCC_RUNTIME_BUILD_JOBS=1`, because workers cannot see the patches:
    `test_owned_runtime_capi_inventory.py`, `test_owned_runtime_selection.py`
    and `test_wheel_owned_runtime.py`. The remote agent did the same for
    `test_owned_runtime_pass_prefix.py` in `724ba09d9`.
- Precise stack-map table, compact encoding estimate over the real 1.07 GB
  table (scratchpad `smap/v3estimate.py`): 143 MB (7.5x). That splits into
  per-function slot tables (2.9 MB), per-function deduplicated live-set
  bitmaps (75 MB; the largest function has 6,093 root slots) and varint
  records (65 MB).
  - The ELF linker does not merge tables. It lays each object's table back
    to back in the metadata segment, so a compact ELF image needs
    `elf_x86_64.py` (SHA-pinned) changes.
- Six IR-shape tests still assumed the identity casts that `739492c54` removed.
  Each failed on HEAD, passed on a snapshot of its parent, and was rewritten
  to accept the direct operand or an alias. Every rewritten assertion was run
  against both the old and the new IR:
  - `test_set_algebra_receiver_ownership.py`
  - `test_ir_scaffold_variadic.py`
  - `test_gc_moving_safepoint_owners.py`
  - `test_super_call_result_roots.py`
  - `test_slot_call_exception_cleanup.py` (used by
    `test_native_container_builtin_error_paths.py`)
  - `test_freestanding_gc_external_resource.py`
  In the last one, a Darwin-only lookup that is a known-null callee on Linux
  used to be called through a `bitcast ptr null`. It is now a direct `call
  null`, which the self backend routes to the runtime's
  `pcc_null_callee_trap`.
- Release publication (`.github/workflows/workflow.yml`, runs on a published
  release in `allstoalls/pcc`, or `workflow_dispatch` to build without
  publishing): sdist plus one `py3-none` wheel per platform (macos-15 arm64,
  ubuntu x86_64/aarch64, windows-2022). Each wheel job builds its own Stage1
  through `ci_worker_size_prior_gate.py --phase stage1`, hands the recorded
  pcc1 to hatch with `scripts/ci_stage1_compiler.py` (receipt PASS, same clean
  commit and source hash, same binary hash), builds the wheel and runs
  `ci_native_wheel_gate.py` on the runner and, for Linux/Windows, inside
  Docker. A failing platform uploads no wheel; publishing needs the sdist and
  at least one wheel. The pcc1 prior-worker gate, suites, five-GC bootstrap
  and the no-libpython audit stay in the qualification workflow and local
  gates. Before this, the Linux/Windows qualification wheel step had no
  `PCC_BUILD_PCC1` and would have re-run hatch's 10-minute self-compile.
- The package version is 0.1.8 (`pyproject.toml`, `uv.lock`; `uv lock
  --check --offline` passes, installed metadata reads 0.1.8). The resync
  removed an undeclared `llvmlite` from the local `.venv`; nothing in the
  lock or the project depends on it.

- macOS Stage1 at `169bcfca1` (CI run 39031828792, artifact
  `bootstrap-macos-arm64`): all codegen finished (3574 s, peak tree RSS
  2.54 GB under the 2.77 GB cap) and the owned Mach-O link reached `fixups`,
  then failed: `_build_seg_info` packed `page_count` > 65535 into a uint16
  (`__text` span 529.6 MiB; `__DATA` file extent > 1 GiB of 16 KiB pages).
  `10be0ed89` bounds the starts table at the last fixup page and moves TLV
  descriptors ahead of DATA. The fixups that remain are the per-function
  addresses in `__pcc_stackmaps` (the pointer-free location table follows
  them), so whether the last one sits below 1 GiB is decided only by a real
  link; the CI run on `e0e6faa81` covers it. A local Stage1 was not run.
- Nested decorated defs: the hoist pass now evaluates decorators whose root
  is an enclosing local or a module-defined (non-imported) binding in the
  enclosing scope, before the defaults, and applies them each time the def
  executes. Before, a local decorator was refused ("Layer 1 does not handle
  decorators") and a module decorator on a nested def never ran. Imported
  decorators keep declaration-time handling. Test:
  `tests/python/test_decorator_application.py::test_nested_decorators_apply_where_the_def_executes`
  (pcc0, GC0-GC4). `constructor_expression_failures` now compiles.
- Type joins: `try` now joins every type that can reach the code after it
  (normal completion; for each handler that falls through, the pre-`try` or
  any partial-body binding unless the handler rebinds; `finally` replaces
  all), and an `if` arm counts a nested `def` as a binding. Before,
  `x = 37` then `try: x = some_function` then `x == 37` failed with "cannot
  coerce FuncType to int". On pcc's own modules this widens 21 locals to dyn
  (scan: `_typeconf_widen_try_join` retargets, 34 events), none in
  `pcc/runtime/`, all on error/observer/config paths or where the old type
  was wrong (`c_codegen.py:3979` `dim`). Test:
  `tests/python/test_try_rebinding_type_join.py` (pcc0, GC0-GC4).
  `constructor_exception_order` and `constructor_default_exception` compile.
  Still open: a decorated nested def that calls itself reaches the
  undecorated function directly.
- The Windows tar native cases (0xC0000005, empty stdout) crash before
  `main()`'s first flushed print. The same generated program compiled on
  macOS extracts and runs to its Windows-specific controls, so the crash is
  in Windows-only module initialization; no Windows host was available here.
- Stale pins updated (each shape re-checked first): function objects are built
  by `py_func_new_signature_slots` since `d0b8b6720` (one construction per
  callable, five tests); the `cell.payload` read publishes into a slot-call
  root and hands off no owner; `mem2reg` moved to `pcc/ir/optimization/` and
  has no nested functions; the sibling-cycle program now captures the later
  sibling's binding (matches CPython natively on GC0-GC4); the capture fixed
  point settles in one pass, so `referenced_sibling_names` runs once per
  nested function. `_module_bound_names` (from `8c7f55e4c`) read handler
  `.name`/`.body` directly; it now uses the stage-safe field accessor.
- `--python-libpython auto` only (the default `off` compiles it): a nested
  function that returns an arithmetic result of calling a captured callable,
  `def outer(f): def wrapped(x): return f(x) * 2`, fails with "slot-call Call
  lacks an immediate owned-result handoff". The captured `f` is a cell read,
  and the libpython-compat call path does not publish into the slot-call sink.
  This is what `test_same_module_bare_decorator_still_applies` hits.
- Still failing on a clean `1694b049f` snapshot:
  `test_hoist_global_cell_boundaries.py::test_host_ir_global_reads_do_not_become_capture_values`
  (both modes), the `_hoist_nested_funcdefs` length check (1810 lines there,
  1833 now, against 1800), `test_py_function_decorators.py::test_same_module_bare_decorator_still_applies`
  ("slot-call Call lacks an immediate owned-result handoff") and
  `::test_imported_bare_decorator_is_metadata_noop` (`singledispatch` refused;
  pcc's native `functools` has no `singledispatch`).
- Starred unpack: a captured `*rest` arrives as its cell subscript and an
  attribute `*obj.tail` was refused ("starred unpack target must be a bare
  name"); both now store like the other unpack targets
  (`test_py_native_closure.py`, the existing captured case plus
  `test_native_starred_unpack_into_captured_and_attribute_targets`, GC0-GC4).
- `test_py_nested_hoist.py::test_nested_hoist_caches_reject_reused_object_id`
  patched `id()` in `hoist_lowering` only, while `hoist_boxing` keyed
  `boxed_function_defs` with the real `id()`, so no def was found to bind a
  cell. The table is now an identity list; with or without the patch the
  probe's IR is identical to the unpatched IR.
- Linux aarch64 and x86_64 at `169bcfca1` both built Stage1 pcc1 and then
  timed out in `ci_worker_size_prior_gate.py --phase pcc1` (300 s): pcc1
  compiling `tests/fixtures/native/worker_size_priors.py` spent 291 s in one
  `--pcc-self-backend-emit-batch-worker ... emit_oversized_0.manifest`
  process (621 MB RSS) on aarch64 (artifact `bootstrap-linux-aarch64`,
  `worker-prior-gate/native-pcc1/result.json`).
- `pip install pcc` installs an unrelated PyPI project ("Predicate collection
  classes"); this distribution is `python-cc`, whose 0.1.7 has no wheels.

## Generated portable-runtime compiler integration

Source baseline `a1da79229ed5a6f934b544f36d4d908eecb05dd2` plus this
milestone. Recovery index (frozen sources, patches, native binaries, command/mode
and runtime receipts):
`/Users/jiamo/pcc-recovery-archive/2026-10-17/portable-runtime/index.json`.

- Temporary `str(...).zfill(width)` results enter the existing output-slot
  protocol before retiring input roots. Tuple-typed stores use the native
  assignment protocol, preserving catchable errors and evaluation/finally order.
  Private module-comprehension unpack slots are registered before publication.
- Public `sys.setrecursionlimit` direct/alias calls use the existing native
  setter with rooted `__index__` conversion and exception checks. The stdlib
  facade exposes the same API; the no-libpython guard remains enabled.
- Native source-hosted self-backend/no-libpython programs match CPython for
  padding/callback order, tuple errors/finally, private comprehension names and
  recursion setter errors/restoration. Fifteen executions at GC0-GC4 pass.
  An unchanged TAG/T2-generated UUID Python program also executes natively;
  dynamic linkage is libSystem only. UUID payloads are compared by format,
  version/variant and count, not unpredictable byte equality.
- The earlier 120-second executable timeouts interrupted cold owned-runtime
  construction (181 modules). A task orchestrator reused only receipt-verified
  objects and completed the same production compile/optimization/emission and
  archive checks. A compiler-identity change correctly rebuilt the objects.
  Production runtime-build checkpoint reuse is still separate unfinished work.
- Compiler ownership for these runs is **host pcc0 frontend, owned self backend,
  libpython off**. The historical installed pcc1 baseline was not replaced.
  New-source pcc1 bootstrap/promotion, full translated Harness/zdb, all Node APIs
  and release qualification remain open; no fixed point or performance claim.

## Harness compiler integration (local source delta)

Baseline `57a64e8735a53dae2782199bfc03d56d3ae75123` plus this source milestone. Frozen sources, patch, actual logs, runtime and native receipts:
`/Users/jiamo/pcc-recovery-archive/2026-10-15/harness-project-closure/index.json`.

- Package/multi-entry collection retains eager cross-package ABI providers with
  stable entry search precedence and bounded optional/lexical/f-string discovery.
  Importlib provider siblings use relative imports under native public names.
  The test runtime cache binds the production compiler checksum.
- Fifty focused cases pass, including two actual linked native inputs at
  GC0–4, five existing native f-string cases, host importlib behavior and cache
  identity checks. One broader integration case remains deselected. Compilation
  uses current host pcc0, self backend and libpython off; this is not a new-source
  native pcc1 bootstrap or promotion claim.
- The unchanged full `/Users/jiamo/my/harness/harness/__main__.py` still refuses
  `raw pointer cannot be a slot-call operand` in `_run_gui`, `gui_app.py:483`.
  No full Harness executable was produced. Keep the existing managed/raw-pointer
  boundary; resolve general typed-call/adapter support without hand-patching
  Harness. An optional `pcc inspect` of the formatted native fixture timed out
  at 20 seconds; OS linkage audit of both new fixtures reports libSystem only.
- Harness is the existing manual Python port and a compile/behavior reference.
  Upstream-to-T2 generated Harness, current-source pcc1 qualification, full GUI
  and complete-product acceptance remain open in the translation goal.
  Application/GUI source bytes are unchanged; owned-process audit is empty.

## 2026-10-15 cloud continuation

- `1c1905` preserves dict/list/set copy dispatch with owned method-result roots.
  Three native pytest nodes passed using a source-matched Linux x64 single-thread
  owned runtime: dict copy/pop under requested GC0-GC4 (10 executions), plus
  the 48-candidate GC4 page-selection case. No full Stage1 or performance claim.
- CI now includes both dict pcc0 nodes in the existing 300-second admitted-runtime gate.
- Windows PE double-charge fix `b1db` awaits CI. macOS `ce73` only fixes input
  lifetime; the initial 3.71 GB stack-map rejection remains open.
- Cloud evidence: `/workspace/shared/pcc-resume-native-20261014/native-v2` (outside the repository).

## 2026-10-15 evening

- `c0e17b72`: the native link-lifetime test's stubs take the linker's
  `archive_selections` keyword; the stubs were stale, not the linker
  (`test_native_object_fastpath.py` natively[pcc0]).
- `495837cb`: a dynamically called module export unboxes to its inferred
  scalar type (`test_native_module_alias_call_does_not_bind_unrelated_class_method`).
- `8c7f55e4`: classes nested in a class body are declared at module level
  ahead of their outer class and bound as its attribute, with CPython's
  `__name__`/`__qualname__`; `tests/python/test_nested_class_definition.py`
  passes natively under GC0-GC4.
- `70f0e38c`: keyword and default calls to known callees bind statically
  when exact: keywords in formal order, omitted defaults None/bool/tagged
  small int. Reordered keywords, str/bytes/tuple/heap-int defaults, `**kwargs`
  callees and every TypeError shape keep the binder. Shape tests that reached
  the binder through a keyword call now use a shape that still needs it; the
  slot-call consumers in 98 test files (`take`, `consume`, ...) gained
  `**extras` for that reason; `test_binder_contract_matrix` pins
  `required_mixed` as a direct call and `reordered_evaluation` as the binder.
  Native GC0-GC4: `test_static_keyword_binding.py` (now with a conditional
  keyword operand), the dynamic-append binder program, the `required_mixed`
  binder-matrix cells. Host: 101 of the 102 affected files on a snapshot of
  these changes (failures there are the pre-existing ones below, plus pcc1
  cells without a provisioned pcc1); `test_valueclass_aggregate_ownership_native.py`
  (only `**extras` added) was stopped unfinished.
- `b085f161`: decorators run once at definition (a call loads the published
  binding; each call used to re-apply them) and a call to a module function
  is accepted as a decorator factory. Its last edit (decorator expressions
  evaluated top to bottom before they are applied) has not run natively yet:
  `tests/python/test_decorator_application.py` with a rebuilt runtime.
- Open, found here: a dynamic `.append()` result passed to a direct call keeps
  one extra reference (positional calls too, so pre-existing; the binder path
  is correct). Repro: `DIRECT_PROGRAM` in
  `tests/python/test_slot_call_dynamic_append_result.py`, pcc0, GC0. The
  no-sink case of `_emit_dyn_list_method_with_runtime_guard` returns a raw PHI
  of a borrowed None and an owned method result, and
  `_emit_generic_dyn_method_call_on_value` never releases the bound method
  from `py_obj_getattr`. Fixed in the commit after `88036dc2` for the list
  route (append takes a result root as `list.extend` does, the other names'
  join is recorded as owned, the method is released): the append cases of
  `tests/python/test_dynamic_method_result_ownership.py` matched CPython on
  GC0; GC1-GC4 and the `direct` variant of
  `test_dynamic_append_owned_result_and_error_native_five_gc` have not run.
  Still open: names the dict route claims first (`copy`, `pop`, ...) leak a
  user method's result the same way (`dyn.pop.result` PHI in
  `dict_lowering.py` is not recorded as owned).
- Pre-existing host failures: `test_lambda_constructor_roots.py`,
  `test_owned_os_descriptors.py` and `test_string_method_result_roots.py`
  are fixed in `17f1819a` (stale shapes; macOS ran the Linux-target model
  with host open flags). `test_bytes_decode_result_roots.py` passes again:
  its `BufferModel` lacked the `memcpy` that `py_bytes_new` uses since
  `bb610f00b`.
- Assigning `__defaults__`/`__kwdefaults__` does not affect calls, through the
  binder as well; the static default fill matches that, and both need a guard
  when the assignment becomes live.
- Python feature matrix (48 programs, pcc0 self backend, no libpython, GC0,
  against CPython, at `304f3926`): 19 equal, 20 differ, 9 fail to compile.
  Programs, runner and results: recovery archive `2026-10-13/featmatrix/`.
  Compile failures: async comprehensions, `except*`, `match`,
  `x = yield from`, lambda forms, class keywords/`__init_subclass__`,
  itertools ("slot-call name has no authoritative registered root"),
  decorator factories and nested classes (both addressed above). Differences:
  decorators re-applied per call, `functools.wraps`/`lru_cache`/`cache`
  compiled as no-ops, `__setattr__` overrides ignored, frozen dataclass
  assignment allowed, Enum, metaclass `super().__new__`, dict view set
  operations, frozenset dict keys, nested f-string format specs,
  `int('1_000')`, `math.isclose`, memoryview slicing, namedtuple `_replace`,
  `__kwdefaults__`, `__getitem__` iteration, non-ASCII case mapping, two
  programs whose `main` compiled as a no-libpython stub.

## 2026-10-15

Commits after `bc7f53a5` (this session): `bad776a3` builtin `type` as a value
(the Windows object-identity failure: `main` was a no-libpython stub) plus
the type-object call and copied class names; `27183c2e`/`ea678a14` user
exception subclasses keep BaseException args, str and repr; `ba9f5aa1`
exception tags as their own numeric domain; `329af921` stale GC4 source pin;
`8d9079d9` quiet successful Mach-O links (macOS empty-stderr tests);
`8fd1be9b` local class `__name__`/`__qualname__`; `110870ba` Mach-O link
retires inputs and source payloads once copied; `2d913a05` one PE stack-map
snapshot copy on CPython.

CI 38584467472 (`9cbcb1e9`) gave the refused link sizes: Windows "PE final
stack-map snapshot" raw=10945047087 retained=8343004237 limit=10201337856;
macOS "Mach-O input stack-map snapshot" raw=3711958786 limit=2485518336, where
the macOS tree cap is the runner's 3.36 GB reclaimable memory minus a 512 MB
reserve. `docs/investigations/stage1-link-memory-budget.md` has the analysis;
`110870ba` and `2d913a05` target those two checks.

Known on these commits:
- `test_classmethod_constructor_consumes_temporary_tuples[pcc0-...tuple]`
  fails under GC4 since `27183c2e`: layout-sensitive exposure of the GC4
  unretired-relocation retention (see the 2026-10-15 section of
  `docs/investigations/gc-backend4-forwarding-retirement-starvation.md`).
- `test_native_object_fastpath.py::
  test_private_link_plan_retires_graph_before_image_allocation_natively[pcc0]`
  failed on `bad776a3` and on these commits ("prepare_executable_object() got
  an unexpected keyword argument 'archive_selections'"): stale test stubs,
  fixed in `c0e17b72`.
- Still open from this session's probes:
  `test_dunder_dispatch_subclass_override` dynamic binary dunders,
  `test_class_definition_defaults` (fails on `bad776a3` too). Nested class
  attributes and the alias-call emit worker are fixed (evening section).
  `test_macho_exec_link`'s data-pointer test was stale since `50889ccd` added
  chained binds for imports in data pointers; it now checks that contract.

## 2026-10-14 afternoon

Between `fa19e4b1` and `ca703ae0` a parallel session pushed 24 commits:
Win64 globals beyond rel32 use ADDR64/SECREL (`b1c2c360`, the PE rel32
failure), merged and deduplicated PE stack maps (`fa5628cc`), oversized-PE
rejection (`69566173`), PE host budgets (`d6f50a7d`, `cc0a4bee`, `ca703ae0`),
GC4 forwarding-shell finalization and idle retirement (`b59142c8`), super-call
result roots (`4a9dbc69`), one `object` identity for builtin and class
predicates (`988bc697`, the isinstance(object) gap), CI Stage1 7200 s
(`0198e205`) and new CI regression steps.

CI at `ca703ae0` (run 38567599226; seven superseded runs were cancelled to
free runners, and this one after its Windows and macOS results; the next
run is 38584467472 for `9cbcb1e9`):
- Worker-prior gate passes on all four platforms (strict closure included);
  the macOS pcc0 jobs and Windows RSS publication pass.
- Linux native: Stage1 passes and the GC0-GC3 native canaries pass (the GC3
  lease fix `c5363d80` holds). The GC4 canary compile exceeds its 6000 s
  deadline: aarch64 GC0 326 s, GC1 747 s, GC2 880 s, GC3 845 s, GC4 > 6000 s
  (run 38530860670). See
  `docs/investigations/gc-backend4-relocation-set-free-walk.md`; `6a242b67`
  removes the per-free relocation-set walk (local probe 186 s -> 16.7 s);
  the page selector's whole-heap scan per selected page remains.
- Windows native: Stage1 compiles every module (codegen done at ~3044 s);
  the PE link stops at "compact stack-map byte budget exceeded: PE final
  stack-map snapshot". The object-identity native test from `988bc697`
  fails on Windows: `main` was compiled as a no-libpython stub
  ("no-libpython function unavailable: program.main").
- macOS native pcc1: Stage1 codegen completes (~4800 s); the Mach-O link stops
  at "compact stack-map byte budget exceeded: Mach-O input stack-map
  snapshot" with a 2.79 GB tree cap and the coordinator at 2.04 GB. `9cbcb1e9`
  adds the refused check's label, estimate, sizes and limit to the PE and
  Mach-O link errors, so the next run shows how far each estimate is over.

Also failing on the `ca703ae0` snapshot (pre-existing, seen during A/B):
`test_runtime_abi_chunking.py` (two chunk-size bounds, 70 and 60 > 50),
`test_freestanding_gc_state.py` (four: 201 state globals, and
`pcc_gc_foreign_lease_active` missing from the runtime ABI registry),
`test_gc_abstraction_surface.py::test_colored_relocating_gc_read_barrier_clears_candidate`,
eight `test_gc_backend4_production.py` cases,
`test_relocation_copy_clears_stale_sweep_verdict_backend4[pcc_python]`, and
`test_foreign_address_leases.py::test_native_gc3_gc4_foreign_address_movement_and_overlap`
(self-link rejects link arguments).

## Recent source changes

- `0f6fa1d9` function-local from-import aliases follow control flow.
- `bbe27219` merged Mach-O `__text` splits at input boundaries (32 MiB chunks)
  so AArch64 thunk islands interleave.
- `510ed001` compile-time-only from-imports (typing, abc, ...) no longer get a
  runtime module global; `TypeAliasType(...)` metadata no longer raises NameError.
- `e62cef29` raw i64 module globals updated through `global n; n += ...` store
  the unboxed value instead of rooting a boxed pointer in the i64 cell.
- `97f3d52f` `self.__class__(kw=..., **mapping)` keeps the parser's operand
  order, so the constructor-unpack path compiles it.
- `3a2530be` the freestanding-directive scan returns early without a marker and
  indexes a character list instead of the source str.
- `28ce3e92` restores the 6 s worker-prior fixture deadline (see decision 2).
- `1c980956` the ELF link reuses a relocatable `.pcc_stackmaps` table's
  validation verdict for its relocated copy (finalize 44.8 s -> 0.9 s on CI).
- `fbe49ada` codegen treats every raw-pointer unsafe intrinsic result as raw;
  fixes the Windows `os.path.abspath` 0xC0000005 (verified on CI Windows).
- `f35eb06f` pcc's json appends/slices whole strings (native loads 2.3x,
  dumps 2.2x faster) and joins escaped surrogate pairs.
- `a21fa121` pcc's json decoder reads NaN/Infinity/-Infinity (Stage2 blocker).
- `3aa13b2a` vthread effect analysis walks each callable once per module pass.
- `68493fcf` str iteration with a byte cursor in the sequence iterator, typed
  `for` loops and typed comprehensions (all three were quadratic on non-ASCII
  strings; the comprehension also left its target without a registered root,
  so `[c.upper() for c in s]` did not compile).
- `de777b21` value-position `enumerate(...)` publishes into the caller's output
  root: `list(enumerate(xs))` compiles again (broken since `97b6bced`,
  2026-10-03) and an owned temporary iterable is released (it leaked every item).
- `263d12af` CI limits (next section): Stage1 watchdog 6000 s, push gate is
  Stage1 plus the five-collector native canary, full chain by manual dispatch,
  strict closure 3600 s (not run on macOS), fixture peer wait 15 s.
  `85825b40` counts its 26 new gate tests in HOST_COUNTS (all three
  worker-prior jobs of run 38436916173 failed on the stale count).
- `52ca3f80` native `os.replace`/`os.unlink`/`os.rmdir` raise CPython's errno
  subclass with errno, strerror, filename(s) (was a bare OSError, so
  `except FileNotFoundError` missed a missing file); the worker RSS publication
  retries an errno-only EACCES on Windows (pcc has no `winerror` yet). Cause of
  the Windows native prior-worker failure in run 38437580834.
- `98ee6010` comprehension targets at module scope and in native lambdas are
  registered roots (`[w.upper() for w in words]` at module scope did not
  compile; the targets were not GC roots there).
- `e06cd523` the errno-only EACCES retry of the worker RSS publication applies
  only to pcc's native runtime on Windows; CPython keeps the winerror retry.
- `aea09dca` a float-typed result of an object call (`sqrt(9)` after
  `from math import sqrt`) is unboxed before `int()`, int conversion and unary
  minus (self IR verifier failure; also fixes the alignment `import_math_alias`
  case: fails on the `e06cd523` snapshot, passes with the fix).
- `ddb5fa70` "PE rel32 out of range" names the symbol, sections, RVAs, addend,
  displacement and the section layout.
- `aa643fb7` the process-tree sampler readmits a reused PID through its owned
  kernel parent (macOS Stage1 abort, CI section).
- `7cc98b33` the tar oracle reads the current IR printer; strict closure CI
  budget 6000 s (CI section).
- `984cb2d4` canary compile workers re-raise after their report
  (`PCC_DEBUG_WORKER_RERAISE`), so a failure prints its file and line.

## CI (follow each pushed commit)

CI limits changed in `263d12af` (maintainer request: at least make it pass). The push
gate on Linux/Windows is now `bootstrap_platform.py --gc all --stage 1`: Stage1
and the native canary under all five collectors. The Stage2/Stage3 fixed point
is unchanged as a definition and runs by manual `workflow_dispatch` with
`full_chain: true`; it cannot finish in a 6 h hosted job today (Stage2 section).
The workflow still has no `continue-on-error`. Stage1 watchdog 6000 s on all
platforms (projections below), macOS job 180 min, its 45-minute target
recorded as `darwin_target` instead of asserted. Worker-prior strict closure
6000 s since `7cc98b33` (was 3600 s; see below), not run on macOS (needs
~8.6 GB, runner admits ~3.1 GB). Fixture peer wait 15 s (decision 2, applied).

- Runs 38436916173 (`263d12af`) and 38437580834 (`85825b40`), Linux aarch64:
  Stage1 2396 s and 2384 s. The GC0 and GC1 native canaries pass; the GC3
  canary compile fails in a pcc1 codegen worker after `direct indexed emit`
  with `RuntimeError: slot-call owned-result lease failed` in both runs (GC2
  and GC4 were cancelled after it). `pcc_gc_foreign_lease_acquire` returns -1
  only when the published object's node is marked freeing, or for a managed
  non-leaf object without a node, which only GC3/GC4 reject. No local pcc1
  is newer than the lease code (`1a59d09d`, 2026-10-02); `984cb2d4` makes the
  next failure print its location.
- Windows (both runs): Stage1 compiles every module (codegen done at ~4600 s in
  run 38437580834) and the COFF link fails with "PE rel32 out of range";
  `ddb5fa70` reports the reference and layout. macOS pcc1 is ~674 MB
  (`__TEXT` 626 MB), so the image size alone does not explain a 2 GB distance.
- macOS native pcc1 job (`263d12af`): Stage1 aborted at 3804 s with
  `active_missing`: macOS gave a new worker PID 7199, which an earlier worker
  had used, and the sampler excluded every reused PID, so the worker never
  entered the tree state. Fixed in `aa643fb7`. macOS worker-prior passes at
  `85825b40` and `e06cd523`; Windows RSS publication passes at `e06cd523`.
- Strict closure: with 3600 s it reached the tar oracle for the first time
  (aarch64 at 2916 s, module 27/34; x86_64 at 3395 s) and failed there: the
  oracle could not read typed calls (`call ptr (ptr) @f`), bitcast root slots,
  retained table loads and literals held in constant item arrays. Fixed in
  `7cc98b33` (all six modules pass on a local closure). Module verify+emit
  runs at ~2.4-2.5 s per IR MB, so all 34 modules project to ~3200 s
  (aarch64) and ~3700 s (x86_64): over 3600 s, hence the 6000 s budget.
- Linux x86_64 wheels of both runs were still in Stage1 after 85 min (slow
  runners).
- GC3 canary root cause, reproduced locally with pcc0: `sys.stdout`,
  `sys.stderr` and the `object` class root are malloc'd immortals without an
  object node. `pcc_gc_foreign_lease_acquire` gave such an owner a mode guard
  on GC0-2 but on GC3/GC4 only for graph-leaf tags, so passing `sys.stderr`
  as an argument (`phase_timing.report(sys.stderr, ...)` in the worker) raised
  "slot-call owned-result lease failed". Fixed in `c5363d80` (a no-node
  immortal outside the nursery/zpage gets the leaf mode guard; model cases for
  GC0-4 in `test_foreign_address_leases.py`, five-GC native test
  `test_immortal_stream_operand_lease.py`, GC3/4 raised before). A/B over 17
  lease/ownership/five-GC files and the CPython alignment suite: no regression
  (`super_call` fails on both sides, pre-existing). CI for `c5363d80` (run
  38453713062) validates the canary.
- Run 38449385286 (`984cb2d4`): the four macOS pcc0/worker-prior jobs and
  Windows RSS publication pass; Linux/Windows Stage1, strict closure and the
  Windows PE diagnostic were still running at 19:17 UTC. Without the lease fix
  its GC3 canary is expected to fail again (now with the file and line).

Stage1 projections (`stage1_projection.py` in the archive: per-module times
scaled against the complete `1c980956` runs): Linux aarch64 2338-2362 s
measured; Linux x86_64 ~2550-2650 s on fast runners (codegen ends ~2310 s, the
ELF link was cut during relocation) and ~4800-4900 s on slow ones (1.82-1.90x
per module); Windows x86_64 ~3850-4500 s (1.45-1.69x plus the COFF link);
macOS arm64 ~4500-5000 s (per task 0.77x of Linux aarch64, but 1.0-1.7
admitted workers on 3 cores; three workers would make it ~2300 s).

- Run 38423410429 (`a21fa121`), Linux aarch64: Stage1 2361 s. GC0 Stage2
  (2 workers per chain): export 459 tasks 1426 task-s, done at 717 s; summary
  now passes (56 tasks, 385 task-s, done at 973 s); preload-delta done at
  1220 s; codegen: the exclusive calibration of `unsafe_lowering` took 653 s
  (Stage1: 80.5 s), then `self_backend_parse` ran 109 functions in 157 s of
  frontend time (Stage1: 11.2 s for the same functions, 14x) until the 2400 s
  stage deadline. GC1/GC2 were cancelled after the GC0 failure.

- Linux aarch64 Stage1 now fits: 2338.1 s at `1c980956` (run 38411293966,
  link 243.7 s), 2362 s at `fbe49ada`. At `3a2530be` it had missed by ~5 s.
- Stage2 (GC0, aarch64, run 38411293966): the export phase completed for all
  459 tasks by 808 s (1608 task-s, ~2 workers); before `3a2530be` it reached 273
  modules in 2400 s. The summary phase then failed reading the AST wire of
  `layer1_support` (`1e309` -> `Infinity`; fixed in `a21fa121`). Slowest exports:
  `type_infer` 197 s and `unsafe_lowering` 98.7 s (quadratic iteration of
  non-ASCII source in the directive scan, pending fix), `c_parsetab` 55 s
  (not yet profiled: a local sample with the `0f6fa1d9` pcc1-profiling binary
  only showed the scan `3a2530be` already removed; a current pcc1 is needed);
  the median module took a few seconds.
- Linux x86_64 Stage1 is runner-dependent: at `1c980956` all 460 modules were
  compiled by 2383 s and only the link missed the cap; at `0f6fa1d9` only 72
  were compiled. On those 72 modules every phase, including untouched CPython
  frontend work, was 1.7-2.0x faster in the later run (runner difference; the
  CPU model is not recorded in the receipts).
- Windows x86_64 Stage1: 83/460 modules in 2400 s. macOS Stage1: ~70/460
  modules (49% of the work) in 2400 s, limited by memory admission (below).
- Run 38423410429 (`a21fa121`): Stage1 completed modules at the 2400 s cap:
  Linux x86_64 73 (slow runner again), Windows 91, macOS 80 (average 1.33
  workers). Linux aarch64 above.
- The former 2400 s Stage1 cap was ours, not GitHub's (6 h per hosted job);
  `263d12af` raised it to 6000 s.
- Worker-prior gates: strict closure times out (aarch64, x86_64) or hits the
  memory cap (macOS); Windows now passes default-xdist (2023), windows-exit and
  the native width cases; the GC4 growth case fails intermittently on Windows
  and Linux x86_64 with "missing worker marker" (decision 2). Same picture at
  `a21fa121`: strict closure 1800 s timeouts at 9.2 GB peak (Linux), macOS
  `MEMORY_LIMIT` (3.3 GB cap) at 707 s, Windows GC4 growth child gave up after
  exactly the 6 s deadline.

## Stage1 within 2400 s (goal 1 / platform qualification)

- aarch64: codegen workers keep all four slots busy until ~2100 s (90 idle
  slot-seconds per run), then the coordinator-only link runs ~244 s: stage inputs
  ~91 s (object parse/validation in the SHA-pinned `elf_x86_64.py`, stack-map
  validation ~1/3), resolve/relocate ~130 s for 39.6 M relocations, classify
  ~16 s. Margin is 40-60 s and depends on the runner.
- x86_64: needs about the link time (~300 s) on a fast runner; ~3x on a slow
  one. Its route assembles text (`object_assemble` 2352 of 9376 task-s); a
  structured emission route like aarch64's would remove most of that.
- macOS (3-core M1, 7 GB): admission budget 2.29 GB against per-task
  reservations of 1.1-3.6 GB (peaks 0.5-1.9 GB), so for ~1100 s one task ran at
  a time. Three-way parallelism would fit 2400 s and needs ~0.76 GB per task.
- Work volume: in `pcc.backend.macho_link` ~37% of IR lines are error/cleanup
  paths; GC bookkeeping calls far outnumber work calls. Static keyword binding
  (decision 1) cuts IR 5.6-21% on real modules.

## Stage2 / pcc1 throughput (goal 1, goal 6)

- Keyword/default calls to known callees went through the runtime signature
  binder (~50x slower than a direct call). Static binding is implemented but
  uncommitted (archive `patches/static_keyword_binding.patch`, test
  `tests/python/test_static_keyword_binding.py`): five-GC output equals CPython,
  default-object identity preserved, IR 5.6-21% smaller on real modules.
- Since `168e4d06` every call through a module-level native from-import is a
  live module-namespace lookup plus a dynamic call. The live semantics are
  required (`tests/python/test_live_import_namespace_rebinding.py`); a fast path
  needs an identity guard or a namespace watcher. Not started.
- Stage2 export (run 38391246598, GC0): 148 ASCII modules 516 task-s (median
  2.4 s), 124 non-ASCII modules 3055 task-s (max 291 s, the quadratic scan fixed
  in `3a2530be`); Stage1 exported all 460 modules in 66 task-s. A replayed
  export of one ASCII module under pcc1 (archive `exportreplay/`): vthread
  effect analysis 31%, AST JSON wire 26% (json.dumps 22%), parse 19%, lift 6%;
  self time is 46% GC roots/barriers, 18% allocator, 13% refcount, 6% TLS.
  `f35eb06f` makes pcc's json append/slice whole strings (native loads 2.3x,
  dumps 2.2x faster on a 6.4 MB AST wire); the vthread analysis now walks each
  callable once per module pass (pending commit; export tables of 459 modules
  byte-identical, the 460th being the edited module itself).
- In that replay `_py_func_call_kwargs` (the runtime keyword binder) is on
  nearly every call stack; its own self time is 0.2%, its cost shows up as the
  allocation and GC work above. This is the path static keyword binding removes.
- Phase ratio Stage2 (pcc1) / Stage1 (CPython), Linux aarch64 GC0: export
  1426-1598 vs 65 task-s (~22-24x; ~19x without the three quadratic outliers),
  summary 385 vs 37 task-s (~10x, run 38423410429), codegen 8x
  (`unsafe_lowering`, calibrated alone) to 14x (`self_backend_parse`
  functions). Stage1 codegen is 8266 task-s, so Stage2 codegen needs ~66k-116k
  task-s: 4.6-8 h for one collector chain even with all four workers, against
  a 6 h job that also builds Stage1. A five-collector Stage2/Stage3 in CI needs
  pcc1 several times faster on the compiler workload. The measured owner is
  runtime overhead (GC roots/barriers/pins/leases, allocation, refcounting:
  ~85% of self time in the export replay).

## Open decisions for the maintainer

1. Resolved 2026-10-15: shape tests that reached the binder through keyword
   calls now use a shape that still needs it (see the 2026-10-15 evening
   section); the behavior is covered by native five-GC execution.
2. Resolved: the worker-prior fixture peer wait is 15 s in `263d12af` (asked
   for together with the other CI limits). Evidence: with 15 s the x86_64
   native-pcc0 gate passed in runs 38400558239 and 38405592470; with 6 s it
   failed in 3 of 5 runs, in 38411293966 and on Windows in 38423410429.
3. Source-pinned files: `tests/test_elf_*_source_controls.py` SHA-pin
   `pcc/backend/elf_x86_64.py`; any link-loop change needs the preimages updated.
4. A Windows unhandled-exception reporter (code, image offset, stack scan)
   would change `pcc_windows_start`, whose model test
   (`test_owned_windows_exit.py`) needs a new double. No longer needed for the
   `abspath` crash: cross-compiling for Windows and running under Wine with
   `WINEDEBUG=+relay,+seh` located it (archive README).

## Known failures

- Default suite at `f65ce326d` (2026-10-19, isolated snapshot rerun of the
  424 files that failed on 2026-10-18): 141 pass, 277 fail, 1 timeout;
  first failures and logs in
  `/Users/jiamo/pcc-recovery-archive/2026-10-19-default-suite-reds/`. The
  failures are a long tail (stale source pins, changed values, missing
  fixtures), not one shared cause. Earlier runs from a snapshot without
  `PYTHONPATH=<snapshot>` are invalid: test subprocesses imported the live
  worktree.
- Runtime archive and the table policy (found 2026-10-19): automatic local
  archive selection judges staleness by the source codegen identity only, so a
  runtime archive built under `PCC_PRECISE_STACKMAP_TABLES=1` (runtime frames
  with records) is reused by default builds and vice versa. The stack-walk
  differential needs a table-enabled runtime: without one the collector's own
  frames have no records and their registered roots show as `unmapped`. Tests
  use `pcc_stackmap_runtime_archive` (its own cache key). Folding the policy
  into `codegen_checksum` made explicit default archives fail admission for
  table-enabled links; the fix is exact identity for automatic selection and
  same-source admission for explicit archives (not done).
- User-mode raw addresses (found 2026-10-19): outside runtime-port modules a
  pointer intrinsic's result is typed `int`, so an IR pointer with an `int`
  type can be either a boxed int or the raw address. `ptr_eq(load_ptr(here,
  0), above)` with `above` an untyped parameter holding the same address
  returns 0 (two locals, or two parameters, compare correctly); comparing
  `ptr_to_int` values works. Runtime ports (pointer lane) are unaffected. Repro:
  `/private/tmp/claude-501/-Users-jiamo-my-pcc/3a7f74e3-a0d6-4ce6-8c6c-209e5c96345a/scratchpad/frame1/eq.py`
  prints `loaded 0` (copy it before that scratch directory goes away).
- GC4 relocation-set selection (found 2026-10-19):
  `tests/python/test_freestanding_gc_mapped_roots.py::test_backend4_precise_stackmap_consumer_rewrites_exact_frame_location`
  exits 4 because `pcc_gc_select_relocation_set(1)` no longer selects the one
  freshly allocated, rooted object (the harness then never reaches the stack
  map consumer). The selector changed in `627285079`, `d27cfb236` and
  `9f16a58a9` (2026-10-15/16); the file's earlier
  `test_mapped_roots_have_one_strict_visitor_and_split_providers` failure (a
  stale raw-only classification of `py_subs_exc_cache_slot`, fixed with the
  stack-map registration commit) had hidden it under `-x`.
- Class-attribute identity of properties (found 2026-10-18 after `d26f61b06`
  let `test_property_aliases_preserve_lexical_replacements` run past the
  verifier): for `class A: value = property(...); before = value`, pcc gives
  `A.before is A.value` False, `A.__dict__['value'] is A.value` False and
  `type(A.value).__name__ == 'object'` (CPython: True, True, `property`). The
  static class-attribute read returns an object other than the class dict
  entry. Instance use of properties is unaffected. Repro:
  `/private/tmp/claude-501/-Users-jiamo-my-pcc/3a7f74e3-a0d6-4ce6-8c6c-209e5c96345a/scratchpad/desc/run5/alias2.py`
  (copy it before that scratch directory goes away). The same holds for
  ordinary methods: `Holder.method.__doc__` and `Holder().method.__doc__` are
  None while `Holder.__dict__["method"].__doc__` is the docstring.
- Red at `98422a5b6` (each rerun in an unmodified `git archive` snapshot of
  that commit with its own runtime archives, 2026-10-19):
  `test_gc_backend4_production.py` 6 (deallocating quarantine, class lookup of
  a forwarded method slot exits 13, three genzgc selector/fragmentation
  policies, class creation base/MRO spans); `test_gc_update_referents.py` 2;
  `test_cpy_call_argument_ownership.py` 20;
  `test_owned_descriptor_module_bindings.py` 6 (WorkerMemoryError: 512 MiB
  budget refuses the task); `test_py_class_export_schema.py` 2 (method ref
  registration; `pcc.package.acquire`: "native super().__init__ has no
  implemented initializer for this foreign base");
  `test_callback_sync_context.py::...native_callback_context[virtual_park-*]`
  (`vt.result(task) == 42 and seen == [1, 2, 3]` fails). The GC4 ones and the
  mapped-roots consumer exit 4 above share the relocation selector.
- `tests/python/test_fallback_baseline.py`: with `-x` the file stops at
  `test_layer1_constants_cross_module_static_imports_stay_native` (line 759,
  live-import binding), so the ratchets after it had not run for a long time.
  Run on their own (worktree at `4b6162010` plus uncommitted property edits,
  which change compile inputs): total fallbacks pass; per-module actions exceed
  the ceilings for nine modules (`precise_stackmap` 25/15 predates
  `5ccad8082`); contextual per-module compile fails for 26 codegen modules with
  `slot-call CPython name requires an output-slot bridge: <local name>` or
  `slot-call Call lacks an immediate owned-result handoff`. Re-run on a frozen
  snapshot before classifying.
- Windows native-pcc0 width case: `0xC0000005` inside `os.path.abspath`
  (phase witness `abspath` in runs 38273917206, 38375711981, 38405592470).
  Root cause found and fixed in `fbe49ada`: `_path_abspath_build`'s
  `windows_full_path(...) if ... else null()` was lowered as an owned managed
  value (retained, rooted) because codegen's `_UNSAFE_RAW_POINTER_RETURNS`
  lacked 14 raw-pointer intrinsics; after `free(buffer)` the exit path released
  the freed buffer. Reproduced and verified locally by cross-compiling for
  `x86_64-pc-windows-msvc` and running under Wine 8.0 in an amd64 container
  (archive README, "Windows os.path.abspath").
- GC4 (`docs/investigations/gc-backend4-forwarding-retirement-starvation.md`):
  explicit `gc.collect()` never retires forwarding entries, so relocated garbage
  stays alive and its `__del__` never runs (fix parked: retirement itself
  over-releases a live replacement, `test_gc_relocation_sweep_verdict.py` code 23
  pre-existing); `tuple(list of finalizable instances)` + collect aborts with
  `BAD_INCREF`; `test_gc_backend4_production.py` fails a source-text assertion.
  The programs of `test_list_iterator_ownership.py` and
  `test_comprehension_iterator_ownership.py` abort with `BAD_INCREF` under GC4
  for every shape (`list(xs)`, `list(reversed(xs))`, `list(enumerate(xs))`,
  enumerate value); GC0-3 pass. The enumerate shapes first report 2472/2483
  `__del__` calls for 2000 objects, i.e. objects are finalized more than once
  (probe: archive `scripts/gc4_list_shapes_probe.py`). `ys = enumerate(build(n))`
  completed under GC4 before the enumerate fix only because it leaked every
  item; now that the items are freed it aborts like the other shapes.
- `isinstance(x, object)` and `issubclass(A, object)` return False (CPython:
  True): the `object` value is `py_builtin_type_for_tag(-1)` ->
  `pcc_type_cls_object` (a `py_class_new("object")` class), while user class
  MROs end with the distinct `py_object_root_cache` root; non-instances never
  match. Repro: archive `gc3lease/object_identity.py`.
- `test_foreign_address_leases.py::test_native_gc3_gc4_foreign_address_movement_and_overlap`
  fails before running: "pcc self-link mode does not support link arguments"
  (same on the `e06cd523` snapshot).
- `str.upper()`/`str.lower()` map only ASCII letters (`"é".upper()` stays
  `"é"`); non-ASCII case mapping needs Unicode tables in the runtime.
- `[... for i, c in enumerate(s)]` over a non-ASCII str still indexes by
  codepoint per item (`py_obj_getitem_i64`), so it stays quadratic; `for` loops
  over `enumerate(s)` are normalised to a plain `for` and are linear.
- `test_py_for_target_representation_join.py`: 4 tests fail at `a21fa121`
  (a `SimpleNamespace` codegen double lacks `_module_globals`; IR-shape
  assertions on dict iteration).
- `test_worker_resource_plan.py::test_macos_failure_workflow_preserves_narrow_guard_evidence`
  fails since `2a3ad7ff` (2026-10-11): it pins the macOS evidence upload paths,
  and that commit added the worker-prior-gate paths.
- `test_python_cpython_alignment.py` at `a21fa121`: `super_call` ("slot-call
  operand has no authoritative source or owned result: Call"). The
  `import_math_alias` case (boxed `sqrt(9)` result passed where a double was
  expected) is fixed in `aea09dca`.
- Also failing on the `e06cd523` snapshot (A/B of `aea09dca`, identical with
  and without it): `test_boolean_float_conversion.py::test_integer_power_bool_inputs_never_use_bigint_payload`
  (4 cases, `NameError: _int_shift_sign` at `py_int_ops.py:312`; the helper
  came with `6f87072c`), `test_int_builtin_object_projection.py` (3 tests:
  program traceback and `py_int_from_cstr_or_raise` IR-shape expectations),
  `test_int_of_float_exact.py::test_raw_lane_int_of_float_raises_instead_of_garbage[pcc0]`
  (error text "... to C int64" expected), and
  `test_worker_memory_budget_selection.py::test_mac_ci_final_guard_reselects_after_startup_and_records_final_observation[3113851289-0]`
  (the test replaces the shared `subprocess.run` for its guard double, and the
  guard's termination path runs `ps` through it: `list.index` ValueError).
- `test_runtime_iterator_roots.py`: every model case fails with
  `NameError: _TUPLE_ITER_START` since `75b2ddd1`; its loader
  (`test_runtime_getter_roots._GetterMemory.load`) execs only the function
  definitions of `py_iter.py`, not the module constant that commit added.
- `tests/python/test_raw_addresses_are_ints.py::test_raw_addresses_execute_as_ints_on_the_self_backend`:
  `py_bytes_new(_str_utf8("hey"), 3)` yields `b'mpl'` (stale pointer from the
  str -> raw-pointer extern path); independent of the 2026-10-13 commits (A/B
  each). Repro: archive `repros/str_utf8_extern_dangling_probe.py`.
- Host tests failing on a clean HEAD snapshot (pre-existing), among others:
  `test_py_multi_file_compile.py` (borrowed-rebind IR shape, raw-int scalar slot
  count, mock of the removed `_link_with_clang`, static-export constructor arg
  vs live imports, CPython fallback IR for relative imports; the other 41 pass),
  `test_intent_constraints.py` (coverage dropped: TestCrossBackendDeterminism),
  `test_fallback_baseline.py` (340 cli_bootstrap fallbacks),
  `test_hoist_global_cell_boundaries.py`, `test_unsafe_atomic_global_store.py`,
  `test_native_module_global_raw_pointer.py`.
- Also red on clean snapshots of `ed25f93d8`/`0272b8f4f` (2026-10-19, each
  rerun there with the same assertion; not caused by the literal/default
  work): `test_runtime_getter_roots.py` (`_INSTANCE_LOOKUP_VARS` undefined in
  the test's interpreter), `test_raw_scaffold_exact_container_subscript_ownership.py`,
  `test_ir_scaffold_field_contract.py` (37 `self.builder` writes vs pinned 35;
  the two extra come from `02b9bc2d3`/`9007dbd1d`), `test_generator_result_lexical_root.py`
  (2 >= 4), `test_cext_strict_decref_lifecycle.py` (source-text assertion;
  `py_list_new` header conflict), `test_cli_bootstrap_pytest_extract.py`,
  `test_exception_chaining_wiring.py` (2), `test_exception_constructor_owned_result.py`
  (all 7), `test_exception_raise_lifetime.py` (100 == 50),
  `test_extern_c_string_arguments.py` (7 of 31), `test_freestanding_strtoint.py`,
  `test_freestanding_mem_str.py`, `test_gc_threading_substrate.py`,
  `test_gc_abstraction_surface.py::test_colored_relocating_gc_read_barrier_clears_candidate`,
  `test_dict_literal_temp_release.py[4]` (GC4 197 of 200), `test_literal_unpack_slot_ownership.py`
  (`MovingModel` stub lacks `_is_valueclass_payload_type`), `test_native_exception_context_chaining.py`,
  `test_native_str_maketrans.py`, `test_native_str_join_gc_rooting.py`,
  `test_native_tuple_dict_literal_cpy_bridge.py`, `test_py_dict_literal_cpython_key_pairs.py`
  and `test_py_literal_splat_source_position.py` (libpython runtime missing),
  `test_str_builtin_consumes_owned_argument.py`, `test_str_encode_gc_roots.py`,
  `test_floor_division_runtime_contract.py` (`stack_alloc` undefined),
  `test_py_frontend_worker_export.py::test_export_worker_full_lifts_native_stdlib_asyncio`,
  `test_self_backend_call_result_registers.py::test_result_consumed_before_any_call_moves_into_a_pool_register`,
  `test_selfhost_no_over_lane_int_literals.py`
  (19 over-lane literals in the compiler; pcc0-compiled code prints all of
  them correctly, so whether they still misbehave under pcc1 is open: compile
  and run such literals with a Stage1 pcc1).
- Virtual threads: `test_virtual_thread_park_effect.py::
  test_concrete_may_park_method_chain_resumes_to_python_result` prints `None`
  instead of 42 (fails at `1c980956` already; the may_park analysis marks
  handler, Worker.leaf and Worker.middle correctly, so the loss is in lowering
  or the runtime), and `test_cross_module_may_park_uses_generator_abi_and_resumes`
  fails on the same snapshot. Not bisected.
- Stale or environment-gated tests seen while checking `fbe49ada` (all fail
  identically without it): `test_freestanding_variadic_export.py` (5 of 6,
  "pcc.i64 does not implicitly convert ordinary Python int values"),
  `test_freestanding_stdio.py` (13 of 15, need `PCC_RUNTIME_ARCHIVE`),
  `test_freestanding_platform_process.py::test_runtime_archive_process_symbols_are_owned_by_python_port`
  (py_gc_backend.py calls `pcc_platform_abort`), `test_native_json_provider_matches_cpython.py`
  (reads the removed `pcc/ir/support/json.py`),
  `test_symbolic_stackmap_location_budget.py::test_shared_budget_object_links_and_executes`
  on macOS (executes a Linux ELF).

## Repro commands

```bash
# Per-phase Stage1 totals from a CI artifact
python3 /Users/jiamo/pcc-recovery-archive/2026-10-13/scripts/phase_breakdown.py \
  <artifact>/bootstrap-<platform>/platform-qualification/shared/stage1.log
# Tar oracle on a local strict closure (parallel frontend, ~380 s), then on
# the saved per-module sections in ~12 s
uv run python /Users/jiamo/pcc-recovery-archive/2026-10-13/scripts/oracle_closure.py OUT_DIR
uv run python /Users/jiamo/pcc-recovery-archive/2026-10-13/scripts/oracle_sections.py OUT_DIR
# Static keyword binding (host IR shape + five-GC native)
env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -x -n0 tests/python/test_static_keyword_binding.py
env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -x -n0 -m integration tests/python/test_static_keyword_binding.py
```

Broad pytest sweeps must set `PCC_NO_AUTO_PCC1=1`: `pcc_gate` fixtures otherwise
start an unguarded Stage1 build to provision pcc1.

## Ten-goal acceptance remains open

1. Bootstrap: produce pcc1, run the original C/Python controls, then establish a no-libpython Stage2/Stage3 byte fixed point.
2. Five collectors: finish correctness, concurrency, relocation, GC4 capacity, FRESH_ALLOC, long-term memory, throughput and stability.
3. Platforms: execute macOS ARM64, Linux x86_64, Windows and Linux aarch64 qualification, including the standard macOS 15 3-core M1 / 7 GB / 45-minute job and wheel parity.
4. Ownership: complete compiler/runtime/ABI/provenance, stubs, bindgen, uv-lock and CLI/API consistency without product cc/LLVM/libpython fallbacks.
5. EDG-derived design: complete frontend semantic representation, a single layout authority and per-function compilation-memory management; qualify layered validation and centralized ABI across platforms. Diagnostic RSS guards are not invented acceptance thresholds. External i128 register-edge ABI, x86 dynamic alloca and two-dimensional VLA remain gaps.
6. Build performance: prove Stage2 no slower than Stage1 and a shared five-GC build under six hours. Diagnostic timings do not establish these targets.
7. Tests and cleanup: execute the full original C/Python and integration inventories; finish TLS, archive, freestanding and asynchronous timeout work.
8. Gateway: qualify actual native network/TLS and structured concurrency, then establish performance beyond asyncio.
9. Optimization: qualify tail-call, MADD, peephole, scaffold identity and thread-logging behavior and performance.
10. GUI: complete native app, five-GC, HTTP/TLS, Loader/HMR, typed FFI, interaction and pixel equivalence against Harness 47f943859bef60e4160492346772ded9b24f765a. The existing 160 host passes and 7,412 verified reference blobs do not establish native or pixel completion.
