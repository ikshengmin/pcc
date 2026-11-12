"""Exact source-node platform obligations; no skip-text inference or fallback."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform as host_platform
import re
import shutil

MANIFEST = "scripts/data/ci_validation_owners.json"
PLATFORMS = {"linux-x86_64": "ubuntu-24.04", "macos-arm64": "macos-15"}
DOMAINS = {"cpp", "rust", "java", "go", "shared", "ci-contracts"}
LANES = {"ordinary", "integration", "reference"}


def validate_manifest(manifest):
    if (not isinstance(manifest, dict) or set(manifest) != {"schema", "default_platform", "overrides"}
            or type(manifest["schema"]) is not int or manifest["schema"] != 1
            or manifest["default_platform"] != "linux-x86_64"
            or not isinstance(manifest["overrides"], list)):
        raise ValueError("invalid ownership manifest")
    seen = set()
    for rule in manifest["overrides"]:
        if not isinstance(rule, dict) or set(rule) != {"domain", "lane", "nodeid", "platforms", "tools"}:
            raise ValueError("invalid ownership override shape")
        domain, lane, node = rule["domain"], rule["lane"], rule["nodeid"]
        if not isinstance(domain, str) or domain not in DOMAINS or not isinstance(lane, str) or lane not in LANES:
            raise ValueError("unknown ownership domain/lane")
        if (not isinstance(node, str) or "::" not in node or not node.split("::", 1)[1]
                or not node.split("::", 1)[0].endswith(".py") or "\\" in node
                or Path(node.split("::", 1)[0]).is_absolute()
                or ".." in Path(node.split("::", 1)[0]).parts):
            raise ValueError("malformed canonical ownership node")
        key = (domain, node)
        if key in seen:
            raise ValueError("duplicate ownership node")
        seen.add(key)
        owners, tools = rule["platforms"], rule["tools"]
        if (not isinstance(owners, list) or not owners or
                any(not isinstance(owner, str) or owner not in PLATFORMS for owner in owners)
                or len(set(owners)) != len(owners)):
            raise ValueError("unknown/duplicate ownership platform")
        if not isinstance(tools, dict) or set(tools) != set(owners):
            raise ValueError("tools must specify every owner exactly")
        for names in tools.values():
            if (not isinstance(names, list) or any(not isinstance(name, str) or
                    re.fullmatch(r"[A-Za-z0-9_+.-]+", name) is None for name in names)
                    or len(set(names)) != len(names)):
                raise ValueError("invalid/duplicate required tool")
    return manifest


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest JSON key: " + key)
        result[key] = value
    return result


def read_manifest(root):
    data = (Path(root) / MANIFEST).read_bytes()
    return validate_manifest(json.loads(data, object_pairs_hook=_unique_object)), hashlib.sha256(data).hexdigest()


def additional_campaigns(domains, manifest):
    validate_manifest(manifest)
    if any(domain not in DOMAINS for domain in domains):
        raise ValueError("unknown campaign domain")
    keys = {(rule["domain"], rule["lane"], owner) for rule in manifest["overrides"]
            if rule["domain"] in domains for owner in rule["platforms"]
            if owner != manifest["default_platform"]}
    return [dict(domain=domain, lane=lane, platform=owner, runner=PLATFORMS[owner])
            for domain, lane, owner in sorted(keys)]


def owner_modules(manifest, domain, lane, platform):
    validate_manifest(manifest)
    if domain not in DOMAINS or lane not in LANES or platform not in PLATFORMS:
        raise ValueError("unknown campaign identity")
    if platform == manifest["default_platform"]:
        raise ValueError("default owner requires complete domain inventory")
    return sorted({rule["nodeid"].split("::", 1)[0] for rule in manifest["overrides"]
                   if rule["domain"] == domain and rule["lane"] == lane and platform in rule["platforms"]})


def partition(rows, manifest, domain, lane, platform, complete=True):
    validate_manifest(manifest)
    if domain not in DOMAINS or lane not in LANES or platform not in PLATFORMS or type(complete) is not bool:
        raise ValueError("unknown campaign identity/scope")
    if complete != (platform == manifest["default_platform"]):
        raise ValueError("platform requires its declared inventory scope")
    # Reuse the raw schema validator without modifying gate or worker evidence.
    try:
        from .ci_validation_run import inventory_rows
    except ImportError:
        from ci_validation_run import inventory_rows
    if not isinstance(rows, list):
        raise ValueError("raw rows must be a list")
    files = sorted({row["file"] for row in rows})
    inventory_rows(rows, files)
    expected_modules = owner_modules(manifest, domain, lane, platform) if not complete else None
    if expected_modules is not None and files != expected_modules:
        raise ValueError("owned module inventory differs")
    by_node = {row["canonical_nodeid"]: row for row in rows}
    rules = {rule["nodeid"]: rule for rule in manifest["overrides"] if rule["domain"] == domain}
    for node, rule in rules.items():
        if complete or node.split("::", 1)[0] in files:
            if node not in by_node:
                raise ValueError("stale ownership node: " + node)
            if by_node[node]["lane"] != rule["lane"]:
                raise ValueError("ownership lane mismatch: " + node)
    groups = {}
    obligations = []
    for row in rows:
        node = row["canonical_nodeid"]
        rule = rules.get(node)
        owners = rule["platforms"] if rule else [manifest["default_platform"]]
        for group in row["groups"]:
            # Different lanes never execute together; within a lane even gated
            # nodes participate in dependency ownership validation.
            key = (row["lane"], group)
            owners_set = frozenset(owners)
            if key in groups and groups[key] != owners_set:
                raise ValueError("ownership splits xdist group: " + group)
            groups[key] = owners_set
        if row["lane"] == lane:
            for owner in sorted(owners):
                obligations.append(dict(domain=domain, lane=lane, nodeid=node, platform=owner,
                                        tools=sorted(rule["tools"][owner]) if rule else []))
    obligations.sort(key=lambda item: (item["nodeid"], item["platform"]))
    return {"obligations": obligations,
            "local_nodeids": sorted(item["nodeid"] for item in obligations if item["platform"] == platform),
            "transfers": [item for item in obligations if item["platform"] != platform]}


def host_context(platform):
    system, machine = host_platform.system(), host_platform.machine()
    actual = {("Linux", "x86_64"): "linux-x86_64", ("Darwin", "arm64"): "macos-arm64"}.get((system, machine))
    if platform not in PLATFORMS or actual != platform:
        raise ValueError("declared platform differs from actual host")
    context = {key: os.environ.get(env, "") for key, env in (
        ("repository", "GITHUB_REPOSITORY"), ("sha", "GITHUB_SHA"),
        ("run_id", "GITHUB_RUN_ID"), ("run_attempt", "GITHUB_RUN_ATTEMPT"))}
    if (re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", context["repository"]) is None
            or re.fullmatch(r"[0-9a-f]{40}", context["sha"]) is None
            or any(re.fullmatch(r"[1-9][0-9]*", context[key]) is None for key in ("run_id", "run_attempt"))):
        raise ValueError("invalid GitHub source/run context")
    return context, {"platform": platform, "system": system, "machine": machine}


def required_tool_bindings(obligations, platform, receipt):
    """Bind actual PATH selection, not a descriptive environment/provenance pin."""
    bindings = []
    names = sorted({name for row in obligations if row["platform"] == platform for name in row["tools"]})
    for name in names:
        row = receipt.get(name, {})
        invocation = shutil.which(name)
        if row.get("status") != "AVAILABLE" or not invocation:
            raise ValueError("required owner tool unavailable: " + name)
        path = Path(invocation)
        resolved = str(path.resolve(strict=True))
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if invocation != row.get("invocation") or resolved != row.get("resolved") or digest != row.get("sha256"):
            raise ValueError("required PATH tool differs from audited tool: " + name)
        bindings.append(dict(tool=name, invocation=invocation, resolved=resolved, sha256=digest))
    return {"platform": platform, "bindings": bindings}
