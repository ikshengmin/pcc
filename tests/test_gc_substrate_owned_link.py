"""Host-only ownership regressions; no compiler, linker or runtime is built."""
from pathlib import Path
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from tests import owned_runtime_c_fixture as owned
from tests import runtime_fixture_provenance as provenance
from tests.python import _gc_substrate_common as substrate


def _forbidden(*args, **kwargs):
    pytest.fail("mock-only fixture control attempted a build or external process")


@pytest.mark.parametrize("threaded", (False, True))
def test_substrate_routes_exact_selected_archive_to_owned_harness(
    tmp_path, monkeypatch, threaded,
):
    default = tmp_path / "default/libpy_runtime_pcc_py.a"
    threaded_archive = tmp_path / "threaded/libpy_runtime_pcc_py.a"
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(default))
    monkeypatch.setenv("PCC_THREADED_RUNTIME_ARCHIVE", str(threaded_archive))
    monkeypatch.setattr(substrate, "cached_pcc_python_runtime", _forbidden)
    monkeypatch.setattr(substrate, "cached_threaded_pcc_python_runtime", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    admitted = []
    def verify(explicit, **kwargs):
        admitted.append((explicit, kwargs))
        return Path(explicit), {}
    monkeypatch.setattr(provenance, "_verified_test_runtime_archive", verify)
    linked = []
    def link(source, output, **kwargs):
        linked.append((source, output, kwargs))
        output.write_bytes(b"mock executable; not native evidence")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(owned, "link_c_harness_bounded", link)
    extra = tmp_path / "extra.c"
    environment = dict(os.environ)
    result = substrate._compile_runtime_probe(
        tmp_path, kind="pcc_python", threaded=threaded, stem="control",
        source_text="\n    int main(void) { return 0; }\n",
        extra_include_dirs=(tmp_path / "include",), extra_sources=(extra,),
    )
    selected = threaded_archive if threaded else default
    assert admitted == [(str(selected), {
        "threads": True if threaded else None, "runtime_root": substrate.RUNTIME_DIR,
    })]
    source, output, kwargs = linked[0]
    assert source.read_text() == "int main(void) { return 0; }\n"
    assert output == result == tmp_path / "control_pcc_python"
    assert kwargs == {
        "runtime_archive": selected,
        "extra_include_dirs": (
            str(tmp_path / "include"), str(substrate.RUNTIME_DIR / "include"),
            str(substrate.RUNTIME_DIR / "src"),
        ),
        "extra_sources": (extra,),
        "timeout": 60,
    }
    assert dict(os.environ) == environment


@pytest.mark.parametrize("failure", (None, "compile", "link"))
def test_owned_harness_pins_admitted_archive_and_restores_environment(
    tmp_path, monkeypatch, failure,
):
    archive = tmp_path / "selected/libpy_runtime_pcc_py.a"
    other = str(tmp_path / "different/libpy_runtime_pcc_py.a")
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", other)
    verified = []
    def verify(explicit):
        verified.append(explicit)
        return archive, {"members": [{
            "object_emitter": "pcc-self-backend-object-writer", "uses_host_cc": False,
        }]}
    monkeypatch.setattr(owned, "_verified_test_runtime_archive", verify)
    harness, extra, output = (tmp_path / name for name in ("main.c", "extra.c", "out"))
    harness.write_text("int main(void) { return helper(); }\n")
    extra.write_text("int helper(void) { return 0; }\n")
    calls = []
    class Evaluator:
        def __init__(self, **kwargs):
            assert kwargs == {"backend": "self", "target_triple": owned.host_target_triple()}
        def compile_translation_units(self, units, **kwargs):
            assert os.environ["PCC_RUNTIME_ARCHIVE"] == str(archive)
            with pytest.raises(AssertionError, match="external build process"):
                subprocess.Popen(["must-not-run"])
            assert len(units) == 2
            assert [(unit.name, unit.path, unit.source) for unit in units] == [
                (extra.name, str(extra), extra.read_text()),
                (harness.name, str(harness), harness.read_text()),
            ]
            assert kwargs == {
                "use_system_cpp": False, "use_compile_cache": False,
                "include_dirs": [str(owned.ROOT / "pcc/runtime/include"),
                                 str(tmp_path), str(owned.ROOT / "utils/fake_libc_include")],
                "cpp_args": ["-DVALUE=1"],
            }
            calls.append("compile")
            if failure == "compile":
                raise ValueError("compile failed")
            return units
        def emit_executable(self, units, path, **kwargs):
            assert os.environ["PCC_RUNTIME_ARCHIVE"] == str(archive)
            assert kwargs == {"link_args": [str(archive)]}
            with pytest.raises(AssertionError, match="external build process"):
                subprocess.Popen(["must-not-run"])
            calls.append("link")
            if failure == "link":
                raise ValueError("link failed")
            Path(path).write_bytes(b"mock executable; not native evidence")
    monkeypatch.setattr(owned, "CEvaluator", Evaluator)
    environment = dict(os.environ)
    def invoke():
        return owned.link_c_harness(
            harness, output, runtime_archive=archive, extra_sources=(extra,),
            defines=("VALUE=1",), extra_include_dirs=(str(tmp_path),),
        )
    if failure:
        with pytest.raises(ValueError, match=failure + " failed"):
            invoke()
    else:
        assert invoke().returncode == 0
    assert verified == [archive]
    assert calls == (["compile"] if failure == "compile" else ["compile", "link"])
    assert dict(os.environ) == environment


def test_owned_harness_rejects_stale_archive_before_compilation(tmp_path, monkeypatch):
    def stale(explicit):
        raise ValueError("explicit test runtime is stale for current codegen")
    monkeypatch.setattr(owned, "_verified_test_runtime_archive", stale)
    monkeypatch.setattr(owned, "CEvaluator", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    with pytest.raises(ValueError, match="stale for current codegen"):
        owned.link_c_harness(tmp_path / "main.c", tmp_path / "out",
                             runtime_archive=tmp_path / "libpy_runtime_pcc_py.a")


@pytest.mark.parametrize("member", (
    {"object_emitter": "pcc-self-backend-object-writer", "uses_host_cc": True},
    {"object_emitter": "host-cc", "uses_host_cc": False},
))
def test_owned_archive_admission_rejects_hosted_members(tmp_path, monkeypatch, member):
    archive = tmp_path / "libpy_runtime_pcc_py.a"
    monkeypatch.setattr(owned, "_verified_test_runtime_archive",
                        lambda explicit: (archive, {"members": [member]}))
    with pytest.raises(AssertionError):
        owned.admitted_runtime(archive)


def test_default_admission_preserves_environment_selection(tmp_path, monkeypatch):
    archive = tmp_path / "libpy_runtime_pcc_py.a"
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))
    seen = []
    def verify(explicit):
        seen.append(explicit)
        return archive, {"members": [{
            "object_emitter": "pcc-self-backend-object-writer", "uses_host_cc": False,
        }]}
    monkeypatch.setattr(owned, "_verified_test_runtime_archive", verify)
    assert owned.admitted_runtime() == archive
    assert seen == [str(archive)]
    with pytest.raises(AssertionError, match="explicitly admitted"):
        owned.admitted_runtime("")


def test_substrate_rejects_unowned_compiler_options_without_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(substrate, "_runtime_variant", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    with pytest.raises(ValueError, match="does not support host compiler arguments"):
        substrate._compile_runtime_probe(
            tmp_path, kind="pcc_python", threaded=False, stem="unsupported",
            source_text="int main(void) { return 0; }",
            extra_compile_args=("-fsanitize=thread",),
        )


@pytest.mark.parametrize("returncode", (0, 124))
def test_bounded_harness_preserves_sixty_second_process_group_deadline(
    tmp_path, monkeypatch, returncode,
):
    from tests.python import process_timeout

    harness, output, archive, extra = (
        tmp_path / name for name in ("main.c", "out", "libpy_runtime_pcc_py.a", "extra.c")
    )
    expected = SimpleNamespace(returncode=returncode, stdout="", stderr="timeout" if returncode else "")
    calls = []
    def supervise(command, **kwargs):
        calls.append((command, kwargs))
        return expected
    monkeypatch.setattr(process_timeout, "run_process_group_timeout", supervise)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    result = owned.link_c_harness_bounded(
        harness, output, runtime_archive=archive,
        extra_include_dirs=(tmp_path,), extra_sources=(extra,), timeout=60,
    )
    assert result is expected
    command, kwargs = calls[0]
    assert command[:3] == [owned.sys.executable, "-m", "tests.owned_runtime_c_fixture"]
    assert kwargs == {
        "timeout": 60, "cwd": owned.ROOT,
        "stderr_path": Path(str(output) + ".compile.stderr"),
    }
    assert json.loads(command[3]) == {
        "harness": str(harness), "output": str(output), "runtime_archive": str(archive),
        "extra_include_dirs": [str(tmp_path)], "extra_sources": [str(extra)],
    }


def test_child_request_preserves_owned_admission_and_arguments(tmp_path, monkeypatch):
    payload = {
        "harness": str(tmp_path / "main.c"), "output": str(tmp_path / "out"),
        "runtime_archive": str(tmp_path / "selected/libpy_runtime_pcc_py.a"),
        "extra_include_dirs": [str(tmp_path / "include")],
        "extra_sources": [str(tmp_path / "extra.c")],
    }
    calls = []
    def link(**kwargs):
        calls.append(kwargs)
        raise ValueError("strict child admission rejected archive")
    monkeypatch.setattr(owned, "link_c_harness", link)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    with pytest.raises(ValueError, match="strict child admission"):
        owned._link_c_harness_request(json.dumps(payload))
    assert calls == [{
        **payload, "harness": Path(payload["harness"]), "output": Path(payload["output"]),
        "extra_sources": (Path(payload["extra_sources"][0]),),
    }]


@pytest.mark.parametrize("needs_kill", (False, True))
def test_bounded_harness_timeout_cleans_owned_process_group(
    tmp_path, monkeypatch, needs_kill,
):
    from tests.python import process_timeout

    events = []
    class Process:
        pid = 123456789  # Never signaled: _kill_process_group is mocked below.
        returncode = 0
        def communicate(self, timeout=None):
            events.append(("communicate", timeout))
            if timeout == 60 or (timeout == 2.0 and needs_kill):
                raise subprocess.TimeoutExpired(["mock-owned-driver"], timeout)
            return "partial stdout", "partial stderr"
    process = Process()
    def start(command, **kwargs):
        assert command[:3] == [owned.sys.executable, "-m", "tests.owned_runtime_c_fixture"]
        assert kwargs["start_new_session"] is True
        events.append(("start", kwargs["cwd"]))
        return process
    def kill_group(child, sig):
        assert child is process
        events.append(("kill_group", sig))
    monkeypatch.setattr(subprocess, "Popen", start)
    # Exercise the POSIX cleanup contract as a pure model on every test host;
    # never change the real os.name or install/send actual process signals.
    monkeypatch.setattr(process_timeout, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(process_timeout, "signal", SimpleNamespace(SIGTERM=15, SIGKILL=9))
    monkeypatch.setattr(process_timeout, "_kill_process_group", kill_group)
    monkeypatch.setattr(process_timeout, "threading_is_main_thread", lambda: False)
    result = owned.link_c_harness_bounded(
        tmp_path / "main.c", tmp_path / "out",
        runtime_archive=tmp_path / "libpy_runtime_pcc_py.a", timeout=60,
    )
    expected = [
        ("start", str(owned.ROOT)), ("communicate", 60),
        ("kill_group", process_timeout.signal.SIGTERM), ("communicate", 2.0),
    ]
    if needs_kill:
        expected += [("kill_group", process_timeout.signal.SIGKILL), ("communicate", None)]
    assert events == expected
    assert result.returncode == 124 and result.stdout == "partial stdout"
    assert "[TIMEOUT] killed process group" in result.stderr
    assert "after 60.0s" in result.stderr


@pytest.mark.parametrize("returncode", (1, 124))
def test_substrate_rejects_failed_or_timed_out_build_with_both_streams(
    tmp_path, monkeypatch, returncode,
):
    monkeypatch.setattr(substrate, "_runtime_variant", lambda kind, threaded: (
        substrate.RUNTIME_DIR, tmp_path / "libpy_runtime_pcc_py.a",
    ))
    monkeypatch.setattr(owned, "link_c_harness_bounded", lambda *args, **kwargs: (
        SimpleNamespace(returncode=returncode, stdout="compiler output; ", stderr="build failed")
    ))
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    with pytest.raises(AssertionError, match="compiler output; build failed"):
        substrate._compile_runtime_probe(
            tmp_path, kind="pcc_python", threaded=False, stem="failure",
            source_text="int main(void) { return 0; }",
        )
