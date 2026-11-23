# pcc

[![PyPI](https://img.shields.io/pypi/v/python-cc)](https://pypi.org/project/python-cc/)
[![Python](https://img.shields.io/badge/python-3.13%20%7C%203.14%20%7C%203.15-blue)](pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**A native compiler for Python and C, written in Python.**

pcc compiles Python and C source into native executables. Compiled Python
programs keep Python's syntax and behavior but run on pcc's own runtime and the
platform C library instead of libpython. Source frontends for C++, Rust, Java
and Go are in progress. pcc is experimental; see [Status and scope](#status-and-scope).

## What makes pcc different

- **Its own toolchain.** Python and C go through pcc's own optimizer, code
  generator, object writers and linkers, and the newer frontends emit the same
  PCC IR. The IR uses LLVM syntax, but no external LLVM is involved.
- **Python and C together.** The Python frontend covers type inference,
  classes, closures, generators and `async`. The C frontend covers C99-style
  code, pointers, aggregates, function pointers and multi-file builds.
- **C++, Rust, Java and Go, in progress.** Each frontend parses and checks its
  language itself and emits PCC IR. Each is driven by one unchanged upstream
  project and that project's own tests: TinyXML2, fallible-streaming-iterator,
  Commons Numbers Primes and Go's `math/bits`. None of them builds and passes
  those tests yet, and the Rust frontend still admits some features only after
  a check by an external `rustc`. See
  [multi-language status](docs/multilanguage-status.md).
- **Python semantics, opt-in value types.** Integers keep arbitrary precision
  and ordinary objects keep their identity. `@pcc.valueclass` marks immutable,
  identity-free types whose values can stay unboxed.
- **One garbage collector:** reference counting with a generational cycle
  collector, the model CPython uses. See [Garbage collection](#garbage-collection).
- **Native threads, no GIL.** Every native runtime is threaded, with atomic
  reference counts. `async`/`await` and virtual threads are supported too.
- **Interop.** Call compiled C from CPython with `import pcc`, or declare C
  functions for compiled Python with `pcc.extern`.
- **Self-hosting.** pcc builds itself into a native compiler; the self-host
  fixed point is not reached yet. See [What is pcc1?](#what-is-pcc1)

## Install

With Git and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/allstoalls/pcc.git
cd pcc
uv sync --locked
uv run pcc --help
```

pcc needs Python 3.13 or newer; uv uses the version pinned in
`.python-version`. If a command reports a locale or encoding error, see
[locale troubleshooting](docs/developer-reference.md#locale-troubleshooting).

A [PyPI release](https://pypi.org/project/python-cc/) is also published. It
can lag behind this source tree; see the
[installation notes](docs/developer-reference.md#pypi-and-native-installation-boundaries).

```bash
python -m pip install python-cc    # installs the `pcc` command
```

## Quick start

Run these from the source checkout on macOS arm64 or Linux x86_64.
`uv run pcc` and `uv run python -m pcc` are the same command.

### Compile Python

```bash
printf 'print("Hello, PCC!")\n' > hello.py
uv run pcc hello.py -o hello
./hello                # Hello, PCC!
```

The first compilation also builds pcc's runtime (about a minute on an
Apple-silicon Mac); later compilations reuse it.

Functions and type annotations use ordinary Python syntax. Save this as
`square.py`:

```python
def square(n: int) -> int:
    return n * n

print(square(7))
```

```bash
uv run pcc square.py -o square
./square               # 49
```

Without `-o`, pcc compiles and runs the program in one step. `--emit-llvm=PATH`
writes the IR instead of an executable. See
[command modes](docs/developer-reference.md#command-modes) and the
[Python frontend scope](docs/developer-reference.md#python-frontend).

### Compile C

Save this as `hello.c`:

```c
#include <stdio.h>

int add(int a, int b) { return a + b; }

int main(void) {
    printf("%d\n", add(20, 22));
    return 0;
}
```

```bash
uv run pcc hello.c -o hello_c
./hello_c              # 42
```

For multi-file programs, Makefile projects and libraries such as PostgreSQL's
`libpq`, see [Compiling C with pcc](docs/c-howto.md).

## Call C from Python

On macOS arm64, `pcc.module` compiles a C file into a shared library and loads
it into CPython. In the repository root, save `add.c`:

```c
int add(int a, int b) { return a + b; }
```

and `call_c.py`:

```python
import pcc

m = pcc.module("add.c")
print(m.add(3, 4))
```

```bash
uv run python call_c.py    # 7
```

`pcc.build` produces the library without loading it; see the
[API source](pcc/api.py). `pcc.inspect_artifact` (`pcc inspect`) reads a
compiled binary's format and dependencies, and `pcc.generate_bindings`
(`pcc bindgen`) turns C prototypes into `pcc.extern` declarations; see
[inspection and binding tools](docs/developer-reference.md#python-library-and-inspection-tools-in-progress).

## Garbage collection

pcc has one collector: reference counting with a generational cycle collector,
the model CPython uses. An object outside a reference cycle is freed when its
last reference goes. Newly tracked containers are young, and most automatic
cycle collections examine only the young ones. A full collection follows once
the tracked population has grown. It runs in bounded steps with the program
running in between, instead of one stop over the whole heap, and it ends by
returning the pages of empty object slabs to the system. An optional collector
thread, off by default, can run those steps while the program runs.

There is no collector selection. The tracing and relocating collectors that
earlier versions offered as backends `1` to `4` have been removed, and the
runtime no longer reads `PCC_GC_BACKEND`.

## What is pcc1?

The numbers count **compiler generations**, not Python versions:

- **pcc0** is the compiler's Python source running on CPython: the `pcc`
  command above.
- **pcc1** is the native compiler executable that pcc0 builds from that source.
- **pcc2** and **pcc3** are rebuilt from the same source by pcc1 and pcc2. When
  they agree byte for byte under the
  [bootstrap checks](docs/developer-reference.md#bootstrap), the compiler has
  reached a fixed point. pcc has not reached it yet; the Stage2 blockers are
  tracked in [allstoalls/pcc#200](https://github.com/allstoalls/pcc/issues/200).

Build pcc1 from the checkout (about 35 minutes on an Apple-silicon Mac); the
result is `build/bootstrap/pcc1`:

```bash
uv run python scripts/bootstrap.py --stage 1 --out-dir build/bootstrap
```

To install one shared `pcc1` for several projects on macOS arm64, see the
[toolchain guide](docs/developer-reference.md#one-installed-compiler-for-multiple-projects).

## Packages

pcc installs packages into its own environment. Check which one is selected:

```bash
uv run pcc env info --json
```

With `pcc1` on PATH, install a package with `pcc1 -m pip install PACKAGE`.
Install and compile in the same environment: `uv run` or an active virtual
environment can select a different one. NumPy and other CPython-extension
packages are not supported in general yet. See
[package environments](docs/developer-reference.md#install-application-packages-into-one-environment).

### Python package support

Package compatibility is experimental. There is **no verified overall package
support percentage**: installing or discovering a package is not evidence that
its imports or application workloads execute natively under pcc1.

Pure-Python packages still depend on the implemented Python and standard-library
semantics. General compatibility with CPython binary wheels and extension
packages must not be assumed.

The committed qualification catalog records these version-pinned targets:

| Package | Workload | Recorded qualification status |
|---|---|---|
| pproxy 1.9.5 | Native pure-Python service | Unverified |
| NumPy 2.4.4 | Scientific package | Unverified |
| simplejson 4.1.1 | Native extension | Unverified |

These are qualification targets, not a list of passing packages. The catalog
also contains non-package workloads, so its total entry count is not a package
support denominator. Historical NumPy 2.4.x experiments cover selected
import/version, construction, scalar-addition and element-access cases; they
do not qualify general NumPy compatibility on the current source.

Native support claims must identify the package version, platform, pcc1/runtime
build and workload actually executed. Parsing, host tests and CPython-fallback
results must not be counted as native pcc1 package support.

See the [qualification catalog](docs/compat/pcc1-cpython-replacement-workloads.json),
[claim contract](docs/compat/pcc1-cpython-replacement-matrix.json), and
[package limitations](docs/developer-reference.md#numpy-and-other-packages).

## Related projects

- [pcc-gateway](https://github.com/allstoalls/pcc-gateway): an experimental
  HTTP/1.1 gateway and web framework built on virtual threads.
- [pcc-gui](https://github.com/allstoalls/pcc-gui): an experimental declarative
  GUI framework; native windows need macOS on Apple silicon and Xcode.

## Experimental work

**GPU.** `@gpu.kernel` and `gpu.launch` compile annotated kernels through
Kernel IR into Metal shader source that pcc generates. On macOS with Apple
silicon, the compiled program hands that source to the system Metal framework
at run time, calling it directly through the Objective-C runtime, without
Xcode's offline Metal tools. Older routes that use Xcode's `metal`/`metallib`
or `xcrun clang` remain only as labeled reference routes. General Python GPU
acceleration is not supported. See the
[GPU scope](docs/design/pcc-gpu-next-work.md) and the
[local-model architecture](docs/design/pcc-local-model-architecture.md).

**Proof checking.** An Agda-class proof checker runs on host CPython; see the
[exploration and implementation state](docs/research/agda-class-proof-checker-exploration.md).

## Status and scope

The examples above run on macOS arm64 and Linux x86_64, except
[calling C from Python](#call-c-from-python), which needs macOS arm64. Python
and standard-library coverage, the C++, Rust, Java and Go frontends,
self-hosting, package compatibility and other platforms are still in progress.

See [current work and blockers](docs/status.md),
[verification results by revision](docs/verification-status.md), and the
[development repository's CI runs](https://github.com/ikshengmin/pcc/actions/workflows/pcc1-package-parity.yml).

## Documentation

- [Project intent](docs/project-intent.md): goals and semantic commitments
- [Architecture](docs/architecture/01-overview.md): pipeline and source map
- [Developer reference](docs/developer-reference.md): modes, APIs and runtime controls
- [Compiling C](docs/c-howto.md): files, directories, Makefile projects and libpq
- [Multi-language status](docs/multilanguage-status.md): the C++, Rust, Java
  and Go projects and their current first blockers
- [Compiler contracts](docs/compiler-contract.md): dependency and execution ownership
- [Runtime and GC](docs/developer-reference.md#garbage-collection),
  [virtual threads](docs/developer-reference.md#virtual-threads-effects-and-proof-checks),
  and [environment controls](docs/developer-reference.md#environment-controls)
- [GPU scope](docs/design/pcc-gpu-next-work.md) and
  [Kernel IR](docs/design/pcc-kernel-ir.md): experimental accelerator design
- [Contributing](AGENTS.md), the [work order](docs/status.md#work-order) that
  maintainers and agents take work from, the
  [validation workflow](docs/validation-workflow.md) and
  [development tools](docs/development-tools.md): changing and checking pcc
- [Investigation index](docs/investigations/INDEX.md) and
  [knowledge index](docs/knowledge/README.md): dated experiments and corrections

The older [Python tutorial](docs/python-tutorial.md),
[how-to](docs/python-howto.md) and [limitations](docs/python-limitations.md)
describe earlier routes and are kept as history.

## License

[MIT](LICENSE)
