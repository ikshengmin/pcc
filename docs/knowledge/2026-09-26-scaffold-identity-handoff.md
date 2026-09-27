# Scaffold identity migration — Mac/ARM64 checkpoint, 2026-09-26

The active task is to finish the identity-based scaffold migration without a
self-host, five-GC, integration or performance regression. The shared checkout
is HEAD 390fcac7 plus uncommitted scaffold work and concurrent platform work.
Nothing in this checkpoint was committed. Preserve the other platform edits.
Do not infer today's Mac result from the changing shared tree: the native runs
below used the sealed V8 source snapshot, which excludes the platform edits.

## Resumed validation: 2026-09-27

The user explicitly revoked the quota pause with “不用管配额一直弄完吧”.
Continue the authorized work without quota checkpoints. Mac qualification
still precedes the other three platform execution gates. Nothing was committed.

### Latest: GC1/2 gray accounting defect reproduced natively

The GC1/2 fix is now in shared source and passed its focused gates. The same
418-PCO link still needs another correction; do not call the timeout fixed.

- V2 fixtures at `/tmp/pcc-gc12-counter-regressions-v2-0ff9iynh` fix only the
  raw output harness (c_rawptr buffer argument, function-local output).
  `/tmp/pcc-gc12-gray-counter-pm99wifd/native-counter-tests-v2/results.json`
  records **all eight native GC1/2 shapes passed**, unchanged inputs.
- Shared persistent gate is `tests/python/test_gc_gray_count_lifecycle.py`,
  with four fixtures in `tests/fixtures/native/gc_gray_count`. The existing
  dispatcher exact-closure test now includes the one new increment import.
  The wrapper later passed eight native cases with the matching i64 frozen
  source/runtime described below; do not run the broader shared tree against
  a V16-derived archive and label that a current-source qualification.
- Corrected-runtime Stage1 completed in **190.5611 s**, peak
  **5,577,244,672 bytes**, libSystem only. Compiler SHA256
  `1dc41c8e16178d4dd4e91d0556a7d9187c99bd765c24c4514a824e50eeb3e9ec`;
  receipt `/tmp/pcc-gc12-gray-counter-pm99wifd/stage1/verified.json`.
  Its disassembly contains the new first-free decrement call.
- Same original PCOs and original output runtime with that compiler's GC1:
  `gc1-link/process.json` in the same root is **still a 300 s timeout**.
  Late RSS drops to 4.64 GB versus original 6.93 GB; no final binary exists.
  `gc1-link/verified.json` confirms unchanged inputs and child cleanup.
- A temporary external diagnostic sampler extends the existing PC sampler
  with a separate nine-global sidecar. Source/tools at
  `/tmp/pcc-gc-state-sampler-candidate-u680f_u2`; parent compiled it with host
  clang **only as a diagnostic dylib**, never a production artifact. The map
  is bound to exact native compiler SHA and nm symbols, checked again inside
  MH_EXECUTE. The smoke run's decoded state has config=ready; missing reads
  remain Unavailable. Old PC raw protocol is unchanged. Actual link state/profile
  completed at the same 300 s timeout/8 GiB guard in
  `/tmp/pcc-gc12-gray-counter-pm99wifd/gc1-state-profile`; inputs and cleanup verified.
- The 2031 valid aligned records now show actual signed-i32 debt overflow:
  54.340→54.486 s, 2,140,800,702→−2,146,202,786, with 51,709 new allocations
  and no marking/discharge. Debt stayed negative about 62.5 s. Live reached
  2,146,126,313 then zero at the first preparation. The latter is consistent
  with overflow plus subtraction clamp; the sparse samples did not directly
  capture negative live. In the last 102 s, live was almost always 4,104 bytes
  while up to 1,286,481 objects were gray. 43 observed mark completions have
  median 2.373 s cycle period. This is repeated complete marking with broken
  byte accounting, not the earlier phantom-gray loop. No sweep was sampled.
- Independently reviewed i64 candidate:
  `/tmp/pcc-gc-byte-counters-i64-9v6t760e`. Eight production files close all
  26 literal accesses to live/debt/override; seven runtime objects require
  rebuilding. Default pacing is unchanged. Threshold math avoids intermediate
  overflow while preserving floor division, saturating only the final result.
  The budget retains the existing cap. Review:
  `/tmp/pcc-i64-width-audit-5e45ymze/REVIEW.md`.
- New full frozen source and output root:
  `/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-gc-i64-ub3grn7z`.
  1088-file build identity `837637c48c75b0b7440c167a6ae5ae61e1e897528a8d85a340429d1920174c59`.
  Seven objects rebuilt with identical seven-pass owned pipeline, preserving
  the prior gray-fixed dispatcher and other objects. Runtime archive SHA256
  `959b72cae9a0954f67a066e3e399c0227be7a47252e19db3e2749aa1e9991ff9`.
  Width changes and five test-file updates are now in shared source (no commit).
  Two boundary pytest cases and 24 scoped scheduler/object/telemetry/root tests
  passed; the telemetry C oracle exercises the high debt word. New Stage1 is
  **192.4407 s**, peak **5,529,223,168 bytes**, libSystem only; compiler SHA256
  `d38ebfd4229c7e39072a3af618506241d148d5bf93241a5b817cc3edf62fc948`.
  Its two native-compiled probes passed all ten executions (`native-probes/verified.json`),
  including real 40-byte object allocation/free across 2 GiB. The persistent
  native gray-counter wrapper is now actually run: eight tests passed in
  `gray-tests`, with matching archive/source and forbidden host Python/cc.
  The same 418-PCO GC1 link still hit 300 s at `gc1-link` (peak
  7,310,180,352 bytes); input identities and owned-process cleanup verified.
  It is not a solved performance gate. The following diagnostic proves the
  overflow/mark-loop mechanism is gone, so stop tuning that loop.
- Full new diagnostic (`gc1-state-profile` under that root) also reached 300 s:
  all 2022 aligned state records valid; live maximum 2,376,016,261 bytes,
  debt maximum 7,168,671,111 bytes, no negative counters, and **gray_count and
  mark_active are zero in every record**. 101074 PC records show distributed
  runtime cost: memset 9.08%, decref preparation 5.24%, index find 3.94%, incref
  preparation 3.73%, graph unlock 3.66%, read barrier 3.42%, thread check 2.52%,
  store commit 2.51%, index remove 2.41%, store finish 2.18%, frame replace 2.16%.
  All inputs unchanged and recorded children gone (`verified.json`).
- Read-only machine-code audit directly confirms that the compiler itself has
  i64 globals/loads/stores and the new quotient/remainder threshold sequence:
  `/tmp/pcc-i64-embedded-runtime-audit-iqj7j6dg/receipt.json`. Five functions'
  instruction streams match new runtime objects modulo link relocations.
- Same new compiler and original link inputs under GC0 complete in 113.3069 s,
  peak 4,995,219,456 bytes; output exactly reproduces original pcc2 SHA
  `b4d936cc29e4244169f459ecb419efccae157db932c7de331e034a47dbdc4e1c`,
  and emitted binary `--help` returns 0. `gc0-link/verified.json` records a
  measurement-wrapper limitation: `/usr/bin/time -l` returns 1 after the
  compiler finishes because sandbox denies sysctl kern.clockrate. Do not label
  its whole wrapper green or repeat the expensive link just for that wrapper.
  The complete artifact, exact hash and native startup independently verify
  the link result. GC1's same-input gap is at least 2.65x.
- Actual owned memset is an already promoted eight-byte loop, not a host stub.
  Its callers are root stores, root/ptr plan initialization and frame nodes;
  perfect elimination only permits about 1.10x. Initial source/IR review shows
  128-byte transaction arrays escape into prepare/commit/finish calls; the
  current SROA covers small literal structs, not these byte arrays, and
  inline-defined's threshold is 32 instructions. Simply adding another SROA
  invocation would not close this gap. Review whole root/frame/transaction
  representation before another sub-5% leaf experiment.
- The original integration scenario was re-run once with the two verified
  correctness fixes, original GC1 600 s stage cap and 8 GiB cap:
  `/tmp/pcc-gc-i64-gc1-chain-01/stage2`. Driver/1097 input bindings at
  `/tmp/pcc-gc1-i64-chain-prep`; includes all 1088 frozen source entries.
  Native deferred frontend/codegen/link only; Stage3 is a separate command
  requiring Stage2 success. Raw bytes are compared first; metadata normalization
  is restricted to copies and named ranges only if needed. **Stage2 stopped at
  MEMORY_LIMIT after 81.6088 s, peak 8,593,227,776 bytes**, before any PIDX/PCO.
  All 1097 input hashes unchanged and owned children gone. No Stage3 started.
  418 export results had completed; 53 summary manifests existed and only 15
  summary results completed. This is the early summary pool, not deferred PCO
  scheduling. Its `compiled_native_summary_jobs` subtracts a fixed 3 GiB driver
  reserve and assumes 512 MiB per child, admitting 10 under 8 GiB. Recorded
  driver RSS was 4,425,498,624 bytes and largest worker 723,648,512. The old GC1
  auto run already peaked at 8,295,235,584 bytes, so do not attribute the entire
  issue to the new counter widths; the old policy was near the cap too.
  Next: audit retained export/AST ownership and reuse existing owned RSS API
  for adaptive reservations, rather than raising the cap or choosing a smaller
  constant without evidence. No new fixed point/full-GC qualification claimed.
- Separate signed-i64 diagnostic used in the above run:
  `/tmp/pcc-gc-state-sampler-i64-v2-j62q8olh`. New GC raw/map version 2 rejects
  old artifacts; pointer fields stay unsigned addresses and live/debt are
  signed i64. PC protocol is unchanged. Generate a map for the exact new binary
  before sampling; never reuse the i32 map. Native --help smoke had 21 aligned
  records (19 readable), and 23 synthetic decoder cases passed at
  `/tmp/pcc-gc-state-i64-decoder-selfcheck-v2`, including signed/unsigned high-word
  and stale-format/mask/index/PC/SHA rejection. External clang built only this
  diagnostic dylib, never a production runtime or compiler.

- Summary sizing now has actual frozen-native calibration at
  `/tmp/pcc-summary-budget-probe-2w6nb7tr`. Original manifest inputs are retained;
  only result TSV/output-directory fields changed for replay. Original hashes
  remained equal. GC1 maximum-sum batch (26,024,154 AST bytes) took 6.333 s,
  observed peak 1,252,589,568 bytes; largest-single-AST batch (20,348,450 total)
  took 5.533 s, peak 947,159,040; smallest two-module batch (471,080 total) took
  0.812 s, peak 172,851,200. All share the same 2,381,604-byte export projection.
  This rejects both the old 512 MiB estimate and blindly using the full-codegen
  6 GiB formula for this summary workload.
- The same maximum-sum batch was executed under GC0–4. Observed peaks:
  677,199,872 / 1,252,589,568 / 1,252,556,800 / 857,636,864 / 1,366,327,296 bytes;
  wall times 2.118 / 6.333 / 6.303 / 7.121 / 10.789 s. All eight summary wire
  hashes are identical across collectors (`five-gc-summary-peaks.json`). These
  are 250 ms sampled peaks (9–42 samples), not absolute peak guarantees or
  full five-GC self-host qualification. Keep budget headroom and the watchdog.
- Active unmerged work: adaptive native summary reservation using the existing
  `pcc_os_current_rss_bytes` and actual collector, with constant-memory file-size
  reads (this freeze's old getsize reads whole files); compact opaque128B store
  transactions preserving deferred tails/logging/provenance; and early release
  of dead export command/edge containers. Each remains separate for attribution.
  No new runtime threshold changes or larger memory/time caps are authorized
  as a workaround. See the later ACTIVE workload entry for current execution.
- Deferred candidates from read-only/authoring subwork, not qualified or applied:
  local identity v3 `/tmp/pcc-local-identity-closed-entry-v3-qozs1wzb` (closed-entry
  AST witness and conservative library/import rejection; not flag retirement);
  revised fspath `/tmp/pcc-fspath-candidate-v2-76gl1n_9` patch
  `8799434e41da081e8ff41117aaa458830e3b5e0afed91a291699f46f23037b06`
  (25 files, latest ten-slot root cleanup needs independent review; fd-stat and
  C-extension descriptors remain open, explicit rejection is not implementation);
  text-memo lifetime `/tmp/pcc-import-scan-lifetime-859sopl7` (two pure str→str
  caches, no AST cache and no evidence of a multi-GiB RSS gain). Do not conflate
  any of these with the two already validated GC accounting fixes.

- Adaptive-budget candidate (not yet in shared source):
  `/tmp/pcc-worker-rss-budget-candidate-tcfxcntc`, production three-file patch
  `9a405c0b432c0ea1538b4c87e63b6add60b8475ebfef5801aa19f3e70c393c2e`.
  Full validation freeze `/tmp/pcc-worker-rss-budget-validation-hwdwxssd`,
  1088-source identity `0f97ec88a1611f5db889d1e2911befca60d798522021344e44f4db87e249335c`.
  It reuses owned RSS and the actual collector, reserves measured parent RSS
  plus headroom, estimates summary batches by observed collector/input size,
  splits overlarge batches and refuses a single task that cannot fit. The
  executor is still a fixed-width pool; estimates do not replace the watchdog.
  This original GC1 input computes two simultaneous workers rather than ten.
- Budget focused gate: 22 host policy tests passed. The first native harness
  used a single-file compile without including its imported production module,
  producing ImportError before entering RSS logic; preserved under `native-probe`.
  Corrected explicit multi-module host compile/native execution passed all five
  collectors (`native-probe-v2`, one pytest case). No production code was changed
  to bypass that harness error; corrected test source is in the validation tree.
- Budget-only Stage1 passed in **188.6385 s**, peak **5,535,121,408 bytes**,
  libSystem only, compiler SHA256
  `46ae36ce8483d3a092a8887ca39d670fc1166bdd1f52c68c431188a5f73e81e4`.
  The same exact production provider was then packaged as a minimal pcc.__main__
  closure and compiled by that native pcc1; five emitted executions verify real
  RSS, budget refusal, actual collector despite changed env, and the input-size
  plan. Program SHA256 `6a55acea3d9a752066eb738a120edbc2abb18a6792df727137bf7575a37a04b2`.
  Receipt `native-compiler-probe/execute-verified.json`. All 1088 build inputs unchanged.
- Budget-v1 Stage2 at `/tmp/pcc-gc1-budget-chain-01/stage2` reached the unchanged
  600 s cap: 417 PIDX / 51 PCO, no pcc2, peak 7,483,064,320 bytes. All 1097
  bound inputs unchanged and recorded children gone; Stage3 was not started.
  It passed the former summary OOM point but is not a timing/fixed-point pass.
  Frontend setup was 172.610 s versus old127.297; export/summary/merge/preload
  aggregate grew44.864 s, including pre-index collect0.946→6.284 s. Preload
  remained about27.5 s. Summary width10→2 is observed, but no separate summary
  timing permits attributing all remaining time to it.
  **Correction:** plan safe_jobs changed2→1, but current auto+budget weighted
  executor does not consume it; do not attribute this run's slowdown to that
  field. Its departing-parent RSS admission is still a wrong-owner boundary.
  New AST wire bytes grew6.15% mainly from repeated SourceSpan.file prefix;
  414/418 ASTs match after only that prefix normalization. Common PIDX total
  grew0.043%, so that alone does not explain the PCO progress gap.
- Compact transaction v1 is blocked by independent static review: its new
  snapshot call can add a threaded safepoint between RC→0 and FINALIZING, and
  after GC4 relocation while a raw pointer is unrooted. It was never built or
  applied. v2 is `/tmp/pcc-compact-store-plan-v2-oe01ghmx`, production SHA
  `5117df68ad179588064cfd5fe93bdb9161884503a735c707892a9f9f50161da6`;
  full-mode AST projection now exactly equals original prepare statements and
  removes new wrapper/snapshot calls from that path. Independent re-review and
  threaded emitted-IR/execution tests remain pending; do not promote yet.
- Separate lifetime candidates now exist at
  `/tmp/pcc-export-reference-lifetime-vz6fvkh5/references` and
  `/tmp/pcc-export-reference-lifetime-vz6fvkh5/collection_before_summary`.
  The former severs dead command/result/edge/use/shard aliases after their final
  consumers; the latter additionally moves the existing one collection before
  summary launch, retaining the late inline-AST branch behavior. Neither changes
  required native_exports or dependencies. Both are only statically reviewed;
  actual RSS benefit and native roots have not been verified.

- Budget-v1 independent reviews found three issues and it is NOT in shared code:
  parent GC and child env could disagree; preload budget ran before deciding
  serial/empty/small work; deferred plans used the departing parent's RSS.
  V2 candidate `/tmp/pcc-worker-rss-budget-v2-7je_gbmd` closes these with a
  summary child-GC snapshot, actual-spawn checks, and current-driver sampling.
  Validation root `/tmp/pcc-worker-budget-v2-validation-lwbnn7ez`, 1088-source
  identity `07efe5992ee437c492d80443409d4dcfec09b5dcde0e3a3ee62b39189f7129bb`.
  32 host policy cases and two host-compiled native cases passed (five collectors
  plus actual conflicting-env child spawn). Stage1 passed191.4788 s, peak
  5,451,546,624 bytes, libSystem only; compiler SHA
  `2644d44e1a5a2b886f549f2576554f52558f96103984ec9f3ef441104a1800a3`.
  That pcc1 compiled the actual production provider/child fixtures; native
  execution again passed (`native-compiler-execution/verified.json`).
  Do not promote v2 yet: >=16 preload roots with insufficient worker budget
  must retain the prior legal parent-serial algorithm, rather than raise.
  Small independent v3 delta `/tmp/pcc-preload-serial-fallback-v3-g7absrtp`
  addresses this at the preload call site only; tests/native gate remain pending.
- Same-input PCO diagnostic now ran successfully:
  `/tmp/pcc-pco-runtime-ab-mtt6hn_5/comparison.json`. Module149 c_codegen PIDX
  70,044,728 bytes, SHA `ef5dec23e76d2359ea7a784666fb7d4d2b8ee6eb675a34854a299f0dba4a9dc0`.
  Original V16 GC1:79.0567 s /2,728,722,432-byte peak;
  gray+i64 GC1:78.4603 s /2,723,856,384-byte peak. Both exactly reproduce
  the20,057,371-byte PCO SHA
  `b2c36563265fee3ea12aaf2c45a2f7a1e1bf2d3634fa7dd6d433d8e92c2837e7`.
  This single pair shows no material emitter regression on this input, not a
  general speedup. Scheduling/overlap still needs actual dispatch evidence.
- Compact-store v2 validation root `/tmp/pcc-compact-store-validation-95q2ijpx`,
  source identity `535030f507aa2f1b37c46a58d823c5f19da87a977fa34eb7cc5fadf647bdf1a6`.
  First threaded IR test failed because it inspected empty legacy block views;
  actual IR had polls. Test-only repair
  `/tmp/pcc-compact-poll-diagnostic-v3-4g3gi2nu` materializes owned indexed blocks
  and joins abstract CFG states without dropping unknown branches. Five injected
  errors were rejected. Recompiled actual threaded IR gate then passed in
  `threaded-ir-v2`, with unchanged production SHA5117df68…61da6.
  Nonthread archive replaces only py_obj.o (170/171 members unchanged), SHA
  `6a9becd5db3debdab401871da8de8de69f5f7e090e296beabbbde5575cb25a2b`;
  object SHA `05df6b1403c1ff0f699ce4fe4ba1f88da781932d9554cb589f746649f4d2f94a`.
  Raw GC1 poison-token/state27 test passed, then all28 selected emitted-native
  cases passed in27.31 s (`native-focused`): modes, aliases, terminal lifetime,
  weakrefs/resurrection, logging/provenance and errors. Threaded runtime/native
  compiler/mixed-slot reentrant gates still pending; no performance claim.
- Complete owned threaded runtime construction was attempted at
  `/tmp/pcc-compact-threaded-prep-ikq3tl4l/run_runtime.py` (600 s/8GiB/sharedlock).
  Source identity3549 files `3d3eff90…846a8`, compact production unchanged.
  All171 objects fresh, no unmatched old threaded cache reused. Existing pthread
  kernel compile exception disables injected polls while preserving its own
  explicit polling; receipts retain exact settings. This build does not itself
  prove threaded execution or native compiler self-host. It stopped after
  14 objects (14.209 s, peak196,952,064 bytes) at threaded py_os_path_relpath:
  stale managed SSA unsafe.ptr.add.7668.246 has ambiguous root provenance.
  This is before candidate py_obj; no archive was published. Source/IR audit
  finds dynamic component views across _path_bytes_equal entry/loop polls,
  late/missing roots for both newly copied absolute strings, and public pin
  routines which themselves contain injected entry polls. The precise-map
  guard remains in force. An incomplete memcmp-only candidate is retained at
  `/tmp/pcc-relpath-leaf-candidate-mcod7ojg`; do not qualify it as a full fix.

- Budget v3 is NOW applied to shared source after scoped validation. Combined
  patch/identity live in `/tmp/pcc-worker-budget-v3-validation-4_w7_9ey`;
  all five production and three test/data files in the shared tree were read
  back byte-identical to validated source. No commit. 35 host policy tests pass.
  Source1088 identity `01758282cdf5e951a21a2c4f58add13898586f4566f26164e1c0f286be14745e`.
  Stage1 189.329 s /5,543,477,248-byte peak, libSystem only, compiler SHA
  `07f0aaa18b76d7cf0caf8ec5941e9f1ef96b1652220709dee5263859e71c1845`.
  Same17-module/16-sensitive-roots fixture: v2 correctly rejects its preload
  child budget; v3 succeeds in-parent at4GiB, then outputs preload-sum136 on
  GC0–4. New pcc1 compiles the RSS and real-child conflict probes; all pass.
  104 new artifacts/source/commands were read back and bound in
  `validation-final.json`, SHA256
  `36ec0378ed6c0a51aaa10d8929c4d189ae740edc4ff807d3ad911deac53a7442`.
  This is scoped native validation, not Stage2/3 or a speed/fixed-point claim.
- Compact v2 now also passes its native-compiler gate using original i64 pcc1
  to compile the poison-token program against runtime6a9…; GC1/2 executions
  pass (`/tmp/pcc-compact-store-validation-95q2ijpx/native-compiler`). Two more
  emitted-native cases pass at `mixed-native`: mixed19/11/27 plans, same-object
  multi-slot aliases, real reentrant finish from __del__/weakref callbacks,
  publication-before-callback, resurrection and final retirement. Test-only
  overlay `/tmp/pcc-compact-mixed-tests-overlay`; production hash stays5117df68…61da6.
  None of the compact production code is in shared source. Threaded qualification
  and controlled performance are still outstanding.
- Pin/path correctness candidate remains isolated, separate from compact-store
  performance work. Strict `freestanding_gc_root_operations` now owns unchanged
  pin/unpin header/metric bodies plus `pcc_gc_take_pinned_slot(ptr,i64)`;
  registered/pinned intermediate slots cover abspath/normpath/relpath and the
  frontend keeps arguments across later operand evaluation. Three real threaded
  wrapper IRs end frame_leave -> strict slot take -> ret without generated
  cleanup. No stackmap guard, thread support or no-GC call whitelist was relaxed.
  Candidate/source identities and v1-v5 IR/object receipts live under
  `/tmp/pcc-pin-path-keeper-candidate-68f72ol4/candidate-manifest.json`.
- Complete owned **threaded** 171-member runtime build now passes from isolated
  correctness-only source at `/tmp/pcc-pin-path-validation-4pp3qpeq`: 89.706s,
  peak234,209,280B, zero reused objects, archive2640feb6…be4b. Source3550 identity
  0db72ad9…cd00 stayed unchanged. Host-pcc compiled and executed ordinary-str
  abspath/relpath operand lifetime/error/retention fixtures under GC1 and GC4;
  both emitted `path-values-and-lifetimes-ok`. These are host frontend -> native
  execution receipts, not native-pcc1 compilation or full self-host evidence.
- Independent review caught root-cwd joining: cwd `/` plus an unconditional
  separator created a semantic POSIX `//` prefix. Correctness v2 changes only
  py_os_path.py (92517c52…ba9c5); `/tmp/pcc-pin-path-validation-v2-ynyoz8_v`
  source3550 identitydd2dd7c7…8dde1 includes the newer four-file test overlay.
  Its archive07663ecc…ec5d reuses170 objects with exact matching source/codegen
  checksums and rebuilds only py_os_path.o e6f86f25…e9ac. Initial launch was
  rejected before compilation by the **nonblocking** performance lock; the
  successful build receipt is `runtime-retry/process.json`, not the stale
  `runtime/process.json` RUNNING record from that rejected watchdog startup.
  Host-pcc -> native root-cwd/direct-normpath gates both pass for GC1 and GC4
  (`host-root-gc1`, `host-root-gc4`), including . / .., exactly-two versus three
  leading slashes, empty arguments and survivor values. No files are written
  in `/`; oracle and native execution use cwd `/`, all artifacts stay in tmp.
- The concurrent path gate is **not green**. Original GC1 program returned0
  with no completion marker (`host-concurrent-gc1`), hence failed. A separate
  marker diagnostic `/tmp/pcc-thread-event-diagnosis-v42ctrd2/output` reached
  all six Event initializers, main, Thread construction/start, then waited for
  ready without reaching the collector's first statement (30s timeout).
  This does not show a generally broken pthread bridge: the smaller GC1
  Thread(target=work) / sleep / is_alive / join probe at
  `/tmp/pcc-thread-target-probe-tjqwy4ec/output` passes with target-entry and
  completion markers (compile1.95s, execution0.52s). Binary inspection of the
  failing marked program shows both ordinary collector/main became __gen_resume
  functions. Event waits are intentional may_park roots for virtual threads;
  Two concrete owners bypass completion: trailing-main entry emits a raw call
  and unboxes the generator as an exit code, and the native callback adapter
  returns the factory result to Thread's one-shot py_obj_call. Existing ordinary
  direct-call lowering already uses emit_generator_may_park_sync. X86 is fixing
  trailing-main through that common lowering in isolated
  `/tmp/pcc-trailing-main-sync-candidate-MsAfyOHB`; arm implements a synchronous
  callback context through the callable dispatcher, with a compiler-produced
  PyFunc role bit to distinguish automatic lifting from source yield/async.
  Driving any returned generator by its result flag is insufficient: an
  ordinary function may intentionally return a continuation. Preserve raw
  factory semantics for explicit vthread.call/spawn. Do not remove Event effect
  roots to mask this, because gateway needs them. Both fixes are unqualified
  and absent from shared source.
- Trailing-main candidate now has scoped execution evidence (still isolated):
  patch0583015d…0f153b, production module_lifecycle_lowering636755a5…de5d96,
  `/tmp/pcc-trailing-main-validation-RiSeXQzk/validation-final.json`
  SHAd4c6d543…f46d97. Eight actual entry-IR tests plus16 host-pcc -> native GC1/4
  executions pass: None0, ordinary int7, parking int23, nested17, ValueError1,
  source generator/async constructed without executing their bodies. No xfail;
  source3694/runtime hashes unchanged, all four watchdogs leave no children,
  peak288,358,400B. New native-pcc1 producer and complete Thread callback gates
  are still pending. Arm's callback candidate is under construction at
  `/tmp/pcc-callback-sync-candidate-s9xkol8l/source`, with AUTO0x800000 and
  TRANSPARENT0x1000000 PyFunc roles plus TLS call/forward contexts. Context must
  be consumed by the actual semantic callee: an ordinary wrapper(fn) must not
  inherit a deferred call and receive a Gen instead of the nested call's value.
  Default ordinary-call overhead and its pointer/exception lifetimes need real
  IR/execution review before qualification; no runtime changes are shared yet.
- Dispatch trace candidate f9687a69…b1d296 remains isolated at
  `/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-chained-dispatch-trace-v2-au9_ckyi`.
  Host short gate10 passed in4.007s, peak150,339,584B, including trace I/O failure
  during cancellation without abandoning other children. Native17 items are
  unexecuted. Frozen host source3555 identity34a60de8…64f7e stayed unchanged;
  receipt `/tmp/pcc-dispatch-trace-host-validation-km8ii82x/verified.json`.
  The first native object gate failed correctly: unannotated telemetry helper
  locals generated managed ints/root operations. Old optimized-IR emission
  control and170 reused member identities passed; no new object/archive existed
  after that failure. Receipt:
  `/tmp/pcc-dispatch-trace-native-validation-x_aua0p0/failure-verified.json`.
  Frontend owns the current small execution slot while revising explicit
  machine lanes and repeating only the affected native gates. Revisions exposed
  explicit int-to-i64 typing, unavailable runtime-port i64(...) construction,
  then augmented-assignment managed lowering; the current proposal uses a
  real-IR-proved i64 identity bridge and explicit x=x+n telemetry assignments.
  No generic integer typing/lowering rule or strict IR assertion is weakened.
- Superseding trace candidate **v7** is
  `/tmp/pcc-chained-dispatch-trace-v7-lj6zigni`, patchdb91b017…c5a9f0;
  `/tmp/pcc-dispatch-trace-native-v7-q1pl9ion/final-verified.json`
  SHA18e9e5e5…8b12 binds the actual three successful gates. Strict raw/optimized
  event/error/scalar IR has no managed allocations/root calls; event calls only
  close/monotonic/strlen/write. Archivea677338d…fcaaf is threads0, exactly170
  reused members plus timeout.o1b622c20…29a41. Existing pcc1 compiled native
  parent/actors; GC0 budget case passes in30.569s, peak668.5MB,19 records with
  spawn0/1/2/3 and two budget blocks. Inventory prep1380us, prep-to-pool2250us;
  inputs unchanged, only libSystem dependencies, no remaining children.
  v6's missing trace was a real namespace mismatch: os.environ writes the owned
  pcc_platform environment, while the new helper read raw getenv. v7 reads the
  existing pcc_platform_getenv owner. The other16 native cases, whole Stage2 and
  observer-overhead qualification remain unrun; this is not shared promotion.
- New native compiler preparation is ready, not executed:
  `/tmp/pcc-budget-v3-pin-path-stage1-ofZOSTbU`. Read-only source3693 identity
  9e28e31b…3196ba is exact budget-v3 plus the independent correctness patch,
  root-cwd delta and tests (no compact/trace/later shared changes). Relative to
  i64:12 production files; all nine path patch preimages matched and five budget
  production files are unchanged. `run_phase.py runtime` prepares a complete
  fresh171-member threads0/plain archive; `run_phase.py stage1` refuses to start
  until that exact archive completes. Both are600s/8GiB/shared-lock phases.
- Native C pin-ABI gate has **not reached runtime execution**. Root's initial
  harness used unsupported bare -I; path test-only overlayv3 corrects it to
  --cpp-arg=-I (delta b8bd18d2…37ed00 under
  `/tmp/pcc-threaded-path-qualification-v3-fr1Xp8Yb`). With the correct CLI,
  compiler07f0 failed after49.7s on the public header probe:
  `Error: Not a constant expression: Constant`
  (`/tmp/pcc-pin-path-validation-v2-ynyoz8_v/native-c-pin-gc1-cli`). X86 reduced
  this to header-free enum VALUE=3 in0.49s; same frozen host-owned pcc compiles
  and executes it successfully. Native compiler disassembly proves the first
  _eval_const_expr isinstance loads `.class.pcc_llvm_capi_ir.Constant` instead
  of `.class.pcc_ast_c_ast.Constant`. Source owner is
  isinstance_lowering.class_name_from_expr: it discards the qualified ClassInfo
  returned by _ensure_native_module_alias_class_export and reuses the colliding
  short name. Generic correction and two-module collision tests are being
  prepared at `/tmp/pcc-isinstance-owner-candidate-VZUfYTag`; no package/name
  special-case is allowed. Diagnosis lives at
  `/tmp/pcc-native-c-constant-diagnosis-J3WmkNTN`. Native header-free return0
  separately reports `unsupported owned optimizer pass list`; this is still
  an open native C default-route gate, not a pin ABI failure.
- Generic isinstance owner patch is now scoped-green but still isolated:
  `/tmp/pcc-isinstance-owner-candidate-VZUfYTag/candidate.patch`
  SHAc2693658…23b71; production isinstance_lowering74c87c6e…8973c72.
  `/tmp/pcc-isinstance-owner-validation-jn4k8jr5/validation-final.json`
  SHAcd836e5c…3c016 binds9 passing pytest nodes: four real IR cases, four native
  same-name two-module tests covering import/input order and scaffold on/off
  (each executes GC1/4), and host-owned C enum IR/execution with explicit-O0.
  No new native pcc1 has compiled that enum yet, so default C is not qualified.
- X86 independently traced two more native C owners in07f0 machine code:
  raw exported method call_sig has annotation=null/Dyn -> caller ptr, while
  class-local self-call inference narrows emit_executable.optimize to Bool/i1
  from its sole self-call optimize=False. Boxed int0(ptr1) therefore arrives as
  True despite the preceding clamp. This is not a wrong bool API annotation:
  the unannotated source accepts bool/int. Generic formal-ABI stabilization
  must preserve incoming integer identity/value; casting optimize to bool or
  exporting bool everywhere is not a fix. Separately, function-local imported
  pass_names comes from an initial-NULL consumer alias cell with no binding
  store; list(NULL) becomes empty and the owned optimizer rejects it. Function
  imports need the existing owned-local _store_unpack_target protocol, not a
  global declaration alone. Evidence and exact source/addresses are in
  `/tmp/pcc-native-c-constant-diagnosis-J3WmkNTN/OPTIMIZER-DIAGNOSIS.md` and
  `optimizer-diagnosis.json`. X86 is implementing these as separate generic
  candidates with same/cross-module method and scope/closure/import regressions;
  no new native or default-route pass claim. Pointer-shaped class inference
  used by existing object-field optimization is a distinct soundness boundary.
- Frontend is preparing, not running, controlled compact-runtime performance:
  same retained418 V16 PCOs + one host-owned linker, two experiment-only native
  compiler bodies differing solely in embedded i64/control959 versus compact6a9
  runtime. First planned workload is the known c_codegen PIDX ef5dec…9dc0 ->
  expected PCO b2c365…3e7 under GC1, then GC0; afterward the full link remains
  capped300s with identical legacy6fac output-runtime input and original pcc2
  byte oracle. A timeout is censored evidence, never a speedup. These artifacts
  cannot qualify new-source pcc1 or compile a new runtime; ABI/relocation/source
  boundary audit must finish before construction. Compact is still unshared.
- That old-PCO performance construction has now run at
  `/tmp/pcc-compact-link-ab-prep-h4eily_l`: both host-owned relinks pass in48.65 /
  48.61s, peak3.29 /3.21GB, only libSystem. Control compilerfd9999c4…b55ae and
  compact6b77c7c9…b8bcd are explicitly benchmark-only. **The first control GC1
  emitter did not qualify**:78.493s, MEMORY_LIMIT at4,295,458,816B, no PCO, no
  ratio. All inputs unchanged; recorded child69115 is gone (cleanup receipt).
  No compact/GC0/full-link case was started. This differs from the original
  Stage1-body i64 compiler's same PIDX78.46s/2.72GB success; source equality and
  public-runtime ABI checks do not prove equal native representation/dispatch
  across stages. Frontend is auditing this before any cap adjustment or repeat.
  An existing alternative has been located but not run: original i64 host
  Stage1 itself selects direct native-object artifacts and supports
  PCC_DEFER_SELF_LINK_PLAN. It writes plan/manifest and preserves those PCOs
  without a source patch, enabling one host frontend construction and two
  runtime-only links. Verify the frozen route and compare the resulting control
  with original d38 before relying on this alternative. Do not replay an old
  deferred plan that can remove earlier outputs.

- Formal method ABI candidate A completed its focused gates at
  `/tmp/pcc-method-dyn-abi-validation-v2-d6133swb/validation-final.json`
  (SHA1e9cad8a…dc68). Five real IR nodes and four host-compiled program nodes
  pass, with eight actual GC1/4 executions. Production type_infer.py remains
  f852521f…bb7db; candidate patch05c373fa…1681b is under
  `/tmp/pcc-method-dyn-abi-v2-c8yfqm2t`. The v1 failure was an overly narrow
  test assertion requiring py_list_get instead of the valid py_list_getitem;
  v2 preserves direct Core field access and forbids generic getattr. No native
  new-source pcc1 or C default-route qualification yet.
- Local-from-import candidate B remains isolated and has exposed separate
  owners. Current frozen v5 is `/tmp/pcc-local-import-validation-v5-4an2ajgf`,
  source3694 identity58ecd59d…7a562, using threaded archive07663ecc…ec5d.
  Local imports now use the owned assignment protocol; import targets enter
  representation analysis, and explicit-global native sibling imports avoid
  incorrect CPython alias registration. v1 had a missing _CSTR declaration;
  v2/v3 main became a strict no-libpython stub because of that CPython alias
  prescan, not because tuple identity is unsupported. v4 removed the stub and
  exposed closure rebindings captured by value. v5 supplies lexical local and
  parameter names to the cell planner: real IR passes and actual GC1 execution
  gets past import/local/parameter closure-rebinding assertions. It then fails
  on provider.VALUES reassignment followed by a fresh import: attribute store
  writes consumer-private .modattr while import reads the module dictionary
  and ordinary module-global loads read .modvar. This is not a green B gate.
  Independent review also found planner/rewrite disagreement for nested global
  declarations; both fixes are in progress in separate candidates. Original
  failure receipts remain in each v1-v5 output directory; no timeout widening.
- Callback v9 and main composition now builds a complete fresh threaded
  runtime: `/tmp/pcc-callback-native-prep-_8zwr2l_/runtime/process.json`,
  171 members, zero reuse, 90.153s, peak218.6MiB, source unchanged and terminal
  children empty. Archive SHA256
  6cf1d070d5379907d81363dce2300667ddc5c3674cb04689c85f09cb27bfb6e5.
  The five runtime objects and six role IR nodes passed earlier; actual
  ordinary/Thread callback execution is now being tested. This build alone
  does not qualify native pcc1, concurrent paths or a self-host chain.
- Callback execution subsequently passed28 host-compiled native programs
  across GC1/4, plus the original concurrent path fixture twice. Latest
  test-only candidate v10 preserves all16 production hashes from v9.
  Tail receipts are in
  `/tmp/pcc-callback-constructor-concurrent-tail-7iuktc7f/verified-tail.json`:
  exact constructor markers, then `threaded-path-collector-ok` for GC1/4,
  stderr empty, children empty. The original fixture is byte unchanged and
  includes48 background collections, real OS-thread identity and retained
  abspath/normpath/relpath results. Initial direct-parking __init__ test failed
  the existing explicit dunder may_park rejection; the earlier static claim
  of reachability was wrong. v10 preserves that negative test and exercises
  the supported ordinary constructor that calls a dynamic callback instead.
  No generation gate was relaxed. These remain host frontend -> native
  evidence, not new-pcc1 compilation or five-GC fixed-point qualification.
- B's separate closure-scope correction now passes4 focused nodes at
  `/tmp/pcc-local-import-validation-v6-n3w4u0rs/scope-validation.json`
  (SHA45ba6f85…67e11), source3695 identity836ef788…9fd7d. Two actual IR nodes
  and four emitted GC1/4 executions cover scaffold on/off, explicit-global
  imported readers, intermediate global barriers, same-name parameters,
  method readers and shared nonlocal import/write cells. Production
  hoist_lowering.py SHA bd266772…162ae unifies the planner and rewrite scopes.
  This does not qualify the original B module-attribute-rebinding fixtures;
  their independent canonical-slot/namespace synchronization failure is open.
- A narrower callback ownership defect was then reproduced through the owned
  native PyFunc entry/header ABI at
  `/tmp/pcc-callback-capture-pin-v3-87iw3a2d/final.json`. A manually tagged
  continuation factory returning py_gen_completed(captures) left its captures
  PIN bit64 behind even though returned identity and metric delta were correct.
  This is a numeric owned-role ABI probe, not an advertised public C role macro
  or a pure-Python/CPython PyCFunction reproducer. Both original6cf GC1/4 runs
  returned31 with prior0/after64. The five-line correction also consults the
  still-live captures slot24 before the existing input-alias correction.
  Production patch2b1fb4e1…20021 changes only py_func.py to26f39860…076b1.
  Candidate archive762c1e9c…0c720 reuses170 exact objects and replaces only
  py_func.o4e77357d…48fe0; old retained IR re-emits byte-identically, and the
  new terminal take has no following call. The same compiled C object
  34376b48…dbb3b4 linked against each archive; eight GC1/4 × prior0/64 ×
  ordinary/factory cases pass with exact identity and metric_delta0. The
  independent pytest wrapper has not run; its fixture C bytes were executed.
  Initial harness attempts with bare -I and a stack-local frame-map definition
  failed before execution; later --cpp-arg=-I and a static frame map preserve
  the same four-root protocol without weakening the stackmap checker.
  Some sub-20ms commands left a final sampled PID after natural exit; separate
  ESRCH readbacks preserve, rather than rewrite, those process snapshots.
- Module attribute coherence now has a scoped candidate and execution proof:
  `/tmp/pcc-module-attr-pointer-v5-6p8en0lt/validation-final.json`
  SHA492e5629…67e10e records13 nodes and8 GC1/4 native executions. Its three
  production files are unchanged from v3: provider storage metadata, canonical
  constant-object reads, and direct managed-pointer attribute stores followed
  by namespace publication. Keep old/new keepers until publication completes;
  preserve the new canonical pin while retiring its temporary. Raw consumer
  reads of provider boxed bigints bypass the integer narrowing bridge, retaining
  identity. Provider self-read, third consumer, fresh import, self-assignment,
  weakrefs and finalizer reentry passed. Unknown producer ownership, raw scalar
  slots, known unsafe RHS and reexport writes fail explicitly; arbitrary
  setattr/__dict__, raw aliases and full cross-binding pin leases are not closed.
  Wire tests exercised both actual export formats. Diagnostic-test iterations
  corrected worker-wrapped exception matching and malloc's actual ptrtoint /
  raw i64 store representation; original failed receipts remain untouched.
- The original B lifetime gate then exposed a genuine frontend owner leak:
  `/tmp/pcc-local-import-lifetime-diagnosis-53_k4stw/fail_after_binding.ll`
  sets TOKEN.owned=1 but its err.exit only leaves five roots and returnsNULL.
  The exception/traceback has no TOKEN owner edge. Generic correction
  `/tmp/pcc-owned-error-cleanup-v3-n9vvcoct` (patch32cf4ab8…a54c32) registers
  owned physical slots and flags, clears flagged slots through one root-store
  before any frame leaves, and places leaves/return in a separate err.finish.
  Header/state declarations are present in all three host initialization/reset
  owners. Native for/comprehension iterators formerly held two references:
  the initial result plus an extra root-store retain. Their end path consumed
  both, but error/early return did not. Both producers now transfer one owner
  via store_root_take; normal owned cleanup clears the slot before releasing,
  and redundant iterator end clears are removed. All13 owned-local producer
  sites were inspected for the one-reference slot invariant.
  `/tmp/pcc-owned-error-v3-validation-l1zj2_ff/validation-final.json`
  SHA7cd3dd77…548408 records8 nodes/12 native GC1/4 executions, including every
  original B bindings/lifetime case plus early error/late binding, unbound
  branches, aliases, borrowed parameters, two finalizing locals, loop end/break/
  return/error, caught-loop target liveness and comprehension errors. New
  generator-activation coverage and updates to pre-existing IR ratchets are
  pending; no new-pcc1/bootstrap claim. All these fixes remain isolated.
- The generator gate found another distinct owner: a retained, completed
  generator still held its saved heap-frame cells after activation cleanup.
  The terminal correction is compiler-side, confined to known frame_slots;
  it does not change py_gen_set_done's arbitrary-userdata runtime ABI. An entry
  borrowed frame root remains healable, and a temporary keeper/pin protects
  the frame while cells are cleared, restoring any prior PIN bit afterward.
  Error cleanup runs after set_done; normal cleanup runs after py_gen_finish
  has retained the return value in StopIteration. Latest candidate
  `/tmp/pcc-generator-terminal-frame-v3-qiq0rgh3/validation-final.json`
  SHA1367d428…76058 records14 nodes/20 actual GC1/4 executions. They include
  retained generator/error/normal/returned-object identity, internal caught
  exceptions that continue yielding, and old objects owned solely by saved
  heap cells whose finalizers call gc.collect during cell clearing. Existing
  exact-int/for-target IR tests now assert the cold NULL slot-clear protocol
  while retaining hot tagged-loop restrictions. No new runtime API was added.
- Final historical-closure composition is frozen at
  `/tmp/pcc-final-compiler-prep-mt8q1r3l/frozen`, source3703 identity
  7f45635da677c529a66ebc9aa29272c43d7c74d1b0b012b2cff7b1dc4c94fe6a;
  build-input1094 identityb871d432…4e1c. Generator/callback overlap was merged
  by exact hunks and both application orders yielded identical bytes. All
  44 sealed scripts/specs/tools/evidence inputs passed readback. The fresh
  171-member threads0/plain runtime build then **failed at py_threading,
  module51**, during owned object emission: `managed slot is outside the final
  frame` from self_backend_precise_stackmaps.py:1047. Earlier50 objects remain
  as evidence; no completed archive or Stage1 exists. Inputs stayed unchanged
  and the watchdog left no children. Preserve runtime/phase-final.json,
  process.json, stderr and optimized py_threading.ll; arm is diagnosing the
  exact frame offset rather than relaxing the guard or widening the timeout.
- The blocker is alignment, not insufficient frame extent. Diagnostic
  `/tmp/pcc-aarch64-frame-diagnostic-7ei5_928/output/error.json` resolves
  user_py_threading__thread_invoke: a32-byte byte-array alloca starts at FP-60
  in a224-byte frame; its four roots are all misaligned by4 bytes. Shared
  stackprep used byte-array alignment1 and aligned the previous upper offset
  rather than the resulting downward-growing start address. Isolated patch
  `/tmp/pcc-stack-alloca-alignment-candidate-wapxojmn/production.patch`
  SHAa2bbd54c…076a6a gives allocas at least8-byte alignment and aligns
  offset+size. Nominal allocated spans and precise-root bounds checks remain.
  Original optimized IR SHA5ec8e612…62bb0 re-emits successfully under this
  candidate at `/tmp/pcc-stack-align-original-gate-vf4bljxj/output/receipt.json`:
  startFP-64, roots[-64,-56,-48,-40], sameframe224, object5d811853…6be167.
  Odd-size/layout/invalid-root and actual GC/address gates are still pending;
  no fresh full runtime retry or Stage1 has begun.
- This composition is not the latest shared checkout. Read-only audit
  `/tmp/pcc-shared-rebase-audit-ak0y3fg0/audit.json` found36 original-input
  differences and4 added pcc Python modules. The three-way integration audit
  `/tmp/pcc-shared-qualified-threeway-tbuiarb9/audit.json` identifies64 selected
  deltas:8 already shared,50 candidate-only,5 automatic dual-side merges and
  one native_os textual conflict to resolve without losing getsize error
  checking or unified path keepers. Current-only long-double/platform/extern/
  metadata/file-lock work stays intact. Mac's current runtime inventory is
  172 because it adds py_file_lock; historical171 receipts cannot qualify it.
  No integration patch has been applied to shared and nothing was committed.
- The unexpected Stage2-body performance control has two additional bounded
  diagnostics at `/tmp/pcc-stage-body-emit-diagnostic-v4x0_fiv`. Old d38 and
  relinked control each intentionally stop at30s (124), with no surviving
  children or input drift. Both decode/target-complete near453.65MB RSS and
  258.3MB managed heap, neither reaches transport-complete. Old/new peak RSS
  1.504/2.070GB and20–30s allocation deltas6.645M/8.809M show different
  transport allocation/retention, not normalized work or a compact speedup.
  Both report gray/mark_active/in_auto_step zero. No compact arm or larger-cap
  control rerun has started; the host-retained-PCO alternative remains unrun.
- The host-retained-PCO alternative subsequently ran and **compact has no
  measured gain**. `/tmp/pcc-host-body-compact-ab-cw9nb9bu` retained420
  host-produced PCOs (689,714,243B) in128.310s, peak5,552,324,608B; source1088
  identity837637c4…4c59 and all inputs stayed fixed. With those exact objects,
  `/tmp/pcc-host-body-compact-runner-yujtgrhp/build-control` linked in46.380s
  and reproduced original pcc1 d38ebfd4…c948 byte-for-byte. Compact linked in
  46.345s to e9d205ac…0248, differing only by embedded runtime6a9 versus959.
  The identical indexed-emitter input produced exact PCO b2c36563…83e7 in
  76.3418s/control versus76.9470s/compact, peak2.724/2.729GB. Both completed,
  all source/runtime/object identities matched, and no children remained.
  `decision.json` records one pair, ratio1.00793 and no promotion; this does
  not establish a statistically stable regression. Remaining GC0/full-link
  arms and compact threaded preparation were stopped because they cannot
  qualify an unmeasured performance benefit. After the earlier1.3% scalar-root
  and2.1% RC attempts, reassess transitive pipeline owners before further local
  root/refcount tuning. Correctness-only pin/path/callback work remains active.

Earlier evidence for the defect and correction:

- Original V16 native linker, same 418 PCOs (681,879,078 bytes), GC1:
  `/tmp/pcc-v16-gc1-link-99yh0qff/process.json`, **300 s timeout**, peak
  **7,303,987,200 bytes**, no output. All 421 compiler/runtime/manifest/PCO
  hashes were unchanged. `cleanup-verified.json` confirms recorded child PIDs
  no longer exist; `terminal_processes` is a pre-termination diagnostic snapshot.
- Same-cap external-sampler diagnostic:
  `/tmp/pcc-v16-gc1-link-profile-0s7f6r35/profile.json`, 101,090 records.
  `pcc_gc_tracing_step_cycle` is 34.7% of the whole timed-out run and **99.95%**
  of a late 10,000-record window spanning about 29 CPU seconds. Hot PCs are
  object/flag loads and the non-GRAY path. Five outside-step samples are spread
  across that window (`late-partial-exits.json`), supporting repeated scans
  with intervening allocations, not proof of a single cyclic linked list.
  Source, optimized IR and machine loop all advance node to next correctly.
- In `note_object_freeing`, a GRAY object is removed without decrementing
  gray_count. Both GC1 and GC2 reproduce this with a real empty list compiled
  by original pcc1 and executed natively: known=1/refcount=1/non-GRAY;
  marking changes count 0→1; decref removes the object from the index but
  count stays 1. GC1 receipt:
  `/tmp/pcc-v16-gray-count-probe-uv09qlmu/execute.stdout`; GC2:
  `/tmp/pcc-gray-count-other-backend-plan-d1va6wou/execute.stdout`.
  Diagnostic restoration of the count only cleans up that probe process.
- Dispatcher root/owner barriers also omit increments on non-GRAY→GRAY.
  Root/trace helpers already increment, trace consumption decrements, and
  epoch preparation/finish reset the aggregate. A positive phantom count
  prevents finish claiming and resets the cursor to head at chain end.
  Non-GRAY visits consume no budget and zero processed work discharges no
  debt, allowing repeated full scans after allocations. The old C mirror
  also had these omissions; do not label them a new Python-port regression.
- **Do not generalize the free decrement to GC3/4.** GC4 copies a GRAY header
  without an independent increment, and freeing a failed copy could steal
  its live source's count. GC3 has transfer through inactive source nodes.
  These moving-color protocols remain a separate boundary.
- Minimal candidate: `/tmp/pcc-gc12-gray-counter-pm99wifd/source`, only
  `freestanding_gc_barrier_dispatcher.py` and `py_gc_backend.py` differ from
  V16. Source identity
  `0bf1fe8654e2a1e7a06242ddbb0fca2b9bb3e1a69f38cb24ac2a241e264bbb9f`.
  It adds barrier increments and first-free decrement/GRAY clear only for
  GC1/2. No thresholds, budgets, GC3/4 or finish-STW logic changed.
  Owned runtime delta built successfully, 171 members, 169 unchanged;
  archive SHA256
  `55c95321aad22785b0429cc29f058e26e61e06c99bec57624ad9b465515b95dc`.
  Original optimized IR re-emission reproduces both replaced control objects.
  **The same native GC1 lifecycle probe now reads mark +1, free -1**:
  `lifecycle-probe/execute.stdout`. Seven existing focused checks also passed
  in 14.34 s (`focused-process.json`), including GC1 live roots/churn and four
  known-object variants that execute GC0–4. The readonly pytest-cache warning
  is not a failed test. This runtime fix is not yet in shared source.
- Four stronger regression sources in
  `/tmp/pcc-gc12-counter-regressions-u32z5_4w` cover lifecycle, root barrier,
  owner barrier and inactive behavior, including repeated shading. They do
  not reset gray_count or alter thresholds. The first driver attempt hit a
  **harness** raw-address ABI problem in its module-top `c_ptr` status writer
  (non-UTF8 output); no pass was claimed. A separate v2 harness using c_rawptr
  and a function-local writer is being prepared. Execute all eight GC1/2
  cases before launching the prepared `stage1/run.py`, then run the same
  original 418-PCO/original-runtime link with the corrected compiler's GC1.
  Expected link output remains original `b4d936cc…`; the runtime in the output
  must stay original for that same-input comparison.

Other newly clarified evidence:

- Module 344 (`pcc.parse.c_parsetab`) was **not** a 100-second literal
  codegen operation. Independent exact-worker replay at
  `/tmp/pcc-v16-cparsetab-344-prep-6ba3bgue`: GC0 **2.9195 s**
  (parse626/infer91/codegen1756 ms); GC1 **8.9407 s**
  (parse2468/infer653/codegen4761 ms). Both PIDX SHA256:
  `f7169b9ba4e21d54cf0a63a4b633622e7af366d40ec46e8553521bb6d96d4bbc`.
  Generic static aggregates already work: three builder calls, 710 total
  calls, not thousands of dict_set operations. Its call score=0 puts it at
  rank399/418; reservation 3,664,566,698 bytes can be blocked by a 6-GiB PCO
  under the 7,516,192,768-byte task budget. Old late mtimes include queueing.
  No dispatch timestamps exist. A diagnostic design is saved at
  `/tmp/pcc-chained-dispatch-trace-candidate/DESIGN.md`; no scheduler change
  has been made. Reassess scheduling only after the collector defect.
- Standard file locking plus generic c_obj argument/result protection have
  now been applied to shared source. Seven focused IR/runtime-export checks
  passed (`/tmp/pcc-owned-locking-check`). A host-compiled native c_obj
  program passed GC0–4, including argument GC/errors/finalizers/result alias;
  `/tmp/pcc-extern-cobj-host-native-h7cih33x` records unchanged 933 inputs.
  Native compiler/real locking process gates are still pending. A frozen
  primitive tree and runtime/Stage1 scripts are prepared, **not run**, at
  `/tmp/pcc-mac-primitives-4bs1pwpr` (source identity
  `a624c1c01c675d5e23eaffaa27fb7341bdd160aea32e4827c7f3187945cb9d60`).
- Revised cache candidate additionally passed all 25 old host retention/
  identity checks at `/tmp/pcc-native-cache-existing-6215i7b5`. Combined with
  its 29 new host tests, this is still host protocol evidence: that frozen
  tree uses host stdlib locks and predates the native provider overlay.
- Fspath v1 is unqualified. Its review found early unpin, callable-descriptor
  role and TLS-exception windows. Partial v2 is saved under
  `/tmp/pcc-fspath-candidate-v2-76gl1n_9/pause-state.json`; its copied patch
  is explicitly STALE and must not be applied. Work paused for the GC defect.

New evidence since the checkpoint below:

- Module 286 producer canary now passed compiler/program GC0–4 with identical
  program bytes and traceback. Receipt:
  `/tmp/pcc-takeover-producer-286-asm-0a62qbpl/producer-five-gc.json`.
- Module 113 (`pcc.llvm_capi.ir`) was independently replayed. Both direct/text
  assembly and native re-emission of its unoptimized PIDX reproduced original
  PCO `c00706c202476b11650662371bae693b18eeba98b07615f9882fcd0822f50bda`.
  Owned mem2reg/sroa changes allocas 2954→2228, loads 4264→2611, stores
  6066→3225; GC calls are unchanged. Replacing only that PCO and relinking
  produced compiler `d64a9860e4ebecf9e9e6cc2a08447db8b0cb78280f72a36a8763c21b44ab4d24`.
  All 420 link inputs remained unchanged. Compiler/program GC0–4 canaries
  passed with identical reference program SHA256
  `7d46fd6382eda39032959e5090a7974dd9b98f55174e8c784537e1470fbb140d`.
  Evidence: `/tmp/pcc-takeover-producer-113-prep.oCH1nT/`;
  use `producer-five-gc-v2.json`. The first canary used `/tmp` instead of
  `/private/tmp` in source argv and therefore changed filename payloads; it
  is explicitly marked invalid as an optimizer comparison. The first link
  attempt also had a malformed input record and failed before linking;
  `candidate-link-v2-process.json` is the successful run. No producer speedup
  or whole-directory pass qualification is claimed; exclusions remain.
- Added owned `os.kill`: checked integer protocol and pid/signal bounds,
  PermissionError/ProcessLookupError classification, and a signal slot root
  across user `pid.__index__`. The initial host-emitted native program passed
  GC0–4 (`/tmp/pcc-takeover-os-kill-check/native-process.json`). Review then
  found the earlier temporary pid could move while evaluating the second
  expression. Shared lowering now roots both operands, cleans owned values
  on success/error, and has a direct-temporary/raising-factory/finalizer probe.
  That host-emitted native probe passed GC0–4 too:
  `/tmp/pcc-takeover-os-kill-check/lifetime-process.json`. The latest operand
  lowering fix is **later than** the Stage1 snapshot below, so its native
  compiler and bootstrap gates remain required.
- Added metadata-only `getsize`, `islink`, and `getmtime` failure propagation.
  Cross-target focused IR tests found a real existing bug: `generate(module)`
  replaced the IR module after target selection, losing its triple and making
  AArch64 Linux `syscall6` choose x86 instructions. Shared generation now
  preserves triple/data layout. Five focused cases passed at
  `/tmp/pcc-owned-metadata-check/process-v3.json`. Earlier test failures also
  exposed an overly broad declaration-based fallback assertion; the test now
  counts real fallback calls. No Linux/Windows execution claim follows.
- Frozen Mac source `/tmp/pcc-owned-metadata-native-tqwspwgw/source` has
  1088-file identity
  `abac6214dc49df73b399359c12586d8ff7adbcd01cb0f2156d2e407d48d7d903`.
  It contains V16 plus initial os.kill, metadata, target preservation and
  worker diagnostic fixes, excluding partial long-double and cache/identity
  candidates. Runtime `runtime/libpy_runtime_pcc_py.a` has SHA256
  `01f8a0dab0952fe92bd6f1c92788fd5119d3eccf8d354239550e01e3a3de58e4`;
  two changed members, other 169 unchanged. This is a controlled runtime
  delta, not a cold-runtime gate. Re-emitting retained original optimized IR
  exactly reproduces each replaced control object. Fresh frontend control
  IR has different source filename globals; no timing is attributed to that.
- New Stage1: **191.0714 s**, peak **5,548,113,920 bytes**, libSystem only;
  binary SHA256
  `17b73ae9e54a6fe0147534643e004ead37f598e86413a848ac1d5ffbf328853c`.
  Receipt `stage1/verified.json` in that root verifies unchanged source.
  The compiler directly compiled and ran all three metadata integration
  cases: symlinks and 64 GiB sparse size, FIFO without opening it, and
  missing/nondirectory/NUL/zero/pre-epoch mtime. Symlink and mtime programs
  run all five GCs. Receipt `native-tests-process.json`: **3 passed**.
  These tests invoke native CLI directly; the unrelated compile_python hook
  consequently reports zero routed calls. They are not Stage2/3 gates.
  The old enabled-cache `_plan_enabled` bridge is still diagnosed in this
  Stage1; object cache was explicitly disabled.

Unmerged candidates and active work:

- `/tmp/pcc-native-object-cache-candidate`: string-path lifecycle, staged
  readers, leased publishers, content/options-bound keys. Initial host
  tests 17 passed. Independent review found stale directory-lock ABA and
  same-PID release hazards; candidate now uses a persistent real OS file
  gate, unique generation handles, and adapts host maintenance to the same
  protocol. Enabled identity/profile errors no longer silently disable the
  cache. Revised host tests: **29 passed, 3 integration deselected** at
  `/tmp/pcc-native-cache-host-v2-q664sv58`. No native-cache proof yet.
- `/tmp/pcc-file-locking-candidate-bzc7as8v`: real fcntl.flock and Windows
  msvcrt.locking providers/runtime/intrinsics, replacing the fcntl no-op.
  Runtime returns None or an owned exception value; the provider raises it
  normally, with no extern symbol-name error whitelist. Under independent
  review; source-only, not applied or executed. The cache must not be tested
  natively against the old no-op. The author is strengthening close/unlock
  tests so the owner remains alive after releasing its lock.
- `/tmp/pcc-local-identity-candidate-xraph1j0`: first production receiver
  identity slice. Source-only, not applied. Root review identified that a
  module-only lookup audit cannot rule out mutation by importing modules;
  it needs an actual closed-entry proof before promotion. Do not certify it.
- Shared metadata tests cover string paths. Review found existing path
  coercion uses str rather than __fspath__/bytes and generic mtime OSError
  loses precise subclasses. A separate candidate is being prepared; do not
  widen the string-path evidence into complete os.path semantics. General
  OSError errno/filename fields also remain outside the current exception
  representation; no completeness claim was made for those attributes.

Full five-GC fixed points, Stage2 ≤300 s, gateway throughput, cache-enabled
native closure, scaffold retirement, full/release/wheel gates and all three
other platform execution gates remain open. The older GC0 fixed-point evidence
below remains V16 evidence only.

## Takeover validation checkpoint: 2026-09-26, quota 47% to 37%

The user explicitly authorized actual validation and instructed this task to
take over the Mac work. The ordering remains Mac first, then Linux x64,
Linux arm64 and Windows x64. No platform execution gate was started here.
Work initially paused at the ten-percentage-point quota checkpoint, before
the user revoked that rule as recorded above. All owned build
and test commands returned; `build/.pcc-performance.lock` was inactive at
the checkpoint. No commits or destructive cleanup were performed.

The shared tree still includes the partial long-double implementation in
`2026-09-26-linux-long-double-handoff.md`. Do not use that changing tree as
evidence for these frozen Mac experiments.

### Re-established control identity

The V16 binary/runtime hashes below were reread and match. Its old
`source-identity.json` belongs to the V14 base; after V15/V16 overlays, six
later file differences remain. A new current-input inventory of 1088 build
files was recorded before this takeover's qualified replay:

- Source: `/tmp/pcc-current-mac-v16-5yc2ueuv`.
- Current source SHA256: `472dae77d0be828cd9087a10311fa965352faeffa5963cec530d67be898bd535`.
- Receipt: `/tmp/pcc-takeover-gc1-control-mlhvlpxp/verified.json`.
- Exact c_codegen PIDX worker, unprofiled GC1: **36.2443 s**, peak
  **2,027,307,008 bytes**, output `ef5dec23e76d2359ea7a784666fb7d4d2b8ee6eb675a34854a299f0dba4a9dc0`.
- Source, compiler, runtime, AST and exports remained unchanged across replay.

The earlier `/tmp/pcc-takeover-gc1-baseline-o4kj6n8f` run is **not a qualified
timing**: initial source inventory preparation failed on the `/tmp` versus
`/private/tmp` alias. Its subsequently written identity explicitly says it
was captured after the run. Do not reuse it as before-run identity evidence.

### GC1 inactive barrier experiment: not promoted

An isolated V16-derived source at
`/tmp/pcc-takeover-gc1-mark-guard-zwvag8ly` changes only
`freestanding_gc_barrier_dispatcher.py`: under the existing graph lock,
GC1 with `mark_active==0` unlocks and returns before known-object queries.
It preserves outer store/refcount operations and leaves GC2 behavior intact.
Candidate source SHA256:
`5e0d1bd7617557247df7ad25eebfe0767f264883f08a76d48bade093dc6ef160`.

Evidence root: `/tmp/pcc-takeover-gc1-mark-guard-validation`.

**Important correction:** the initial `runtime/` delta omitted the runtime
Makefile's seven-pass optimization sequence. Its original/candidate alloca
counts were 0/19. The first `worker-ab/results.json` is marked
`performance_comparable=false`; its 2.4% number cannot attribute the guard's
effect. Do not treat that first A/B as an optimization verdict.

The corrected `runtime-opt/` uses exactly
`mem2reg,sroa,instsimplify,inline-defined,instsimplify,instcombine,dce`.
Rebuilding the **unchanged** barrier through this pipeline produced an object
byte-identical to the original, SHA256
`4090fa4d754debd4c5de38a7a43db105d7f80946b25006124cc3860df0426b1a`.
Both corrected IRs have zero allocas and 24 PHIs. The candidate archive is
`runtime-opt/libpy_runtime_pcc_py.a`, SHA256
`1bae66466f15fcb7c6b500342abb13e466788dd065b19a102b6de9585e94f410`.
Only one of its 171 members changes; this is a controlled delta, **not** a
cold-runtime construction qualification.

Corrected checks:

- `optimized-validation/process.json`: **7 passed** in 14.57 s, including
  GC1 explicit collect/churn and four known-object cleanup variants running
  all five collectors.
- `optimized-validation/native-five-gc.json`: original native pcc1 compiled
  the known-object/finalizer/weakref/resurrection program against the new
  runtime; its emitted program ran successfully on GC0–4. It links libSystem
  only. This is not a five-GC self-host fixed point.
- `optimized-validation/ordinary-gc{1,2}-process.json`: native pcc1-compiled
  barrier probe prints `0` on each backend. It covers inactive/active root
  and owner colors, GC1 nonblack-owner behavior, GC2 flush counts, epoch/
  request state and outer store metrics. Use `ordinary_probe.py`; earlier
  `probe.py`/`raw_probe.py` were invalid harness forms, not passing tests.
- Corrected Stage1: `stage1-opt/process.json`, **196.8684 s**, peak
  **5,592,711,168 bytes**. Compiler SHA256
  `9a884ab5b3ee5f3c7d1251319e8602914fc423d03cdb3e1ea702a12fb89c62d7`.
- `worker-ab-opt/results.json`: unprofiled AB/BA pairs, identical inputs and
  all eight PIDX hashes equal to the control.

| GC | Control seconds | Candidate seconds | Median time reduction |
|---|---|---|---:|
| 1 | 37.3485, 37.4405 | 38.2722, 34.7646 | 2.34% |
| 0 | 12.3942, 12.6422 | 12.6546, 12.6295 | -0.99% |

The GC1 effect is small and inconsistent; this guard was **not copied into
shared runtime source**. Together with the earlier rejected small runtime
candidates, it calls for reassessing a larger owner, not another leaf tweak.
No candidate Stage2/3 chain was attempted.

### Actual compiler pass coverage and first producer check

The runtime's hot modules already have effective memory promotion; e.g.
incref/decref prepare have zero allocas. The compiler pipeline is different:
the direct worker's `PCC_PYTHON_IR_PASSES=off` bypasses mem2reg/sroa, and even
`default` still excludes all `pcc.py_frontend.codegen` modules plus
`pcc.llvm_capi.ir`. `FUSE_USES` is capture metadata, not memory promotion.
Register allocation remains enabled. Historical exclusion comments in
`fe1de470`/`977ad074` identify zero-filled traceback C strings after optimizing
these producers; do not delete the guard without executing that shape.

Producer evidence: `/tmp/pcc-takeover-producer-286-asm-0a62qbpl`.

- Actual module indices in this Stage2 manifest are **113** for
  `pcc.llvm_capi.ir` (worker 12) and **286** for `string_globals_lowering`
  (worker 258). Do not confuse module index, sibling-init index or worker ordinal.
- Replayed module 286 with the original compiler/runtime and retained text.
  Direct/text assembly matched; re-encoded control PCO equals the original,
  SHA256 `7793bbcbf539633d0d58bc590eba58b79afd23b2e7735e6629f5445985a3b944`.
- `pass-effects.json`: on the actual producer IR, mem2reg/sroa changes allocas
  **147→116**, loads **219→142**, stores **328→178**, PHIs **25→38**.
  GC call counts do not change. Initial empty counts from the diagnostic block
  view were discarded: this parser normally stores bodies in indexed kernels.
- `candidate.pidx` was emitted by the original native indexed emitter to
  `candidate.pco`, using `optimize=False`, the same emission policy as control.
- Native relink of the **unchanged** 418-PCO manifest took **113.6918 s** and
  exactly reproduced original pcc2, SHA256
  `b4d936cc29e4244169f459ecb419efccae157db932c7de331e034a47dbdc4e1c`.
- Replacing only module 286 produced `candidate-compiler`, SHA256
  `74cc75fa25dffb0fde3f31f6c32793b007a53486a8a99c165c3991d1df58be33`.
- `producer-canary.json`: control and candidate compile the same nested
  exception program to byte-identical native output, SHA256
  `7d46fd6382eda39032959e5090a7974dd9b98f55174e8c784537e1470fbb140d`.
  Execution preserves filename, both function names, source line, Unicode,
  quotes/backslash and the exact ValueError message. Expected exit is 1.

This is only the first GC0 producer correctness check. It does **not** qualify
module 113, the whole codegen directory, GC1–4 compiler execution or a speedup.
The hardcoded pass exclusions remain unchanged. Next: compile the same canary
under GC1–4, qualify module 113 separately, then compare exact worker speed
with changed compiler producers and identical runtime/emitter settings.

### Shared diagnostic fixes made during takeover

Actual worker replays found two harness-path defects. Native-object encoding
cleared `direct_asm` before comparing it with the text oracle, yielding a false
mismatch despite a byte-identical PCO. Text-control-only mode also suppressed
instruction text and returned empty IR. Narrow fixes are in
`pipeline_frontend_worker_execution.py`, `llvm_capi/ir.py` and
`codegen/generation_lowering.py`, with real worker regressions in
`tests/python/test_direct_indexed_worker_validation.py`.

Host focused checks: **8 passed, 3 Windows cases explicitly deselected**,
receipt `/tmp/pcc-worker-validation.IIJNwi/process.json`. The tests include
intentional mismatch rejection and preserve the nonvalidation release-order
contract. The Windows cases remain in the matrix: the first run failed before
comparison with COFF `KeyError: '.Lpcc_seh_1_213'`, captured in
`/tmp/pcc-worker-validation.M61mMG/stdout.log`. That Windows issue is open.
These shared source fixes have not received a new native self-host chain.

Mac five-GC full bootstrap, <=300 s Stage2, gateway >asyncio, scaffold flag
retirement, enabled native cache and full release gates remain open. The next
platforms must not borrow any Mac result as qualification.

## Later Mac/ARM64 checkpoint: frozen V16 after platform overlay

V16 source is `/tmp/pcc-current-mac-v16-5yc2ueuv`, copied from the V14
platform-inclusive snapshot with the owned `shutil.rmtree` runtime/lowering
repair and disabled-object-cache entrypoint split. The exact overlay file
hashes are in `v15-overlay-identity.json` and `v16-overlay-identity.json` in
those snapshots. This is a frozen experiment, not proof that later edits in
the shared checkout behave identically. The pcc-Python runtime archive is
`/tmp/pcc-current-v15-rmtree/cache/27ffe0f21ba8f4efd9007c59-pcc-py/libpy_runtime_pcc_py.a`
(SHA256 `6facbbbd431eec5d8165331206341615b8cd507840fb3d9094d03b36e7206e25`).

The first V14 Stage2 failure was `no-libpython function unavailable:
pcc.py_frontend.pipeline._prepare_direct_native_object_dir` after 13.4 s.
Four isolated compilations proved `shutil.rmtree`, not `import shutil`,
`os.path.isdir`, or `os.makedirs`, emitted `py_cpy_ensure_init`. V15 added
owned `py_shutil_rmtree` over `pcc_platform_remove_tree`; its native nested
tree, missing path, file and root-symlink tests passed. A fresh V15
Stage1→Stage2→Stage3 chain passed and pcc2/pcc3 were byte-identical.

The V15 pcc1 then found a separate gateway-compile failure in
`pipeline_self_backend_cache.plan`. A contextual IR probe of the compiler
closure showed that function-local `import hashlib` first emitted
`py_cpy_import`; local imports of the host-owned cache-retention module also
bridged. V16 leaves explicitly enabled object-cache helpers as fail-closed
native gaps, while the default disabled-cache `maintain`, `plan`, and
`publish` entrypoints return without calling them. Focused disabled-cache IR
and host enabled-cache hit/identity tests passed (4/4). Do not report native
object caching as implemented; `PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY` plus
cache enabled still requires an owned implementation.

V16 Stage1 succeeded in 172.38 s, pcc1 SHA256
`c0cbfb61bf9c50cf57a18e292cc2a7afbdc3d6da5f7e6c2bb97fb0f5a473f02e`.
GC0 Stage2 succeeded in 369.19 s and Stage3 in 392.38 s under the 8 GiB
tree cap. Their binaries are byte-identical, SHA256
`b4d936cc29e4244169f459ecb419efccae157db932c7de331e034a47dbdc4e1c`,
and link only libSystem. Receipts and profiles are in
`/tmp/pcc-current-v16-stage1.rbFiBJ/` and
`/tmp/pcc-current-v16-gc0-stage.zkBIwW/`. The relevant focused suite on a
writable copy of V15 passed 445 tests (5 deselected); V16's four cache tests
passed. The 120 s fallback-baseline watchdog timed out at 46%, before a gate
verdict. The bootstrap baseline test in the sealed copy selected zero items;
the isolated V16 pcc2/pcc3 byte check is first-hand evidence instead.

The unchanged gateway benchmark sources ran a complete V16 six-arm comparison
with 180 validated executions at
`/tmp/pcc-gateway-scaffold-v6.obelhk97/benchmarks/results-v16.json`.
At C100/zero wait the native pcc1 median was 74,241 QPS against host pcc
74,565 and CPython asyncio 92,052 (pcc1/asyncio 0.807); the host/native
artifacts have identical hashes. The asyncio-vthread prototype measured
35,508 against asyncio-gather 86,249. Gateway HTTP, dashboard and structured
failure-cleanup integration passed 6/6 in 380.78 s; receipt
`benchmarks/integration-v16-process.json` in the same copied gateway tree.
This is no meaningful relative-ratio improvement over V8, and the target of
exceeding asyncio remains unmet.

GC1 Stage2, using the V16 pcc1 with two frontend workers, timed out at the
normal 600 s gate with 26/420 PIDX outputs, no PCO/link, and peak tree RSS
6.73 GiB; receipt `/tmp/pcc-current-v16-gc1-stage.1E9lxz/stage2-process.json`.
The GC1 coordinator/export phase took 247.27/211.20 s against GC0
41.45/25.27 s: the GC1 test's explicit frontend width `2` also capped its
light export lane at 2 workers, whereas GC0 `auto` used 10. The first GC1
PIDX arrived at 256.7 s; its nine oversized modules then emitted serially
until roughly 485 s. Do not attribute that entire 485 s to oversized codegen.
All owned children were gone after the watchdog.

An otherwise identical GC1 `auto` run gave export 88.18 s with 10 workers
while heavy codegen remained at 2. At the same 600 s gate it had 417 PIDX
and 331 PCO outputs, still no link, and peak RSS 7.73 GiB (close to the
8 GiB guard). Receipt:
`/tmp/pcc-current-v16-gc1-auto.eeC5dP/stage2-process.json`. This is a
measured configuration gain, not a completed GC1 Stage2 or a qualified new
default. Both runs timed out, and GC2–4 full stage chains were not started.

The exact V16 `pcc.codegen.c_codegen` PIDX worker was replayed with the same
manifest and output hash `ef5dec23e76d2359ea7a784666fb7d4d2b8ee6eb675a34854a299f0dba4a9dc0`:
GC0 12.84 s / 1.15 GiB, GC1 36.79 s / 1.89 GiB. The in-process sampler's
main-thread CPU deltas were 11.49 s and 35.16 s. GC1 self samples were
distributed across memset (6.5%), index find (5.1%), decref preparation
(4.6%), incref preparation (3.6%), index remove (3.1%) and graph unlock
(3.0%). Receipts are in `/tmp/pcc-v16-worker-profile/gc{0,1}_pidx/`.
The first replay accidentally emitted `.s` instead of PIDX because it omitted
`PCC_DIRECT_INDEXED_SIDECAR=1`; it is excluded from this comparison.

Five-GC native execution of the builder counterexample
and recursive directory deletion passed on V16 as recorded in
`/tmp/pcc-current-v16-stage1.rbFiBJ/five-gc-native.json`, but that is narrower
than a five-GC self-host fixed point. Stage2 remains above the 300 s target even at
GC0. Scaffold retirement, default/native cache completeness, the full test
suite and the gateway throughput target remain open.

## Frozen inputs and results

Mac source: /private/tmp/pcc-scaffold-mac-v8.mqq0s4pb
Stage1 input receipt: /tmp/pcc-scaffold-mac-v8-direct-stage1._yfyzdk5/source-identity.json
Stage1 source hash over 1047 build inputs:
55fc8df143e918714460919da1e1af56e4b42497ec71c071e94b39ad9f63f6e8
Matching runtime archive:
/private/tmp/pcc-scaffold-mac-v8-bundle.59cifpza/libpy_runtime_pcc_py.a
SHA256: 7a986e1d03ecf5c7901b37166652547544ea6b6a4154f1c318a6d2123722a6eb

The runtime archive was rebuilt from V8 using its host pcc, with full
provenance verification and 171 owned arm64 Mach-O members. Its recorded
manifest is not stale. The Stage1 compiler at
/tmp/pcc-scaffold-mac-v8-direct-stage1._yfyzdk5/pcc1 has SHA256
50228c09949e72d171afc594f53ac594d44ad139d433922fa0d9323316052cbf.
Direct Stage1 took 218.845 s. The normal Stage1 wrapper's /usr/bin/time -lp
fails in this sandbox on kern.clockrate, so the equivalent frozen command was
run under the libproc-backed process-tree guard instead. The process receipt
in that directory records its exact argv, source, RSS cap and environment.

Stage2 and Stage3 receipts live at
/tmp/pcc-scaffold-v8-gc0-stage.dlyqi52k/stage2-process.json and
stage3-process.json. Both returned zero, with 8 GiB tree caps, and took
373.803 s and 410.847 s. The pcc2 and pcc3 binaries are byte-identical,
SHA256 034b7e8f88ff811c9f5629a88e031d6defce98d18d779267d595a47f96013131;
they link only libSystem. All 395 PIDX and PCO module outputs matched across
the two stages. The Stage3 wall difference remains unexplained and is
deprioritized by user direction. Stage2 still exceeds the five-minute target.
No GC1–4 full native fixed-point chain has passed on V8.

The function-bearing ordinary user-builder counterexample was compiled by
V8 pcc1 and pcc3 and ran with CPython-equal output under GC0–4. The focused
host scaffold tests on the V8-derived testroot passed 273 cases, with one
native case deselected; the native shape was executed separately as above.
The V8 provider-signature audit returned zero issues; receipt:
/tmp/pcc-scaffold-v8-signatures.json. Full repository tests are not green:
the historical per-module fallback ratchet also fails on the frozen r64
baseline, and must be repaired with a source-identified baseline rather than
silently recaptured.

## Implementation and unresolved migration

Production lowering now uses call-identity and receiver-flow facts for
ordinary builder calls and known IRBuilder receivers. An ordinary user
builder.add no longer becomes an IRBuilder symbol. Proven ClassLowering
function/parent fields retain their direct calls; the class_gen OFF-mode
fallback count is back to the r64 count of 29. Native frontend worker
ownership/admission fixes preserve the singleton/deferred path for explicit
numeric jobs and cap preload workers against the actual tree budget.
Host-only signature and decision-audit tools and focused tests were added.

This is an intermediate migration. The --ir-scaffold switch, special lowering
table, import-name closure filter, provider dependency metadata and unproved
receiver gaps remain. Do not call scaffold retired or the full architecture
complete. The host-only observer's hard-coded claims that emission and proof
contracts were unchanged have been removed from the shared script after V8;
its observer/signature tests passed 27 cases. This script correction does not
alter the compiled V8 native source snapshot.

## Performance and gateway

The frozen V8 gateway comparison, in the sandbox copy
/tmp/pcc-gateway-scaffold-v6.obelhk97/benchmarks/results-v8.json, completed
all 180 validated runs. Host pcc and pcc1 emitted byte-identical binaries.
At C100 with zero wait, pcc1 measured 69,162 QPS versus CPython asyncio
85,911; V7 pcc1 was 69,259 and r64 pcc1 66,779 in their separate runs.
V8 has no visible regression against V7 but has not surpassed asyncio.
The benchmark uses two child tasks and sorted JSON validation; it is a batch
handler workload, not socket throughput. V8 gateway native integration in the
copied gateway tree passed all six host/native HTTP, dashboard and structured
failure-cleanup cases in 382.32 s. Its receipt and live result log are
benchmarks/integration-v8-process.json and integration-v8.stdout there.

Diagnostic-only ablations, all with the same V8 pcc1/runtime and 5000
requests per sample, are under
/tmp/pcc-gateway-scaffold-v6.obelhk97/benchmarks/probes-v8. Removing JSON
validation gave about 80.9k QPS; replacing TaskScope with direct spawn/join
gave about 103.4k but drops failure-cleanup semantics and is not a patch.
An exact success/failure join body inlined in the diagnostic request gave
about 76.8k. Disassembly confirms the normal TaskScope.join call is already
static, so scaffold dispatch is not the cause. Moving the join try block
outside its loop preserved focused unit tests but gave only +0.27% in adjacent
native A/B; that candidate is not promoted. The benchmark explicitly runs
with PCC_DISABLE_BULK_GENERATOR_FRAME_INIT=1 (also the compiler default);
turning on the existing bulk path replaced eleven list appends in the join
factory with one fixed frame allocation, but ten paired native C100 repeats
measured -0.29% QPS. That candidate is not promoted. A release/default change
would still require the native GC gates.
Do not attribute these ablations to a production improvement.

GC1–4 remain much slower. The exact c_codegen frontend worker emits the same
PIDX under each collector. The current in-process main-thread profile in
/tmp/pcc-signal-profiler-v1/gc{0,1,2,3,4}_thread/ measured, respectively:

| GC | worker wall | main-thread CPU | peak tree RSS | main-image samples |
|---|---:|---:|---:|---:|
| 0 | 10.77 s | 10.55 s | 1.14 GiB | 97.5% |
| 1 | 32.22 s | 31.81 s | 1.88 GiB | 98.6% |
| 2 | 31.41 s | 31.15 s | 1.88 GiB | 98.6% |
| 3 | 69.07 s | 68.57 s | 1.65 GiB | 97.2% |
| 4 | 111.28 s | 110.35 s | 3.71 GiB | 98.0% |

GC1's extra 21.26 CPU seconds are distributed across root/frame/index and
refcount preparation. Approximate self-time increases: memset +1.97 s,
index find/remove +2.63 s, incref/decref prepare +2.72 s, then graph locks,
frame-index updates, root store plans and allocator metadata. The LR probe in
gc1_thread_lr/ ties memset to root store, frame-node allocation and plan
initialization; index operations come mainly from known-object tests and frame
leave. GC4's largest leaves are uncached object-start validation (11%), index
find (7%), managed-pointer index (5%), memset (5%) and read barrier (5%). Its
LR probe ties index find chiefly to managed-pointer and known-object checks.
None is a one-function cure for the full gap. GC2's profile closely matches
GC1's root-store, frame-index and refcount pattern.

The first ITIMER signal profiler was invalid for hotspot percentages: it
biased 60–75% of PCs to the __mmap syscall return. A separate mmap interposer
counted 30,428 mappings under GC0 and 37,559 under GC1, but direct timing
measured only 0.093 s and 0.114 s inside those calls. The 0.021 s difference
cannot explain the 21 s worker gap. The replacement sampler is a separate
in-process thread that suspends the main thread and reads its Mach PC/LR and
CPU totals; its control program put all 446 main samples in one compute loop.
Use scripts/pcc_inprocess_sampler.c and pcc_inprocess_profile.py, listed in
docs/development-tools.md. Host clang builds this diagnostic dylib; it is
external profiling equipment, not an owned pcc1 runtime or toolchain route.
The repository's end-to-end busy-loop test in
tests/python/test_pcc_inprocess_profile.py passed on macOS ARM64.
For a new frozen worker replay, build and decode it with:

    clang -dynamiclib -O2 -Wall -Wextra scripts/pcc_inprocess_sampler.c -o /tmp/pcc-sampler.dylib
    env -u LC_ALL uv run python scripts/pcc_inprocess_profile.py --binary /path/to/pcc1 --expected-binary-sha256 EXPECTED_SHA256 --sampler-dylib /tmp/pcc-sampler.dylib --samples /tmp/worker.raw --output /tmp/worker-profile.json --caller-for _memset

Run the exact receipt-bound worker command between those steps, under the
normal performance lock, timeout and tree-RSS guard, adding only
DYLD_INSERT_LIBRARIES=/tmp/pcc-sampler.dylib,
PCC_THREAD_SAMPLE_FILE=/tmp/worker.raw and PCC_THREAD_SAMPLE_US=2000 to its
child environment. Use a fresh raw/output path. The reporter refuses a binary
hash mismatch; the worker PIDX hash must still match its uninstrumented input.
Disabling GC1 debt checks had no material effect; GC4 provenance-probe-off
saved about 12% in an earlier isolated worker but is not accepted without
an ownership proof.

A source-frozen scalar-root fast-path experiment was also denied. V9 is
/tmp/pcc-scaffold-mac-v9.wiz5i2zw: only pcc/py_runtime/py/py_obj.py differs
among 881 pcc Python sources. Its forced runtime rebuild passed provenance;
after normalizing absolute source paths, 170 of 171 runtime IR modules matched
V8 exactly and only py_obj.ll differed. The V9 archive SHA256 is
274e6995bef537aacaf82f9c1264a830da8c3ad173110b881cb301f18e7f5488;
the owned Stage1 pcc1 SHA256 is
b2f4dd95ca47ce6d3e71e84ad702b51db7caebed3db66e4effea240d8b19e352.
The change lets a single-threaded GC1/2 root store of NULL/tagged values over
a NULL/tagged old value keep the graph lock, store note and write barrier but
skip the refcount/128-byte plan, with a recheck under lock. A native 2M-store
microbenchmark ran on all five GCs and improved GC1/2 2.6x, but the exact
c_codegen Stage2 worker, in balanced V8→V9→V9→V8 replays, improved only
33.04→32.61 s (1.3%). All four produced the same frozen PIDX. Its receipt is
/tmp/pcc-scaffold-v9-worker-ab.pukkb15k/result.json. This candidate was not
ported to the shared tree, and no Stage2/fixed-point qualification was claimed
for V9. Do not extrapolate the microbenchmark ratio to bootstrap.

The default-GC owned Stage2 link was independently replayed from the 395
retained PCOs at /tmp/pcc-v8-link-profile.5dpa1t08. Its inputs.json hashes
each PCO plus the manifest and runtime archive. The native pcc1 command in
process.json completed in 102.98 s with 4.56 GiB peak tree RSS and produced a
pcc2 byte-identical to the original Stage2 output, SHA256
034b7e8f88ff811c9f5629a88e031d6defce98d18d779267d595a47f96013131.
Only libSystem is linked. The in-process profile captured about 102 s of
main-thread CPU, with 98.4% of active PCs in the sampled pcc1 image. Grouping
self PCs by symbol family gives about 37% GC/root/barrier operations and 43%
other Python object-runtime operations, versus low single digits directly in
Mach-O layout, packed-object decode and stackmap function bodies. This is a
symbol-family attribution, not a separate phase timer: decode and layout
trigger those runtime calls. Its top leaves are pcc_gc_load_ptr (7.8%),
py_incref (4.9%), py_decref (4.4%), then provenance/root stores, bytes and
integer operations. The link slowdown is thus broad repeated object/ownership
work; merely changing one decoder arithmetic loop has a small Amdahl ceiling.
One tempting shortcut was ruled out before editing: the unordered
OwnedMergedSourceView relocation iterator yields sequential indices, but
tests/python/test_native_object_fastpath.py requires its next()/close()
contract. Replacing it with range would silently remove that cleanup surface.

## Reproduction and next gates

Each retained process JSON has the full command, timeout, tree cap and live
samples. Recheck source and archive hashes before replay. Read worker.tsv to
identify the actual module; the worker manifest filename is not its module
index. Three verified V8 pcc1/pcc2 replays (cli_bootstrap, unsafe_lowering,
type_infer) emitted identical PIDX and differed by only about 0.25 s each;
an earlier filename-based module guess was invalid and is withdrawn.

The next speed slice should address the measured shared root/index/ownership
pipeline and the linker object-operation volume, testing candidates against
frozen workers/PCOs before rebuilding a Stage chain. Preserve user-builder
counterexamples, signature checks,
no-libpython linkage, Stage1→2→3, all
five GCs and complete gateway workloads before promoting a production change.
The shared worktree and the V8 snapshot are different inputs; any new
production edit requires a newly sealed source/runtime and fresh gates.
