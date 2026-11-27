from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "replay_pcc_codegen_worker.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("replay_codegen_worker", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_replay_rewrites_only_owned_outputs_and_restores_stage_env(
    tmp_path, monkeypatch,
):
    tool = _load_tool()
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "default")
    compiler = tmp_path / "pcc1"
    compiler.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    compiler.chmod(0o755)
    source_result = tmp_path / "old.tsv"
    source_artifacts = tmp_path / "old-artifacts"
    manifest = tmp_path / "source.manifest"
    rows = [
        tool.MANIFEST_SCHEMA,
        str(source_result),
        str(source_artifacts),
        "/frozen/native_exports.json",
        "codegen",
        "/frozen/ast",
        "pcc.__main__",
        "off",
        "on",
        "0",
        "1",
        "subprocess",
        "pcc.backend",
        "0\tpcc.mod\t/frozen/mod.py",
        "1",
        "0",
    ]
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    stage_receipt = tmp_path / "stage.json"
    stage_receipt.write_text(
        json.dumps(
            {
                "environment": {
                    "PCC_RUNTIME_ARCHIVE": "/frozen/runtime.a",
                    "PCC_SOURCE_ROOT": "/frozen/source",
                    "PCC_PY_FRONTEND_JOBS": "auto",
                    "PCC_BOOTSTRAP_PYTHON_IR_PASSES": "off",
                    "PCC_BOOTSTRAP_RUNTIME_HIGH": "py",
                    "PCC_BOOTSTRAP_RUNTIME_CC": "pcc",
                    "LC_ALL": "C",
                }
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "replay"

    command, environment = tool.prepare_replay(
        compiler=compiler,
        manifest=manifest,
        stage_receipt=stage_receipt,
        output_dir=output,
        native_object=1,
    )

    replay_lines = (output / "worker.manifest").read_text(
        encoding="utf-8"
    ).splitlines()
    assert replay_lines[0] == tool.MANIFEST_SCHEMA
    assert replay_lines[1] == str(output / "worker.tsv")
    assert replay_lines[2] == str(output / "artifacts")
    assert replay_lines[3:] == rows[3:]
    assert environment["PCC_RUNTIME_ARCHIVE"] == "/frozen/runtime.a"
    assert environment["PCC_SOURCE_ROOT"] == "/frozen/source"
    assert environment["PCC_PY_FRONTEND_JOBS"] == "1"
    assert environment["PCC_DIRECT_INDEXED_NATIVE_OBJECT"] == "1"
    assert environment["PCC_PYTHON_IR_PASSES"] == "off"
    assert environment["PCC_RUNTIME_HIGH"] == "py"
    assert environment["PCC_RUNTIME_CC"] == "pcc"
    assert environment["PCC_DIRECT_INDEXED_KERNEL_CAPTURE"] == "1"
    assert environment["PCC_DIRECT_INDEXED_KERNEL_EMIT"] == "1"
    assert environment["PCC_DIRECT_INDEXED_SIDECAR"] == "1"
    assert "LC_ALL" not in environment
    assert command[-2:] == [
        "--pcc-python-multi-codegen-worker",
        str(output / "worker.manifest"),
    ]
    receipt = json.loads((output / "replay.json").read_text(encoding="utf-8"))
    assert receipt["schema"] == tool.REPLAY_SCHEMA
    assert receipt["native_object"] == 1
    assert receipt["effective_worker_environment"]["PCC_PYTHON_IR_PASSES"] == "off"

    with pytest.raises(tool.ReplayError, match="refusing existing"):
        tool.prepare_replay(
            compiler=compiler,
            manifest=manifest,
            stage_receipt=stage_receipt,
            output_dir=output,
            native_object=1,
        )

    override = tmp_path / "selected-native-exports.json"
    override.write_text("{}", encoding="utf-8")
    override_output = tmp_path / "override-replay"
    tool.prepare_replay(
        compiler=compiler,
        manifest=manifest,
        stage_receipt=stage_receipt,
        output_dir=override_output,
        native_object=0,
        exports_path=override,
    )
    override_lines = (override_output / "worker.manifest").read_text(
        encoding="utf-8"
    ).splitlines()
    assert override_lines[3] == str(override.resolve())
    override_receipt = json.loads(
        (override_output / "replay.json").read_text(encoding="utf-8")
    )
    assert override_receipt["exports_override"] == str(override.resolve())

    host_root = tmp_path / "host-source"
    (host_root / "pcc").mkdir(parents=True)
    (host_root / "pcc" / "__main__.py").write_text("pass\n")
    host_output = tmp_path / "host-replay"
    host_command, host_env = tool.prepare_replay(
        compiler=compiler, manifest=manifest, stage_receipt=stage_receipt,
        output_dir=host_output, native_object=1, host_source_root=host_root,
    )
    assert host_command[-6:] == [str(compiler), "-P", "-m", "pcc",
                                  "--pcc-python-multi-codegen-worker",
                                  str(host_output / "worker.manifest")]
    assert host_env["PYTHONPATH"] == str(host_root)
    assert host_env["PCC_SOURCE_ROOT"] == str(host_root)
    assert host_env["PCC_DIRECT_INDEXED_SIDECAR"] == "0"
    assert (host_output / "worker.manifest").read_text().splitlines()[3:] == rows[3:]
    host_receipt = json.loads((host_output / "replay.json").read_text())
    assert host_receipt["execution_owner"] == "host-cpython"

    import os
    import subprocess
    import sys

    (host_root / "pcc" / "__init__.py").write_text("marker = 'frozen'\n")
    cwd_package = tmp_path / "cwd" / "pcc"
    cwd_package.mkdir(parents=True)
    (cwd_package / "__init__.py").write_text("marker = 'wrong-cwd'\n")
    imported = subprocess.run(
        [sys.executable, "-P", "-c", "import pcc; print(pcc.marker)"],
        cwd=cwd_package.parent,
        env={**os.environ, "PYTHONPATH": str(host_root)},
        capture_output=True, text=True, timeout=10,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout == "frozen\n"


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_replay_timing_owner_is_explicit_and_preserves_worker_argv(tmp_path, monkeypatch, platform):
    tool = _load_tool()
    monkeypatch.setattr(tool.sys, "platform", platform)
    compiler, manifest, stage_receipt = _timing_fixture(tmp_path, 0)
    output_dir = tmp_path / "replay"
    command, _environment = tool.prepare_replay(
        compiler=compiler, manifest=manifest, stage_receipt=stage_receipt,
        output_dir=output_dir, native_object=1,
    )
    replay = json.loads((output_dir / "replay.json").read_text())
    worker_argv = [str(compiler), "--pcc-python-multi-codegen-worker",
                   str(output_dir / "worker.manifest")]
    assert command[-3:] == worker_argv
    assert replay["command"] == command
    assert replay["worker_command"] == worker_argv
    if platform == "linux":
        assert replay["timing_owner"] == "linux-wait4"
        assert replay["timing_receipt"] == str(output_dir / "timing.json")
        assert command[:7] == [
            tool.sys.executable, "-I", "-S", str(TOOL), "--_linux-time",
            replay["timing_receipt"], "--",
        ]
    else:
        assert replay["timing_owner"] == "darwin-system-time"
        assert replay["timing_receipt"] == ""
        assert command[:2] == ["/usr/bin/time", "-lp"]


def _timing_fixture(tmp_path, returncode):
    compiler = tmp_path / "ordinary-worker-fixture"
    compiler.write_text(
        "#!/bin/sh\nprintf 'ordinary result\\n'\nprintf 'ordinary stderr\\n' >&2\n"
        + "exit " + str(returncode) + "\n", encoding="utf-8",
    )
    compiler.chmod(0o755)
    manifest = tmp_path / "source.manifest"
    manifest.write_text("\n".join([
        "pcc.frontends.python.codegen_worker.v4", "/frozen/result.tsv",
        "/frozen/artifacts", "/frozen/exports.json", "codegen", "/frozen/ast",
        "entry", "on", "1", "0", "1", "0\tpcc.mod\t/frozen/mod.py", "1", "0",
    ]) + "\n", encoding="utf-8")
    stage_receipt = tmp_path / "stage.json"
    stage_receipt.write_text(json.dumps({"environment": {}}), encoding="utf-8")
    return compiler, manifest, stage_receipt


@pytest.mark.skipif(sys.platform != "linux", reason="Linux wait4 timing")
@pytest.mark.parametrize("returncode", [0, 7])
def test_actual_ordinary_worker_wait4_timing_and_exit_propagation(tmp_path, returncode):
    import subprocess
    tool = _load_tool()
    compiler, manifest, stage_receipt = _timing_fixture(tmp_path, returncode)
    output_dir = tmp_path / "replay"
    command, environment = tool.prepare_replay(
        compiler=compiler, manifest=manifest, stage_receipt=stage_receipt,
        output_dir=output_dir, native_object=1,
    )
    child = subprocess.run(command, env=environment, capture_output=True, timeout=10)
    assert child.returncode == returncode, child.stderr
    assert child.stdout == b"ordinary result\n"
    assert child.stderr == b"ordinary stderr\n"
    receipt = json.loads((output_dir / "timing.json").read_text())
    assert receipt["schema"] == "pcc.codegen-worker-timing.v1"
    assert receipt["command"] == command[-3:]
    assert receipt["returncode"] == returncode
    assert receipt["wall_s"] > 0
    assert receipt["user_s"] >= 0 and receipt["system_s"] >= 0
    assert receipt["cpu_s"] == receipt["user_s"] + receipt["system_s"]
    assert type(receipt["max_rss_bytes"]) is int and receipt["max_rss_bytes"] > 0
    assert receipt["max_rss_bytes"] % 1024 == 0
    assert receipt["rss_scope"] == "wait4_child_maximum_not_tree_aggregate"
    assert "instructions" not in receipt and "cycles" not in receipt


@pytest.mark.skipif(sys.platform != "linux", reason="Linux wait4 timing")
def test_linux_timing_refuses_an_existing_receipt_before_launch(tmp_path):
    import subprocess
    tool = _load_tool()
    compiler, manifest, stage_receipt = _timing_fixture(tmp_path, 0)
    output_dir = tmp_path / "replay"
    command, environment = tool.prepare_replay(
        compiler=compiler, manifest=manifest, stage_receipt=stage_receipt,
        output_dir=output_dir, native_object=1,
    )
    timing = output_dir / "timing.json"
    timing.write_text("existing receipt")
    child = subprocess.run(command, env=environment, capture_output=True, timeout=10)
    assert child.returncode != 0
    assert child.stdout == b""
    assert timing.read_text() == "existing receipt"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux wait4 timing")
def test_actual_replay_cli_execs_portable_timing_wrapper(tmp_path):
    import subprocess
    compiler, manifest, stage_receipt = _timing_fixture(tmp_path, 0)
    output_dir = tmp_path / "replay"
    result = subprocess.run([
        sys.executable, str(TOOL), "--compiler", str(compiler),
        "--manifest", str(manifest), "--stage-receipt", str(stage_receipt),
        "--output-dir", str(output_dir), "--native-object", "1",
    ], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"ordinary result\n"
    assert result.stderr == b"ordinary stderr\n"
    replay = json.loads((output_dir / "replay.json").read_text())
    timing = json.loads((output_dir / "timing.json").read_text())
    assert timing["command"] == replay["worker_command"]
    assert timing["cpu_scope"] == "wait4_child_and_waited_descendants"


@pytest.mark.parametrize("args", [[], ["receipt"], ["receipt", "--"],
                                  ["receipt", "/bin/true"], ["receipt", "--", "/bin/true"]])
def test_internal_linux_timing_requires_fresh_absolute_receipt(tmp_path, args):
    tool = _load_tool()
    with pytest.raises(tool.ReplayError):
        tool._linux_time(args)
