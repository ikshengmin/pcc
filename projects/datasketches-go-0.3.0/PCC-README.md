# Apache DataSketches Go 0.3.0: unchanged Go project

Part of the Apache DataSketches set pinned for PCC (scripts/datasketches_sources.py):
the same sketch library in C++, Rust, Go and Java plus its Python package, so
each PCC frontend compiles one algorithm suite and the results can be compared
for performance against each language's own toolchain.

`pcc-source-lock.json` pins the release tag archive (https://codeload.github.com/apache/datasketches-go/tar.gz/refs/tags/v0.3.0) at commit
`7c9421e3af8fadc6ae0ba805b508cefe9e57e738`: every member is unchanged and its Git blob ID was checked
against the commit tree (scripts/pin_datasketches.py reproduces the lock byte
for byte). Paths upstream marks export-ignore are listed, not fetched.
License: Apache-2.0 (LICENSE, NOTICE and the original source headers).

Dependencies are recorded in the lock and are not present: github.com/twmb/murmur3 v1.1.8, github.com/cespare/xxhash/v2 v2.3.0 and golang.org/x/exp/constraints at runtime, github.com/stretchr/testify v1.8.4 for tests, and the slices, iter and encoding/binary standard packages (absent from the pinned go1.27.3 closure).
No package manager, network fetch, generated source or substitute exists.
projects/.gitattributes keeps the upstream bytes (no text or whitespace rewriting).

`tests/test_datasketches_projects.py` verifies every byte and drives the work.

First observed blocker (local Mac): the 19 production packages, compiled with the pinned
go1.27.3 standard-library closure, stop at `common/item_sketch_double.go:33:58`, the
instantiated generic type `CompareFn[float64]` (`go.unsupported.generic-boundaries`).
