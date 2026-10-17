# PCC current status

Updated 2026-10-17. Source: `ikshengmin/pcc` master (`shenming` remote).
Durable receipts for this period (patches, profiles, A/B scripts, repros):
`/Users/jiamo/pcc-recovery-archive/2026-10-13/README.md`. The 2026-10-07 status
(source preservation, the remote agent's backups) is in git history at
`38357808:docs/status.md`. All ten goals below remain open.

## 2026-10-17 Mac local continuation

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
- Still failing on a clean `1694b049f` snapshot:
  `test_hoist_global_cell_boundaries.py::test_host_ir_global_reads_do_not_become_capture_values`
  (both modes), the `_hoist_nested_funcdefs` length check (1810 lines there,
  1833 now, against 1800), `test_py_function_decorators.py::test_same_module_bare_decorator_still_applies`
  ("slot-call Call lacks an immediate owned-result handoff") and
  `::test_imported_bare_decorator_is_metadata_noop` (`singledispatch` refused),
  `test_py_native_closure.py::test_native_closure_captures_starred_unpack_target`
  ("starred unpack target must be a bare name"), and
  `test_py_nested_hoist.py::test_nested_hoist_caches_reject_reused_object_id`.
  The last forces every `id()` equal; `boxed_function_defs` (id -> retained
  object) then keeps only the last def, which live objects cannot trigger.
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
  with host open flags). Still failing: `test_bytes_decode_result_roots.py`,
  which models the decoder body that `c21f5a09` replaced with
  `_bytes_decode_body` and `py_text_decode`; it needs a model of the new body.
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

CI limits changed in `263d12af` (maintainer request: "至少能成功"). The push
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
