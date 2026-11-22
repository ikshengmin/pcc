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
