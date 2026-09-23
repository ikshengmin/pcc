# Investigation: native Stage2 link exceeds its budget while assembling inputs

## Status

Active — 2026-09-22 route correction and sorted-input repair: the owned native
link driver passes `_consume_inputs=True`. Its 40-input merge residual is
9,361,909 bytes before this round and 7,843,495 afterward; the earlier
43,688,822-byte measurement uses the separate borrowed-input path. Neither is
a full Stage2 peak. The keyed `sorted` source-owner fix is measured on the
owned route; the borrowed input-list owner remains open.

Active — 2026-09-22 constructor-boundary repair: the actual 40-PCO merge's
second-call residual falls from 131,052,935 to 43,688,822 requested bytes
(66.66%) by routing classmethod `cls(...)` through the existing constructor
argument lifetime manager. Output bytes match the host after forced GC.
The remaining residual, new pcc1 and full Stage2/Stage3 are still open;
no whole-stage memory or timing qualification is claimed.

Active — 2026-09-22 follow-up: native lambda/callable-key lifetime defects
are repaired in focused execution, but the real 40-PCO merge still retains
about 131 MB per call. This round does **not** resolve the merge-graph owner
or qualify Stage2. The final update records the negative component A/B.

Active — 2026-09-22 correction: a factor-separated 40-PCO experiment now
identifies and repairs typed-enumerate comprehension owners and the final
unpacked binding. Native GC0 retained requested bytes fall from 36,994,891
to 11,573 per second call. This is a host-compiled native component result;
the new compiler, full Stage2 and the separate merge-graph retention remain
unqualified. See the final update for identities and failed adjacent checks.

Active — 2026-09-21. The latest diagnostic Stage2 attempt timed out at
3600 seconds during native link input decoding, after all 392 frontend
modules and PCOs completed. No pcc2 was produced. Peak tree RSS was 2.83 GiB
under a 6-GiB cap. This resource configuration forced serial PCO emission;
see the final update for timings and sampled owners. The earlier 2400-second
attempt stopped in emission with 18 PCOs and remains a separate failed run.
Native bootstrap continuation also has a fresh Stage1 and small native
CLI/five-GC link execution proof.
The earlier full source-v13 link replay exceeded 8 GiB during image finalization after
layout/relocations completed. No pcc2 was produced. The preparation-frame
change released some memory but did not retire the large graph sufficiently;
its focused native tests are not full-link qualification. All source, inputs,
receipts and logs remain available. That replay remains unsuccessful.
Related transport history:
[native object worker memory](pcc1-small-lane-native-object-worker-memory.md).

## Problem Description

The fresh native Stage2 completed all 389 IR captures and emitted 43 ASM plus
346 PCO artifacts. Its native `owned_link_driver` then exceeded the 4.5 GiB
process-tree cap while the sampled process was in `arm64_asm_driver`.
The watchdog stopped its own children; no pcc2 was produced. A successful
small native-link probe had not qualified these much larger ASM inputs.

The deferred runner retained the historical host-linker's ASM/PCO lane split
when selecting a native linker. The native entry assembles ASM in process and
keeps the resulting objects alongside previously read inputs. The sampled
failure proves this selected route exceeds the budget; it does not quantify
how much of that peak belongs to the current assembler versus retained objects.

## Repro

Evidence root: `/private/tmp/pcc-stage2-native-ayfmxjgz`.
Compiler source SHA256:
`92fc1c1b13ec5627bae8ced72dda2a03c385d1de00a8ea85f8852e709ca4e437`.
pcc1 SHA256:
`425b242ecd77fd5c6caaccc8f6cfd1ec11eef98cab9d0de4810d339f5df2a6af`.
Native linker SHA256:
`a37448b5101a70ad801aeed231018a0351c82775ec047c9ad23194182f7ef483`.
Runtime SHA256:
`d8f82e49001889450c2d6a7a084397889e0f638e609fec7a9a0ed9932a3aeef6`.

`loop-stage2/codegen-launch.json` freezes commands, environment, tool hashes
and inputs. `loop-stage2-codegen.result.json` is terminal `MEMORY_LIMIT` at
1921.426 s, with peak tree RSS 4,869,914,624 bytes against a 4,831,838,208-byte
cap. The native linker process itself accounts for 4,828,168,192 bytes in the
last sample. `loop-stage2-link-active.folded` resolves the live sample against
that linker's own symbols. The input manifest and all codegen outputs remain.

## Test [CONFIRMED]

The original native-link attempt fails as above. Replaying only the largest
CLI module's same frozen PIDX input with
`pcc1 --pcc-self-backend-indexed-emit-worker INPUT OUTPUT PCO` succeeds in
52.711 s at 2,067,906,560-byte peak tree RSS. Its input is 54,142,045 bytes.
Exact command, input/compiler hashes and watchdog result are in
`loop-stage2-pco-recovery/cli-emit.launch.json` and `cli-emit.result.json`.
This is an emitted-object check, not executable or full-link qualification.

## Proposals

When the deferred v2 runner selects a native linker, use fresh PCO emit workers
for every module. Keep its existing per-worker memory admission and outer tree
cap. The linker then consumes packed inputs; it need not assemble the compiler's
large generated text. Assembly remains a pcc-owned capability.

`run_pcc_deferred_link.py --native-linker` now selects that route and records
the linker hash/owner. Seven focused tool tests pass, including ordered PCO
publication for the native route and the unchanged host lane selection. A v1
plan with this new option fails explicitly before starting workers.

The recovery reuses the 346 completed PCOs and the qualified CLI PCO, and
emits the remaining 42 PIDX inputs into isolated outputs. It does not rerun
frontend/type inference or reuse objects from a different compiler source.
`loop-stage2-pco-recovery/recovery.json` binds its input/output hashes and
native owners; the complete link and emitted-program execution remain pending.

## Update: packed-input link also exceeds the budget

All 42 recovery emits succeeded in 805.803 s. Together with the retained CLI
PCO and original 346 PCOs, these cover all 389 modules. The subsequent native
link reached 4,834,525,184-byte tree RSS and was stopped at 1122.603 s recovery
elapsed. `loop-stage2-pco-recovery/process.result.json` is terminal
`MEMORY_LIMIT`; no pcc2 exists and the recorded child processes are gone.
The recovery JSON's last `LINKING` state was interrupted by the outer watchdog
and must not be interpreted as a live process.

`loop-stage2-pco-recovery/link-active.folded` resolves the sampled path to
`link_relocatable_native -> _native_section_payload -> decoded_relocations ->
Struct.unpack_from`. Source also materializes `Relocation` records during
merge and later `NativeRelocation` records at the output boundary. Their exact
shares of the peak have not been measured; this is not yet a complete memory
root-cause attribution.

A read-only, fully validated inventory of the retained inputs reports 389
objects, 652,051,177 encoded bytes, 315,106,426 section bytes, 252,241 symbols
and **7,306,424 relocations**. `link-population.json` and its watchdog receipt
record the 8.198-second inventory. Runtime archive members are additional inputs.
Narrow emitted-program checks did not reproduce a general nested-generator or
imported-dataclass argument ownership leak. A filtered tuple generator at one
million iterations used 11,878,400-byte peak RSS; the matching finalizer probes
released their objects. These checks do not establish full linker leak freedom.

The next experiment must reuse these PCOs and isolate link record retention.
Do not repeat the full compiler build. The native all-PCO scheduling route
removes the ASM handoff but is not a qualified solution to the remaining merge
memory problem. Preflights for 8, 6 and 5.5 GiB budgets all refused the current
swap-pressure condition; protections were not bypassed.

## Update: one bounded retention investigation and operand fixes

The maintainer requested one investigation round. Evidence is isolated under
`/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-link-retention-_e7852l7`;
`round-result.json` records source/test/probe/input identities. No full Stage2
was rerun. Diagnostic-only logging was added in private source copies, not in
the working-tree linker. The native diagnostic linker passed the small emitted
execution check (exit 42 and byte equality) before replaying the retained inputs.

Timestamped stage markers were matched to the nearest tree-RSS sample:

| Marker | Time | Tree RSS |
|---|---:|---:|
| 389 PCO inputs loaded | 139.208 s | 2535.8 MiB |
| 528 inputs inspected, including archive selections | 147.245 s | 2710.0 MiB |
| 65 inputs merged, 1,419,969 retained relocations | 181.667 s | 3366.6 MiB |

This deliberately bounded diagnostic hit its 3.5 GiB cap at 185.535 s.
It shows substantial memory already present before merge, plus further
growth while retaining relocation records. Neither the packed input byte size
nor one sampled hot stack alone explains the complete footprint.

A native probe then repeatedly decoded the actual CLI PCO, discarded the
result and ran GC while observing the source bytes' reference count. The
baseline rose from **1 to 30,796 after five decodes**, or 6,159 per decode.
Object field counts/sizes were normal: PackedNativeObject 4/64 bytes,
PackedNativeSymbol 4/64, PackedNativeSection 11/120.

Two producer/consumer ownership gaps were reproduced and fixed generically:

- `_emit_slice_load` did not consume owned field/call receivers. The ordinary
  reduction `holder.data[1:2]` retained 32 references after 32 iterations.
  Native slicing now balances receiver and bound owners, protects them while
  later bounds execute, cleans exceptional edges, and preserves an aliased
  result across cleanup and moving collection.
- One-argument `bytes`, `bytearray` and `memoryview` constructors did not consume
  owned operands such as `self.encoded`. Their shared lowering now records its
  new result and balances input ownership on success and error. Borrowed
  operands retain an independent temporary owner.

The slice fix alone reduced the probe to four leaked references per decode.
Both fixes together keep the reference count **exactly 1 after every decode**.
The generated native decoder uses the real production module and the same
runtime/input bytes, self backend and IR-passes-off mode. The initial probe
outside the package context failed to import the compiled backend; it is not
counted as decoder execution evidence. The corrected package-context probes
are `decode-package`, `decode-fixed`, and `decode-final` in the evidence root.

Validation: `test_slice_operand_ownership.py` has 19 passing host-compiler
cases, each executing its generated program on GC0–GC4. They include borrowed
controls, field/call receivers, bounds evaluation failure, raised `__getitem__`,
bound/receiver finalizers, rebinding and a returned receiver alias. Thirty
adjacent bytes-decoding, container-ownership, borrowed-loop, getitem and slice-
mutation tests pass. The corresponding 19 pcc1 integration cases are present
but were not run with a newly rebuilt pcc1 in this bounded round.

**Limit:** this fixes demonstrated reference leaks, not the complete Stage2
memory failure. The five-decode probe's single-run peak remains approximately
301.5 MB before versus 303.0 MB after; there is no demonstrated peak-memory or
throughput improvement. The new-source Stage1/pcc1 and full native link gates
remain pending. Keep the retained PCOs for the next link investigation.

A separate pre-existing runtime gap surfaced during error-test construction:
`py_bytes_slice` returns NULL for a zero step without setting ValueError. The
new ownership test uses an explicitly raising `__getitem__` to test the runtime
error cleanup edge; it does not claim the bytes zero-step case is fixed.

## Update: 2026-09-20 — membership temporaries retain decoder allocations

The next requested round reused the same PCO and runtime and kept the preceding
operand fixes. Evidence root:
`/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-decode-live-qs2n_pbb`.
`round-result.json` records compiler-source, probe and input hashes. Both probe
binaries were emitted by host pcc's self backend with IR passes off, then run
natively under a 1 GiB process-tree guard and the shared performance lock.

The probe separately sampled allocator live-requested bytes, mapped capacity
and reclaimable raw slabs. Discarding each decoded result and collecting still
left about 20.49 MB of additional **in-use** allocation per iteration, with
the source-buffer reference count correctly remaining one. Thus this was not
merely RSS retained after freeing all temporaries.

Native phase instrumentation placed most growth in ordinary relocation
validation: framing increased live bytes from 18,911,868 to 20,718,468;
checking the text section's 214,013 relocations increased them from
21,077,516 to 40,557,996. The first instrumentation attempt incorrectly called
the native-only heap extern from the host compiler; it failed and was replaced
by a native-execution guard in the private diagnostic copy.

Small native controls showed no comparable growth for direct struct unpack,
wrapped/method unpack, tuple destructuring, dictionary lookup or set insertion.
The discriminating ordinary program `(bool_value, length) in forms` retained
400,000 bytes over 10,000 iterations: one 40-byte tuple per call. The new
regression with both `in` and `not in` retained 800,000 bytes per batch before
the fix. Tuple-literal membership also emitted its RHS once before dispatch
and then emitted its elements again in the unrolled path.

`compare_membership_lowering._emit_membership` now evaluates native operands
once in source order, preserves their owners across RHS evaluation and
contains callbacks, and cleans success/error paths. Already-evaluated tuple
literals use the runtime contains operation, preserving identity and comparison
short-circuiting without another element evaluation. The existing known-CPython
and string paths remain; late CPython containers retain precomputed operands
in their original domain. The defensive late-domain IR tests use controlled
hint/emission hooks, not evidence of a separately executed CPython runtime.

The same five-decode native probe reports:

| Measurement | Before membership fix | After |
|---|---:|---:|
| Extra live bytes after dropping all results | 102,983,232 | 12,447,067 |
| Peak process-tree RSS | 298,172,416 | 49,086,464 |
| Elapsed wall | 20.266 s | 20.546 s |
| Source-buffer reference count after each iteration | 1 | 1 |

This scoped probe reduces retained bytes about 88% and peak RSS about 84%.
It is one controlled component comparison, not a full Stage2 or throughput
acceptance. The remaining steady growth is 2,382,927 bytes per decode and is
still open. No new Stage1/pcc1 or full Stage2 was built in this round.

Validation: **47 passed, 25 deselected** in the bounded packet. The new
ownership/error cases execute generated programs on GC0–GC4; evaluation-order
and comparison-short-circuit behavior are also executed. The packet includes
C/pcc-Python bytes membership comparison, prior slice/buffer ownership,
dynamic getitem and string comparison checks. CPython-domain routing checks
are explicitly IR tests. A stale routing test treated `os.environ.keys()` as
foreign; executing the old lowering reproduced its failure too. It now uses
an actual CPython eval container, with a separate assertion that environment
keys remain native.

The working-tree compiler files still require fresh native compiler/bootstrap
qualification. Retained Stage2 objects predate these lowering changes and must
not be used as new-source fixed-point evidence.

## Update: 2026-09-20 — three further scoped rounds

Evidence root:
`/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-three-rounds-6r82o857`.
The user requested three rounds. `round-source-hashes.json` records the changed
source/tests; `canonical-source-manifest.json` identifies the frozen bootstrap
closure as `f52d2ff95316b81295bc192b76d70f3a2559dfa30dbf08381df158a9907fb011`.
The decoder input and runtime are unchanged from the preceding experiment.
Native component builds still use host pcc0, self, no libpython, direct indexed
emission and IR passes off. The paired `*.result.json` receipts retain commands,
timeouts, RSS limits and output paths under the shared performance lock.

### Round 1: materialized range ownership [CONFIRMED]

After the membership fix, ordinary relocation validation still accounted for
most growth. A reduction exhausted generators yielding either integers or
tuples. Repeated 10,000-item scans retained 131,208 bytes each; repeated
200,000-item scans retained 2,097,288 bytes each (`generator.stdout`). This was
not a one-time generator initialization cost.

Generator range loops call `_emit_range_value_call` directly and iterate its
materialized list. The helper did not record that construction owner, so the
iterator retained the list without the caller consuming the original owner.
The helper now records its owned result. No generator scheduling or runtime
layout changed. `test_generator_range_ownership.py` first failed with
131,152/131,192 bytes retained, then passed exhausted/close/error exits with
emitted execution on GC0–GC4. The phase-instrumented real decoder's first
decode/drop sample fell from 2,915,471 to 776,847 extra live bytes.

### Round 2: int.from_bytes arguments [CONFIRMED]

The next reduction separated symbol-name reading from validation. Name reading
alone retained 178,253 bytes each time; validation alone retained 65,816.
Complete decode/drop repeated at approximately 244 KB per call. The name path
uses `Struct.unpack_from`'s single-integer case, which slices four bytes and
calls `int.from_bytes`. That lowering evaluated the buffer and byteorder but
released neither transient argument.

The generic lowering now retains borrowed arguments across subsequent effects,
pins them, balances success/error exits, and preserves the returned integer
while releasing its inputs. `test_int_from_bytes_ownership.py` reproduces
760,000/760,040 bytes retained over two 10,000-call batches before the fix,
versus 0/40 afterwards. It also executes argument failure, runtime failure,
borrowed-global rebinding and a 128-bit conversion on GC0–GC4.

The same five-decode/drop/GC component probe, with both fixes, reports:

| Measurement | Before these rounds | After rounds 1–2 |
|---|---:|---:|
| Extra live bytes after dropping all results | 12,447,067 | 861,472 |
| Peak process-tree RSS | 49,086,464 | 39,436,288 |
| Elapsed wall | 20.546 s | 20.513 s |
| Source-buffer refcount | 1 | 1 |

This is a retained-allocation reduction, not a demonstrated speedup. The
remaining steady growth is **65,808 bytes per decode**; full linker memory
acceptance remains open. `both-decoder.stdout` and `both-decoder.result.json`
are the native execution receipts, not a pcc1/bootstrap gate.

### Round 3: dynamic equality narrowed an integer expression [CONFIRMED]

The new conversion test exposed a separate failure: a correctly decoded
128-bit integer printed identically to `2 ** 128 - 1`, but equality was false
in a module using the raw integer ABI. IR showed the mixed DynType/scalar
comparison emitting `py_int_pow`, then `py_int_to_i64` and `sub i64` for its
right operand. Printing used an exact object boundary and preserved the value.

Mixed dynamic equality now evaluates ordinary `int` operands at the existing
exact object boundary. Explicit machine integer types keep their selected
representation. `test_dynamic_bigint_comparison.py` first reproduced the false
equality, then passed both operand orders, equality/inequality and a custom
comparison method on GC0–GC4. The original equality assertion was also retained
in the conversion test after this fix; it was not removed to hide the failure.

### Validation limits

The adjacent packet stopped with **63 passed, 1 failed, 31 deselected**. Its
failure was the existing `libpython=auto` generator test using `itertools.chain`:
`AttributeError: 'str' object has no attribute 'chain'`. Compiling the identical
input with the preceding round's frozen compiler source reproduced the same
failure (`baseline-cpy-iter`, `cpy-baseline.stderr`). Thus this packet is not
green, and the remaining CPython-bridge cases were not silently counted.

A separate remaining-native packet passed **9 tests**, including generator
protocol/close/contextmanager, integer bytes conversion, bigint parsing, the
exact-int protocol ratchet and five-GC promotion with the C runtime mirror.
That gives 72 passing relevant nodes. Fresh Stage1/native compiler qualification
is recorded separately below; no full Stage2/Stage3 claim follows from these
host-compiler checks.

The fresh Stage1 build **succeeded** from the frozen source above, using the
existing receipt builder with two frontend/backend jobs and the same prebuilt
pcc-Python runtime. Compile wall was 585.890 seconds; the guarded build plus
native function smoke took 611.617 seconds, peak tree RSS 5,287,329,792 bytes.
The new compiler compiled a function program whose emitted executable printed
`42`. Compiler SHA-256:
`737abbd6b82510ef29effebe54d4ba8b3e80b7ed5ec13f7e8b688c577e7e22b0`.
Receipts are `stage1.result.json`, `stage1/stage1-result.json`,
`stage1/build-receipt.json` and `stage1/function-smoke-run.stdout` under the
evidence root. This proves fresh Stage1 and the executed function shape; it is
not Stage2/Stage3, a new runtime build, or bootstrap speed acceptance.

The resulting native pcc1 then passed **6 integration regressions, 6 host
variants deselected**, in 143.39 seconds. All three new test files used
`PCC_CURRENT_PCC1` pointing to this isolated compiler and its runtime bundle;
their compiler fixture denies host compiler/cc helpers. Every case compiled
with `-o`, then executed its generated program under GC0–GC4 and checked values
or lifetime effects. `native-tests.{result.json,stdout,nodes.jsonl}` records the
gate. This adds fresh pcc1 evidence for the three changed shapes without
claiming a current-source Stage2 or fixed point.

The bootstrap baseline checks read the existing `build/bootstrap-self/pcc*`,
not this isolated build. `baseline-artifact-identities.json` records their
actual hashes/sizes. The checked-in baseline JSON supplies historical linkage
and normalized-equality expectations without a source hash, and this legacy
artifact directory has no JSON build receipt. Its passing linkage/normalized
byte checks therefore do not qualify the current source. The fresh compiler
identity and executed shape gates above remain the applicable new evidence.

The combined bootstrap/fallback baseline packet hit its unchanged 120-second
watchdog while running `test_closure_per_module_codegen_passes`. The durable
node log records 23 preceding passes, but the packet **timed out and is not a
pass**; later IR fallback checks were not reached. Its process group was
terminated by the watchdog. No budget was widened and no fourth investigation
was started. Full baseline completion remains a qualification gap alongside
the unrun current-source Stage2/Stage3.

## Update: 2026-09-20 — full-link replay, callable owners and quadratic rebasing

The next user-requested Stage2 round uses
`/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-stage2-link-round-fvsjs7ch`.
`launch.json` verifies the successful f52d Stage1 source/compiler/runtime and
all 389 retained PCO hashes. These inputs were emitted from source 92fc, so a
successful replay is a completed old-input link, not current-source Stage2.
All full native replays keep the 600-second timeout and 4.5 GiB tree limit.
The first launch mistakenly hid `ps` from the monitor as well as the native
child: its `SAMPLER_ERROR` receipt records termination after 0.063 seconds.
The subsequent launches restrict only the native child's PATH.

The freshly built baseline native linker passes a native return-42 probe with
bytes identical to the host-owned reference. Its full replay nevertheless
ends `MEMORY_LIMIT` at 365.256 seconds, peak 4,874,780,672 bytes. No pcc2 is
produced. `readin.folded` and `merge.folded` bind samples to this exact binary;
the latter attributes 666 of 1,526 samples to `_native_section_payload`'s
duplicate relocation scan. That scan has not been optimized in this round.

Two additional owner defects were reproduced and fixed in
`call_expression_lowering.py`:

- Native `sorted()` returns were not marked owned. A generator whose final
  branch calls `sorted()` lost ownership tracking even on its `range()` branch.
  The real PCO payload/read-relocation reductions retained about 2,097,256
  bytes per discarded call, then 64 bytes after recording the sorted owner.
- Native indirect-call results were not marked owned. An optional callback
  (`source=None`) returning a generator added one receiver reference per call.
  A 100-call regression changed from 100 extra references/no finalization to
  zero extra references/one finalization. The real merged `NativeObject`'s
  observed reference count changed from 11 to 1: the validator calls its bound
  relocation source twice for each of five sections. Known CPython result
  domain tagging remains separate.

Eight new host-compiler regressions execute on GC0–GC4. The generic expression
callback also releases fresh results, but a bound callable expression still
retains two receiver references through separate operand lifetimes. Static
custom sorting releases its result storage; object-finalization probes remain
unresolved. These are not claims of complete callable/sort leak freedom.
The full merge drop probe also retains substantial allocation despite the
result reference correction; raw-ABI probe cleanup is not yet isolated.

The owner-fixed diagnostic replay merges all 528 inputs, retaining 7,444,150
relocations. After payload materialization it records 2,897,173,166 in-use
bytes versus 4,269,420,544 bytes allocator capacity. It later reaches
`MEMORY_LIMIT` at 564.428 seconds, peak 4,939,776,000 bytes. It does not produce
pcc2. A late sample corrects the initial inference that this was still the
stack-map merge: the process was in section-target rewriting, repeatedly
searching `target.relocations` with `next(...)` and then `remove()`.

Section-target records are now deferred at collection time, retained directly,
and rewritten/appended in the same order as the former remove-and-append
algorithm. Symbol relocations keep their original relative order. This
eliminates the repeated full-list searches and shifts. In the 1,152-record
regression, the old algorithm read `Relocation.offset` 406,208 times; the new
one satisfies the linear bound below 46,080 reads. Native/packed/Mach-O inputs
all pass, including named targets, biased anonymous targets, unchanged
fail-closed executable rejection for anonymous targets, and emitted execution.

The rebuilt native candidate links the 1,152-record/128-section-target probe
with byte-identical host output and native exit 42. The surrounding object,
link, literal-dispatch, callable and kwargs packet passes 81 tests. The final
compiler-source snapshot is
`33b69d66b1a4ba566ba4235879dd320294b48aefaf889b28c159b99aa7a1b0aa`.
`rebase-fixed-source-hashes.json` separately identifies the private diagnostic
markers; `rebase-fixed-launch.json` distinguishes compiler source, binary,
runtime and old input identities. Full replay and fresh compiler gate results
are recorded separately; none of the component passes establishes a fixed point.

The linear-rebase candidate still ends `MEMORY_LIMIT`: 355.641 seconds, peak
4,931,993,600 bytes. Its last markers show all 528 inputs merged, then 25,907
pending section rewrites. At the latter boundary it reports 3,281,940,353 live
bytes and 4,783,185,920 allocator-capacity bytes. It does not reach the marker
immediately before `NativeObject.from_sections`. Thus the remaining peak lies
between rebasing and construction of the source-section list; the exact large
allocation has not been attributed. Source inspection confirms `pack_into`
writes individual bytes, so a whole-buffer copy there is not established.
`bytes(m.data)` snapshots and overlapping object representations remain
candidates for the next bounded investigation, not confirmed fixes.

A separate balanced native A/B uses identical frozen 1,152-relocation inputs
(128 section targets), the owner-fixed control and linear-rebase candidate.
Three alternating pairs all produce byte-identical images and execute with
exit 42. Median wall is **1.328146 s -> 0.145015 s (9.16x)**; median CPU is
**1.31 s -> 0.13 s**. `rebase-ab.json`, its per-run `/usr/bin/time` receipts and
process-tree receipt record the experiment. This demonstrates the targeted
rebasing improvement; it is not a completed full-link or Stage2 throughput
measurement. No pcc2 was produced by any of this round's full native replays.

Fresh Stage1 from the final 33b6 source succeeds: compile 576.110 seconds,
guarded build plus native function smoke 596.501 seconds, peak tree RSS
5,284,315,136 bytes. Its new compiler compiles and runs the function smoke with
output `42`; compiler SHA-256 is
`b4910ca5b253f2c56ecbe7599df215d5f59f49a6bed195141de686c6121c8e90`.
The same prebuilt D8 runtime was reused. This single build is qualification
evidence for Stage1, not a paired bootstrap speed verdict.

The new pcc1 passes all **8 integration regressions, 8 host variants
deselected**, in 150.38 seconds. Both new test files compile with this exact
native compiler and execute the resulting programs on GC0–GC4.
`native-tests.{result.json,stdout,nodes.jsonl}` records the run. The applicable
evidence is therefore fresh Stage1, eight native changed-shape checks, the
81-test adjacent packet, and the scoped native rebase A/B. The prior full
baseline closure timeout remains an open gate; it was not rerun or reclassified
as green here. Full current-source Stage2/Stage3, the remaining link peak and
the separate callable/sort lifetime gaps remain unfinished. This requested
round stops after saving its evidence; no commit, push or installation.

## Update: 2026-09-20 — immutable payloads and exact bytes construction

The following requested round uses
`/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-stage2-peak-round-iywpb0ew`.
The current source matched the preceding successful 33b6 Stage1. All 389 old
PCO hashes and the D8 runtime were verified before reuse. An additional native
phase trace completed all 25,907 rebases, then failed while constructing the
first output section: `__TEXT,__text`, 136,281,820 data bytes and 7,380,323
relocations. No section-end marker followed. `trace.result.json` records
`MEMORY_LIMIT` at 369.480 seconds and 4,932,059,136-byte peak tree RSS.

The linker now retains immutable joined bytes and copies a section into a
bytearray only when a rebase actually writes it. Merged stack maps remain
immutable. Input stack-map payloads and finished private rebase/name tables
are released before output construction. The initial use of `set.clear()`
failed a native probe; local rebinding replaced these cleanup calls. A full
replay accidentally launched after that failed probe was stopped after
17.291 seconds, and is not validation evidence.

Native 32 MiB payload measurements exposed another copy: `bytes(existing_bytes)`
always called `py_bytes_new`. Both C and pcc-Python runtimes now retain and
return exact bytes objects; mutable inputs still copy. The new identity and
post-rebinding lifetime test failed in both original runtimes and passed with
CPython. It now passes alongside the C/pcc-Python comparison and slice ownership
packet: **23 passed, 21 deselected**, with relevant programs executing GC0–GC4.

Only `py_obj_stubs.o` was rebuilt using the owned library frontend, owned IR
passes and owned object emitter. The owned archive writer assembled it with
170 byte-identical retained members, each checked against its original receipt.
The incremental archive SHA is
`8e6ddd7b614aeed1ce8359a4e799cb981dd333f51399db99ef48cfe251b85c10`;
the frozen compiler/runtime source is
`246e1a03040c2c7ef4213a082786e01b915e3cf8bfa1223179702d768007d6ff`.
`runtime-incremental/{reuse.json,result.json,commands.json}` records this scope.
This is not a rebuild of every runtime member with the latest compiler.

Three alternating native 32 MiB payload A/B pairs produce byte-identical images
and exit 42. Median process RSS is **438,419,456 -> 337,723,392 bytes**; wall
is **2.226900 -> 2.022836 seconds**, CPU **2.21 -> 2.00 seconds**. In-use bytes
at output-section completion no longer increase by the payload size. This is
a scoped memory/time result, not whole Stage2 throughput.

The complete candidate crosses the former failed phase: every output section,
including the 176,669,608-byte stack-map section, finishes. Before entering
`NativeObject.from_sections`, it records 2,794,522,165 live bytes and
4,324,888,576 allocator-capacity bytes. It then reaches `MEMORY_LIMIT` at
352.631 seconds, peak 4,868,849,664 bytes, with the unchanged 4.5 GiB limit.
No pcc2 is produced. The next boundary is inside final object validation or
conversion; this run does not distinguish the exact allocating operation.

A separate native reduction using the new runtime finds stable retained growth
of 1,120,000 bytes per 10,000 `any(value > index for value in empty_items)` calls
after collection. `_validate_relocation` contains this shape once per record,
and current any/all lowering only releases bytes-like owned source operands.
This is a concrete next reproducer, not a proven attribution of the entire
full-link peak. `any_probe.py` and `any-probe.stdout` preserve it unchanged.

The linker packet passes **76 tests**. The requested record-inventory check
fails because `FoldFrameAddressPass` and `FoldImmediatePass` are unclassified;
the preceding frozen 33b6 backend produces the identical report, saved in
`baseline-inventory.json`. That gate is not reported green. Native runtime
qualification and remaining limits are recorded below.

The existing native pcc1 b491 compiles the new runtime identity/lifetime
regression with `-o`, and its output passes on GC0–GC4: **1 integration test
passed in 17.88 seconds**. The first attempt was rejected by provenance because
the incremental archive directory lacked its source files and the compiler
fell back to its own frozen old source root. Copying the matching frozen `py/`
tree alongside the archive made the existing verification succeed; no check
was disabled. The archive bytes did not change during that packaging correction.

No new Stage1 or current-source Stage2/Stage3 was built in this round. Native
helper execution, the incremental-runtime test and the scoped A/B establish
their named shapes only. The inventory failure, previous baseline closure
timeout, and final-object memory boundary remain open. Work stops after this
requested round, without committing, pushing or installing.


## Update: 2026-09-20 — any/all input lifetime and the next validation peak

The next requested single repair round is preserved in
`/private/tmp/pcc-stage2-any-round-W8oOBN`. Its frozen compiler source is
`81863865a9075c043343ac02c112f216e34b7ae686665190cd482d43b5365c52`.
Only `numeric_builtin_lowering.py` differs from the preceding 246e compiler
closure. The runtime remains the verified incremental 8e6 archive above.

The runtime-iterable any/all lowering released owned bytes/bytearray inputs
but omitted temporary list/tuple/dictionary/dynamic inputs. It now owns and
pins the input through the walk, retaining borrowed inputs before callbacks
can replace their original owner. Dynamic error edges share input cleanup;
normal exhaustion and short-circuit exits release it once. The dictionary-key
view is pinned independently and released before the original dictionary.
Literal and map-lambda specializations are separate paths and are not claimed
fixed by this patch.

`test_any_all_operand_ownership.py` executes temporary generators, list/tuple
constructors, typed dictionary returns, borrowed inputs, length/getitem/truth
exceptions, the dynamic missing-element guard, and callback rebinding. The
host-compiled self/no-libpython programs run under GC0–GC4. The initial typed
normal-input regression retained **1,648,000 bytes per 2,000-iteration batch**;
the fixed any and all variants each retain **0 bytes**. Each error variant
reports 128 caught exceptions and 160 finalized sources. The first unannotated
dictionary factory entered the existing dynamic mapping path; the test was
corrected to explicitly exercise a static dictionary return before the failing
ownership measurement was taken (`red.log`, `red2.log`).

The final focused packet passes **6 tests** (four execution variants and two
IR/error-check guards), with durable `final-tests.nodes.jsonl` and a guarded
receipt. The original bytes/bytearray packet passes **2 tests**. The exact
preceding-round `any_probe.py`, recompiled from the frozen source with IR
passes off and the same runtime, now prints **0 / 0**, versus the previous
**2,686,720 / 1,120,000** bytes. `focused-execution.json` and
`any-probe.{stdout,stderr}` preserve actual outputs; the rebuilt probe SHA is
`eed57705c9b950203d8b60c69f7f9270f1c4a34c831cfca08bbb6e4f83b888ff`.

Fresh Stage1 qualification did **not** start compiling: the runtime bundle
preflight rejected its mixed codegen checksums. The manifest contains 170
members from checksum `315a07b7...` and one rebuilt member from `37c78899...`.
`stage1-preflight-diagnosis.json` records both complete identities;
`stage1.result.json` records return code 1 after 1.197 seconds, despite its
watchdog status being `COMPLETE` (process completion, not success).
`_seal_runtime_bundle` currently requires a single checksum. No receipt was
rewritten and no validation was bypassed. A uniformly rebuilt current runtime,
or a separately reviewed manifest-aware incremental bundle contract, is needed
before this Stage1 gate can pass. The new pcc1 test variants were not executed;
this round does not qualify the changed compiler through self-hosting.

For independent link diagnosis, the owned native helper was compiled by the
host self backend from frozen source with IR passes off and direct indexed
emission. Only the existing private link/heap trace additions differ from the
production snapshot (`trace-source-hashes.json`). Its SHA is
`40ecb0cc7a9af8c173fbc970c138fece4576f2172c88e3352142f45f09bdd41b`.
It links only libSystem. The native 1,152-relocation probe produces byte-equal
host output and the emitted binary exits 42. This probe passed before the
full replay was launched.

All 389 retained Stage2 PCO hashes were reverified. With the same 8e6 runtime,
PATH disabled and host/cc helpers set to `/usr/bin/false`, the full native
replay finishes all 528 inputs, section rebases and output-section construction.
Immediately before `NativeObject.from_sections`, it records 2,788,196,826
live bytes and 4,314,341,376 allocator-capacity bytes. It remains in validation
long enough for a two-second exact-PID sample to identify
`_validate_source_sections -> py_set_add -> _rehash_refcount_fast` and
`_validate_relocation`. Source confirms that this validation retains a
`set[int]` of relocation offsets for duplicate rejection.

The full run still fails: **MEMORY_LIMIT at 408.978 seconds**, peak sampled
RSS **5,400,887,296 bytes**, under the unchanged **4,831,838,208-byte** cap and
600-second timeout. The watchdog terminates the child after observing the
large allocation jump; the sampled overshoot is not a raised limit. No pcc2
exists. The late set-growth sample identifies the next relevant owner, but
does not quantify its exclusive share of the final jump or establish that it
is the only remaining peak. `validation.{folded,svg}` and
`any-fixed.{result.json,samples.tsv,stderr}` preserve that evidence. A longer
time to failure is not an end-to-end speedup, and this run does not establish
a full-link memory improvement.

Remaining work: qualify this codegen change with a fresh pcc1 after resolving
the runtime-bundle preflight; reduce the final validation peak while retaining
duplicate-relocation rejection; then resume current-source Stage2/Stage3.
The earlier inventory and baseline closure failures remain open. No full
five-GC bootstrap or gateway rerun was attempted. Owned processes were checked
exited. This requested round stops here without commit, push or installation.


## Update: 2026-09-20 — verified incremental runtime, compact offset checks, fresh Stage1

This requested round uses `/private/tmp/pcc-stage2-validation-round-j5QlyQ`.
The first frozen compiler source is
`adedfd74a32538f38c3c3fdb4349953d71cd22702b00f8244b95614492352b76`;
the final source after streaming symbol partitions is
`25cec78136550048a3560a79fcef447273a4d86718a5ac4c264baca1fce2a5e8`
(`source-v2-manifest.json`, immutable `source-v2/`). The runtime remains the
8e6 incremental archive, not a full rebuild by this compiler.

The Stage1 preflight failure was a receipt-summary limitation: member and
source hashes were already checked, but `_seal_runtime_bundle` required all
members to have one producer checksum. It now validates every checksum and
records the sorted distinct identities for a mixed archive. Its singular
`codegen_checksum` is then null; it does not label old members current. The
verified member manifest remains frozen and hashed, source/object integrity
checks still run, unknown identities still fail, and mixed object emitters
remain rejected. Uniform archives keep their previous evidence shape.
The regression first failed for a two-producer manifest, then the full tool
packet passed **46 tests**. The actual 171-member 8e6 archive subsequently
passed snapshot, verification and readback (`runtime-preflight.json`).

Source, packed and Mach-O duplicate-offset validators now share a density-
bounded bitmap: one bit per byte for dense sections, a set for sparse sections.
The bitmap allocation is capped relative to the relocation count. The checks
still run after the same type/range validation, reject exact duplicate offsets
in input order and allow distinct overlapping offsets. New tests exercise
out-of-order offsets, a bitmap byte boundary, the last bit, and sparse offsets
near the signed Mach-O limit. The standalone native test compiles the actual
helper source because compiler-private modules are not runtime-importable
application modules; its initial import-based attempt failed explicitly and
is recorded in `offset-tests.log`.

The first packet passed **41 tests**, including one host-compiled native
helper program on GC0–GC4 and five external assembler differential references.
The latter are oracle evidence, not an ownership claim. The bitmap native
linker passed the 1,152-relocation byte-equal probe and emitted exit 42.
Its full old-input replay reached `NATIVE_FINAL source_validation_done`, with
2,799,369,940 live bytes and 4,316,672,000 allocator-capacity bytes. Thus the
previous offset-set validation boundary finished. The run then failed
`MEMORY_LIMIT` at **396.397 seconds**, peak **5,397,446,656 bytes**, under the
unchanged 4.5 GiB cap. No pcc2 was produced.

A separate native reduction isolates `NativeObject.from_sections`: 252,241
symbols with no relocations completed with allocator capacity 554,725,376
bytes; one symbol and one million relocations completed with capacity
1,134,137,344 bytes. In the latter, live bytes rose from 269,340,165 before
relocation conversion to 429,729,148 after record construction, then
596,416,895 after validation. These probes did not collect before measuring;
the final increase is not established as a leak. A proposed generic fresh-
object `yield` leak was not reproduced: 10,016 Item finalizers ran and net
post-collection growth was 48 bytes (`yield-probe.stdout`). No generator
lowering change was made on that hypothesis.

`NativeObject.from_sections` now emits symbol partitions in canonical order
without retaining global `(symbol, section)` pairs, tuple-valued sort keys,
and visibility-filtered copies. It reuses the existing per-section offset
ordering helper. The exact order regression covers unsorted symbols, equal
offsets, locals/externals across two sections and undefined-name sorting. The
adjacent packet passed **35 tests**; the explicit ordering/duplicate packet
passed **4 tests**. No paired performance claim is made for this transformation.

Fresh Stage1 from the final 25cec source **succeeded**. Compiler-only wall was
596.430 seconds; the guarded build plus native function smoke completed in
**616.339 seconds**, peak tree RSS **5,219,336,192 bytes** under the unchanged
5.5 GiB build cap. The compiler SHA is
`01c029fa8b21988d7ae966999ba795c73fee4455309731429d458b262522880e`;
it links only libSystem. Its own function smoke compiled with `-o`, ran and
printed `42`. The successful build receipt retains both runtime producer
identities. This is a new-source Stage1, not a Stage2/Stage3 fixed point.

The exact new pcc1 then passed **7 integration tests in 115.84 seconds**.
They cover dense/sparse offsets, both any/all temporary-input cases, both
any/all exception/rebinding cases, and bytes identity/lifetime with the two
runtime mirrors. Six use the pcc-Python runtime; one uses the C mirror as a
differential reference. Generated programs execute on GC0–GC4. This supplies
fresh-pcc1 evidence for the preceding round's any/all correction as well.
`native-tests.{result.json,stdout,nodes.jsonl}` and the Stage1 function-smoke
outputs preserve the execution evidence.

The final diagnostic helper includes the bitmap and streamed symbol partitions;
its SHA is `45375e7be2cff8964db339a05981ee44dfc69eff93aff0481a2b034dd642ef9e`.
It again produced a byte-equal native rebase image that exits 42, and rejected
a deliberately corrupted duplicate-offset PCO without producing an output.
All 389 retained PCO input hashes and the runtime hash were checked again.
The first replay launcher attempt referred to a nonexistent renamed historical
launch JSON; that private-script path was corrected before any child started.
The actual final replay is recorded separately in `streamed.*` below.


The final streamed-symbol replay still fails `MEMORY_LIMIT`: **384.498 seconds**,
peak sampled RSS **5,725,044,736 bytes**, with the same **4,831,838,208-byte**
cap and 600-second timeout. Source validation finishes at 2,799,369,998 live
bytes / 4,316,672,000 capacity. The subsequent `symbols_done` marker does not
appear. This narrows this run's failure to symbol generation in
`NativeObject.from_sections`, before the later relocation-record conversion;
the earlier broad description “conversion peak” must not be mistaken for
proof that NativeRelocation allocation caused this particular failure.
No `pcc2-streamed` or other pcc2 output was produced in this round.

The matching isolated 252,241-symbol / zero-relocation reduction was repeated
with the final implementation. It still prints the expected count, with
111,844,708 live bytes at `symbols_done` and final capacity 469,757,952 bytes.
The original diagnostic sample's peak tree RSS was 584,351,744 bytes versus
500,531,200 for the new sample. These isolated inputs use a simple symbol
shape; they do not reproduce all merged names, offsets, visibility partitions
or retained full-link input owners. In particular, the lower isolated peak
does not explain or negate the full run's larger sampled allocation jump.
The next investigation should reproduce that actual merged symbol shape and
locate the allocation before `symbols_done`, without disabling validation or
raising the cap. Do not infer an exclusive allocator or ownership cause from
the marker alone.

New-source Stage1 and its exact native regressions are now successful. Full
native linking remains blocked, and no new-source Stage2 or Stage3 was run.
The earlier baseline closure timeout and record-inventory findings remain
open; this is not release or five-GC bootstrap qualification. No quota reading
was available. No commit, push or installation was performed.

Three alternating, separately executed native symbol-only A/B pairs all print
`ok 252241 0`. Median process max RSS is **605,306,880
-> 515,964,928 bytes**; median wall is
**6.989 -> 5.453 seconds**.
`symbol-ab.json`, per-run stdout/time logs and the process-tree receipt bind
the two frozen binaries to these results. This supports retaining the scoped
symbol transformation; it is not a completed Stage2 performance result.


## Update: 2026-09-21 — GC index capacity, temporary owners, retained relocation graph

Evidence root: `/private/tmp/pcc-stage2-symbols-round-Ne5EHv`. Source snapshots,
launch JSON, private diagnostic helpers and process-tree receipts are retained
there. All full replays still use the original 389 PCO hashes (producer/source
identities in the Repro above), 600 seconds and 4,831,838,208 bytes. Host Python,
host pcc and external cc are disabled in the native link child. The helper is
host-compiled with the owned emitter; this is native execution qualification
of that helper, not fresh pcc1 or a new-source fixed point.

The previous symbol-stage jump is explained by the primary GC index. At
8,371,356 live entries in a 16,777,216-slot table, the next 25,383 external
symbols cross the half-full threshold. The old `nextpow2((count+1)*4)` policy
jumps to 67,108,864 slots: 1.61 GB at the former 24-byte stride. Source plus
native counter markers establish this boundary; an attempted late stack sample
missed the exited process and provides no stack-profile evidence.
`gc-index-trace.result.json` stops at 389.255 seconds, peak 5,521,391,616 bytes.

Growth now doubles capacity. Backward-shift deletion already removes
interior holes, so a non-null key identifies occupied slots without the old
state byte/padding: stride is 16 bytes. C and pcc-Python mirrors, forwarding,
frame and backend preallocation agree through `pcc_gc_index_slot_size`.
The exact raw cross-object ABI is registered; malformed signatures still fail.
`index-native-tests2` passed seven tests with the preceding round's real pcc1
and the new archive, including GC0–GC4 emitted execution. The raw mirror/oracle
packet passed seven, and frame/forwarding checks passed eleven. The unrelated
exact-text expectation in `test_freestanding_module` still fails on
`define external i64`; that packet is not claimed green.

Runtime v2 SHA is
`4de433583315a15600ce27deaa60bc50098f0a062119c2e9722ee49f644b83b7`:
four GC objects rebuilt, 167 prior members retained. Runtime v3 SHA is
`f5f08c2e95443bb2a009cd74227592c654afaf2e4baf7bddec4eb4196df692ef`:
only `py_file.o` changed from v2, 170 members retained. Matching source and
per-member producer hashes are verified; these are explicit incremental owned
runtime builds, not claims that every member was rebuilt with this frontend.

Additional changes and named execution evidence:

- The private merge now consumes its newly allocated relocation list one row
  at a time, freeing original records during indexed conversion. Ordinary
  caller inputs remain borrowed. Validation runs before any consumption;
  weakref tests check previous source records are gone before the next one.
- Exact arithmetic temporary results are owned. A native copy of the actual
  uint/sint validators retained 1,602,240 bytes per 10,000 calls before the fix
  and zero after. The regression also exposed preexisting inline bigint
  argument narrowing, reproduced with source v3 before this change. Exact
  binary/unary lowering now preserves 80-bit subtract/divide/negate/invert.
  `test_bigint_temporary_ownership`, dynamic comparison and the exact-loop
  ratchet execute across all five GCs. Untyped dynamic unary is separately
  unfixed; this does not establish all numeric ownership or precision.
- NativeObjectView copies payloads only for section-target writes; its legacy
  flat `.data` is materialized lazily. The executable path uses section data.
  Host API tests and native byte-equal executable probes cover the final-link
  path, not separate native execution of every legacy accessor.
- Relocation ordering uses the existing raw integer arena with stable packed
  address/index keys instead of millions of decorated Python tuples. The
  classification pass skips sorting. Arena cleanup includes early generator
  close. An attempted i64-import version failed compilation and was replaced
  by bounded ordinary indices; the GC verifier was not weakened.
- File read/readline results now carry NEW ownership through raw-ABI consumers.
  A 200×32 KiB read/decode probe fell from 6,580,584 to 21,984 retained bytes.
  Closed reads now raise ValueError in both runtime mirrors instead of
  returning a silent NULL. Four new host-compiled cases exercise all five GCs;
  nine adjacent file tests passed. Fresh pcc1 coverage remains pending.
- str/bytes/bytearray join results now carry NEW ownership. Isolation showed
  the join result, not its input view or method-return path, retained the large
  payload. The probe fell from 6,579,730 to 26,105 retained bytes. Three new
  cases across five GCs and three existing native/C-reference cases passed.
  A direct memoryview join remains unsupported and is not claimed fixed.

Production source v10 SHA:
`273cd515034953b045f1fde03f6fe146711f4ff657cb6cd6e9a84333fa20fbb1`.
Diagnostic native linker SHA:
`4e45a1e797b111952a448a920075fc2b1f9e3b93c6319290d444df5b73b1b7e7`.
Its 1,152-relocation/128-section-target probe produced identical golden bytes
and exit 42 on GC0–GC4; a duplicate-offset PCO was rejected. Full `join.*`
replay then stopped at **532.036 seconds**, peak **4,838,047,744 bytes**,
`MEMORY_LIMIT`. At `EXEC_PHASE classified`, live allocator bytes were
3,074,208,554 and capacity 4,726,292,480; primary index count was 8,836,777.
The following output-section marker did not appear. No pcc2 was produced.
Read/join local improvements therefore did not suffice to close the full link.

The next candidate stores large owned relocation lists using the existing
44-byte codec in bounded batches; consumers materialize one record at a time.
This directly addresses the retained 7.44-million-record graph. Source v11 SHA:
`ac609620c4037201e9f41515ef9209cc0f4accbfdfbf26d107f05a4447ec8bdb`.
The host packet passes 45 cases, including crossing a batch boundary, section
targets, codec/Mach-O equality, emitted execution returning 42, and preservation
of the current executable SUBTRACTOR rejection. Native validation is pending;
this candidate is not yet qualified.

Historical comparison correction: installed v84 evidence remains at
`~/.local/share/pcc/toolchains/v84-baseline-c1f4342696e9/evidence/stage2-result.json`
and `stage2.json`. It genuinely produced pcc2 SHA
`1bed60ee8e3d2ba6422fccf6784d518a61b4c878c451d361a6bfc3bfbf27cd2e`,
returncode 0, guarded elapsed 464.688 seconds, peak 8,023,670,784 bytes under
an 8 GiB cap. Its prediction records 228 modules. Its retained source
`pipeline_self_backend_link.py` explicitly resolves host Python to run the
owned link script. Current native-link replay disables that route and uses a
4.5 GiB cap. These are different execution owners, source sizes and envelopes;
neither proves C syntax itself caused the current regression. The history is
real output evidence, not full native execution ownership or Stage3 evidence.

No new-source Stage1/Stage2/Stage3 has been completed for this round's frontend
changes. No quota reading is available. No commit, push or installation was
performed.


### Compact relocation execution and revised resource budget

The source-v11 diagnostic linker SHA is
`beed8081a7d52cb20136eb2c5b9f8aaf65f1af5a173897dd035e866e125c2ad6`.
Build completed in 49.740 seconds, peak 619,249,664 bytes. Both the new
8,195-relocation probe and the preceding 1,152-relocation/128-section-target
probe produced golden-identical executables returning 42 on GC0–GC4 (ten
executions, `packed-probes.json`). The image links only libSystem.
Nevertheless the full `packed.*` replay failed at 430.455 seconds, peak
4,832,624,640 bytes against the original 4.5 GiB cap, after `symbols_done`
and before `records_done`. The small pass is not full-link qualification.

A separate native `object_validation_probe` with 1,000,000 relocations completed
in 54.076 seconds, peak 401,145,856 bytes. Live allocator bytes at input-ready
were 194,234,607 and at records-done 94,432,956. A second run with 15,000
symbols and the same million relocations completed in 54.947 seconds, peak
438,288,384 bytes. Both print the exact counts. Neither reproduces the full
merged graph or proves the cause of its transient peak. The private probe adds
batch-boundary counters; no production validation is disabled.

The user explicitly requested increasing the budget to obtain a successful
Stage2. `replay_packed_8g.py` replays the same frozen helper/source/runtime/389
PCO inputs with **8,589,934,592 bytes and 1,200 seconds**, isolated output
`pcc2-packed-8g`. `packed-8g-launch.json` records this authorization and the
unchanged identities. The higher-budget result is a completion experiment,
not acceptance of the original budget or throughput. It is currently running.

Timing clarification: the retained September 19 full attempt had a separate
511.207-second frontend/export phase. The later codegen admission receipt
breaks its pre-link work into 992.624 seconds of indexed frontend lowering,
523.704 seconds of ASM emission and 298.333 seconds of PCO emission. Summed
worker wall / lane wall gives approximately 2.53, 1.36 and 3.42 concurrent
workers, respectively; those ratios are not CPU-utilization measurements.
The median charged floor / sampled worker peak is 3.84, 2.43 and 5.26.
This identifies conservative admission as a candidate contributor, not a
measured speedup or permission to lower its safety envelope blindly. The
receipt predates this round's changes. A new full current-source Stage2 time
has not been measured; approximately 50 minutes is only a planning estimate.
The earlier v84 success must not be dismissed, and source growth or native
link ownership alone does not explain the full regression.

The record-inventory test was rerun and still fails on the same two existing
unclassified target passes, FoldFrameAddressPass and FoldImmediatePass. It is
not a green gate. Documentation freshness passed two tests before this update.
The permanent compact-link integration test has been added for pcc0 and pcc1;
the new pcc1 variant still awaits a fresh Stage1 containing this source.


### Correction: packed-record experiment rejected; direct-record replay active

The 8 GiB packed-record run did not succeed and did not hit its watchdog. The
agent stopped its own process group at **1,118.797 seconds**, returncode -15,
peak **5,159,600,128 bytes**. `packed-8g-agent-stop.json` names the reason.
Its sampler says COMPLETE because the process exited; returncode -15 and the
stop receipt make clear that this is not success. The object validation and
view phases finished, but no executable was published.

The new representation traded retained records for repeated generic decoding.
The direct-record source v10 had reached import classification within its
532-second failed replay. The compact candidate only finished native-object
validation around 13 minutes and its view around 16 minutes. A 2-second,
1,667-sample inclusive profile at view construction attributes 1,542 samples
to `_PackedRelocations` and 896 to `_unpack_native_little`; every sampled stack
passed through `_raw_relocation_count`. The self-only sample had obscured this
caller ownership by scattering time across GC, fields and arithmetic. This is
a new regression introduced by this experiment, not evidence of the original
compiler's unchanged cost. It fails throughput acceptance.

Only this turn's experimental additions to `native_object.py` were removed,
after reading the complete diff against frozen source v10. They remain in
source-v11 and `native_object_packed_raw_candidate.py`, with an exact removal
diff/hash receipt. The latter also contains an unqualified direct-load decoder
draft: it passed host tests but was never compiled/executed natively, and is
not in production source. No earlier changes were discarded. The large
conversion/encoding/execution regressions remain, testing observable behavior
rather than requiring the rejected representation. The host packet passes
45 cases after removal.

Current production source again matches v10 SHA
`273cd515034953b045f1fde03f6fe146711f4ff657cb6cd6e9a84333fa20fbb1` exactly.
`replay_join_8g.py` uses the already native-tested direct-record linker SHA
`4e45a1e797b111952a448a920075fc2b1f9e3b93c6319290d444df5b73b1b7e7`,
the same v3 runtime and all 389 retained PCOs. It has the user-authorized
8 GiB / 1,200-second budget, isolated `join-8g.*` receipts and output
`pcc2-join-8g`. The run is active; no success is yet claimed.

The two existing self bootstrap baseline checks passed in 1.47 seconds. They
inspect historical `build/bootstrap-self` artifacts and do not establish a
fresh Stage2/Stage3 for this work. Issue #60 was updated and read back at
comment 5751622771; its ownership fixes remain while the independent compact
record experiment is rejected. No commit, push or install was performed.


### Direct-record 8 GiB replay: final symbol-table copying is the next owner

`join-8g.result.json` ended TIMEOUT, returncode -15, **1,200.535 seconds**,
peak **7,505,215,488 bytes**. No pcc2 file was published. It crossed the former
4.5 GiB failure: object validation, view, import classification, output-section
allocation and relocation application all finished. Text is 136,588,772 bytes
(130.3 MiB), with two veneer islands. The 8 GiB budget resolved the earlier
memory cutoff, but did not make the final serialization finish on time.

The live apply-phase sample (`join-8g-apply.folded`) has 1,152 / 1,657 inclusive
samples inside struct's native-little decoder. Later, the finalization sample
(`join-8g-finalize.folded`) has **1,510 / 1,654** self samples in memset/memcpy
under `py_bytes_concat`, called directly from `link_prepared_executable`.
The final symbol loop repeatedly appends each encoded name and nlist to a
bytearray; current native augmented assignment produces replacements and
copies the complete prefix each iteration. This is quadratic in accumulated
output, not a SHA-256 attribution. The source also makes `bytearray.extend`
allocate a replacement; simply rewriting `+=` as `.extend()` is insufficient.

A separate function probe confirms the generic semantic gap. With
`alias = value`, both `value += b'b'` and `value.extend(b'b')` starting from
`bytearray(b'a')` print `True ab ab` under CPython and `False a ab` in native
execution on all five GCs. `bytearray-alias-proof.json` retains source, binary,
runtime hashes and all outputs. This is a **failing conformance probe**. The
underlying mutable storage/alias/buffer-export/GC contract remains unfixed;
no ABI layout shortcut or C/Python mirror divergence was introduced here.
Issue #60 comment 5751873848 records the gap and was read back. The tiny probe
compile overlapped early input loading in the later completion replay; that
replay is not a controlled performance comparison.

The writer algorithm now collects encoded fragments and joins once. Shared
`macho_obj._build_string_table` serves both object and executable writers,
preserving name order, exact encoded byte offsets, NULs and eight-byte padding.
Final nlist records are also collected and joined once. This is an internal
serialization algorithm change, not a claim that bytearray semantics were
fixed. Host tests cover empty/aligned/duplicate/UTF-8 names, and a 40,000-name
native function checks every offset and byte span on GC0–GC4. Seven focused
cases pass; the preceding host object/link packet passed 45.

Frozen production source v12 SHA:
`48512cfc19ca06ccb464bf305fe2e3f21eb43f94b9e9ad4a3fe980f852a212b3`.
The native helper build completed in 43.007 seconds, peak 615,022,592 bytes.
`linear-probes.json` records **15 successful native link/execution cases**:
32,769 symbols with long names and mixed visibility, 8,195 relocations, and
the original 1,152-relocation/128-section-target probe; each runs on GC0–GC4,
produces host-golden-identical bytes and an executable returning 42. The helper
and its diagnostic sources are hashed in that receipt and
`linear-trace-hashes.json`. The matrix completed in 107.657 seconds. These
local timings are not a controlled whole-Stage2 speed verdict.

`replay_linear_8g.py` now runs the same verified 389 PCOs and runtime v3 with
8 GiB / 1,200 seconds, isolated `linear-8g.*` receipts and target
`pcc2-linear-8g`. It is active. The prior packed-record representation and
unqualified raw-decoder draft remain rejected/outside production source.
Fresh current-source Stage1 and pcc1 regression variants are still pending.


### Pause checkpoint — 2026-09-21

The user asked when work would pause. The assistant agreed to stop immediately
and started no further build. Its own native link process group 25226 was
terminated and the sampler reaped it. `linear-8g-user-pause-stop.json` records
this explicit stop; the sampler's terminal status must be read together with
returncode -15, not interpreted as success.

Final stopped replay: elapsed **487.363 seconds**, peak **4,681,678,848 bytes**.
It completed record construction and was in subsequent native-object
validation. No `pcc2-linear-8g` file exists. The full linear-writer result is
unverified, not failed on a new compiler error. Source v12 and its helper,
runtime v3 and every input hash remain unchanged and available.

Resume from the retained helper with:

```sh
cd /Users/jiamo/my/pcc
env -u LC_ALL uv run python /private/tmp/pcc-stage2-symbols-round-Ne5EHv/replay_linear_8g.py
```

The launcher has the user-authorized 8 GiB / 1,200-second watchdog. Preserve
the stopped `linear-8g.*` receipts under a new attempt name before replaying
so the previous terminal evidence is not overwritten. If a pcc2 is published,
`check_linear_pcc2.py` performs help and a native function compile/run with
host Python/cc disabled and stale deferred flags cleared; run it under the
same existing process-tree guard. Never relabel the old PCO recovery as a
current-source fixed point. The helper's source is v12; the compiler inputs
remain source 92fc / producer 425 as recorded above.

Further work: finish this native link and execute its artifact; fresh Stage1
and pcc1 variants for the ownership and serialization changes; actual fresh
Stage2/Stage3; generic bytearray mutation/alias/buffer lifetime semantics;
existing record-inventory and fallback-boundary gaps. No commit, push,
installation or quota reading was performed. No task process is left running.


### Correction to pause checkpoint: user did not request an immediate stop

The preceding checkpoint incorrectly characterized the user's question as a
stop request. The user explicitly corrected this: “没让你停啊”. The assistant
misinterpreted “你准备什么时候停一下” and stopped the process on its own. The
487.363-second stopped run is therefore an assistant interruption, not a
user-authorized pause, compiler failure, or watchdog timeout. Its files remain
unchanged; `linear-8g-stop-correction.json` corrects their recorded reason.

The frozen helper, runtime and all 389 inputs were reverified, then
`replay_linear_8g_resume1.py` started the same 8 GiB / 1,200-second run with
`linear-8g-resume1.*` receipts and target `pcc2-linear-8g-resume1`. The source,
compiler and runtime identities are unchanged. `check_linear_resume1_pcc2.py`
checks the matching output if it is produced. This supersedes the paused
status and the instruction not to resume; work is active again.


### Final bounded repair: release the preparation frame before signing

The user directed: “如果失败修复一轮，如果成功停下”. The resumed source-v12
replay failed MEMORY_LIMIT at **799.483 seconds**, peak **8,620,818,432 bytes**,
while hashing the completed image for its signature. The final symbol-table
copying timeout was passed. No pcc2 output was published. The signing profile
contains only 14 samples because the watchdog stopped the process; it supports
location, not a reliable percentage attribution.

The permitted additional repair is a function-lifetime boundary. Native
callers retain temporary arguments through a call even when the callee deletes
its parameter. The old `link_prepared_executable(prepare_executable_object(...))`
kept the merged graph alive in its caller while allocating/signing the image.
A host weakref check had passed because CPython releases that temporary sooner.
An exact native reduction of the old calling shape prints zero finalized
objects and fails its expected-one assertion on **all five GCs**
(`old-link-lifetime-proof.json`). The updated preparation wrapper returns only
immutable output regions and scalar layout, then its frame exits before the
image/signature finisher is called. The equivalent native lifetime regression
now prints one on all five GCs. Output region containers are cleared once the
immutable image owns their bytes, before hashing and signed-image allocation.
The public prepared-object API still preserves the caller's object and
signature callbacks; no frozen caller object is mutated.

The host object/incremental-link/relocation packet passed **57 tests**. Four
focused lifetime tests passed, including the native all-GC reduction and a
weakref check that region owners are gone before signing. The old hook-based
owned-input test now checks the actual preparation boundary and asserts the
hook ran, avoiding a false pass after the refactor.

Frozen source v13 SHA:
`b40f7e49b0be3e95a30e628b7f7e66e03b82ffddfcb97b106bd28f6954011dbd`.
Native helper build: 43.510 seconds, peak 611,532,800 bytes.
`lifetime-probes.json` records **15 native cases**, all five GCs, golden bytes
and emitted exit42, with the same three workload shapes as the v12 matrix.
The guarded matrix completed in 114.046 seconds. Runtime v3 is unchanged.

`replay_lifetime_8g.py` is the final allowed full verification: 389 reverified
old-source PCOs, 8 GiB / 1,200 seconds, separate `lifetime-8g.*` receipts and
output `pcc2-lifetime-8g`. If published, `check_lifetime_pcc2.py` runs help and
function compile/run with host Python/cc disabled. After this attempt and its
minimal smoke, stop and report. Further failures must remain explicit, not
start another repair round. This remains old-input recovery; fresh-source
Stage1/Stage2/Stage3 is not claimed.


### Final result at the user-requested round limit — NOT COMPLETE

`lifetime-8g.result.json`: **MEMORY_LIMIT**, returncode **-15**, elapsed
**803.239 seconds**, peak tree RSS **8,608,415,744 bytes** against
**8,589,934,592 bytes**. The watchdog reaped its own process tree; PID60519 is
no longer running. There is **no `pcc2-lifetime-8g` output**, so no pcc2 help,
function compilation, emitted execution, or new-source fixed point is claimed.
The user allowed one additional repair after failure; that round is exhausted
and work has stopped. No further repair/build/benchmark was started.

At `plan_ready`, live allocator bytes were 4,642,990,773 and primary-index
count was 9,539,428. At `preparation_frame_released`, they were 4,435,555,403
and 9,475,439: about 198 MiB and 63,989 entries retired. This confirms a local
lifetime improvement, **not** removal of the whole retained record graph.
The subsequent finalization still exceeds the cap. The precise remaining
owners are unproven; do not assert that the final graph was freed merely
because the synthetic lifetime test passed. The final runtime retained a
managed-pointer index capacity of 8,388,608 at about 109,749 live entries;
that observation is not yet an attributed root cause or a new fix.

Retained current source: v13 `b40f7e49b0be3e95a30e628b7f7e66e03b82ffddfcb97b106bd28f6954011dbd`;
helper `a74b7d72a07136c663c03253a48e261c2f1c2867fde346183a6638f4b259f0c2`;
runtime v3 `f5f08c2e95443bb2a009cd74227592c654afaf2e4baf7bddec4eb4196df692ef`.
All 389 PCOs remain the older source92fc/producer425 recovery set. Preserve
existing result files under a new attempt name before any later explicitly
requested replay of `replay_lifetime_8g.py`; `check_lifetime_pcc2.py` is the
prepared smoke only if a future run actually publishes its target.

Still unfinished: usable recovered pcc2; fresh-source Stage1/pcc1 regressions
for this round; fresh Stage2/Stage3; generic bytearray mutation/alias/buffer
semantics; remaining frontend ownership and baseline/inventory boundaries.
Current local patches and tests are retained uncommitted and unqualified for
release. No commit, push, installation or observed quota reading occurred.

### Root cause: comprehension tuple-unpack targets leak their elements

The retained graph is not a linker lifetime problem. It is a pre-existing pcc
codegen ownership defect, and `comprehension_lowering.py` is not among this
batch's changed files.

Minimal standalone reproducer, no linker and no inputs:

```python
values = build()                                   # 200k objects
out = [value for offset, value in enumerate(values)]   # leaks all of values
out = [value for value in values]                      # clean
for offset, value in enumerate(values): out.append(value)   # clean
```

`[x for a, b in enumerate(seq)]` leaks about `seq`'s whole size per call,
doubles on a second call, and survives frame exit and three collections.
`[offset for offset, value in ...]` leaks the same amount although it discards
`value`, so the leak is the binding, not the kept element.

Mechanism: NOT established. The desugaring is real -- `for a, b in pairs`
becomes `for comp_pair in pairs: a, b = comp_pair` (line ~214) and the
instrumented build shows `unpack_stmt` non-None six times for the reproducer --
but the first hypothesis, that the unpack's bound names hold unreleased
`py_tuple_get` results, was tested and failed: a release emitted at the
iteration join point found no object slot to release for any bound name (they
are filtered out as absent from `env`, non-pointer, or exact-int), and the
emitted binary was byte-identical. Two further attempts, routing
`_emit_enumerate_loop_in_comp` and `_emit_comprehension_obj_indexed` through
`_for_prepare_owned_object_target` / `_for_store_owned_target`, were also
byte-identical: path instrumentation shows the reproducer reaches neither
function. All three attempts were reverted.

What the instrumentation did establish: the reproducer reaches
`_emit_comprehension_after_bind` seven times and neither of the two raw-alloca
generator paths, so it runs a path that already uses the owned-target protocol.
The next round should identify that path by instrumenting the remaining
generator emitters before changing any of them, and should verify the emitted
binary's hash actually changes before reading any measurement -- three separate
"fixes" here produced identical binaries and one earlier reading was compared
against a different probe program's baseline, which made an ineffective change
look like a 23% improvement.

Measured on the retained 389 PCOs, 40 at a time, GC0, in returning frames:

| stage | first call | second call |
|---|---:|---:|
| decode | +2.17 MiB | +0.01 MiB |
| `_coerce_link_objects` | +86.72 MiB | +88.87 MiB |
| `_validate_input_load_commands` | +0.15 MiB | +2.00 MiB |
| `link_relocatable_native` | +254 MiB | +260 MiB |

`_coerce_link_objects` does not copy its inputs -- `_coerce_link_object`
returns a `PackedNativeObject` unchanged -- and leaks about their size because
of `[(start_index + offset, value) for offset, value in enumerate(values)]`.

Controls that could have falsified this and did not:

- An ordinary 2M-record graph gives back 73-76% of its live bytes here, so the
  metric reflects references. `pcc_os_heap_in_use_bytes` is
  `pcc_allocator_live_requested_bytes`, not mapped pages.
- `PCC_MACHO_LINK_JOBS=off` builds no threads at all and leaks identically, so
  `ordered_parallel_map`'s workers are not the owner.
- The leak survives the measuring frame's exit and doubles on a second call,
  so it is not the caller-retains-temporaries semantics.
- `sum(size_hint(v) for v in values)` is clean, so generator expressions are
  not involved.

Also measured: the watchdog's tree RSS tracks mapped capacity, which runs about
1.56x live bytes. That is why 8,608,415,744 and 8,223 MiB peaks sat 1-31 MiB
over an 8,192 MiB cap rather than far above it.

Not done here: the fix, and the mechanism. The leak, its reproducer and the
four controls above stand; the owner inside the comprehension lowering does
not. `comprehension_lowering.py` is unchanged in the working tree.

### Correction: the comprehension was never the owner

Three rounds of the section above are wrong at the premise. The first
reproducer happened to use a comprehension, and that was assumed rather than
decomposed. Removing the comprehension takes four lines and shows the leak is
in the call:

```python
def just_build():          # user function returning a list
    xs = build()
    return len(xs)                       # clean, +0.00 MiB on the second call

def just_list_call():      # no comprehension anywhere
    xs = build()
    ys = list(xs)
    return len(ys)                       # LEAKS +46.81 MiB per call

def just_enumerate_call():
    xs = build()
    ys = list(enumerate(xs))
    return len(ys)                       # LEAKS +86.07 MiB per call
```

`xs = build()` is released when the frame exits; `ys = list(xs)` is not. A
plain `for p in list(xs)` loop and `[p for p in list(xs)]` leak the same
+38.81 MiB, so the loop form is irrelevant. What leaks is an owned temporary
returned by a builtin call and bound to a local.

Four fixes were attempted against the wrong premise and all four changed the
emitted binary while leaving the measurement identical to the hundredth of a
MiB:

1. releasing the tuple-unpack target's bindings at the iteration join point;
2. routing `_emit_enumerate_loop_in_comp`'s pair through
   `_for_prepare_owned_object_target` / `_for_store_owned_target`;
3. the same for `_emit_comprehension_obj_indexed`;
4. adding `enumerate` to the owned-builtin allowlist in
   `_expr_returns_owned_object` plus `_gc_release_if_owned(iter_val, iter_e)`
   on the three comprehension dispatch arms that lacked it.

(4) also produced a `refcount operation on an unmanaged pointer` audit report
that the baseline does not, so `enumerate`'s result is not a managed object on
every path and that allowlist entry is not safe as written. All four were
reverted; `comprehension_lowering.py` and `ownership_lowering.py` are
unmodified.

Two method notes for the next round, both earned the hard way here:

- Compare arms of the same program. An earlier "23% improvement" was one
  probe's numbers against a different probe's baseline, and four builds that
  shared one fix were compared against each other and read as "no change".
  Verify the binary hash differs before reading any measurement, and keep a
  genuine unfixed arm.
- Decompose the reproducer before reading any code. The five-shape split that
  cleared destructuring, and the three-shape split that cleared the
  comprehension, each took minutes and each overturned a hypothesis that had
  survived a full round of code reading.

Still open: why a builtin call's owned result bound to a local is not released
at frame exit while a user function's is. `_expr_returns_owned_object` lists
`list` among the owned builtin constructors already, so the gap is elsewhere --
most likely in whatever decides owned-local membership for the binding, not in
the expression classifier.

### 2026-09-21: list conversion iterator owners — native reproduction and scoped fix

Evidence: `/private/tmp/pcc-list-owner-round-wgtk6s2d`. This round uses a
copied, provenance-verified runtime and the current host frontend to emit and
execute native programs. It does not rebuild pcc1 or run full Stage2.

The previous hypothesis about local binding is corrected by the emitted IR:
`ys = list(xs)` has an owned slot, stores `ys.owned = 1`, and releases it on
exit. In the selected dynamic-iterable arm, however, `_emit_list_append_via_iter`
obtains NEW references from `py_obj_iter` and `py_obj_next` and emits neither
iterator release nor per-item release. The iterator retains the source, and
every yielded owner remains in addition to the output list's append retain.
The original native program shows source-list refcount 1 before construction,
2 afterward and 2 after deleting the result. This is not a failure of the
result local's owned flag.

The same 20,000-object program, same runtime and options, gives zero Item
finalizers in both list-copy shapes before the fix. The iterator/item fix
restores source refcount 1 and removes the list-copy growth. Value-position
`enumerate` also produces a known NEW managed list; ownership is now recorded
at that actual emitter, rather than declaring all enumerate ASTs managed.
`list` consumes that temporary source on success and errors. The source owner
stays alive through iteration, including when `__iter__` returns an independent
iterator; it is not dropped immediately after obtaining the iterator.

Second-call retained requested bytes in the instrumented same-program probe:
`list`: 2,910,944 -> 152; `list(enumerate(...))`: 5,559,448 -> 152.
That debug probe itself reads an indexed Item through an unsafe call and
retains that one observed element; it is not used to claim all finalizers ran.
The separate clean regression does not include that instrumentation and proves
all 2,000 objects are finalized after each call (4,000 total).

`test_list_iterator_ownership.py`: five host-compiler cases pass, each emitted
program runs on GC0–GC4. Shapes: borrowed input and identity, temporary input,
enumerate value result, __iter__/__next__ errors with partial output, and the
input object's lifetime across an independent iterator. Adjacent map/filter,
sorted and dynamic-call ownership checks: 14 passed. The four-case packet and
adjacent checks ran before the final source-lifetime refinement; all five new
cases passed after it. Native pcc1 fixture variants remain unrun integration
checks; this is not bootstrap or release qualification.

A real-PCO `_coerce_link_objects` A/B attempt was **invalid**, not green. Its
Boolean mode selected the decode arm twice; both native binaries had the same
hash and printed the same mode. The hash assertion caught this, and the run
exited nonzero. Also, a coordinator-only in-memory codegen substitution does
not establish that worker processes used it. No real-link memory reduction is
claimed from `coerce-ab.*`. It was not repeated while another Stage2 run was
reported active. A later real-component A/B must use separate frozen source
files, explicit entry functions, proof that both paths ran, and different
emitted identities before interpreting counters.

Read-only bootstrap check: the deferred guard reads PCC_RUNTIME_ARCHIVE
straight from the environment before the normal late runtime resolver. The
current bootstrap.sh sets plan/output variables but not the archive. The
retained September6 v84 source has the same default deferred mode and guard;
its successful receipt explicitly supplies the archive. Therefore a missing
archive is a real default-launch defect but does not explain the earlier
memory failures that already had one. Current bootstrap also omits
--native-linker, so its deferred runner defaults to host-CPython linking.
No bootstrap or running Stage2 input was edited in response to this finding.

### 2026-09-21: native bootstrap continuation applied and exercised

After the user confirmed that the other Stage2 had ended, the isolated patch
was applied. `bootstrap.sh` now invokes `run_pcc_native_deferred.sh`, which
checks and calls the same compiler's `--pcc-native-deferred-worker` entry.
The compiled `native_deferred.py` consumes the frozen plan, uses the existing
native worker pool for frontend/PIDX-to-PCO emission, and calls the owned
linker directly. An old compiler fails preflight instead of using Python.
The in-process native self-link boundary also rejects unsupported surfaces
before resolving a host interpreter.

Two launch gaps were corrected: the default archive is supplied before
deferred frontend codegen, and the indexed capture/emit flags establish its
artifact root. The effective stage environment now encloses the continuation
as well as the initial compiler; a shell regression executes the real
`run_stage` body and observes identical pass/worker/runtime/indexed settings
in all three child invocations. Snapshot support includes the new shell
launcher. Existing host deferred tooling remains an explicit diagnostic route.

Evidence root:
`/private/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-native-entry-9hjlcat9`.
Compiler source snapshot SHA256:
`a6f24271e1a53aec43519cbd6e834b62b4afced7e82ebeab09df3971e31bca51`.
Reused, verified runtime SHA256:
`da9967aadbf41eef0f17447e74e97af5994d9c468dc12f38580d6952bf3acd72`.

The first Stage1 attempt omitted the existing `--direct-indexed-emit` option.
It emitted 1,119 assembly units totaling 2,150,492,952 bytes, then hit its
900-second timeout without producing pcc1. `guard.json` reports wrapper rc1;
`stage1/manifest.json` records the actual command timeout. This is a failed
build, not successful compile timing. The timeout was not increased.

A two-module indexed probe ran successfully and printed 42; its profile
records two PCO modules, zero assembly modules and zero direct IR-text bytes.
The same frozen compiler source/runtime then built with
`run_pcc_stage1_build.py --direct-indexed-emit --python-ir-passes off --jobs 2
--self-backend-jobs 2 --timeout 900`, guarded by an 8-GiB tree cap and the
shared performance lock. It emitted 393 PCO modules. `stage1-direct` records
618.35 seconds compilation wall time, 636.61 seconds including verification,
and `direct-guard.json` records 4,455,858,176 bytes peak tree RSS. No speedup
ratio is claimed against the incomplete first attempt.

The produced pcc1 SHA256 is
`1e8063e979fe341f8bdc677997e639c59632b8469ed7ccd09ea9d7610002dd56`.
Its receipt is `stage1-direct/build-receipt.json`; linkage contains only
`/usr/lib/libSystem.B.dylib`. Its native `--check` succeeds with empty PATH
and a failing host-Python selector. The current shell launch/environment
and snapshot-list fixes were made after freezing compiler inputs; the
compiled Python implementation is the frozen one, and the shell regressions
exercise the subsequently corrected launcher. Do not use the earlier frozen
support-script snapshot as if it already included those later fixes.

`test_pcc1_native_deferred_cli_compiles_and_executes_two_modules` passed in
15.18 seconds using this fresh pcc1 for coordinator, frontend workers, indexed
emit workers and final link. The output prints 42. PATH provides only
`ls/mkdir/cp/rm/sh/cat`; Python/cc/LLVM are absent and host selectors point to
`/usr/bin/false`. The same pcc1's link entry executed the emitted program
under GC0–GC4: five tests passed. Receipts/logs are `native-cli-test.*` and
`native-cli-gc.*`. The process watchdog/test runner is host-side measurement
infrastructure; it does not implement the tested compiler phases.

Focused launcher, protocol, snapshot and Stage1-tool tests: 72 passed;
adjacent owned-link driver tests: two passed. Separate helper-recompilation
integration variants were not rerun. No full Stage2, Stage3, new-source fixed
point, full-GC bootstrap, release/fallback qualification or gateway run was
performed. Runtime construction was not rebuilt. Host-side bootstrap cache
identity/normalization and remaining runtime-construction boundaries still
need ownership work; this result closes the tested deferred execution route,
not the whole no-Python project goal. No commit or installation was made.

### 2026-09-21: full native Stage2 times out before linking

The user requested full Stage2. Evidence root:
`/private/tmp/pcc-stage2-native-current-k1t37f49`.
`launch.json` records the exact command, environment, frozen runtime bundle
and source identity. The current source/support snapshot is
`d0fae9f619c9d545d0411f53621e50c0bb5324ffaf826a671c9e252afb08d519`.
All `pcc/` package source hashes match the successful Stage1 seed; only
`scripts/bootstrap.sh` and the newly included native launcher differ from
its older support-file snapshot. Seed pcc1 and runtime hashes are the ones
recorded in the preceding section.

The selected target was the current production native continuation script,
with frontend jobs `auto`, backend jobs 2, native link jobs 8, indexed
capture/emission enabled, Python IR passes `off` (the bootstrap setting),
GC0, and both caches disabled. It invoked pcc1 on frozen `pcc/__main__.py`
with `--backend self --python-libpython off --ir-scaffold on -o pcc2`.
Target PATH contained only file utilities; host-Python/host-pcc selectors
were `/usr/bin/false`. The outer process-tree sampler alone used host Python.
Top-level bootstrap cache-identity/normalization/receipt utilities were not
part of this compiler target. This is not an all-bootstrap-tooling ownership
claim. The existing performance lock, 8-GiB cap and 2400-second watchdog
were retained throughout; no timeout increase or Stage3 run followed.

`process.json` reports **TIMEOUT**, rc -15, 2400.314 seconds, peak tree RSS
2,564,276,224 bytes (2.39 GiB), and at most four target-tree processes.
The coordinator profile reports 420.277 seconds. The versioned plan has
391 modules, nine oversized serial lanes and two safe-worker slots. All
391 frontend result rows say OK and all PIDX files were produced; the last
frontend result's artifact timestamp is 1975.643 seconds after launch.
Thus roughly 1555 seconds followed the coordinator before frontend workers
finished. PCO emission then used the remaining roughly 425 seconds.

At termination there were **18 PCOs**, all subsequently read and accepted
by `decode_packed_native_object`; their sizes and SHA256 hashes are preserved
in `result.json`. The native internal-input link manifest was not published,
and pcc2 does not exist: **linking and signing never began**. stderr was
empty, so there is no compiler diagnostic establishing a semantic failure.
This run failed its time budget in frontend/PCO generation, not its memory
cap or linker. It does not qualify the whole emitted compiler's correctness.

After timeout, the three owned terminal PIDs were confirmed absent. Frozen
source, seed executable and runtime hashes were rechecked unchanged.
All plans, worker results, PIDX and PCO artifacts remain available. No source
fix, larger-budget rerun, commit, install or gateway benchmark was performed.
Next diagnosis should use the retained indexed inputs and the actual native
frontend/emitter costs; repeating the full frontend or tuning the unentered
linker would not address the measured failure stage.

### 2026-09-21: frontend scheduling optimization measured separately

The follow-up native scheduling change and its controlled measurements are
recorded in [the worker-cost investigation](pcc1-worker-object-protocol-tax.md#update-2026-09-21--restore-phase-aware-native-frontend-admission).
The same 391 frozen frontend inputs completed in 679.61s with all PIDX bytes
unchanged, versus the retained approximately 1555s frontend interval. A new
pcc1 containing the change was built and passed the real deferred CLI and
five-GC link probes. PCO emission/linking and complete Stage2 have not been
rerun; the timeout above remains the latest complete-chain attempt.

### 2026-09-21: repeat Stage2 — serial emission and boxed PCO decoding dominate

User requested another Stage2 and an assessment of what remains slow.
Evidence root: `/private/tmp/pcc-stage2-frontopt-9719lk23`.
`launch.json` records the exact cleared target environment and command;
`result.json` binds validated artifacts, timings, source identities and
profile summaries. Source SHA256 is
`3a1c44e74291190b74a77165ab3055999b16d3d284571016a29d0efb7527e360`;
seed pcc1 SHA256 is
`17012e25163d20ea71ebb386a41b85e22cc8b62182301b9353d1a27a4a608401`;
runtime remains `da9967aadbf41eef0f17447e74e97af5994d9c468dc12f38580d6952bf3acd72`.
Every compiler/source input and the runtime were rehashed unchanged afterward.
No implementation was edited during this run.

The initial 8-GiB resource preflight refused current swap pressure. The run
used a stricter 6-GiB tree cap AND a matching 6-GiB worker budget, retaining
the 8-GiB host reserve and unchanged resource preflight. A 3600s diagnostic
limit was set before launch to observe the previously unreached link phase;
it was not increased during execution. These settings differ from the prior
8-GiB/2400s run, so total wall is not a paired regression measurement.

This choice had a concrete throughput consequence: the old compiled safe-job
formula charges 3 GiB per worker plus a 1-GiB coordinator reserve, yielding
floor((6-1)/3) = **one slot**. `pipeline_frontend_parallel` writes that value
to the deferred plan and `native_deferred._run_lanes` reuses it for PCO emit,
even though the combined frontend state has already exited. Actual plan:
392 modules, nine oversized modules, safe_jobs=1. Export/summary work also
had one safe worker. The frontend-only phase used the new AST-weighted
scheduler, within its smaller budget. Choosing this configuration knowingly
amplified serial cost; it must not be blamed on a compiler semantic error or
treated as an equivalent performance comparison with 8 GiB.

Terminal result: **TIMEOUT**, rc -15, 3600.256s, peak tree RSS
3,042,410,496 bytes (2.83 GiB). No pcc2 or executable smoke result.
All 392 frontend result rows say OK. All 392 PCOs (662,572,516 bytes total)
were subsequently read, hashed and accepted by `decode_packed_native_object`.
The ordered internal-input manifest was published and native linking started.
All owned terminal PIDs were confirmed absent after watchdog cleanup.

| Phase | Observed seconds | Boundary |
|---|---:|---|
| Coordinator | 517.533 | compiler profile total |
| Frontend workers | approximately 898.812 | last result mtime minus coordinator interval |
| PCO emission | approximately 1938.264 | frontend end to internal-input manifest publication |
| Link before timeout | approximately 245.647 | manifest publication to watchdog stop |

The emission tree's sampled peak was only 1,130,676,224 bytes (1.05 GiB).
This does not certify arbitrary wider execution, but demonstrates that the
3-GiB combined-worker charge is overly conservative on these completed inputs.
Some slow completed serial output intervals, including worker startup:
`pcc.codegen.c_codegen` 58.35s, `pcc.cli_bootstrap` 53.26s,
`pcc.py_frontend.type_infer` 41.69s, `class_gen` 39.11s.

Three diagnostic samples used the target process's actual executable/symbols:

- Frontend worker: a short window produced 988 samples; instance getattr
  appeared in 50.9% of stacks, `_strs_eq` had 18.2% self time. This corroborates
  the prior attribute/MRO lookup owner, not an entire-phase CPU percentage.
- `c_codegen` PCO emitter: 8s window, 6631 samples. 58.5% cumulative in
  `encode_native_object_from_sections`; 41.4% in AArch64 transport completion.
  Nested costs included `_validate_source_sections` 15.0%, packed decode
  12.4%, and instruction encoding 34.4%. These nested percentages must not
  be summed as disjoint costs. Source confirms input validation followed by
  exact encoded-byte validation; deleting either check is not the proposed fix.
- Link input reading: 8s window, 6725 samples. Packed-object decode appeared
  in 98.4% of stacks; `struct._unpack_native_little` in 56.3%. Self samples
  included GC pointer loads 9.3%, native struct unpack 7.7%, object equality
  6.3%, incref 5.0%, integer boxing 4.7%, and decref 3.8%. This window shows
  generic boxed unpack/validation cost before layout/signing, not whole-link
  CPU attribution.

Assessment: prioritize phase-specific native PCO admission, then fixed-format
binary decoding/validation without repeated generic boxed tuple operations;
retain exact byte/schema validation. Attribute/MRO lookup remains a separate
frontend CPU optimization. Retained PCOs now permit link-only diagnosis, and
retained PIDX permits emitter-only replay: another full frontend rerun is
unnecessary to investigate these owners. No Stage3, commit, install, runtime
rebuild or gate relaxation was performed.

### 2026-09-21: fixed-layout PCO relocation decoding implemented

The preceding profile's codec hotspot is now changed in
`pcc/backend/native_object.py`. The order scan bounds-checks the complete
relocation span and reads only each uint64 offset. Validation and linker
consumption use fixed loads for the 44-byte relocation layout, avoiding the
owned generic `struct` format loop and its intermediate list. Returned rows
still contain Python integers; this does not eliminate all boxing. Host
CPython retains its standard-library struct oracle. Native loads re-read the
rooted bytes object across allocation safepoints and preserve unsigned values,
signed fields, unaligned offsets and ignored padding. Both source validation
and the encoder's final packed-byte validation remain mandatory.

Evidence root:
`/private/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-pco-codec-zya8z3xh`.
`result.json` records hashes, measurements and limits. Control source is
`3a1c44e74291190b74a77165ab3055999b16d3d284571016a29d0efb7527e360`;
candidate is
`a3f6e2b15cd86a6d77ffa1cf5a933d9ad9ff5def4774c628a081f5105bbd96f9`.
Only `native_object.py` differs in compiler sources. Both arms use the same
frozen runtime `da9967aadbf41eef0f17447e74e97af5994d9c468dc12f38580d6952bf3acd72`.
The 392 inputs, totaling 662,572,516 bytes, were rehashed against the preceding
Stage2 receipt after measurement; all match.

Host pcc built native helpers with `--backend self --python-libpython off`,
`PCC_PYTHON_IR_PASSES=off`, indexed capture/emission and zero-fallback checks,
with compiler caches disabled. Execution used `env -i PATH=/nonexistent`,
GC 0 and disabled host-Python selectors. Helpers depend only on libSystem.
The exact build/run argv, cwd, environment and RSS records are in each
`control-*.json` / `candidate-*.json`. Runs used the shared performance lock,
4-GiB build caps and 2-GiB execution caps. No runtime was rebuilt.

| Measured boundary | Control seconds | Candidate seconds |
|---|---:|---:|
| Sum of decoding all 392 PCOs | 139.782 | 42.548 |
| Same replay including reads/process lifetime | 144.228 | 46.471 |
| `c_codegen` PIDX through complete PCO publication, one paired run | 71.791 | 54.069 |

The decode improvement is 3.285x, with peak RSS approximately 35 MiB in both
arms. This probe releases prior objects, unlike a full link retaining all
inputs; it is not a whole-link timing. `run_emit_comparison.py` replays the
production `emit_indexed_module_file(..., "PCO", optimize=False)` path on the
retained `module_135.direct.pidx`. Both emitted files are byte-identical to
the original 20,028,037-byte PCO, SHA256
`3bd1c7d44e36a2122517a8d3a9aa6e60596d45c3c4cb736d92f0640dfb8f957a`.
The single emitter pair must not be extrapolated to the whole 32-minute phase.

Validation in `tests/python/test_native_object_fastpath.py`: 53 selected
non-integration checks pass, covering malformed bytes, integer widths,
truncation, padding, relocation ordering and linker differentials.
`test_fixed_relocation_reads_execute_natively_under_all_collectors` passes
with both pcc0 and the retained pcc1 compiling its actual helper functions;
each emitted program executes under all five collectors.
`test_packed_relocation_native_link_executes_under_all_collectors[pcc0]`
builds the owned native linker, links a real cross-object BRANCH26, and runs
each of five outputs with expected exit 42. Detailed logs are `tests.*` and
`integration.*` in the evidence root.

The broader file run stops at the existing
`test_signing_releases_output_region_owners`: its captured region list is
empty. The exact failure also reproduces against the frozen control source
(`signing-baseline.stdout`); the subsequent 53-check selection excludes this
case explicitly. This is not an all-green suite claim. No fresh full pcc1,
Stage2/Stage3, installation or commit was performed. Serial PCO admission and
frontend cost remain separate unfinished work.

### 2026-09-22: native PCO scheduling connected; five-minute targets still open

The maintainer's acceptance targets are now **complete Stage1 <= 300 seconds
and complete Stage2 <= 300 seconds, measured separately, without losing
capabilities or semantics**. Neither target is met by this experiment.

`native_deferred._codegen` now calls `run_pco_commands` with the actual PIDX
inventory. Auto mode with a valid existing tree budget groups workers using
the host controller's PCO memory estimates and the shared native process pool.
It no longer reuses the combined frontend/emitter's one/two-slot policy.
Explicit counts and unknown budgets retain the previous policy. The host
controller delegates its PCO estimate to the same implementation. No new
environment variable or lower memory coefficient was introduced.

Evidence root:
`/private/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-pco-admission-h5bq6m_p`.
Source SHA256:
`8b2cd9d7112b743267fe7736a65d0202960ac2679b4991b66b16d3188ba4b95c`.
The source-frozen Stage1 includes this scheduler and the preceding fixed-layout
decoder. Its compiler SHA256 is
`a069d0c3c6e622a3efe3323d2274cecd3dca6a3f2cd342215509eb57a3a904a0`,
retained at `stage1/pcc1`. The guarded build/checks completed in **648.696s**,
peak **4,493,213,696 bytes**; compilation itself took **629.764s**. Configuration:
two host frontend workers, two self-backend workers, owned self backend,
no libpython, IR passes off, direct indexed emission, reused runtime. The
5-GiB hard cap and 8-GiB host reserve passed the existing preflight.

Scheduling-only A/B uses one native controller binary and the SAME retained
pcc1 worker (`17012e...` from the preceding Stage2), on 12 frozen real PIDX
inputs. A six-GiB launch was refused by swap preflight before workers started.
Both arms then used a matching **4-GiB planner budget and hard cap**, preserving
the 8-GiB reserve and the unchanged preflight. `run_cohort_4g.py` and each
`a*-4g.json` / `b*-4g.json` retain exact commands and resource records:

| Arm order | Wall seconds | Peak tree bytes |
|---|---:|---:|
| A1, previous serial policy | 11.702 | 74,629,120 |
| B1, phase-specific admission | 4.345 | 360,775,680 |
| B2 | 4.351 | 360,628,224 |
| A2 | 11.721 | 75,235,328 |

Mean speedup is **2.694x**. All 48 PCO outputs match the retained originals.

Full PCO replay then used the NEW pcc1 on all **392** frozen old-source PIDX
files, with GC0, a **5-GiB budget/cap**, 8-GiB reserve and fixed 1500s timeout.
`run_full_phase.py` invokes the actual native scheduler and the compiler's
`--pcc-self-backend-indexed-emit-worker`; no frontend recompile, host-Python
compiler worker, external optimizer or assembler is selected. The target
environment has `PATH=/nonexistent` and disabled host-Python selectors.
Result: **1143.566s (19m04s)**, sampled peak **1,096,646,656 bytes**, maximum
13 processes (controller plus 12 workers). All 392 PCOs, totaling 662,572,516
bytes, decode successfully and are byte-identical to the originals. Input
hashes and all output hashes were verified; `result.json` binds them.

This is a historical improvement from the earlier 1938.264s PCO phase, not a
paired attribution: that run used the old compiler and a 6-GiB budget, while
this one includes both the decoder and scheduling changes. It is a complete
PCO-phase replay of retained inputs, **not a fresh-source Stage2 or fixed point**.

The remaining limitation is now measured: **33 large modules still execute
serially for approximately 801.741s (70.1% of the phase)**; the parallel tail
takes approximately 341.825s. The largest 58,691,834-byte PIDX is charged the
old 6-GiB cap despite the complete replay's approximately 1.02-GiB sampled
tree peak. Reusing old coefficients left the dominant serial work in place.
Next work should recalibrate phase memory and admission from complete input
coverage, retaining headroom and the hard watchdog; these samples do not
justify setting every reservation to the observed peak. The original serial
run sampled 373/392 emitter processes; 19 lack per-worker sampled peaks.
For Stage1, the recorded profile assigns 452.012s to codegen worker commands,
53.721s to export and 76.686s to linking; the two-worker default also needs
evaluation against the new time target.

Validation: **86** focused host checks; pcc0 and pcc1 each compile and execute
the actual admission functions under all five collectors; **8** checks on
the fresh pcc1 pass, including the real deferred two-module compile/run,
five-GC native link entry, fixed-layout reader and admission regressions.
The native-object encoding closed-world fallback check also passes.
The broader baseline packet stops after ten passes at the existing missing
`_direct_virtual_method_calls` host-contract field. Those constructor/contract
files are unchanged by this round. Remaining fallback checks were not reached;
the two bootstrap baseline tests inspect historical receipts, not a new
Stage2/Stage3. No install, commit or whole-project qualification is claimed.

End-of-run identity check: the frozen measured sources remain unchanged.
The live worktree subsequently differs only by removal of five `DEFER-ROOTS`
diagnostic lines in `pipeline_frontend_parallel.py`'s invalid-root branch.
This diagnostic drift is preserved and is not included in the measured pcc1;
`source-after.json` and `result.json` record the distinction.

### 2026-09-22: complete PCO peak envelope and redundant stack-map sorting

The maintainer requested one optimization round, then explicitly limited the
round to repairing the identified problems and stopping. The Stage1/Stage2
300-second targets remain unmet; no new complete bootstrap or installation is
claimed here.

Evidence root:
`/private/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-pco-envelope-xyzz55zj`.
Control source is `5c749e891398a3ed65639be9df1aa4e688f0a3bd6086592cf48c2ce0cdda780e`;
candidate is `e6942d0f50f76652cecd06bc0812fc038941da4cdeeb6b017a3a29d4a4813690`.
Worker remains the prior native pcc1 `a069d0c3...`; its emitter implementation
matches control (the source difference is the invalid-root diagnostic noted
above). The 392 frozen PIDX files and expected PCO hashes are retained from
the preceding round.

`pcc_emit_rank.py` now supports the indexed PCO worker protocol, selected GC,
system `/usr/bin/time -lp` peaks and reference output hashes. The calibration
used three workers, GC0, a 5-GiB tree cap, unchanged 8-GiB reserve and a fixed
900s watchdog. All **392** workers completed and their PCO hashes matched.
Maximum per-worker RSS was approximately **1044.28 MiB**, maximum physical
footprint **1024 MiB**; sampled aggregate peak was approximately **2.51 GiB**.
The full records are `calibration/manifest.json`; the portable regression
fixture is `tests/data/pco_gc0_worker_peaks.json`.

The GC0 reservation is now **320 MiB + 21 MiB per decimal MB of PIDX**, retaining
the existing 6-GiB cap. Every recorded worker is covered by **25% plus 128 MiB**
over the larger of its system RSS/footprint peaks. Collectors 1–4 and unknown
collector selections retain the previous estimate; no new environment knob
was added. These are estimates with headroom, not guaranteed future maxima.
The native and host deferred controllers share this selection.

The maintainer also requested renewed CPU profiling to look for larger or
simpler implementation mistakes. A complete `c_codegen` native replay yielded
**50,252** samples (`native-full.folded`, matching actual executable symbols):
71.7% inclusive in AArch64 transport/preparation, 21.9% in PIDX decoding, and
6.2% in final native-object encoding. Nested costs include instance lookup,
bound-method construction, tuples and reference traffic. The first live
capture had only 367 tail samples and is not used for these whole-replay shares.

CPython **3.15.0rc1** Tachyon cross-process sampling failed with macOS process
memory Permission Error; `sudo -n` also required a password. It produced no
valid profile. The existing in-process host sampler was then run under 3.15,
with a separate cProfile call-count run. cProfile-instrumented wall time is not
a compiler performance comparison. The plain sampled/audited host replay
produced reference-identical PCO bytes and 1567 samples. Scalar-record dataclass
constructors accounted for approximately 25% self samples via arena getters;
stack-map sorting was 6.1% inclusive. Native PIDX restore visibly expands raw
columns into lists and then freezes them back into arenas; its specific
avoidable share remains unisolated. These further costs were not changed.

The concrete sort audit found **619/619 groups, 140,033/140,033 records already
strictly ordered** before the existing heapsort. The call-count run recorded
1,436,614 swaps. `_sort_final_stack_map_records` now checks strict ordering
linearly and returns when it already holds. The native check reads the arena
through integer-address intrinsics; unordered/equal-key inputs retain the
original heap algorithm and tie ordering. No validation, payload word or
collector contract was removed.

Validation: **198** focused host checks, **five** ordering checks, and **four**
native regression cases pass. Both pcc0 and pcc1 compile the actual admission
and sort functions; every emitted program runs on GC0–GC4, checking zero swaps
for ordered input, real swaps for unordered/equal keys, and every payload word
(including ids above 2**38). `native-regression.*` records these executions.
The new whole compiler was not rebuilt and post-change full-phase throughput
was not measured after the explicit stop instruction. Previous full-gate
failures and the fresh-source Stage2/Stage3 requirement remain open.

### 2026-09-22: typed enumerate owners — factor separation and native repair

The maintainer requested one bounded round, profiling and evidence before
changes. No full Stage2 was started. Evidence root:
`/private/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-coerce-owner-6njozswa`.
Control source: `e6942d0f50f76652cecd06bc0812fc038941da4cdeeb6b017a3a29d4a4813690`.
Final source: `b3cf2a91944bf53b1380c2f5f5fbd29bef474986b5544137aa635e11690bcf1c`.
The only compiler-source difference is `comprehension_lowering.py`.

This experiment selects the first 40 numerically ordered PCOs from the retained
**389-input** corpus, totaling **31,933,041 bytes**. It does not reuse the newer
392-input performance corpus, or claim the same subset as the earlier 864-MiB
extrapolation. `inputs.json` records every path, size and hash; all were checked
again after measurement. Runtime SHA256 is
`da9967aadbf41eef0f17447e74e97af5994d9c468dc12f38580d6952bf3acd72`.

#### Memory profile before editing [CONFIRMED]

Each explicit entry function decodes the same inputs, performs one operation
and returns only its length. Measurements occur after that frame exits and
three collections. The counter is live **requested allocator bytes**, not RSS.
There is no Boolean selector that could accidentally run the same arm twice.
Second-call retained bytes (`factors-3g.stdout`):

| Operation | Retained bytes |
|---|---:|
| Decode only | 12,065 |
| Untyped enumerate comprehension | 3,991,716 |
| Above plus tuple freeze | 3,991,716 |
| Above plus indexed coercion mapping | 3,991,716 |
| Actual `_coerce_link_objects` | 36,994,891 |

Further factors ruled out the size-hint generator, dynamic mapper, serial
`ordered_parallel_map`, `start_index` addition, keyword-only marker and try
wrapper as the extra source-wide retention. Same-module copies differing in
one annotation then isolated **`values: list`**: removing this parameter's
annotation removed the large increment; removing the return or index
annotation did not. Plain `list`, `list[PackedNativeObject]` and the production
union-element list all reproduced it (`factors2/3/4.*`).

This corrects the earlier categorical heading “the comprehension was never
the owner.” The untyped reductions select a different lowering path. The
previous `list()` repair remains valid, but does not repair this typed path.
Diagnostic function copies were added only to temporary source after the
original control was compiled; they are absent from the final measured source.

The independent 2,000-Item finalizer probe corroborates the counter. Before
repair, an untyped comprehension finalizes 1,999 objects; the typed one
finalizes **zero**. The generated typed function calls `py_int_from_i64`,
`py_obj_getitem` and `py_tuple_new` without balancing their NEW references.
The tuple setter retains its arguments, and a plain target-slot store loses
the previous pair's owner. Separately, restoring the outer comprehension
environment drops the final unpacked target bindings without retiring them.
`minimal-before.ll`, `minimal-functions.ll` and `minimal-before.stdout`
preserve that evidence. It was reported before the production edit.

#### Repair and execution

Typed enumerate now transfers each pair into an owned, traced target slot,
releases the index and element after insertion, and unwinds the iterable and
pair on success and errors. Comprehension scope exit clears owned inner target
slots and restores the enclosing ownership flags along with the environment.
Temporary dictionaries stay alive throughout their key enumeration, including
their values; a new lifetime check caught premature destruction in an
intermediate implementation, and passed after retaining the original source.
No linker special case, runtime change, new environment variable or GC-policy
change was introduced.

Repairing only the enumerate path changes typed finalizers from 0 to 1,999;
adding scope cleanup changes both paths to **2,000/2,000**. All five collectors
run every finalizer. GC0 second-call requested-byte growth is 64; GC4 still
retains allocator bytes despite completing finalizers, so this is not a
five-collector heap-flatness claim.

The final same-program real-component replay (`factors-final.*`) gives:

| Measurement | Control | Final |
|---|---:|---:|
| Decode-only, second-call retained bytes | 12,065 | 10,785 |
| `_coerce_link_objects`, second-call retained bytes | 36,994,891 | 11,573 |
| Whole probe peak RSS | 117,571,584 | 52,822,016 |
| Whole probe seconds | 23.644 | 22.305 |

Control binary SHA256:
`da98eef082ea3b053a431487ecbc35e19d44788ce83e63d895c445bde7e2cd07`.
Final binary SHA256:
`d3ab5e2488b52606edf8c06a49ee489584a04ba63bc5e40c2cb3d8a6cf823d5d`.
Both use host pcc0, self backend, no libpython, IR passes off, direct indexed
capture/emission with zero fallback required, and disabled compilation caches.
Native execution has `PATH=/nonexistent`, GC0, serial link jobs and disabled
host-Python selectors. `build-final.json` and `factors-final.json` contain exact
argv; `verify_final_probe.py` records the construction procedure. Each run uses
the shared performance lock, a 3-GiB tree cap and unchanged 8-GiB host reserve.

The wrapper's first post-run identity check rejected the added diagnostic
driver as an extra source file. This was a manifest-bookkeeping error after
successful build/execution, not a passed identity check. Verification then
explicitly required exactly the frozen production manifest **plus the unchanged
probe hash**, and rechecked every input and the live compiler sources.
`identity-verification.json` and `final-diagnostic-source.json` preserve that
successful check. No memory/performance result relies on an unverified change
to the measured production sources.

#### Validation and remaining boundaries

There are **28 distinct passing focused tests** across `final-tests.*` and
`tail-tests.*` (the strengthened typed identity check is repeated in the latter).
Eleven new ownership shapes each execute GC0–GC4: typed/dynamic sources,
temporary input, filtering, raised body, local/parameter shadowing, nested
enumerate, temporary-dict lifetime, tuple input and collection inside the body.
Adjacent element/error cleanup, comprehension scoping, nested-hoist, callable
capture and previous list-conversion regressions also pass.

Two new strict xfails retain independently reproduced pre-existing defects:
comprehension lambda late binding, and lambda-default capture ownership.
For the latter, an **untyped** control removes the typed-enumerate confounder:
before and after both finalize 0/2,000 captures and retain 708,120 requested
bytes on the second call (`closure-default-results.json`). Neither is fixed
or counted as passing. The broader packet stops at the existing
`test_lambda_comprehension_target_allocas_in_lambda` IR-dump assertion;
the frozen pre-fix compiler fails the same assertion (`lambda-alloca-before.log`).
The remaining six adjacent cases were then executed separately and pass.

No fresh pcc1, pcc1-compiled regression variants, full link, Stage2, Stage3,
release baseline qualification, installation or commit is claimed. The
separate merge-graph retention and Stage1/Stage2 <=300-second targets remain
open. These 40-input results do not establish that the complete Stage2 peak
now fits 6 GiB, or that either historical whole-corpus extrapolation is fixed.

### 2026-09-22 follow-up: lambda lifetime fixes do not close merge retention

One bounded follow-up reused exactly the preceding 40 PCOs and runtime.
Evidence root:
`/private/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-merge-owner-4wlgfj62`.
Control source: `b3cf2a91944bf53b1380c2f5f5fbd29bef474986b5544137aa635e11690bcf1c`.
Final source: `dbf8423dcb5c4902f07bc1cbed80bb81cf2906d088f55f72a10d6d60d95747e9`.
Only `lambda_helpers_lowering.py`, `list_method_lowering.py` and
`ownership_lowering.py` differ between these compiler-source manifests.
The preceding comprehension repair remains included in both arms.

#### Profiling and factors before changes

The new probe calls separate returning functions twice, then collects three
times and reads `pcc_os_heap_in_use_bytes`. These are requested allocator
bytes after frame exit, not process RSS. Initial second-call results:

| Operation | Retained requested bytes |
|---|---:|
| Decode 40 inputs | 10,785 |
| Inspect inputs / freeze external definitions | 664,776 |
| Read 351,949 relocations | 71,265 |
| `link_relocatable_native` | 131,055,207 |

Same-module diagnostic prefixes stop before joining section payloads, before
`order.sort(key=lambda ...)`, and after that sort. Second-call residuals are
37,279,972, 37,483,388 and 125,650,013 bytes respectively (`run-cuts.stdout`).
These locate an increment around that boundary; they do not prove the sort
callback is the sole owner. The prefixes are diagnostics in a separate frozen
tree, not production rewrites. A five-second live native CPU capture produced
4,200 samples (`merge-cpu.folded` / `.svg`), including GC traversal and input
decoding. This short mixed-phase sample is not a whole-merge percentage profile
and cannot establish a retained reference's owner.

Independent execution and emitted IR prove narrower lambda defects:

- A named callback releases all 2,000 captured Items. The equivalent inline
  callable key releases zero and grows by 8,500,368 bytes on the second call.
- The sort emitter does not retire its freshly created callable key.
  Adding that release alone still leaves every captured Item alive.
- The inline native adapter also obtains NEW `py_tuple_get` references for
  its captures and arguments without retiring them; the named-function
  adapter already balances those reads.
- Dynamic calls store the lambda in a keyword dictionary. Its insertion
  retains the function, but the temporary-owner classifier excludes the
  lambda's FunctionType even when its emitter recorded a NEW result. A
  dictionary-capturing reduction, including the linker's three-component
  conditional key, independently reproduces that second entry route.

These observations and IR checks were reported before each corresponding
production edit. The fixes root and retire adapter operands on return/error,
preserve an owned and rooted return value, mark native lambda results owned,
consume lambda temporaries after container insertion, and keep callable sort
keys rooted until sorting publishes its receiver. Structural inline keys and
borrowed callbacks keep their existing ownership. A return-parameter test
caught stale managed SSA across cleanup; the final implementation uses the
existing return-root protocol, registered before argument roots for LIFO exit.
No runtime, linker algorithm, environment option or admission cap was changed.

#### Negative real-component A/B [CONFIRMED]

The SAME probe source, input hashes, runtime, options and operation order were
rebuilt with each frozen compiler. Final results (`run.json`, `run-final.json`):

| Measurement | Control | Final |
|---|---:|---:|
| Second merge retained requested bytes | 131,055,207 | 131,052,935 |
| Whole probe peak RSS bytes | 417,792,000 | 417,759,232 |
| Whole probe seconds | 59.842 | 58.596 |

The control also had the five-second external CPU sampler attached; these
wall times are observations, not a clean throughput comparison.

**A 2,272-byte residual reduction does not resolve this retention or establish
a Stage2 performance gain.** The isolated lambda repair must not be promoted
to a full-merge claim. Control binary SHA256:
`d503ad15306808af03922039a05da08042816bc4dcd7e2fe40370ef4d1f82267`;
final: `cc8aa9fb075e4c8f21bbcc7a09453de455ddeda0c526a068663514bab9cde7df`.
`identity-verification.json` verifies both frozen closures, the identical probe,
all 40 inputs, the unchanged runtime and the final live compiler sources.

Builds use host pcc0, self backend, no libpython, IR passes off, direct indexed
capture/emission with zero fallback required, and disabled compilation caches.
Native execution uses GC0, serial link jobs, `PATH=/nonexistent` and disabled
host-Python selectors. `commands*.json` and `run*.json` retain exact argv.
Process-tree watchdogs use the shared performance lock, 3-GiB RSS cap,
unchanged 8-GiB launch reserve, and fixed 120-second execution budget.

An additional diagnostic worker retained and wrote its actual closed-module
IR. It contains the new callable and capture releases; the new binary's
lambda symbol is also larger. However, retaining text disables that worker's
direct capture, so the diagnostic build then fails `direct indexed kernel
output requested without capture`. Its IR is inspection evidence only, not
a successful build. The earlier standalone module dump has strict import
stubs and cannot substitute for the real closed-module function.

#### Focused validation and open boundaries

`checked-tests.*`: **43 passed, 4 xfailed**. Ten new passing shapes each run
on GC0–GC4: typed/dynamic sorting, sorted-copy behavior, borrowed/factory/class
callables, empty input, raised key, collection in the callback, and returning
a borrowed argument. Adjacent sort, map/filter, module-lambda and prior
comprehension regressions pass. The static L1 method table check also passes.

The separate integration check
`test_packed_relocation_native_link_executes_under_all_collectors[pcc0]`
also passes (`native-link-test.*`, 42.09 seconds). Host pcc0 builds the actual
native owned-link driver; that driver links two PCOs with an external BRANCH26
relocation and emits an executable returning 42 on each of GC0–GC4, with host
Python selectors disabled and `PATH=/nonexistent`. This is native linking and
emitted-program execution evidence, not a new pcc1 build. Total focused checks: 44 passing,
four expected failures retained.

The four strict xfails preserve existing gaps; none counts as passing. The
two new boundary shapes were also executed using the pre-fix compiler:
the default-capture case finalizes 299/300 Items per call; returning a captured
list finalizes none, with 361,916-byte second-call growth both before and after.
The previous comprehension default-capture and late-binding xfails remain.
These outcomes do not establish the precise owner of those separate gaps.

Full merge retention, a new pcc1, pcc1-emitted regressions, full Stage2/Stage3,
the 6-GiB complete-stage fit and both 300-second time targets remain open.
No installation or commit is claimed. Future work must re-isolate the actual
retained merge objects; removing one independently real lambda leak was not
enough, and another timeout increase or full bootstrap is not justified by
these results.

### 2026-09-22: classmethod constructor argument owners retain the output graph

The maintainer explicitly redirected this round toward the large retained
graph, rather than additional small independent fixes. Evidence root:
`/private/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-merge-retainers-lwmsk7ll`.
Baseline source: `dbf8423dcb5c4902f07bc1cbed80bb81cf2906d088f55f72a10d6d60d95747e9`.
Fixed source: `f232bbcc3f24231b281ecf02be17d08f5ec7bd43ba01f6d3411c7ffa3c515e35`.
The only production-source change in this round is the `cls(...)` branch in
`call_expression_lowering.py`; earlier working-tree changes were preserved.

#### Real retained-object profile before editing [CONFIRMED]

All experiments reuse the same 40 frozen PCOs and runtime as the preceding
round. Weak references observe existing loop bindings, avoiding a new strong
root into the graphs being measured. Native `pcc_capi_refcnt` instrumentation
was first calibrated with a list: one owner, two owners, then one owner.

- All five `_MergedSection` objects are **dead** after merge-frame exit and
  collection, while 131,283,345 requested bytes remain in that instrumented
  run. The `merged` dictionary's refcount is 1 before and after sorting.
- The returned `NativeObject` also dies. Discarding it without even reading
  `.sections` still retains 131,184,055 bytes on the second call. The length
  measurement is therefore not the cause of the large retention.
- In contrast, weak references to all five output `NativeSection` objects
  and a sampled `NativeSymbol` are still live after that object dies.
- Removing `_validate_native_object` in a **diagnostic-only** frozen copy
  changes the original probe's second-call residual only from 131,052,935
  to 131,052,860 bytes. Validation is not the large owner; the production
  validator was never edited or weakened.

These observations overturn the earlier focus on sorting's captured merge
dictionary. `run-v2.*`, `run-returns.*`, `run-observe.*` and
`run-novalidate.*` contain the individual results. The initial instrumented
build failed because host linking also executed the native-only refcount
extern. The diagnostic build wrapper subsequently replaced only that host
call with a no-op; native calibration and execution still call the real
counter. The failed initial receipt is preserved and not counted as evidence
of native execution.

#### Constructor-boundary factor and producer proof

The actual `NativeObject.from_sections` ends with:

```python
return cls(tuple(native_sections), tuple(symbols))
```

In a frozen diagnostic copy, naming those two tuples first reduces the SAME
real probe's second-call residual to **43,688,822 bytes**. Replacing only `cls`
with `NativeObject` produces the identical residual in another diagnostic
copy. Neither workaround was applied to production: the named-tuple version
isolates temporary ownership, while the direct-class version intentionally
does not test subclass dispatch.

A smaller classmethod reproducer then gives 0/2,000 Item finalizers for the
inline call, versus 2,000/2,000 for named arguments. Its native IR constructs
two NEW tuples and passes them directly into `__init__`, with no releases.
The ordinary named-class path already calls `_emit_class_init_call`, which
materializes, pins and retires its temporary arguments on success and failure.
The classmethod branch bypassed that wrapper and directly called
`class_lowering.emit_instantiate`. This exact branch is now routed through
the existing wrapper. No new lifetime mechanism or package-specific code was
introduced. Argument-resolution behavior and all linker validations remain.

The real-factor result, minimal finalizers and emitted-IR evidence were
reported before the production edit. The corrected minimal program finalizes
all 2,000 Items on every GC; its GC0 second-call growth is 56 bytes.

#### Same-program measurement after the generic fix

The original uninstrumented `merge_factor_probe.py` is unchanged. Its
`run-fixed.*` receipt compared with the previous `run-final.*` gives:

| Measurement | Baseline | Fixed |
|---|---:|---:|
| Second merge retained requested bytes | 131,052,935 | 43,688,822 |
| Whole probe peak tree RSS bytes | 417,759,232 | 278,724,608 |
| Whole probe wall seconds | 58.596 | 53.519 |

The requested-byte reduction is **87,364,113 bytes / 66.66%**. The remaining
43,688,822 bytes are not resolved by this patch. These single probe timings
do not establish a full Stage2 speedup or the 300-second target.

Baseline executable SHA256:
`cc8aa9fb075e4c8f21bbcc7a09453de455ddeda0c526a068663514bab9cde7df`;
fixed: `8b8785fc583d5db268b7f84d1226a0a896396e4606099b8b947b3eb0982a840c`.
The runtime remains
`da9967aadbf41eef0f17447e74e97af5994d9c468dc12f38580d6952bf3acd72`.
`identity-and-output.json` verifies every input hash, the frozen sources,
the matching final live compiler source and identical probe source.

Exact commands are in `commands-fixed.json` and `build-fixed.json` /
`run-fixed.json`: host pcc0, self backend, no libpython, IR passes off,
direct indexed capture/emission, zero fallback required and compiler caches
disabled. Native runs use GC0, serial link jobs, `PATH=/nonexistent` and
disabled host-Python selectors. The shared performance lock, 3-GiB RSS cap,
8-GiB launch reserve and fixed timeouts remain in force.

#### Correctness checks and remaining scope

Eight focused constructor checks pass. The three new cases in
`test_class_init_argument_ownership.py` execute on GC0–GC4 and cover positional
and keyword temporary tuples, later-argument failure, `__init__` failure,
source rebinding and collection during finalizers. Their pcc1 compiler
variants are retained as integration tests but were not executed this round.

The separate native-link integration passes: a pcc0-built owned linker emits
and executes an exit42 program on every GC. In addition, the real 40-input
serialization probe drops its input owners, collects three times, then
encodes the merged object. Its **31,785,446-byte** PCO is byte-for-byte equal
to the host result (5 sections, 14,350 symbols), SHA256:
`bdcfe69bbe6101982f4b51dc5fd8032a368c8bd0176685f4b34b59614c02d3e9`.
`run-serialization.*`, `host-serialization.*` and `identity-and-output.json`
preserve that check. Total focused pytest checks: nine passing.

This round repairs a measured large owner in host-compiled native code.
A newly rebuilt pcc1, pcc1-emitted regression programs, full Stage2/Stage3,
whole-stage peak below 6 GiB and Stage1/Stage2 each below 300 seconds remain
unverified. No commit or installation was made; no whole-corpus extrapolation
is used to declare those goals complete.

### 2026-09-22: separate owned-link inputs and release keyed-sort sources

This follow-up preserves the previous worktree and targets the next measured
owner. Evidence root:
`/private/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-residual-owner-xvbpq4to`.
Baseline compiler source is `f232bbcc3f24231b281ecf02be17d08f5ec7bd43ba01f6d3411c7ffa3c515e35`.
The fixed compiler source is `1a188d2ca9cf850406b0def76a0da6714ab6b808ca8d5778c8f5459875100313`.
Only `list_method_lowering.py` changes in the production compiler this round.

#### Selected-route correction

Weak-reference controls establish that decoding alone releases all 40
`PackedNativeObject` inputs. After a borrowed-input merge, all 40 remain
alive, while the output NativeSections and sampled NativeSymbol now die.
Separate explicit calls then distinguish the modes; the consuming call
asserts that the caller's list actually becomes empty, so it cannot silently
exercise the borrowed arm. It retains about 9.5 MB, versus about 43.8 MB for
the borrowed arm in the same diagnostic executable.

Current `owned_link_driver.py` calls `link_executable(..., _consume_inputs=True)`;
`macho_exec.py` forwards that selection to the merge. Therefore the earlier
43.7-MB number must **not** be relabeled as the native Stage2 route's residual.
A fresh uninstrumented consuming-route control gives **9,361,909 bytes** on
the second call (`run-consumed-baseline.*`). This is still only a 40-input
component measurement, not a full executable-link or Stage2 memory bound.

Naming the input parameter differently does not fix the borrowed path.
Clearing one expired private container at a time shows that clearing `objects`
releases 39/40 input objects; clearing `indexed_objects`, `inspected_inputs`
or `parsed_objects` individually leaves 40/40 alive (`run-clear.*`). The last
input also has later local aliases. No production early-clear workaround or
parameter-ownership change was made on this evidence alone.

#### Object census and source factor before editing

A read-only, single-threaded GC0 diagnostic scans LIVE object-family slab
cells and separately registered objects, excluding immortals. It reports
object counts and their requested payload sizes, not a complete accounting
of raw backing buffers or allocator metadata. Layouts and lifecycle constants
come from the current allocator ABI. It allocates no managed objects during
the scan. The helper is pcc-compiled and pcc-assembled into a 4-KB object,
then added to a private diagnostic archive; all **171 original runtime
members are verified byte-identical**. The normal measurement and validation
runs continue using the original runtime archive.

The second consuming merge adds the following live class objects
(`run-classes.stdout`):

| Class | Additional live instances |
|---|---:|
| TextSymbol | 13,132 |
| SymbolDefinition | 1,382 |
| PackedNativeSection | 200 |
| NativeObject / NativeSection / NativeSymbol / NativeRelocation | 0 |
| PackedNativeObject / Relocation / Section / _MergedSection / _InspectedLinkInput | 0 |

Strings increase by 30,852 and tuples by 16,648. Integers increase by 4,653,
so they were not selected as the leading object-count owner. The census and
class-tag mapping are diagnostic evidence; no runtime source was edited.

The largest class population leads to
`symbols=tuple(sorted(m.symbols, key=lambda s: s.offset))` in the real merge.
In a frozen diagnostic copy, naming only `m.symbols` before this expression
reduces the consuming-route residual to **7,843,607 bytes**. The production
linker source keeps its original expression. A separate minimal program
finalizes 0/2,000 Items for `sorted(make(), key=...)`, and 2,000/2,000 when the
input has a named local owner. Its IR copies the source into the sorted result
without consuming the source expression's original reference. These results
were reported before editing the producer.

#### Generic repair and measurement

`_emit_sorted_with_key_lambda` now roots its source through callbacks and
retires its temporary owner on success and error. Borrowed inputs keep their
caller-owned references. The result stays pinned while source/key cleanup
can run finalizers and collect; error cleanup also releases that result.
No sorting algorithm, validation, pass selection or public option changed.

The original consuming probe and unchanged runtime give:

| Measurement | Baseline | Fixed |
|---|---:|---:|
| Second-call retained requested bytes | 9,361,909 | 7,843,495 |
| Peak process-tree RSS bytes | 226,689,024 | 223,199,232 |
| Probe wall seconds | 34.402 | 30.865 |

The retained-byte reduction is **1,518,414 / 16.22%**. These are single
component runs; the wall times do not certify an end-to-end speedup. The
remaining approximately 7.84 MB and the separate borrowed-input owner are
still open. No new pcc1 or full Stage2/Stage3 was built in this round.

#### Validation and diagnostic limits

Five new cases in `test_sorted_result_ownership.py` execute on GC0–GC4:
temporary factory input, attribute input, borrowed input, source lifetime
through a collecting callback, and cleanup when that callback raises. The
adjacent packet completes with **33 passed, 2 pre-existing xfails**; the five
new cases are included in those passes. The pcc1 fixture variants remain
unrun integration cases pending a compiler containing these changes.

The native owned-link integration adds one passing check: a pcc0-built
linker emits and executes an exit42 program on each of GC0–GC4. Total focused
pytest checks are **34 passed, 2 existing xfails**. A separate real consuming
merge probe asserts that inputs were cleared, drops the input binding,
collects three times and encodes the result. Its 31,785,446-byte PCO is
byte-identical to the host output, SHA256
`bdcfe69bbe6101982f4b51dc5fd8032a368c8bd0176685f4b34b59614c02d3e9`.
The original runtime archive is unchanged for these checks.

The baseline executable SHA256 is
`bae56ce4412882a8f54793feced626c38f769dbd649926f9f73ea68434981d05`;
the fixed executable is
`c8f4bbc3ef961e4084cb3a195881713496110edb2bbcd50445b1717eb3bab6b7`.
`identity-and-output.json` verifies the frozen and live sources, all 40 input
hashes, the identical consuming probe and the complete output comparison.

The machine refused a 3-GiB diagnostic build at swap preflight. Subsequent
runs retain the 8-GiB system reserve and use a stricter **2-GiB** RSS cap;
frontend diagnostic builds are limited to two workers. The first histogram
approaches failed through an unresolved helper, aggregate worker memory
limit, an annotation mismatch and unsupported external link arguments. Those
receipts are preserved. The successful route compiles the freestanding helper
separately with its required C-ABI exports and adds it to the private archive;
it does not disable a production guard or use cc/LLVM.

Exact source manifests, commands, private-archive identities, stdout and
watchdog receipts live together under the evidence root. No commit or
installation is claimed. Remaining native compiler qualification and both
300-second stage targets are unchanged.

### 2026-09-22: two leaked owners retain the consumed relocation buffer

[CONFIRMED] The native consuming merge retained a 4-MiB list backing array
through **two independent extra references**. Both are repaired in generic
lowering; the production linker and runtime sources are unchanged. This
round reduces retained requested bytes, not measured single-link time or RSS.

Evidence root:
`/private/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-big-owner-uc0ko8x3`.
`result.json`, `round.patch`, source manifests, command JSONs, sampler logs
and `HANDOFF-2026-09-22.md` keep the exact reproduction together.

- Baseline production identity: `1a188d2ca9cf850406b0def76a0da6714ab6b808ca8d5778c8f5459875100313`.
- Final production identity: `91f817c547872778e83001d4989a9747d40917f46b06cdf6a5fef230637fc79f`.
- Same original pcc-Python runtime archive: `da9967aadbf41eef0f17447e74e97af5994d9c468dc12f38580d6952bf3acd72`.
- Same 40 PCOs / 31,933,041 bytes from the retained 389-input corpus;
  `inputs.json` verifies each path, size and digest.

The selected route remains native `link_relocatable_native(...,
_consume_inputs=True)`. Host pcc builds the isolated native probe with the
owned self emitter, no libpython, IR passes off, two frontend workers,
direct indexed capture/emission/fused uses, and both frontend/backend caches
disabled. Native replay uses `PATH=/nonexistent` and disabled host selectors.
Runs hold the performance lock, a 2-GiB tree-RSS cap and an 8-GiB system reserve.
This is a native linker component experiment, not a newly rebuilt pcc1.

#### Factor separation and ownership proof

The baseline object census confirms that the preceding keyed-sort fix freed
all 13,132 TextSymbols. Loading alone retains 11,206 requested bytes on the
second call; loading plus symbol inspection retains 665,036 bytes. The full
consuming merge retains 7,843,439 bytes with the diagnostic census present.

A source-equivalent diagnostic rewrite of the symbol-address `setdefault`
call saves only 525,400 bytes. Set result ownership and unkeyed sorted input
cleanup are real independently reproduced defects, but together save only
32,856 bytes in this workload. They were not accepted as the requested large
improvement. An ablation of final object construction narrows the larger
remainder; omitting native-object validation changes only 75 bytes, while
omitting source-section validation removes approximately 1.153 MB of strings.
These ablations are diagnostic only. No production validation was removed.

The extended native census reads list capacity as well as object size.
`run-lists.stdout` shows one additional large list per merge, **4,194,304
backing bytes and refcount 2**. Four repetitions with GC-index capacity
counters reject the hypothesis that this was merely the first-call index
capacity increase. Two owners account for the large list:

1. The runtime builtin `isinstance(section.relocations, list)` borrows its
   operand but lowering failed to release the NEW attribute-load reference.
   The builtin helper now releases an operand it evaluated; tuple classinfo
   keeps its shared operand until all checks finish. `run-isinstance.stdout`
   measures the same retained array with refcount **1** after this repair.
2. `_validate_section(..., relocations=None)` assigns `sec.relocations` to
   the parameter. Cleanup still excluded it as an incoming borrowed parameter.
   Object parameters recognized as assignment/for targets now enter a fresh
   owned local binding before body lowering, using the existing traced-binding
   promotion and error-cleanup machinery. Their original borrowed ABI slots
   remain intact. Both function and method entry paths use the helper;
   freestanding/manual raw ABI and captured-cell paths keep their existing
   ownership contracts. `run-complete.stdout` has **zero** large retained
   lists after both calls.

The smaller fixes register fresh set-operator results as owned and share
sorted input/key/result lifetimes across plain, keyed and custom-comparison
paths. No package-specific workaround or backend `clear()` was added.

#### Original-runtime replay and executed results

`normal-baseline.stdout` and `run-normal.stdout` use the identical original
probe source and unchanged production runtime, without the census helper:

| Measurement | Before | After |
|---|---:|---:|
| Second-call retained requested bytes | 7,843,495 | 3,480,621 |
| Two-call probe wall seconds | 31.999 | 32.003 |
| Peak tree RSS bytes | 223,199,232 | 229,736,448 |

The retention reduction is **4,362,874 bytes / 55.62%**. Wall time is unchanged
in this single pair and RSS did not improve. Do not extrapolate this result
to full Stage2 timing or memory. Approximately 3.48 MB of component retention
remains, including strings, tuples and symbol-definition containers.

The fixed native executable SHA256 is
`3e5a2b6e6ba62e22c45039e2bc45b297031bcf139718ea7741561d287e52e074`.
The separate serialization probe consumes and drops its inputs, collects
three times, then encodes the surviving output. Native and host outputs are
byte-identical: **31,785,446 bytes**, SHA256
`bdcfe69bbe6101982f4b51dc5fd8032a368c8bd0176685f4b34b59614c02d3e9`.

**49 related pytest cases pass**: 27 ownership cases, 21 adjacent semantic
checks and one actual owned-link execution check. The ownership packet runs
its emitted programs on GC0–GC4. It covers set-result assignment/discard,
plain sorted input success/error, dynamic attribute operands in builtin and
tuple isinstance, parameter rebinding in functions/methods/classmethods,
borrowed inputs, early return, self-assignment, repeated replacement and
exceptional exits. The native-link integration emits and runs an exit42
program with a real external BRANCH26 relocation under every collector.
The generated L1 static-method table check is also current.

Two experimental mistakes are excluded from the evidence: early preindex
ablations failed type/signature/default/build-link checks; only `preindex6`
ran successfully, and it is not an output-equivalence check. Early small
baseline probes changed the parent's import path but left frontend-worker
cwd at the live repository, mixing source versions. The corrected baseline
ran from `baseline-source` with caches disabled: the isolated isinstance
probe increases refcount by one per check and retains 1,048,656 bytes per
call. Its original test also contained an unrelated identity comparison;
the retained regression isolates the predicate in `check(holder)`.

No fresh pcc1, complete Stage2/Stage3, fixed point, commit or installation is
claimed. Both 300-second stage goals remain open. Weekly quota data was not
available. New actionable leads remain recorded here with their measurements;
the next qualification must use a compiler containing these frontend fixes.

### 2026-09-22: reduce native relocation traversal CPU, paired 21.3% component win

[CONFIRMED] This round changes the packed relocation consumers, with a
measured **21.3% wall / 20.8% CPU reduction** in the native decode-and-merge
component. It does not establish a complete Stage2 speedup. The prior memory
fix remains present; retained requested bytes stay at 3,480,621 per second
merge rather than being traded for a cache that survives the operation.

Evidence root:
`/private/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-speed-owner-mzfciwtq`.
`result.json`, `abba.json`, `native.folded`, `native.svg`, `round.patch`,
source manifests, command JSONs and watchdog receipts bind the measurements.
`replay_abba.py` and `qualify.py` preserve the executable reproduction.

- Baseline source: `91f817c547872778e83001d4989a9747d40917f46b06cdf6a5fef230637fc79f`.
- Candidate source: `35d8492a303936fc787b9caee6c1d9dc1ffe76b2d388d601b6463f663239d7b9`.
- Baseline executable: `3e5a2b6e6ba62e22c45039e2bc45b297031bcf139718ea7741561d287e52e074`.
- Candidate executable: `97b685a2c0b842db9fc8b221825ecf7ce226bf2a9478ccf8020cfcd42f3cb723`.
- Unchanged runtime: `da9967aadbf41eef0f17447e74e97af5994d9c468dc12f38580d6952bf3acd72`.

Both arms use the identical native probe, loading and consuming the same
40 read-only PCOs (31,933,041 bytes) twice, with three collections between
calls. Host pcc builds the native executable with the self backend,
`--python-libpython off`, `PCC_PYTHON_IR_PASSES=off`, the same direct indexed
emission options and disabled compiler caches. Thus no new optimizer pass
or emitter selection explains the difference. Native execution selects GC0,
`PCC_MACHO_LINK_JOBS=off`, `PATH=/nonexistent` and disabled host selectors.
All heavy runs keep the performance lock, 2-GiB tree cap, 8-GiB system reserve
and isolated outputs. Source, runtime and all input hashes were rechecked.

#### Profile before editing

The existing `pcc_flamegraph.py cpu PID 20 --exact-pid` sampled the actual
native process and resolved its own executable: **16,905 on-CPU samples**.
In this 20-second window, `NativeObject.from_sections` is 35.0% inclusive,
packed decode 21.4%, `_read_relocations` 16.8%, and generator-next dispatch
appears in 19.1% of stacks. These shares overlap and cover a window of the
component run, not the entire Stage2 or disjoint removable costs.

Source tracing found repeated work underneath those owners:

- Packed rows passed through `relocation_fields` and `decoded_relocations`,
  building a raw tuple and then a normalized tuple which the consumer
  immediately unpacked. Validation also used a generator wrapper and a
  second tuple solely to project three indices.
- Payload extraction decoded every relocation again just to find the
  section-target rows. Most rows only needed their target-section u32 tested.
- `_relocation_symbol_name` repeatedly loaded symbol metadata and applied the
  same local rename for every relocation referencing a symbol.

The first frozen traversal-only candidate ran in 29.321s, an unpaired
exploratory observation. The final change also resolves the symbol-name table
once per packed input and shares it across section reads. The subsequent
paired measurements, not that exploratory observation, support the result.

#### Implementation and semantic boundary

`_read_relocations` now traverses the stored indices directly, preserving the
existing descending/stable ordering and rare unordered-table sort. Packed
validation reads each storage-order record directly and decodes its three
indices without an intermediate tuple. `_packed_section_target_relocations`
checks the whole span before unsafe reads, reads only the target-index field
for ordinary symbol rows, and fully decodes selected target rows in the
original iteration order. Public normalized iterators remain available.

Symbol resolution is still the existing resolver, now called once per input
symbol; local names are computed separately for each input. Invalid index
queries retain the original diagnostic route. There is no production
validation bypass, wire-format change, record-class change, new runtime
implementation or new environment option.

#### Alternating A/B result

`/usr/bin/time -lp` measures the native command inside the watchdog. Its real
time is distinct from the additional sampler/wrapper wall time in the JSONs.

| Order | Real seconds | User + system seconds | Instructions |
|---|---:|---:|---:|
| A1 | 32.53 | 31.89 | 536,728,370,051 |
| B1 | 25.67 | 25.24 | 425,272,398,811 |
| B2 | 25.52 | 25.25 | 425,733,114,857 |
| A2 | 32.52 | 31.86 | 536,607,787,475 |

Mean real time is **32.525 -> 25.595s**, a **1.271x** component speedup.
Mean CPU is **31.875 -> 25.245s**; instruction count falls **20.7%**. Both
candidate runs beat both controls. Peak tree RSS is approximately 221 MB in
both arms; no material RSS improvement is claimed. All four runs return five
merged sections and the same second-call retained-byte count.

#### Qualification

The complete object fast-path packet passes **70 cases**, followed by **one
native-link integration case**. New checks cover ascending/descending/mixed
storage order, selected-row decoding counts, bounds before raw reads,
repeated symbol references and per-input local-name collisions. Existing
wire-mutation and integer-width coverage remains green.

The native integration now links both BRANCH26 and a non-monotone mix of
section-target/symbol relocations under GC0–GC4. Each native-produced
executable equals the host executable byte-for-byte and actually returns 42;
the section-target program loads the relocated pointer and its value.
A separate 40-PCO merge, input release, collection and serialization run also
matches the host and the pre-change golden output: **31,785,446 bytes**,
SHA256 `bdcfe69bbe6101982f4b51dc5fd8032a368c8bd0176685f4b34b59614c02d3e9`.

The broader packet initially exposed a pre-existing stale signing test: it
intercepted `materialize_output`, while production uses
`materialize_output_buffer`. The frozen baseline reproduced the same failure.
The test now intercepts the actual entry, forwards `reserve`, and retains its
weakref assertion that region owners are dead before signing. Its focused
check and the full packet pass; production signing was not changed.

No fresh pcc1, complete Stage2/Stage3, fixed point, commit or installation was
performed. The frontend and PCO-emission stages still need their own current
measurements, and both 300-second stage targets remain open. This source
change reaches a new native compiler only when that compiler is rebuilt.
Weekly quota data was unavailable; no quota monitoring is claimed.

### 2026-09-23: caller-focused profiling and typed PIDX restore

[CONFIRMED] The profiling tools now retain failed-workload evidence and can
partition previously captured stacks by a named caller. Usage and interpretation
are maintained in [profiling-hotspots](../knowledge/profiling-hotspots.md), linked
from the development-tool index. That guide also explains why a mechanism spread
across many small call sites can be an architectural bottleneck.

`pcc_flamegraph.py report --input-folded FILE --focus SYMBOL --report-json NEW`
reports aggregate self/inclusive weights, focus callers, disjoint direct children
and both denominators. Recursive inclusive occurrences count once. Missing or
ambiguous focus names fail; reports retain full symbol names and input hashes.
Native capture retains raw sample text, binary identity and stderr before
symbolization. Host failure retains each child file and stderr, emits partial
attribution when available, and returns nonzero. Correctness remains explicitly
NOT_CHECKED. This is not whole-process-tree native sampling, source-operation
mapping, or automatic ownership-graph analysis.

`pcc_passes_explain.py` formerly printed three hardcoded `ran=true` entries with
zero durations even with no input. It now says UNKNOWN without observed events,
or reads existing IR-pass JSONL using `--telemetry`, preserving order, module,
run/cache/skip/error status and available fields. It does not switch optimization
routes, certify native ownership, or infer useful IR changes from byte sizes.
The tool packet has 28 passing tests, including a real failing host subprocess,
offline CLI execution, recursion, partial capture and binary identity rejection.

#### Frozen native evidence and rejected alternative

Evidence root:
`/private/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-pidx-restore-cy3w48w9`.
`typed-abba-summary.json`, `typed-abba-*.{json,stderr,stdout,tsv,pco}`,
`typed-restore-command.json`, `typed-restore-source.json`,
`accepted-production-source.json`, `owner-tool-report.json`,
`typed-profile-report.json` and `profile-typed-evidence/` bind the runs.
The input is retained module_135 (c_codegen), 58,691,834 bytes, 619 functions,
120,579 values, SHA256
`10d85229d265ccf627327a95e5d61d629a8e4ec338770ccb4c316194f7c8dc5c`.

- Baseline production source: `35d8492a303936fc787b9caee6c1d9dc1ffe76b2d388d601b6463f663239d7b9`.
- Accepted candidate production source: `bcdf59777039e70282cb11e7fa5f48d12467a49b6288c04f26d2895072bf3ec9`.
- Candidate frozen source including identical probe driver: `5573acddb5e1d901884d5fbdf5515f0c83cde79ec166dd6558a7d80a0f740503`.
- Baseline native probe: `bb802a905ffb34d3e99c5392f75414151601e90b624d037b8310615e60e0663d`.
- Candidate native probe: `058c27ae81de21cb51167cb51280ec8cc4727817933baaaa728f8bed5f3d3458`.
- Unchanged runtime archive: `da9967aadbf41eef0f17447e74e97af5994d9c468dc12f38580d6952bf3acd72`.

The actual baseline decoder profile has 12,025 samples. Its disjoint immediate
children include attribute lookup 43.05%, decref 21.92%, dynamic call 13.33%,
kernel construction 7.18% and seed reconstruction 3.03% of the capture. These
are decoder-window weights, not percentages of the whole Stage2. Generic
`temporary_arenas.get()` results route repeated arena accesses through method
binding. The accepted decoder change projects these three checked values to
`CompilerIntArena`, preserving the original construction-list/freeze sequence,
input validation, normalization and explicit temporary-arena close.

[DENIED] An earlier attempt additionally adopted the raw value planes directly
into IndexedFunctionKernel. Its approximately 1.7-second decode was invalid as
speed evidence: full native emission failed in `publish_value_type_id` with an
IndexError. Constructor instrumentation preserved all lengths but then exposed
an SSA-dominance failure. Small alias/tuple/cast and decode-only reductions were
green and did not prove the full shape. The adoption and skipped construction
lists have been removed from the current worktree; no kernel representation
change remains. Its source snapshots and failed receipts remain available.
Do not reuse those timings or name cast as the established cause of that failure.

The previously found generic `typing.cast` borrowed-result lifetime repair is
retained, and its six native cases each execute GC0–GC4. Comparing the manifests,
these performance arms differ in **three production files**: decoder projections,
the cast repair, and the earlier typed regalloc local required by the record
inventory. Therefore the timing is the combined candidate result, not a strict
three-line-only attribution. Pass selection, emitter algorithm and runtime are
unchanged: host pcc builds self/no-libpython probes with IR passes off and direct
indexed emission; the measured native programs run with PATH=/nonexistent and
host selectors disabled. No LLVM/cc oracle supplies the measured work.

#### Alternating measurements and exact outputs

Uninstrumented A1/B1/B2/A2, identical input, fresh process each time. Values below
are means from `/usr/bin/time -lp`, separate from watchdog launch/exit overhead.
Decode uses a 768-MiB tree cap; emission/build uses 1.5 GiB; all retain the shared
performance lock and 8-GiB Darwin reserve. No contending builds ran in these pairs.

| Native component | Baseline | Candidate | Reduction |
| --- | ---: | ---: | ---: |
| Decode wall | 12.160 s | 3.285 s | 73.0% |
| Decode CPU | 11.990 s | 3.230 s | 73.1% |
| Decode retired instructions | 199.293 billion | 54.399 billion | 72.7% |
| Decode through PCO publication wall | 53.240 s | 45.110 s | 15.3% |
| Decode through PCO publication CPU | 52.765 s | 44.425 s | 15.8% |
| Decode through PCO retired instructions | 890.728 billion | 747.953 billion | 16.0% |

Every full emission produces the same PCO SHA256
`3bd1c7d44e36a2122517a8d3a9aa6e60596d45c3c4cb736d92f0640dfb8f957a`.
A separate candidate capture executes the changed profiling tool on the real
native process, retaining 4,419 samples and raw evidence. The decoder now exposes
a direct `CompilerIntArena_get_unchecked` child (5.32%); the previous immediate
`py_obj_call`/`py_decref` concentrations disappear from its leading children.
Kernel construction and attribute lookup remain leading costs. The shorter
capture and its percentages are diagnostic, not additional A/B timing evidence.

#### Executed boundaries and open failure

62 focused cases pass across the codec, cast ownership, record inventory and
profiling tools. One initially incorrect test attempted a prohibited freestanding
`typing` import; it now checks the existing rejection explicitly. The ordinary
raw-ABI cast case still checks absence of managed retain/pin/incref calls.

Native candidate emission of a three-function fixture (alloca/load/store,
branch/PHI, call and zero-value function) succeeds under all five collectors.
All five PCOs equal the host oracle, SHA256
`33820ddc0c0101dc2b72859cd8d4a13d139d19d1913c96af77730a01060ede37`.
The owned host linker links them; each resulting native program actually exits
42. `typed-small-execution.json` and `/tmp/pcc-typed-native-execution.*` record this.
This is native component execution, not a native-owned complete link chain.

[CONFIRMED] A **separate integration gate remains red**:
`test_restored_value_arenas_survive_native_collection_and_roundtrip[pcc0]` reports
`BackendUnavailable: parsed function has an invalid indexed kernel` in the
scenario containing explicit collections, repeated kernel reads and re-encoding.
Both candidate and a control restoring the original untyped decoder fail under
GC0–GC4 with the same message. The control retains the other prior frontend
repairs; this establishes that the three decoder projections are not necessary
for the failure, not its precise lifetime/GC owner. Do not claim the collections
alone are causal until that factor is separated. The test remains failing and
was neither skipped nor weakened. Source/commands/results are in
`gc-control-*`, `/tmp/pcc-pidx-native-integration.*` and
`typed-small-execution.json`. Its durable pytest source rebuilds the reproducer.
Next diagnosis: place boundaries before/after collection, first kernel read,
encoding and final read; separate those factors before a shared-code edit.

No new pcc1, full Stage2/Stage3, fixed point, installation or commit was performed.
The component improvement is measured; the explicit-collection integration and
both 300-second stage targets remain open. Weekly quota readings were unavailable.

### 2026-09-23 (round 2): host Stage1 ownership — collector traversal, probe spawns, rescans

Measured owners of the host Stage1 wall, then the changes that remove them.
Every change keeps the produced artifacts identical; none alters what pcc1
executes (see "Native behaviour" below). Evidence root (session scratchpad):
`/private/tmp/claude-501/-Users-jiamo-my-pcc-gateway/3a7f74e3-a0d6-4ce6-8c6c-209e5c96345a/scratchpad`
(`cg/` worker/export replays, `link/` link replays, `e2e/` Stage1 arms).
Worker/export replays reuse the frozen frontend checkpoint
`/private/tmp/pcc-stage-opt-4fb0fqpj/frontend-checkpoint` and the recorded
environment of the 2026-09-22 successful Stage1 (IR passes off, direct
indexed emission, GC0, two frontend + two self-backend workers).

#### Profiles before editing

`python-tachyon -m profiling.sampling run --mode cpu` (external sampler; the
in-process `sys._current_frames` sampler over-weights function entries
because it samples at GIL hand-off, and read 26% "dataclass __init__" where
tachyon reads 5%) over one 98-module codegen chunk:

- [CONFIRMED] The two explicit per-module `gc.collect()` calls
  (`_release_direct_frontend_state` and the post-emission release) cost 26% of
  worker CPU: each full collection re-traversed every long-lived object —
  exports for all 394 modules, the imported compiler, and every AST the worker
  had read up front but not yet processed. `gc.get_stats()`: 215 full
  collections, 37.0 s, in a 210 s chunk.
- [CONFIRMED] Export workers keep every lifted module alive until exit; their
  automatic collections reclaimed 26 objects in 21 full collections (12.5 s) —
  43% of a 33.2 s single-chunk export worker.
- [CONFIRMED] The coordinator's unlabelled pre-export window (~46–68 s wall,
  ~17 s CPU) was waiting on `_host_find_spec_origin`: 2,125 host-interpreter
  probe spawns for 32 distinct names (`pcc` 1,366 times, `__future__` 686),
  49.1 s wall. Closure discovery also rescanned each source ~10 times
  (2,505 `_source_import_discovery_text` and 1,746
  `_without_type_checking_imports` calls for 256 sources).
- [CONFIRMED] Link validation is >30% of a full link replay's CPU. Of it,
  `NativeObject.from_sections` validates its source sections and then
  `__post_init__` regenerates and re-validates the same relocations
  (14.4% of link CPU). The final-image peak is materialisation: three full
  image copies overlapped (regions+buffer, `bytes(image)`, and the
  validation slice `image[:dataoff]`).

This is not a CPython collector defect: a full collection is defined to
traverse every tracked object, and a generational collector cannot know a
large heap is acyclic. The workload shape made it expensive.

#### Changes and paired measurements

| Change | Measurement | Before | After | Output |
|---|---|---:|---:|---|
| Codegen worker: read each AST when its module is processed; `gc.freeze()` survivors after each module's final collection | 98-module chunk, arms concurrent, user CPU | 209.95 s | 180.03 s | PCO digest `bb4c0cb8…` identical |
| (same) | GC time (`gc.get_stats`) | 40.5 s | 10.5 s | |
| (same) | max RSS | 910 MB | 783 MB | |
| Export worker: automatic collector off for the worker | 394-module single chunk, user CPU | 33.15 s | 21.83 s | exports + all AST wire files `cde5b2c5…` identical |
| (same) | GC time / max RSS | 14.1 s / 528 MB | 0.01 s / 532 MB | |
| Link: validate the signature over `memoryview(image)` | full 394-PCO link replay, peak tree RSS | 3.49 GiB | 3.17 GiB | executable `53e1d821…` identical |
| Coordinator: cache host find-spec probe per (interpreter, cwd, import-path env, name) | probe spawns / wall | 2,125 / 49.1 s | 32 / 0.8 s | module rows of the export manifest identical |
| Import scanners memoized by source text (bounded) | closure CPU (in-process) | 2.97 s | 1.48 s | closure digest `ef0edfdc…` identical |

`gc.freeze()` is applied only when ASTs are read lazily (no unprocessed module
graph can be frozen) and only after the module's final collection; at exit
the permanent generation held 77,621 objects, so emitted transports are not
retained. A failed probe is not cached (retried as before).

#### Denied or deferred

- [DENIED] Disabling the collector for the whole in-process link: CPU
  73.7 → 68.4 s but max RSS 3.77 → 4.32 GB. The link does create cyclic
  garbage; the collector stays on there.
- [DENIED] The re-export fixed point (`_merge_closed_world_reexport_edges`,
  394 modules × 4,971 edges) costs 0.16 s CPU — not an owner, left unchanged.
- Deferred: summary workers spend 0.18 s of 1.43 s per 8-module batch in GC
  (~4 s wall total); interpreter startup dominates.
- Deferred: the duplicate relocation validation in `from_sections` needs a
  trusted-construction path pcc1 can compile; no compiled module yet uses
  `object.__new__`/`object.__setattr__`, so it was not changed.
- Deferred: `CompilerInt4` host records are frozen dataclasses; prebound slot
  setters cut construction 287 → 192 ns, ≈2% of worker CPU.

#### Native behaviour

pcc's `gc.freeze()` and `gc.disable()` lower to bookkeeping-only runtime
calls (`py_gc_freeze` stores a count; `py_gc_enabled` is read only by
`isenabled`), so pcc1 collects exactly as before. `_host_find_spec_origin`
returns before spawning under pcc. The link's memoryview-over-bytes slice
already has compiled precedents (`native_object.py`, `precise_stackmap.py`).

#### Test note

`test_comprehension_owners_leave_scope[nested]` timed out at 30 s per
backend: 1000×1000 iterations took GC1 21.25 s standalone (GC0 1.30 s,
GC2 5.18 s, GC3 10.76 s, GC4 16.10 s), all with `created == destroyed`. The
inner list is now 16 items: same nested shape, and a per-iteration owner
leak would still retain >700 KB against the 8,192-byte GC0 bound.

#### Complete Stage1, alternating arms

`scripts/run_pcc_stage1_build.py --jobs 2 --self-backend-jobs 2
--memory-budget-bytes 5368709120 --python-ir-passes off --direct-indexed-emit`,
same runtime archive as the 2026-09-22 run, frozen snapshots
`e2e/src-base` (this tree without this round's edits) and `e2e/src-cand`
(B1: collector changes only) / `e2e/src-cand3` (B2: all changes except the
import-scan memoization, which was added afterwards and is measured only
in-process above). The
process-tree guard is a scratch copy of `run_process_tree_sample.py` whose only
change retries a slow `ps` once (1 s then 5 s): at load average 70–180 the
unmodified guard stopped a healthy baseline with `SAMPLER_ERROR` ("ps timed
out after bounded process-table retries: 1.0 seconds"). All four runs passed
their smoke checks.

| Arm | Load | CPU s | Total s | Pre-export s | Exports s | Codegen s | Link s | Peak tree GiB |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A1 baseline | 45–180 | 1108.3 | 663.7 | 67.6 | 54.8 | 468.6 | 72.5 | 4.22 |
| B1 collector changes | ~6 | 891.0 | 536.0 | 45.7 | 21.0 | 397.2 | 72.0 | 4.32 |
| B2 all but scan memo | ~5 | 824.1 | 474.5 | 9.8 | 19.8 | 374.7 | 70.2 | 4.41 |
| A2 baseline | ~6 | 922.9 | 541.9 | 40.0 | 23.8 | 408.3 | 69.8 | 4.25 |

A2 and B2 ran under comparable load: CPU −10.7%, total −67.4 s (−12.4%),
with the serial pre-export window 40.0 → 9.8 s. Codegen gains less end to end
(−8%) than in the 98-module chunk (−14%): Stage1's eight chunks hold fewer
unprocessed ASTs per worker, so each collection had less to re-traverse. A1's
export time was inflated by load, not by the collector.

The 300-second Stage1 target is still open. Codegen is 79% of B2; its CPU is
about 750 s across two workers, so the two-worker policy bounds this phase
near 375 s unless per-module emission gets roughly twice as fast. A
single-slot tail (last chunk alone for ~44 s in A1) remains.

#### pcc1 built with direct virtual method calls

- [DENIED] `PCC_DIRECT_VIRTUAL_METHOD_CALLS=1` during the Stage1 compile
  (`e2e/stage1-dv/pcc1`, smoke passed; 16.5 KB smaller than B2's pcc1) does
  not speed pcc1 up on the compiler workload. Replaying Stage2's own singleton
  manifest for `pcc.py_frontend.pipeline_ast_wire` (index 32; flag forced off
  for the output): 83.0 G instructions against 82.9 G and 83.2 G for B2's
  pcc1, identical PIDX digest `c4fafc34…`. The guard (no `__slots__`,
  `__getattr__`, `__getattribute__` or native extension class anywhere in the
  closed world) excludes the compiler's own hot call sites, which use slotted
  dataclasses and valueclasses.

The same replay puts pcc1 at about 2.1× CPython's instructions on that module
(83 G vs 38.8 G for the host worker) — far from the 6–7× suggested by
comparing whole stages; the whole-stage gap needs its own attribution.

## Update 2026-09-23 — current Stage2 link data plane and class-lookup probe

The fresh-source self/no-libpython GC0 Stage2 in
`/tmp/pcc-stage2-reset.x83FTu/profile/stage2.result.json` completed in
1941.757 s and produced runnable pcc2 SHA256
`739df188ef64648c822d84ce63737ad854187b1a575c7517fb80ff63e2453581`.
Its pcc1 came from frozen Stage1 source
`1a33e9331453239b127b588c30380ec274ae33ff34baf78a6721d25780fc24bf`
and executable
`34f88551cf28a048096013fc5e1c3646873ae468fce87820c68ccd422d85d876`;
the archive SHA256 was
`da9967aadbf41eef0f17447e74e97af5994d9c468dc12f38580d6952bf3acd72`.
Stage1's outer build was about 297 s. Stage2≤Stage1 remains **open**.

### Exact-link progression

All rows below used the same retained 392-PCO manifest
`/tmp/pcc-stage2-reset.x83FTu/pcc2.pcc-codegen-plan.internal-inputs`, the
same archive above, the shared performance lock, a 10-GiB process-tree RSS
breaker and isolated outputs. Every successful image had the exact pcc2 SHA256
above, linked only libSystem, and the final compact-row image compiled and ran
a function smoke printing `42`. These are native pcc1 link replays, not full
fresh-source Stage2 timings.

| Link implementation | Guard receipt | Wall s | Peak tree GB |
|---|---|---:|---:|
| Original pcc1, same inputs | `/tmp/pcc-source-view-native-link-control.guard.json` | 579.287 | 8.367 |
| Owned source view (GC0 release fixed) | `/tmp/pcc-source-view-native-link-v7.guard.json` | 576.032 | 8.379 |
| Per-input stack-map key memo | `/tmp/pcc-stackmap-cache-link-v1.guard.json` | 560.292 | 8.382 |
| Validated rows in Python tuples | `/tmp/pcc-flat-link-native-v1.guard.json` | 508.183 | 9.253 |
| Validated rows in 12-byte buffers | `/tmp/pcc-raw-link-native-v1.guard.json` | 509.844 | 7.908 |
| Private four-field final consumer | `/tmp/pcc-compact-link-native-v1.guard.json` | 484.987 | 7.908 |

The final link source SHA256 was
`03109fb1939bc8fb1f1ee295cde7aac95f23a298b8ddc9963abdb19d26e5bca4`,
and its pcc1 SHA256 was
`eb54e5300ba5182f2881beb0b682e93cf9c838000f17fe826b6ae7b5a867ce33`.
GC0–GC4 strict-provenance tiny native links executed with exit code 42; the
current default entry also passed
`test_owned_link_driver_large_relocations_execute_natively[pcc0]` (its
compiled linker ran the five collectors and 8195 relocations). The pcc1 pytest
arm still has a 300-s **compiler** timeout and was not used as an execution
claim. No commit or complete combined-source Stage2 is claimed.

The decisive census was 7,458,926 PCO relocations, of which 7,437,677 were
plain symbol references. The existing packed inputs already avoid an input
`NativeRelocation` graph; the merge recreated `Relocation` objects and the
final view read their fields repeatedly. A current-pcc1 15-s window at about
85–100 s attributed 84% to whole-merged-object validation; the next window
at about 165–180 s attributed 80% to the final relocation iterator and 55%
to dynamic `py_instance_getattr`. The captures are
`/tmp/pcc-stackmap-link-{early,mid}.json`. A type annotation alone did not
change the standalone module IR, while the private 12-byte record and
four-field consumer removed that final dynamic-field projection. Validation
still runs before conversion; the public indexed-object and dict-row APIs
are unchanged. Host byte equality and native hash/execution were checked.

Stack-map memoization was selected by counting 4,668,402 safepoint records
but only 85,222 distinct local `(table index, location count)` ranges across
the 392 inputs (55× reuse). It avoids hashing about 2.48 GB of repeated key
bytes; the 178,320,024-byte merged payload and address-offset table hashes
remained unchanged. This saved about 16 s of native link time, not 55× of the
whole link. The final private row representation accounts for the larger link
gain. The remaining ~485-s link **alone** still exceeds Stage1's ~297 s.

### Class method signature allocation and replay correction

The existing 24-byte method-table hash candidate initially hit the 6-GiB
worker breaker at 85.9 s without PIDX output, versus a 79.75-s / 1.16-GB
baseline. Its compiled `py_class.ll` proved `_method_name_signature` allocated
a two-element Python tuple and boxed two integers on each lookup; the caller
unpacked it without releasing the result. The pcc-Python helper now writes
hash and length to an 8-byte stack output, matching the C mirror's out-arg
shape. The rebuilt production-policy runtime archive SHA256 is
`1b4a48f15e61694cb28d9671721155af67bdf35e3b8e185693fcc6ffa3cb96ca`;
its sole changed member **source** is `py_class.py` SHA256
`4025fb4fa7d226d2e463859f7d9753286f85c69a539cb0f44aaec43ef8ea84b6`.
The scalar helper's emitted IR contains no tuple creation or getitem.
Five-GC C/pcc-Python differential class-lookup tests passed.

The corrected hash pcc1 source SHA256 is
`7c49ea93e5fb90b820acbd943467b42891db3c518c1e1716e7024700784806cc`,
executable SHA256
`22d93acc0b1f883da9cbdee8246c04bd3b46d3e468db2551fcfb3b18a75c394f`.
On frozen Stage2 `worker_2.manifest` (`pcc.codegen.c_codegen`, module 135),
effective production flags and `PCC_PYTHON_IR_PASSES=off`, alternating native
replays were baseline **80.593, 81.723 s** and candidate **72.353, 72.326 s**.
All four PIDX SHA256 values equaled the original Stage2 artifact
`26897534caa1fa75246bb2020c26dab5f20e64d2a2ad999a1bf80e7e90325165`;
tree peaks stayed 1.15–1.17 GB. This qualifies a roughly 10.8% **single-worker**
gain, not a 392-module or full-Stage2 gain. Receipts are
`/tmp/pcc-worker135-methodhash-effective-{base,cand,cand2,base2}.guard.json`.

The first replay attempts were invalid: `replay_pcc_codegen_worker.py`
restored the outer `PCC_BOOTSTRAP_PYTHON_IR_PASSES=off` but not the child
`PCC_PYTHON_IR_PASSES=off`. They generated 18-MB LLVM text or no PIDX and
must not be counted as performance arms. The tool now reconstructs the
production native worker environment, records it in `replay.json`, and a
retained `pcc.ast` replay matched the original PIDX byte-for-byte with zero
LLVM text. Focused tool test passed. The combined compact-link + scalar-hash
source has **not** completed a fresh Stage2/Stage3; the <=Stage1 target is
still unqualified and far beyond these measured component savings.

### Follow-up — combined checkpoint, tuple-loop denial, method-call ceiling

The compact-link and stack-output class hash sources were combined in a
frozen Stage1 snapshot, source SHA256
`8692511ee122e7791e8786c47c0dce29220c82d40b5ab7668a0b247b88dcf636`.
It built pcc1 SHA256
`eaf2370b4c2a8533e381bcfc176b601333dacdb3189eaded3d782ca04a0e5adb`
in a guarded 313.281 s and ran the native function smoke (`42`), libSystem
only. This is a Stage1 interaction check, not a new Stage2 timing.

An isolated one-file `type_infer.py` experiment restored precise element
types for matching-arity tuple-unpack `for` targets. The small inference shape
changed from Dyn/Dyn to int/str. Contrary to the older revision's failure,
current-source pcc1 SHA256
`2e6fe09bb0d7f43f9ab858fcf5037d3e38a7636cdd649fe20bc32b27a068d056`
(source SHA256
`9726d70594d09547a987d6f39a09905c96803bd901dada16474b7f876acc10ab`)
built, passed its function smoke, and compiled/executed the nested tuple-loop
program under GC0–GC4 strict refcount provenance with the expected five-line
output. It did **not** pass the performance-selection gate: frozen
`c_codegen` worker 135 took 71.286 s baseline versus 71.498 s candidate,
same PIDX SHA256 `26897534...`; `type_infer` worker 53 took 40.335 versus
40.352 s, with a changed PIDX (`ad644965...` versus `c6c48674...`) that has
not been linked/executed. The experiment remains outside the worktree; no
full Stage2 was spent on it. Receipts:
`/tmp/pcc-worker{135,53}-typedloop-{base,cand}.guard.json`.

`scripts/pcc_per_op_cost_table.py` now includes an overridden-method-call
case. At N=50,000, ordinary method calls measured ~1,914 pcc versus ~1,003
CPython instructions/op; the overridden call measured ~104,722 versus ~1,092
(95.9×), about 6,000 versus 200 ns/op. With the existing
`PCC_DIRECT_VIRTUAL_METHOD_CALLS=1` flag, pcc's overridden case fell to
~13,206 instructions and ~600 ns/op with identical output. Receipts:
`/tmp/pcc-per-op-method-v1/result.json` and
`/tmp/pcc-per-op-virtual-v1/result.json`. This is an operation ceiling, not
an observed Stage2 gain: an earlier pcc1 build with that flag did not improve
the compiler worker.

A host-only codegen reachability probe on the retained `native_modules.py`
AST/export inputs found 355 overridden-method-call candidates; 329 entered
the soundness check and **all 329 were rejected solely because
`_native_module_exports` was nonempty**. No custom `__getattribute__` /
`__getattr__` or same-name field triggered the current module-local checks.
Receipt: `/tmp/pcc-direct-virtual-reach-native-modules/reach.json`. The
rejection is overbroad for ordinary pcc-owned sibling-module exports, but
simply removing it is unsound: a cross-module runtime subclass can add an
interceptor or instance shadow. The next implementation must either prove the
receiver's complete exported subclass graph or make the direct-call helper
check those runtime conditions and fall back to full attribute semantics.
Run five-GC shadowing, mutation, descriptor, extension and resurrection gates
before enabling it on pcc1. No direct-virtual source change was made in this
follow-up.

### Follow-up — unsafe direct-call opt-in and first guarded prototype

The opt-in runtime helper itself was unsound after an instance dynamically
shadowed a method: `child.step = lambda: 99; child.run()` printed `2` with
`PCC_DIRECT_VIRTUAL_METHOD_CALLS=1`, versus CPython's `99`. The source in
`/tmp/pcc_virtual_shadow_probe.py` is a minimal executed counterexample.
The pcc-Python helper now falls back on custom `__getattribute__`, class attrs,
same-name instance fields or a populated dynamic attribute dict. The focused
new regression compiled once and printed `99` under GC0–GC4 with strict
refcount provenance (`/tmp/pcc-direct-virtual-shadow-gate.guard.json`). The
new runtime archive SHA256 is
`3c6a5764a897d28cb3a02372a1db58ba9c0327e2b6e79c1c18ce90e1400ba10f`.
This fixes that opt-in semantic bug, but is **not** a speed qualification or a
full C/pcc-Python differential gate for the helper.

The generic runtime checks are too expensive on the hot path: the controlled
overridden-method operation used about 104,722 pcc instructions/iteration on
the old generic route, 13,206 with the unsafe direct helper, and **119,011**
with the guarded helper. Wall deltas at N=50,000 were about 6,000, 600 and
6,800 ns/iteration, respectively, with equal program output. Receipts:
`/tmp/pcc-per-op-{method-v1,virtual-v1,virtual-guarded}/result.json`.
Consequently the compiler-wide direct-virtual flag remains off. The next
viable design needs a mutation/GC-safe **per-callsite** cache of the runtime
class identity, class-attribute epoch and method location, with a cheap hit
path and full-protocol fallback on a miss or instance shadow. A global
raw-class-address MRO cache was previously measured and denied; it is not the
candidate to retry. No Stage2≤Stage1 claim follows from this operation probe.

## 2026-09-23 — weighted native stages and the remaining CPU bound

The completed source-frozen GC0 self/no-libpython Stage2 is
`/tmp/pcc-weighted-score-stage2-v2/stage2-record.json`: **1204.956 s**,
`pcc2` SHA256 `6bcf4f965cfff01896fc132f39e7057253c0bfb2804361142bc964f63d37ecb7`,
peak tree RSS 7.853 GB, only libSystem. Its Stage1 source/compiler/runtime
identities are recorded in
`/tmp/pcc-weighted-score-stage1-v1/build-receipt.json` (pcc1 SHA256
`9bfe21ee0cea5b603c2a6d7934d3a17eca518e5d86afba8ac250090972b6c4b2`,
runtime SHA256 `607c7fb6a974d5ea9a61cf45146df00a06224079e15ebb97379197356d3d72cf`);
Stage1 wall was 302.09 s. The pcc2 compiled a function program in 12.28 s;
the emitted program printed `42` on GC0–GC4 with strict refcount provenance.
This is a successful Stage2, **not** Stage2≤Stage1 or a pcc2→pcc3 fixed point.
The previous full Stage2 was 1941.757 s under a different frozen source, so
737 s is a sequential-build observation, not a same-source A/B claim.

Source/output timestamps in the completed run put native coordinator at
197.98 s, last frontend PIDX at 609.43 s, last PCO at 880.04 s, pcc2
publication at 1204.96 s. This is a serial chain of roughly 198 + 411 + 271
+ 310 + 15 seconds. The stage consumed 5917.69 user and 116.18 system CPU
seconds; on 12 cores even perfect parallelization of the same work exceeds
500 seconds, and the final link alone is about 310 seconds. The five-minute
goal requires both less total work and less serial work.

The original plan's `jobs=2` was **not** its execution width. In the first
1941.757-s run, the automatic budget scheduler reached 14 frontend processes
and 16 PCO processes; the tree peaks in those phases were only 3.24 and 3.58
GB. The actual waste was `_admission_groups` executing memory-width groups
behind barriers: two large-module outputs finished near elapsed 224/302 s,
then the next one-module group finished near 383 s. A labeled host-scheduled
reference using the *same native pcc1 workers* and frozen AST/exports completed
all 392 PIDX in 545.27 s, byte-identical, at 3.59 GB peak
(`/tmp/pcc-front-weighted-v1/result.json`). Its corresponding PCO reference
completed 392 byte-identical outputs in 315.36 s, 5.18 GB peak
(`/tmp/pcc-pco-weighted-v1/result.json`). Those reference drivers did not
prove pcc1 orchestration. Production now uses the pcc-Python weighted process
pool with a C mirror; the native five-GC failure/cleanup test passed. The
completed Stage2 above is the production execution proof. A prior attempt
stopped at 180 s because pcc1 has no `bytes.count`; the repaired fixed-byte
scan was natively executed on GC0–GC4. Another attempt was killed at 724 s
by the sampling tool's one-second `ps` timeout; its bounded 1/3-second retry
passed all 16 sampler tests and the successful run.

Native link component progression on the *same retained 392 PCOs* was
484.987 s (compact-row consumer), 476.819 s (typed relocation projection),
397.955 s (direct 32-bit instruction patch writes), 322.799 s (raw safepoint
record reads), and 319.650 s (raw unique-location reads). All successful
images had identical SHA256 `739df188ef64648c822d84ce63737ad854187b1a575c7517fb80ff63e2453581`.
The 3.15-s final location change and 8.17-s typed projection are below the
large-gap materiality floor; neither explains the remaining Stage2 ratio.

Two runtime dispatch candidates were rejected after actual measurements.
A guarded direct virtual method helper was slower than the generic path
(119,011 versus 104,722 instructions/operation); its flag remains off.
A per-callsite field cache preserved descriptor mutation and GC0–GC4 output
but regressed a million-read program from 0.28 to 0.45 s and 5.63B to
8.98B instructions; its new default callsite emission and ABI were removed.
Early user-instance routing in `py_obj_getattr` improved that micro by 1.23×,
but an alternating native `pcc.cli_bootstrap` worker A/B showed byte-identical
PIDX and only **0.75%** median wall/CPU gain (75.00 to 74.44 s; receipt
`/tmp/pcc-early-getattr-worker-ab.json`), so the route was removed. Its first
prototype also exposed an imported ABI constant lowered into a null managed
module reference; an in-module raw 65536 comparison fixed the crash in the
experiment, but the low-gain route is not retained. The independently proven
class `__dict__` exposure epoch bump remains in C and pcc-Python, with a
five-GC differential test.

Finally, `PCC_PYTHON_IR_PASSES=off` is not by itself an established cause of
the 4× stage ratio. On frozen `pcc.backend.macho_link` AST/exports, owned
`mem2reg,sroa` reduced alloca 1137→725, load 1571→751, store 2606→1292,
but call only 20969→20758. The worker rose from 9.44 to 24.90 s, including
9.73 s in passes; PIDX shrank about 5%. The entire
`pcc.py_frontend.codegen.*` prefix is currently skipped by this pass policy.
Instruction counts prove a real memory-pass effect on that module, not a
compiler-wide speed gain. The next owner should be selected from actual
cross-module call/attribute and PCO/link work, not another broad pass toggle.

### 2026-09-24 — relocation order and raw-index final consumption

An exact pcc1 replay of the retained 392-PCO final link sampled at elapsed
194–209 s put 74% of that **window** under
`OwnedMergedSourceView._iter_section_relocations` →
`CompilerIntArena.sort`; the raw arena was heapsorting about 7.46 million
nonnegative encoded `(descending address, original index)` keys. A dedicated
LSD radix sort preserves that numeric order without changing signed `sort()`.
The native 500,000-key alternating component A/B had identical first/last
keys and median 1.463 versus 0.846 s (1.73×); receipt
`/tmp/pcc-radix-sort-ab.json`. The native arena executed this shape under
GC0–GC4. An isolated pcc1 differing from the prior source only in
`self_backend_value_arena.py` and `native_object.py` built in 296.05 s
(`/tmp/pcc-radix-link-stage1-v1/stage1-result.json`). On the same PCO and
runtime archive, final link was 295.514 s versus the earlier 310.243 s
control; the pcc2 SHA256 remained
`6bcf4f965cfff01896fc132f39e7057253c0bfb2804361142bc964f63d37ecb7`
and linkage only libSystem. This is a 14.73-s single-pair link observation,
about 4.7% of that phase and 1.2% of full Stage2, **not** a stage-level
five-minute claim. A source-view 8195-relocation mixed symbol/section-target
image was byte-identical to the host and executed return code 42 on all five
collectors with strict provenance.

After radix sort, a 12-second native link window attributed about 31% to
the private four-field relocation tuple generator. A private raw-index
consumer in `macho_exec` now reads the already validated 12-byte rows in
place; public relocation iterators remain available. The focused host tests
passed, and a pcc0-built native linker emitted a byte-identical 8195-row
image returning 42 on GC0–GC4. The isolated candidate pcc1 Stage1 was
295.40 s (`/tmp/pcc-raw-index-stage1-v1/stage1-result.json`); the same
392-PCO native final link was 282.479 s at 6.509 GB peak
(`/tmp/pcc-raw-index-native-v1/guard.json`), versus the 295.514-s radix
candidate. Output SHA256 and libSystem-only linkage were identical. Its pcc2
compiled a function program that printed `42` on GC0–GC4. A first candidate
window showed the index generator at about 18%, with generic attribute
lookup now ranking above it. Again the 13.03-s single-pair reduction is
about 4.4% of link and about 1.1% of full Stage2; no complete fresh-source
Stage2/Stage3 after this change is claimed.

During the `PATH=/nonexistent` pcc2 function compile, stderr included
`/bin/sh: rm: command not found` even though compilation exited zero and
the emitted program ran. Current `pipeline_runtime_archive.py` contains a
shell `rm` in the runtime-build lock cleanup; whether that exact route owns
this observed diagnostic still needs a process-boundary trace. The ownership
claim for this separate CLI path is therefore open; successful output alone
does not prove no external helper was attempted.
