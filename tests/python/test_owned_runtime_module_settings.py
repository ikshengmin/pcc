"""Per-module compiler settings at the owned runtime construction boundary."""

import os

import pytest

from pcc.frontends.python.owned_runtime_build import (
    _RUNTIME_IR_OUTPUT_ENV,
    _compile_runtime_module,
)
from pcc.frontends.python.thread_runtime_config import implicit_thread_safepoints_enabled


# These kernel/root-publication modules must not poll implicitly before their
# state is ready. The runtime stays threaded; explicit safepoint calls remain.
# Keep this inventory independent of the implementation's private tuple.
NO_POLL_MODULES = (
    "freestanding_thread_kernel_pthread", "py_runtime_log", "py_tempfile",
    "py_os_mkdir", "py_cleanup_runtime", "py_exc_traceback", "py_re_bridge_entry",
)
ORDINARY_MODULES = ("py_threading", "freestanding_platform_rss")
POLL_ENV = "PCC_IMPLICIT_THREAD_SAFEPOINTS"


def _configure(monkeypatch, key, value):
    if value is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("module", NO_POLL_MODULES)
@pytest.mark.parametrize("previous", [None, "0", "1"])
@pytest.mark.parametrize("threads", [None, "1"])
@pytest.mark.parametrize("fail", [False, True])
def test_raw_runtime_disables_implicit_polls_and_restores_environment(
    monkeypatch, module, previous, threads, fail,
):
    from pcc.frontends.python import pipeline

    _configure(monkeypatch, "PCC_WITH_THREADS", threads)
    _configure(monkeypatch, POLL_ENV, previous)
    calls = []

    def compile_source(source, output, **options):
        assert os.environ.get("PCC_WITH_THREADS") == threads
        assert os.environ[POLL_ENV] == "0"
        assert implicit_thread_safepoints_enabled() is False
        calls.append((source, output, options))
        # Restore the caller's setting even if nested compilation mutates it.
        os.environ[POLL_ENV] = "nested compile mutation"
        if fail:
            raise RuntimeError("source diagnostic")

    monkeypatch.setattr(pipeline, "compile_python", compile_source)
    if fail:
        with pytest.raises(RuntimeError, match="source diagnostic"):
            _compile_runtime_module(module, "kernel.py", "kernel.ll",
                                    "x86_64-pc-windows-msvc")
    else:
        _compile_runtime_module(module, "kernel.py", "kernel.ll",
                                "x86_64-pc-windows-msvc")
    assert len(calls) == 1
    assert calls[0][2]["target_triple"] == "x86_64-pc-windows-msvc"
    assert calls[0][2]["libpython_mode"] == "off"
    assert os.environ.get("PCC_WITH_THREADS") == threads
    assert os.environ.get(POLL_ENV) == previous


@pytest.mark.parametrize("module", ORDINARY_MODULES)
@pytest.mark.parametrize("previous", [None, "0", "1"])
@pytest.mark.parametrize("fail", [False, True])
def test_ordinary_runtime_modules_preserve_caller_poll_policy(
    monkeypatch, module, previous, fail,
):
    from pcc.frontends.python import pipeline

    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    _configure(monkeypatch, POLL_ENV, previous)
    calls = []

    def compile_source(*_args, **_kwargs):
        assert os.environ["PCC_WITH_THREADS"] == "1"
        assert os.environ.get(POLL_ENV) == previous
        calls.append(implicit_thread_safepoints_enabled())
        if fail:
            raise RuntimeError("ordinary compile failed")

    monkeypatch.setattr(pipeline, "compile_python", compile_source)
    if fail:
        with pytest.raises(RuntimeError, match="ordinary compile failed"):
            _compile_runtime_module(module, "ordinary.py", "ordinary.ll",
                                    "aarch64-unknown-linux-gnu")
    else:
        _compile_runtime_module(module, "ordinary.py", "ordinary.ll",
                                "aarch64-unknown-linux-gnu")
    assert calls == [previous != "0"]
    assert os.environ["PCC_WITH_THREADS"] == "1"
    assert os.environ.get(POLL_ENV) == previous


@pytest.mark.parametrize("module", (NO_POLL_MODULES[0], ORDINARY_MODULES[0]))
@pytest.mark.parametrize("threads", ("0", "false", "no", "off"))
def test_poll_suppression_never_hides_removed_single_thread_runtime(
    monkeypatch, module, threads,
):
    from pcc.frontends.python import pipeline

    monkeypatch.setenv("PCC_WITH_THREADS", threads)
    monkeypatch.setenv(POLL_ENV, "1")
    calls = []

    def compile_source(*_args, **_kwargs):
        calls.append(os.environ["PCC_WITH_THREADS"])
        # This is the real policy consulted by L1CodeGen during construction.
        implicit_thread_safepoints_enabled()
        pytest.fail("removed single-thread runtime was accepted")

    monkeypatch.setattr(pipeline, "compile_python", compile_source)
    with pytest.raises(ValueError, match="single-thread runtime was removed"):
        _compile_runtime_module(module, "kernel.py", "kernel.ll",
                                "x86_64-unknown-linux-gnu")
    assert calls == [threads]
    assert os.environ["PCC_WITH_THREADS"] == threads
    assert os.environ[POLL_ENV] == "1"


@pytest.mark.parametrize("module", ORDINARY_MODULES + NO_POLL_MODULES)
@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_runtime_ir_masks_direct_and_deferred_modes_and_restores(monkeypatch, module, configured, fail):
    from pcc.frontends.python import pipeline

    for key in _RUNTIME_IR_OUTPUT_ENV:
        if configured:
            monkeypatch.setenv(key, "pending.json" if "PLAN" in key or "OUTPUT" in key else "1")
        else:
            monkeypatch.delenv(key, raising=False)
    before = {key: os.environ.get(key) for key in _RUNTIME_IR_OUTPUT_ENV}
    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    monkeypatch.setenv("PCC_GC_BACKEND", "4")
    monkeypatch.setenv("PCC_REFCOUNT_KIND", "atomic")
    monkeypatch.setenv(POLL_ENV, "1")
    seen = []

    def compile_source(source, output, **options):
        assert options["emit_llvm_only"] is True
        assert options["python_library"] is True
        assert options["backend"] == "self"
        assert options["target_triple"] == "aarch64-unknown-linux-gnu"
        assert all(key not in os.environ for key in _RUNTIME_IR_OUTPUT_ENV)
        assert os.environ["PCC_WITH_THREADS"] == "1"
        assert os.environ[POLL_ENV] == ("0" if module in NO_POLL_MODULES else "1")
        assert implicit_thread_safepoints_enabled() is (module not in NO_POLL_MODULES)
        assert os.environ["PCC_GC_BACKEND"] == "4"
        assert os.environ["PCC_REFCOUNT_KIND"] == "atomic"
        seen.append((source, output))
        # A failed nested compile must not leak changes to the masked options.
        os.environ["PCC_DIRECT_INDEXED_KERNEL_EMIT"] = "changed during compile"
        if fail:
            raise RuntimeError("runtime compile failed")

    monkeypatch.setattr(pipeline, "compile_python", compile_source)
    if fail:
        with pytest.raises(RuntimeError, match="runtime compile failed"):
            _compile_runtime_module(module, "module.py", "module.ll", "aarch64-unknown-linux-gnu")
    else:
        _compile_runtime_module(module, "module.py", "module.ll", "aarch64-unknown-linux-gnu")
    assert seen == [("module.py", "module.ll")]
    assert {key: os.environ.get(key) for key in _RUNTIME_IR_OUTPUT_ENV} == before
    assert os.environ["PCC_WITH_THREADS"] == "1"
    assert os.environ[POLL_ENV] == "1"


def test_bootstrap_overrides_inherited_deferred_plans(monkeypatch):
    from scripts.bootstrap_platform import _stage_environment, ROOT

    inherited = {
        "PCC_DEFER_FRONTEND_CODEGEN_PLAN": "other-task-front.json",
        "PCC_DEFER_FRONTEND_OUTPUT": "other-task-output",
        "PCC_DEFER_SELF_LINK_PLAN": "other-task-link.json",
    }
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    monkeypatch.setenv("PCC_RUNTIME_DIR", "other-runtime-tree")
    environment = _stage_environment(4)
    assert all(environment[key] == "" for key in inherited)
    assert environment["PCC_DIRECT_INDEXED_KERNEL_EMIT"] == "1"
    assert environment["PCC_WITH_THREADS"] == "1"
    assert environment["PCC_GC_BACKEND"] == "4"
    assert environment["PCC_RUNTIME_DIR"] == str(ROOT / "pcc" / "runtime")
    assert os.environ["PCC_RUNTIME_DIR"] == "other-runtime-tree"
    assert {key: os.environ[key] for key in inherited} == inherited
