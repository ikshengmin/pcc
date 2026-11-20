"""Host-only admission tests; the successful checks do not start a child."""
import json
from pathlib import Path

import pytest
from scripts import run_process_tree_sample as guard


CAP = 1 << 30
RESERVE = 7 << 30
REQUIRED = CAP + RESERVE


def install_meminfo(monkeypatch, text):
    # The explicit-cap check reads this one physical snapshot only. Automatic
    # sizing and cgroup visibility have different contracts and separate tests.
    def read(path, *, encoding):
        assert path == Path("/proc/meminfo") and encoding == "ascii"
        if isinstance(text, Exception):
            raise text
        return text
    monkeypatch.setattr(guard.sys, "platform", "linux")
    monkeypatch.setattr(Path, "read_text", read)


def observation(reserve=RESERVE):
    return guard._linux_physical_resource_preflight(max_tree_rss_bytes=CAP, reserve_bytes=reserve)


def info(available):
    return f"MemTotal: 10485760 kB\nMemFree: 12 kB\nMemAvailable: {available} kB\n"


@pytest.mark.parametrize("extra", [0, 2048])
def test_snapshot_fits_at_equality_and_above(monkeypatch, extra):
    install_meminfo(monkeypatch, info(REQUIRED // 1024 + extra))
    assert observation() == {
        "kind": "linux_host_memavailable", "max_tree_rss_bytes": CAP,
        "reserve_bytes": RESERVE, "required_physical_bytes": REQUIRED,
        "mem_available_bytes": REQUIRED + extra * 1024, "mem_total_bytes": 10 << 30,
    }


@pytest.mark.parametrize("text", [
    "", "MemTotal: 10240000 kB\n", "MemAvailable: 1234 kB\n",
    info(-1), info("1e8"), info("+1234"), info(1.5), info(1 << 64),
    info(18014398509481984), info(10485761), info(100).replace("10485760", "0"),
    info(100).replace("100 kB", "100 MB"), info(100).replace("100 kB", "100"),
    info(100) + "MemAvailable: 100 kB\n", info(100) + "MemAvailable 100 kB\n",
    PermissionError("unreadable"), UnicodeDecodeError("ascii", b"\xff", 0, 1, "invalid"),
])
def test_unusable_physical_observation_fails_closed(monkeypatch, text):
    install_meminfo(monkeypatch, text)
    with pytest.raises(guard.ProcessTreeSampleError) as rejected:
        observation()
    assert rejected.value.resource_preflight["required_physical_bytes"] == REQUIRED


def test_exactly_one_byte_short_cannot_be_rounded_up(monkeypatch):
    install_meminfo(monkeypatch, info(REQUIRED // 1024))
    with pytest.raises(guard.ProcessTreeSampleError, match="insufficient") as rejected:
        observation(RESERVE + 1)
    assert rejected.value.resource_preflight["mem_available_bytes"] == REQUIRED


def cli(tmp_path, options):
    return ["--result", str(tmp_path / "receipt.json"), "--samples", str(tmp_path / "samples"),
            "--stdout", str(tmp_path / "stdout"), "--stderr", str(tmp_path / "stderr"),
            "--cwd", str(tmp_path), "--timeout", "900", *options, "--", "must-not-launch"]


@pytest.mark.parametrize("opts", [
    ["--max-tree-rss-bytes", str(CAP), "--linux-physical-preflight-reserve-bytes", "0"],
    ["--max-tree-rss-bytes", str(CAP), "--linux-physical-preflight-reserve-bytes", "-1"],
    ["--max-tree-rss-bytes", "0", "--linux-physical-preflight-reserve-bytes", "2"],
    ["--auto-tree-rss-ceiling-bytes", str(CAP), "--linux-physical-preflight-reserve-bytes", "2"],
    ["--preserve-worker-budget", "--max-tree-rss-bytes", str(CAP),
     "--linux-physical-preflight-reserve-bytes", "2"],
])
def test_invalid_or_re_admitted_budget_does_not_launch(tmp_path, monkeypatch, opts):
    monkeypatch.setattr(guard.subprocess, "Popen", lambda *_a, **_k: pytest.fail("launched"))
    assert guard.main(cli(tmp_path, opts)) == 2
    assert not (tmp_path / "samples").exists()


def test_reserve_platforms_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        guard._parser().parse_args(cli(tmp_path, [
            "--max-tree-rss-bytes", str(CAP), "--darwin-preflight-reserve-bytes", "1",
            "--linux-physical-preflight-reserve-bytes", "1",
        ]))


def test_wrong_host_fails_before_observing_memory(monkeypatch):
    monkeypatch.setattr(guard.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: pytest.fail("read non-Linux host"))
    with pytest.raises(guard.ProcessTreeSampleError, match="Linux physical preflight"):
        observation()


@pytest.mark.parametrize("text,reserve", [
    (FileNotFoundError("no meminfo"), RESERVE),
    ("MemTotal: 10485760 kB\n", RESERVE),
    (info(100).replace("100 kB", "100 MB"), RESERVE),
    (info(REQUIRED // 1024) + "MemAvailable: 8388608 kB\n", RESERVE),
    (info(REQUIRED // 1024), RESERVE + 1),
    (info(0), RESERVE),
], ids=["unreadable", "missing", "malformed", "ambiguous", "one-byte-short", "zero-available"])
def test_cli_writes_rejection_before_target_or_samples(tmp_path, monkeypatch, text, reserve):
    launches = []

    def must_not_launch(*args, **kwargs):
        launches.append(args)
        pytest.fail("guard attempted to start target")

    monkeypatch.setattr(guard.subprocess, "Popen", must_not_launch)
    with monkeypatch.context() as m:
        install_meminfo(m, text)
        assert guard.main(cli(tmp_path, [
            "--max-tree-rss-bytes", str(CAP),
            "--linux-physical-preflight-reserve-bytes", str(reserve),
        ])) == 2
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert not launches
    assert receipt["status"] == "PREFLIGHT_REJECTED"
    preflight = receipt["resource_preflight"]
    assert (preflight["max_tree_rss_bytes"], preflight["reserve_bytes"]) == (CAP, reserve)
    assert preflight["required_physical_bytes"] == CAP + reserve
    if reserve == RESERVE + 1:
        assert preflight["mem_available_bytes"] == preflight["required_physical_bytes"] - 1
    assert (receipt["timeout_s"], receipt["max_tree_rss_bytes"]) == (900, CAP)
    assert not any((tmp_path / n).exists() for n in [
        "stdout", "stderr", "samples", "receipt.json.worker-rss.tsv",
    ])


def test_cli_exact_fit_preserves_requested_cap_at_spawn_boundary(tmp_path, monkeypatch):
    # Stop at the launch boundary itself: this verifies admission and the
    # original worker cap, without running any child, compiler or benchmark.
    launches = []

    def capture(command, **kwargs):
        launches.append((command, kwargs["env"]["PCC_WORKER_TREE_BUDGET_BYTES"]))
        raise OSError("synthetic stop at spawn boundary")

    monkeypatch.setattr(guard.subprocess, "Popen", capture)
    with monkeypatch.context() as m:
        install_meminfo(m, info(REQUIRED // 1024))
        assert guard.main(cli(tmp_path, [
            "--max-tree-rss-bytes", str(CAP),
            "--linux-physical-preflight-reserve-bytes", str(RESERVE),
        ])) == 2
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert launches == [(["must-not-launch"], str(CAP))]
    assert receipt["status"] == "SAMPLER_ERROR" and "synthetic stop" in receipt["error"]
    assert receipt["max_tree_rss_bytes"] == CAP
    assert receipt["resource_preflight"]["mem_available_bytes"] == REQUIRED
    assert receipt["resource_preflight"]["required_physical_bytes"] == REQUIRED
    assert receipt["resource_preflight"]["reserve_bytes"] == RESERVE
