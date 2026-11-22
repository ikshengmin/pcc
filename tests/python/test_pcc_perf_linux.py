"""Deterministic Linux UAPI ring controls for pcc_perf's preflight."""
import hashlib
import importlib.util
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from tests.python.owned_regression_support import explicit_owned_runtime

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
    (struct.pack("=IHH", 9, 1, 32) + _sample()[8:], "non-user perf sample"),
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


def test_child_protocol_refuses_a_persistently_open_output_pipe():
    if sys.platform != "linux":
        return
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as pipe:
        try:
            with pytest.raises(ValueError, match="left an output pipe open"):
                RING._verify_marker_eof(pipe)
        finally:
            os.close(write_fd)
        RING._verify_marker_eof(pipe)


@pytest.mark.integration
def test_owned_native_child_emits_user_task_samples(tmp_path, explicit_owned_runtime, monkeypatch):
    from tests.owned_c_execution import compile_owned_c_with_runtime

    if sys.platform != "linux":
        # The real CLI rejection is covered above without calling perf_event_open.
        with pytest.raises(OSError, match="requires Linux"):
            RING.capture_cooperative_child((), env={})
        return
    source = Path(__file__).with_name("fixtures") / "pcc_perf_linux_user_loop.c"
    binary = tmp_path / "task_clock_owned"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    samples = RING.capture_cooperative_child([str(binary)], env={"PATH": "/nonexistent"})
    assert len(samples) > 0 and len({sample.ip for sample in samples}) > 0
    assert len({(sample.pid, sample.tid) for sample in samples}) == 1
    # Resolve addresses from the exact owned ELF, even though the linker
    # emits no symbol table. Do not treat unmapped (e.g. vDSO) as its code.
    blob = binary.read_bytes()
    image = RING.read_executable_image(blob)
    offsets = [image.file_offset(sample.ip) for sample in samples]
    assert any(offset is not None and offset < len(blob) for offset in offsets)
    assert image.file_offset(0) is None

    expected = hashlib.sha256(blob).hexdigest()
    resolved = RING.capture_sealed_cooperative_child(binary, expected, env={"PATH": "/nonexistent"})
    assert any(sample.file_offset is not None for sample in resolved)
    assert sum(sample.sample.period_ns for sample in resolved) > 0
    original_capture = RING.capture_cooperative_child

    def move_original_after_open(argv, **kwargs):
        binary.rename(binary.with_suffix(".original"))
        binary.write_bytes(b"another file at the original pathname")
        return original_capture(argv, **kwargs)

    monkeypatch.setattr(RING, "capture_cooperative_child", move_original_after_open)
    resolved = RING.capture_sealed_cooperative_child(binary, expected, env={"PATH": "/nonexistent"})
    assert any(sample.file_offset is not None for sample in resolved)
    with pytest.raises(ValueError, match="different SHA-256"):
        RING.capture_sealed_cooperative_child(binary, expected, env={"PATH": "/nonexistent"})


def _elf(*segments, kind=2, machine=62, entry=0x400080):
    data = bytearray(4096)
    struct.pack_into("<16sHHIQQQIHHHHHH", data, 0, b"\x7fELF\x02\x01\x01",
                     kind, machine, 1, entry, 64, 0, 0, 64, 56, len(segments), 0, 0, 0)
    for index, segment in enumerate(segments):
        struct.pack_into("<IIQQQQQQ", data, 64 + 56 * index, *segment)
    return bytes(data)


_EXE_LOAD = (1, 5, 0, 0x400000, 0, 512, 768, 4096)


def test_elf_pc_mapping_excludes_memory_tail_gaps_and_non_executable_data():
    data_load = (1, 6, 2048, 0x401800, 0, 256, 256, 4096)
    image = RING.read_executable_image(_elf(data_load, _EXE_LOAD))
    assert image.file_offset(0x400080) == 0x80
    assert image.file_offset(0x4001FF) == 511
    for address in (0x3FFFFF, 0x400200, 0x4002FF, 0x400300, 0x401800):
        assert image.file_offset(address) is None


@pytest.mark.parametrize(("data", "reason"), (
    (b"\x7fELF", "truncated"),
    (_elf(_EXE_LOAD, kind=3), "ET_EXEC"),
    (_elf(_EXE_LOAD, machine=243), "ET_EXEC"),
    (_elf(_EXE_LOAD, (3, 0, 0, 0, 0, 0, 0, 0)), "dynamic ELF"),
    (_elf(_EXE_LOAD, (2, 0, 0, 0, 0, 0, 0, 0)), "dynamic ELF"),
    (_elf((1, 5, 4095, 0x400000, 0, 2, 2, 1)), "load extent"),
    (_elf((1, 5, 0, 0x400000, 0, 300, 299, 1)), "load extent"),
    (_elf((1, 5, 0, 0x400000, 0, 512, 768, 3)), "load alignment"),
    (_elf(_EXE_LOAD, _EXE_LOAD), "overlapping"),
    (_elf(_EXE_LOAD, entry=0x400200), "entry"),
), ids=("short", "pie", "wrong-machine", "interpreter", "dynamic",
        "past-file", "past-mem", "alignment", "overlap", "non-text-entry"))
def test_elf_pc_mapping_fails_closed(data, reason):
    with pytest.raises(ValueError, match=reason):
        RING.read_executable_image(data)


def test_sealed_capture_rejects_in_place_content_drift(tmp_path, monkeypatch):
    if sys.platform != "linux":
        return
    binary = tmp_path / "path"
    expected_machine = RING.ELF_MACHINES.get(RING.platform.machine())
    if expected_machine is None:
        # On a different Linux architecture ELF parsing fails earlier.
        expected_machine = RING.ELF_MACHINES["x86_64"]
        monkeypatch.setattr(RING.platform, "machine", lambda: "x86_64")
    original = _elf(_EXE_LOAD, machine=expected_machine)
    binary.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()

    def mutate(_, **kwargs):
        # Use a stand-in for the completed successful child; the post-run
        # guard must reread the file rather than trusting the initial bytes.
        binary.write_bytes(original[:-1] + b"X")
        return [RING.TaskSample(0x400080, 7, 7, 500_000)]

    monkeypatch.setattr(RING, "capture_cooperative_child", mutate)
    with pytest.raises(ValueError, match="changed during capture"):
        RING.capture_sealed_cooperative_child(binary, digest, env={})


@pytest.mark.parametrize(("counters", "reason"), (
    ((1, 2, 2, 9), "lost perf samples"),
    ((1, 2, 1, 0), "full enabled time"),
    ((0, 2, 2, 0), "full enabled time"),
    ((1, 0, 0, 0), "full enabled time"),
))
def test_perf_read_counters_reject_loss_or_incomplete_scheduling(counters, reason):
    with pytest.raises(ValueError, match=reason):
        RING._decode_event_read(struct.pack("=QQQQ", *counters))


def test_perf_read_counters_accept_exact_scheduling_not_a_short_read():
    full = struct.pack("=QQQQ", 3, 4, 4, 0)
    assert RING._decode_event_read(full) == (3, 4)
    with pytest.raises(ValueError, match="truncated perf"):
        RING._decode_event_read(full[:-1])


@pytest.mark.integration
def test_kernel_reports_loss_even_without_room_for_lost_record():
    if sys.platform != "linux":
        return
    import time

    # 32 bytes/sample, one data page. Keep the scope to one owned
    # thread and one bounded CPU burst; never change host sampling policy.
    with RING._task_ring(0, 100_000, data_pages=1) as (fd, mapping):
        wall = time.monotonic_ns() + 3_000_000_000
        started = time.thread_time_ns()
        with RING._enabled(fd):
            while time.thread_time_ns() - started < 250_000_000:
                assert time.monotonic_ns() < wall, "bounded CPU fixture did not finish"
        with pytest.raises(ValueError, match="kernel counted [1-9][0-9]* lost perf samples"):
            RING._read_capture(fd, mapping)


@pytest.mark.integration
def test_owned_native_four_task_cohort_emits_four_distinct_sample_streams(tmp_path, explicit_owned_runtime):
    from tests.owned_c_execution import compile_owned_c_with_runtime

    if sys.platform != "linux":
        with pytest.raises(OSError, match="requires Linux"):
            RING.capture_cooperative_cohort((), env={}, expected_tasks=4)
        return
    source = Path(__file__).with_name("fixtures") / "pcc_perf_linux_cohort.c"
    binary = tmp_path / "task_clock_cohort"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    clean_env = {"PATH": "/nonexistent", "PCC_GC_BACKEND": "0"}
    receipts = RING.capture_cooperative_cohort([str(binary)], env=clean_env, expected_tasks=4)
    assert len(receipts) == 4
    assert len({task.tid for task in receipts}) == 4
    assert all(task.clock_ns > 0 and task.running_ns > 0 and task.samples for task in receipts)
    assert all({sample.tid for sample in task.samples} == {task.tid} for task in receipts)
    assert len({sample.pid for task in receipts for sample in task.samples}) == 1
    with pytest.raises(ValueError, match="expected 3 owned tasks, saw 4"):
        RING.capture_cooperative_cohort([str(binary)], env=clean_env, expected_tasks=3)


def test_exact_site_tally_validates_provenance_and_retains_unknown_denominator():
    image = _elf(_EXE_LOAD)
    sha = hashlib.sha256(image).hexdigest()
    rows = [RING.SealedSample(RING.TaskSample(pc, 7, 7, 500_000), offset, sha)
            for pc, offset in ((0x400080, 128), (0x400081, 129), (1, None))]
    site = RING.ExactSite("site", 128, image[128:130])
    assert RING.exact_site_sample_tally(rows, image, [site]) == {
        "exact_site_samples": {"site": 1}, "other_executable_samples": 1,
        "unmapped_samples": 1, "total_samples": 3,
    }
    with pytest.raises(ValueError, match="site bytes"):
        RING.exact_site_sample_tally(rows, image, [RING.ExactSite("bad", 128, b"\xff")])
    with pytest.raises(ValueError, match="site is not entirely"):
        RING.exact_site_sample_tally(rows, image, [RING.ExactSite("tail", 511, b"\0\0")])
    with pytest.raises(ValueError, match="does not match"):
        RING.exact_site_sample_tally(
            [RING.SealedSample(rows[0].sample, 129, sha)], image, [site],
        )
    with pytest.raises(ValueError, match="does not match"):
        RING.exact_site_sample_tally(
            [RING.SealedSample(rows[0].sample, 128, "0" * 64)], image, [site],
        )


@pytest.mark.integration
def test_owned_linux_exact_instruction_calibration(tmp_path):
    if sys.platform != "linux" or RING.platform.machine() != "x86_64":
        return
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file

    source = Path(__file__).with_name("fixtures") / "pcc_perf_linux_atomic_x64_asm.txt"
    for locked in (False, True):
        asm = source.read_text()
        if not locked:
            asm = asm.replace("lock xadd", "xadd")
        obj = assemble_file(asm)
        syms = {symbol.name: symbol for symbol in obj.symbols}
        payload = link_static_executable([obj])
        binary = tmp_path / ("atomic" if locked else "plain")
        binary.write_bytes(payload)
        binary.chmod(0o755)
        entry = RING.ELF_HEADER.unpack_from(payload)[4]
        pc = entry + syms["probe_instruction"].value - syms["_start"].value
        loop_end = entry + syms["probe_loop_end"].value - syms["_start"].value
        assert syms["probe_instruction"].section_index == syms["_start"].section_index
        offset = RING.read_executable_image(payload).file_offset(pc)
        assert offset is not None
        code = payload[offset:offset + (9 if locked else 8)]
        assert code.startswith(bytes.fromhex("f0 48 0f c1 05" if locked else "48 0f c1 05"))
        rows = RING.capture_sealed_cooperative_child(
            binary, hashlib.sha256(payload).hexdigest(), env={"PATH": "/nonexistent"},
        )
        assert any(pc <= row.sample.ip < loop_end for row in rows)
        result = RING.exact_site_sample_tally(rows, payload, [RING.ExactSite("xadd", offset, code)])
        assert (result["exact_site_samples"]["xadd"] + result["other_executable_samples"]
                + result["unmapped_samples"]) == len(rows)
        # Task-clock sampling is not evidence that the processor spent one
        # sample's worth of time executing the instruction at each exact IP.
        print({"locked": locked, **result})
