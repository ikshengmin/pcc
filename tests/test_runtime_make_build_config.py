"""Make producer plumbing uses owned target/configuration admission.

The controlled frontend supplies tiny IR to the real Make rules and owned
emitter. It does not compile the full runtime or prove native runtime execution.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.python import owned_runtime_build as owned
from pcc.tools import ir_to_obj, runtime_archive_provenance as provenance
from pcc.tools import runtime_module_inventory


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "pcc" / "runtime"
TARGETS = (
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "arm64-apple-darwin",
    "x86_64-pc-windows-msvc",
)


def _forbidden(*_args, **_kwargs):
    raise AssertionError("unexpected external compiler, subprocess or runtime rebuild")


@pytest.fixture
def emitter_inputs(tmp_path, monkeypatch):
    monkeypatch.delenv("PCC_IR_TO_OBJ_EMITTER", raising=False)
    source = tmp_path / "py" / "probe.py"
    source.parent.mkdir()
    source.write_text("def probe() -> int:\n    return 7\n", encoding="utf-8")
    ir = tmp_path / "probe.ll"
    ir.write_text(
        f'target triple = "{TARGETS[0]}"\n'
        "define i32 @probe() {\nentry:\n  ret i32 7\n}\n",
        encoding="utf-8",
    )
    obj = tmp_path / "probe.o"
    receipt = provenance.receipt_path_for_object(obj)
    return [str(ir), str(obj), "--provenance", str(receipt),
            "--source", str(source), "--runtime-root", str(tmp_path)], obj, receipt


@pytest.mark.parametrize("threads, refcount, expected", [
    ("0", "atomic", {"threads": False, "refcount": "atomic"}),
    ("1", "local", {"threads": True, "refcount": "local"}),
    ("1", " ATOMIC ", {"threads": True, "refcount": "atomic"}),
    ("0", "", {"threads": False, "refcount": "atomic"}),
])
def test_emitter_records_explicit_configuration(
    emitter_inputs, monkeypatch, threads, refcount, expected,
):
    args, obj, receipt = emitter_inputs
    monkeypatch.setenv("PCC_WITH_THREADS", "1" if threads == "0" else "0")
    monkeypatch.setenv("PCC_REFCOUNT_KIND", "ambient-other")
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    assert ir_to_obj.main(args + ["--runtime-threads", threads,
                                 "--runtime-refcount", refcount]) == 0
    record = json.loads(receipt.read_text(encoding="utf-8"))
    assert record["runtime_build_config"] == expected
    assert record["target_triple"] == TARGETS[0]
    assert record["uses_host_cc"] is False
    assert obj.read_bytes().startswith(b"\x7fELF")


@pytest.mark.parametrize("options, error", [
    (["--runtime-threads", "1"], "must be used together"),
    (["--runtime-refcount", "atomic"], "must be used together"),
    (["--runtime-threads", "0", "--runtime-refcount", "../atomic"], "refcount"),
    (["--runtime-threads", "0", "--runtime-refcount", "atomic/"], "refcount"),
    (["--runtime-threads", "0", "--runtime-refcount", "not a mode"], "refcount"),
])
def test_invalid_configuration_preserves_published_pair(
    emitter_inputs, monkeypatch, capsys, options, error,
):
    args, obj, receipt = emitter_inputs
    obj.write_bytes(b"previous object")
    receipt.write_bytes(b"previous receipt")
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    assert ir_to_obj.main(args + options) == 1
    assert error in capsys.readouterr().err
    assert obj.read_bytes() == b"previous object"
    assert receipt.read_bytes() == b"previous receipt"
    assert not list(obj.parent.glob(".*.tmp"))
    assert not list(obj.parent.glob(".*.pending"))


def test_runtime_configuration_requires_provenance(emitter_inputs, capsys):
    args, obj, receipt = emitter_inputs
    assert ir_to_obj.main(args[:2] + [
        "--runtime-threads", "0", "--runtime-refcount", "atomic",
    ]) == 1
    assert "--provenance, --source, and --runtime-root" in capsys.readouterr().err
    assert not obj.exists()
    assert not receipt.exists()


def test_generic_receipt_does_not_invent_runtime_configuration(emitter_inputs):
    args, _, receipt = emitter_inputs
    assert ir_to_obj.main(args) == 0
    assert "runtime_build_config" not in json.loads(receipt.read_text())


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("threads", [False, True])
def test_make_inventory_matches_owned_target_membership(tmp_path, target, threads):
    assert shutil.which("make"), "Make is required for its production graph test"
    inspection = tmp_path / "inspection.mk"
    inspection.write_text(
        f"include {RUNTIME / 'Makefile'}\n"
        ".PHONY: inspect-runtime-inventory\n"
        "inspect-runtime-inventory:\n"
        "\t@printf '%s\\n' '$(PCC_RUNTIME_TARGET)' '$(PCC_PY_OBJECTS)'\n",
        encoding="utf-8",
    )
    result = subprocess.run([
        "make", "--no-print-directory", "-rR", "-f", str(inspection),
        f"PYTHON={sys.executable}", f"PCC_REPO_ROOT={ROOT}",
        f"PCC_RUNTIME_TARGET={target}", f"PCC_WITH_THREADS={int(threads)}",
        "inspect-runtime-inventory",
    ], cwd=RUNTIME, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    selected_target, object_line = result.stdout.splitlines()
    names = owned.runtime_modules(str(RUNTIME), target, threads)
    assert selected_target == target
    assert object_line.split() == ["build_py/" + name + ".o" for name in names]
    assert len(names) == len(set(names))
    assert ("freestanding_thread_kernel_pthread" in names) == threads
    assert ("freestanding_thread_kernel" in names) != threads
    assert ("freestanding_c_linux_start" in names) == ("linux" in target)
    assert ("freestanding_linux_aarch64" in names) == target.startswith("aarch64")
    assert ("freestanding_linux_threads" in names) == (threads and "linux" in target)
    assert ("freestanding_windows_threads" in names) == (threads and "windows" in target)


@pytest.mark.parametrize("field", ["modules", "target"])
def test_inventory_rejects_unknown_target_without_partial_output(capsys, field):
    assert runtime_module_inventory.main([
        "--runtime-root", str(RUNTIME), "--target", "unsupported-unknown-none",
        "--field", field,
    ]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "no emitter" in output.err


def _controlled_make_runtime(tmp_path, target, threads, refcount):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    # Retain the actual production recipes and configuration selection. Reduce
    # only the common fixture inventory; the target module additions stay real.
    makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
    makefile = re.sub(r"^PY_MODULES =.*$", "PY_MODULES = probe py_runtime_log", makefile, flags=re.M)
    makefile = re.sub(r"^PY_MODULES \+=.*$", "", makefile, flags=re.M)
    makefile = re.sub(r"^FREESTANDING_PY_MODULES (?:\+)?=.*$", "", makefile, flags=re.M)
    (runtime / "Makefile").write_text(makefile, encoding="utf-8")
    (runtime / "py").mkdir()
    for name in owned.runtime_modules(str(runtime), target, threads):
        (runtime / "py" / (name + ".py")).write_text(
            "# Controlled frontend input; not a functional runtime.\n", encoding="utf-8")
    frontend = tmp_path / "controlled_frontend.py"
    frontend.write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "target = next(value.split('=', 1)[1] for value in args if value.startswith('--target='))\n"
        "out = Path(next(value.split('=', 1)[1] for value in args if value.startswith('--emit-llvm=')))\n"
        "name = Path(args[-1]).stem\n"
        "with open(os.environ['FRONTEND_LOG'], 'a') as log:\n"
        "    log.write(json.dumps({'name': name, 'target': target, 'threads': os.environ.get('PCC_WITH_THREADS'), 'refcount': os.environ.get('PCC_REFCOUNT_KIND')}) + '\\n')\n"
        "out.write_text('target triple = \"' + target + '\"\\ndefine i32 @PyProbe_' + name + '() {\\nentry:\\n  ret i32 7\\n}\\n')\n",
        encoding="utf-8",
    )
    host_cc = tmp_path / "forbidden-host-cc"
    host_cc.write_text("#!/bin/sh\necho unexpected-host-cc >&2\nexit 99\n", encoding="utf-8")
    host_cc.chmod(0o755)
    log = tmp_path / "frontend.jsonl"
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith("PCC_"):
            environment.pop(key)
    environment.update(FRONTEND_LOG=str(log), CC=str(host_cc),
                       PCC_WITH_THREADS="0" if threads else "1",
                       PCC_REFCOUNT_KIND="ambient-other")
    result = subprocess.run([
        "make", "--no-print-directory", "-rR", "-j1",
        f"PYTHON={sys.executable}", f"PCC_REPO_ROOT={ROOT}",
        f"PCC={shlex.quote(sys.executable)} {shlex.quote(str(frontend))}",
        f"PCC_RUNTIME_TARGET={target}", f"PCC_WITH_THREADS={int(threads)}",
        f"PCC_REFCOUNT_KIND={refcount}", f"CC={host_cc}",
        "AR=ar", "RANLIB=ranlib", "libpy_runtime_pcc_py.a",
    ], cwd=runtime, env=environment, capture_output=True, text=True, timeout=90)
    (tmp_path / "make.stdout").write_text(result.stdout, encoding="utf-8")
    (tmp_path / "make.stderr").write_text(result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    return runtime, [json.loads(line) for line in log.read_text().splitlines()]


@pytest.mark.parametrize("threads, refcount", [
    (False, "atomic"), (True, "atomic"), (False, "local"), (True, "local"),
])
def test_real_make_recipes_record_archive_configuration(tmp_path, monkeypatch, threads, refcount):
    target = TARGETS[0]
    runtime, calls = _controlled_make_runtime(tmp_path, target, threads, refcount)
    archive = runtime / "libpy_runtime_pcc_py.a"
    config = {"threads": threads, "refcount": refcount}
    names = owned.runtime_modules(str(runtime), target, threads)
    assert [call["name"] for call in calls] == names
    for call in calls:
        assert call["target"] == target
        assert call["refcount"] == refcount
        assert call["threads"] == ("0" if call["name"] in owned._NO_IMPLICIT_POLL_MODULES else str(int(threads)))
    manifest = provenance.verify_runtime_archive_manifest(archive, runtime_root=runtime)
    assert [row["member"] for row in manifest["members"]] == [name + ".o" for name in names]
    assert all(row["runtime_build_config"] == config for row in manifest["members"])
    assert owned._manifest_matches_config(manifest, str(runtime), target, config)
    for key in ("PCC_RUNTIME_DIR", "PCC_RUNTIME_ARCHIVE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PCC_WITH_THREADS", str(int(threads)))
    monkeypatch.setenv("PCC_REFCOUNT_KIND", refcount)
    monkeypatch.setattr(owned, "build_runtime_archive", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    assert owned.ensure_target_runtime(str(runtime), target, explicit_archive=str(archive)) == str(archive)

    # Preserve valid payload/source hashes to exercise configuration admission,
    # rather than letting a generic integrity mismatch conceal the producer gap.
    manifest_path = provenance.manifest_path_for_archive(archive)
    original = manifest_path.read_bytes()
    for tamper in ("absent", "null", "threads", "refcount", "codegen"):
        changed = json.loads(original)
        member = changed["members"][0]
        if tamper == "absent":
            del member["runtime_build_config"]
        elif tamper == "null":
            member["runtime_build_config"] = None
        elif tamper == "threads":
            member["runtime_build_config"]["threads"] = not threads
        elif tamper == "refcount":
            member["runtime_build_config"]["refcount"] = "different"
        else:
            member["codegen_checksum"] = "0" * 64
        changed["members_sha256"] = provenance._members_sha256(changed["members"])
        manifest_path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError, match="runtime build configuration|target/source configuration"):
            owned.ensure_target_runtime(str(runtime), target, explicit_archive=str(archive))
    manifest_path.write_bytes(original)
    source = runtime / "py" / "probe.py"
    source.write_text(source.read_text() + "# changed source\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source"):
        owned.ensure_target_runtime(str(runtime), target, explicit_archive=str(archive))
