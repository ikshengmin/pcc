# Go and Java as Python native-extension languages

**Status: planned product capability and acceptance targets, not an implementation claim.**

## Product goal

Enable developers to write Python libraries whose functional core is implemented in
Go or Java, comparable in role to C/C++ in NumPy and Rust in orjson.

The intended PCC path is:

```text
Go or Java source
  -> PCC frontend
  -> PCC IR
  -> PCC-owned native code and supported runtime
  -> native Python binding/package
  -> Python import and callable library API
```

Creating reusable bindings and finding suitable existing packages are
complementary tracks. The goal is not contingent on discovering an existing
package with exactly the desired integration model. It also extends beyond
getting one selected package to run: the result should support developing new
Python libraries in Go and Java.

This document does not establish that these capabilities already work, promise
unmodified compatibility, or claim any package build, import, test, or GC result.

## Package targets and their roles

| Language | Target | Role and scope |
| --- | --- | --- |
| C/C++ | NumPy | Selected real-package acceptance target. Its build requires native compilers and dependencies; successful compilation of a sample is not a NumPy package pass. |
| Rust | orjson | Selected real-package acceptance target. Track its actual Python FFI/build dependencies separately from the whole PyO3 ecosystem. |
| Go | go-enry / its Python package | Recommended research candidate, not a package-specific selection attributed to the user. It already exposes useful Go language-detection functionality to Python using a C shared library and cffi. |
| Java | ZXing core plus a new PCC Python binding | Recommended staged acceptance candidate. Start with the bounded Reed-Solomon source subset, then expand to a QR decoding/API subset. Do not describe the new binding as unmodified pyzxing compatibility. |
| Java + Rust | extractous / Tika native | Later, larger integration candidate. Its existing native-extension shape is useful evidence, but its GraalVM/Tika dependency scope makes it unsuitable as the first Java-frontend acceptance target. |

Primary sources:

- [NumPy source build requirements](https://numpy.org/doc/stable/building/).
- [orjson build dependencies](https://github.com/ijl/orjson/blob/master/Cargo.toml)
  and [PyO3's description of direct `pyo3-ffi` usage](https://github.com/PyO3/pyo3/blob/main/pyo3-ffi/README.md).
- [go-enry library and Python binding documentation](https://github.com/go-enry/go-enry).
- [ZXing core build/dependency manifest](https://github.com/zxing/zxing/blob/master/core/pom.xml),
  [Reed-Solomon source subset](https://github.com/zxing/zxing/tree/master/core/src/main/java/com/google/zxing/common/reedsolomon),
  and [upstream Reed-Solomon tests](https://raw.githubusercontent.com/zxing/zxing/master/core/src/test/java/com/google/zxing/common/reedsolomon/ReedSolomonTestCase.java).
- [pyzxing's existing Java Runner integration](https://github.com/ChenjieXu/pyzxing).
- [extractous Python native binding](https://raw.githubusercontent.com/yobix-ai/extractous/main/bindings/extractous-python/Cargo.toml),
  [native-library build/link path](https://raw.githubusercontent.com/yobix-ai/extractous/main/extractous-core/build.rs),
  and [Tika native build configuration](https://github.com/yobix-ai/extractous/blob/main/extractous-core/tika-native/build.gradle).

These are source-discovery links, not immutable acceptance inputs. Select and
record exact upstream revisions, licenses, dependency scope, platforms, and Python
versions before an implementation or compatibility run.

## Java acceptance boundary

The production target is Java **source -> PCC IR -> native execution**, not a
bytecode frontend or a JVM-backed substitute for compiling the tested Java core.
An external JVM may run the upstream reference implementation or serve an
explicitly separate bridge test; that result does not validate PCC's Java frontend.

For ZXing, the first proposed scope is its five-file Reed-Solomon package and the
supporting semantics it actually needs. This is meaningful library code, not a
claim that all of ZXing or pyzxing is supported. Even this subset includes objects,
arrays, exceptions, and library operations. Its native binding needs an explicit
array/error/ownership contract; feasibility remains to be implemented and tested.

After that subset is established, expand to QR decoding and a documented Python
API subset. Compare behavior with a pinned upstream reference. Replacing
pyzxing's Java Runner with a new PCC binding is an adaptation, not evidence that
the original package works without changes.

extractous demonstrates an existing Python -> Rust native binding ->
GraalVM-produced Tika shared-library path. Loading that existing library only
establishes foreign-library interoperability. It does not show that PCC compiled
the Java implementation, nor that a PCC library is a drop-in replacement for its
GraalVM-specific interface.

## What bridge compatibility does and does not prove

- [JPype](https://jpype.readthedocs.io/en/stable/) and
  [PyJNIus](https://github.com/kivy/pyjnius) access Java through an existing JVM/JNI
  integration; [jpy](https://github.com/jpy-consortium/jpy) is a bidirectional bridge.
- [Jep](https://github.com/ninia/jep) primarily embeds CPython in Java through JNI.
- [Py4J](https://www.py4j.org/faq.html) uses TCP communication with Java.
- Running any of these bridges can test a boundary or provide a reference. It is
  not, by itself, Java source-frontend acceptance.
- `pyo3-ffi` is the low-level Python FFI layer. An orjson-oriented acceptance result
  using it must not be reported as full PyO3 support, including the high-level API,
  macros, and broader ecosystem.

## Independent acceptance gates

Each gate needs its own result and scope. A later gate must not be inferred from
an earlier one, and a reference-toolchain result is not a PCC result.

| Gate | Evidence required before claiming it |
| --- | --- |
| Source/frontend | The scoped Go or Java implementation is compiled from source through PCC IR, with its required language and library semantics identified. |
| Native artifact | PCC-produced native code links with the explicitly supported runtime and binding dependencies; no substitute upstream-compiled implementation is credited as frontend coverage. |
| Package build | A usable Python extension/package is produced by the documented build path for the declared platform and Python ABI. |
| Import/API | That produced package imports in a clean process and executes the claimed public API with correct outputs and errors. |
| Upstream behavior | Applicable pinned upstream tests and differential cases pass; adapted tests, exclusions, and unsupported APIs remain explicit. A subset pass is not a full-suite pass. |
| Five-GC coverage | The claimed extension operations and lifecycle behavior are checked across all five project GC configurations. Results remain per configuration; one passing configuration is not five-GC acceptance. |

PCC's runtime and language boundaries should be integrated without erasing the
observable semantics of Go, Java, Python, or the other source languages. Binding
contracts must make data conversion, ownership, errors, and lifetime behavior
explicit. A shared runtime direction does not imply that different languages'
semantics are interchangeable.

## Proposed progression

1. Define a small reusable native-binding contract and the source-to-artifact
   evidence needed for new Go/Java Python libraries.
2. Use bounded real code: a researched go-enry API slice and ZXing Reed-Solomon,
   with exact source/dependency scope recorded separately.
3. Establish native package build, clean import, and callable API behavior; then
   expand upstream-test and five-GC coverage without collapsing these gates.
4. Grow the reusable bindings and real-package coverage together. Consider QR
   APIs and later extractous/Tika only as their dependencies and semantics become
   supported.

This is a target definition. It schedules no build, changes no implementation
status, and supplies no test pass or completion estimate.
