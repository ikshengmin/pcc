# Bounded typed-template PCC experiment

Isolated research; **no production hook or automatic patch application**.
Baseline: `ikshengmin/pcc@85b0e08cd28e143d87607a5cd76e8f7c350cabd7`.
The accompanying [verified-fold experiment](VERIFIED_FOLD.md) is a separate
logical deliverable.

## Method and boundaries

[Typed Template Fuzzing](https://arxiv.org/html/2610.31342v1) motivates precomputed
type/scope annotations and a query-free text mutation loop. This small adaptation
uses five checked-in, grounded templates; it does **not** reproduce Typos's LLM
annotation/repair system or its large campaign. Its reported validity is not
transferred to PCC.

Three paired arms share seed 42 and the template schedule: typed substitution,
scope-only substitution, and naive substitution ignoring types and scope.
The mutation loop itself does not parse Python. A separate safety checker
examines every generated program before execution. It allows one `probe(2, 5)`,
no imports/attributes/loops/recursion/external access, depth 2 mutation, at most
20 cases per arm, 4096 source bytes, 512 AST nodes, and resource bounds on
intermediate integer magnitude (4096), sequence length (1024) and expression
depth (8). Growth is checked abstractly before Python evaluates the expression.
The runner does not accept arbitrary source files.

Real target path: `parse_and_lift → infer_module → L1CodeGen.generate`
(`ir_scaffold_mode="on"`) `→ parse_self_backend_module → verify_parsed_module`.
`generate` returns serialized owned IR; lowering and serialization are timed
together. No hand-authored IR is substituted for compilation. This is an
explicit host/text route, not normal direct-indexed Stage1.

## Observed small run

Cloud Linux x86_64, **CPython 3.15.0rc1**, not the repository's final-3.15.0 pin.
20 cases per arm, depth 2, seed 42; one run, no statistical confidence claim.

| Arm | Python-valid | PCC accepted | Both | PCC time / both-valid case |
|---|---:|---:|---:|---:|
| typed | 20/20 | 20/20 | 20 | 219 ms |
| scope-only | 6/20 | 17/20 | 5 | 650 ms |
| naive | 6/20 | 18/20 | 5 | 636 ms |

Python-valid means this fixed input completed without an exception, **not**
validity for all inputs. PCC acceptance means lowering/IR well-formedness, not
native behavioral equivalence. Dynamic Python TypeErrors need not be rejected
by a compiler. Total PCC time was **4.38 s / 3.25 s / 3.18 s**: typed generated
more useful inputs but did not make individual compilation faster. Timing has
visible cloud variance; the approximately 3x useful-check ratio is not a compiler
or application speedup. Generation times were only milliseconds and are not a
credible throughput benchmark at this sample size.

### Finding and typed blind spot

Both controls independently reached the **same** cross-type comparison gap;
count this once. Six fixed reductions all preserved PCC's explicit
`NotImplementedError: Layer 1 cannot coerce TupleType to int`:

```python
def probe(a: int, b: int):
    return () == 0
result = probe(2, 5)
```

CPython returns `False`. This is a lowering capability gap, **not observed native
miscompilation**, and not a claim of a previously unknown bug. The included
`cases/tuple_int_comparison.py` is the shortest of the six checked reductions,
not a globally proved minimal program. Crucially, the strictly typed expression
pool excluded this legal cross-type comparison: **better validity missed this
defect**. A useful future generator needs some semantics-aware cross-type
operators; no such expansion was run here.

## Reproduce (existing tools only)

Set `BASE` to the frozen PCC source, `RESEARCH` to this package's parent
`scripts/research`, `PYTHON` to an existing compatible interpreter, and `OUT` to a
writable evidence directory. The recorded source snapshot was Git-blob-verified
by the shared baseline task; it is a source closure, not a full Git checkout.

```sh
export PYTHONDONTWRITEBYTECODE=1 UV_PYTHON_DOWNLOADS=never
export PYTHONPATH="$BASE:$RESEARCH" PCC_RESEARCH_SOURCE="$BASE"
export UV_CACHE_DIR="$OUT/uv-cache"
flock "$LOCK" timeout 50s uv run --offline --no-project --python "$PYTHON" \
  python -m pcc_ttf_verified.run_fuzz --count 20 --depth 2 --seed 42 \
  --output "$OUT/fuzz-small"
flock "$LOCK" timeout 30s uv run --offline --no-project --python "$PYTHON" \
  python -m pcc_ttf_verified.reduce_failure --output "$OUT/minimized"
uv run --offline --no-project --python "$PYTHON" python -m pytest \
  -x -vv --tb=short tests/research/test_pcc_ttf_verified.py
```

Keep repository pytest defaults (`-n 6 --dist=loadgroup`). In the isolated
delivery checkout its small `pyproject.toml` supplies the same worker defaults.
Generated corpora, per-case timings/failures and summary JSON are written under
`OUT`; retain those outside the production repository. No native compiler build,
five-GC run, Stage1, CI, external target or security campaign was performed.
