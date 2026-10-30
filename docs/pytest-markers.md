# Complete pytest groups in unified native CI

`.github/workflows/pcc1-package-parity.yml` owns Stage1, the existing native
checks, and **both complete pytest marker groups on all four native platforms**:

| Platform | Runner | Compiler format |
| --- | --- | --- |
| macOS ARM64 | `macos-15` | Mach-O ARM64 |
| Linux x86-64 | `ubuntu-24.04` | ELF64 x86-64 |
| Linux ARM64 | `ubuntu-24.04-arm` | ELF64 AArch64 |
| Windows x86-64 | `windows-2022` | PE32+ AMD64 |

On push and pull request, `pytest-markers` has eight jobs: each platform runs
`-m 'not integration' tests` and `-m integration tests` independently. Both
are actual pytest execution, using the unchanged `pyproject.toml` default
`-n 6 --dist=loadgroup`. There is no separate `workflow_run` consumer, no
second dispatch, and no latest-master condition that discards an older run
when a peer advances the branch. Changes anywhere under `tests/` also trigger
this unified workflow.

## One Stage1 build, source-bound artifacts

The original producers remain `other-platform-native-wheel` (Linux x86-64,
Linux ARM64, Windows x86-64) and `pcc1-package-parity` (macOS ARM64). Their
Stage1 commands, 7200-second allowance, thread/atomic runtime configuration,
five-collector canaries, resource limits and later native/wheel checks remain.
The independent preflight is not added to the Stage1 critical path.

Each producer exports its successful Stage1 **before** later native checks can
fail. The dependent pytest matrix waits for the existing producer jobs, then
downloads only its own platform's artifact from the same workflow run. Names
and manifest admission bind repository, workflow, run ID, attempt, source SHA,
source digest, target ABI, exact compiler/runtime bytes and original receipts.
A later prior/wheel failure remains a failure; it does not suppress an already
admitted Stage1 input. Missing exports fail the dependent download/admission,
not a silent skip or test pass.

Import rejects wrong platform/binary ABI, stale source or attempt, non-threaded
runtime configuration, changed hashes, duplicate/unexpected tar members,
links, traversal and existing destinations. Runtime provenance is checked
against the current checkout. No exporter/importer builds or provisions native
inputs; the consumer installs frozen dev dependencies with
`--no-install-project`. Original receipt bytes and absolute producer paths are
preserved; a different checkout path is not silently rebased.

Linux/Windows integration gets a byte-identical copy of the original shared
Stage1 receipt, without a symlink or another compiler copy. macOS's
`shared_stage1_pcc1` fixture explicitly rechecks the CI lease/source/hashes and
uses the imported compiler. Both routes fail closed on absent/stale admission
instead of falling back to a hidden Stage1 rebuild.

## Fixed-point ownership and manual selections

The default is `full_chain=false`, `pytest_selection=both`. In this mode:

* Producer jobs build Stage1 once. Linux/Windows retain their five-GC native
  canaries; macOS retains its original native publish barrier.
* Each platform's **integration pytest job** executes the selected
  `test_pcc_bootstrap_full_gc0.py` through `gc4.py` bodies. Those bodies own the
  real Stage2/Stage3 builds, native checks and raw-byte fixed-point assertions.
  Reusing Stage1 is not fixed-point evidence. An interrupted or timed-out chain
  is not a pass.

The existing manual producer-owned full-chain route remains available, but
requires the explicit combination `full_chain=true`,
`pytest_selection=not integration`. That mode runs the existing Linux/Windows
producer `--full-chain` five-GC Stage2/Stage3 validation, all four non-integration
pytest jobs, and **one mandatory macOS integration pytest job**. The latter is
macOS's sole real Stage2/Stage3/fixed-point owner; its producer still builds only
Stage1. The configuration log and input descriptions explicitly identify this
exception to the non-integration subset, so no platform silently loses its
fixed-point owner. This mode does not claim complete Linux/Windows integration
pytest coverage. Combining `full_chain=true` with `both` or `integration` fails
the configuration job before any heavy job starts, rather than executing the
Linux/Windows Stage2/Stage3 chains twice or silently dropping tests.

A normal manual unified run can choose `both` (default), `integration`, or
`not integration` while leaving `full_chain=false`. For example, the explicit
legacy mode is:

```sh
gh workflow run pcc1-package-parity.yml --ref master \
  -f full_chain=true -f pytest_selection='not integration'
```

This is a documented operator command, not an instruction to duplicate an
active CI run.

## Limits and honest results

Each marker retains its existing watchdog allowance: 2400 seconds for
non-integration and 1800 seconds for integration. Linux/Windows retain the
16 GiB tree ceiling. macOS uses the existing observed-memory admission with a
4 GiB ceiling, 2 GiB floor and 512 MiB reserve, never rounding a low observation
up. Windows uses the real Job Object watchdog; POSIX uses the existing sampled
process-tree watchdog. The outer guard does not take the performance lock that
selected bootstrap tests own. No cap or deadline is increased to fit the suite.

These budgets are **not** evidence that every complete group currently fits.
Unsupported OS-specific tests, unavailable dependencies and resource failures
must be diagnosed as actual results; this integration adds no blanket skips,
file allowlists, `-k`, `--ignore`, `-n0`, collection-only substitute or IR/model
replacement for native execution. Original hardware/opt-in gates are recorded
as explicit inventory gaps, not universal coverage.

All six workers record their full matching inventory and actual selection.
Live phase reports, JUnit, guard logs and terminal receipts distinguish actual
passes, failures, unexecuted fail-fast tails, deselections, skips and xfails.
`PASS_WITH_GAPS` is not a whole-suite or all-hardware pass. A local contract test
or miniature six-worker test is not native four-platform qualification.

The frozen `85b0e08c` research comparison source remains checked out separately
and hash-validated; current source never substitutes for that immutable baseline.
The PyPI release workflow is separate and unchanged.
