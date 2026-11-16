# Apache DataSketches Java 9.1.0: unchanged Java project

Part of the Apache DataSketches set pinned for PCC (scripts/datasketches_sources.py):
the same sketch library in C++, Rust, Go and Java plus its Python package, so
each PCC frontend compiles one algorithm suite and the results can be compared
for performance against each language's own toolchain.

`pcc-source-lock.json` pins the release tag archive (https://codeload.github.com/apache/datasketches-java/tar.gz/refs/tags/9.1.0) at commit
`d47c992b55c63d16c050711b3b9a0ac51b627031`: every member is unchanged and its Git blob ID was checked
against the commit tree (scripts/pin_datasketches.py reproduces the lock byte
for byte). Paths upstream marks export-ignore are listed, not fetched.
License: Apache-2.0 (LICENSE, NOTICE and the original source headers).

Dependencies are recorded in the lock and are not present: the JDK 25 API (java.lang.foreign memory segments and the java.* types imported), plus TestNG and slf4j-simple for tests.
No package manager, network fetch, generated source or substitute exists.
projects/.gitattributes keeps the upstream bytes (no text or whitespace rewriting).

`tests/test_datasketches_projects.py` verifies every byte and drives the work.

First observed blocker (local Mac): the 374 production units exceed the 256-unit input limit
of both the class model and the native source path, which report it as invalid input
(JavaError without a capability code) before parsing any unit.
