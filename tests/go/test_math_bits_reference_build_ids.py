"""Original go1.27.3 `go list -deps -test -json math/bits` build identities.

The portable fixture is the full reference selection, not package sources, a
substitute Go installation, or a mock project pass. It retains full build IDs,
ForTest, source directory relative to GOROOT/src, original selected filenames
by role, and every Imports/ImportMap edge; only the generated cache file is
marked symbolically. The real integration reader still hashes all source bytes
and independently compares the generated test entry.
"""
from hashlib import sha256
import json
from pathlib import Path

import pytest
from scripts import ci_go_math_bits_project as project


FIXTURE = Path(__file__).with_name("fixtures") / "math_bits_reference_build_instances.jsonl"
P = "math/bits"
MAIN = P + ".test"
S = " [" + MAIN + "]"


def original_rows(goroot, cache):
    raw = FIXTURE.read_bytes()
    assert sha256(raw).hexdigest() == project.REFERENCE_BUILD_IDS_SHA256
    rows = [json.loads(line) for line in raw.splitlines()]
    for row in rows:
        row["Dir"] = str(goroot / "src" / row.pop("SourceDir"))
        if row["ImportPath"] == MAIN:
            assert row["GoFiles"] == ["<generated-test-entry>"]
            row["GoFiles"] = [str(cache / "cmdgo-testmain")]
    return rows


def input_inventory(rows):
    return {(r["Dir"], f) for r in rows if r["ImportPath"] != MAIN for role in
            ("GoFiles", "SFiles", "HFiles") for f in r[role]}


def test_actual_ordinary_and_recompiled_roots_are_not_the_same_graph(tmp_path):
    root, cache = tmp_path / "go", tmp_path / "cache"
    rows = original_rows(root, cache)
    indexed = {r["ImportPath"]: r for r in rows}
    result = project.reference_build_ids(rows, root, cache)
    assert result == {"instances": 133, "test_variants": 91, "import_map_edges": 511,
                      "sha256": project.REFERENCE_BUILD_IDS_SHA256}
    # Enumerate exact instance IDs, not source-package labels or directory names.
    visited, pending = set(), [MAIN]
    while pending:
        key = pending.pop()
        if key not in visited:
            visited.add(key)
            pending.extend(indexed[key]["Imports"])
    assert len(visited) == 132 and set(indexed) - visited == {P}
    assert len(input_inventory(rows)) == 912
    assert sum(r["ImportMap"].get(P) == P + S for r in rows) == 22
    assert indexed[MAIN]["ImportMap"]["reflect"] == "reflect" + S
    assert indexed[P]["Dir"] == indexed[P + S]["Dir"]
    assert set(indexed[P + S]["GoFiles"]) - set(indexed[P]["GoFiles"]) == {"export_test.go"}
    assert indexed[P + "_test" + S]["ImportMap"][P] == P + S
    # Output row ordering, the absolute machine root and the name of the
    # private generated cache file are not properties of the Go build graph.
    elsewhere = original_rows(tmp_path / "other-go", tmp_path / "other-cache")
    main = next(r for r in elsewhere if r["ImportPath"] == MAIN)
    main["GoFiles"] = [str(tmp_path / "other-cache" / "different-cache-entry")]
    assert project.reference_build_ids(elsewhere[::-1], tmp_path / "other-go", tmp_path / "other-cache") == result


@pytest.mark.parametrize(("fault", "diagnostic"), [
    ("existing-wrong-instance", "build-instance selection differs"),
    ("changed-variant-file-ownership", "build-instance selection differs"),
    ("implicit-reflect-dropped", "build-instance selection differs"),
    ("absent-target", "ImportMap/Imports"), ("not-in-imports", "ImportMap/Imports"),
    ("missing-map", "ImportMap/Imports"), ("wrong-for-test", "ForTest"),
    ("duplicate-id", "duplicate"), ("cache-escape", "outside the private cache"),
    ("extra-role", "input role"),
])
def test_real_reference_graph_rejects_wrong_owners_and_edges(tmp_path, fault, diagnostic):
    root, cache = tmp_path / "go", tmp_path / "cache"
    rows = original_rows(root, cache)
    ids = {row["ImportPath"]: row for row in rows}
    entry, external = ids[MAIN], ids[P + "_test" + S]
    inventory = input_inventory(rows)
    if fault == "existing-wrong-instance":
        # In the original external test, bits_test.go uses DeBruijn64, which
        # exists only in the internal-test variant's export_test.go. Retarget
        # to a real existing instance; every selected file and source hash
        # could still be identical across the entire graph.
        selected = external["ImportMap"].pop(P)
        external["Imports"][external["Imports"].index(selected)] = P
        assert P in ids and P + S in ids and input_inventory(rows) == inventory
    elif fault == "changed-variant-file-ownership":
        ids[P]["GoFiles"].append(ids[P + S]["GoFiles"].pop())
        assert input_inventory(rows) == inventory
    elif fault == "implicit-reflect-dropped":
        entry["Imports"].remove(entry["ImportMap"].pop("reflect"))
    elif fault == "absent-target":
        rows.remove(ids["os" + S])
    elif fault == "not-in-imports":
        entry["Imports"].remove("os" + S)
    elif fault == "missing-map":
        del entry["ImportMap"]["os"]
    elif fault == "wrong-for-test":
        ids["os" + S]["ForTest"] = "some/other/package"
    elif fault == "duplicate-id":
        rows.append(dict(entry))
    elif fault == "cache-escape":
        entry["GoFiles"] = [str(cache / ".." / "unrelated")]
    elif fault == "extra-role":
        ids["os" + S]["SysoFiles"] = ["replacement.syso"]
    with pytest.raises(ValueError, match=diagnostic):
        project.reference_build_ids(rows, root, cache)
