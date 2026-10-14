# pcc

[![PyPI](https://img.shields.io/pypi/v/python-cc)](https://pypi.org/project/python-cc/)
[![Python](https://img.shields.io/pypi/pyversions/python-cc)](https://pypi.org/project/python-cc/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

The PyPI badges describe the published package's version and declared Python
requirements, not qualification of this source tree or its native compiler.

**A native compiler for Python and C, written in Python.**

pcc compiles Python and C source into native programs. This experimental
compiler and runtime aims to preserve Python's syntax and behavior without
libpython, with self-hosting, five garbage collectors, value types and native
concurrency at its core.

Start with [Install](#install), [Quick start](#quick-start), or
[calling C from Python](#compile-and-call-c-from-python).
See [Status and scope](#status-and-scope) for the qualification boundaries.

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

## Install

### From source

The shell examples below use POSIX syntax for Linux/macOS.

Install Git and [uv](https://docs.astral.sh/uv/), then prepare this checkout:

```bash
git clone https://github.com/ikshengmin/pcc.git
cd pcc
env -u LC_ALL uv sync --locked
env -u LC_ALL uv run pcc --help
```

The package metadata requires host Python 3.13 or newer; `.python-version`
selects CPython 3.15.0rc1 for repository development. `uv sync --locked`
installs the editable project and its locked development dependencies.
Editable setup skips the release-only runtime and `pcc1` builds.
The host command is `pcc`, equivalently `python -m pcc`.

### Published package

To install the separately published [PyPI package](https://pypi.org/project/python-cc/):

```bash
python -m pip install python-cc
```

This selects a published release, not the source checkout above. Its metadata
and build requirements belong to that release. A successful installation does
not establish current-source native `pcc1`, package compatibility, or a
self-hosted fixed point. See the
[installation boundaries](docs/developer-reference.md#pypi-and-native-installation-boundaries).

## Quick start

### Compile Python

From the source checkout above, a minimal native-output example on Linux x86_64:

```bash
printf 'print("Hello, PCC!")\n' > hello.py
env -u LC_ALL uv run python -m pcc hello.py -o hello
./hello
```

Expected output: `Hello, PCC!`. CPython runs the compiler; `./hello` runs the
native result. Defaults are `--backend self`, `--python-libpython off` and
`--ir-scaffold on` when no `PCC_*` overrides are set.

Functions and type annotations use ordinary Python syntax. For example,
save this as `square.py`:

```python
def square(n: int) -> int:
    return n * n

print(square(7))
```

```bash
env -u LC_ALL uv run pcc square.py -o square
./square                                      # expected: 49
env -u LC_ALL uv run pcc hello.py               # compile and run
env -u LC_ALL uv run pcc hello.py --emit-llvm=hello.ll
```

`--emit-llvm=PATH` writes inspectable IR instead of an executable. The IR uses
LLVM syntax; it does not select an external LLVM backend. The first native
compilation may take several minutes to build pcc's runtime. Later builds can
reuse a matching archive. These small examples do not imply full Python
compatibility; see the [Python frontend scope](docs/developer-reference.md#python-frontend).

### Compile C

Create `hello.c` in the same checkout:

```c
#include <stdio.h>

int add(int a, int b) { return a + b; }

int main(void) {
    printf("%d\n", add(20, 22));
    return 0;
}
```

Compile with the owned backend and run the resulting program:

```bash
env -u LC_ALL uv run pcc hello.c -o hello_c
./hello_c                                     # expected: 42
```

Other C entry points include IR/object output and directory builds:

```bash
env -u LC_ALL uv run pcc hello.c --emit-llvm=hello_c.ll
env -u LC_ALL uv run pcc hello.c --emit-obj hello_c.o --target x86_64-unknown-linux-gnu
# Replace src/ with your C project directory:
env -u LC_ALL uv run pcc --separate-tus src/ -o app
```

`--separate-tus` compiles each C file as a separate translation unit; without
it, a directory build merges its sources. C/library coverage and native
`pcc1` execution require their own gates. See
[C compilation and ownership](docs/developer-reference.md#c-compilation-and-ownership-boundaries)
for project, linker and platform boundaries.

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

### Inspect artifacts and generate bindings

The host-Python API also exposes inspection and C declaration generation.
For example, save this as `inspect_hello.py` and run it with
`env -u LC_ALL uv run python inspect_hello.py` after the Linux hello example:

```python
import pcc

report = pcc.inspect_artifact("./hello")
print(report["format"], report["architecture"])

bindings = pcc.generate_bindings(
    "int abs(int value);",
    target="x86_64-unknown-linux-gnu",
)
print(bindings)
```

Inspection reads supported thin Mach-O64 and ELF64 x86_64 object/static
executable files without running them. It reports declared dependencies, not
runtime dynamic loading or build provenance. Binding generation accepts a
bounded set of expanded LP64 C prototypes and returns `pcc.extern`
declarations for compilation; it does not load those functions into CPython.
The public `pcc inspect` / `pcc bindgen` command routes compile their tool
modules natively and have separate qualification boundaries; see
[inspection and binding tools](docs/developer-reference.md#python-library-and-inspection-tools-in-progress).

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

For a staged macOS arm64 installation, start with the source installer's
options and [toolchain guide](docs/developer-reference.md#one-installed-compiler-for-multiple-projects):

```bash
env -u LC_ALL uv run python scripts/install_pcc1_toolchain.py --help
```

The installer has a `--from-source` entry and a `--stage-only` candidate mode.
Shared applications use a qualified `~/.local/bin/pcc1` through PATH, backed by
a versioned toolchain, rather than a checkout's `build/bootstrap/pcc1`.
Installation and promotion need matching validation receipts; an old installed
compiler does not qualify the current source.

## Packages and related projects

Inspect the package environment selected by the current shell:

```bash
env -u LC_ALL uv run python -m pcc env info --json
```

For an already qualified native compiler, the package command surface is:

```bash
pcc1 env info --json
pcc1 -m pip install /path/to/package
```

`VIRTUAL_ENV`, `PCC_ENVIRONMENT` and data-root overrides can change the selected
environment. Install and compile in the same environment; use `env info` to
check the actual paths. `PCC_PACKAGE_SITE` adds source-search locations for
development. Clean package acquisition, installation and native execution
remain separate qualification work; installation success is not general
NumPy or CPython-extension compatibility. See
[package environments](docs/developer-reference.md#install-application-packages-into-one-environment).

Two related projects live in separate upstream repositories:

- [pcc-gateway](https://github.com/allstoalls/pcc-gateway): an experimental
  HTTP/1.1 gateway and web framework using virtual threads and structured
  concurrency, with HTTP and dashboard examples.
- [pcc-gui](https://github.com/allstoalls/pcc-gui): an experimental declarative
  GUI framework with layout, controls, styling and macOS examples, including a
  diff viewer. Native windows use an AppKit/Metal bridge and require macOS on
  Apple silicon plus the Xcode toolchain.

Their application, platform and compiler-version gates are separate from this
repository's quick start. GUI OS/bridge dependencies are not a zero-system-library
claim. Follow each project's setup and current status before using its examples.

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
zero-libc or C-speed claim. See [current work and blockers](docs/status.md),
[revision-scoped verification results](docs/verification-status.md), and
[this repository's CI runs](https://github.com/ikshengmin/pcc/actions/workflows/pcc1-package-parity.yml).
Historical results and completed jobs apply to their recorded revision and
workload, not automatically to the latest source or every platform.

## Documentation

- [Project intent](docs/project-intent.md): goals and semantic commitments
- [Architecture](docs/architecture/01-overview.md): pipeline and source map
- [Developer reference](docs/developer-reference.md): modes, APIs and runtime controls
- [Compiler contracts](docs/compiler-contract.md): dependency and execution ownership
- [Runtime and GC](docs/developer-reference.md#garbage-collection),
  [virtual threads](docs/developer-reference.md#virtual-threads-effects-and-proof-checks),
  and [environment controls](docs/developer-reference.md#environment-controls)
- [GPU scope](docs/design/pcc-gpu-next-work.md) and
  [Kernel IR](docs/design/pcc-kernel-ir.md): experimental accelerator design
- [Contributing](AGENTS.md), [validation workflow](docs/validation-workflow.md),
  and [development tools](docs/development-tools.md): changing and checking pcc
- [Investigation index](docs/investigations/INDEX.md) and
  [knowledge index](docs/knowledge/README.md): dated experiments and corrections
- [Book](books/README.md): design and implementation in
  [English](books/en/SUMMARY.md) and [中文](books/cn/SUMMARY.md)

The older [Python tutorial](docs/python-tutorial.md),
[how-to](docs/python-howto.md), and [limitations](docs/python-limitations.md)
remain useful historical background. They include earlier LLVM/libpython
routes and retired scope statements; use the current source, this README and
the developer reference for today's defaults and qualification boundaries.

## License

[MIT](LICENSE)
