"""Exact ownership and one shared integration budget; no compiler execution."""
from __future__ import annotations

import math

BOOTSTRAP_NODES = tuple(
    f"tests/python/gc/test_pcc_bootstrap_full_gc{gc}.py::test_full_three_stage_bootstrap_self_gc{gc}"
    for gc in range(5)
)
PARTITIONS = ("all", "bootstrap", "remainder")
INTEGRATION_SECONDS = 1800


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_node(node):
    # Only xdist's loadgroup suffix is stripped; test names/parameters stay exact.
    return node.split("@", 1)[0]


def partition_inventory(expected, selected, partition):
    require(partition in PARTITIONS, "unknown integration partition")
    require(len(expected) == len(set(expected)) and len(selected) == len(set(selected)),
            "duplicate node in inventory")
    if partition == "all":
        return list(expected), list(selected), []
    owners = set(BOOTSTRAP_NODES)
    expected_owned = [node for node in expected if canonical_node(node) in owners]
    require(len(expected_owned) == 5 and {canonical_node(node) for node in expected_owned} == owners,
            "the original five bootstrap owners must all be present exactly once")
    if partition == "bootstrap":
        require(len(expected) == 5, "unexpected node in bootstrap owner collection")
        return list(expected), list(selected), []
    transferred = expected_owned
    remaining = [node for node in expected if canonical_node(node) not in owners]
    kept = [node for node in selected if canonical_node(node) not in owners]
    require(not set(remaining) & set(transferred) and set(remaining) | set(transferred) == set(expected),
            "integration ownership is not a disjoint complete partition")
    return remaining, kept, transferred


def remaining_seconds(owner, *, source, producer, platform, manifest_sha256, guard):
    """Account the original 1800 seconds once, including owner guard cleanup.

    The artifact is downloaded by exact same-run/platform name. Unknown or
    malformed receipts cannot grant a fresh budget. A failed owner may still
    leave time for remainder coverage, but never yields a combined PASS.
    """
    require(owner.get("partition") == "bootstrap" and owner.get("marker") == "integration",
            "not a bootstrap owner receipt")
    require(owner.get("source") == source and owner.get("producer") == producer
            and owner.get("platform") == platform
            and owner.get("manifest_sha256") == manifest_sha256,
            "bootstrap owner source/run/attempt/platform/input differs")
    require(owner.get("timeout_s") == INTEGRATION_SECONDS
            and owner.get("status") in ("PASS", "FAIL"), "owner not terminal under original budget")
    elapsed = owner.get("guard_elapsed_s")
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0,
            "invalid owner elapsed time")
    windows = platform == "windows-x86_64"
    guard_elapsed = guard.get("seconds" if windows else "elapsed_s")
    require(guard.get("requested_timeout" if windows else "timeout_s") == INTEGRATION_SECONDS,
            "owner guard original timeout differs")
    terminals = ("COMPLETE", "FAIL") if windows else ("COMPLETE", "TIMEOUT", "MEMORY_LIMIT", "INTERRUPTED", "SAMPLER_ERROR")
    require(guard.get("status") in terminals, "owner guard is not terminal")
    require(type(guard_elapsed) in (int, float) and math.isfinite(guard_elapsed)
            and 0 <= guard_elapsed <= elapsed, "owner elapsed undercounts or lacks guard elapsed")
    if owner["status"] == "PASS":
        require(guard["status"] == "COMPLETE" and guard.get("returncode") == 0,
                "owner PASS contradicts guard terminal result")
    remaining = math.floor(INTEGRATION_SECONDS - elapsed)
    require(remaining > 0, "BUDGET_EXHAUSTED: integration remainder UNRUN")
    return remaining


def verify_union(owner, remainder):
    require(owner["partition"] == "bootstrap" and remainder["partition"] == "remainder",
            "invalid ownership pair")
    owned, rest, original = map(set, (owner["selected"], remainder["selected"], remainder["original_expected"]))
    require({canonical_node(node) for node in owned} == set(BOOTSTRAP_NODES) and len(owned) == 5,
            "owner inventory is not the exact five nodes")
    require(owned == set(remainder["transferred"]) and not owned & rest and owned | rest == original,
            "owner and remainder do not cover the original integration inventory exactly")
