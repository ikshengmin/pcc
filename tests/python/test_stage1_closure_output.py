"""Portable, invocation-owned IR output; no full closure or native build."""

from pathlib import Path
import sys

import pytest

from scripts import probe_stage1_closure as probe

pytestmark = pytest.mark.xdist_group("stage1-closure-output")


@pytest.fixture
def owned_temp_root(tmp_path, monkeypatch):
    root = tmp_path / "managed temp 空间"
    root.mkdir()
    # Exercise the standard-library default temp-directory contract, including
    # spaces/Unicode, rather than making /tmp or changing the production API.
    monkeypatch.setattr(probe.tempfile, "tempdir", str(root))
    return root


def _replace_compile(monkeypatch, compile_function):
    from pcc.frontends.python import pipeline
    monkeypatch.setattr(pipeline, "compile_python_multi", compile_function)


def _assert_empty(root):
    assert list(root.iterdir()) == []


def test_success_preserves_exact_compile_arguments_and_cleans_output(owned_temp_root, monkeypatch):
    sources, modules = ["entry.py", "helper.py"], ["entry", "helper"]
    paths = []
    def compile_ir(actual_sources, output, **kwargs):
        assert actual_sources is sources
        assert kwargs == {"verbose": False, "emit_llvm_only": True,
                          "entry_module": "entry", "module_names": modules,
                          "libpython_mode": "off", "backend": "self"}
        assert kwargs["module_names"] is modules
        path = Path(output); paths.append(path)
        assert path.name == "closure.ll" and path.parent.parent == owned_temp_root
        assert path.parent.is_dir() and not path.exists()
        path.write_text("; exact caller output\n", encoding="utf-8")
    _replace_compile(monkeypatch, compile_ir)
    assert probe._try_full_multi_compile(sources, modules) == (True, "; exact caller output\n", "")
    assert len(paths) == 1 and not paths[0].exists() and not paths[0].parent.exists()
    _assert_empty(owned_temp_root)


@pytest.mark.parametrize("failure", ["before_write", "after_write", "no_output"])
def test_failure_preserves_error_and_cleans_owned_directory(owned_temp_root, monkeypatch, failure):
    paths = []
    def compile_ir(sources, output, **kwargs):
        path = Path(output); paths.append(path)
        if failure == "after_write": path.write_text("partial output", encoding="utf-8")
        if failure != "no_output": raise ValueError("ordinary compile sentinel")
    _replace_compile(monkeypatch, compile_ir)
    ok, text, error = probe._try_full_multi_compile(["entry.py"], ["entry"])
    assert ok is False and text == ""
    if failure == "no_output":
        assert error == "no IR produced"
    else:
        assert error.startswith("ValueError: ordinary compile sentinel\nTraceback")
        assert "compile_ir" in error
    assert len(paths) == 1 and not paths[0].parent.exists()
    _assert_empty(owned_temp_root)


def test_repeated_call_cannot_reuse_old_ir(owned_temp_root, monkeypatch):
    paths = []
    def compile_ir(sources, output, **kwargs):
        path = Path(output); paths.append(path)
        assert not path.exists()
        if len(paths) == 1: path.write_text("; first result\n", encoding="utf-8")
    _replace_compile(monkeypatch, compile_ir)
    assert probe._try_full_multi_compile(["entry.py"], ["entry"]) == (True, "; first result\n", "")
    assert probe._try_full_multi_compile(["entry.py"], ["entry"]) == (False, "", "no IR produced")
    assert len(paths) == 2 and paths[0] != paths[1]
    _assert_empty(owned_temp_root)


def test_overlapping_calls_have_independent_output_ownership(owned_temp_root, monkeypatch):
    paths = []
    def compile_ir(sources, output, **kwargs):
        path = Path(output); paths.append(path)
        if sources == ["outer.py"]:
            path.write_text("; outer result\n", encoding="utf-8")
            assert probe._try_full_multi_compile(["inner.py"], ["inner"]) == (True, "; inner result\n", "")
            assert path.read_text(encoding="utf-8") == "; outer result\n"
        else:
            assert sources == ["inner.py"]
            assert len(list(owned_temp_root.iterdir())) == 2
            path.write_text("; inner result\n", encoding="utf-8")
    _replace_compile(monkeypatch, compile_ir)
    assert probe._try_full_multi_compile(["outer.py"], ["outer"]) == (True, "; outer result\n", "")
    assert len(paths) == 2 and paths[0].parent != paths[1].parent
    _assert_empty(owned_temp_root)


def test_temporary_directory_failure_remains_compile_failure(monkeypatch):
    def unavailable(**kwargs): raise PermissionError("temporary directory unavailable")
    def never_compile(*args, **kwargs): raise AssertionError("compiler must not run")
    monkeypatch.setattr(probe.tempfile, "TemporaryDirectory", unavailable)
    _replace_compile(monkeypatch, never_compile)
    ok, text, error = probe._try_full_multi_compile(["entry.py"], ["entry"])
    assert ok is False and text == ""
    assert error.startswith("PermissionError: temporary directory unavailable\nTraceback")


def test_real_two_module_compile_returns_owned_ir(owned_temp_root, tmp_path, monkeypatch):
    from pcc.frontends.python import pipeline
    from pcc.backend.self_backend_parse import parse_self_backend_module

    entry = tmp_path / "closure_entry.py"
    helper = tmp_path / "closure_helper.py"
    entry.write_text("from closure_helper import inc\n\ndef main() -> int:\n    return inc(41)\n", encoding="utf-8")
    helper.write_text("def inc(value: int) -> int:\n    return value + 1\n", encoding="utf-8")
    # Match the existing fallback fixture's raw-codegen selection. This does
    # not change pytest's six workers or the frontend's scheduling policy.
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.delenv("PCC_IR_SCAFFOLD", raising=False)
    assert pipeline._python_frontend_worker_command_prefix() == [sys.executable, "-m", "pcc"]
    original = pipeline.compile_python_multi
    paths = []
    def observe(*args, **kwargs):
        path = Path(args[1]); paths.append(path)
        assert path.parent.parent == owned_temp_root and path.parent.is_dir()
        assert kwargs["emit_llvm_only"] is True and kwargs["backend"] == "self"
        return original(*args, **kwargs)  # Actual production frontend, not a stub.
    monkeypatch.setattr(pipeline, "compile_python_multi", observe)
    sources = [str(entry), str(helper)]
    ok, ir_text, error = probe._try_full_multi_compile(sources, ["closure_entry", "closure_helper"])
    assert ok, error
    assert error == "" and ir_text
    assert "; ---- module: closure_entry ----\n" in ir_text
    assert "; ---- module: closure_helper ----\n" in ir_text
    parsed = parse_self_backend_module(ir_text)
    names = {function.name for function in parsed.functions}
    assert {"user_closure_entry_main", "user_closure_helper_inc"} <= names
    assert len(paths) == 1 and not paths[0].parent.exists()
    _assert_empty(owned_temp_root)
