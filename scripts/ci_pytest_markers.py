"""Run the complete tests tree with one explicit marker and an existing Stage1.

Both marker groups run on all four native CI platforms in the package workflow.
This is an execution entry, not evidence that the whole suite already passes.
Nothing here builds a compiler or runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci_stage1_bundle import (
    PLATFORMS, host_platform, import_bundle, admit_in_place, workflow_lease,
)

from scripts.ci_bootstrap_partition import (
    BOOTSTRAP_NODES, PARTITIONS, partition_inventory, remaining_seconds, verify_union,
)

TIMEOUTS = {"not integration": 2400, "integration": 1800}
FROZEN_SOURCE = "pcc/ir/optimization/integer_fold_contract.py"
FROZEN_SHA256 = "8591c99dbd7eb97ae73b6151ecc1bab7def1d995a64c0b983e2200ba52df302a"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def marker_command(directory, marker, partition="all"):
    require(marker in TIMEOUTS, "unsupported marker expression")
    require(partition in PARTITIONS and (partition == "all" or marker == "integration"), "invalid marker partition")
    paths = list(BOOTSTRAP_NODES) if partition == "bootstrap" else ["tests"]
    return [sys.executable, "-m", "pytest", "-x", "-vv", "--tb=short",
            "-p", "scripts.pytest_live_report", "--pcc-live-report", str(directory / "live.jsonl"),
            "-p", "scripts.ci_pytest_markers", "--junitxml", str(directory / "junit.xml"),
            "--basetemp", str(directory / "tmp"), "-m", marker, *paths]


def watchdog_command(directory, marker, platform_id=None, *, partition="all", timeout=None):
    """Keep original POSIX budgets; selected tests retain their own build locks."""
    require(marker in TIMEOUTS, "unsupported marker expression")
    timeout = TIMEOUTS[marker] if timeout is None else timeout
    require(0 < timeout <= TIMEOUTS[marker], "marker budget exceeds original envelope")
    platform_id = platform_id or host_platform()
    require(platform_id != "windows-x86_64", "Windows requires the Job Object guard")
    options = (["--auto-tree-rss-ceiling-bytes", "4294967296", "--min-tree-rss-bytes", "2147483648",
                "--darwin-preflight-reserve-bytes", "536870912"] if platform_id == "macos-arm64"
               else ["--max-tree-rss-bytes", "17179869184"])
    return [sys.executable, str(ROOT / "scripts/run_process_tree_sample.py"),
            "--result", str(directory / "result.json"), "--samples", str(directory / "samples.tsv"),
            "--stdout", str(directory / "target.stdout"), "--stderr", str(directory / "target.stderr"),
            "--cwd", str(ROOT), "--timeout", str(timeout), *options, "--no-performance-lock",
            "--", *marker_command(directory, marker, partition)]


def run_guarded(directory, marker, env, platform_id, *, partition="all", timeout=None):
    """Use the existing native Windows Job Object or POSIX tree watchdog."""
    timeout = TIMEOUTS[marker] if timeout is None else timeout
    require(0 < timeout <= TIMEOUTS[marker], "marker budget exceeds original envelope")
    command = marker_command(directory, marker, partition)
    if platform_id == "windows-x86_64":
        from scripts.platform_process_watchdog import run
        record = {"guard": "platform_process_watchdog.run", "status": "RUNNING",
                  "requested_rss_limit": 17179869184, "requested_timeout": timeout,
                  "outer_performance_lock": False}
        save(directory / "result.json", record)
        started = time.monotonic()
        try:
            result = run(command, cwd=ROOT,
                         env=dict(env, PCC_WORKER_TREE_BUDGET_BYTES="17179869184"),
                         log_path=directory / "target.stdout", timeout=timeout,
                         rss_limit=17179869184)
            require(result["command"] == command
                    and 0 <= result["peak_tree_rss"] <= 17179869184,
                    "Windows guard command or tree budget differs")
            record.update(result, status="COMPLETE", returncode=0,
                          return_contract="normal return after child exit 0 and Job cleanup/final wait")
        except BaseException as error:
            # A raising guard returns no result; include its cleanup/final wait.
            record.update(status="FAIL", error=type(error).__name__ + ": " + str(error),
                          seconds=time.monotonic() - started)
            raise
        finally:
            save(directory / "result.json", record)
    else:
        from scripts.ci_worker_size_prior_gate import verify_posix_guard
        subprocess.run(watchdog_command(directory, marker, platform_id, partition=partition, timeout=timeout),
                       cwd=ROOT, env=env, check=True)
        verify_posix_guard(json.loads((directory / "result.json").read_text()), timeout)


def prepare_shared_input(root, manifest):
    """Copy only the original receipt; no symlink privileges or new Stage1."""
    relative = manifest["paths"].get("shared_receipt")
    if relative is None:
        return  # Darwin's integration fixture consumes the admitted compiler directly.
    common = root / "build/platform-bootstrap/shared"
    require(not common.exists() and not common.is_symlink(), "refuse an existing bootstrap input")
    common.mkdir(parents=True)
    shutil.copyfile(root / relative, common / "receipt.json")
    require(digest(common / "receipt.json") == manifest["files"][relative]["sha256"],
            "shared receipt copy differs")


def admitted_stage1(root, manifest_path, runtime_archive):
    """Recheck the explicit CI lease before a bootstrap fixture reuses Stage1.

    No receipt-only fixed-point claim: selected tests still execute Stage2/3.
    A missing/stale input is a hard failure, never permission to rebuild Stage1.
    """
    from scripts.bootstrap_platform import source_identity
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    root = Path(root).resolve(strict=True)
    require(digest(manifest_path) == os.environ.get("PCC_CI_STAGE1_MANIFEST_SHA256"),
            "Stage1 admission manifest changed")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    require(manifest["producer"] == workflow_lease(), "Stage1 workflow lease differs")
    require(manifest["platform"] == host_platform(), "Stage1 host platform differs")
    source = source_identity()
    require(all(source.get(key) == manifest["source"].get(key)
                for key in ("commit", "source_sha256", "clean_commit"))
            and source["clean_commit"] is True, "Stage1 source differs")
    compiler = root / manifest["paths"]["compiler"]
    runtime = root / manifest["paths"]["runtime"]
    require(compiler.resolve().is_relative_to(root) and runtime.resolve().is_relative_to(root),
            "Stage1 fixture paths escape checkout")
    require(Path(runtime_archive).resolve(strict=True) == runtime.resolve(strict=True),
            "Stage1 fixture runtime differs")
    for path in (compiler, runtime):
        relative = path.relative_to(root).as_posix()
        require(digest(path) == manifest["files"][relative]["sha256"], "Stage1 fixture bytes differ")
    _verified_test_runtime_archive(runtime, threads=True)
    return compiler


def pytest_configure(config):
    """A small plugin records full marker inventory before repository gates."""
    directory = os.environ.get("PCC_MARKER_REPORT_DIR")
    if not directory:
        return
    import pytest
    partition = os.environ.get("PCC_MARKER_PARTITION", "all")
    require(partition in PARTITIONS and (partition == "all" or config.getoption("markexpr") == "integration"),
            "invalid marker partition")
    if not hasattr(config, "workerinput"):
        save(Path(directory) / "configuration.json", {
            "marker": config.getoption("markexpr"), "workers": config.getoption("numprocesses"),
            "dist": config.getoption("dist"),
            **({"partition": partition} if partition != "all" else {}),
        })

    class Inventory:
        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_collection_modifyitems(self, items):
            marker = config.getoption("markexpr")
            require(marker in TIMEOUTS, "unexpected effective marker")
            expected_items = [item for item in items
                              if (item.get_closest_marker("integration") is not None) == (marker == "integration")]
            result = yield
            # xdist's loadgroup hook adds @group to nodeids. Compare the final
            # ids of the same original items, not pre-hook strings.
            expected = [item.nodeid for item in expected_items]
            original_expected = list(expected)
            expected, selected, transferred = partition_inventory(expected, [item.nodeid for item in items], partition)
            if partition == "remainder":
                selected_set = set(selected)
                moved = [item for item in items if item.nodeid not in selected_set]
                config.hook.pytest_deselected(items=moved)
                items[:] = [item for item in items if item.nodeid in selected_set]
            worker = getattr(config, "workerinput", {}).get("workerid", "controller")
            save(Path(directory) / ("inventory-" + worker + ".json"), {
                "marker": marker, "expected": expected, "selected": selected,
                "gated_out": sorted(set(expected) - set(selected)),
                "unexpected": sorted(set(selected) - set(expected)),
                **({"partition": partition, "original_expected": original_expected, "transferred": transferred}
                   if partition != "all" else {}),
            })
            return result

    config.pluginmanager.register(Inventory(), "pcc-complete-marker-inventory")


def summarize(directory, marker, partition="all"):
    inventories = [json.loads(path.read_text()) for path in sorted(directory.glob("inventory-gw*.json"))]
    require(len(inventories) == 6, "all six worker inventories are required")
    first = inventories[0]
    require(all(row == first for row in inventories), "worker selection differs")
    if partition != "all":
        require(first.get("partition") == partition, "inventory partition differs")
        expected, selected, transferred = partition_inventory(
            first["original_expected"], first["selected"] + first["transferred"], partition)
        require(expected == first["expected"] and selected == first["selected"]
                and transferred == first["transferred"], "partition inventory changed")
    configuration = json.loads((directory / "configuration.json").read_text())
    require(configuration == {"marker": marker, "workers": 6, "dist": "loadgroup",
                              **({"partition": partition} if partition != "all" else {})}
            and first["marker"] == marker and not first["unexpected"], "marker/default scheduling changed")
    events = [json.loads(line) for line in (directory / "live.jsonl").read_text().splitlines()]
    start = next(row for row in events if row["event"] == "start")
    require(start["markexpr"] == marker and not any(start[key] for key in
            ("keyword", "ignore", "ignore_glob", "deselect", "collect_only", "override_ini", "noconftest", "confcutdir", "lf", "stepwise", "stepwise_skip")),
            "pytest selection was narrowed or collection substituted")
    require(events[-1]["event"] == "finish" and events[-1]["exitstatus"] == 0, "pytest did not complete successfully")
    reports = [row for row in events if row["event"] == "report"]
    selected = set(first["selected"])
    require(selected, "empty marker selection")
    require({row["nodeid"] for row in reports} == selected, "selected tests did not all execute")
    incomplete = []
    by_node = {}
    for row in reports:
        phases = by_node.setdefault(row["nodeid"], {})
        require(row["when"] not in phases, "duplicate phase report")
        phases[row["when"]] = row
    for node in sorted(selected):
        phases = by_node[node]
        if set(phases) != {"setup", "call", "teardown"} or any(
                row["outcome"] != "passed" or "wasxfail" in row for row in phases.values()):
            incomplete.append({"nodeid": node, "reports": list(phases.values())})
    return {"marker": marker, "status": "PASS_WITH_GAPS" if first["gated_out"] or incomplete else "PASS",
            "marker_count": len(first["expected"]), "selected_count": len(selected),
            "gated_out": first["gated_out"], "skipped_or_xfailed": incomplete,
            "claim": ("complete marker selection on this native platform, not all hardware gates or a whole-suite qualification"
                      if partition == "all" else "exact bootstrap owner component only; not complete integration coverage"
                      if partition == "bootstrap" else "integration remainder component; combined coverage needs the owner union proof")}


def run(args):
    from scripts.ci_worker_size_prior_gate import environment
    from scripts.bootstrap_platform import source_identity, build_configuration
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    partition = getattr(args, "partition", "all")
    require(partition in PARTITIONS and (partition == "all" or args.marker == "integration"), "invalid marker partition")
    require(bool(getattr(args, "in_place", False)) == (partition == "bootstrap"), "only bootstrap owner admits in place")
    require(bool(getattr(args, "owner_report", None)) == (partition == "remainder"), "remainder requires its owner report")
    out = args.out.resolve()
    require(not out.exists(), "refuse stale marker receipts")
    out.mkdir(parents=True)
    receipt = {"status": "RUNNING", "marker": args.marker, "platform": args.platform,
               "partition": partition,
               "timeout_s": TIMEOUTS[args.marker], "outer_performance_lock": False,
               "memory_policy": "Darwin: auto 2-4 GiB, 512 MiB reserve; Linux/Windows: 16 GiB",
               "lock_owner": "selected tests retain their existing bootstrap/build locks"}
    try:
        require(args.platform == host_platform(), "runner platform differs from matrix")
        source = source_identity()
        admit = admit_in_place if partition == "bootstrap" else import_bundle
        manifest = admit(ROOT, args.bundle, source["commit"], args.platform, workflow_lease())
        require(source["source_sha256"] == manifest["source"]["source_sha256"], "checkout source digest differs")
        if partition != "bootstrap":
            require(args.research_source is not None and digest(args.research_source / FROZEN_SOURCE) == FROZEN_SHA256,
                    "required frozen 85b0 research source is missing or differs")
        runtime = ROOT / manifest["paths"]["runtime"]
        compiler = ROOT / manifest["paths"]["compiler"]
        env = environment()
        # environment() first rejects an inherited compiler override. Only the
        # already admitted same-run/source executable may then own API/CLI
        # compilation. Pure internal unit calls are still host-unit evidence;
        # direct C APIs need their own native artifact boundary.
        env.update(PCC_RUNTIME_ARCHIVE=str(runtime), PCC_THREADED_RUNTIME_ARCHIVE=str(runtime),
                   PCC_CURRENT_PCC1=str(compiler), PCC_REQUIRE_CURRENT_PCC1="1", PCC_NO_AUTO_PCC1="1",
                   PCC_TEST_COMPILER=str(compiler), PCC_TEST_COMPILER_STRICT="1",
                   PCC_TEST_COMPILER_LOG=str(out / "pytest" / "pcc1-routing.jsonl"),
                   PCC_TEST_NO_NATIVE_PROVISIONING="1", UV_PYTHON_DOWNLOADS="never",
                   PCC_CI_STAGE1_MANIFEST=str((args.bundle / "manifest.json").resolve()),
                   PCC_CI_STAGE1_MANIFEST_SHA256=digest(args.bundle / "manifest.json"),
                   PCC_MARKER_REPORT_DIR=str(out / "pytest"), PCC_MARKER_PARTITION=partition)
        if args.research_source is not None:
            env.update(PCC_RESEARCH_SOURCE=str(args.research_source.resolve()), PCC_RESEARCH_BASELINE_SOURCE=str(ROOT))
        os.environ.update(env)
        _verified_test_runtime_archive(runtime, threads=True)
        shared_path = manifest["paths"].get("shared_receipt")
        if shared_path is not None:
            shared = json.loads((ROOT / shared_path).read_text())
            require(shared["source"] == source and shared["configuration"] == build_configuration(manifest["target"]),
                    "shared bootstrap inputs differ")
        prepare_shared_input(ROOT, manifest)
        receipt.update(source=source, producer=manifest["producer"], bundle=manifest,
                       manifest_sha256=digest(args.bundle / "manifest.json"))
        timeout = TIMEOUTS[args.marker]
        owner = None
        if partition == "remainder":
            owner = json.loads((args.owner_report / "receipt.json").read_text())
            require(digest(args.owner_report / "pytest/result.json") == owner.get("guard_result_sha256"),
                    "owner guard receipt bytes changed")
            timeout = remaining_seconds(owner, source=source, producer=manifest["producer"],
                                        platform=args.platform, manifest_sha256=receipt["manifest_sha256"],
                                        guard=json.loads((args.owner_report / "pytest/result.json").read_text()))
            receipt.update(timeout_s=timeout, owner_status=owner["status"],
                           owner_guard_elapsed_s=owner["guard_elapsed_s"])
        save(out / "receipt.json", receipt)
        directory = out / "pytest"
        directory.mkdir()
        save(directory / "command.json", {"argv": marker_command(directory, args.marker, partition),
                                          "timeout": timeout, "outer_performance_lock": False})
        started = time.monotonic()
        try:
            run_guarded(directory, args.marker, env, args.platform, partition=partition, timeout=timeout)
        finally:
            receipt["guard_elapsed_s"] = time.monotonic() - started
            if (directory / "result.json").is_file():
                receipt["guard_result_sha256"] = digest(directory / "result.json")
        receipt.update(summarize(directory, args.marker, partition))
        require(receipt["status"] == "PASS", "strict selection has missing, gated, skipped or xfailed cases")
        if partition == "remainder":
            # Recompute strict summaries from immutable same-run artifacts; do
            # not trust a PASS label or a count in place of actual phase reports.
            require(summarize(args.owner_report / "pytest", "integration", "bootstrap")["status"] == "PASS"
                    and owner["status"] == "PASS", "bootstrap owner did not pass")
            owner_inventory = json.loads((args.owner_report / "pytest/inventory-gw0.json").read_text())
            remainder_inventory = json.loads((directory / "inventory-gw0.json").read_text())
            verify_union(owner_inventory, remainder_inventory)
            receipt.update(combined_integration_status="PASS",
                           original_integration_count=len(remainder_inventory["original_expected"]))
        require(source_identity() == source, "source changed during tests")
        require(digest(args.bundle / "manifest.json") == env["PCC_CI_STAGE1_MANIFEST_SHA256"],
                "admission manifest changed during tests")
        require(all(digest(ROOT / name) == row["sha256"] for name, row in manifest["files"].items()),
                "admitted artifacts changed during tests")
    except BaseException as error:
        receipt.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        save(out / "receipt.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marker", choices=tuple(TIMEOUTS), required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--platform", choices=tuple(PLATFORMS), required=True)
    parser.add_argument("--research-source", type=Path)
    parser.add_argument("--partition", choices=PARTITIONS, default="all")
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--owner-report", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args(argv))


if __name__ == "__main__":
    main()
