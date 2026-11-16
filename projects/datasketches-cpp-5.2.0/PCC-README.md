# Apache DataSketches C++ 5.2.0: unchanged C++ project

Part of the Apache DataSketches set pinned for PCC (scripts/datasketches_sources.py):
the same sketch library in C++, Rust, Go and Java plus its Python package, so
each PCC frontend compiles one algorithm suite and the results can be compared
for performance against each language's own toolchain.

`pcc-source-lock.json` pins the release tag archive (https://codeload.github.com/apache/datasketches-cpp/tar.gz/refs/tags/5.2.0) at commit
`de8553ba372e618382c2e7b44b0ffc9422b9458c`: every member is unchanged and its Git blob ID was checked
against the commit tree (scripts/pin_datasketches.py reproduces the lock byte
for byte). Paths upstream marks export-ignore are listed, not fetched.
License: Apache-2.0 (LICENSE, NOTICE and the original source headers).

Dependencies are recorded in the lock and are not present: the library is header-only C++11 over the standard library; its tests need Catch2 v2.13.9 (BSL-1.0) and CMake.
No package manager, network fetch, generated source or substitute exists.
projects/.gitattributes keeps the upstream bytes (no text or whitespace rewriting).

`tests/test_datasketches_projects.py` verifies every byte and drives the work.

First observed blocker (local Mac): a translation unit including any family header
(`hll.hpp`, `theta_sketch.hpp`, `kll_sketch.hpp`, `cpc_sketch.hpp`) stops in the owned
preprocessor at `common/include/common_defs.hpp:23`, `#include <cstdint>`; the owned C++
headers cover only the C-library shims, while the library also needs `<string>`, `<memory>`,
`<vector>`, `<iostream>`, `<random>`, `<chrono>` and `<thread>`. The missing header raises a
bare RuntimeError rather than a located diagnostic.
