# go-enry 2.9.6 tokenizer: unchanged Go package slice target

docs/python-native-extension-languages.md names go-enry and its Python package
as the recommended Go research candidate. Its language-detection API depends on
the generated `data` package (21 files, about 12.6 MB) and its Python binding on
a cgo shared library plus cffi; neither fits the existing source-lock bounds or
the current Go frontend. This directory pins the first bounded real-code slice
instead: the module's `regex` and `internal/tokenizer` packages, which the
detection API uses to tokenize file content. It is not a go-enry, data-table or
Python-binding claim.

The source lock pins official release `v2.9.6` at commit
`71719e4bdde528496011d030354a97b7f3ed4ff6` (module
`github.com/go-enry/go-enry/v2`), read from immutable raw.githubusercontent.com
URLs and checked against the GitHub tree's Git blob IDs. It retains every file
of both packages, including the build-tag alternatives `regex/oniguruma.go`
(`oniguruma`) and `internal/tokenizer/tokenize_c.go` (`flex`), both original
upstream test files, and root LICENSE, README.md, go.mod and go.sum. The `flex`
subpackage is a separate package used only under that tag and is not selected.
Nothing is stripped or adapted. License: Apache-2.0.

The default build (no tags), confirmed by the official Go 1.27.3 `go list`,
uses `regex/regex.go`, `regex/standard.go` (standard library `regexp`),
`internal/tokenizer/common.go` and `internal/tokenizer/tokenize.go` (`bytes`).
The upstream tests need testify v1.8.1; no verified source closure is supplied,
so owned and reference upstream-test execution remain explicitly UNRUN.

`tests/go/test_go_enry_tokenizer_project.py` drives the work:

- ordinary nodes verify every original byte and the build-constraint selection;
- the integration node compiles both original default-build packages with the
  owned Go frontend through a one-call admission main that exports `Tokenize`.
  A compiler rejection is a failing test, never an expected-gap pass;
- with `PCC_GO_REFERENCE` (an official Go 1.27.3 `go`), a separate reference
  node lists and builds the same originals in GOPATH mode without module
  downloads. It cannot supply PCC output or qualify PCC.

First observed blocker (local Mac, PCC master at the runtime-owned-linkname
round): `tokenize.go:16:23`, the `[]byte`/`[]string` slice types of `Tokenize`
(`go.unsupported.slice-storage`: slice types require shared-runtime backing
storage).
