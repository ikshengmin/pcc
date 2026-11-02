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

ROOT = Path(__file__).resolve().parents[1]
LANES = ("ordinary", "integration", "reference")
TIMEOUT = 90
RSS = 1073741824
DISK_FLOOR = 1073741824
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


def batches(rows, lane):
    """Partition runnable nodes exactly; never split a module/xdist group."""
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
    command = [sys.executable, "-m", "pytest", "-vv", "--tb=short", "-m", "",
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
    return {"sha": sha, "files": hashes, "sparse_omissions": omitted}


def run_guard(directory, command, environment, original):
    if identity() != original:
        raise ValueError("source identity changed before batch")
    if shutil.disk_usage(directory).free < DISK_FLOOR:
        raise ValueError("original 1-GiB free-disk floor not met")
    wrapper = subprocess.run(guarded_command(directory, command), cwd=ROOT, env=environment, check=False)
    result = json.loads((directory / "result.json").read_text())
    if (result["status"] != "COMPLETE" or result["timeout_s"] != TIMEOUT or
            result["max_tree_rss_bytes"] != RSS or result["peak_tree_rss_bytes"] > RSS):
        raise ValueError("resource/guard anomaly; no later batch admitted")
    if wrapper.returncode != result["returncode"]:
        raise ValueError("guard exit differs from its terminal target receipt")
    if identity() != original or shutil.disk_usage(directory).free < DISK_FLOOR:
        raise ValueError("source/disk postcondition differs; no later batch admitted")
    return result["returncode"]


def summarize_batch(directory, expected):
    """Keep valid partial evidence; admission errors never erase earlier reports."""
    errors, events, mapping = [], [], {}
    try:
        witnesses = [json.loads(path.read_text()) for path in sorted((directory / "inventory").glob("gw*.json"))]
        if len(witnesses) != 6 or any(row != witnesses[0] for row in witnesses):
            raise ValueError("six identical worker inventories required")
        witness = witnesses[0]
        if witness["collect_only"] or witness["workers"] != 6 or witness["dist"] != "loadgroup":
            raise ValueError("original execution scheduling differs")
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
    if receipt["unrun"] or receipt.get("unattempted") or any(status != "PASS" for status in statuses):
        return "INCOMPLETE_UNRUN"
    return "NOT_APPLICABLE" if not receipt["lane_count"] else "PASS"


def run(args):
    if args.out.exists():
        raise ValueError("refuse stale output directory")
    args.out.mkdir(parents=True)
    tool_receipt = None
    tool_receipt_path = None
    tool_receipt_sha = None
    consumed_rust_receipt = None
    receipt = {"status": "RUNNING", "domain": args.domain, "lane": args.lane,
               "claim": "host-PCC bounded source tests; not native pcc1 or whole-project qualification",
               "workers": 6, "dist": "loadgroup", "timeout_s": TIMEOUT, "rss_bytes": RSS,
               "batches": [], "unrun": [], "unattempted": []}
    try:
        options = shlex.split(tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]["addopts"])
        if options != ["-n", "6", "--dist=loadgroup", "-m", "not integration"]:
            raise ValueError("default pytest policy changed")
        original = identity()
        if original["sha"] != os.environ.get("GITHUB_SHA"):
            raise ValueError("source SHA differs from workflow revision")
        save(args.out / "source-before.json", original)
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
        files = modules(args.domain)
        save(args.out / "modules.json", files)
        env = dict(os.environ, PCC_NO_AUTO_PCC1="1", PCC_TEST_NO_NATIVE_PROVISIONING="1",
                   PYTEST_ADDOPTS="", PYTHONDONTWRITEBYTECODE="1", PCC_PYTEST_AUTO_CLEAN="0",
                   PCC_VALIDATION_REFERENCES=str(REFERENCES),
                   PCC_VALIDATION_SOURCE_MANIFEST=str(args.out / "source-before.json"))
        collect = args.out / "collection"
        collect.mkdir()
        env["PCC_VALIDATION_INVENTORY"] = str(collect / "inventory")
        code = run_guard(collect, pytest_command(collect, files, collect=True), env, original)
        if code not in (0, 5):
            raise ValueError("collection failed")
        rows = json.loads((collect / "inventory/controller.json").read_text())["rows"]
        ids = [row["canonical_nodeid"] for row in rows]
        if len(ids) != len(set(ids)) or not rows:
            raise ValueError("empty/duplicate raw inventory")
        if any(row["lane"] not in LANES for row in rows):
            raise ValueError("unclassified execution lane")
        receipt["raw_union_count"] = len(rows)
        receipt["lane_count"] = sum(row["lane"] == args.lane for row in rows)
        receipt["unrun"] = [dict(row, status="UNRUN_PREREQUISITE") for row in rows
                            if row["lane"] == args.lane and not row["selected_by_repository"]]
        plan = batches(rows, args.lane)
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
            summary = summarize_batch(directory, expected)
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
        save(args.out / "source-after.json", identity())
        receipt["status"] = campaign_status(receipt)
    except Exception as error:
        receipt.update(status="INFRASTRUCTURE_OR_RESOURCE_FAILURE", error=type(error).__name__ + ": " + str(error))
    finally:
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
    return 0 if receipt["status"] in {"PASS", "NOT_APPLICABLE"} else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True, choices=("cpp", "rust", "java", "go", "shared", "ci-contracts"))
    parser.add_argument("--lane", required=True, choices=LANES)
    parser.add_argument("--out", required=True, type=Path)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
