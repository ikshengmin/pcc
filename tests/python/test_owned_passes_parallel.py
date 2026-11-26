"""The owned IR passes of a host build run module by module in a process pool.

Each module's passes read only that module's text, so the pool's result must
be the serial loop's byte for byte, in the input order, with skipped modules
untouched. pcc1 has no host interpreter and keeps the serial loop.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline_pass_driver as driver
from pcc.frontends.python.compiled_owned_passes import run_owned_passes


ROOT = Path(__file__).resolve().parents[2]


def _module(index: int, functions: int) -> str:
    # Allocas for mem2reg, a dead add for DCE, a constant branch for
    # simplifycfg: every owned pass has work in every function.
    parts = []
    for function in range(functions):
        parts.append(
            f"define i64 @m{index}_f{function}(i64 %x) {{\n"
            "entry:\n"
            "  %slot = alloca i64\n"
            "  store i64 %x, ptr %slot\n"
            "  %v = load i64, ptr %slot\n"
            "  %dead = add i64 %v, 1\n"
            "  br i1 true, label %then, label %else\n"
            "then:\n"
            f"  %r = add i64 %v, {function}\n"
            "  ret i64 %r\n"
            "else:\n"
            "  ret i64 0\n"
            "}\n"
        )
    return "".join(parts)


def _modules() -> list[tuple[str, str]]:
    return [(f"mod{index}", _module(index, 3 + index * 4)) for index in range(5)]


def _passes() -> list[str]:
    return driver.resolve_python_ir_pass_names(default_raw="default")


def _serial(modules, names):
    return [(name, run_owned_passes(text, names)) for name, text in modules]


def test_pool_matches_the_serial_loop_byte_for_byte(monkeypatch):
    monkeypatch.setattr(driver, "OWNED_PARALLEL_MIN_BYTES", 1)
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_JOBS", "3")
    modules = _modules()
    names = _passes()
    assert names
    serial = _serial(modules, names)
    assert any(out != text for (_, out), (_, text) in zip(serial, modules))
    parallel = driver.run_owned_passes_parallel(
        modules, names, lambda: sys.executable, lambda: str(ROOT),
    )
    assert parallel == serial


def test_apply_passes_many_uses_the_pool_and_keeps_order(monkeypatch):
    monkeypatch.setattr(driver, "OWNED_PARALLEL_MIN_BYTES", 1)
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_JOBS", "4")
    calls = []
    original = driver.run_owned_passes_parallel

    def recording(*args):
        result = original(*args)
        calls.append(result is not None)
        return result

    monkeypatch.setattr(driver, "run_owned_passes_parallel", recording)
    modules = _modules()
    out = driver.apply_passes_many(
        modules,
        apply_one=None,
        host_python_command=lambda: sys.executable,
        pcc_source_root=lambda: str(ROOT),
        default_raw="default",
    )
    assert calls == [True]
    assert [name for name, _ in out] == [name for name, _ in modules]
    assert out == _serial(modules, _passes())


def test_skipped_modules_stay_unchanged(monkeypatch):
    monkeypatch.setattr(driver, "OWNED_PARALLEL_MIN_BYTES", 1)
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_JOBS", "2")
    modules = _modules()
    skipped = modules[1][0]
    monkeypatch.setattr(driver, "python_ir_pass_should_skip_module",
                        lambda name: name == skipped)
    parallel = driver.run_owned_passes_parallel(
        modules, _passes(), lambda: sys.executable, lambda: str(ROOT),
    )
    assert parallel[1] == modules[1]
    assert parallel[0] == (modules[0][0], run_owned_passes(modules[0][1], _passes()))


@pytest.mark.parametrize("case", ("one-module", "small", "one-job"))
def test_serial_loop_where_the_pool_cannot_pay(monkeypatch, case):
    modules = _modules()
    if case == "one-module":
        monkeypatch.setattr(driver, "OWNED_PARALLEL_MIN_BYTES", 1)
        modules = modules[:1]
    elif case == "one-job":
        monkeypatch.setattr(driver, "OWNED_PARALLEL_MIN_BYTES", 1)
        monkeypatch.setenv("PCC_PYTHON_IR_PASS_JOBS", "1")
    assert driver.run_owned_passes_parallel(
        modules, _passes(), lambda: sys.executable, lambda: str(ROOT),
    ) is None


def test_a_failing_worker_raises(monkeypatch):
    monkeypatch.setattr(driver, "OWNED_PARALLEL_MIN_BYTES", 1)
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_JOBS", "2")
    modules = [("bad", "define i64 @broken( {\n"), ("good", _module(0, 2))]
    with pytest.raises(driver.PassDriverError):
        driver.run_owned_passes_parallel(
            modules, ["no-such-pass"], lambda: sys.executable, lambda: str(ROOT),
        )
