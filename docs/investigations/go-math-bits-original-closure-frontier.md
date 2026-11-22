# Go math/bits: original test-entry and pinned testing frontier

Linux x86-64 host pcc0 source evidence at `36f9ab29`, not an upstream
test execution, native pcc1, or a dependency-complete selection.

The unchanged `test_owned_original_test_entry_and_actual_project_build_attempt`
ran under the 90 s / 1 GiB guard. All original normal production source
files lowered to IR, SHA-256
`aef9ef227413c799b17952f7ea420f5e025747fdd4ff82af53f5e101cfba315e`.
The original test-entry generator bound 26 tests, 36 examples and 43
benchmarks. The actual project compilation is **blocked**, not passed:
`_testmain.go:4:2` requires original `os` source and the target-specific
dependency closure is not supplied by this gate. It emitted no test IR or
test binary. A passing outer pytest asserts that exact frontier.

The independent existing
`test_math_bits_external_tests_with_the_pinned_closure_stop_inside_testing`
also passed. Its hash-verified Go 1.27.3 source union can reach
`testing/benchmark.go:95:2`: `go.unsupported.embedded-fields` for the
`common` embedded member of `B`. This source union is deliberately not a
Linux/amd64 build selection. It pins 667 files across 107 packages for
four Go build contexts and excludes compiler-owned runtime packages.
Do not substitute it for the actual project gate or fill imports with
stubs. Target file selection, compiler-owned dependencies and official
Go 1.27.3 reference availability remain independent obligations.

Next focused frontend slice: preserve the identity and direct selector of
a named value-embedded field, with native execution and Go reference.
Then address promotion and method sets explicitly; admitting a field
declaration alone does not prove `testing.B` semantics.


## Defined value embedding

The direct selector of an embedded defined value record reuses the
existing owned aggregate storage. The named-field and embedded-field
types remain distinct, and record copy is by value. The same original
program linked and ran in Linux pcc0 and with the exact Go 1.27.3
Linux-amd64 archive, whose SHA-256 matched the repository pin.
128 focused adjacent tests and one native/reference test passed.
Promoted selectors and other embedded storage forms remain explicit.

The pinned source union now advances to `testing/benchmark.go:116:8`
at `extra map[string]float64` (parser expected a name, found `map`).
This does not change the separate missing target-specific source
selection or qualify its original test entry.


## Map declaration syntax

Ordinary map type declarations now retain the key, element and source
location. The modeled invalid slice key remains a source error; a valid
map such as `map[string]float64` reaches `go.unsupported.map-storage`
before IR. The identical small original compiled and ran with the pinned
official Go 1.27.3 reference; no owned map execution is claimed. Ninety
adjacent tests passed. The pinned source union now stops at
`testing/benchmark.go:170:18` in `make(map[string]float64, 16)`.
The original math/bits test build remains blocked by the separate
target-specific `os` source closure.


## Explicit make map operand

The parser preserves `make(map[string]float64, 16)` as a type operand and
a size expression. Obvious noninteger, negative and surplus sizes remain
source errors. A valid call reaches the same explicit storage boundary
before IR. The field and make originals execute with official Go 1.27.3;
87 adjacent checks passed. The separate pinned source union now reaches
`testing/benchmark.go:238:2`, an unsupported `go` statement. No owned map
allocation or goroutine support is implied.

## Official Linux/amd64 dependency selection

At `9857d0d2`, the exact repository-pinned official Go 1.27.3 archive
(SHA-256 `65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40`)
ran metadata selection only:

```sh
env GOTOOLCHAIN=local GOENV=off GOWORK=off GOPROXY=off GOSUMDB=off \
    GO111MODULE=off CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOAMD64=v1 \
    GOFLAGS= GOEXPERIMENT= GODEBUG= GOCACHEPROG= GOMAXPROCS=2 \
    "$GOROOT/bin/go" list -deps -test -json math/bits
```

Use an explicit verified GOROOT and isolated GOCACHE, GOTMPDIR and GOPATH.
The existing `decode_graph`, `validate_graph`, and `reference_build_ids`
checks accept this capture. The portable build-instance digest equals
the checked-in pin
`220a909f4e633dcfdb0eb92c31d74ad4425bb87eb48538d9e600cfb45d804b54`:
133 instances, 91 test variants, 511 import-map edges. No original Go test
or test binary was run.

| Officially selected physical source | same locked bytes | absent from lock |
| --- | ---: | ---: |
| All selected sources (excluding generated test entry) | 567 | 345 |
| Missing under compiler-owned runtime/unsafe exclusions | | 283 (258 Go, 20 assembly, 5 headers) |
| Missing outside those exclusions | | 62 (32 Go, 29 assembly, 1 header) |

Zero present selected files differed; 104 files in the four-context union
were not selected. All 45 selected `os` Go files are already locked, as are
the five selected `fmt`, ten `testing` and one
`testing/internal/testdeps` Go files. The current `os` error means the
gate hasn't assembled the exact target graph; it doesn't mean that
`os` source is absent from the repository. Supplying the whole union would
add unrelated platform/experiment files without filling the real gaps.
The reference runner records this evidence in
`external_reference.dependency_graph.locked_source_gaps`, retaining the
missing paths and separating the compiler-owned and other roles. A
selected file with changed locked bytes is distinguished from a missing
file. This metadata does not change owned admission or qualify the gate.
The scoped Go reference/closure suite passed 56 checks, and the helper
reproduced the counts above from the official capture.

## Inputs outside the current runtime exclusion patterns

On the same pinned Linux/amd64 graph, these 62 physical files are owned
by 24 package paths. The four-context lock has no package entry for nine
of them. Go's selected roles and importing package instances, not file
name guesses, separate the missing inputs:

| Number | Selected role and location | Boundary |
| ---: | --- | --- |
| 22 | `GoFiles` in `compress/flate` (15), `compress/gzip` (2), `hash/crc32` (4), `text/tabwriter` (1) | Original Go sources on the `testing/internal/testdeps → runtime/pprof` branch. Pin separately; CRC's selected assembly remains open. |
| 10 | `GoFiles` in `internal/chacha8rand` (2), `internal/coverage/rtcov` (1), `internal/goos` (3), `internal/profilerecord` (1), `internal/trace/tracev2` (3) | Only imported by official `runtime`, `runtime/pprof`, `runtime/trace` or `internal/runtime/sys` in this graph. Reconcile with the owned runtime before granting admission. |
| 29 | `SFiles`: 27 in packages already present; one each in `hash/crc32` and `internal/chacha8rand` | Selected Go assembler inputs; a Go file pin cannot handle their ABI or replace their symbols. |
| 1 | `HFiles`: `crypto/internal/boring/goboringcrypto.h` | Header listed in the reference package; the row has no selected Cgo or C sources. This by itself is not evidence of a cgo dependency. |

No selected nonexcluded C source, cgo source, object or embedded asset was
missing. Do not expand the runtime exclusion rules from this audit alone,
compile the union as one file set, or treat the source-only pin as a complete
build closure.

The separate `pcc-linux-amd64-profiling-source-pin.json` manifest records
the 22 original source identities (200,263 bytes in total) from the
verified official Linux binary distribution's GOROOT. The existing
reference runner verifies them against the exact portable selection
identity before running official tests. Their bytes are not vendored by
this change, so the existing fixture-gap count and owned gate remain
unchanged. Scoped verification: 57 checks passed, and all 22 hashes
matched the official selection capture. No project tests were run.

## Goroutine call syntax

The frontend now preserves unparenthesized calls in `go` statements;
malformed and invalid calls are source errors, and calls the checker
can otherwise handle stop before IR at `go.unsupported.goroutines`. A
function literal retains its independent function-value boundary.
The unchanged pinned diagnostic union now enters the nested function
at `testing/benchmark.go:238:2` and stops at its channel send,
`b.signal <- true` at `242:13`. The separate original project still
fails target dependency admission; no goroutine, channel or upstream
test execution by PCC is claimed. The scoped suite passed 266 checks.
Official Go 1.27.3 built and ran the four small valid originals and
rejected the five invalid originals; only the reference executed them.
