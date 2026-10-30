"""Failure-evidence contracts only; mocked launches are not native acceptance."""

import json
import subprocess

import pytest

from tests.fixtures.native import elf_image_raw_abi as fixture


def _ready(directory):
    (directory / "mapper-host").write_bytes(b"unexecuted fixture identity")
    (directory / "build.json").write_text(json.dumps({"status": "BUILT", "inputs": {}, "artifacts": {}}))


@pytest.mark.parametrize("timeout", [False, True])
def test_target_failure_keeps_exact_streams_and_started_identity(tmp_path, monkeypatch, timeout):
    _ready(tmp_path)

    def fail(command, **options):
        before = json.loads((tmp_path / "execution.json").read_text())
        assert before["status"] == before["results"][0]["status"] == "RUNNING"
        assert before["results"][0]["command"] == command
        assert options["env"]["PATH"] == "" and 0 < options["timeout"] <= 10
        options["stdout"].write(b"partial stdout\x00\xff")
        options["stderr"].write(b"partial stderr\r\n")
        if timeout:
            raise subprocess.TimeoutExpired(command, options["timeout"])
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(subprocess.TimeoutExpired if timeout else AssertionError):
        fixture.execute(tmp_path)
    after = json.loads((tmp_path / "execution.json").read_text())
    assert after["status"] == "FAILED" and len(after["results"]) == 1
    row = after["results"][0]
    assert row["status"] == ("TIMEOUT" if timeout else "COMPLETE")
    assert row["binary_sha256"] == fixture._digest(tmp_path / "mapper-host")
    assert (tmp_path / "mapper-host.stdout.bin").read_bytes() == bytes.fromhex(row["stdout_hex"]) == b"partial stdout\x00\xff"
    assert (tmp_path / "mapper-host.stderr.bin").read_bytes() == bytes.fromhex(row["stderr_hex"]) == b"partial stderr\r\n"
    if not timeout:
        assert row["returncode"] == 7
