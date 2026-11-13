# Executable import-scanner compile corpus

Select `benchmarks/ci_ab/import_scan/manifest.json` as the reviewed
`pcc.ci-ab-inputs.v1` input manifest. It contains one warmup and exactly six
distinct measured entry files. The manifest, every input, these contracts and
all measurement tools belong in **both** source trees, byte for byte. The only
admitted A/B source difference is
`pcc/frontends/python/pipeline_import_scan.py`; adding the corpus to only one
arm is not a valid experiment.

This is a synthetic executable **compile slice**, not a representative compiler
bootstrap workload and not evidence that Stage2 improved. It is deliberately
small enough not to manufacture an amplified result by adding megabytes of
comments or unused string literals. No measured result is recorded here.

## Inputs and independently derived outputs

For each marker class, `n`, `4n` and `16n` mean 32, 128 and 512 straight-line
integer additions inside the executed `compute` function. They do not mean
exact whole-file byte ratios: imports, function scaffolding and output checks
have fixed overhead. The additions are exactly `total = total + i` for
`i = 1 .. count`; their sums are 528, 8256 and 131328.

The manifest keeps same-scale inputs adjacent and reverses marker-class order
at `4n`. Since the harness alternates compiler order by pair index, both marker
classes see both compiler orders; marker class is not perfectly confounded
with which compiler runs first. Six distinct inputs give only three examples
per marker class, not repeated timing samples at each scale.

Each program then calls the public `urllib.parse.quote` on two ASCII strings
with `safe=""`. Complete expected stdout is:

```text
<sum>
a%20b%2Fc
d%20e%2Ff
```

All three lines end in a newline. These are independently derived expectations,
not claimed observed output. The reference test must confirm them with CPython,
and the existing native compile A/B must independently compile and execute both
arms and the unchanged baseline/baseline control.

- `negative_*.py` contain no `except AttributeError` marker. They exercise the
  proposed exact-string negative shortcut on actual entry source read by the
  compiler, not by importing or mocking the helper in the emitted program.
- `positive_*.py` contain a syntactically recognized `except AttributeError:`
  suite with a real `from urllib.parse import quote as fallback_quote`. The
  suite is reached by raising and catching `AttributeError`; the imported alias
  produces the third output line. The outer `quote` import independently anchors
  the provider. Closure discovery must hide the fallback import, while the
  emitted program must retain and execute it. The positive branch is the
  unchanged scanner algorithm, not the unchanged-compiler drift control.
- `warmup.py` exercises that same provider and reached fallback with sum 6. Its
  complete expected stdout is `6\na%20b%2Fc\nd%20e%2Ff\n`, checked by the
  reference contract. The v1 manifest has only a warmup path, so it does not
  claim an extra warmup-output field or a seventh measured input.

## Actual compiler route and the frozen-input boundary

Source review at `f869bb1316f85f4f1f658b5106e865586efe97a5` establishes this route;
future source changes must retain or revalidate it:

1. `scripts/run_pcc_compile_ab.py::_compile_one` invokes the native compiler with
   `--ir-scaffold=on --backend self <entry> -o <output>`, not emit-only mode.
   The compiled `pcc/__main__.py` enters
   `pcc.driver.cli_bootstrap.bootstrap_cli_sys_argv_exit`, then its ordinary
   Python-source branch and `_observed_compile_python` call `compile_python`.
2. `compile_python` selects the route and calls `_compile_python_routed`.
   Before codegen-worker selection, `_prepare_single_source_compile_closure`
   sees executable output and calls `_collect_relative_module_closure`.
   The flattened plain entry reaches `_top_level_import_targets`, which calls
   `_without_attribute_error_handler_imports` on its actual file contents.
   This is coordinator closure scanning, not AST/export-worker execution.
3. The public `urllib.parse` import is discovered by the ordinary provider
   policy. `_expand_required_native_builtin_providers` admits the owned
   `urllib` package, and `_sources_use_native_stdlib` enables recursive stdlib
   discovery. The entry is routed to `compile_python_multi`, whose
   `_prepare_multi_source_compile_closure` performs relative discovery,
   scaffold filtering, required-provider admission and recursive discovery.
   `_expand_recursive_stdlib` admits the real `urllib.parse` provider and its
   `urllib` parent. `_package_import_targets` reads the real package files and
   calls the same filter before scanning their package-local imports.
4. The provider files are existing, unchanged
   `pcc/stdlib/urllib/__init__.py` and `pcc/stdlib/urllib/parse.py`. The resolver
   uses the common frozen baseline tree supplied by `PCC_SOURCE_ROOT` and
   `PCC_REPO_ROOT`. Both measurement arms and both unchanged-control contexts
   use those same bytes. `quote`'s ordinary ASCII path keeps alphanumeric
   characters and writes uppercase `%HH` for the space and slash. This corpus
   makes no non-ASCII, codec or whole-urllib compatibility claim.

The orchestrator and inner A/B runner each copy **only the entry file**, finally
renaming it to `pairN.py`. There are no sibling corpus imports, package-relative
entry imports, absolute checkout paths or additional search-path requirements.
The owned `pcc/stdlib` provider already belongs to the frozen compiler/helper
source snapshot; no arbitrary benchmark directory needs to be copied. No
production resolver, stdlib source or harness extension is needed for this
corpus. A new local package corpus would require generic, explicit closure
snapshot support in both freeze layers and separate review.

Only the **entry** arithmetic/source scan scales. The small fixed provider
package is scanned as well, but its source size does not scale. The package
files currently contain no matching fallback marker, so recognized positive
coverage belongs to the entry. Their package-local scan can legitimately
return no additional dependencies. The common source test observes the actual
filter calls and verifies the resolved paths, rather than interpreting that
empty return as a skipped scan.

## Validation and result boundaries

`tests/test_ci_ab_import_scan_corpus.py` checks the exact manifest,
distinct inputs, arithmetic/marker shapes, scanner content semantics, real
closure discovery after entry-only flattening, and CPython reference stdout.
Its transparent source-test observers delegate to the original scanner and
resolver. They establish the source route, not native execution or performance.
There are no candidate-only object-identity expectations in these shared tests.

The existing campaign owns the six alternating compile pairs, real unchanged
baseline/baseline control, equal emitted bytes, complete stdout, output/runtime
and compiler linkage checks, frozen receipts and unchanged acceptance
thresholds. A helper microbenchmark, CPython output or source trace alone cannot
satisfy those native gates. Review the individual scaled inputs as well as the
aggregate result: fixed provider/compiler overhead can conceal a scaling cost.
No result from this small synthetic corpus establishes full Stage1/Stage2
parity, pcc2/pcc3 fixed point, five-GC equality or representative production
speedup; the original owners remain mandatory.

Keep the current resource contract unchanged: six source-gate workers with the
original 90-second/1-GiB guards; one sampler with a 4-GiB ceiling, 2-GiB floor
and 512-MiB reserve; one absolute 195-minute campaign within the 200-minute job;
Stage1 at most 7200 seconds; build frontend/backend/link 2/2/8 and measured
frontend/backend/link 1/2/2. This corpus does not supply qualified source refs,
runtime provenance, authorization to publish a candidate, or permission to run
outside CI. Keep raw logs, receipts and measured reports outside Git.
