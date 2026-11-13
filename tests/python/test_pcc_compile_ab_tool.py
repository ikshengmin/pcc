from __future__ import annotations

import importlib.util
import difflib
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_pcc_compile_ab.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("run_pcc_compile_ab", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(wall, cpu, instructions, cycles, rss, footprint):
    return {
        "metrics": {
            "wall_s": wall,
            "cpu_s": cpu,
            "instructions": instructions,
            "cycles": cycles,
            "max_rss_bytes": rss,
            "peak_footprint_bytes": footprint,
        }
    }


def _write_build_evidence(
    tool, tmp_path, compilers, runtime, *, shared_logical_source_root=True,
    source_variable=None,
):
    if source_variable is None:
        source_variable = tool.PRIMARY_SOURCE
    canonical_source = tmp_path / "canonical-source"
    canonical_build_root = tmp_path / "canonical-stage-build"

    def populate_source(root, primary_text):
        (root / "pcc" / "ir").mkdir(parents=True)
        (root / "pcc" / "driver").mkdir()
        (root / "scripts").mkdir()
        (root / "utils" / "fake_libc_include").mkdir(parents=True)
        (root / tool.PRIMARY_SOURCE).write_text("exact = True\n", encoding="utf-8")
        variable = root / source_variable
        variable.parent.mkdir(parents=True, exist_ok=True)
        variable.write_text(primary_text, encoding="utf-8")
        (root / "pcc" / "driver" / "cli_core.py").write_text("common = 1\n", encoding="utf-8")
        (root / "pcc" / "__main__.py").write_text("main = 1\n", encoding="utf-8")
        (root / "AGENTS.md").write_text("# frozen\n", encoding="utf-8")
        (root / "pyproject.toml").write_text("[project]\nname='pcc'\n", encoding="utf-8")
        (root / "scripts" / "bootstrap.py").write_text(
            "# bootstrap\n", encoding="utf-8"
        )
        (root / "scripts" / "run_pcc_native_deferred.py").write_text(
            "# native deferred wrapper\n", encoding="utf-8"
        )
        (root / "scripts" / "run_pcc_deferred_link.py").write_text(
            "# deferred link\n", encoding="utf-8"
        )
        (root / "scripts" / "pcc_link_macho.py").write_text("# macho\n", encoding="utf-8")
        (root / "scripts" / "pcc_link_elf.py").write_text("# elf\n", encoding="utf-8")
        (root / "utils" / "fake_libc_include" / "Python.h").write_text(
            "/* fake */\n", encoding="utf-8"
        )

    populate_source(canonical_source, "exact = True\n")
    receipts = []
    snapshot_roots = {}
    runtime_portable = tool._portable_file_receipt(tool._path_receipt(runtime), "test")
    runtime_bundle = {
        "files": {
            runtime.name: {
                "sha256": runtime_portable["sha256"],
                "size_bytes": runtime_portable["size_bytes"],
            }
        },
        "provider": runtime_portable,
        "ar": runtime_portable,
        "manifest_target": "test",
        "manifest_member_count": 1,
        "wheel_target": "test",
        "object_emitter": "test-object-emitter",
        "codegen_checksum": "f" * 64,
        "producer_claim": "binary-integrity-only; producer source closure not proven",
    }
    external_tools = [runtime_portable]
    host_python_runtime = {
        "schema": "pcc.host-python-runtime.v1",
        "test_identity": "same-host-runtime",
    }
    for arm, compiler, primary_text in zip(
        ("candidate", "baseline"),
        compilers,
        ("exact = True\n", "exact = False\n"),
        strict=True,
    ):
        build_root = tmp_path / f"{arm}-build"
        build_root.mkdir()
        logical_build_root = (
            canonical_build_root if shared_logical_source_root else build_root
        )
        built_compiler = logical_build_root / "pcc1"
        logical_source_root = logical_build_root / "source-snapshot"
        source_root = build_root / "source-snapshot"
        populate_source(source_root, primary_text)
        snapshot_roots[arm] = source_root
        files = {
            path.relative_to(source_root).as_posix(): tool.sha256_path(path)
            for path in tool.build_source_files(source_root)
        }
        source_manifest = build_root / "source-manifest.json"
        source_manifest.write_text(
            json.dumps(
                {
                    "schema": tool.SOURCE_MANIFEST_SCHEMA,
                    "bootstrap_source_sha256": tool._source_manifest_identity(files),
                    "files": files,
                }
            ),
            encoding="utf-8",
        )
        time_output = build_root / "stage1.time"
        time_output.write_text(
            "real 1.0\nuser 0.8\nsys 0.1\n"
            "100 maximum resident set size\n100 instructions retired\n"
            "100 cycles elapsed\n100 peak memory footprint\n",
            encoding="utf-8",
        )
        (build_root / "stage1.profile.json").write_text("{}\n", encoding="utf-8")
        (build_root / "stage1.stdout").write_text("", encoding="utf-8")
        (build_root / "stage1.stderr").write_text("", encoding="utf-8")
        producer_dir = build_root / "producer-tools"
        producer_dir.mkdir()
        producer_tools = {}
        for name in ("run_pcc_stage1_build.py", "run_pcc_compile_ab.py"):
            producer = producer_dir / name
            producer.write_text("# same producer\n", encoding="utf-8")
            producer_tools[name] = {
                "path": "producer-tools/" + name,
                "sha256": tool.sha256_path(producer),
                "size_bytes": producer.stat().st_size,
            }
        stage_result = build_root / "stage1-result.json"
        artifacts = {}
        for name, relative in (
            ("time", "stage1.time"),
            ("profile", "stage1.profile.json"),
            ("stdout", "stage1.stdout"),
            ("stderr", "stage1.stderr"),
        ):
            artifact = build_root / relative
            artifacts[name] = {
                "path": relative,
                "sha256": tool.sha256_path(artifact),
                "size_bytes": artifact.stat().st_size,
            }
        stage_result.write_text(
            json.dumps(
                {
                    "schema": "pcc.stage1-build-result.v1",
                    "returncode": 0,
                    "compiler": str(built_compiler),
                    "compiler_sha256": tool.sha256_path(compiler),
                    "compiler_size_bytes": compiler.stat().st_size,
                    "metrics": tool.parse_time_output(time_output.read_text(encoding="utf-8")),
                    "profile_sha256": tool.sha256_path(
                        build_root / "stage1.profile.json"
                    ),
                    "stdout_sha256": tool.sha256_path(build_root / "stage1.stdout"),
                    "stderr_sha256": tool.sha256_path(build_root / "stage1.stderr"),
                    "linkage": {
                        "checked": True,
                        "links_libpython": False,
                        "links_llvm": False,
                        "stdout": "strict",
                    },
                    "artifacts": artifacts,
                }
            ),
            encoding="utf-8",
        )
        command = [
            str(tool.TIME_BINARY),
            "-lp",
            "-o",
            str(logical_build_root / "stage1.time"),
            sys.executable,
            "-m",
            "pcc",
            "--profile-json",
            str(logical_build_root / "stage1.profile.json"),
            "--ir-scaffold=on",
            "--backend",
            "self",
            str(logical_source_root / "pcc" / "__main__.py"),
            "-o",
            str(built_compiler),
        ]
        environment = {
            "PCC_RUNTIME_ARCHIVE": str(runtime),
            "PCC_SOURCE_ROOT": str(logical_source_root),
            "PCC_REPO_ROOT": str(logical_source_root),
            "PCC_HOST_PYTHON": sys.executable,
            "PCC_GC_BACKEND": "0",
            "PCC_RUNTIME_HIGH": "py",
            "PCC_SELF_LINK": "pcc",
            "PCC_SELF_BACKEND_PUBLISH_SYNC": "1",
            "PCC_PYTHON_IR_PASSES": "off",
            "PCC_PYTHON_IR_PASS_JOBS": "1",
            "PCC_PY_FRONTEND_IR_CACHE": "0",
            "PCC_SELF_BACKEND_OBJECT_CACHE": "0",
            "PCC_DEBUG_IR_CALL": "0",
            "PCC_DEBUG_IR_RENDER": "0",
            "PYTHONHASHSEED": "0",
            "PCC_PY_FRONTEND_JOBS": "10",
            "PCC_BOOTSTRAP_STAGE1_PY_FRONTEND_JOBS": "10",
            "PCC_SELF_BACKEND_JOBS": "8",
            "PCC_MACHO_LINK_JOBS": "8",
            "HOME": str(logical_build_root / "private-state" / "home"),
        }
        receipt = build_root / "build-receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "schema": tool.BUILD_RECEIPT_SCHEMA,
                    "arm": arm,
                    "status": "SUCCEEDED",
                    "compiler_sha256": tool.sha256_path(compiler),
                    "compiler_size_bytes": compiler.stat().st_size,
                    "runtime_archive_sha256": tool.sha256_path(runtime),
                    "bootstrap_source_sha256": tool._source_manifest_identity(files),
                    "primary_source_sha256": files[tool.PRIMARY_SOURCE],
                    "origin_source_root": str(canonical_source),
                    "logical_source_root": str(logical_source_root),
                    "source_manifest": source_manifest.name,
                    "source_manifest_sha256": tool.sha256_path(source_manifest),
                    "source_snapshot": source_root.name,
                    "command": command,
                    "command_sha256": tool._canonical_sha256(command),
                    "environment": environment,
                    "environment_sha256": tool._canonical_sha256(environment),
                    "cwd": str(logical_source_root),
                    "stage_result": stage_result.name,
                    "stage_result_sha256": tool.sha256_path(stage_result),
                    "runtime_bundle": runtime_bundle,
                    "runtime_bundle_sha256": tool._canonical_sha256(runtime_bundle),
                    "external_tools": external_tools,
                    "external_tools_sha256": tool._canonical_sha256(external_tools),
                    "producer_tools": producer_tools,
                    "producer_tools_sha256": tool._canonical_sha256(producer_tools),
                    "host_python_runtime": host_python_runtime,
                    "host_python_runtime_sha256": tool._canonical_sha256(
                        host_python_runtime
                    ),
                }
            ),
            encoding="utf-8",
        )
        tool._seal_source_snapshot(source_root, arm + " fixture source snapshot")
        receipts.append(receipt)
    source_diff = tmp_path / "candidate-vs-baseline.diff"
    baseline_text = (snapshot_roots["baseline"] / source_variable).read_text(
        encoding="utf-8"
    )
    candidate_text = (snapshot_roots["candidate"] / source_variable).read_text(
        encoding="utf-8"
    )
    source_diff.write_text(
        "".join(
            difflib.unified_diff(
                baseline_text.splitlines(keepends=True),
                candidate_text.splitlines(keepends=True),
                fromfile="baseline/" + source_variable,
                tofile="candidate/" + source_variable,
                lineterm="\n",
            )
        ),
        encoding="utf-8",
    )
    return receipts, source_diff


def _rewrite_stage_result(tool, receipt_path, mutate):
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    stage_result_path = receipt_path.parent / receipt["stage_result"]
    stage_result = json.loads(stage_result_path.read_text(encoding="utf-8"))
    mutate(stage_result)
    stage_result_path.write_text(json.dumps(stage_result), encoding="utf-8")
    receipt["stage_result_sha256"] = tool.sha256_path(stage_result_path)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")


def test_time_parser_uses_labels_not_line_positions():
    tool = _load_tool()
    metrics = tool.parse_time_output(
        "warning before metrics\n"
        "real 41.69\n"
        "user 39.02\n"
        "sys 1.82\n"
        "          3568123904  maximum resident set size\n"
        "         39468301366  instructions retired\n"
        "         10749673296  cycles elapsed\n"
        "           281166520  peak memory footprint\n"
    )
    assert metrics == {
        "wall_s": 41.69,
        "user_s": 39.02,
        "system_s": 1.82,
        "max_rss_bytes": 3_568_123_904,
        "instructions": 39_468_301_366,
        "cycles": 10_749_673_296,
        "peak_footprint_bytes": 281_166_520,
        "cpu_s": pytest.approx(40.84),
    }
    with pytest.raises(tool.CompileABError, match="expected one wall_s, found 2"):
        tool.parse_time_output(
            "real 1.0\nreal 2.0\nuser 1.0\nsys 1.0\n"
            "1 maximum resident set size\n1 instructions retired\n"
            "1 cycles elapsed\n1 peak memory footprint\n"
        )


def test_measurement_environment_drops_ambient_pcc_state(monkeypatch, tmp_path):
    tool = _load_tool()
    monkeypatch.setenv("PCC_FORCE_GENERIC_IR_CALLS", "1")
    monkeypatch.setenv("PCC_BOOTSTRAP_PROFILE_DIR", "/should/not/leak")
    monkeypatch.setenv("PCC_GC_BACKEND", "4")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", "/should/not/leak/pycache")
    host_root = tmp_path / "host-source"
    host_root.mkdir()
    env = tool._measurement_env(
        tmp_path / "runtime.a",
        0,
        host_source_root=host_root,
        host_python=Path(sys.executable),
        private_root=tmp_path / "private",
        frontend_jobs=10,
        self_backend_jobs=8,
    )
    assert env["PCC_GC_BACKEND"] == "0"
    assert env["PCC_PY_FRONTEND_IR_CACHE"] == "0"
    assert env["PCC_SELF_BACKEND_OBJECT_CACHE"] == "0"
    assert "PCC_FORCE_GENERIC_IR_CALLS" not in env
    assert "PCC_BOOTSTRAP_PROFILE_DIR" not in env
    assert env["PCC_SOURCE_ROOT"] == str(host_root)
    assert env["PCC_HOST_PYTHON"] == str(Path(sys.executable))
    assert env["HOME"].startswith(str(tmp_path))
    assert env["PYTHONHASHSEED"] == "0"
    assert "PYTHONDONTWRITEBYTECODE" not in env
    assert Path(env["PYTHONPYCACHEPREFIX"]) == tmp_path / "private" / "pycache"
    assert Path(env["PYTHONPYCACHEPREFIX"]).is_dir()
    assert Path(env["PCC_RUNTIME_CC"]).is_absolute()
    assert env["PCC_RUNTIME_CC"] == "/usr/bin/false"
    assert env["PCC_SELF_LINK"] == "pcc"
    assert env["PCC_SELF_BACKEND_PUBLISH_SYNC"] == "1"
    assert env["PCC_PY_FRONTEND_JOBS"] == "10"
    assert env["PCC_SELF_BACKEND_JOBS"] == "8"
    assert env["PCC_MACHO_LINK_JOBS"] == "8"


def test_measurement_environment_rejects_relative_path(monkeypatch, tmp_path):
    tool = _load_tool()
    monkeypatch.setenv("PATH", ".:/usr/bin")
    with pytest.raises(tool.CompileABError, match="absolute directories"):
        tool._measurement_env(
            tmp_path / "runtime.a",
            0,
            host_source_root=tmp_path / "host-source",
            host_python=Path(sys.executable),
            private_root=tmp_path / "private",
            frontend_jobs=10,
            self_backend_jobs=8,
        )


def test_host_source_closure_contains_link_and_fake_libc_owners():
    tool = _load_tool()
    names = {
        path.relative_to(tool.REPO_ROOT).as_posix()
        for path in tool.build_source_files(tool.REPO_ROOT)
    }
    assert {
        "AGENTS.md",
        "scripts/run_pcc_deferred_link.py",
        "scripts/run_pcc_native_deferred.py",
        "scripts/pcc_link_macho.py",
        "scripts/pcc_link_elf.py",
        "utils/fake_libc_include/Python.h",
    } <= names


def test_host_python_runtime_tree_identity_changes_with_module_bytes(tmp_path):
    tool = _load_tool()
    runtime_root = tmp_path / "stdlib"
    runtime_root.mkdir()
    module = runtime_root / "worker.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    before = tool._runtime_tree_evidence(runtime_root)
    module.write_text("VALUE = 2\n", encoding="utf-8")
    after = tool._runtime_tree_evidence(runtime_root)
    assert before["file_count"] == after["file_count"] == 1
    assert before["content_sha256"] != after["content_sha256"]


@pytest.mark.parametrize(
    ("dependency", "owner"),
    (
        ("/usr/local/lib/libpython3.13.dylib", "libpython"),
        ("/Library/Frameworks/Python.framework/Versions/3.13/Python", "libpython"),
        ("/usr/local/lib/libLLVM.dylib", "LLVM"),
    ),
)
def test_linkage_rejects_library_and_framework_python(
    dependency, owner, tmp_path, monkeypatch
):
    tool = _load_tool()
    otool = tmp_path / "otool"
    otool.write_text("", encoding="utf-8")
    monkeypatch.setattr(tool, "OTOOL_BINARY", otool)
    monkeypatch.setattr(
        tool,
        "_run_process",
        lambda command, *, timeout, env, cwd: tool.subprocess.CompletedProcess(
            command, 0, "probe:\n\t" + dependency + " (compatibility 1.0.0)\n", ""
        ),
    )
    with pytest.raises(tool.CompileABError, match="forbidden " + owner):
        tool._linkage(
            tmp_path / "probe", timeout=1, env={}, cwd=tmp_path
        )


def test_claim_platform_rejects_non_arm64(monkeypatch):
    tool = _load_tool()
    monkeypatch.setattr(tool.sys, "platform", "darwin")
    monkeypatch.setattr(tool.platform, "machine", lambda: "x86_64")
    with pytest.raises(tool.CompileABError, match="Darwin arm64"):
        tool.require_claim_platform()


def test_pair_order_is_predeclared_and_alternating():
    tool = _load_tool()
    assert [tool.pair_order(index) for index in range(1, 5)] == [
        ("candidate", "baseline"),
        ("baseline", "candidate"),
        ("candidate", "baseline"),
        ("baseline", "candidate"),
    ]
    assert tool._verdict_exit_code({"verdict": "ACCEPT"}) == 0
    assert tool._verdict_exit_code({"verdict": "DENY"}) == 2


def test_summary_uses_paired_wall_ratio_and_rejects_resource_regression():
    tool = _load_tool()
    pairs = [
        {
            "candidate": _row(90.0, 80.0, 90, 90, 100, 100),
            "baseline": _row(100.0, 81.0, 100, 100, 100, 100),
        },
        {
            "candidate": _row(100.0, 80.0, 90, 90, 100, 100),
            "baseline": _row(108.0, 81.0, 100, 100, 100, 100),
        },
        {
            "candidate": _row(110.0, 80.0, 90, 90, 100, 100),
            "baseline": _row(121.0, 81.0, 100, 100, 100, 100),
        },
    ]
    summary = tool.summarize_pairs(
        pairs,
        min_speedup_ratio=1.08,
        max_compute_regression_ratio=1.0,
        max_memory_regression_ratio=1.02,
    )
    assert summary["paired_median_wall_speedup"] == pytest.approx(1.1)
    assert summary["median_wall_speedup"] == pytest.approx(1.08)
    assert summary["verdict"] == "ACCEPT"

    pairs[0]["candidate"]["metrics"]["cycles"] = 200
    pairs[1]["candidate"]["metrics"]["cycles"] = 200
    summary = tool.summarize_pairs(
        pairs,
        min_speedup_ratio=1.08,
        max_compute_regression_ratio=1.0,
        max_memory_regression_ratio=1.02,
    )
    assert summary["verdict"] == "DENY"
    assert summary["resource_regressions"] == [
        "cycles=median:2.000000x/paired:2.000000x/max:2.000000x"
    ]


def test_summary_denies_when_one_counterbalanced_pair_is_slower():
    tool = _load_tool()
    pairs = [
        {"candidate": _row(90, 80, 90, 90, 100, 100), "baseline": _row(100, 80, 90, 90, 100, 100)},
        {"candidate": _row(90, 80, 90, 90, 100, 100), "baseline": _row(100, 80, 90, 90, 100, 100)},
        {"candidate": _row(90, 80, 90, 90, 100, 100), "baseline": _row(100, 80, 90, 90, 100, 100)},
        {"candidate": _row(101, 80, 90, 90, 100, 100), "baseline": _row(100, 80, 90, 90, 100, 100)},
    ]
    summary = tool.summarize_pairs(
        pairs,
        min_speedup_ratio=1.08,
        max_compute_regression_ratio=1.0,
        max_memory_regression_ratio=1.02,
    )
    assert summary["paired_median_wall_speedup"] > 1.08
    assert summary["paired_wall_speedup_range"][0] < 1.0
    assert summary["verdict"] == "DENY"


def test_summary_denies_when_one_pair_exceeds_resource_ceiling():
    tool = _load_tool()
    pairs = [
        {
            "candidate": _row(90, cpu, 90, 90, 100, 100),
            "baseline": _row(100, 100, 100, 100, 100, 100),
        }
        for cpu in (90, 90, 90, 101)
    ]
    summary = tool.summarize_pairs(
        pairs,
        min_speedup_ratio=1.08,
        max_compute_regression_ratio=1.0,
        max_memory_regression_ratio=1.02,
    )
    assert summary["paired_median_resource_ratios"]["cpu_s"] < 1.0
    assert summary["paired_max_resource_ratios"]["cpu_s"] == pytest.approx(1.01)
    assert summary["verdict"] == "DENY"


def test_summary_requires_both_arm_medians_and_paired_median_to_pass():
    tool = _load_tool()
    pairs = [
        {
            "candidate": _row(10.0, 10, 10, 10, 10, 10),
            "baseline": _row(9.0, 10, 10, 10, 10, 10),
        },
        {
            "candidate": _row(100.0, 10, 10, 10, 10, 10),
            "baseline": _row(108.0, 10, 10, 10, 10, 10),
        },
        {
            "candidate": _row(1000.0, 10, 10, 10, 10, 10),
            "baseline": _row(1070.0, 10, 10, 10, 10, 10),
        },
    ]
    summary = tool.summarize_pairs(
        pairs,
        min_speedup_ratio=1.08,
        max_compute_regression_ratio=1.0,
        max_memory_regression_ratio=1.02,
    )
    assert summary["median_wall_speedup"] == pytest.approx(1.08)
    assert summary["paired_median_wall_speedup"] == pytest.approx(1.07)
    assert summary["verdict"] == "DENY"

    reverse_pairs = [
        {
            "candidate": _row(10.0, 10, 10, 10, 10, 10),
            "baseline": _row(11.0, 10, 10, 10, 10, 10),
        },
        {
            "candidate": _row(100.0, 10, 10, 10, 10, 10),
            "baseline": _row(107.0, 10, 10, 10, 10, 10),
        },
        {
            "candidate": _row(1000.0, 10, 10, 10, 10, 10),
            "baseline": _row(1080.0, 10, 10, 10, 10, 10),
        },
    ]
    summary = tool.summarize_pairs(
        reverse_pairs,
        min_speedup_ratio=1.08,
        max_compute_regression_ratio=1.0,
        max_memory_regression_ratio=1.02,
    )
    assert summary["median_wall_speedup"] == pytest.approx(1.07)
    assert summary["paired_median_wall_speedup"] == pytest.approx(1.08)
    assert summary["verdict"] == "DENY"


def test_resource_guards_require_arm_and_paired_medians_independently():
    tool = _load_tool()
    arm_only = [
        {
            "candidate": _row(1, candidate, 1, 1, 1, 1),
            "baseline": _row(2, baseline, 1, 1, 1, 1),
        }
        for candidate, baseline in ((10, 11), (100, 101), (1000, 5))
    ]
    summary = tool.summarize_pairs(
        arm_only,
        min_speedup_ratio=1.01,
        max_compute_regression_ratio=1.0,
        max_memory_regression_ratio=1.02,
    )
    assert summary["paired_median_resource_ratios"]["cpu_s"] < 1.0
    assert summary["candidate_medians"]["cpu_s"] > summary["baseline_medians"]["cpu_s"]
    assert any(item.startswith("cpu_s=") for item in summary["resource_regressions"])

    paired_only = [
        {
            "candidate": _row(1, candidate, 1, 1, 1, 1),
            "baseline": _row(2, baseline, 1, 1, 1, 1),
        }
        for candidate, baseline in ((1000, 500), (10, 5), (100, 101))
    ]
    summary = tool.summarize_pairs(
        paired_only,
        min_speedup_ratio=1.01,
        max_compute_regression_ratio=1.0,
        max_memory_regression_ratio=1.02,
    )
    assert summary["paired_median_resource_ratios"]["cpu_s"] > 1.0
    assert summary["candidate_medians"]["cpu_s"] < summary["baseline_medians"]["cpu_s"]
    assert any(item.startswith("cpu_s=") for item in summary["resource_regressions"])


def test_thresholds_reject_nan():
    tool = _load_tool()
    with pytest.raises(tool.CompileABError, match="must be finite"):
        tool.summarize_pairs(
            [{"candidate": _row(1, 1, 1, 1, 1, 1), "baseline": _row(2, 1, 1, 1, 1, 1)}],
            min_speedup_ratio=1.01,
            max_compute_regression_ratio=float("nan"),
            max_memory_regression_ratio=1.02,
        )


def test_timeout_kills_child_that_ignores_term_and_holds_pipe(tmp_path):
    tool = _load_tool()
    child_pid = tmp_path / "child.pid"
    child_code = (
        "import os, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "open(sys.argv[1], 'w').write(str(os.getpid())); "
        "print('holding inherited pipe', flush=True); time.sleep(60)"
    )
    parent_code = (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); "
        "sys.exit(0)"
    )
    started = time.monotonic()
    with pytest.raises(tool.CompileABError, match="timed out"):
        tool._run_process(
            [sys.executable, "-c", parent_code, child_code, str(child_pid)],
            timeout=1,
            env=os.environ.copy(),
        )
    assert time.monotonic() - started < 6
    pid = int(child_pid.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"timed-out child {pid} survived process-group kill")


def test_successful_parent_cannot_leave_a_residual_child(tmp_path):
    tool = _load_tool()
    child_pid = tmp_path / "residual-child.pid"
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess, sys; "
        "child=subprocess.Popen([sys.executable, '-c', sys.argv[1]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "open(sys.argv[2], 'w').write(str(child.pid))"
    )
    with pytest.raises(tool.CompileABError, match="returned while a child process remained"):
        tool._run_process(
            [sys.executable, "-c", parent_code, child_code, str(child_pid)],
            timeout=5,
            env=os.environ.copy(),
        )
    pid = int(child_pid.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"normal-return residual child {pid} survived cleanup")


def test_direct_run_interrupt_cleans_process_group(monkeypatch):
    tool = _load_tool()
    events = []

    class FakeProcess:
        pid = 123
        returncode = None

        def communicate(self, timeout):
            raise KeyboardInterrupt

    monkeypatch.setattr(tool.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(
        tool, "_terminate_process_group", lambda process: events.append(process.pid)
    )
    with pytest.raises(KeyboardInterrupt):
        tool._run_process(["probe"], timeout=1, env={}, cwd=tool.REPO_ROOT)
    assert events == [123]


def test_runtime_snapshot_lock_is_archive_scoped_and_released(tmp_path):
    tool = _load_tool()
    first = tmp_path / "first.a"
    second = tmp_path / "second.a"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    first_lock = Path(str(first) + ".build.lock")
    with tool._runtime_snapshot_lock(first):
        assert first_lock.is_dir()
        with pytest.raises(tool.CompileABError, match="build lock is held"):
            with tool._runtime_snapshot_lock(first):
                pass
        with tool._runtime_snapshot_lock(second):
            assert Path(str(second) + ".build.lock").is_dir()
    assert not first_lock.exists()
    assert not Path(str(second) + ".build.lock").exists()


def test_runner_requires_distinct_inputs_and_exact_oracles(tmp_path):
    tool = _load_tool()
    with pytest.raises(tool.CompileABError, match="distinct content hashes"):
        tool._require_distinct_input_hashes(["same", "same", "different"])
    parser = tool._parser()
    base = [
        "--candidate", str(tmp_path / "a"),
        "--baseline", str(tmp_path / "b"),
        "--runtime-archive", str(tmp_path / "runtime.a"),
        "--warmup", str(tmp_path / "warmup.py"),
        "--output-dir", str(tmp_path / "out"),
        "--input", str(tmp_path / "one.py"),
        "--input", str(tmp_path / "two.py"),
        "--input", str(tmp_path / "three.py"),
    ]
    with pytest.raises(SystemExit):
        parser.parse_args(base)
    with pytest.raises(tool.CompileABError, match="child of build"):
        tool._validate_output_dir(str(tool.REPO_ROOT / "tests" / "accidental-output"))
    with pytest.raises(tool.CompileABError, match="child of build"):
        tool._validate_output_dir(str(tool.REPO_ROOT / "projects" / "output"))
    assert tool._validate_output_dir(
        str(tool.REPO_ROOT / "build" / "claim-grade-output")
    ) == tool.REPO_ROOT / "build" / "claim-grade-output"


def test_failure_status_only_updates_the_claimed_output(tmp_path):
    tool = _load_tool()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / tool.RUN_OWNER_FILE).write_text(
        json.dumps({"run_token": "somebody-else"}), encoding="utf-8"
    )
    manifest = foreign / "manifest.json"
    manifest.write_text('{"status":"KEEP"}\n', encoding="utf-8")
    before = manifest.read_bytes()
    tool._mark_owned_run_failure(
        foreign, run_token="ours", status="ERROR", error="must not write"
    )
    assert manifest.read_bytes() == before

    owned = tmp_path / "owned"
    tool._claim_output_directory(owned, harness="test", run_token="ours")
    tool._mark_owned_run_failure(
        owned, run_token="ours", status="ERROR", error="expected"
    )
    recorded = json.loads((owned / "manifest.json").read_text(encoding="utf-8"))
    assert recorded["status"] == "ERROR"
    assert recorded["run_token"] == "ours"


@pytest.mark.parametrize("source_variable", [
    "pcc/ir/ir.py", "pcc/frontends/python/pipeline_import_scan.py",
])
def test_build_receipts_bind_compilers_runtime_manifests_and_single_diff(
    tmp_path, source_variable,
):
    tool = _load_tool()
    compilers = []
    for arm in ("candidate", "baseline"):
        compiler = tmp_path / arm
        compiler.write_text(arm, encoding="utf-8")
        compilers.append(compiler)
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"runtime")
    runtime_sha = tool.sha256_path(runtime)
    receipts, source_diff = _write_build_evidence(
        tool, tmp_path, compilers, runtime, source_variable=source_variable,
    )
    loaded = [
        tool._load_build_receipt(
            receipt,
            arm=arm,
            compiler_sha256=tool.sha256_path(compiler),
            runtime_sha256=runtime_sha,
            compiler_size_bytes=compiler.stat().st_size,
        )
        for receipt, arm, compiler in zip(
            receipts, ("candidate", "baseline"), compilers, strict=True
        )
    ]
    single = tool._validate_single_variable(
        loaded[0], loaded[1], [source_variable], source_diff
    )
    assert single["changed_sources"] == [source_variable]
    assert single["allowed_changed_sources"] == [source_variable]
    if source_variable == tool.IMPORT_SCAN_SOURCE:
        assert loaded[0]["receipt"]["primary_source_sha256"] == (
            loaded[1]["receipt"]["primary_source_sha256"]
        )
    assert single["logical_source_root"].endswith(
        "/canonical-stage-build/source-snapshot"
    )
    with pytest.raises(tool.CompileABError, match="different compiler bytes"):
        tool._load_build_receipt(
            receipts[0],
            arm="candidate",
            compiler_sha256="0" * 64,
            runtime_sha256=runtime_sha,
            compiler_size_bytes=compilers[0].stat().st_size,
        )
    with pytest.raises(tool.CompileABError, match="different compiler size"):
        tool._load_build_receipt(
            receipts[0],
            arm="candidate",
            compiler_sha256=tool.sha256_path(compilers[0]),
            runtime_sha256=runtime_sha,
            compiler_size_bytes=compilers[0].stat().st_size + 1,
        )

    mismatched_producer = copy.deepcopy(loaded[1])
    mismatched_producer["producer_tools"]["run_pcc_stage1_build.py"][
        "sha256"
    ] = "f" * 64
    with pytest.raises(tool.CompileABError, match="different build producer"):
        tool._validate_single_variable(
            loaded[0], mismatched_producer, [source_variable], source_diff
        )

    mismatched_env = copy.deepcopy(loaded[1])
    mismatched_env["receipt"]["environment"]["PCC_PY_FRONTEND_JOBS"] = "9"
    with pytest.raises(tool.CompileABError, match="build environments differ"):
        tool._validate_single_variable(
            loaded[0], mismatched_env, [source_variable], source_diff
        )

    source_diff.write_text("not the canonical source diff\n", encoding="utf-8")
    with pytest.raises(tool.CompileABError, match="canonical diff"):
        tool._validate_single_variable(
            loaded[0], loaded[1], [source_variable], source_diff
        )


@pytest.mark.parametrize("source_variable", [
    "pcc/ir/ir.py", "pcc/frontends/python/pipeline_import_scan.py",
])
def test_single_variable_rejects_distinct_absolute_consumed_source_roots(
    tmp_path, source_variable,
):
    tool = _load_tool()
    compilers = []
    for arm in ("candidate", "baseline"):
        compiler = tmp_path / arm
        compiler.write_text(arm, encoding="utf-8")
        compilers.append(compiler)
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"runtime")
    receipts, source_diff = _write_build_evidence(
        tool,
        tmp_path / "per-arm-roots",
        compilers,
        runtime,
        shared_logical_source_root=False,
        source_variable=source_variable,
    )
    loaded = [
        tool._load_build_receipt(
            receipt,
            arm=arm,
            compiler_sha256=tool.sha256_path(compiler),
            runtime_sha256=tool.sha256_path(runtime),
            compiler_size_bytes=compiler.stat().st_size,
        )
        for receipt, arm, compiler in zip(
            receipts, ("candidate", "baseline"), compilers, strict=True
        )
    ]
    with pytest.raises(tool.CompileABError, match="different absolute source roots"):
        tool._validate_single_variable(
            loaded[0], loaded[1], [source_variable], source_diff
        )


@pytest.mark.parametrize("allowed", [
    ["pcc/driver/cli_core.py"],
    ["scripts/run_pcc_native_deferred.py"],
    ["pcc/runtime/py/sample.py"],
    ["pcc/frontends/python/../python/pipeline_import_scan.py"],
    ["pcc/ir/ir.py", "pcc/frontends/python/pipeline_import_scan.py"],
    ["pcc/frontends/python/pipeline_import_scan.py", "scripts/bootstrap.py"],
    ["pcc/frontends/python/pipeline_import_scan.py", "pcc/runtime/py/sample.py"],
])
def test_single_variable_rejects_unadmitted_or_mixed_sources(tmp_path, allowed):
    tool = _load_tool()
    baseline = {"source_manifest": {"files": {name: "a" * 64 for name in allowed}}}
    candidate = {"source_manifest": {"files": {name: "b" * 64 for name in allowed}}}
    with pytest.raises(tool.CompileABError, match="only admits exactly one"):
        tool._validate_single_variable(
            candidate, baseline, allowed, tmp_path / "unused.diff",
        )


@pytest.mark.parametrize("allowed", [
    [],
    ["pcc/frontends/python/pipeline_import_scan.py"] * 2,
])
def test_scanner_single_variable_rejects_empty_or_duplicate_allowlist(
    tmp_path, allowed,
):
    tool = _load_tool()
    with pytest.raises(tool.CompileABError, match="non-empty and unique"):
        tool._validate_single_variable({}, {}, allowed, tmp_path / "unused.diff")


@pytest.mark.parametrize("extra_source", [
    "pcc/ir/ir.py",
    "pcc/driver/cli_core.py",
    "scripts/run_pcc_native_deferred.py",
    "pcc/runtime/py/sample.py",
])
def test_scanner_single_variable_rejects_undeclared_source_changes(
    tmp_path, extra_source,
):
    tool = _load_tool()
    paths = (tool.IMPORT_SCAN_SOURCE, extra_source)
    baseline = {"source_manifest": {"files": {name: "a" * 64 for name in paths}}}
    candidate = {"source_manifest": {"files": {name: "b" * 64 for name in paths}}}
    with pytest.raises(tool.CompileABError, match="not single-variable"):
        tool._validate_single_variable(
            candidate, baseline, [tool.IMPORT_SCAN_SOURCE], tmp_path / "unused.diff",
        )


@pytest.mark.parametrize("field,error", [
    ("origin_source_root", "same canonical source path"),
    ("build_output_root", "same canonical build-output alias"),
    ("runtime_bundle", "different runtime bundle"),
    ("external_tools", "different external tool"),
    ("producer_tools", "different build producer"),
    ("host_python_runtime", "different host Python runtimes"),
])
def test_scanner_single_variable_preserves_identity_gates(tmp_path, field, error):
    tool = _load_tool()
    common = {
        "origin_source_root": "/canonical-source",
        "source_root": "/canonical-build/source-snapshot",
        "logical_source_root": "/canonical-build/source-snapshot",
        "build_output_root": "/canonical-build",
        "runtime_bundle": {"sha256": "c" * 64},
        "external_tools": [{"sha256": "d" * 64}],
        "producer_tools": {"sha256": "e" * 64},
        "host_python_runtime": {"sha256": "f" * 64},
    }
    candidate = copy.deepcopy(common)
    baseline = copy.deepcopy(common)
    candidate["source_manifest"] = {"files": {tool.IMPORT_SCAN_SOURCE: "a" * 64}}
    baseline["source_manifest"] = {"files": {tool.IMPORT_SCAN_SOURCE: "b" * 64}}
    candidate[field] = "different"
    with pytest.raises(tool.CompileABError, match=error):
        tool._validate_single_variable(
            candidate, baseline, [tool.IMPORT_SCAN_SOURCE], tmp_path / "unused.diff",
        )


def test_build_receipt_v3_is_self_contained_after_original_build_is_removed(tmp_path):
    tool = _load_tool()
    compilers = []
    for arm in ("candidate", "baseline"):
        compiler = tmp_path / arm
        compiler.write_text(arm, encoding="utf-8")
        compilers.append(compiler)
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"runtime")
    receipts, _source_diff = _write_build_evidence(
        tool, tmp_path / "builds", compilers, runtime
    )
    bundle = tool._snapshot_build_evidence(
        receipts[0], tmp_path / "localized-evidence", "candidate"
    )
    copied_receipt = Path(bundle["receipt"])
    old_receipt = json.loads(copied_receipt.read_text(encoding="utf-8"))
    receipts[0].parent.rename(tmp_path / "retired-candidate-build")
    (tmp_path / "builds" / "canonical-source").rename(
        tmp_path / "retired-canonical-source"
    )
    loaded = tool._load_build_receipt(
        copied_receipt,
        arm="candidate",
        compiler_sha256=tool.sha256_path(compilers[0]),
        runtime_sha256=tool.sha256_path(runtime),
        compiler_size_bytes=compilers[0].stat().st_size,
    )
    assert loaded["receipt"] == old_receipt
    assert not Path(loaded["source_root"]).exists()
    tool._verify_build_evidence(bundle)


def test_build_receipt_v2_fails_closed(tmp_path):
    tool = _load_tool()
    compilers = []
    for arm in ("candidate", "baseline"):
        compiler = tmp_path / arm
        compiler.write_text(arm, encoding="utf-8")
        compilers.append(compiler)
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"runtime")
    receipts, _source_diff = _write_build_evidence(
        tool, tmp_path / "builds", compilers, runtime
    )
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    receipt["schema"] = "pcc.stage1-build-receipt.v2"
    receipt.pop("origin_source_root")
    receipt.pop("logical_source_root")
    receipts[0].write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(tool.CompileABError, match="fields mismatch"):
        tool._load_build_receipt(
            receipts[0],
            arm="candidate",
            compiler_sha256=tool.sha256_path(compilers[0]),
            runtime_sha256=tool.sha256_path(runtime),
            compiler_size_bytes=compilers[0].stat().st_size,
        )


def test_build_receipt_rejects_origin_consumption_and_output_overlap(tmp_path):
    tool = _load_tool()
    compilers = []
    for arm in ("candidate", "baseline"):
        compiler = tmp_path / arm
        compiler.write_text(arm, encoding="utf-8")
        compilers.append(compiler)
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"runtime")
    receipts, _source_diff = _write_build_evidence(
        tool, tmp_path / "origin-consumed", compilers, runtime
    )
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    origin = receipt["origin_source_root"]
    receipt["cwd"] = origin
    receipt["logical_source_root"] = origin
    receipt["environment"]["PCC_SOURCE_ROOT"] = origin
    receipt["environment"]["PCC_REPO_ROOT"] = origin
    receipt["environment_sha256"] = tool._canonical_sha256(receipt["environment"])
    receipt["command"][12] = str(Path(origin) / "pcc" / "__main__.py")
    receipt["command_sha256"] = tool._canonical_sha256(receipt["command"])
    receipts[0].write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(tool.CompileABError, match="owned build snapshot"):
        tool._load_build_receipt(
            receipts[0],
            arm="candidate",
            compiler_sha256=tool.sha256_path(compilers[0]),
            runtime_sha256=tool.sha256_path(runtime),
            compiler_size_bytes=compilers[0].stat().st_size,
        )

    receipts, _source_diff = _write_build_evidence(
        tool, tmp_path / "overlap", compilers, runtime
    )
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    receipt["origin_source_root"] = str(
        Path(receipt["logical_source_root"]).parent
    )
    receipts[0].write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(tool.CompileABError, match="overlaps the build output"):
        tool._load_build_receipt(
            receipts[0],
            arm="candidate",
            compiler_sha256=tool.sha256_path(compilers[0]),
            runtime_sha256=tool.sha256_path(runtime),
            compiler_size_bytes=compilers[0].stat().st_size,
        )


@pytest.mark.parametrize(
    ("position", "replacement_name"),
    ((3, "other.time"), (8, "other.profile.json"), (14, "other-pcc1")),
)
def test_build_receipt_binds_exact_command_artifact_paths(
    tmp_path, position, replacement_name
):
    tool = _load_tool()
    compilers = []
    for arm in ("candidate", "baseline"):
        compiler = tmp_path / arm
        compiler.write_text(arm, encoding="utf-8")
        compilers.append(compiler)
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"runtime")
    receipts, _source_diff = _write_build_evidence(
        tool, tmp_path / "exact-command", compilers, runtime
    )
    receipt_path = receipts[0]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    original_build_root = Path(receipt["logical_source_root"]).parent
    receipt["command"][position] = str(original_build_root / replacement_name)
    receipt["command_sha256"] = tool._canonical_sha256(receipt["command"])
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(tool.CompileABError, match="strict stage1 protocol"):
        tool._load_build_receipt(
            receipt_path,
            arm="candidate",
            compiler_sha256=tool.sha256_path(compilers[0]),
            runtime_sha256=tool.sha256_path(runtime),
            compiler_size_bytes=compilers[0].stat().st_size,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("extra-field", "stage result fields mismatch"),
        ("profile-hash", "artifact hashes are inconsistent"),
        ("compiler-size", "not a strict successful build"),
        ("compiler-size-type", "not a strict successful build"),
    ),
)
def test_build_receipt_cross_binds_exact_stage_result(
    tmp_path, mutation, message
):
    tool = _load_tool()
    compilers = []
    for arm in ("candidate", "baseline"):
        compiler = tmp_path / arm
        compiler.write_text(arm, encoding="utf-8")
        compilers.append(compiler)
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"runtime")
    receipts, _source_diff = _write_build_evidence(
        tool, tmp_path / "stage-result-binding", compilers, runtime
    )

    def mutate(stage_result):
        if mutation == "extra-field":
            stage_result["unexpected"] = True
        elif mutation == "profile-hash":
            stage_result["profile_sha256"] = "0" * 64
        elif mutation == "compiler-size":
            stage_result["compiler_size_bytes"] += 1
        else:
            stage_result["compiler_size_bytes"] = float(
                stage_result["compiler_size_bytes"]
            )

    _rewrite_stage_result(tool, receipts[0], mutate)
    with pytest.raises(tool.CompileABError, match=message):
        tool._load_build_receipt(
            receipts[0],
            arm="candidate",
            compiler_sha256=tool.sha256_path(compilers[0]),
            runtime_sha256=tool.sha256_path(runtime),
            compiler_size_bytes=compilers[0].stat().st_size,
        )


@pytest.mark.parametrize(
    "shape",
    ["producer", "legacy", "missing-contract", "wrong-scope", "wrong-verdict-type"],
)
def test_build_receipt_validates_current_producer_metric_contract(tmp_path, shape):
    from scripts import run_pcc_stage1_build as producer

    tool = _load_tool()
    compilers = [tmp_path / "candidate", tmp_path / "baseline"]
    for compiler in compilers:
        compiler.write_bytes(compiler.name.encode())
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"runtime")
    receipts, _ = _write_build_evidence(
        tool, tmp_path / "metric-contract", compilers, runtime
    )

    def mutate(result):
        if shape == "legacy":
            return
        result["metric_scopes"] = dict(producer.STAGE1_METRIC_SCOPES)
        result["comparison_contract"] = dict(producer.STAGE1_COMPARISON_CONTRACT)
        if shape == "missing-contract":
            result.pop("comparison_contract")
        elif shape == "wrong-scope":
            result["metric_scopes"]["instructions"] = "whole_process_tree"
        elif shape == "wrong-verdict-type":
            result["comparison_contract"]["single_wall_verdict_allowed"] = 0

    _rewrite_stage_result(tool, receipts[0], mutate)

    def load():
        return tool._load_build_receipt(
            receipts[0],
            arm="candidate",
            compiler_sha256=tool.sha256_path(compilers[0]),
            compiler_size_bytes=compilers[0].stat().st_size,
            runtime_sha256=tool.sha256_path(runtime),
        )

    if shape in {"producer", "legacy"}:
        assert load()["receipt"]["status"] == "SUCCEEDED"
    else:
        with pytest.raises(
            tool.CompileABError, match="stage result (fields|metric contract)"
        ):
            load()


def test_build_receipt_rejects_protocol_artifact_and_incomplete_source(tmp_path):
    tool = _load_tool()
    compilers = []
    for arm in ("candidate", "baseline"):
        compiler = tmp_path / arm
        compiler.write_text(arm, encoding="utf-8")
        compilers.append(compiler)
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"runtime")
    receipts, _source_diff = _write_build_evidence(
        tool, tmp_path, compilers, runtime
    )
    receipt_path = receipts[0]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["command"][receipt["command"].index("self")] = "llvm"
    receipt["command_sha256"] = tool._canonical_sha256(receipt["command"])
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(tool.CompileABError, match="strict stage1 protocol"):
        tool._load_build_receipt(
            receipt_path,
            arm="candidate",
            compiler_sha256=tool.sha256_path(compilers[0]),
            runtime_sha256=tool.sha256_path(runtime),
            compiler_size_bytes=compilers[0].stat().st_size,
        )

    receipts, _source_diff = _write_build_evidence(
        tool, tmp_path / "artifact-case", compilers, runtime
    )
    (receipts[0].parent / "stage1.time").write_text("changed\n", encoding="utf-8")
    with pytest.raises(tool.CompileABError, match="stage artifact time changed"):
        tool._load_build_receipt(
            receipts[0],
            arm="candidate",
            compiler_sha256=tool.sha256_path(compilers[0]),
            runtime_sha256=tool.sha256_path(runtime),
            compiler_size_bytes=compilers[0].stat().st_size,
        )

    receipts, _source_diff = _write_build_evidence(
        tool, tmp_path / "closure-case", compilers, runtime
    )
    extra = receipts[0].parent / "source-snapshot" / "pcc" / "unlisted.py"
    extra.parent.chmod(0o755)
    extra.write_text("extra = 1\n", encoding="utf-8")
    with pytest.raises(tool.CompileABError, match="complete build closure"):
        tool._load_build_receipt(
            receipts[0],
            arm="candidate",
            compiler_sha256=tool.sha256_path(compilers[0]),
            runtime_sha256=tool.sha256_path(runtime),
            compiler_size_bytes=compilers[0].stat().st_size,
        )
    with pytest.raises(tool.CompileABError, match="original source snapshot"):
        tool._snapshot_build_evidence(
            receipts[0], tmp_path / "laundering-output", "candidate"
        )


@pytest.mark.parametrize("has_stamp", [False, True])
def test_runtime_bundle_copies_provenance_sources_and_detects_changes(
    tmp_path, monkeypatch, has_stamp,
):
    tool = _load_tool()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        tool,
        "_seal_runtime_bundle",
        lambda _bundle_dir, _archive, _copied: {"verified": True},
    )
    runtime_source = tmp_path / "pcc" / "runtime" / "py" / "sample.py"
    runtime_source.parent.mkdir(parents=True)
    runtime_source.write_text("VALUE = 1\n", encoding="utf-8")
    source_sha = hashlib.sha256(runtime_source.read_bytes()).hexdigest()
    runtime = tmp_path / "renamed-runtime.a"
    runtime.write_bytes(b"frozen runtime")
    Path(str(runtime) + ".capi_syms").write_text("_PyAnchor\n", encoding="utf-8")
    if has_stamp:
        Path(str(runtime) + ".target").write_text("darwin:arm64:test\n", encoding="utf-8")
    Path(str(runtime) + ".provenance.json").write_text(
        json.dumps(
            {
                "members": [
                    {
                        "source": "pcc/runtime/py/sample.py",
                        "source_sha256": source_sha,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out"
    output.mkdir()
    bundle = tool._prepare_runtime_bundle(runtime, output)
    copied_source = output / "runtime-bundle" / "py" / "sample.py"
    assert copied_source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert Path(bundle["archive"]).read_bytes() == b"frozen runtime"
    assert Path(bundle["archive"] + ".target").exists() == has_stamp
    tool._verify_runtime_bundle(bundle)
    copied_source.chmod(0o644)
    copied_source.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(tool.CompileABError, match="changed during A/B"):
        tool._verify_runtime_bundle(bundle)


@pytest.mark.parametrize("checksums", [["a" * 64], ["a" * 64, "b" * 64]])
@pytest.mark.parametrize("has_stamp", [False, True])
def test_runtime_bundle_preserves_every_verified_codegen_identity(
    tmp_path, monkeypatch, checksums, has_stamp,
):
    from types import SimpleNamespace

    tool = _load_tool()
    archive = tmp_path / "runtime.a"
    archive.write_bytes(b"archive whose members were verified")
    for suffix, content in [
        (".target", "darwin:arm64:arm64-apple-darwin\n"),
        (".provenance.json", "{}\n"), (".capi_syms", "PyAnchor\n"),
    ]:
        if suffix != ".target" or has_stamp:
            Path(str(archive) + suffix).write_text(content)
    manifest = {
        "target_triple": "arm64-apple-darwin", "member_count": len(checksums),
        "members": [{"object_emitter": "pcc", "codegen_checksum": checksum}
                    for checksum in checksums],
    }
    calls = []

    def verify(path, **kwargs):
        calls.append((path, kwargs["runtime_root"]))
        return manifest

    monkeypatch.setattr(tool, "_load_provenance_provider", lambda: (
        SimpleNamespace(verify_runtime_archive_manifest=verify), tool._path_receipt(SCRIPT),
    ))
    copied = {}
    verification = tool._seal_runtime_bundle(tmp_path, archive, copied)
    assert calls == [(archive, tmp_path)]
    assert verification["target_identity_source"] == (
        "verified-manifest+legacy-stamp" if has_stamp else "verified-manifest"
    )
    assert Path(str(archive) + ".target").exists() == has_stamp
    # One compiler gets the singular key; several get the plural one and the
    # singular key is absent.  It is never published as null: a reader that
    # only knows "codegen_checksum" must fail to find it rather than read a
    # null that looks like "not recorded" beside real checksums.
    if len(checksums) == 1:
        assert verification["codegen_checksum"] == checksums[0]
        assert "codegen_checksums" not in verification
    else:
        assert "codegen_checksum" not in verification
        assert verification["codegen_checksums"] == checksums
    evidence = tool.runtime_bundle_evidence({"files": copied, "verification": verification})
    assert evidence.get("codegen_checksum") == verification.get("codegen_checksum")
    assert evidence.get("codegen_checksums") == verification.get("codegen_checksums")
    assert ("codegen_checksum" in evidence) == ("codegen_checksum" in verification)
    assert evidence["producer_claim"] == "binary-integrity-only; producer source closure not proven"
    assert evidence["target_identity_source"] == verification["target_identity_source"]
    if has_stamp:
        for invalid_stamp in ("", "darwin:x86_64:arm64-apple-darwin", "darwin:arm64:arm64-apple-darwin\nextra"):
            Path(str(archive) + ".target").write_text(invalid_stamp)
            with pytest.raises(tool.CompileABError, match="runtime target"):
                tool._seal_runtime_bundle(tmp_path, archive, {})
        Path(str(archive) + ".target").write_text("darwin:arm64:arm64-apple-darwin\n")
    for invalid_target in ("x86_64-unknown-linux-gnu", "arm64-apple-darwin\ninjected"):
        manifest["target_triple"] = invalid_target
        with pytest.raises(tool.CompileABError, match="claim platform"):
            tool._seal_runtime_bundle(tmp_path, archive, {})
    manifest["target_triple"] = "arm64-apple-darwin"

    for invalid in (None, "unknown", "f" * 63):
        manifest["members"][-1]["codegen_checksum"] = invalid
        with pytest.raises(tool.CompileABError, match="unknown codegen checksum"):
            tool._seal_runtime_bundle(tmp_path, archive, {})


def test_runtime_bundle_requires_provenance_even_after_archive_rename(
    tmp_path, monkeypatch
):
    tool = _load_tool()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    runtime = tmp_path / "renamed.a"
    runtime.write_bytes(b"archive")
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(tool.CompileABError, match="runtime sidecar"):
        tool._prepare_runtime_bundle(runtime, output)


def test_runtime_bundle_can_verify_against_an_isolated_source_root(
    tmp_path, monkeypatch
):
    tool = _load_tool()
    live_root = tmp_path / "live"
    frozen_root = tmp_path / "frozen"
    monkeypatch.setattr(tool, "REPO_ROOT", live_root)
    monkeypatch.setattr(
        tool,
        "_seal_runtime_bundle",
        lambda _bundle_dir, _archive, _copied: {"verified": True},
    )
    runtime_source = frozen_root / "pcc" / "runtime" / "py" / "sample.py"
    runtime_source.parent.mkdir(parents=True)
    runtime_source.write_text("VALUE = 1\n", encoding="utf-8")
    source_sha = hashlib.sha256(runtime_source.read_bytes()).hexdigest()
    live_source = live_root / "pcc" / "runtime" / "py" / "sample.py"
    live_source.parent.mkdir(parents=True)
    live_source.write_text("VALUE = 2\n", encoding="utf-8")

    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"frozen runtime")
    Path(str(runtime) + ".capi_syms").write_text("_PyAnchor\n", encoding="utf-8")
    Path(str(runtime) + ".target").write_text("darwin:arm64:test\n", encoding="utf-8")
    Path(str(runtime) + ".provenance.json").write_text(
        json.dumps(
            {
                "members": [
                    {
                        "source": "pcc/runtime/py/sample.py",
                        "source_sha256": source_sha,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out"
    output.mkdir()

    bundle = tool._prepare_runtime_bundle(
        runtime,
        output,
        runtime_source_root=frozen_root,
    )

    copied = output / "runtime-bundle" / "py" / "sample.py"
    assert copied.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert Path(bundle["archive"]).read_bytes() == b"frozen runtime"


def test_runner_writes_complete_manifest_for_four_matched_pairs(tmp_path, monkeypatch):
    _assert_runner_manifest_for_source(tmp_path, monkeypatch, "pcc/ir/ir.py")


def test_runner_writes_scanner_manifest_for_four_matched_pairs(tmp_path, monkeypatch):
    _assert_runner_manifest_for_source(
        tmp_path, monkeypatch, "pcc/frontends/python/pipeline_import_scan.py",
    )


def _assert_runner_manifest_for_source(
    tmp_path, monkeypatch, source_variable, *, unchanged_control=False,
    guarded=False, failure=None,
):
    tool = _load_tool()
    real_linkage = tool._linkage
    if unchanged_control:
        fake_time = tmp_path / "fake-time"
        fake_time.write_text("protocol fixture", encoding="utf-8")
        monkeypatch.setattr(tool, "TIME_BINARY", fake_time)
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        tool,
        "_linkage",
        lambda _binary, *, timeout, env, cwd: {
            "checked": True,
            "links_libpython": False,
            "links_llvm": False,
            "stdout": "fake strict linkage",
        },
    )
    monkeypatch.setattr(tool, "_toolchain_receipts", lambda: [])

    def fake_runtime_bundle(runtime, output):
        root = output / "runtime-bundle"
        root.mkdir()
        copied = root / runtime.name
        tool._snapshot_file(runtime, copied, label="runtime")
        return {
            "source_archive": tool._path_receipt(runtime),
            "archive": str(copied),
            "files": {copied.name: tool._path_receipt(copied)},
            "verification": {
                "provider": tool._path_receipt(runtime),
                "ar": tool._path_receipt(runtime),
                "manifest_target": "test",
                "manifest_member_count": 1,
                "wheel_target": "test",
                "object_emitter": "test-object-emitter",
                "codegen_checksum": "f" * 64,
                "producer_claim": "binary-integrity-only; producer source closure not proven",
            },
        }

    monkeypatch.setattr(tool, "_prepare_runtime_bundle", fake_runtime_bundle)
    fake_source = (
        "#!/usr/bin/env python3\n"
        "import shlex\n"
        "import sys\n"
        "from pathlib import Path\n"
        "output_index = sys.argv.index('-o')\n"
        "source = Path(sys.argv[output_index - 1])\n"
        "output = Path(sys.argv[output_index + 1])\n"
        "value = source.read_text(encoding='utf-8').strip()\n"
        "output.write_text('#!/bin/sh\\nprintf \"%s\\\\n\" ' + shlex.quote(value) + "
        "'\\n', encoding='utf-8')\n"
        "output.chmod(0o755)\n"
    )
    compilers = []
    for index, name in enumerate(("candidate-pcc1", "baseline-pcc1")):
        compiler = tmp_path / name
        compiler.write_text(fake_source + f"# arm {index}\n", encoding="utf-8")
        compiler.chmod(0o755)
        compilers.append(compiler)
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"frozen runtime")
    monkeypatch.setattr(
        tool,
        "external_tool_evidence",
        lambda _host_python: [
            tool._portable_file_receipt(tool._path_receipt(runtime), "test")
        ],
    )
    receipts, source_diff = _write_build_evidence(
        tool,
        tmp_path,
        compilers,
        runtime,
        source_variable=source_variable,
    )
    recorded_producer_tools = json.loads(
        receipts[0].read_text(encoding="utf-8")
    )["producer_tools"]
    recorded_host_runtime = json.loads(
        receipts[0].read_text(encoding="utf-8")
    )["host_python_runtime"]
    monkeypatch.setattr(
        tool,
        "current_producer_tool_evidence",
        lambda: (recorded_producer_tools, []),
    )
    monkeypatch.setattr(
        tool,
        "host_python_runtime_evidence",
        lambda _host_python: recorded_host_runtime,
    )
    warmup = tmp_path / "warmup.py"
    warmup.write_text("warmup\n", encoding="utf-8")
    inputs = []
    expected_values = ["74856", "84856", "94856", "104856"]
    if unchanged_control:
        expected_values += ["114856", "124856"]
    for index, value in enumerate(expected_values, 1):
        source = tmp_path / f"pair{index}.py"
        source.write_text(value + "\n", encoding="utf-8")
        inputs.append(source)
    output_dir = tmp_path / "build" / "ab-output"
    argv = [
        "--candidate",
        str(compilers[0]),
        "--baseline",
        str(compilers[1]),
        "--runtime-archive",
        str(runtime),
        "--candidate-build-receipt",
        str(receipts[0]),
        "--baseline-build-receipt",
        str(receipts[1]),
        "--source-diff",
        str(source_diff),
        "--allowed-changed-source",
        source_variable,
        "--warmup",
        str(warmup),
        "--output-dir",
        str(output_dir),
        "--min-speedup-ratio",
        "100",
    ]
    for source, expected in zip(
        inputs, expected_values, strict=True
    ):
        argv.extend(("--input", str(source), "--expected-output", expected))
    launches = []
    if unchanged_control:
        argv.extend(("--unchanged-control", "--frontend-jobs", "1" if guarded else "2",
                     "--self-backend-jobs", "2"))
        _mock_control_processes(tool, tmp_path, monkeypatch, launches,
                                linkage_validator=real_linkage, failure=failure)
    args = tool._parser().parse_args(argv)

    def run():
        if guarded:
            contract = _write_guard_contract(tool, tmp_path, monkeypatch)
            with tool._performance_lock() as token:
                return tool.run_guarded(args, performance_lock_token=token, **contract)
        return tool.run(args)

    if failure is not None:
        with pytest.raises(tool.CompileABError):
            run()
        persisted = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
        return tool, None, persisted, launches, args
    manifest = run()
    persisted = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == persisted["status"] == "DENIED"
    assert manifest["claim_level"] == "optimization-slice"
    assert manifest["summary"]["verdict"] == "DENY"
    assert (
        manifest["environment_by_arm"]["candidate"]["PCC_SOURCE_ROOT"]
        == manifest["environment_by_arm"]["baseline"]["PCC_SOURCE_ROOT"]
        == manifest["host_source_roots"]["baseline"]
    )
    assert "build-evidence/arm-b/source-snapshot" in manifest[
        "host_source_roots"
    ]["baseline"]
    assert manifest["host_helper_policy"] == "common-frozen-baseline-build-evidence"
    assert manifest["single_variable"]["changed_sources"] == [source_variable]
    assert manifest["single_variable"]["allowed_changed_sources"] == [source_variable]
    assert manifest["runtime_bundle"]["archive"].endswith("runtime-bundle/runtime.a")
    assert manifest["compiler_linkage"]["candidate"]["checked"] is True
    assert len(manifest["pairs"]) == len(expected_values)
    assert manifest["warmup"]["schedule"] == [
        "candidate",
        "baseline",
        "baseline",
        "candidate",
    ]
    assert [pair["order"] for pair in manifest["pairs"]] == [
        list(tool.pair_order(index)) for index in range(1, len(expected_values) + 1)
    ]
    assert all(
        pair["candidate"]["binary_sha256"] == pair["baseline"]["binary_sha256"]
        for pair in manifest["pairs"]
    )
    assert all(pair["candidate"]["run"]["returncode"] == 0 for pair in manifest["pairs"])
    return tool, manifest, persisted, launches, args


def _admission_receipt_fixture(tmp_path):
    tool = _load_tool()
    compilers = []
    for arm in ("candidate", "baseline"):
        compiler = tmp_path / arm
        compiler.write_bytes(arm.encode())
        compilers.append(compiler)
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"runtime")
    receipts, _ = _write_build_evidence(
        tool, tmp_path, compilers, runtime, shared_logical_source_root=False,
    )
    return tool, receipts[0], compilers[0], runtime


def _load_admission_receipt(tool, path, compiler, runtime):
    return tool._load_build_receipt(
        path, arm="candidate", compiler_sha256=tool.sha256_path(compiler),
        runtime_sha256=tool.sha256_path(runtime),
        compiler_size_bytes=compiler.stat().st_size,
    )


@pytest.mark.parametrize("shape", ["historical", "caller", "explicit", "external_guard", "automatic"])
def test_build_receipt_admits_known_budget_extension_without_changing_history(tmp_path, shape):
    tool, path, compiler, runtime = _admission_receipt_fixture(tmp_path)
    receipt = json.loads(path.read_text())
    if shape != "historical":
        receipt["memory_budget_selection"] = {
            "selection_kind": shape, "max_tree_rss_bytes": 1024,
            "observation": {"platform": "fixture", "available_bytes": 2048},
        }
        receipt["environment"]["PCC_WORKER_TREE_BUDGET_BYTES"] = "1024"
        receipt["environment_sha256"] = tool._canonical_sha256(receipt["environment"])
        path.write_text(json.dumps(receipt))
    loaded = _load_admission_receipt(tool, path, compiler, runtime)
    assert loaded["receipt"] == receipt
    assert ("memory_budget_selection" in loaded["receipt"]) == (shape != "historical")


@pytest.mark.parametrize("selection,advertised", [
    (None, "1024"), ([], "1024"), ({}, "1024"),
    ({"selection_kind": "caller", "max_tree_rss_bytes": True}, "True"),
    ({"selection_kind": "caller", "max_tree_rss_bytes": "1024"}, "1024"),
    ({"selection_kind": "caller", "max_tree_rss_bytes": 1024.0}, "1024"),
    ({"selection_kind": "caller", "max_tree_rss_bytes": 0}, "0"),
    ({"selection_kind": "caller", "max_tree_rss_bytes": -1}, "-1"),
    ({"selection_kind": "unknown", "max_tree_rss_bytes": 1024}, "1024"),
    ({"max_tree_rss_bytes": 1024}, "1024"),
    ({"selection_kind": "caller", "max_tree_rss_bytes": 1024}, None),
    ({"selection_kind": "caller", "max_tree_rss_bytes": 1024}, "2048"),
])
def test_build_receipt_rejects_invalid_admission_extension(tmp_path, selection, advertised):
    tool, path, compiler, runtime = _admission_receipt_fixture(tmp_path)
    receipt = json.loads(path.read_text())
    receipt["memory_budget_selection"] = selection
    if advertised is not None:
        receipt["environment"]["PCC_WORKER_TREE_BUDGET_BYTES"] = advertised
    receipt["environment_sha256"] = tool._canonical_sha256(receipt["environment"])
    path.write_text(json.dumps(receipt))
    with pytest.raises(tool.CompileABError, match="admission budget"):
        _load_admission_receipt(tool, path, compiler, runtime)


@pytest.mark.parametrize("with_selection", [False, True])
def test_build_receipt_admission_extension_does_not_allow_unknown_fields(tmp_path, with_selection):
    tool, path, compiler, runtime = _admission_receipt_fixture(tmp_path)
    receipt = json.loads(path.read_text())
    if with_selection:
        receipt["memory_budget_selection"] = {
            "selection_kind": "caller", "max_tree_rss_bytes": 1024,
        }
        receipt["environment"]["PCC_WORKER_TREE_BUDGET_BYTES"] = "1024"
        receipt["environment_sha256"] = tool._canonical_sha256(receipt["environment"])
    receipt["unrecognized_admission_override"] = True
    path.write_text(json.dumps(receipt))
    with pytest.raises(tool.CompileABError, match="fields mismatch"):
        _load_admission_receipt(tool, path, compiler, runtime)


@pytest.mark.parametrize("selected_by_guard", [False, True])
def test_stage1_produced_admission_receipt_passes_strict_loader(tmp_path, monkeypatch, selected_by_guard):
    """Exercise the actual receipt producer with all process launches replaced."""
    import subprocess

    tool, fixture_path, _compiler, runtime = _admission_receipt_fixture(tmp_path)
    fixture = json.loads(fixture_path.read_text())
    spec = importlib.util.spec_from_file_location(
        "stage1_admission_producer", ROOT / "scripts/run_pcc_stage1_build.py",
    )
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    output = tmp_path / "produced-build"
    cap = 1024
    args = builder._parser().parse_args([
        "--arm", "candidate", "--source-root", str(fixture_path.parent / "source-snapshot"),
        "--runtime-archive", str(runtime), "--output-dir", str(output),
        "--jobs", "2", "--self-backend-jobs", "2", "--memory-budget-bytes", str(cap),
    ])
    args.jobs = 2
    monkeypatch.setenv("PATH", os.defpath)
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(cap))
    monkeypatch.delenv("PCC_WORKER_TREE_STATE_PATH", raising=False)
    monkeypatch.delenv("PCC_WORKER_TREE_RECOVERY", raising=False)
    if selected_by_guard:
        # The inherited-cap branch performs no host observation or process launch.
        args.memory_budget_selection = builder._host_memory_budget_selection(cap)
    monkeypatch.setattr(tool, "require_claim_platform", lambda: None)
    monkeypatch.setattr(tool, "_prepare_runtime_bundle", lambda *a, **k: {"archive": str(runtime)})
    monkeypatch.setattr(tool, "runtime_bundle_evidence", lambda bundle: fixture["runtime_bundle"])
    monkeypatch.setattr(tool, "_verify_runtime_bundle", lambda bundle: None)
    monkeypatch.setattr(tool, "external_tool_evidence", lambda python: fixture["external_tools"])
    monkeypatch.setattr(tool, "host_python_runtime_evidence", lambda python: fixture["host_python_runtime"])
    linkage = {"checked": True, "links_libpython": False, "links_llvm": False, "stdout": "fixture"}
    monkeypatch.setattr(tool, "_linkage", lambda *a, **k: linkage)
    monkeypatch.setattr(builder, "run_function_smoke", lambda *a, **k: None)
    launches = []

    def fake_process(command, **kwargs):
        launches.append(command)
        if len(launches) == 1:
            assert command[0] == str(tool.TIME_BINARY)
            (output / "pcc1").write_bytes(b"synthetic compiler")
            (output / "pcc1").chmod(0o755)
            (output / "stage1.time").write_bytes((fixture_path.parent / "stage1.time").read_bytes())
            (output / "stage1.profile.json").write_text("{}\n")
        else:
            assert command == [str(output / "pcc1"), "--help"]
        return subprocess.CompletedProcess(command, 0, "", "")

    def forbidden(*args, **kwargs):
        pytest.fail("admission receipt regression must not launch a real process")

    monkeypatch.setattr(tool, "_run_process", fake_process)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    builder._run_build(args, tool, run_token="admission-fixture")
    loaded = _load_admission_receipt(tool, output / "build-receipt.json", output / "pcc1", runtime)
    selection = loaded["receipt"]["memory_budget_selection"]
    assert selection["max_tree_rss_bytes"] == cap
    assert selection["selection_kind"] == ("explicit" if selected_by_guard else "caller")
    assert loaded["receipt"]["environment"]["PCC_WORKER_TREE_BUDGET_BYTES"] == str(cap)
    assert len(launches) == 2
    stage_spec = importlib.util.spec_from_file_location(
        "stage_ab_admission_consumer", ROOT / "scripts/run_pcc_stage_ab.py",
    )
    assert stage_spec is not None and stage_spec.loader is not None
    stage_ab = importlib.util.module_from_spec(stage_spec)
    stage_spec.loader.exec_module(stage_ab)
    stage_ab._assert_stage1_admission_budget(
        loaded["receipt"], expected=cap, arm="candidate",
    )


def _write_guard_contract(tool, tmp_path, monkeypatch):
    """Synthetic protocol evidence only. No guard or compiler is executed."""
    from scripts import run_process_tree_sample as sampler

    def forbidden(*args, **kwargs):
        pytest.fail("guard contract validation must not observe the host")

    monkeypatch.setattr(sampler, "_host_memory_observation", forbidden)
    monkeypatch.setattr(tool.time, "monotonic", lambda: 100.0)
    monkeypatch.setenv("PATH", os.defpath)
    path = tmp_path / "outer-guard.json"
    cap = 3 * 1024**3
    observation = {
        "platform": "darwin", "reclaimable_bytes": cap + tool.CI_TREE_RESERVE_BYTES,
        "disk_free_bytes": 32 * 1024**3, "swap_total_bytes": 0,
        "swap_used_bytes": 0, "swap_free_bytes": 0,
    }
    selection = sampler.select_tree_memory_budget(
        0, default_ceiling=tool.CI_TREE_CEILING_BYTES,
        minimum_bytes=tool.CI_TREE_FLOOR_BYTES,
        reserve_bytes=tool.CI_TREE_RESERVE_BYTES, observation=observation,
    )
    environment = {
        "PCC_WORKER_TREE_BUDGET_BYTES": str(cap),
        "PCC_WORKER_TREE_STATE_PATH": str(path) + ".worker-rss.tsv",
        "PCC_WORKER_TREE_RECOVERY": "pcc.worker-tree-recovery.v1\t1.0,3.0\t4.0\t200.0",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    command = [sys.executable, str(tmp_path / "ci-orchestrator.py"), "--guarded"]
    payload = {
        "schema": "pcc.process_tree_sample.v1", "status": "RUNNING",
        "command": command, "cwd": str(tmp_path.resolve()),
        "environment": environment, "max_tree_rss_bytes": cap,
        "memory_budget_selection": selection,
        "resource_preflight": selection["resource_preflight"],
        "darwin_preflight_reserve_bytes": tool.CI_TREE_RESERVE_BYTES,
        "started_at_utc": "2026-11-13T00:00:00+00:00",
        "timeout_s": 100.0, "interval_s": 0.25,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return {
        "guard_result_path": path, "expected_guard_command": command,
        "expected_guard_cwd": tmp_path, "campaign_deadline_monotonic": 200.0,
    }


def _mock_control_processes(tool, tmp_path, monkeypatch, launches, *, linkage_validator, failure=None):
    """Exercise real harness parsing and validation, replacing only Popen."""
    monkeypatch.setattr(tool, "require_claim_platform", lambda: None)
    otool = tmp_path / "fake-otool"
    otool.write_text("protocol fixture", encoding="utf-8")
    monkeypatch.setattr(tool, "OTOOL_BINARY", otool)
    # Restore the real linkage validator replaced by the old smoke helper.
    monkeypatch.setattr(tool, "_linkage", linkage_validator)
    monkeypatch.setattr(tool, "_process_group_exists", lambda process: False)

    class Process:
        def __init__(self, command, **kwargs):
            self.command = command
            self.pid = 12345
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""
            launches.append({"command": command, **kwargs})
            if command[0] == str(tool.TIME_BINARY):
                assert command[1:3] == ["-lp", "-o"]
                assert command[5:8] == ["--ir-scaffold=on", "--backend", "self"]
                assert command[9] == "-o"
                source, output = Path(command[8]), Path(command[10])
                output.write_bytes(source.read_bytes())
                output.chmod(0o755)
                wall = 10 if "arm-a" in command[4] else 12
                if "-ctl-a" in output.name:
                    wall = 8  # Deliberate control drift must not adjust A/B.
                Path(command[3]).write_text(
                    f"real {wall}.0\nuser 8.0\nsys 1.0\n"
                    "100 maximum resident set size\n100 instructions retired\n"
                    "100 cycles elapsed\n100 peak memory footprint\n",
                    encoding="utf-8",
                )
                if output.name == "pair1-ctl-b.bin":
                    if failure == "bytes":
                        output.write_bytes(b"different\n")
                    elif failure == "compiler_mutation":
                        # The real snapshot is sealed. Deliberately corrupt our
                        # owned fixture, then restore its mode so this reaches
                        # the identity guard rather than failing at open().
                        compiler = Path(command[4])
                        original_mode = compiler.stat().st_mode
                        assert original_mode & 0o222 == 0
                        compiler.chmod(original_mode | 0o200)
                        try:
                            compiler.write_bytes(b"changed compiler")
                        finally:
                            compiler.chmod(original_mode)
                    elif failure == "deadline":
                        monkeypatch.setattr(tool.time, "monotonic", lambda: 201.0)
            elif command[0] == str(otool):
                assert command[1] == "-L"
                if failure == "linkage" and Path(command[2]).name == "pair1-ctl-b.bin":
                    self.stdout = "libpython3.15.dylib\n"
                else:
                    self.stdout = "libSystem.B.dylib\n"
            else:
                assert len(command) == 1
                self.stdout = Path(command[0]).read_text(encoding="utf-8")
                if Path(command[0]).name.startswith("pair1-ctl-"):
                    if failure == "stdout":
                        self.stdout = "wrong\n"
                    elif failure == "stderr":
                        self.stderr = "unexpected diagnostic\n"
                    elif failure == "returncode":
                        self.returncode = 1

        def communicate(self, timeout):
            launches[-1]["timeout"] = timeout
            return self.stdout, self.stderr

    def forbidden(*args, **kwargs):
        pytest.fail("control protocol regression must not launch a real process")

    monkeypatch.setattr(tool.subprocess, "Popen", Process)
    monkeypatch.setattr(tool.subprocess, "run", forbidden)


def test_guarded_budget_forwards_actual_admission_and_equal_private_environments(tmp_path, monkeypatch):
    tool = _load_tool()
    contract = _write_guard_contract(tool, tmp_path, monkeypatch)
    budget = tool._validated_worker_budget(**contract)
    assert budget.max_tree_rss_bytes == 3 * 1024**3
    assert budget.deadline_monotonic == 200.0
    assert budget.remaining_timeout(120) == 100.0
    monkeypatch.setenv("PCC_UNRELATED_FORGED_SETTING", "1")
    environments = []
    for name in ("arm-a", "arm-b", "ctl-a", "ctl-b"):
        private = tmp_path / name
        env = tool._measurement_env(
            tmp_path / "runtime.a", 0, host_source_root=tmp_path / "common-source",
            host_python=Path(sys.executable), private_root=private,
            frontend_jobs=1, self_backend_jobs=2, worker_budget=budget,
        )
        assert all(env[key] == budget.environment[key] for key in tool.WORKER_BUDGET_KEYS)
        assert env["PCC_MACHO_LINK_JOBS"] == "2"
        assert "PCC_UNRELATED_FORGED_SETTING" not in env
        environments.append(tool._normalized_measurement_env(
            env, host_source_root=tmp_path / "common-source", private_root=private,
        ))
    assert all(value == environments[0] for value in environments)


@pytest.mark.parametrize("missing", [
    "PCC_WORKER_TREE_BUDGET_BYTES", "PCC_WORKER_TREE_STATE_PATH", "PCC_WORKER_TREE_RECOVERY",
])
def test_guarded_budget_rejects_partial_environment(tmp_path, monkeypatch, missing):
    tool = _load_tool()
    contract = _write_guard_contract(tool, tmp_path, monkeypatch)
    monkeypatch.delenv(missing)
    with pytest.raises(tool.CompileABError, match="budget is missing or mismatched"):
        tool._validated_worker_budget(**contract)


@pytest.mark.parametrize("mutation", [
    "schema", "status", "command", "cwd", "cap", "cap_below_floor", "ceiling",
    "reserve", "selection", "observation", "preflight", "environment", "mailbox",
    "recovery", "retry", "grace", "nan_deadline", "expired", "extended", "timing", "started_at_utc",
])
def test_guarded_budget_rejects_forged_or_malformed_contract(tmp_path, monkeypatch, mutation):
    tool = _load_tool()
    contract = _write_guard_contract(tool, tmp_path, monkeypatch)
    path = contract["guard_result_path"]
    payload = json.loads(path.read_text())
    if mutation in ("schema", "status", "cwd", "started_at_utc"):
        payload[mutation] = "wrong"
    elif mutation == "command":
        payload["command"] = ["unrelated"]
    elif mutation == "cap":
        payload["max_tree_rss_bytes"] = 5 * 1024**3
    elif mutation == "cap_below_floor":
        payload["max_tree_rss_bytes"] = 1024**3
    elif mutation == "ceiling":
        payload["memory_budget_selection"]["default_ceiling_bytes"] *= 2
    elif mutation == "reserve":
        payload["memory_budget_selection"]["configured_host_memory_reserve_bytes"] = 0
    elif mutation == "selection":
        payload["memory_budget_selection"]["selection_kind"] = "explicit"
    elif mutation == "observation":
        payload["memory_budget_selection"]["observation"]["reclaimable_bytes"] += 1024**3
    elif mutation == "preflight":
        payload["resource_preflight"] = {}
    elif mutation == "environment":
        payload["environment"]["PCC_WORKER_TREE_BUDGET_BYTES"] = "1"
    elif mutation == "timing":
        payload["timeout_s"] = 0
    else:
        key = "PCC_WORKER_TREE_RECOVERY"
        replacements = {
            "mailbox": "not-the-guard.worker-rss.tsv", "recovery": "malformed",
            "retry": "pcc.worker-tree-recovery.v1\t1.0,30.0\t31.0\t200.0",
            "grace": "pcc.worker-tree-recovery.v1\t1.0,3.0\t5.0\t200.0",
            "nan_deadline": "pcc.worker-tree-recovery.v1\t1.0,3.0\t4.0\tnan",
            "expired": "pcc.worker-tree-recovery.v1\t1.0,3.0\t4.0\t99.0",
            "extended": "pcc.worker-tree-recovery.v1\t1.0,3.0\t4.0\t201.0",
        }
        if mutation == "mailbox":
            key = "PCC_WORKER_TREE_STATE_PATH"
        payload["environment"][key] = replacements[mutation]
        monkeypatch.setenv(key, replacements[mutation])
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tool.CompileABError):
        tool._validated_worker_budget(**contract)


def test_guard_budget_revalidates_receipt_environment_and_original_deadline(tmp_path, monkeypatch):
    tool = _load_tool()
    contract = _write_guard_contract(tool, tmp_path, monkeypatch)
    budget = tool._validated_worker_budget(**contract)
    monkeypatch.setattr(tool.time, "monotonic", lambda: 199.0)
    assert budget.remaining_timeout(100) == 1.0
    monkeypatch.setattr(tool.time, "monotonic", lambda: 200.0)
    with pytest.raises(tool.CompileABError, match="deadline exhausted"):
        budget.verify()
    monkeypatch.setattr(tool.time, "monotonic", lambda: 100.0)
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(4 * 1024**3))
    with pytest.raises(tool.CompileABError, match="environment changed"):
        budget.verify()
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(3 * 1024**3))
    payload = json.loads(contract["guard_result_path"].read_text())
    payload["status"] = "TIMEOUT"
    contract["guard_result_path"].write_text(json.dumps(payload))
    with pytest.raises(tool.CompileABError, match="guard contract changed"):
        budget.verify()


def test_guarded_entry_rejects_fake_lock_and_silent_default_workers(tmp_path, monkeypatch):
    from argparse import Namespace
    tool = _load_tool()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    contract = _write_guard_contract(tool, tmp_path, monkeypatch)
    args = Namespace(unchanged_control=True, frontend_jobs=10, self_backend_jobs=8,
                     output_dir=str(tmp_path / "output"))
    with pytest.raises(tool.CompileABError, match="live performance lock token"):
        tool.run_guarded(args, performance_lock_token=True, **contract)
    with tool._performance_lock() as token:
        with pytest.raises(tool.CompileABError, match="frontend=1 and backend<=2"):
            tool.run_guarded(args, performance_lock_token=token, **contract)
        args.frontend_jobs = 1
        args.self_backend_jobs = 2
        args.unchanged_control = False
        with pytest.raises(tool.CompileABError, match="requires the unchanged control"):
            tool.run_guarded(args, performance_lock_token=token, **contract)
    with pytest.raises(tool.CompileABError, match="live performance lock token"):
        tool.run_guarded(args, performance_lock_token=token, **contract)


def test_guarded_child_refuses_missing_budget_before_popen(tmp_path, monkeypatch):
    tool = _load_tool()
    contract = _write_guard_contract(tool, tmp_path, monkeypatch)
    monkeypatch.setattr(tool, "_ACTIVE_WORKER_BUDGET", tool._validated_worker_budget(**contract))
    def forbidden(*args, **kwargs):
        pytest.fail("malformed budget must be rejected before Popen")
    monkeypatch.setattr(tool.subprocess, "Popen", forbidden)
    with pytest.raises(tool.CompileABError, match="child environment does not match"):
        tool._run_process(["never-run"], timeout=10, env={}, cwd=tmp_path)


def test_control_requires_six_inputs_before_any_process(tmp_path, monkeypatch):
    from argparse import Namespace
    tool = _load_tool()
    monkeypatch.setattr(tool, "require_claim_platform", lambda: None)
    monkeypatch.setattr(tool, "TIME_BINARY", Path(sys.executable))
    args = Namespace(inputs=["a", "b", "c", "d"], unchanged_control=True)
    with pytest.raises(tool.CompileABError, match="six distinct matched inputs"):
        tool._run_experiment(args, run_token="not-launched")


@pytest.mark.parametrize("guarded", [False, True])
def test_unchanged_control_uses_baseline_and_keeps_metrics_separate(tmp_path, monkeypatch, guarded):
    tool, manifest, persisted, launches, args = _assert_runner_manifest_for_source(
        tmp_path, monkeypatch, "pcc/frontends/python/pipeline_import_scan.py",
        unchanged_control=True, guarded=guarded,
    )
    assert manifest == persisted
    control = manifest["unchanged_control"]
    assert control["status"] == "MEASURED"
    assert control["identity_sha256"] == tool._canonical_sha256(control["identity"])
    assert len(control["pairs"]) == 6
    assert len({pair["input_sha256"] for pair in control["pairs"]}) == 6
    assert len(control["warmup"]) == 2
    assert manifest["candidate"]["snapshot"] != manifest["baseline"]["snapshot"]
    for index, pair in enumerate(control["pairs"], 1):
        assert pair["status"] == "VALIDATED"
        assert pair["order"] == (["control_a", "control_b"] if index % 2 else ["control_b", "control_a"])
        for arm in ("control_a", "control_b"):
            assert pair[arm]["command"][4] == manifest["baseline"]["snapshot"]
            assert pair[arm]["command"][8] == manifest["pairs"][index - 1]["input"]
            assert pair[arm]["binary_sha256"] == manifest["pairs"][index - 1]["baseline"]["binary_sha256"]
            assert pair[arm]["run"]["stdout"] == pair["expected_stdout"]
            assert pair[arm]["linkage"]["checked"]
    a, b = (control["environment_by_context"][key] for key in ("control_a", "control_b"))
    assert a["HOME"] != b["HOME"]
    assert a["PCC_SOURCE_ROOT"] == b["PCC_SOURCE_ROOT"] == manifest["host_source_roots"]["baseline"]
    assert control["summary"]["pair_wins"]["wall_s"] == {"control_a": 6, "control_b": 0, "ties": 0}
    assert control["summary"]["paired_median_control_b_over_a_ratios"]["wall_s"] == 1.5
    assert control["summary"]["drift_subtracted_from_ab"] is False
    assert control["summary"]["ab_thresholds_relaxed"] is False
    assert manifest["summary"]["paired_median_wall_speedup"] == 1.2
    assert manifest["summary"]["verdict"] == "DENY"  # Original 100x threshold remains.
    assert manifest["claim_level"] == "optimization-slice"
    assert "pcc2-to-pcc3 fixed point" in manifest["does_not_prove"]
    timed = [row for row in launches if row["command"][0] == str(tool.TIME_BINARY)]
    assert len(timed) == 32  # 4 A/B + 4 A/A warmups, 12 A/B + 12 A/A measurements.
    if guarded:
        assert manifest["worker_budget"]["max_tree_rss_bytes"] == 3 * 1024**3
        assert manifest["worker_budget"]["per_arm_tree_rss_claim"] is False
        assert manifest["protocol"]["measurement_worker_policy"] == {
            "requested_frontend_jobs": 1,
            "effective_frontend_policy": "explicit_serial_native_frontend",
            "effective_frontend_jobs": 1, "self_backend_jobs": 2,
            "macho_link_jobs": 2, "production_stage2_native_auto_equivalent": False,
        }
        assert manifest["mode"].endswith("/serial-compile-slice")
        for row in launches:
            assert row["env"]["PCC_PY_FRONTEND_JOBS"] == "1"
            assert row["env"]["PCC_SELF_BACKEND_JOBS"] == "2"
            assert row["env"]["PCC_MACHO_LINK_JOBS"] == "2"
            assert row["timeout"] <= 100
            for key in tool.WORKER_BUDGET_KEYS:
                assert row["env"][key] == manifest["worker_budget"]["worker_environment"][key]
    else:
        assert "worker_budget" not in manifest


@pytest.mark.parametrize("failure", ["bytes", "stdout", "stderr", "returncode", "linkage", "compiler_mutation", "deadline"])
def test_control_failure_retains_partial_receipt_and_cannot_accept(tmp_path, monkeypatch, failure):
    _tool, _manifest, persisted, launches, _args = _assert_runner_manifest_for_source(
        tmp_path, monkeypatch, "pcc/frontends/python/pipeline_import_scan.py",
        unchanged_control=True, guarded=True, failure=failure,
    )
    assert persisted["status"] == "ERROR"
    if failure == "compiler_mutation":
        assert "unchanged control compiler changed during A/B" in persisted["error"]
        compiler = Path(persisted["unchanged_control"]["identity"]["compiler"]["path"])
        assert compiler.read_bytes() == b"changed compiler"
        assert compiler.stat().st_mode == persisted["unchanged_control"]["identity"]["compiler"]["mode"]
    assert "summary" not in persisted
    assert len(persisted["pairs"]) == 1
    control = persisted["unchanged_control"]
    assert control["status"] == "IN_PROGRESS"
    assert len(control["pairs"]) == 1
    assert control["pairs"][0]["status"] == "IN_PROGRESS"
    assert "metrics" in control["pairs"][0]["control_a"]
    assert "metrics" in control["pairs"][0]["control_b"]
    assert not any("pair2-" in row["command"][-1] for row in launches)


def test_ordinary_environment_does_not_trust_inherited_budget_without_contract(tmp_path, monkeypatch):
    tool = _load_tool()
    monkeypatch.setenv("PATH", os.defpath)
    for key in tool.WORKER_BUDGET_KEYS:
        monkeypatch.setenv(key, "forged")
    env = tool._measurement_env(
        tmp_path / "runtime.a", 0, host_source_root=tmp_path / "source",
        host_python=Path(sys.executable), private_root=tmp_path / "private",
        frontend_jobs=10, self_backend_jobs=8,
    )
    assert not any(key in env for key in tool.WORKER_BUDGET_KEYS)
    assert env["PCC_PY_FRONTEND_JOBS"] == "10"
    assert env["PCC_SELF_BACKEND_JOBS"] == "8"
    assert env["PCC_MACHO_LINK_JOBS"] == "8"


def test_guarded_measurement_rejects_numeric_two_frontend_bypass(tmp_path, monkeypatch):
    tool = _load_tool()
    budget = tool._validated_worker_budget(**_write_guard_contract(tool, tmp_path, monkeypatch))
    with pytest.raises(tool.CompileABError, match="frontend=1 and backend<=2"):
        tool._measurement_env(
            tmp_path / "runtime.a", 0, host_source_root=tmp_path / "source",
            host_python=Path(sys.executable), private_root=tmp_path / "private",
            frontend_jobs=2, self_backend_jobs=2, worker_budget=budget,
        )


def test_guard_progress_rewrites_preserve_live_admission_contract(tmp_path, monkeypatch):
    tool = _load_tool()
    contract = _write_guard_contract(tool, tmp_path, monkeypatch)
    budget = tool._validated_worker_budget(**contract)
    path = contract["guard_result_path"]
    original_stat = path.stat()
    payload = json.loads(path.read_text())
    payload.update(elapsed_s=10.0, sample_count=40, peak_tree_rss_bytes=123456,
                   peak_process_count=3, process_table_retry_count=1,
                   command_lookup_failure_count=0,
                   largest_process_observed={"pid": 42, "rss_bytes": 123456})
    replacement = path.with_suffix(".tmp")
    replacement.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(replacement, path)
    assert path.stat().st_ino != original_stat.st_ino
    budget.verify()
    assert budget.remaining_timeout(120) == 100
    payload["timeout_s"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tool.CompileABError, match="guard contract changed"):
        budget.verify()
