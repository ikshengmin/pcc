"""Run complete source/ABI lanes in bounded, module/group-preserving batches.

No source test is inferred PASS from collection or an absent prerequisite. Uses
the existing process-tree watchdog and durable pytest reporter; never provisions
pcc1, changes worker count, grants Rust certification, or enlarges a budget.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tomllib

try:
    from . import ci_validation_owners as owners
except ImportError:
    import ci_validation_owners as owners

ROOT = Path(__file__).resolve().parents[1]
LANES = ("ordinary", "integration", "reference")
TIMEOUT = 90
RSS = 1073741824
DISK_FLOOR = 1073741824
COLLECTION_MODULES = 10
REFERENCES = ROOT / "scripts/data/ci_validation_references.json"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def modules(domain, root=ROOT):
    if domain in {"cpp", "rust", "go"}:
        paths = set(root.glob(f"tests/{domain}/**/test_*.py"))
    elif domain == "java":
        paths = set(root.glob("tests/java/**/test_source*.py"))
    elif domain == "shared":
        data = json.loads((root / "scripts/data/ci_validation_shared.json").read_text())
        explicit = set(data["extra_modules"]).union(*map(set, data["groups"].values()))
        absent = sorted(path for path in explicit if not (root / path).is_file())
        if absent:
            raise ValueError("explicit shared scope disappeared: " + repr(absent))
        paths = {root / path for path in explicit}
        for pattern in data["refresh_globs"]:
            paths.update(root.glob(pattern))
    elif domain == "ci-contracts":
        paths = set(root.glob("tests/test_ci_*.py"))
        paths.update(root.glob("tests/test_xdist*.py"))
        for path in root.glob("tests/**/test_*.py"):
            if ".github/workflows/" in path.read_text(encoding="utf-8"):
                paths.add(path)
    else:
        raise ValueError("unknown source validation domain")
    if not paths:
        raise ValueError("empty source validation domain")
    return sorted(path.relative_to(root).as_posix() for path in paths)


def collection_plan(files):
    """Bound collection by whole modules; never exclude an inventoried file."""
    if (not isinstance(files, list) or not files or
            any(not isinstance(path, str) or not path.endswith(".py") or
                Path(path).is_absolute() or ".." in Path(path).parts or "::" in path for path in files) or
            len(files) != len(set(files))):
        raise ValueError("invalid/duplicate collection module inventory")
    ordered = sorted(files)
    return [ordered[index:index + COLLECTION_MODULES] for index in range(0, len(ordered), COLLECTION_MODULES)]


def inventory_rows(rows, files):
    """Validate identities and retain all raw rows, including unmet gates."""
    if not isinstance(rows, list):
        raise ValueError("raw inventory rows must be a list")
    identities, nodeids = set(), set()
    for row in rows:
        if (not isinstance(row, dict) or
                not all(isinstance(row.get(key), str) and row[key]
                        for key in ("nodeid", "canonical_nodeid", "file", "function")) or
                row["file"] not in files or not row["canonical_nodeid"].startswith(row["file"] + "::") or
                row.get("lane") not in LANES or type(row.get("selected_by_repository")) is not bool or
                not isinstance(row.get("groups"), list) or
                not all(isinstance(group, str) for group in row["groups"]) or
                not isinstance(row.get("gates"), list) or
                not all(isinstance(gate, dict) for gate in row["gates"]) or
                "reference_activity" not in row):
            raise ValueError("malformed or out-of-shard raw inventory row")
        if row["canonical_nodeid"] in identities or row["nodeid"] in nodeids:
            raise ValueError("duplicate raw inventory identity")
        identities.add(row["canonical_nodeid"])
        nodeids.add(row["nodeid"])
    return rows


def summarize_collection(directory, files, code):
    """A complete collection witness is never test-execution PASS evidence."""
    paths = sorted((directory / "inventory").glob("*.json"))
    if [path.name for path in paths] != ["controller.json"]:
        raise ValueError("one controller-only collection inventory required")
    witness = json.loads(paths[0].read_text())
    if (not isinstance(witness, dict) or witness.get("collect_only") is not True or
            type(witness.get("workers")) is not int or witness["workers"] != 0 or
            witness.get("dist") != "no" or witness.get("local_schedule") != {"workers": 0, "dist": "no"} or
            witness.get("marker") != "" or witness.get("requested_modules") != files):
        raise ValueError("collection command, module manifest, or scheduling differs")
    reports = witness.get("module_reports")
    if (not isinstance(reports, list) or len(reports) != len(files) or
            any(not isinstance(row, dict) or row.get("outcome") != "passed" or
                row.get("nodeid") not in files for row in reports) or
            {row["nodeid"] for row in reports} != set(files) or witness.get("collection_anomalies") != []):
        raise ValueError("missing/duplicate/failed/skipped module or nested collection evidence")
    rows = inventory_rows(witness.get("rows"), files)
    if witness.get("raw_nodeids") != [row["canonical_nodeid"] for row in rows]:
        raise ValueError("raw collection identities and metadata rows disagree")
    if any(row["nodeid"] != row["canonical_nodeid"] for row in rows):
        raise ValueError("single-process collection changed canonical node identities")
    selected = witness.get("selected")
    if (not isinstance(selected, list) or not all(isinstance(node, str) for node in selected) or
            len(selected) != len(set(selected)) or
            set(selected) != {row["nodeid"] for row in rows if row["selected_by_repository"]}):
        raise ValueError("collection selected identities disagree with repository gate evidence")
    events = [json.loads(line) for line in (directory / "live.jsonl").read_text().splitlines()]
    if not events or any(not isinstance(event, dict) or not isinstance(event.get("event"), str) for event in events):
        raise ValueError("malformed or missing collection live evidence")
    starts = [event for event in events if event["event"] == "start"]
    finishes = [event for event in events if event["event"] == "finish"]
    collected = [event for event in events if event["event"] == "collected"]
    deselections = [event.get("nodeids") for event in events if event["event"] == "deselected"]
    if any(not isinstance(nodes, list) or not all(isinstance(node, str) for node in nodes) for nodes in deselections):
        raise ValueError("malformed collection deselection evidence")
    deselected = [node for nodes in deselections for node in nodes]
    if (len(deselected) != len(set(deselected)) or
            set(deselected) != {row["nodeid"] for row in rows if not row["selected_by_repository"]}):
        raise ValueError("collection deselection disagrees with prerequisite inventory")
    permitted = {"start", "finish", "collected", "deselected"}
    if (len(starts) != 1 or events[0] is not starts[0] or starts[0].get("collect_only") is not True or
            type(starts[0].get("maxfail")) is not int or starts[0]["maxfail"] != 0 or
            len(finishes) != 1 or events[-1] is not finishes[0] or
            len(collected) != 1 or collected[0].get("nodeids") != selected or
            any(event["event"] not in permitted for event in events)):
        raise ValueError("nonterminal, duplicate, interrupted, or executing collection evidence")
    finish = finishes[0]
    expected_code = 0 if selected else 5
    if (type(code) is not int or code != expected_code or
            type(finish.get("exitstatus")) is not int or finish["exitstatus"] != code or
            type(finish.get("testscollected")) is not int or finish["testscollected"] != len(selected) or
            type(finish.get("testsfailed")) is not int or finish["testsfailed"] != 0 or
            finish.get("shouldstop") or finish.get("shouldfail")):
        raise ValueError("collection terminal counts/exit disagree")
    return {"modules": files, "rows": rows, "selected_count": len(selected), "status": "COLLECTED_NOT_EXECUTED"}


def merge_collections(parts, files):
    plan = collection_plan(files)
    if (not isinstance(parts, list) or len(parts) != len(plan) or
            any(not isinstance(part, dict) or part.get("modules") != expected
                or part.get("status") != "COLLECTED_NOT_EXECUTED" for part, expected in zip(parts, plan))):
        raise ValueError("collection shards do not cover the exact disjoint module inventory")
    rows = [row for part in parts for row in inventory_rows(part.get("rows"), part["modules"])]
    inventory_rows(rows, files)
    if not rows:
        raise ValueError("empty raw inventory")
    return rows


def resource_exclusion_groups(config, domain):
    """Read explicit scheduling semantics, never infer them from a group name."""
    policy = config.get("tool", {}).get("pcc-ci", {}).get("group_policy", {})
    if (not isinstance(policy, dict) or
            any(scope not in {"cpp", "rust", "go", "java", "shared", "ci-contracts"}
                or not isinstance(groups, dict) or not groups
                or any(not isinstance(name, str) or not name.strip() or
                       mode != "resource-exclusion-only" for name, mode in groups.items())
                for scope, groups in policy.items())):
        raise ValueError("invalid CI xdist group policy")
    return tuple(sorted(policy.get(domain, {})))


def batches(rows, lane, *, resource_groups=()):
    """Keep modules and default groups atomic; split opted-in resources serially.

    The caller must complete each guarded batch before admitting the next one.
    Resource-only groups promise no cross-module session/fixture dependency.
    Their original xdist marker remains the sole exclusion key in each batch.
    """
    if (not isinstance(resource_groups, (tuple, list)) or
            any(not isinstance(group, str) or not group.strip() for group in resource_groups) or
            len(resource_groups) != len(set(resource_groups))):
        raise ValueError("invalid resource-exclusion group declarations")
    resources = set(resource_groups)
    if resources - {group for row in rows for group in row["groups"]}:
        raise ValueError("declared resource-exclusion group absent from raw domain inventory")
    # Check even other lanes and unmet prerequisites in the frozen inventory.
    # xdist combines multiple names into a different key, breaking exclusion.
    for row in rows:
        if resources.intersection(row["groups"]) and len(row["groups"]) != 1:
            raise ValueError("resource-exclusion group must be the sole xdist group")
    selected = [row for row in rows if row["lane"] == lane and row["selected_by_repository"]]
    files = sorted({row["file"] for row in selected})
    parent = {path: path for path in files}

    def find(path):
        while parent[path] != path:
            path = parent[path]
        return path

    owner = {}
    for row in selected:
        for group in row["groups"]:
            if group in resources:
                continue
            if group in owner:
                parent[find(row["file"])] = find(owner[group])
            owner[group] = row["file"]
    components = {}
    for row in selected:
        components.setdefault(find(row["file"]), []).append(row)
    groups = sorted(components.values(), key=lambda values: min(row["file"] for row in values))
    target = 250 if lane == "ordinary" else 10
    result, current = [], []
    for group in groups:
        if current and (len(current) + len(group) > target or
                        len({row["file"] for row in current + group}) > 10):
            result.append(current)
            current = []
        current.extend(group)
    if current:
        result.append(current)
    identities = [row["canonical_nodeid"] for batch in result for row in batch]
    if len(identities) != len(set(identities)) or set(identities) != {row["canonical_nodeid"] for row in selected}:
        raise ValueError("batch union is not exact and disjoint")
    return result


def guarded_command(directory, command):
    # Equal admission floor/ceiling keeps the original exact cap. The shared
    # selector observes Linux MemAvailable/cgroup headroom or Darwin's existing
    # reclaimable/swap/disk policy; an explicit cap plus Darwin-only preflight
    # would reject every Linux invocation before pytest can start.
    return [sys.executable, str(ROOT / "scripts/run_process_tree_sample.py"),
            "--timeout", str(TIMEOUT), "--auto-tree-rss-ceiling-bytes", str(RSS),
            "--min-tree-rss-bytes", str(RSS),
            "--performance-lock", "--process-rss-history", "--darwin-preflight-reserve-bytes", "536870912",
            "--cwd", str(ROOT), "--result", str(directory / "result.json"),
            "--samples", str(directory / "samples.tsv"), "--stdout", str(directory / "target.stdout"),
            "--stderr", str(directory / "target.stderr"), "--", *command]


def pytest_command(directory, files, *, collect=False):
    command = [sys.executable, "-m", "pytest", "-x", "--maxfail=0", "-vv", "--tb=short", "-m", "",
               "-p", "scripts.ci_validation_inventory", "-p", "scripts.pytest_live_report",
               "--pcc-live-report", str(directory / "live.jsonl"), "--pcc-live-subtests",
               "--basetemp", str(directory / "tmp"), "--junitxml", str(directory / "junit.xml")]
    if collect:
        # Single-process collection only. Every test-body run retains six.
        command += ["--collect-only", "-n0"]
    return command + files


def identity():
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True,
                         text=True, timeout=10).stdout.strip()
    raw = subprocess.run(["git", "ls-files", "-t", "-z"], cwd=ROOT, check=True, capture_output=True,
                         timeout=10).stdout
    hashes, omitted = {}, []
    for entry in raw.decode().split("\0"):
        if entry:
            status, name = entry.split(" ", 1)
            path = ROOT / name
            if not path.exists() and status == "S" and name.startswith("projects/nginx-1.28.3/"):
                omitted.append(name)
            else:
                hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    result = {"sha": sha, "files": hashes, "sparse_omissions": omitted}
    if (ROOT / owners.MANIFEST).exists():
        manifest, digest = owners.read_manifest(ROOT)
        result.update(ownership_manifest=manifest, ownership_manifest_sha256=digest)
    return result


def run_guard(directory, command, environment, original):
    if identity() != original:
        raise ValueError("source identity changed before batch")
    if shutil.disk_usage(directory).free < DISK_FLOOR:
        raise ValueError("original 1-GiB free-disk floor not met")
    wrapper = subprocess.run(guarded_command(directory, command), cwd=ROOT, env=environment, check=False)
    result = json.loads((directory / "result.json").read_text())
    if (result["status"] != "COMPLETE" or result["timeout_s"] != TIMEOUT or
            result["max_tree_rss_bytes"] != RSS or result["peak_tree_rss_bytes"] > RSS or
            result.get("post_exit_cleanup_pids")):
        raise ValueError("resource/guard anomaly; no later batch admitted")
    if wrapper.returncode != result["returncode"]:
        raise ValueError("guard exit differs from its terminal target receipt")
    if identity() != original or shutil.disk_usage(directory).free < DISK_FLOOR:
        raise ValueError("source/disk postcondition differs; no later batch admitted")
    return result["returncode"]


def summarize_batch(directory, expected, frozen_rows=None):
    """Keep valid partial evidence; admission errors never erase earlier reports."""
    errors, events, mapping = [], [], {}
    try:
        witnesses = [json.loads(path.read_text()) for path in sorted((directory / "inventory").glob("gw*.json"))]
        if len(witnesses) != 6 or any(row != witnesses[0] for row in witnesses):
            raise ValueError("six identical worker inventories required")
        witness = witnesses[0]
        if witness["collect_only"] or witness["workers"] != 6 or witness["dist"] != "loadgroup":
            raise ValueError("original execution scheduling differs")
        if frozen_rows is not None:
            files = sorted({row["file"] for row in frozen_rows})
            actual = inventory_rows(witness["rows"], files)
            if witness.get("raw_nodeids") != [row["canonical_nodeid"] for row in actual]:
                raise ValueError("raw execution identities and metadata rows disagree")
            frozen = inventory_rows(frozen_rows, files)
            def metadata(values):
                return {row["canonical_nodeid"]: {key: value for key, value in row.items() if key != "nodeid"}
                        for row in values}
            if metadata(actual) != metadata(frozen):
                raise ValueError("execution raw inventory differs from frozen collection metadata")
        selected = witness["selected"]
        candidate = {row["nodeid"]: row["canonical_nodeid"] for row in witness["rows"]}
        if (len(selected) != len(set(selected)) or len(selected) != len(expected) or
                {candidate[node] for node in selected} != set(expected)):
            raise ValueError("executed selection differs from frozen batch")
        mapping = {node: candidate[node] for node in selected}
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append("inventory: " + str(error))
    try:
        lines = (directory / "live.jsonl").read_text().splitlines()
    except (OSError, UnicodeError) as error:
        errors.append("live report: " + str(error))
        lines = []
    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get("event"), str):
                raise ValueError("invalid event shape")
            events.append(event)
        except ValueError as error:
            errors.append(f"live report line {index + 1}: {error}")
    starts = [event for event in events if event["event"] == "start"]
    if len(starts) != 1 or starts[0].get("maxfail") != 0:
        errors.append("one start event with maxfail=0 required")
    finishes = [event for event in events if event["event"] == "finish"]
    finish = finishes[0] if len(finishes) == 1 and events[-1] is finishes[0] else {}
    if not finish:
        errors.append("pytest did not produce one terminal finish report")
    for field in ("exitstatus", "testsfailed"):
        if type(finish.get(field)) is not int or finish[field] < 0:
            errors.append("invalid/missing pytest " + field)
    session_events = [event for event in events if event["event"] in {
        "internalerror", "interrupt", "worker_crash", "collection"}]
    if finish.get("shouldstop") or finish.get("shouldfail"):
        session_events.append({"event": "session_stop", "shouldstop": finish.get("shouldstop"),
                               "shouldfail": finish.get("shouldfail")})
    outcomes, subtests, unattributed, invalid_nodes = {}, {}, [], set()
    for event in events:
        if event["event"] in {"report", "subtest"}:
            if (not all(isinstance(event.get(key), str) for key in ("nodeid", "when", "outcome"))
                    or event["nodeid"] not in mapping or event["when"] not in {"setup", "call", "teardown"}
                    or event["outcome"] not in {"passed", "failed", "skipped"}):
                unattributed.append(event)
                errors.append("unmapped or malformed test report")
                if isinstance(event.get("nodeid"), str) and event["nodeid"] in mapping:
                    invalid_nodes.add(mapping[event["nodeid"]])
                continue
            node = mapping[event["nodeid"]]
            if event["event"] == "subtest":
                context = event.get("subtest_context")
                valid = (event["when"] == "call" and type(event.get("subtest_index")) is int
                         and event["subtest_index"] > 0 and event.get("report_type") == "_pytest.subtests.SubtestReport"
                         and isinstance(context, dict) and isinstance(context.get("kwargs"), dict)
                         and (context.get("msg") is None or isinstance(context["msg"], str)))
                if not valid:
                    errors.append("malformed subtest identity: " + node)
                    invalid_nodes.add(node)
                subtests.setdefault(node, []).append(event)
            else:
                outcomes.setdefault(node, []).append(event)
    rows = []
    for node in sorted(expected):
        reports = outcomes.get(node, [])
        phases = {event["when"]: event for event in reports}
        unique = len(phases) == len(reports)
        if not unique:
            errors.append("duplicate pytest phase: " + node)
        children = subtests.get(node, [])
        if [event.get("subtest_index") for event in children] != list(range(1, len(children) + 1)):
            errors.append("duplicate/nonsequential subtest identity: " + node)
            invalid_nodes.add(node)
        complete = unique and set(phases) == {"setup", "call", "teardown"}
        passed = (complete and node not in invalid_nodes
                  and all(event["outcome"] == "passed" and "wasxfail" not in event for event in reports + children))
        failed = any(event["outcome"] == "failed" for event in reports + children)
        terminal = unique and (complete or (set(phases) == {"setup", "teardown"}
                                            and phases["setup"]["outcome"] in {"failed", "skipped"}))
        calls = [event for event in reports if event["when"] == "call"]
        rows.append({"nodeid": node, "status": "PASS" if passed else "FAIL" if failed else "UNRUN_OR_SKIPPED",
                     "full_phase": complete, "terminal_node": terminal,
                     "call_outcome": calls[0]["outcome"] if len(calls) == 1 else "NOT_RECORDED_OR_AMBIGUOUS",
                     "observed_phases": sorted(phases), "reports": reports, "subtests": children})
    return {"outcomes": rows, "pytest_exitstatus": finish.get("exitstatus"),
            "pytest_testsfailed": finish.get("testsfailed"), "session_events": session_events,
            "evidence_errors": errors, "unattributed_reports": unattributed}


def batch_continuation(batch):
    """Only an ordinary completed pytest session may admit independent work."""
    if batch.get("guard_error"):
        return False, "resource/guard/source/disk anomaly"
    if batch["evidence_errors"] or batch["session_events"]:
        return False, "incomplete/invalid evidence or interrupted/internal-error session"
    code = batch["returncode"]
    if code not in (0, 1) or code != batch["pytest_exitstatus"]:
        return False, "nonordinary or inconsistent pytest exit"
    if not all(row["terminal_node"] for row in batch["outcomes"]):
        return False, "selected nodes lack terminal phase evidence"
    failed = any(row["status"] == "FAIL" for row in batch["outcomes"])
    if (code == 0 and (failed or batch["pytest_testsfailed"] != 0) or
            code == 1 and (not failed or batch["pytest_testsfailed"] <= 0)):
        return False, "pytest exit and reported failures disagree"
    return True, "completed test-failure session" if code else "completed pytest session"


def campaign_status(receipt):
    statuses = [row["status"] for batch in receipt["batches"] for row in batch["outcomes"]]
    session_failed = any(batch["returncode"] != 0 or batch["pytest_exitstatus"] != 0 or
                         batch["pytest_testsfailed"] != 0 or batch.get("guard_error") or
                         batch.get("evidence_errors") or batch.get("session_events")
                         for batch in receipt["batches"])
    if session_failed or "FAIL" in statuses:
        return "FAIL"
    if (receipt["unrun"] or receipt.get("unattempted") or receipt.get("collection_pending_modules") or
            any(status != "PASS" for status in statuses)):
        return "INCOMPLETE_UNRUN"
    if receipt.get("coverage", {}).get("transfers"):
        return "PLATFORM_COVERAGE_PENDING"
    return "NOT_APPLICABLE" if not receipt["lane_count"] else "PASS"


def run(args):
    if args.out.exists():
        raise ValueError("refuse stale output directory")
    args.out.mkdir(parents=True)
    tool_receipt = None
    tool_receipt_path = None
    tool_receipt_sha = None
    consumed_rust_receipt = None
    native_inputs = None
    manifest = manifest_digest = required_tools = None
    platform = getattr(args, "platform", None)
    receipt = {"status": "RUNNING", "domain": args.domain, "lane": args.lane,
               "claim": "host-PCC bounded source tests; not native pcc1 or whole-project qualification",
               "workers": 6, "dist": "loadgroup", "timeout_s": TIMEOUT, "rss_bytes": RSS,
               "batches": [], "unrun": [], "unattempted": [],
               "collections": [], "collection_pending_modules": []}
    try:
        if platform is not None:
            context, host = owners.host_context(platform)
            manifest, manifest_digest = owners.read_manifest(ROOT)
            receipt["coverage"] = {"schema": 1, "context": context, "host": host,
                                   "manifest_sha256": manifest_digest,
                                   "scope": "domain" if platform == manifest["default_platform"] else "owned-modules"}
            save(args.out / "ownership-manifest.json", manifest)
        config = tomllib.loads((ROOT / "pyproject.toml").read_text())
        resources = resource_exclusion_groups(config, args.domain)
        receipt["group_policy"] = {name: "resource-exclusion-only" for name in resources}
        options = shlex.split(config["tool"]["pytest"]["ini_options"]["addopts"])
        if options != ["-n", "6", "--dist=loadgroup", "-m", "not integration"]:
            raise ValueError("default pytest policy changed")
        original = identity()
        if original["sha"] != os.environ.get("GITHUB_SHA"):
            raise ValueError("source SHA differs from workflow revision")
        if manifest is not None and (original.get("ownership_manifest") != manifest or
                original.get("ownership_manifest_sha256") != manifest_digest):
            raise ValueError("source identity does not bind frozen ownership manifest")
        save(args.out / "source-before.json", original)
        native_updates = {}
        native_bundle = getattr(args, "stage1_bundle", None)
        try:
            from . import ci_validation_native_inputs
        except ImportError:
            import ci_validation_native_inputs
        if ci_validation_native_inputs.requires_native_inputs(args.domain, args.lane) or native_bundle is not None:
            native_inputs, native_updates = ci_validation_native_inputs.admit(
                ROOT, native_bundle, domain=args.domain, lane=args.lane)
            receipt["native_inputs"] = native_inputs
            receipt["claim"] = ("bounded source tests retain explicit host-PCC/native-pcc1 ownership; "
                                "admitted Stage1 inputs are not fixed-point or whole-project qualification")
            save(args.out / "native-inputs-before.json", native_inputs)
        tool_receipt_path = Path(os.environ["PCC_CI_TOOL_RECEIPT"])
        tool_receipt_bytes = tool_receipt_path.read_bytes()
        tool_receipt_sha = hashlib.sha256(tool_receipt_bytes).hexdigest()
        tool_receipt = json.loads(tool_receipt_bytes)
        # The Rust test helper extracts only rustc's pin. CI separately verifies
        # all declared tool files, including the installed_files library closure.
        try:
            from .ci_validation_tools import verify_tool_receipt, consumed_rust_receipt_identity
        except ImportError:
            from ci_validation_tools import verify_tool_receipt, consumed_rust_receipt_identity
        if os.environ.get("PCC_RUSTC_INSTALL_RECEIPT"):
            consumed_rust_receipt = consumed_rust_receipt_identity(
                tool_receipt, os.environ["PCC_RUSTC_INSTALL_RECEIPT"])
            save(args.out / "consumed-rust-receipt-before.json", consumed_rust_receipt)
        save(args.out / "tools-setup-receipt.json", tool_receipt)
        save(args.out / "tools-before.json", verify_tool_receipt(tool_receipt))
        files = (owners.owner_modules(manifest, args.domain, args.lane, platform)
                 if manifest is not None and platform != manifest["default_platform"] else modules(args.domain))
        save(args.out / "modules.json", files)
        env = dict(os.environ, PCC_NO_AUTO_PCC1="1", PCC_TEST_NO_NATIVE_PROVISIONING="1",
                   PYTEST_ADDOPTS="", PYTHONDONTWRITEBYTECODE="1", PCC_PYTEST_AUTO_CLEAN="0",
                   PCC_VALIDATION_REFERENCES=str(REFERENCES),
                   PCC_VALIDATION_SOURCE_MANIFEST=str(args.out / "source-before.json"))
        env.pop("PCC_VALIDATION_SELECTION", None)
        env.update(native_updates)
        shards = collection_plan(files)
        save(args.out / "collection-plan.json", shards)
        receipt["collection_pending_modules"] = [path for shard in shards for path in shard]
        parts = []
        for index, shard in enumerate(shards):
            collect = args.out / f"collection-{index:03}"
            collect.mkdir()
            save(collect / "modules.json", shard)
            env.update(PCC_VALIDATION_INVENTORY=str(collect / "inventory"),
                       PCC_VALIDATION_COLLECTION=str(collect / "modules.json"))
            part = {"directory": collect.name, "modules": shard, "status": "COLLECTION_INCOMPLETE"}
            receipt["collections"].append(part)
            save(args.out / "receipt.json", receipt)
            try:
                code = run_guard(collect, pytest_command(collect, shard, collect=True), env, original)
                collected = summarize_collection(collect, shard, code)
            except Exception as error:
                part["error"] = type(error).__name__ + ": " + str(error)
                raise
            parts.append(collected)
            part.update(status=collected["status"], returncode=code, raw_count=len(collected["rows"]),
                        selected_count=collected["selected_count"])
            receipt["collection_pending_modules"] = [path for pending in shards[index + 1:] for path in pending]
            save(args.out / "receipt.json", receipt)
        rows = merge_collections(parts, files)
        save(args.out / "raw-inventory.json", {"modules": files, "rows": rows})
        env.pop("PCC_VALIDATION_COLLECTION", None)
        receipt["raw_union_count"] = len(rows)
        receipt["lane_count"] = sum(row["lane"] == args.lane for row in rows)
        planner_rows = rows
        local_ids = {row["canonical_nodeid"] for row in rows if row["lane"] == args.lane}
        if manifest is not None:
            partition = owners.partition(rows, manifest, args.domain, args.lane, platform,
                                         complete=platform == manifest["default_platform"])
            receipt["coverage"].update(partition)
            receipt["transfers"] = [dict(row, status="TRANSFER_PENDING") for row in partition["transfers"]]
            local_ids = set(partition["local_nodeids"])
            receipt["lane_count"] = len(local_ids)
            # Only the planner copy changes selection. Raw collection and worker
            # witnesses retain the repository's actual gate decisions unchanged.
            planner_rows = [dict(row, selected_by_repository=(row["selected_by_repository"] and
                            row["canonical_nodeid"] in local_ids)) for row in rows]
            required_tools = owners.required_tool_bindings(partition["obligations"], platform, tool_receipt)
            save(args.out / "required-tools-before.json", required_tools)
            if platform != manifest["default_platform"]:
                resources = tuple(group for group in resources if any(group in row["groups"] for row in rows))
        receipt["unrun"] = [dict(row, status="UNRUN_PREREQUISITE") for row in rows
                            if row["canonical_nodeid"] in local_ids and not row["selected_by_repository"]]
        plan = batches(planner_rows, args.lane, resource_groups=resources)
        receipt["unattempted"] = [row["canonical_nodeid"] for batch in plan for row in batch]
        save(args.out / "batch-plan.json", [[row["canonical_nodeid"] for row in batch] for batch in plan])
        for index, batch in enumerate(plan):
            directory = args.out / f"batch-{index:03}"
            directory.mkdir()
            expected = [row["canonical_nodeid"] for row in batch]
            save(directory / "selection.json", expected)
            env.update(PCC_VALIDATION_SELECTION=str(directory / "selection.json"),
                       PCC_VALIDATION_INVENTORY=str(directory / "inventory"))
            code, guard_error = None, None
            try:
                code = run_guard(directory, pytest_command(directory, sorted({row["file"] for row in batch})), env, original)
            except Exception as error:
                guard_error = type(error).__name__ + ": " + str(error)
            batch_files = {row["file"] for row in batch}
            summary = summarize_batch(directory, expected, [row for row in rows if row["file"] in batch_files])
            save(directory / "outcomes.json", summary)
            result = {"directory": directory.name, "returncode": code, "guard_error": guard_error, **summary}
            proceed, reason = batch_continuation(result)
            result.update(continue_independent_batches=proceed, continuation_reason=reason)
            receipt["batches"].append(result)
            receipt["unattempted"] = [row["canonical_nodeid"] for pending in plan[index + 1:] for row in pending]
            save(args.out / "receipt.json", receipt)
            if not proceed:
                raise ValueError(reason + "; no later batch admitted")
            # Ordinary assertion/fixture failures stay FAIL, but do not suppress
            # independent frozen batches. Resource/session anomalies stop here.
        final_source = identity()
        save(args.out / "source-after.json", final_source)
        if final_source != original:
            raise ValueError("source identity changed after campaign")
        receipt["status"] = campaign_status(receipt)
    except KeyboardInterrupt as error:
        # Preserve interruption semantics while the existing finally persists
        # a non-running receipt and checks any admitted input/tool closure.
        receipt.update(status="INTERRUPTED", error=type(error).__name__ + ": " + str(error))
        raise
    except Exception as error:
        receipt.update(status="INFRASTRUCTURE_OR_RESOURCE_FAILURE", error=type(error).__name__ + ": " + str(error))
    finally:
        if manifest is not None:
            try:
                if owners.read_manifest(ROOT) != (manifest, manifest_digest):
                    raise ValueError("ownership manifest changed during campaign")
                if required_tools is not None:
                    actual = owners.required_tool_bindings(receipt["coverage"]["obligations"], platform, tool_receipt)
                    save(args.out / "required-tools-after.json", actual)
                    if actual != required_tools:
                        raise ValueError("required PATH tool bindings changed during campaign")
            except Exception as error:
                receipt.update(status="INFRASTRUCTURE_OR_RESOURCE_FAILURE",
                               ownership_closure_error=type(error).__name__ + ": " + str(error))
        if native_inputs is not None:
            try:
                save(args.out / "native-inputs-after.json",
                     ci_validation_native_inputs.verify_after(ROOT, native_inputs))
            except Exception as error:
                receipt.update(status="INFRASTRUCTURE_OR_RESOURCE_FAILURE",
                               native_inputs_error=type(error).__name__ + ": " + str(error))
        if tool_receipt is not None:
            try:
                if hashlib.sha256(tool_receipt_path.read_bytes()).hexdigest() != tool_receipt_sha:
                    raise ValueError("setup tool receipt changed during source campaign")
                if consumed_rust_receipt is not None:
                    actual = consumed_rust_receipt_identity(tool_receipt, os.environ["PCC_RUSTC_INSTALL_RECEIPT"])
                    if actual != consumed_rust_receipt:
                        raise ValueError("consumed Rust receipt path or bytes changed during source campaign")
                    save(args.out / "consumed-rust-receipt-after.json", actual)
                save(args.out / "tools-after.json", verify_tool_receipt(tool_receipt))
            except Exception as error:
                receipt.update(status="INFRASTRUCTURE_OR_RESOURCE_FAILURE",
                               tool_closure_error=type(error).__name__ + ": " + str(error))
        save(args.out / "receipt.json", receipt)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as stream:
                stream.write(f"## {args.domain} / {args.lane}: {receipt['status']}\n\n"
                             f"Raw union: {receipt.get('raw_union_count', 'UNRUN')}; lane: {receipt.get('lane_count', 'UNRUN')}; "
                             f"prerequisite UNRUN: {len(receipt['unrun'])}.\n")
    return 0 if receipt["status"] in {"PASS", "NOT_APPLICABLE", "PLATFORM_COVERAGE_PENDING"} else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True, choices=("cpp", "rust", "java", "go", "shared", "ci-contracts"))
    parser.add_argument("--lane", required=True, choices=LANES)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--platform", choices=tuple(owners.PLATFORMS),
                        help="Explicit source coverage producer; final reconciliation is required")
    parser.add_argument("--stage1-bundle", type=Path,
                        help="Same-run Linux x86-64 Stage1 bundle required by native-dependent source lanes")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
