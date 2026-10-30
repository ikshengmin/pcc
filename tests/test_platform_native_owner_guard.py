"""Sampled-name controls and explicit Linux/Windows host-supervisor checks.

The integration checks execute child processes; they do not qualify pcc1 or
prove that a native compiler has no short-lived/renamed external tool route.
"""

import json
import os
import platform
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import platform_process_watchdog as watchdog

LEGACY_OWNERS = (
    "python",
    "python3",
    "python3.15",
    "python.exe",
    "pythonw.exe",
    "cc",
    "gcc",
    "clang",
    "clang.exe",
    "cl.exe",
    "ld",
    "ld.lld",
    "link.exe",
    "ar",
    "ranlib",
    "make",
    "make.exe",
    "codesign",
    "sh",
    "bash",
    "zsh",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "ls",
    "rm",
    "mkdir",
    "find",
    "tar",
    "unzip",
    "nm",
    "objdump",
    "otool",
)


@pytest.mark.parametrize("name", LEGACY_OWNERS)
def test_original_forbidden_native_owner_still_rejects(name):
    assert watchdog.is_forbidden_native_owner("/usr/bin/" + name)


@pytest.mark.parametrize(
    "name",
    (
        "g++",
        "clang++",
        "cc1plus",
        "c++",
        "cpp",
        "cc1",
        "collect2",
        "as",
        "g++-14",
        "clang++-20.1",
        "cc1plus-14.2.0",
        "gcc-14",
        "clang-20",
        "x86_64-linux-gnu-g++-14",
        "aarch64-linux-gnu-clang++-20",
        "arm-none-eabi-g++",
        "x86_64-w64-mingw32-g++.exe",
        "riscv64-unknown-linux-gnu-cc1plus",
        "aarch64-linux-android24-clang++",
        "x86_64-linux-gnu-gcc-14",
        "i686-w64-mingw32-ld.bfd",
        "ld.gold",
        "ld.lld-20",
        "lld-link.exe",
        "llvm-ar-20",
        "llvm-ranlib-20",
        "llvm-objdump",
        "llvm-as",
        "llvm-link",
        "llvm-mc",
        "llvm-objcopy",
        "llc-20",
        "opt-20",
        "clang-cl.exe",
        "clang-cpp-20",
        "python2.7",
        "python3.13",
        "python3.15.0",
        "python3.15t",
        "python3.15td",
        "python2.7mu",
        "python3_d.exe",
        "pythonw3.13.exe",
        "pythonw_d.exe",
        "bash.exe",
        r"C:\Program Files\LLVM\bin\CLANG++.EXE",
        r"C:\Python315\PYTHON3.15T.EXE",
        "/usr/bin/g++-14 (deleted)",
        "/usr/bin/python3.15 (deleted)",
    ),
)
def test_versioned_target_prefixed_cpp_and_python_owners_reject(name):
    assert watchdog.is_forbidden_native_owner(name)


@pytest.mark.parametrize(
    "name",
    (
        "pcc",
        "pcc1",
        "pcc2",
        "pcc3",
        "pcc1.exe",
        "pcc1 (deleted)",
        "owned-guard-probe",
        "cpp-probe",
        "python_semantics_probe",
        "python3_semantics_probe",
        "clang++-result-probe",
        "libpython3.15.so",
        "/cache/g++/pcc1",
        r"C:\Python315\pcc1.exe",
        "",
        "/unknown/renamed-tool",
    ),
)
def test_non_tool_names_are_not_mistaken_for_forbidden_owners(name):
    # Non-matches are not attestations: the last name could hide any tool.
    assert not watchdog.is_forbidden_native_owner(name)


def _model_run(tmp_path, monkeypatch, *, image, windows=False, native=True):
    events = []
    clock = [0.0]
    process = SimpleNamespace(pid=101, returncode=None, polls=0)

    def poll():
        process.polls += 1
        if process.returncode is None and process.polls >= 3:
            process.returncode = 0
        return process.returncode

    def wait(timeout):
        events.append(("wait", timeout))
        return process.returncode

    def kill(pid, signal):
        events.append(("kill", pid))
        process.returncode = -9

    process.poll = poll
    process.wait = wait

    def sample(root, known):
        assert root == process.pid
        known[root] = "start-identity"
        return [(root, 4096, image)]

    class Job:
        def __init__(self, limit):
            events.append(("job", limit))

        def attach(self, child, before_resume):
            assert child is process
            before_resume()
            events.append(("resume",))

        def sample(self):
            return [(process.pid, 4096, image)]

        def kill(self):
            events.append(("job-kill",))
            process.returncode = -9

        def close(self):
            events.append(("job-close",))

    monkeypatch.setattr(
        watchdog,
        "os",
        SimpleNamespace(
            name="nt" if windows else "posix",
            path=os.path,
            kill=kill,
            killpg=kill,
        ),
    )
    monkeypatch.setattr(
        watchdog,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0],
            sleep=lambda delay: clock.__setitem__(0, clock[0] + delay),
        ),
    )
    monkeypatch.setattr(
        watchdog,
        "subprocess",
        SimpleNamespace(
            Popen=lambda *args, **kwargs: process,
            STDOUT=subprocess.STDOUT,
            CalledProcessError=subprocess.CalledProcessError,
        ),
    )
    monkeypatch.setattr(watchdog, "linux_sample", sample)
    monkeypatch.setattr(watchdog, "WindowsJob", Job)
    monkeypatch.setattr(watchdog, "_publish_windows_root_guard", lambda *args: None)
    log = tmp_path / "model.log"
    error = None
    result = None
    try:
        result = watchdog.run(
            ["pcc1", "input.cpp"],
            cwd=tmp_path,
            env={},
            log_path=log,
            timeout=10,
            rss_limit=8192,
            native=native,
        )
    except RuntimeError as exc:
        error = exc
    rows = [
        json.loads(line)
        for line in log.with_suffix(".resources.jsonl").read_text().splitlines()
    ]
    return SimpleNamespace(error=error, result=result, events=events, rows=rows)


@pytest.mark.parametrize(
    "windows,image",
    (
        (False, "/usr/bin/x86_64-linux-gnu-g++-14"),
        (False, "/usr/lib/gcc/x86_64-linux-gnu/14/cc1plus"),
        (False, "/usr/bin/python3.15t"),
        (True, r"C:\LLVM\bin\clang++-20.exe"),
        (True, r"C:\Python313\python3.13.exe"),
    ),
)
def test_native_run_rejects_sampled_external_owner_and_reaps_model(
    tmp_path,
    monkeypatch,
    windows,
    image,
):
    run = _model_run(tmp_path, monkeypatch, windows=windows, image=image)
    assert isinstance(run.error, RuntimeError)
    assert "native stage invoked external execution owners" in str(run.error)
    assert repr(image) in str(run.error)
    assert run.result is None
    assert run.rows[0]["processes"] == [[101, 4096, image]]
    assert run.events[-1] == ("wait", 10)
    if windows:
        assert run.events[-3:] == [("job-kill",), ("job-close",), ("wait", 10)]
    else:
        assert ("kill", 101) in run.events


@pytest.mark.parametrize(
    "windows,image,native",
    (
        (False, "/owned/pcc1", True),
        (True, r"C:\owned\pcc2.exe", True),
        (False, "/usr/bin/x86_64-linux-gnu-g++-14", False),
        (True, r"C:\Python313\python3.13.exe", False),
    ),
)
def test_owned_stage_names_and_explicit_host_mode_keep_the_return_contract(
    tmp_path,
    monkeypatch,
    windows,
    image,
    native,
):
    run = _model_run(tmp_path, monkeypatch, windows=windows, image=image, native=native)
    assert run.error is None
    assert set(run.result) == {
        "seconds",
        "peak_tree_rss",
        "log",
        "command",
        "launched_command",
    }
    assert run.result["peak_tree_rss"] == 4096
    assert run.events[-1] == ("wait", 10)


@pytest.mark.integration
def test_linux_windows_native_guard_rejects_and_reaps_real_host_python(
    tmp_path, monkeypatch
):
    assert (
        sys.platform.startswith("linux") or sys.platform == "win32"
    ), "this process supervisor integration covers Linux/Windows only"
    launched = []
    original_popen = subprocess.Popen

    def launch(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        launched.append(child)
        return child

    monkeypatch.setattr(
        watchdog,
        "subprocess",
        SimpleNamespace(
            Popen=launch,
            STDOUT=subprocess.STDOUT,
            CalledProcessError=subprocess.CalledProcessError,
        ),
    )
    log = tmp_path / "denied-python.log"
    with pytest.raises(
        RuntimeError, match="native stage invoked external execution owners"
    ):
        watchdog.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            env=os.environ.copy(),
            log_path=log,
            timeout=10,
            rss_limit=128 * 1024 * 1024,
            native=True,
        )
    assert len(launched) == 1 and launched[0].poll() is not None
    samples = [
        json.loads(line)
        for line in log.with_suffix(".resources.jsonl").read_text().splitlines()
    ]
    assert any(
        watchdog.is_forbidden_native_owner(row[2])
        for sample in samples
        for row in sample["processes"]
    )


@pytest.mark.integration
def test_linux_x86_64_native_guard_runs_actual_owned_elf(tmp_path, monkeypatch):
    assert sys.platform == "linux" and platform.machine() in (
        "x86_64",
        "AMD64",
    ), "this owned-artifact process control executes Linux x86_64 only"
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file

    # Hold the artifact across sampler ticks without an external sleep tool.
    # This is an assembler/linker control, not a pcc1 compiler executable.
    with monkeypatch.context() as guard:
        guard.setattr(
            subprocess, "Popen", lambda *a, **k: pytest.fail("external build tool")
        )
        obj = assemble_file(
            ".intel_syntax noprefix\n.text\n.globl _start\n_start:\n"
            "  sub rsp, 16\n"
            "  mov qword ptr [rsp], 0\n"
            "  mov qword ptr [rsp+8], 400000000\n"
            "  mov eax, 35\n"
            "  mov rdi, rsp\n"
            "  xor esi, esi\n"
            "  syscall\n"
            "  mov dword ptr [rsp], 0x0a4b4f\n"
            "  mov eax, 1\n"
            "  mov edi, 1\n"
            "  mov rsi, rsp\n"
            "  mov edx, 3\n"
            "  syscall\n"
            "  mov eax, 60\n"
            "  xor edi, edi\n"
            "  syscall\n"
        )
        executable = link_static_executable([obj], entry="_start")
    binary = tmp_path / "owned-guard-probe"
    binary.write_bytes(executable)
    binary.chmod(0o700)
    log = tmp_path / "owned.log"
    result = watchdog.run(
        [str(binary)],
        cwd=tmp_path,
        env=os.environ.copy(),
        log_path=log,
        timeout=10,
        rss_limit=128 * 1024 * 1024,
        native=True,
    )
    assert log.read_bytes() == b"OK\n"
    assert result["command"] == [str(binary)]
    samples = [
        json.loads(line)
        for line in log.with_suffix(".resources.jsonl").read_text().splitlines()
    ]
    assert any(
        row[2] == str(binary) for sample in samples for row in sample["processes"]
    )
