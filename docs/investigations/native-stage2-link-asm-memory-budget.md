# Investigation: native Stage2 link exceeds its budget while assembling inputs

## Status

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
