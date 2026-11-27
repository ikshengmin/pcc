"""The opt-in map belongs to the actual final owned ELF pipeline output."""

import hashlib
import json
import os
import platform
import subprocess
import sys

import pytest

from pcc.backend import elf_x86_64 as elf
from pcc.backend import owned_elf_link
from pcc.frontends.python import pipeline
from pcc.frontends.python import pipeline_self_backend_link as self_link
from tests.python.test_elf_x86_64 import _exit_42_object

TARGET = "x86_64-unknown-linux-gnu"
ENV = "PCC_OWNED_ELF_FUNCTION_MAP_FOR"
SUFFIX = ".functions.jsonl"
pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or platform.machine().lower() not in ("x86_64", "amd64"),
    reason="actual Linux x86-64 owned final ELF pipeline",
)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _input(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_SELF_LINK", "pcc")
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv("PCC_DEFER_SELF_LINK_PLAN", raising=False)
    monkeypatch.delenv("PCC_DEBUG_SELF_IR_DUMP_DIR", raising=False)
    monkeypatch.delenv("PCC_MACHO_SEMANTIC_LAYOUT", raising=False)
    path = tmp_path / "start.pco"
    path.write_bytes(elf.emit_relocatable(_exit_42_object()))
    return path


def _stage_map(output):
    return output.with_name(output.name + ".tmp" + SUFFIX)


def _map(output):
    return output.with_name(output.name + SUFFIX)


def _direct(input_path, output):
    pipeline._link_with_self_backend_direct_artifacts(
        [("actual-owned-fixture", "PCO", str(input_path))], str(output),
        None, False, target_triple=TARGET,
    )


def _link_only(input_path, output, target=TARGET):
    pipeline._run_self_link_command(
        [], None, str(output) + ".tmp", None, (), False,
        pcc_native_object_inputs=(str(input_path),), target_triple=target,
    )


def _finish(output):
    pipeline._finish_self_backend_executable(str(output) + ".tmp", str(output), None,
                                             signature_owned_by_pcc=True)


def test_actual_direct_pipeline_final_map_and_native_program(tmp_path, monkeypatch):
    input_path = _input(tmp_path, monkeypatch)
    off, unrelated, on = (tmp_path / name for name in ("off", "unrelated", "on"))
    real_link = owned_elf_link.link_inputs
    calls = []
    def observe(**kwargs):
        calls.append(dict(kwargs))
        return real_link(**kwargs)
    monkeypatch.setattr(owned_elf_link, "link_inputs", observe)
    _direct(input_path, off)
    monkeypatch.setenv(ENV, str(on))
    _direct(input_path, unrelated)
    assert not _map(off).exists() and not _map(unrelated).exists()
    _direct(input_path, on)
    assert len(calls) == 3
    assert "function_map_path" not in calls[0] and "function_map_path" not in calls[1]
    assert calls[2]["function_map_path"] == str(_stage_map(on))
    assert not _stage_map(on).exists()
    assert off.read_bytes() == unrelated.read_bytes() == on.read_bytes()
    lines = _map(on).read_text().splitlines()
    header = json.loads(lines[0])
    assert header["schema"] == "pcc.owned-elf-function-map.v1"
    assert header["target"] == TARGET
    assert header["image_sha256"] == _sha(on)
    assert header["image_bytes"] == on.stat().st_size
    assert header["function_count"] == len(lines) - 1
    assert {json.loads(line)["name"] for line in lines[1:]} >= {"_start", "answer"}
    # This is a real native executable created by the same final publication
    # path as Stage1; neither a fabricated map nor a host executable oracle.
    assert subprocess.run([str(on)], check=False).returncode == 42


@pytest.mark.parametrize("collision", ["final-hardlink", "final-dangling", "stage-hardlink", "stage-temp-hardlink", "image-temp-hardlink", "inner-image-temp-hardlink"])
def test_opt_in_rejects_existing_output_aliases(tmp_path, monkeypatch, collision):
    input_path = _input(tmp_path, monkeypatch)
    output = tmp_path / "program"
    monkeypatch.setenv(ENV, str(output))
    protected = {
        "final-hardlink": _map(output), "final-dangling": _map(output),
        "stage-hardlink": _stage_map(output),
        "stage-temp-hardlink": _stage_map(output).with_name(_stage_map(output).name + ".pcc-link.tmp"),
        "image-temp-hardlink": output.with_name(output.name + ".tmp"),
        "inner-image-temp-hardlink": output.with_name(output.name + ".tmp.pcc-link.tmp"),
    }[collision]
    before = input_path.read_bytes()
    if collision == "final-dangling":
        os.symlink(tmp_path / "missing", protected)
    else:
        os.link(input_path, protected)
    with pytest.raises(pipeline.PyPipelineError, match="function map"):
        _direct(input_path, output)
    assert input_path.read_bytes() == before and not output.exists()
    assert os.path.lexists(protected)


@pytest.mark.parametrize("route", ["arm", "external"])
def test_opt_in_rejects_wrong_link_owner_before_delegation(tmp_path, monkeypatch, route):
    input_path = _input(tmp_path, monkeypatch)
    output = tmp_path / "program"
    monkeypatch.setenv(ENV, str(output))
    if route == "external":
        monkeypatch.setenv("PCC_SELF_LINK", "cc")
    def forbidden(*args, **kwargs):
        pytest.fail("function map request attempted an unsupported link owner")
    monkeypatch.setattr(self_link.subprocess, "run", forbidden)
    monkeypatch.setattr(owned_elf_link, "link_inputs", forbidden)
    with pytest.raises(pipeline.PyPipelineError, match="function map"):
        _link_only(input_path, output, "aarch64-unknown-linux-gnu" if route == "arm" else TARGET)
    assert not output.exists() and not _map(output).exists()


@pytest.mark.parametrize("damage", ["missing", "image", "sha", "size", "target", "count"])
def test_final_publish_refuses_missing_or_mismatched_staged_map(tmp_path, monkeypatch, damage):
    input_path = _input(tmp_path, monkeypatch)
    output = tmp_path / "program"
    output.write_bytes(b"previous image")
    monkeypatch.setenv(ENV, str(output))
    _link_only(input_path, output)
    staged = _stage_map(output)
    assert staged.is_file() and not _map(output).exists()
    if damage == "missing":
        staged.unlink()
    elif damage == "image":
        with open(str(output) + ".tmp", "ab") as stream:
            stream.write(b"different")
    else:
        lines = staged.read_text().splitlines()
        header = json.loads(lines[0])
        if damage == "sha": header["image_sha256"] = "0" * 64
        elif damage == "size": header["image_bytes"] += 1
        elif damage == "target": header["target"] = "aarch64-unknown-linux-gnu"
        else: header["function_count"] += 1
        lines[0] = json.dumps(header)
        staged.write_text("\n".join(lines) + "\n")
    with pytest.raises(self_link.SelfBackendLinkError, match="function map"):
        _finish(output)
    assert output.read_bytes() == b"previous image" and not _map(output).exists()


@pytest.mark.parametrize("failed", ["image", "map"])
def test_final_rename_failure_never_publishes_valid_map(tmp_path, monkeypatch, failed):
    input_path = _input(tmp_path, monkeypatch)
    output = tmp_path / "program"
    output.write_bytes(b"previous image")
    monkeypatch.setenv(ENV, str(output))
    _link_only(input_path, output)
    real_replace = os.replace
    def fail(src, dest):
        if os.fspath(dest) == str(output if failed == "image" else _map(output)):
            raise OSError("injected final publication failure")
        return real_replace(src, dest)
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="injected final publication failure"):
        _finish(output)
    assert not _map(output).exists()
    if failed == "image":
        assert output.read_bytes() == b"previous image"
    else:
        elf.parse_static_executable(output.read_bytes())
