"""Deterministic Linux UAPI ring controls for pcc_perf's preflight."""
import importlib.util
import struct
import subprocess
import sys
from pathlib import Path

import pytest

# The engine is loaded only for the Linux command; these synthetic native-
# endian rings also run on hosts that cannot open a Linux perf event.
MODULE = Path(__file__).resolve().parents[2] / "scripts/pcc_perf_linux.py"
SPEC = importlib.util.spec_from_file_location("_pcc_task_clock_test", MODULE)
RING = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RING
SPEC.loader.exec_module(RING)


def _sample(ip=0x2000, pid=7, tid=9, period=500_000):
    return struct.pack("=IHHQIIQ", 9, 2, 32, ip, pid, tid, period)


def _ring(data, *, tail=0, capacity=128):
    offset = 4096
    mapping = bytearray(offset + capacity)
    struct.pack_into("=QQQQ", mapping, 1024, tail + len(data), tail, offset, capacity)
    for i, byte in enumerate(data):
        mapping[offset + (tail + i) % capacity] = byte
    return mapping


def test_task_clock_sample_header_and_payload_decode_across_wrap():
    expected = [RING.TaskSample(0x2000, 7, 9, 500_000),
                RING.TaskSample(0x2001, 7, 9, 1_000_000)]
    records = _sample() + _sample(0x2001, period=1_000_000)
    for tail in (0, 128 - 3, 128 - 17, 128 + 64):
        mapping = _ring(records, tail=tail)
        decoded = RING._read_frozen_ring(mapping)
        assert decoded == expected
        assert struct.unpack_from("=Q", mapping, 1032)[0] == tail


@pytest.mark.parametrize(("data", "match"), (
    (b"\0" * 3, "truncated perf record header"),
    (struct.pack("=IHH", 9, 2, 0), "invalid perf record size"),
    (struct.pack("=IHH", 9, 2, 64), "invalid perf record size"),
    (struct.pack("=IHH", 9, 2, 8), "unexpected perf record"),
    (struct.pack("=IHH", 2, 0, 8), "lost samples"),
    (struct.pack("=IHH", 13, 0, 8), "lost samples"),
    (_sample(ip=0), "incomplete perf sample"),
    (_sample(tid=0), "incomplete perf sample"),
))
def test_task_clock_refuses_incomplete_or_unusable_attribution(data, match):
    with pytest.raises(ValueError, match=match):
        RING._read_frozen_ring(_ring(data))


def test_task_clock_refuses_invalid_ring_size_and_overrun():
    for head, tail, size in ((64, 100, 128), (129, 0, 128), (32, 0, 127), (32, 0, 256)):
        mapping = _ring(_sample())
        struct.pack_into("=QQQQ", mapping, 1024, head, tail, 4096, size)
        with pytest.raises(ValueError, match="metadata or overrun"):
            RING._read_frozen_ring(mapping)


def test_profile_preflight_invalid_request_refused_by_real_cli():
    script = MODULE.with_name("pcc_perf.py")
    p = subprocess.run([sys.executable, str(script), "profile-preflight", "--cpu-ms", "251"],
                       capture_output=True, text=True, timeout=5)
    assert p.returncode != 0
    expected = ("cpu-ms must be between 10 and 250" if sys.platform == "linux"
                else "profile-preflight requires Linux")
    assert expected in p.stderr
