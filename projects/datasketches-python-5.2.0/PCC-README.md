# Apache DataSketches Python 5.2.0: unchanged Python package

Part of the Apache DataSketches set pinned for PCC (scripts/datasketches_sources.py):
the same sketch library in C++, Rust, Go and Java plus its Python package, so
each PCC frontend compiles one algorithm suite and the results can be compared
for performance against each language's own toolchain.

`pcc-source-lock.json` pins the release tag archive (https://codeload.github.com/apache/datasketches-python/tar.gz/refs/tags/5.2.0) at commit
`d40921a3d294eceab2c25e5bca5eba40d1899b8a`: every member is unchanged and its Git blob ID was checked
against the commit tree (scripts/pin_datasketches.py reproduces the lock byte
for byte). Paths upstream marks export-ignore are listed, not fetched.
License: Apache-2.0 (LICENSE, NOTICE and the original source headers).

Dependencies are recorded in the lock and are not present: nanobind >= 1.6 (BSD-3-Clause), CMake and setuptools to build the `_datasketches` extension; the C++ core is projects/datasketches-cpp-5.2.0 (CMake GIT_TAG 5.2.0) and numpy is projects/numpy-2.4.4.
No package manager, network fetch, generated source or substitute exists.
projects/.gitattributes keeps the upstream bytes (no text or whitespace rewriting).

`tests/test_datasketches_projects.py` verifies every byte and drives the work.

First observed blocker (local Mac): the `_datasketches` extension unit `src/datasketches.cpp:20`
includes `<nanobind/nanobind.h>`, whose source is not pinned; every pure-Python module
(`__init__`, `PySerDe`, `TuplePolicy`, `KernelFunction`) imports that extension.
