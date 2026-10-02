# Current status

Updated October 2, 2026. This is the maintained progress page; update it in place.
[Project Intent](project-intent.md) and [compiler contracts](compiler-contract.md)
remain the requirements. Review repairs and pre-commit checks are part of the
original work, not a replacement for its unfinished goals.

## Where work stands

The repair candidate starts from the exact submitted HEAD, `1a59d09`. Its full
tracked-file contents and executable modes were verified before editing. The
separate live worktree and its unfinished changes are preserved. Nothing in this
repair has been committed or promoted as an installed compiler.

Four failures were reproduced before repair:

- The exception runtime module could not compile because internal helper
  annotations misclassified values that may be managed runtime objects
- Full-bootstrap tests could not collect after a helper rename; callable gate
  probes were also being ignored
- Imported-constructor tests still expected the old raw binder API although the
  emitted code uses the authoritative-slot binder
- C `typeof(sizeof(int))` reached an unsupported evaluation path

Repairs are being integrated and checked on this one candidate. A separate
scratch-frame review found two exception-cleanup reads that confuse a slot index
with a byte offset. The original failing cleanup paths were reproduced and fixed;
focused models pass, while native qualification remains separate.
The scratch-frame layout was preserved by a literal-substitution AST check;
65 deterministic host-model tests passed, including unchanged ownership/error
assertions and new layout checks. Import formatting is checked for whole-module
AST equivalence. These checks do not establish native GC or bootstrap correctness.

Several source-matched whole-runtime builds and focused native regressions have
passed during repair. Later integrations still require requalification. C suite
execution has exposed additional compiler defects, including integer cast widths,
variadic argument handling, float constants and unbounded recursive macro
rescanning. Corpus-oracle language and library settings also needed correction.
Historical test outcomes remain available; they are not a green result for the
current source.

The Boolean return-ownership repair passed seven native programs under all five
collectors and the production stackmap-planner program under all five collectors
on its isolated, source-matched snapshot. That evidence does not qualify the
whole Python suite or the newly integrated candidate. Original full Stage1
qualification is still in progress.

The next qualification must use a freshly built **whole runtime** matched to
this source, collect the full test suite, execute both the C and Python tests,
and compile and execute native C and Python smoke programs. Preserve a
deterministic node inventory and report passes, failures, unavailable tests and
unobserved tests separately. Bounded shards resume unobserved coverage rather
than restarting completed work; each failing shard stops before diagnosis.
A successful runtime module, object file or Stage1 link is not a successful
native compiler or a Stage1 → Stage2 → Stage3 fixed point.

## Next checks

Use the checked-in Python version and an isolated environment. Disable automatic
native provisioning for collection and focused diagnosis. These are reproducible
entry commands, not claims that the final candidate has passed them:

```bash
# Run from the repository root with the isolated environment already selected.
export PCC_NO_AUTO_PCC1=1 PCC_TEST_NO_NATIVE_PROVISIONING=1
export PYTHONDONTWRITEBYTECODE=1
env -u LC_ALL uv run --no-sync pytest -o addopts= -x -n0 -vv --tb=short --collect-only tests
env -u LC_ALL uv run --no-sync pytest -o addopts= -x -n0 -vv --tb=short tests/c
env -u LC_ALL uv run --no-sync pytest -o addopts= -x -n0 -vv --tb=short tests/python
env -u LC_ALL uv run --no-sync pytest -o addopts= -x -n0 -vv --tb=short \
  tests/python/test_imported_constructor_binding.py \
  tests/python/test_dict_super_slot_roots.py \
  tests/python/test_rooted_index_entries.py
```

The integration coordinator first builds the matched runtime, then runs these
checks with timeouts, process-tree RSS caps and separate logs. Native consumers
must receive explicitly verified matching artifacts. Full bootstrap and other
large test nodes run as explicit sequential jobs, not surprise fixture builds.
After those pass, the self-host entry is `scripts/bootstrap.py`; build stages in
order and require raw-byte equality for the final fixed-point comparison. Use
[the validation workflow](validation-workflow.md) for controlled long runs.
Do not run competing runtime/bootstrap builds or change their frozen inputs.

## Original goals still open

A historical green result does not close any item below on this source.

- Deliver the requested qualified repair batch and commit, then continue all
  original goals; do not include unqualified work in that delivery
- Remove external execution owners across the workflow: host pcc uses CPython
  and its standard library only; native pcc1 owns C preprocessing, runtime
  construction, optimization, emission, linking, archives, caches and installation
  without LLVM, external C compilers, host Python or libpython
- Qualify complete macOS, Linux x86_64, Windows and Linux aarch64 jobs, including
  runtime, installation, native C/Python and the original self-host chain.
  Preserve the macOS three-core M1 / 45-minute target and resolve the recorded
  Python, wheel and owned-Meson boundaries rather than bypassing them
- Run all five GC self-host chains within the six-hour total budget, with bounded
  concurrency, matched artifacts, successful per-stage timings and tree RSS.
  Recheck Stage2 performance against Stage1 on successful comparable runs
- Close native pcc0/pcc1 CLI and API capability gaps, unavailable function stubs,
  native runtime self-construction, bindgen, lockfile and diagnostic boundaries
- Reproduce and close the GC4 concurrent pointer failure on the original program;
  qualify relocation and allocation selection while preserving finalizers,
  weakrefs, resurrection and the shared root/slot contract. Measure long-running
  RSS, pause times, throughput and fragmentation
- Finish external TLS and BSD archive-symbol regressions, stale freestanding IR
  expectations and partial-async timeout diagnosis without removing the tests
- Qualify structured runtime emission on the default production path through the
  whole runtime and bootstrap, rather than extrapolating from selected modules
- Finish repository artifact/reference cleanup within explicit authorization;
  retain source, tests and failure evidence
- Complete the native scheduler/gateway comparison with asyncio using identical
  inputs and configuration, including long-running behavior and traceable results
- Complete pcc-gui and Harness against their pinned DeepSeek reference, including
  actual native application interactions and visual acceptance; source presence
  and host-model checks do not establish product parity
- Finish direct tail calls, multiply-add, peephole work, identity-based scaffold
  recognition and thread logging with generic native semantics and measured gains

The five EDG-inspired improvements also remain open: complete frontend semantic
facts; one authoritative layout source; layered host/native/execution validation;
measured per-function memory lifetimes; and centralized target ABI qualification
through full native, bootstrap and cross-platform jobs. Retain the opt-in value
model, arbitrary-precision ordinary Python integers and C as a first-class
capability throughout this work.

## Evidence and recovery

Detailed manifests, commands, source hashes and logs live outside the working
repository in the sibling repair workspace. Its `actual-head-verification.json`
identifies the immutable baseline; diagnosis and final-gate directories identify
the source used for each run. Earlier receipts are historical evidence only.

The 19 old engineering handoffs are preserved byte-for-byte in
`../recovery/2026-10-02-handoffs/` relative to this checkout. Its `manifest.json`
records each original path and SHA-256; `README.md` explains verification and
non-overwriting restoration. They total 280,099 bytes. The nine other knowledge
files remain in the repository. The original handoffs are also recoverable from
commit `1a59d09df54cc0b7b46219395d3419cab8543675` at their original paths.
