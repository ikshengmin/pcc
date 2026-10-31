# Specification feature ledger — schema 1

This is a metadata layer over existing language/project tests and
scripts/cpython_conformance/run.py, not another compiler or suite runner.

## Coverage contract

Exactly three feature states are accepted: supported,
explicitly_unimplemented, missing. Each feature has an ID, normative chapter
links, explicit narrow scope, test IDs, diagnostic mappings and evidence IDs.
Missing can mean missing implementation or missing required qualification:
the scope must distinguish them. Out-of-scope reasons are orthogonal.

Supported requires passing unit, real-program and same-original-source raw
differential evidence in the SAME compiler revision, pcc0/pcc1 mode, platform,
backend and GC configuration. Reports expose those exact lanes. Unit tests must
exit zero; a program may intentionally exit nonzero if its exact reference does.
stdout, stderr and exit are compared separately. Metadata/typecheck references
and normalized primitive-value comparisons cannot qualify raw-stream support.
Java semantic models do not qualify native object/runtime execution.

Chapter inventory and feature inventory completeness are separate flags.
Coverage denominators are inventoried rows, not all normative requirements.
Uninventoried chapters have zero denominator, not a false 100% coverage claim.

## Language documents

Required top-level fields:
schema_version=1; language; specification {title, version, url};
inventory {chapters_complete, features_complete, notes};
chapters [{id, title, url}]; features; diagnostics; evidence.

A feature contains id, title, chapter_ids, scope, status, tests (node IDs),
diagnostics (stable codes), evidence (IDs), and optional out_of_scope_reason.
Diagnostics contain inventory_complete, notes, codes mapping code to feature_id
and source {path, sha256}, and message_only source-bound gaps. Each explicit
unsupported row needs a stable code or a mapped message-only boundary. Generic
syntax diagnostics do not substitute for a feature-specific unsupported error.

Evidence contains id, test_id, kind (unit/program/differential), purpose
(unit/runtime/typecheck/rejection), independent outcome, immutable source_revision,
source {repository-relative path, sha256}, mode, platform, backend, gc,
command and command_kind (executed/reproduction). Program and differential rows
also bind input_sha256 of unchanged original program bytes, not merely the
Python container test file. Executed rows contain actual stdout_sha256,
stderr_sha256 and integer exit_code. Differentials require expected observations
and external_reference identity (name, version, platform, kind, equal original
input_sha256). Runtime comparison.kind is raw_process_streams or
normalized_primitive_value. The latter preserves actual raw_reference metadata
and describes normalization, but cannot qualify supported.

    uv run python scripts/language_feature_ledger.py specs/features/*.json --report
    uv run python scripts/language_feature_ledger.py specs/features/cpp.json \
      --audit-diagnostics /exact/compiler/source

The AST audit scans actual frontend files, including finite constructed codes.
Unmapped/stale codes fail; unresolved dynamic construction is an explicit gap.
--verify-files SOURCE rehashes recorded source files against an exact snapshot.
Historical evidence from different revisions must not be presented as a single
current tree. Schema validation alone is metadata validation, not execution
certification.

## Suite results, shards, regression gate

scripts/conformance_ledger.py validates immutable suite metadata, reports
accepted/rejected/executed outcomes separately, plans deterministic shards
(default six, no execution), exports verified passed IDs, gates regressions,
and ranks blockers by distinct failing tests and real projects.

A suite pins id, language, version, license, source_url, immutable source_revision
(full commit or sha256 content identity), source_sha256, source_unchanged=true,
inventory_complete, scope, existing adapter, and tests
{id, path, sha256, feature_ids, expectation: accept/reject/run}.
A run binds suite identity, compiler_revision, mode/platform/backend/GC,
per-test original input hash and actual/reference compile/run phases.
Blocked, absent, compile-only and runtime-disagreeing outcomes remain distinct.

    uv run python scripts/conformance_ledger.py plan SUITE.json --shards 6
    uv run python scripts/conformance_ledger.py summary SUITE.json RUN.json
    uv run python scripts/conformance_ledger.py allowlist SUITE.json RUN.json \
      --receipt-index PRIVATE_RECEIPTS.json
    uv run python scripts/conformance_ledger.py qualified-allowlist \
      specs/features/cpp.json PRIVATE_FEATURE_RECEIPTS.json
    uv run python scripts/conformance_ledger.py gate BASELINE.json CURRENT.json \
      --current-revision FULL_CURRENT_COMMIT

Both export routes require an audited private receipt index derived from sealed
test evidence. They rehash original/test sources, actual/reference raw streams,
process receipt JSON and available binaries; check exit codes, unchanged source
bytes and recognized receipt shapes. Outcome-only JSON is rejected. Runtime
suite entries need both compile and run receipt pairs, never compile-only proof.

The trust boundary is explicit: retained process artifacts are checked; mapping
a case, original source and compiler revision to that execution comes from the
audited private index. This is not cryptographic compile provenance. Current
allowlists must be generated by those receipt-verifying exporters, not edited
to relabel a historic run. The gate requires provenance, exact current compiler
revision, unchanged test-source identity and all formerly passed case identities.

Feature receipt indexes have records keyed by id, binding test_id,
source_revision, source, input, result, stdout, stderr, exit_field, optional
binary, and nested reference. File descriptors are local path plus sha256.
Suite indexes key by test_id/compiler_revision and carry compile/run records.
Private indexes, absolute recovery paths, raw logs and credentials stay outside
the repository. Exported baselines contain only exact IDs, content hashes,
reference identity and platform/owner provenance.

## Explicit limits

No complete official suite was downloaded or run by this change. The checked-in
passed-ID baselines contain 35 individually verified historical native identities:
23 C++, 2 Go, 6 Rust and 4 Java. They do not claim whole-language suite coverage. Existing aggregate PASS counts are not expanded into
invented IDs. pcc1 and user-Mac results are absent unless separately measured.
CPython Lib/test remains inventory scope; no Python debt cleanup is started.
