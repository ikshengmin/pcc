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

The isolated owned CPUID adapter is pinned to both unchanged inputs
(`cpu_x86.go` declarations and `cpu_x86.s`) and to Linux/amd64. It
translates ABI0's four uint32 slots into PCC's two register pairs,
preserving RBX. A public-IR caller linked the owned object and produced
the same 32 bytes as official Go assembling the unchanged original
`cpu_x86.s` for leaf/subleaf (0,0) and (7,1). Inserts, instruction
changes and a wrong target reject. Fifty-nine scoped checks and the
native differential passed. This is not an automatic Go linker
provider and does not admit `xgetbv`, `getGOAMD64level` or the
whole-package tests.

Exact source-instance analysis now verifies the selected owned CPUID
object, target, symbol and typed signature. A mismatched declaration
and another bodyless function still fail closed. The real source-only
report verified object SHA-256
`9622ab5281faa5b1f2536bc2e7d3e8b0b295371f9219b656d21a677a873d2bc4`
and advanced to the separate `xgetbv` at `cpu_x86.go:15:6`.
Validation: 156 scoped checks and the unchanged original CPUID assembly
differential. The project graph still emits no IR or test binary; the
CPUID object's link was only the isolated control.

The owned x86 encoder now accepts no-operand XGETBV and rejects explicit
operands. Linux owned ELF and the system assembler matched on a probe
which tested CPUID leaf 1's OSXSAVE bit before reading XCR0. On this
host the guarded instruction executed; a separate forced-disabled
arm confirmed the guard bypasses the instruction and returns the zero
receipt. All 248 adjacent checks and three native CPUID/XGETBV controls
passed. The original `doinit` guard is
`if X86.HasOSXSAVE { eax, _ := xgetbv() … }`; the project still has
no XGETBV symbol owner binding.

The exact Linux XGETBV adapter is now built from the locked source pair
and bound as a two-`uint32` result. Its generated body independently
tests CPUID OSXSAVE before the instruction; an invalid direct call
cannot read XCR0 without that feature. The original guarded Go assembly
and owned object produced the same 16 bytes (including the executed
branch marker) on Linux. The original source guard cannot be edited
under this owner. The real graph advances to `getGOAMD64level`,
`cpu_x86.go:18:6`. Seventy-one scoped checks and two raw native
assembly comparisons passed, without linking the original package.

The immutable source plan now retains its checked GOAMD64 selection.
The third native CPU symbol uses the pinned v1 preprocessor branch
and rejects a different requested setting; it does not query the host.
Its four native output bytes match official Go assembling the same
original under GOAMD64=v1. Sixty-two scoped and three original-assembly
native comparisons passed. The real graph verifies all three objects
and now stops in `appendBytes(b []byte, args ...uint32) []byte`
(`cpu_x86.go:272:20`). `Name` reserves 48 bytes, performs three
16-byte appends and returns a string conversion; slice aliasing,
capacity, return lifetime and string ownership remain separate from
the assembly objects.

The checked slice constructor now validates size, arity and constant
length/capacity consistency. The unchanged `Name` expression at
`cpu_x86.go:246` supplies an extent of (0,48); dynamic dimensions
do not supply a static extent. The scalar backing plan stays
unresolved and does not establish a call lifetime, alias proof or
allocation. The exact full graph still rejects `appendBytes`'s
slice carriers, so the next step is an independent call-growth and
escape proof. All 178 adjacent checks and the owned static-string
control passed; official Go executed the same constructor shapes
and rejected four invalid forms.

The selected package's byte-append source proof recognizes the closed
uint32 loop and its four byte shifts (0, 8, 16, 24), including the
returned input-alias contract. A different result, escaped slice or
shadowed builtin does not qualify. With the checked (0,48) input,
the original call counts produce extents 16, 32 and 48 on the same
backing; an additional word is rejected by this in-place proof. The
unchanged original helper, run in an official Go control, confirmed
all three extents and the backing address. Forty-eight scoped checks
and the unchanged PCC variadic native differential passed. Native
byte writes and the carrying slice ABI are not introduced by this proof.

The independent owned lowering now materializes one zeroed fixed
entry-stack array and writes each checked byte offset. It rejects
overflowing extent, wrong word width and use from another function
before emitting stores. The same backing remains through each append.
A public-IR/native ELF control using dynamic uint32 arguments matched
48 bytes from official Go executing the unchanged original helper.
Forty-one adjacent checks passed. This validates an isolated store
primitive, not yet the original caller's source lifetime, a general
slice carrier, or the returned string's ownership.

The exact selected CPU caller now has a conservative source-shape
proof: one fixed make, three straight-line in-capacity appends,
two local reslices, scalar reads, and one required copied string
result. Address taking, aliases, unknown calls, shadowing, backedges
and growth after a view cannot use this proof. The copied-string
requirement does not grant managed allocation. Fifty-eight scoped
and two adjacent native differential checks passed. The original
caller is not yet lowered as a whole.

Owned byte subviews now carry the same stack owner plus offset and
length. Checked subviews cannot grow into uninitialized capacity;
unsigned range checks also reject negative bounds, and the scalar load
is emitted only on the success edge. A failure block in a different
function rejects before emission. Seven dynamic native cases matched
the explicit checked Go control (including reversed, negative and
end bounds); 47 scoped checks and two native differentials passed.
This does not supply general slicing, a Go panic owner, or an escaping
string value.

The owned prefix scan now works on checked byte views with either
comparison polarity. Removing leading spaces and stopping at NUL in
the original two loops was compared against official Go on five
cases (spaces, NUL, absent delimiter, high bytes and empty result).
The native observation folds the remaining bytes and length to a
scalar; it does not return a pointer or claim a string copy.
Twenty-five scoped and three native differential checks passed.

The owned copy primitive now requires a different preallocated backing
and checks capacity before the first store. In the native differential,
overwriting the source after copying leaves the destination unchanged;
an undersized destination remains zero, and copying to the source
rejects before emission. Twenty-six checks and four native differentials
passed. This uses an isolated function-local destination, not an
escaping Go string or heap-backed slice. The counted copy contract is
available for a future owned runtime allocation.

The exact source-only package path now binds the closed byte-append
helper's signature and proof. A normal compile or an unproved caller
still rejects it; no executable descriptor or IR is supplied. The
real selected graph advances to the allocating `DataCacheSizes() []uintptr`
at `internal/cpu/datacache_x86.go:15:23`; that result genuinely escapes,
so the fixed local byte plan cannot apply. The scoped suite passed
146 checks and five native byte/variadic differentials.

The escaping scalar slice now has a source-only plan: a rooted owner,
offset, length and capacity; checked pointer-free uintptr payload extent;
and explicit nil versus allocated-empty and return ownership requirements.
This does not choose a descriptor, register a runtime object or lower a
return. The original x86 cache-size function and CPUID assembly built
unchanged in the pinned Go toolchain. On this Linux host the harness
observed three sizes and non-nil backing after a collection; that result
is host-specific, not a fixed output requirement. The Intel path appends
in a CPUID-dependent loop. Eighty-four focused checks and one independent
native static-string differential passed. Next: qualify the real runtime
backing lifetime, then growth and ownership transfer; do not reuse the
function-local byte stack array.

The existing opt-in shared managed ABI has a native scalar backing
lifetime control on Linux x86-64 GC0. An owned C producer allocates
pointer-free repeated words, hands the caller a different owning root
cell and drops its original handle. The caller reads the full-width
words after an actual collection, observes allocated-empty distinct
from nil, then releases the final owner and observes reclamation.
Overflow and attempts to read a reference slot leave the output
unchanged. Both this control and the existing graph fixture passed
with a current-codegen owned archive; the same binaries explicitly
rejected other collectors. No runtime code or Go semantics changed.
Next bind the checked Go scalar layout to this optional descriptor
and call boundary without opening the default link path.

The optional Go scalar emitter now derives the real v1 descriptor from
the checked payload map and calls the existing allocator via its exact
descriptor/count/handle-output ABI. No new allocator or default entry
path is added. The C observer exercised the Go-emitted object through
the unchanged production archive: allocated-empty, zero-initialized
words, full-width values through collection, overflow, and owner release.
The Go adapter and two independent native runtime controls passed;
23 adjacent checks passed. GC1–4 remain explicit rejections. The next
source shape starts at nil and appends one uintptr, as used by both
cache-size producers; storage growth and source-result ownership still
need dedicated proofs.

The opt-in first append from nil now allocates one scalar word into a
temporary result slot, checks status, initializes the element, and only
then publishes its new owning handle to the caller. Native x86-64 GC0
kept the single root live after the producer frame returned and through
collection, then dropped it without residue. The three observed full-width
words matched the unchanged Go fixture under Go 1.27.3, including zero
and two high-bit values. Null output and unsupported collectors leave no
new owner; unsupported backend status leaves caller output unchanged.
Twenty-four adjacent and four native checks passed. This exercises the
optional ABI, not Go source lowering of the fixture by PCC, growth of a
non-nil slice, or the full original cache-size function.

An explicit owned entry now parses and proves an isolated one-function
Go producer containing an implicit nil scalar-slice declaration followed
by one builtin append and return. It rejects other state, additional
declarations, shadowing, aliases and unproved bodies before IR. The exact
producer file was unchanged between PCC and official Go. The native
Linux x86-64 GC0 program and Go 1.27.3 emitted the same three words
after collection; the driver is still a foreign status/handle observer.
Thirty-five adjacent and four native checks passed. This is not a native
Go result ABI, general package admission, or the actual CPU producer,
which also needs growth from non-nil and loop-carried ownership.

A full zero-offset scalar backing can now grow through the optional
Linux x86-64 GC0 ABI. It validates the descriptor, checks extent, then
allocates before reloading the old payload and copying the occupied
elements. It publishes only the new handle after append; the old
handle and its aliases still own their original backing. The two native
views remained independent after real collection and cross-mutation.
Five raw words matched the unchanged Go 1.27.3 growth control. The
bounded native process also observed real allocation failure leaving
its old owner and output untouched. Thirty-six adjacent and five native
checks passed. Geometric capacity may differ from Go's implementation;
only the full base view, logical contents and ownership are qualified.

A zero-offset scalar slice with spare capacity now writes its next
element directly through the existing owner. An explicit unsigned
length bound rejects full, out-of-range and negative-as-word indexes
before data access, and an incompatible live descriptor is rejected.
No allocation, root registration, or replacement occurs on this
path. Two native GC0 handles saw the same mutation after collection;
the surviving alias continued to append after the original handle
was dropped. The raw words matched Go 1.27.3. Thirty-seven adjacent
and six native checks passed. The caller's logical length still needs
to flow through nil/in-capacity/full branches in one owning state.

The optional owned handle-and-length transition now selects first
allocation, in-capacity mutation, or growth. Successful replacement
publishes both slots before releasing the prior handle; failures leave
both slots unchanged. The native x86-64 GC0 loop appended nine values
with collection between appends and retained the original alias while
the current owner grew. Roots returned to baseline. A real constrained
allocation failure preserved handle, length and contents. The nine
words matched Go 1.27.3. Thirty-eight adjacent and seven native checks
passed. This control drives the helper from a foreign observer; proving
and lowering the original Go loop remains a separate source step.

The explicit opt-in entry now also lowers a proved zero-origin uint32
loop from unchanged Go source. The checked lexical owner is the only
slice local, its only write is the builtin append, and its only escape
is the result. The native Linux x86-64 GC0 observer consumed a nil
result at zero and live length-bearing handles for one and ten values;
after collection all fourteen words matched Go 1.27.3. The entry
drops any owned prefix on failure before publishing outputs. Forty-six
adjacent and eight native checks passed. The isolated source shape
does not yet prove the conditional/loop paths or producer forwarding
of the actual CPU package.

The exact four Linux-amd64 CPU source files now have a source-only
ownership census. The dispatcher has two nil returns and two direct
producer forwards; Intel owns one zero-value local, appends from the
CPUID loop, checks one length and returns that owner; AMD uses one
owner, three append sites and three owned returns. The proof rejects
aliasing, address taking, shadowing, forwarding cycles and unknown
owner use. Normal scalar/control checking and actual package
lowering have not been bypassed. Eighty-one adjacent checks passed.
The exact upstream cache-size file and CPUID assembly ran unchanged
in an official Go 1.27.3 reference harness across collection; a
separate PCC/Go static backing native control passed. The next step
is to attach these proved roles to the ordinary source checker.

An explicit source-only analyzer now resolves proved []uintptr signatures
and locals, type-checks appended elements with ordinary Go assignability, and
retains typed len, nil and forwarding results. Unproved callers and normal
Go compilation still reject these managed slices. Eighty-three adjacent
checks and three native/reference controls passed. For the exact 133-instance
original graph, explicitly enabling the probe advances past the cache-size
signatures to `internal/cpu/cpu.go:24:2`, the blank X86 field of named type
`CacheLinePad` (a record containing only byte padding). This is before the
actual cache-size bodies are checked; no test IR or native binary was emitted.

A blank field may now be a named record made entirely of nonempty
byte-array padding or nested blank-only padding records. Its real
storage remains in the record layout, while positional evaluation
and equality keep the existing blank-field rules. Linux native PCC
and official Go agreed on both standalone padding originals; 106
adjacent checks passed. The same 133-instance opt-in source probe
advances to `internal/cpu/cpu.go:207:13` at `var options []option`.
That global is filled by a bounded builder whose Feature pointers
refer to package bool fields; unlike the scalar cache-size slice it
needs record backing and initialization provenance. No native
package build or upstream tests ran.

The unchanged x86 option-builder prefix now proves eight initial records
and at most three five-record append groups, with counts 23/18/13/8 for
GOAMD64 v1/v2/v3/v4 respectively. Every Feature expression addresses a
distinct declared package bool field; the one-sided X86 linker export
is explicitly unresolved, not an invented native binding. The proof
rejects unbounded loops, duplicate labels, captured uses and non-static
or non-bool pointer sources. Seventy-six scoped checks and three
standalone native/reference controls passed. The original global catalog
is not yet represented or initialized by PCC, and its consumer flow is
a separate obligation.

An exact source-instance request now recomputes the bounded catalog
proof before retaining the global's nominal record-slice declaration.
It supplies neither initializer nor native ABI symbol; every materialized
use still rejects. Ordinary Go compilation and an invalid proof request
continue to stop at the declaration. On the exact 133-instance original
graph the probe advances to `internal/cpu/cpu.go:231:20`, the dynamic
`env[:i]` split after `indexByte`. No full package IR or test executable
is produced. Seventy-eight adjacent checks and three standalone native
differentials passed.

Named immutable string descriptors now support checked two-index
subviews. Bounds evaluate once in source order; high is checked
against byte length before low is checked against high; no payload
pointer is formed on failure. The Linux bootstrap terminal path
matched Go 1.27.3 on valid byte views and six invalid bounds,
including negative, very wide unsigned, reversed and competing
bounds (initial panic line and exit status, not stack or recover).
One hundred thirty-nine adjacent checks and two native nodes
passed. The exact source probe now stops at the catalog range
receiver in `internal/cpu/cpu.go:255:19`; neither this catalog nor
the whole upstream package has a native build.

The source-only catalog mode now accepts direct bool and string field
places in a single-index range over the proved catalog. The index is
the loop's unchanged int binding; the checker rejects rebinds, computed
indexes, address taking, calls, pointer fields and whole-record
materialization in this lane. Both original index-only ranges now
type-check. The exact source probe stops at the copied-element range
in `internal/cpu/cpu.go:273:20`, which also needs the Feature pointee
origin. Sixty-nine adjacent plus 35 existing native/reference checks
passed; the catalog itself is still not emitted.

The copied-element catalog range now checks its scalar reads and its
immediate Feature dereferences. Pointer-flow validation retains the
proved package bool owners rather than inventing an address in the
copy's local frame. Pointer extraction, copied record escape, field
replacement and taking an address in the copy all remain unadmitted.
The earlier indexed places also survive pointer-flow validation.
The exact source-instance probe advances to dynamic string byte
indexing in `internal/cpu/cpu.go:293:8`. One hundred twenty-nine
adjacent and 57 existing native/reference tests passed. The catalog,
its global initialization and whole upstream execution remain unbuilt.

Named string byte indexing now checks the unsigned offset against
the descriptor length before any data access. A native indexByte-like
loop, non-ASCII raw bytes and one-time index operand evaluation matched
Go 1.27.3; four out-of-bounds forms matched the initial panic line and
terminal status. The same tests retain the original checked-slice
behavior, including the benchmark-shaped named input. One hundred
twenty-one adjacent and four native/reference nodes passed. The exact
source probe now reaches the original `options = []option{...}` write
at `internal/cpu/cpu_x86.go:73:2`; catalog initialization and upstream
native execution are still unsupported.

The source-only catalog initializer and each proved append now
type-check their original string labels and direct package bool
addresses. The fixed capacity/source proofs retain exact write-site
identity; all other global slice reads or writes remain rejected.
The exact source probe advances to the noncapturing doDerived
function literal at `internal/cpu/cpu_x86.go:213:14`. One hundred
four adjacent and three native/reference controls passed. This is
not a native catalog allocation, linked X86 export, or upstream build.

A zero-argument/void function literal assigned to an ordinary package
procedure slot can now use an internal code pointer when every body
read/write is a proved package place and no lexical capture or call is
present. The normal checker rechecks its original body in a fresh
activation. Linux code observes current package fields and distinct
assignment sites, matching Go 1.27.3; 141 adjacent and 22 native or
reference checks passed. The exact source probe now reaches the
original CPU Name's `make([]byte,0,48)` at
`internal/cpu/cpu_x86.go:246:10`. Native catalog storage and upstream
test execution remain unbuilt.

An explicit bounded-caller request now rechecks the whole byte-owner
shape, binds the exact small constant make and append calls, and
type-checks their uint32 inputs and the owner's len. Changed capacity,
wrong element types and unproved callers still reject. The exact
source-instance probe advances through the original CPU Name's 48-byte
builder to its first checked read at `internal/cpu/cpu_x86.go:257:28`.
Eighty adjacent checks and four isolated native byte-operation
differentials passed. The original CPU Name was not lowered or
executed by PCC; its dynamic byte views and copied result remain open.
