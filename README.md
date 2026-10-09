# pcc

**A native compiler for Python and C, written in Python.**

pcc compiles Python and C source into native programs. This experimental
compiler and runtime aims to preserve Python's syntax and behavior without
libpython, with self-hosting, five garbage collectors, value types and native
concurrency at its core.

## What makes pcc different

- **An owned native toolchain.** Python and C frontends feed pcc's own IR
  optimizer, code generator, object writers and linkers. The IR uses LLVM
  syntax; the default and only public backend is the in-tree `self` backend.
- **Python and C in one project.** The Python frontend includes type inference,
  classes, closures, generators and async lowering. The C frontend includes
  preprocessing, C99-oriented parsing, pointers, aggregates, function pointers
  and multi-file builds. Coverage depends on the feature and target.
- **Opt-in value classes.** `@pcc.valueclass` describes immutable, identity-free
  payloads. Supported values can stay unboxed until object boundaries. Ordinary
  classes retain their identity model; preserving Python semantics is a core
  requirement, including arbitrary-precision integers.
- **Native concurrency.** An optional threaded runtime, `async`/`await` lowering
  and compiler-recognized virtual threads provide building blocks for
  concurrent programs. The scheduler manages continuations, timers and I/O
  waits with explicit GC roots. Workload-specific qualification is ongoing.
- **Package and C interoperability.** Call compiled C functions from host
  Python with `import pcc`, or use `pcc.extern` declarations in compiled code.
  Owned standard-library providers, package environments and extension-ABI
  checks support the goal of compiling real applications. General package
  compatibility, including NumPy and other ecosystem targets, is unfinished.
- **Self-hosting as a correctness test.** The goal is to build the compiler
  with pcc, then repeat until it reaches a stable fixed point.
  [The compiler generations](#what-is-pcc1) explain what that means.

## Quick start

Minimal native-output example on Linux x86_64, with Git and
[uv](https://docs.astral.sh/uv/) installed:

```bash
git clone https://github.com/ikshengmin/pcc.git
cd pcc
printf 'print("Hello, PCC!")\n' > hello.py
env -u LC_ALL uv run python -m pcc hello.py -o hello
./hello
```

Expected output: `Hello, PCC!`. CPython runs the compiler; `./hello` runs the
native result. Defaults are `--backend self`, `--python-libpython off` and
`--ir-scaffold on`. To compile and run in one command, omit `-o hello`.

The checkout's `.python-version` selects host CPython. `uv run` prepares the
project's [Python environment](https://docs.astral.sh/uv/concepts/projects/run/).
The first compilation may then take several minutes to build pcc's runtime.
Editable development setup does not build a native `pcc1` compiler.

### Build the runtime separately

From the repository root, with default build settings and no `PCC_*`
overrides, this low-level entry point prebuilds the host runtime:

```bash
env -u LC_ALL uv run python -m pcc.frontends.python.owned_runtime_build \
  --output build/runtime/libpy_runtime_pcc_py.a
PCC_RUNTIME_ARCHIVE="$PWD/build/runtime/libpy_runtime_pcc_py.a" \
  env -u LC_ALL uv run python -m pcc hello.py -o hello
./hello
```

Keep the compiler/runtime source, target and build settings matched between
the two commands, including `PCC_WITH_THREADS` and `PCC_REFCOUNT_KIND` if set.
The archive's provenance sidecars must remain alongside it. This builds the
runtime library, not pcc1; a mismatched explicitly selected archive is rejected.

Without an explicit archive, pcc checks for a matching packaged runtime, then
builds/reuses `pcc/runtime/build_owned/<target>/<configuration>/libpy_runtime_pcc_py.a`.
Default target/configuration pairs are `x86_64-unknown-linux-gnu/single-atomic`
on Linux x86_64 and `arm64-apple-darwin/single-atomic` on macOS arm64.
A threaded build uses `threads-atomic`. Reuse avoids a runtime rebuild, but
compilation and cache checks still have their own cost.

## Compile and call C from Python

Compile a C file, load its shared library and call its functions from host
Python. This route currently requires **CPython on macOS arm64**.

Create `add.c` in the repository root:

```c
int add(int a, int b) { return a + b; }
```

Create `call_c.py` beside it:

```python
import pcc

m = pcc.module("add.c")
print(m.add(3, 4))
```

Run `env -u LC_ALL uv run python call_c.py`. Expected output: `7`.
`pcc.module` uses the owned shared-library builder and loads the result through
`ctypes`. `pcc.build` returns an artifact without loading it; see the
[API source and options](pcc/api.py).

## Five garbage collectors

pcc develops five selectable collectors behind a shared runtime contract:

| Mode | Approach |
|---|---|
| `0` | Reference counting with cycle collection; the default |
| `1` | Incremental tricolor mark-and-sweep |
| `2` | Concurrent mark-and-sweep |
| `3` | Generational young/old collection |
| `4` | Colored, relocating collection |

Select one at program startup, for example `PCC_GC_BACKEND=0 ./hello`.
Each collector has separate correctness and concurrency gates. Benchmarking
tracks pause behavior, memory use and throughput over time; five selectable
modes do not imply that all workloads are qualified across all five.

## What is pcc1?

The numbers name **compiler build generations**, not Python versions:

- **pcc0:** the compiler's Python source running on host CPython, as above.
  Its public command is `pcc` or `python -m pcc`.
- **pcc1:** the first native compiler executable built from that source by pcc0.
- **pcc2:** the compiler rebuilt from the same source using pcc1.
- **pcc3:** another rebuild of that source using pcc2.

The separate Stage1 developer build entry is:

```bash
env -u LC_ALL uv run python scripts/bootstrap.py --stage 1 --out-dir build/bootstrap
```

On a successful Unix build, the output is `build/bootstrap/pcc1`. This is a
substantial build; current-source Stage1 and fixed-point qualification remain
open. A usable pcc1 must compile and execute programs itself. A fixed point
further requires successful Stage2/Stage3 builds and byte-for-byte agreement
under the [bootstrap checks](docs/developer-reference.md#bootstrap).

The Python distribution is named `python-cc`; its module and host launcher
are named `pcc`. A native `pcc1` is a separate build artifact.

## Experimental GPU work

`@gpu.kernel` markers, Kernel IR and a bounded Metal compilation/launch path
target macOS with the Xcode Metal toolchain and compatible hardware. Kernel
shapes and real device execution have their own gates; general Python GPU
acceleration is not claimed. See the [GPU scope](docs/design/pcc-gpu-next-work.md).

## Status and scope

Linux x86_64 native examples have recorded execution results. Broader Python
and standard-library compatibility, self-hosting, package installation and
platform coverage remain under qualification. Five-GC correctness, concurrency
and long-running performance need evidence for the exact source and workload.

No-libpython describes the Python-runtime boundary; it is not a universal
zero-libc or C-speed claim. See [verification status](docs/verification-status.md)
and [CI runs](https://github.com/ikshengmin/pcc/actions/workflows/pcc1-package-parity.yml).

## Documentation

- [Project intent](docs/project-intent.md): goals and semantic commitments
- [Architecture](docs/architecture/01-overview.md): pipeline and source map
- [Developer reference](docs/developer-reference.md): modes, APIs and runtime controls
- [Python limitations](docs/python-limitations.md): compatibility boundaries
- [Compiler contracts](docs/compiler-contract.md): dependency and execution ownership
- [Contributing](AGENTS.md): development and validation workflow
- [Book: English and 中文](books/README.md): a longer guide to the design

## License

[MIT](LICENSE)
