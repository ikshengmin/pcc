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

The send-statement slice retains the target and value as separate
expressions in the nested `b.signal <- true` at `242:13`; resolved
scalar targets stay invalid. The pinned union now reaches the next
original line of interest, `<-b.signal` at `247:2`. The scoped suite
passed 202 checks. Official Go executed a nested send/receive control and
rejected the scalar send. PCC still does not execute channels or the
upstream test program.

Unary receive now preserves the original `<-b.signal` at `247:2`;
assignment and comma-ok retain the receive expression without claiming
receive semantics. The diagnostic union's next stop is `make(chan bool)`
at `754:20`. The scoped suite passed 206 checks. Official Go executed a
receive control exercising discard, comma-ok and unary precedence, and
rejected the three scalar receive forms. No upstream test was run.

The channel operand now parses at `benchmark.go:754:20`. Channel
directions remain distinct from receiving a converted channel value,
and explicit `make(chan T, n)` checks the buffer size before the owned
storage boundary. The diagnostic union now stops at the original
`select` in `testing/testing.go:2747:4`. The focused suite passed 225
checks; official Linux/amd64 Go ran the directional channel control and
rejected all five invalid arguments. PCC never created a channel.

Select now preserves the original communication and default clauses at
`testing/testing.go:2747:4`. Scheduling and readiness still reject
before IR. The first gap in this diagnostic union is now
`context/context.go:109:9` (`Done() <-chan struct{}`). Scoped evidence:
257 focused checks and 15 unchanged owned Linux ELF branch controls;
official Go ran a select with send, receive, default and labeled break
and rejected six invalid cases. No owned select was executed.

Channel signatures now keep the original `Done() <-chan struct{}`
interface result and the concrete method's identical result. Named
parameters with a channel direction are preserved too. Channel ABI
still rejects before IR. The diagnostic union first stops at
`context/context.go:752:17`, its type switch. The scoped suite passed
180 checks and the interface/channel reference control ran with
official Linux/amd64 Go.

The original type switch in `context/context.go:752:17` now retains
the initializer, guard binding and case types without evaluating dynamic
dispatch. The pinned union's first stop is now import admission in
`errors/join.go:8:2` (`unsafe`), not a parser diagnostic. That original
uses `unsafe.String(&b[0], len(b))` on appended byte storage; suppressing
the slot or import check would not supply the required owned lifetime.
Scoped checks: 254 focused and 15 unchanged Linux PCC branch
executions; official Go executed the type-switch control and rejected
seven invalid forms. Whole-project owned target assembly remains open.

The existing shared managed-object plan now separates a logical
`[]byte` view (offset, length, capacity) from the owner of repeated
one-byte payloads. A managed string span can describe that same base
owner, not a raw interior pointer. Allocation, root registration,
mutation/alias proof, safepoint reload and physical offsets are still
unresolved. `[]error` is intentionally not treated as scalar backing:
Join also needs a separate traced interface-slice plan and normal slice
operations. The original named `unsafe` import still rejects before IR.
The focused suite passed 81 checks; an unchanged static-string PCC ELF
control passed. The pinned `errors/join.go` matches the official
GOROOT byte-for-byte, and an official caller passed nil/single checks and
kept the returned multi-error bytes across a collection after growth.

The repository already has a strict `LockedGoSourcePlan` for normal
target packages (used by the Go Enry probe). Its schema has one row per
import path, while the reference test graph has build instances. Next
verify their normal roots separately rather than flattening test
variants or substituting upstream objects.

## Exact normal-package source plan

```sh
python scripts/pin_go_enry_linux_stdlib.py go1.27.3.linux-amd64.tar.gz normal-plan.json \
  --root os --root fmt --root testing --root testing/internal/testdeps
```

This writes a byte-identical copy of the checked-in
`pcc-linux-amd64-math-bits-normal-plan.json`. The original default
Go Enry recipe also regenerates byte-for-byte. The existing source plan
loader verified 130 packages and 914 physical originals (7,880,883
bytes including support headers/license/version). The recipe has 908
normal package files: 853 Go, 49 assembly and six headers. All 908
matched the package-test selection, whose four additional files are the
original `math/bits` internal/external test sources; its generated main
and build variants are not folded into this plan.

The verified plan still reports unresolved owners: 25 package assembly
owners, 15 runtime/intrinsic owners and five generated layout headers
(categories overlap). It does not load code from the external toolchain
into the owned compiler and does not run the upstream tests. Next attach
it as explicit source-only input to the owned project path, preserving
the separate test variants and native owner checks.

The owned project preflight now verifies that exact snapshot using
`PCC_GO_MATH_BITS_SOURCE_ROOT`, independently of the reference tool
settings. It compares the normal `math/bits` bytes against the original
project lock and does not treat the internally augmented test package
as a replacement for a normal locked package. A missing or altered
explicit source root fails closed. Linux validation copied only the 914
locked files into a tree without `bin` or `pkg/tool`: source status
`SOURCES_VERIFIED`, production frontend and generated-entry checks passed,
test build `BLOCKED`, test execution `UNRUN`. The scoped suite passed
88 checks and the CI environment wiring test passed. GitHub Actions
was not run for this change.

The same source-only path now emits `owned-test-source-bindings.json`
using the existing pinned build-instance IDs and import maps. It binds
133 instances and 912 distinct original package files, with 511 remapped
imports. The ordinary and internally augmented `math/bits` instances
remain distinct; the external tests bind the latter, and generated main
binds the recompiled `reflect`. Only the locally generated main bytes
enter its own identity, not an upstream Go cache copy. The real isolated
source tree produced digest
`6f30c67047b6c7a48f3b8a575c95d161f9dcd71aeb39ce77987ffad507a52e2a`.
All 89 scoped checks passed. This binds source identities, not compiler
admission, the native ABI, or actual execution.

The owned parser now reads the locally generated test main and follows
its first declared import through that exact instance map. It keeps
the compiler-added `reflect` edge distinct from declared imports and
rechecks source hashes before parsing. On the isolated source-only
tree it reached `os [math/bits.test]` and stopped at the unchanged
`src/os/file.go:201:2`, the embedded `*File` in
`fileWithoutReadFrom`. This is the real first bound package parse
blocker, separate from the `errors.Join` diagnostic union. Ninety
scoped checks passed. No native assembly, runtime owner or project
test was admitted.

Embedded pointer fields now preserve their name, type and tag, including
qualified instantiated types, while rejecting storage and promoted
receivers before IR. The exact original `*File` at `os/file.go:201:2`
parses; the same source-only run now stops at `os/file_unix.go:229:49`,
the bodyless declaration of the runtime-owned `sigpipe`. Scoped
evidence: 116 checks, an unchanged named-value native differential,
and official Go's valid embedded-pointer and invalid interface/pointer
cases. No Go pointer-bearing record was executed by PCC.

Bodyless declarations now preserve their signature separately from a
real empty definition and reject before IR without a native owner.
The exact bound `os` source parses past `func sigpipe()` and stops at
the qualified embedding `sync.Mutex` in `os/getwd.go:14:2`. The scoped
suite passed 208 checks. Official Go linked and ran the same form with
an explicit assembly body and rejected a bodyless generic function.
PCC did not invoke either the assembly implementation or a host shim.

Qualified value embedding now keeps the local field name and imported
nominal owner. The unchanged `sync.Mutex` embedding parses and the
source-only run parsed all 45 selected files of `os [math/bits.test]`
with 20 declared imports bound to their exact IDs. The focused suite
passed 119 checks; two owned Linux ELF differentials matched official
Go for local and cross-package value-record copy. The latter used a
small original value record, not the standard mutex. The original
project still has no admitted lock, assembly ABI, or native test binary.

The original runner now feeds bound imports to the same owned semantic
package walker without coalescing normal and test instances. The walker
keeps separate symbols for a control's two versions of one import path,
rejects missing bindings, and never supplies original runtime sources as
an owned implementation. The selected real graph now follows the
`os` sources and first stops at `internal/bytealg/bytealg.go:37:16`
(`string | []byte`). All 157 scoped checks and an unchanged Linux PCC
ELF/reference source-plan control passed. This semantic probe never
lowers the test graph or consumes external objects; runtime/assembly
owners and compiler-added non-source edges are not qualified.

Compound generic constraints now retain their validated token sequence
and defer rejection to semantic constraint resolution; named
`any`/`comparable` behavior is unchanged. The exact test-instance source
walk now stops at the selected normal `internal/cpu/cpu.go:12:27`,
its `_ [CacheLinePadSize]byte` padding field. It has not executed either
the byte or string branch of `hashStr` with PCC. The focused suite passed
249 checks and the selected existing Go generic integration subset
passed 97 checks; an official Go reference ran both byte and string controls.

Nonempty blank `[N]byte` fields now occupy their true physical slots.
Positional operands still evaluate once in source order; blank fields
are neither selectable nor keyed and are skipped by equality. The
selected `CacheLinePad` declaration is past this boundary. The exact
source-instance walk now stops at `internal/cpu/cpu.go:212:2`, the
`*bool` field in `option`. Scoped validation: 137 checks and 22
integration/native checks, including same-input Linux PCC and official
Go for the byte padding and expression ordering. No pointer-bearing
`option` or upstream test executed.

The Linux/amd64/v1 original `cpu_x86.go` is unchanged and byte-matched
to the selected plan. AST verification finds 23 distinct
`Feature: &X86.Has…` initializers in `doinit`, each pointing into package
storage, not a local frame or a movable allocation. The options still
live in a global slice created/appended in `doinit`.
`processOptions` ranges over copies and conditionally reads/writes
the pointee; `Initialize` invokes `doinit` and then that loop. Direct
address/call/alias behavior for a static bool field already executes
with PCC and matches official Go (one native comparison, two scoped
checks). It does not justify accepting arbitrary aggregate pointers,
global slice storage, the initialization sequence, or the bodyless
`cpuid`/`xgetbv`/`getGOAMD64level` symbols.

The pointer checker now has the explicit direct scalar-field proof:
the field path must end at an ordinary package variable, not a local,
dereferenced receiver, indexed place or runtime linkname. Its fact
retains the static base owner; all other places keep their existing
analysis. The new proof is not authorization for aggregate storage
or alias transport. Scoped validation: 189 checks and 24 selected
pointer integration checks. The selected `option` declaration still
rejects pending the distinction between metadata and storage uses.

A boolean-pointer member now survives as declaration metadata only.
Local/global values, composite temporaries, parameter and result
carriers with that member still reject before IR. The exact bound
package now reaches `internal/cpu/cpu.go:178:1`: one-argument
`//go:linkname X86`, a local symbol exported for assembly consumers.
It is not the two-argument runtime-object import currently supported
in the restricted integer panic path. The scoped suite passed 237
checks and the direct static-field native differential still passed.
No pointer-bearing record or CPU feature table executed.

The exact source-instance analysis now distinguishes one-argument
local linkname declarations from two-argument imports. It retains
source owner metadata only, requires the real declaration and
file-local unsafe import, and normal compilation still rejects
unbound exports. The bound walk clears the eight CPU local export
directives and stops at the selected bodyless `cpuid` declaration
(`internal/cpu/cpu_x86.go:12:6`). The scoped suite passed 132 checks,
the existing runtime-error ABI subset passed ten, and an official
one-argument local-owner control ran. No PCC object export or upstream
assembly has been admitted.

The selected original `cpu_x86.s` defines `cpuid` with two uint32
inputs at Go ABI0 offsets 0 and 4 and four outputs at offsets 8,
12, 16, and 20. The owned x86 encoder now implements the no-operand
CPUID byte sequence; it rejects explicit operands. A same-input ELF
control compared all four leaf-zero results against a system-assembled
reference and verified that its wrapper preserves caller RBX. The
owned build ran with subprocess compilation forbidden. The adjacent
encoder/cache suite passed 244 checks and this native control passed.
This does not interpret the Go assembly frame or supply the bodyless
Go declaration, XGETBV or the build-level routine. Next establish the
actual PCC tuple result transport before binding any original symbol.

The exact two-`uint32` argument, four-`uint32` result shape now has
an owned native register observation. The two input words enter via
EDI/ESI. PCC returns the first two words in RAX and the last two in
RDX, each low word first. A separate owned-assembled caller read
all 16 result bytes including high-bit values, and the complete unchanged
Go control also matched official Go. Thirty-three focused result checks
and 22 integration checks passed. This establishes the adapter-side ABI,
not the source-to-body binding for the original CPUID.
