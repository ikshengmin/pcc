# Current status

Updated October 3, 2026. Maintain this page in place. [Project Intent](project-intent.md)
and [compiler contracts](compiler-contract.md) remain the requirements; focused
review repairs do not replace the original ten goals.

## Source and recovery boundary

A cloud filesystem reset lost work after the October 2, 19:20 UTC backup,
including generated compiler/runtime artifacts and unuploaded test receipts.
The three source repositories were restored from verified Library bytes matching
the last synchronized local commits: PCC `351c617eb27979ecaad6c96bc94a38b98fbbd444`,
gateway `83577bc41dc79d3ead2e217bfa04f0f500799012`, and GUI
`3843a75a5634dfb3107890a2d628517713782080`.

The exact PCC base `1a59d09df54cc0b7b46219395d3419cab8543675` was verified
across 29,601 Git blob bytes and modes, followed by all 154 paths in the backed-up
forward delta. This restores source content, not Git history or qualification.
The candidate includes reconstructed later changes, each with actual before/after
bytes, modes and hashes outside the repositories. Historical passing results
cannot qualify these recreated changes.

Hourly local synchronization is authorized from October 3, 04:40 UTC. Include all
legitimate source work, including failing or unqualified work, in one commit per
changed repository, preserve unrelated local edits and do not push. The accidental
`work-in-progress` directory is removed with its bytes preserved outside source.
Scratch files, backups and duplicate handoffs must stay outside the repositories.

## Fresh evidence and active repairs

CPython 3.15.0rc1 and locked pytest dependencies are restored in an isolated
environment. Fresh post-reset results are tied to their recorded source hashes;
later changes require the affected checks again:

- Compiler/capture/producer matrix: 107 passed, 1 failed, 28 deselected. The
  original integer-constructor case exposed an unowned repr(self.value) result
  in a generated dataclass __repr__. A shared repr/ascii producer repair is now
  reconstructed and its13-case causal gate passed, including the unchanged
  original program and strict rejection checks. Two native cases are pending.
- Callable metadata: 20 host/source-model checks passed, covering rooted
  construction, default lifetimes, callback representation and helper contracts.
  The corrected py_func and py_class components emitted strict owned objects
  in38.98s/339MB with the codegen checksum unchanged. A whole matched archive
  and native execution remain pending.
- Installed-wheel isolation/provenance: 15 checks passed, including a real
  isolated interpreter import fixture. This does not qualify a native wheel.
- Runtime receipt producers:25focused/controlled Make cases passed, including
  missing/null/configuration/stale-source/codegen rejection. The producer files
  were unchanged; an early broad preflight snapshot preceded a separate metadata
  annotation correction, so no whole-candidate stability is claimed for this gate.
- Explicit default/threaded/integration runtime fixtures:61host cases passed
  with873production/fixture hashes and modes unchanged, including actual
  archive-byte tampering and MZ-prefix handling. This is no native Windows proof.
- GUI Harness: 147 host tests passed with all 32 input hashes unchanged. Native,
  TLS, reference application, interaction and pixel gates remain open against
  DeepSeekHarness `47f943859bef60e4160492346772ded9b24f765a`.

Reconstructed compiler changes include transitive closure captures, hidden
capture operands, persistent default roots, pooled-name ownership, and shared
Call/Subscript/BinOp result publication. Runtime list/tuple concatenation and
mixed numeric/reflected-add dispatch have code and formal tests, but no fresh
native qualification. Live class namespace and bound-method delegation remain
partially unreconstructed. Runtime configuration/inventory producers and explicit
fixture provenance are freshly checked at their host/component boundaries.
C parser/abort work, narrow atomics and thread logging remain active reconstruction
or qualification work.

The pre-reset full compiler graph audit observed 237 lowering passes and 204
first failures across 441 modules, with no timeouts. These are historical counts
from a lost graph, not independent bug counts or current results. A fresh graph
must be exported and audited through the production worker with validation
unchanged before the next cold runtime/bootstrap qualification. No current pcc1
or pcc2 exists, and no Stage2 to Stage3 fixed point is established.

## Qualification order

Use the checked-in Python version, isolated outputs, durable node logs, the shared
heavy-run lock and a process-tree RSS watchdog. Disable surprise native builds
with PCC_NO_AUTO_PCC1=1 and PCC_TEST_NO_NATIVE_PROVISIONING=1. Every pytest
diagnostic uses -x -n0 -vv --tb=short. Timeouts guard runaway processes; they are
not acceptance budgets. Freeze source, configuration and artifact identities.

Finish causal producer checks and the fresh full graph audit, then build a matched
whole runtime. Execute changed-shape programs under all five GCs, original C/Python
controls, and complete tests/c and tests/python inventories. Report passed, failed,
deselected, unavailable and unobserved nodes separately. Run dependent bootstrap
stages in order through scripts/bootstrap.py; only successful Stage2 and Stage3
raw-byte equality establishes the fixed point. See [validation workflow](validation-workflow.md).

## Ten goals still open

1. Native pcc0 to pcc1 to pcc2 to pcc3, byte-identical Stage2/Stage3, original
   C/Python execution controls, and no libpython.
2. Equal semantics and production correctness/concurrency/performance for all
   five collectors. Include original GC4 getter/iterator/capacity failures,
   relocation, FRESH_ALLOC, finalizers, weakrefs, resurrection and long-run RSS,
   throughput and stability under the shared authoritative root/slot contract.
3. Actual macOS ARM64, Linux x86_64, Windows and Linux aarch64 jobs, including
   Linux aarch64 Stage1 through Stage3. Preserve macOS-15 standard three-core M1,
   7 GB and 45-minute qualification, plus installed wheel/version parity.
4. Owned compiler/runtime/ABI/provenance, native runtime self-build, bindgen,
   lockfile, CLI/API/diagnostics and real function implementations, without LLVM,
   host C compiler or hidden external-owner fallback.
5. Complete EDG-inspired semantic facts, one layout authority, layered native
   validation, compilation-memory targets and centralized target ABI coverage.
6. Successful comparable Stage2 no slower than Stage1, and shared builds for
   all five collectors within six hours.
7. Complete C and Python suites and authorized cleanup, including external TLS,
   BSD archives, freestanding assertions and partial-async timeout cases.
8. Gateway semantics and measured performance beyond asyncio with identical
   inputs, long-running behavior and traceable configuration.
9. Tail calls, multiply-add, peepholes, scaffold identity and PCC_LOG=thread,
   with native semantics and measured effects.
10. GUI/Harness native app/five-GC behavior, HTTP/TLS, Loader HMR, typed FFI/UI
    bridges, interaction and pixel equivalence against the pinned reference.
    Host models and cross-compilation do not prove device execution.

## Durable evidence

The recoverable base delta is Library `libfile_85beb2beb8f88191b47144ac4fa719ea`.
Reconstruction code and receipts are versioned under
`libfile_a5f7c11281008191be4ea89ea9611cbd`; version 7 contains the October 3,
04:27 checkpoint. Archives contain recoverable code bytes and a verified
baseline/incremental chain, not only recipes or remembered hashes. The cloud
recovery directory is pcc-reset-recovery-20261003 outside the source repositories.
Save every coherent batch before heavy tests and preserve failure evidence.
Old generated binaries, runtimes and the 441-module graph must be rebuilt;
historical hashes do not recover bytes.
