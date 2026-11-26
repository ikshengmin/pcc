"""Host-only observer controls: no real process inventory or child launch."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import platform_process_watchdog as watchdog


def _stat(user="13", system="7", *, start="100", comm="owned", parent=1):
    fields = ["S", str(parent)] + ["0"] * 18
    fields[11:15] = [str(user), str(system), "999999", "888888"]
    fields[19] = str(start)
    return "10 (" + comm + ") " + " ".join(fields)


def _proc(monkeypatch, records):
    reads = []

    class Entry:
        def __init__(self, pid):
            self.name = str(pid)

        def __truediv__(self, field):
            def read_text():
                reads.append((int(self.name), field))
                row = records[int(self.name)]
                if field == "status":
                    return f"PPid:\t{row.get('parent', 1)}\nVmRSS:\t{row.get('rss', 2)} kB\n"
                assert field == "stat"
                value = row["stat"]
                if isinstance(value, Exception):
                    raise value
                return value
            return SimpleNamespace(read_text=read_text)

    def path(name):
        assert name == "/proc", "no additional inventory path is allowed"
        return SimpleNamespace(iterdir=lambda: [Entry(pid) for pid in records])

    monkeypatch.setattr(watchdog, "Path", path)
    monkeypatch.setattr(watchdog, "os", SimpleNamespace(readlink=lambda name: "/owned/pcc"))
    return reads


@pytest.mark.parametrize("user,system,comm,expected", [
    ("13", "7", "owned", (13, 7)),
    ("13", "7", "a name with ) parentheses", (13, 7)),
    (str(2 ** 61 + 3), str(2 ** 60 + 1), "owned", (2 ** 61 + 3, 2 ** 60 + 1)),
    ("0", "0", "owned", (0, 0)),
    ("bad", "7", "owned", (None, None)),
    ("-1", "7", "owned", (None, None)),
], ids=["ordinary", "complex-comm", "large", "zero", "malformed", "negative"])
def test_cpu_fields_preserve_rss_and_use_the_same_stat_read(monkeypatch, user, system, comm, expected):
    reads = _proc(monkeypatch, {10: {"stat": _stat(user, system, comm=comm)}})
    old_known, observed_known, cpu = {}, {}, []
    old = watchdog.linux_sample(10, old_known)
    old_reads = list(reads)
    reads.clear()
    observed = watchdog.linux_sample(10, observed_known, cpu_observations=cpu)
    assert observed == old == [(10, 2048, "/owned/pcc")]
    assert old_known == observed_known == {10: "100"}
    assert reads == old_reads == [(10, "status"), (10, "stat")]
    assert cpu == [(10, "100", *expected)]
    assert watchdog._own_cpu_ticks([]) == (None, None)


def test_unowned_cpu_is_not_exported(monkeypatch):
    _proc(monkeypatch, {10: {"stat": _stat()}, 20: {"stat": _stat(start="200")}})
    cpu = []
    assert watchdog.linux_sample(10, {}, cpu_observations=cpu) == [(10, 2048, "/owned/pcc")]
    assert cpu == [(10, "100", 13, 7)]


def test_pid_reuse_keeps_birth_pairing_without_aggregation(monkeypatch):
    records = {10: {"stat": _stat()}, 11: {"parent": 10, "stat": _stat(start="110", parent=10)}}
    _proc(monkeypatch, records)
    known, first, second = {}, [], []
    watchdog.linux_sample(10, known, cpu_observations=first)
    records[11]["stat"] = _stat("1", "0", start="999", parent=10)
    watchdog.linux_sample(10, known, cpu_observations=second)
    assert [row for row in first if row[0] == 11] == [(11, "110", 13, 7)]
    assert [row for row in second if row[0] == 11] == [(11, "999", 1, 0)]
    records[11]["parent"] = 1
    records[11]["stat"] = _stat(start="1000")
    third = []
    assert watchdog.linux_sample(10, known, cpu_observations=third) == [(10, 2048, "/owned/pcc")]
    assert third == [(10, "100", 13, 7)]


def test_owned_orphan_keeps_original_accounting(monkeypatch):
    _proc(monkeypatch, {10: {"stat": _stat()}, 11: {"stat": _stat(start="110")}})
    known, cpu = {10: "100", 11: "110"}, []
    rows = watchdog.linux_sample(10, known, cpu_observations=cpu)
    assert sorted(rows) == [(10, 2048, "/owned/pcc"), (11, 2048, "/owned/pcc")]
    assert sorted(cpu) == [(10, "100", 13, 7), (11, "110", 13, 7)]


def test_exited_process_has_no_stale_cpu_row(monkeypatch):
    _proc(monkeypatch, {10: {"stat": _stat()}, 11: {"stat": ProcessLookupError()}})
    cpu = []
    rows = watchdog.linux_sample(10, {10: "100", 11: "110"}, cpu_observations=cpu)
    assert rows == [(10, 2048, "/owned/pcc")]
    assert cpu == [(10, "100", 13, 7)]


def test_missing_clock_unit_is_explicit_and_raw_ticks_survive(monkeypatch):
    def unavailable(name):
        assert name == "SC_CLK_TCK"
        raise OSError("unit unavailable")
    monkeypatch.setattr(watchdog.os, "sysconf", unavailable, raising=False)
    receipt = watchdog._stage1_observation({}, "linux")
    assert receipt["ticks_per_second"] is None
    assert receipt["tick_frequency_status"] == "unavailable"
    assert receipt["cpu_unit"] == "clock_ticks"
    assert watchdog._own_cpu_ticks(_stat().rsplit(")", 1)[1].split()) == (13, 7)


def test_invalid_clock_unit_never_becomes_assumed_100(monkeypatch):
    for value in (0, -1, None, "100", True):
        monkeypatch.setattr(watchdog.os, "sysconf", lambda name: value, raising=False)
        receipt = watchdog._stage1_observation({}, "linux")
        assert receipt["ticks_per_second"] is None
        assert receipt["tick_frequency_status"] == "unavailable"


def _fake_run(tmp_path, monkeypatch, *, outcome="complete", observe=True, native=False,
              malformed=False, tick_frequency=250):
    clock = [0.0]
    events = []
    units = []
    samples = []
    process = SimpleNamespace(pid=101, returncode=None, polls=0)

    def poll():
        process.polls += 1
        if outcome in ("complete", "nonzero") and process.polls >= 3:
            process.returncode = 9 if outcome == "nonzero" else 0
        return process.returncode

    def wait(timeout):
        events.append(("wait", timeout))
        if process.returncode is None:
            process.returncode = -9
        return process.returncode

    process.poll = poll
    process.wait = wait

    def popen(command, **options):
        events.append(("popen", list(command), options["env"], options["start_new_session"]))
        return process

    def sample(root, known, *, cpu_observations=None):
        assert root == 101
        samples.append(cpu_observations is not None)
        known[101] = "700"
        if cpu_observations is not None:
            values = watchdog._own_cpu_ticks(_stat("bad" if malformed else "13").rsplit(")", 1)[1].split())
            cpu_observations.append((101, "700", *values))
        return [(101, 4096 if outcome == "memory" else 1024, "/owned/pcc")]

    def unit(name):
        units.append(name)
        return tick_frequency

    class Cancel:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls >= 3

    monkeypatch.setattr(watchdog, "sys", SimpleNamespace(platform="linux", implementation=SimpleNamespace(name="cpython")))
    monkeypatch.setattr(watchdog, "time", SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda delay: clock.__setitem__(0, clock[0] + delay)))
    monkeypatch.setattr(watchdog, "subprocess", SimpleNamespace(
        Popen=popen, STDOUT=subprocess.STDOUT, CalledProcessError=subprocess.CalledProcessError,
        run=lambda *a, **k: pytest.fail("no extra observer process")))
    monkeypatch.setattr(watchdog, "linux_sample", sample)
    monkeypatch.setattr(watchdog, "os", SimpleNamespace(
        name="posix", path=os.path, sysconf=unit,
        kill=lambda pid, sig: events.append(("kill", pid, sig)),
        killpg=lambda pid, sig: events.append(("killpg", pid, sig))))
    log = tmp_path / "stage1.log"
    error = None
    result = None
    try:
        result = watchdog.run(["/owned/pcc", "input.py"], cwd=tmp_path, env={"ImageVersion": "20261029.1"},
                              log_path=log, timeout=0.1 if outcome == "timeout" else 3,
                              rss_limit=2048, native=native, observe_stage1=observe,
                              cancel=Cancel() if outcome == "cancel" else None)
    except (RuntimeError, TimeoutError, subprocess.CalledProcessError) as exc:
        error = exc
    lines = [json.loads(line) for line in log.with_suffix(".resources.jsonl").read_text().splitlines()]
    return SimpleNamespace(result=result, error=error, lines=lines, events=events, units=units, samples=samples)


def test_clock_unit_is_queried_once_per_enabled_run(tmp_path, monkeypatch):
    run = _fake_run(tmp_path, monkeypatch)
    assert run.error is None and len(run.lines) == 2
    assert run.units == ["SC_CLK_TCK"]
    assert run.result["stage1_observation"]["ticks_per_second"] == 250
    assert run.lines[0]["stage1_observation"] == run.result["stage1_observation"]
    assert "stage1_observation" not in run.lines[1]
    other = _fake_run(tmp_path / "other-run", monkeypatch, tick_frequency=500)
    assert other.error is None and other.units == ["SC_CLK_TCK"]
    assert other.result["stage1_observation"]["ticks_per_second"] == 500


def test_mac_is_unavailable_without_queries(monkeypatch):
    monkeypatch.setattr(watchdog.os, "sysconf", lambda *a: pytest.fail("unsupported platform queried"), raising=False)
    for platform in ("darwin",):
        receipt = watchdog._stage1_observation({}, platform)
        assert receipt["cpu_status"] == "unavailable"
        assert receipt["ticks_per_second"] is None
        assert receipt["cpu_source"] is None
        assert receipt["unavailable_reason"] == "recurring_observer_does_not_read_cpu_time"


def test_default_off_keeps_legacy_schema_and_queries(tmp_path, monkeypatch):
    run = _fake_run(tmp_path, monkeypatch, observe=False)
    assert run.error is None and run.units == []
    assert not any(run.samples)
    assert all(set(row) == {"elapsed", "rss", "processes"} for row in run.lines)
    assert set(run.result) == {"seconds", "peak_tree_rss", "log", "command", "launched_command"}


def test_native_caller_cannot_enable_cpu_observation(tmp_path, monkeypatch):
    run = _fake_run(tmp_path, monkeypatch, observe=True, native=True)
    assert run.error is None and run.units == [] and not any(run.samples)
    assert all("cpu_time" not in row and "stage1_observation" not in row for row in run.lines)


@pytest.mark.parametrize("outcome", ["complete", "nonzero", "timeout", "memory", "cancel"])
def test_observation_preserves_enforcement_and_cleanup(tmp_path, monkeypatch, outcome):
    run = _fake_run(tmp_path, monkeypatch, outcome=outcome)
    if outcome == "complete":
        assert run.error is None
        assert run.result["peak_tree_rss"] == 1024
        assert run.result["command"] == run.result["launched_command"] == ["/owned/pcc", "input.py"]
    else:
        expected = {"nonzero": subprocess.CalledProcessError, "timeout": TimeoutError,
                    "memory": RuntimeError, "cancel": RuntimeError}[outcome]
        assert type(run.error) is expected
        if outcome == "nonzero":
            assert run.error.returncode == 9
        else:
            message = {"timeout": "stage deadline exceeded", "memory": "process-tree RSS cap exceeded",
                       "cancel": "qualification cancelled after a peer failure"}[outcome]
            assert str(run.error) == message
    assert run.events[-1] == ("wait", 10)
    assert any(event[:2] == ("kill", 101) for event in run.events)
    assert run.samples[-1] is False, "cleanup uses the unchanged legacy sampler"
    assert all(row["processes"] == [[101, row["rss"], "/owned/pcc"]] for row in run.lines)
    assert all(row["cpu_time"] == [[101, "700", 13, 7]] for row in run.lines)
    assert run.events[0] == ("popen", ["/owned/pcc", "input.py"], {"ImageVersion": "20261029.1"}, True)


def test_environment_provenance_is_allowlisted_and_missing_is_explicit(monkeypatch):
    monkeypatch.setattr(watchdog.os, "sysconf", lambda name: 250, raising=False)
    receipt = watchdog._stage1_observation({"ImageVersion": "20261029.1", "TOKEN": "secret",
                                          "RUNNER_NAME": "private-name", "PCC_PY_FRONTEND_JOBS": "4"}, "linux")
    image = receipt["runner_environment"]
    assert set(image) == {"RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion",
                          "PCC_PY_FRONTEND_JOBS", "PCC_SELF_BACKEND_JOBS"}
    assert image["ImageVersion"] == {"value": "20261029.1", "status": "supplied_environment"}
    assert image["RUNNER_OS"] == {"value": None, "status": "unavailable"}
    assert "secret" not in json.dumps(receipt) and "private-name" not in json.dumps(receipt)
    assert receipt["effective_cpu_quota"] is None and receipt["affinity_cpu_count"] is None


def test_invalid_environment_values_are_bounded_and_never_echoed(monkeypatch):
    monkeypatch.setattr(watchdog.os, "sysconf", lambda name: 250, raising=False)
    for value in ("x" * 129, "bad\nvalue", "非ASCII", 4, None, ""):
        receipt = watchdog._stage1_observation({"ImageVersion": value}, "linux")
        assert receipt["runner_environment"]["ImageVersion"] == {"value": None, "status": "unavailable"}
        assert len(json.dumps(receipt)) < 2048


def test_malformed_cpu_cannot_mask_memory_enforcement(tmp_path, monkeypatch):
    run = _fake_run(tmp_path, monkeypatch, outcome="memory", malformed=True)
    assert type(run.error) is RuntimeError
    assert str(run.error) == "process-tree RSS cap exceeded"
    assert run.lines[0]["rss"] == 4096
    assert run.lines[0]["cpu_time"] == [[101, "700", None, None]]
    assert run.events[-1] == ("wait", 10)


@pytest.mark.parametrize("failure", [None, "membership-false", "membership-api", "times-api", "times-error"])
def test_windows_same_job_handle_cpu_times_and_unavailable(failure):
    from ctypes import wintypes as w
    import ctypes
    calls=[]
    def membership(handle, job, result):
        calls.append(("member", handle, job))
        ctypes.cast(result, ctypes.POINTER(w.BOOL)).contents.value = int(failure != "membership-false")
        return int(failure != "membership-api")
    def times(handle, *fields):
        calls.append(("times", handle))
        if failure == "times-error":
            raise OSError("optional query")
        for field, value in zip(fields, (2**33+5, 999, 2**34+7, 2**35+9)):
            v=ctypes.cast(field,ctypes.POINTER(w.FILETIME)).contents
            v.dwHighDateTime=value>>32
            v.dwLowDateTime=value&0xffffffff
        return int(failure != "times-api")
    kernel=SimpleNamespace(IsProcessInJob=membership, GetProcessTimes=times)
    actual=watchdog._windows_job_own_cpu_times(kernel, 101, 201)
    assert actual == ((2**33+5,2**35+9,2**34+7) if failure is None else (None,None,None))
    assert calls[0] == ("member",101,201)
    assert calls[1:] == ([] if failure in ("membership-false", "membership-api") else [("times",101)])


def test_windows_stage1_metadata_records_filetime_not_host_quota(monkeypatch):
    monkeypatch.setattr(watchdog.os,"sysconf",lambda *a:pytest.fail("Windows sysconf query"),raising=False)
    receipt=watchdog._stage1_observation({"ImageVersion":"20261126.1"},"win32")
    assert receipt["cpu_status"]=="sampled"
    assert receipt["cpu_source"]=="same_windows_job_process_handle_GetProcessTimes"
    assert receipt["cpu_columns"]==["pid","creation_filetime_100ns","own_user_100ns","own_kernel_100ns"]
    assert receipt["cpu_unit"]=="filetime_100ns" and receipt["ticks_per_second"]==10_000_000
    assert receipt["tick_frequency_source"]=="Windows FILETIME (100 ns)"
    assert receipt["unavailable_reason"] is None and receipt["capacity_status"]=="unavailable_not_observed"
    assert receipt["effective_cpu_quota"] is None and receipt["affinity_cpu_count"] is None


def test_windows_sample_reuses_existing_rss_and_image_handles(monkeypatch):
    import ctypes
    job=object.__new__(watchdog.WindowsJob)
    job.handle=71
    events=[]
    def pids(handle,kind,buffer,size,ret):
        assert handle==71 and kind==3
        ctypes.c_uint32.from_buffer(buffer,4).value=3
        for i,pid in enumerate((10,11,12)):
            ctypes.c_size_t.from_buffer(buffer,8+i*ctypes.sizeof(ctypes.c_size_t)).value=pid
        return 1
    def open_process(mask,inherit,pid):
        assert mask==0x410 and not inherit
        events.append(("open",pid))
        return pid+100 if pid!=12 else 0
    def memory(handle,mem,size):
        p=mem._obj
        events.append(("memory",handle))
        p.working_set=2048
        return int(handle!=111)
    def name(handle,flags,text,size):
        text.value="owned.exe";return 1
    def cpu(kernel,handle,owner):
        events.append(("cpu",handle,owner))
        return (123,50,70)
    job.kernel=SimpleNamespace(QueryInformationJobObject=pids,OpenProcess=open_process,
                               QueryFullProcessImageNameW=name,CloseHandle=lambda h:events.append(("close",h)))
    job.psapi=SimpleNamespace(GetProcessMemoryInfo=memory)
    monkeypatch.setattr(watchdog,"_windows_job_own_cpu_times",cpu,raising=False)
    baseline=job.sample();ordinary=list(events);events.clear();rows=[]
    observed=job.sample(cpu_observations=rows)
    assert observed==baseline==[(10,2048,"owned.exe")]
    assert rows==[(10,123,50,70)]
    assert [x for x in events if x[0]!="cpu"]==ordinary
    assert events.index(("memory",110))<events.index(("cpu",110,71))<events.index(("close",110))
    assert events.count(("close",110))==events.count(("close",111))==1


@pytest.mark.parametrize("observe,native,outcome", [
    (True, False, "complete"), (False, False, "complete"),
    (True, True, "complete"), (True, False, "memory"),
    (True, False, "timeout"), (True, False, "nonzero"),
])
def test_windows_run_cpu_observation_is_optional_and_preserves_guard(tmp_path, monkeypatch, observe, native, outcome):
    events=[];clock=[0.0]
    class Job:
        def __init__(self, limit):
            events.append(("job",limit))
        def attach(self, process, before_resume):
            events.append(("attach",process.pid));before_resume();events.append(("resume",process.pid))
        def sample(self, *, cpu_observations=None):
            events.append(("sample",cpu_observations is not None))
            if cpu_observations is not None:
                cpu_observations.append((101,None,None,None) if outcome=="memory" else (101,700,13,7))
            return [(101,4096 if outcome=="memory" else 1024,"owned.exe")]
        def kill(self):events.append(("kill",))
        def close(self):events.append(("close",))
    process=SimpleNamespace(pid=101,returncode=None,polls=0)
    def poll():
        process.polls+=1
        if outcome in ("complete","nonzero") and process.polls>=3:process.returncode=9 if outcome=="nonzero" else 0
        return process.returncode
    process.poll=poll
    process.wait=lambda timeout:events.append(("wait",timeout))
    def popen(args, **kwargs):
        assert kwargs["creationflags"]==0x204 and not kwargs["start_new_session"]
        events.append(("popen",));return process
    monkeypatch.setattr(watchdog,"WindowsJob",Job)
    monkeypatch.setattr(watchdog,"_publish_windows_root_guard",lambda *args:events.append(("guard",)))
    monkeypatch.setattr(watchdog,"sys",SimpleNamespace(platform="win32",implementation=SimpleNamespace(name="cpython"),executable="host.exe",_base_executable="host.exe"))
    monkeypatch.setattr(watchdog,"os",SimpleNamespace(name="nt",path=os.path))
    monkeypatch.setattr(watchdog,"subprocess",SimpleNamespace(Popen=popen,STDOUT=subprocess.STDOUT,CalledProcessError=subprocess.CalledProcessError))
    monkeypatch.setattr(watchdog,"time",SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda d:clock.__setitem__(0,clock[0]+d)))
    kwargs=dict(cwd=tmp_path,env={},log_path=tmp_path/"windows.log",timeout=0.1 if outcome=="timeout" else 3,rss_limit=2048,native=native,observe_stage1=observe)
    if outcome=="complete":
        receipt=watchdog.run(["owned.exe"],**kwargs)
        assert ("stage1_observation" in receipt)==(observe and not native)
    else:
        exception={"memory":RuntimeError,"timeout":TimeoutError,"nonzero":subprocess.CalledProcessError}[outcome]
        with pytest.raises(exception):watchdog.run(["owned.exe"],**kwargs)
    rows=[json.loads(line) for line in (tmp_path/"windows.resources.jsonl").read_text().splitlines()]
    assert rows and all(("cpu_time" in row)==(observe and not native) for row in rows)
    if observe and not native:
        assert rows[0]["stage1_observation"]["cpu_status"]=="sampled"
        assert all(row["cpu_time"]==([[101,None,None,None]] if outcome=="memory" else [[101,700,13,7]]) for row in rows)
    assert events[:5]==[("job",2048),("popen",),("attach",101),("guard",),("resume",101)]
    assert all(event==("sample",observe and not native) for event in events[5:-3])
    assert events[-3:]==[("kill",),("close",),("wait",10)]


@pytest.mark.pcc_gate(probe=lambda: sys.platform == "win32")
def test_real_windows_own_cpu_observation(tmp_path):
    """Hosted-only smoke: actual Job handle and real Win32 CPU counters."""
    log = tmp_path / "win-cpu.log"
    child = "import time; end=time.monotonic()+0.9\nwhile time.monotonic()<end: pass\n"
    receipt = watchdog.run([sys.executable, "-c", child], cwd=tmp_path,
                           env=dict(os.environ), log_path=log, timeout=10,
                           rss_limit=256 * 1024**2, observe_stage1=True)
    samples = [json.loads(line) for line in log.with_suffix(".resources.jsonl").read_text().splitlines()]
    assert receipt["stage1_observation"]["cpu_unit"] == "filetime_100ns"
    assert samples[0]["stage1_observation"] == receipt["stage1_observation"]
    measured = {}
    for sample in samples:
        assert {row[0] for row in sample["cpu_time"]} == {row[0] for row in sample["processes"]}
        for pid, birth, user, kernel in sample["cpu_time"]:
            if birth is not None:
                assert birth > 0 and user >= 0 and kernel >= 0
                measured.setdefault((pid, birth), []).append(user + kernel)
            else:
                assert user is None and kernel is None
    assert any(len(values) >= 2 and values[-1] > values[0] for values in measured.values()), measured
