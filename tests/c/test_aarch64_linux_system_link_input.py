"""Host ELF/argv contracts; no external compiler or native program is run."""

from pathlib import Path
import subprocess

import pytest

from pcc.backend.elf_x86_64 import EM_AARCH64, SHN_UNDEF, parse_relocatable
from pcc.backend import BackendUnavailable
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
import pcc.frontends.c.evaluator.c_evaluator as c_evaluator


TARGET = "aarch64-unknown-linux-gnu"


def _units(count=1, target=TARGET):
    caller = f'''target triple = "{target}"
declare i64 @read_value()
define i64 @main() {{
entry:
  %value = call i64 @read_value()
  ret i64 %value
}}
'''
    units = [("main.c", caller, None, {})]
    if count == 2:
        provider = f'''target triple = "{target}"
@value = global i64 42
define i64 @read_value() {{
entry:
  %result = load i64, ptr @value
  ret i64 %result
}}
'''
        units.append(("value.c", provider, None, {}))
    return units


def _evaluator(target=TARGET):
    # This entry already receives compiled IR. Do not construct a C parser or
    # compile fixture sources merely to exercise its link-input contract.
    evaluator = CEvaluator.__new__(CEvaluator)
    evaluator.target_triple = target
    evaluator.is_cross = False
    return evaluator


def _observe_process_boundary(monkeypatch, evaluator, *, link_returncode=0):
    calls = []
    payloads = []
    monkeypatch.setattr(evaluator, "_system_cc", lambda: "explicit-system-cc-oracle")
    monkeypatch.setattr(evaluator, "_freestanding_link_inputs",
                        lambda *args, **kwargs: ([], [], []))

    def observe(argv, **kwargs):
        calls.append((list(argv), kwargs))
        if argv[0] == "explicit-system-cc-oracle":
            payloads.append(Path(argv[1]).read_bytes())
            return subprocess.CompletedProcess(argv, link_returncode, "", "sentinel link failure")
        assert len(calls) == 2, "unexpected external process"
        assert Path(argv[0]).name == "a.out"
        return subprocess.CompletedProcess(argv, 0, "host boundary only", "")

    monkeypatch.setattr(c_evaluator.subprocess, "run", observe)
    return calls, payloads


def _run(evaluator, units, *, optimize=False, **kwargs):
    return evaluator._run_compiled_translation_units_self_backend(
        units, optimize=optimize, link_with_system_cc=True,
        capture_output=True, text=True, **kwargs,
    )


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("selector", [None, "pcc"])
def test_aarch64_system_link_receives_real_owned_elf(monkeypatch, count, selector):
    if selector is None:
        monkeypatch.delenv("PCC_SELF_OBJ", raising=False)
    else:
        monkeypatch.setenv("PCC_SELF_OBJ", selector)
    evaluator = _evaluator()
    calls, payloads = _observe_process_boundary(monkeypatch, evaluator)
    result = _run(evaluator, _units(count), prog_args=["argument"], link_args=["-lm"])
    assert result.stdout == "host boundary only"
    assert len(calls) == 2 and len(payloads) == 1
    assert Path(calls[0][0][1]).name == "self_backend.o"
    assert calls[0][0][-1] == "-lm"
    assert calls[1][0][1:] == ["argument"]
    assert payloads[0][:4] == b"\x7fELF"
    obj = parse_relocatable(payloads[0])
    assert obj.machine == EM_AARCH64
    symbols = {symbol.name: symbol for symbol in obj.symbols}
    assert symbols["main"].section_index != SHN_UNDEF
    assert (symbols["read_value"].section_index != SHN_UNDEF) == (count == 2)
    assert any(section.name == ".text" and section.data for section in obj.sections)
    assert any(section.relocations for section in obj.sections)
    assert sum(section.name == ".pcc_stackmaps" for section in obj.sections) <= 1
    assert not Path(calls[0][0][1]).parent.exists(), "temporary inputs must be retired"


@pytest.mark.parametrize("optimize", [False, 0, True, 2])
def test_object_handoff_does_not_repeat_optimization(monkeypatch, optimize):
    monkeypatch.delenv("PCC_SELF_OBJ", raising=False)
    evaluator = _evaluator()
    _observe_process_boundary(monkeypatch, evaluator)
    original = evaluator._prepare_self_backend_units
    prepared = []

    def record(units, *, optimize):
        prepared.append(optimize)
        return original(units, optimize=optimize)

    monkeypatch.setattr(evaluator, "_prepare_self_backend_units", record)
    _run(evaluator, _units(), optimize=optimize)
    assert prepared == ([optimize] if evaluator._normalize_opt_level(optimize) > 0 else [])


@pytest.mark.parametrize("target", [
    "x86_64-unknown-linux-gnu", "arm64-apple-darwin", "x86_64-pc-windows-msvc",
])
def test_other_target_system_link_routes_keep_assembly(monkeypatch, target):
    evaluator = _evaluator(target)
    calls, payloads = _observe_process_boundary(monkeypatch, evaluator)
    monkeypatch.setattr(evaluator, "_self_backend_asm_text", lambda units: "original transport\n")

    def reject(*args, **kwargs):
        pytest.fail("non-AArch64-Linux route unexpectedly selected object emission")

    monkeypatch.setattr(evaluator, "_emit_compiled_units_self_backend", reject)
    _run(evaluator, _units(target=target))
    assert Path(calls[0][0][1]).name == "self_backend.s"
    assert payloads == [b"original transport\n"]

    # The added target inspection must not replace the established assembly
    # failure or reach system-cc when the input target itself is invalid.
    calls.clear()
    def invalid_assembly(units):
        raise BackendUnavailable("original invalid-target assembly boundary")
    monkeypatch.setattr(evaluator, "_self_backend_asm_text", invalid_assembly)
    with pytest.raises(BackendUnavailable, match="^original invalid-target assembly boundary$"):
        _run(evaluator, _units(target="riscv64-unknown-linux-gnu"))
    assert calls == []


def test_unknown_object_selector_is_not_an_external_fallback(monkeypatch):
    monkeypatch.setenv("PCC_SELF_OBJ", "misspelled")
    evaluator = _evaluator()
    calls, _ = _observe_process_boundary(monkeypatch, evaluator)
    with pytest.raises(BackendUnavailable, match="PCC_SELF_OBJ must be"):
        _run(evaluator, _units())
    assert calls == []


def test_mixed_target_objects_fail_before_external_process(monkeypatch):
    monkeypatch.delenv("PCC_SELF_OBJ", raising=False)
    evaluator = _evaluator()
    calls, _ = _observe_process_boundary(monkeypatch, evaluator)
    with pytest.raises(BackendUnavailable, match="cannot combine target identities"):
        _run(evaluator, _units() + _units(target="x86_64-unknown-linux-gnu"))
    assert calls == []


def test_link_failure_keeps_error_and_retires_object(monkeypatch):
    monkeypatch.delenv("PCC_SELF_OBJ", raising=False)
    evaluator = _evaluator()
    calls, _ = _observe_process_boundary(monkeypatch, evaluator, link_returncode=1)
    with pytest.raises(RuntimeError, match="self backend link failed: sentinel link failure"):
        _run(evaluator, _units())
    assert len(calls) == 1
    assert not Path(calls[0][0][1]).parent.exists()


def test_explicit_system_assembler_selection_remains_explicit(monkeypatch):
    monkeypatch.setenv("PCC_SELF_OBJ", "system-as")
    evaluator = _evaluator()
    monkeypatch.setattr(evaluator, "_system_cc", lambda: "explicit-system-cc-oracle")
    calls = []

    def refuse(argv, **kwargs):
        calls.append(list(argv))
        assert argv[0:2] == ["explicit-system-cc-oracle", "-c"]
        assert Path(argv[2]).name == "self_backend.s"
        return subprocess.CompletedProcess(argv, 1, "", "explicit assembler sentinel")

    monkeypatch.setattr(c_evaluator.subprocess, "run", refuse)
    with pytest.raises(RuntimeError, match="object emission failed: explicit assembler sentinel"):
        _run(evaluator, _units())
    assert len(calls) == 1
