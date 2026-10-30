"""No-follow path predicates: adaptation, lowering, and real native witness.

Host/model checks do not qualify the owned runtime. The integration witness
requires a separately admitted, source-matched rebuilt pcc1/runtime pair.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.process_timeout import run_process_group_timeout
from tests.python.test_owned_os_path_provider import load_provider


ROOT = Path(__file__).resolve().parents[2]


def provider_with_lexists(native):
    provider = load_provider(native=native)
    # Explicit host ABI model, not an owned-runtime implementation witness.
    provider._path_lexists_result = os.path.lexists
    return provider


@pytest.mark.parametrize("native", (False, True), ids=("host", "modeled-owned-abi"))
def test_lexists_regular_directory_missing_and_pathlike(tmp_path, native):
    provider = provider_with_lexists(native)
    regular = tmp_path / "regular"
    regular.write_bytes(b"content")
    for path, expected in ((regular, True), (tmp_path, True), (tmp_path / "missing", False)):
        assert provider.path.lexists(str(path)) is expected
        assert provider.path.lexists(os.fsencode(path)) is expected
        calls = []

        class PathArg:
            def __fspath__(self):
                calls.append("fspath")
                return str(path)

        assert provider.path.lexists(PathArg()) is expected
        assert calls == ["fspath"]


@pytest.mark.parametrize("native", (False, True), ids=("host", "modeled-owned-abi"))
def test_lexists_observes_live_and_broken_symlinks(tmp_path, native):
    provider = provider_with_lexists(native)
    regular = tmp_path / "regular"
    regular.write_bytes(b"unchanged")
    live = tmp_path / "live"
    broken = tmp_path / "broken"
    live.symlink_to(regular)
    broken.symlink_to(tmp_path / "absent")
    assert provider.path.lexists(live) is True
    assert provider.path.lexists(broken) is True
    assert os.path.exists(broken) is False
    assert regular.read_bytes() == b"unchanged"


@pytest.mark.parametrize("native", (False, True), ids=("host", "modeled-owned-abi"))
def test_lexists_path_protocol_and_predicate_errors(tmp_path, native):
    provider = provider_with_lexists(native)
    assert provider.path.lexists("bad\0path") is False
    for value in (None, 7, object()):
        with pytest.raises(TypeError):
            provider.path.lexists(value)

    class InvalidPath:
        def __fspath__(self):
            return 7

    with pytest.raises(TypeError):
        provider.path.lexists(InvalidPath())
    failure = RuntimeError("path callback failure")

    class RaisedPath:
        def __fspath__(self):
            raise failure

    with pytest.raises(RuntimeError) as captured:
        provider.path.lexists(RaisedPath())
    assert captured.value is failure
    for failure in (OSError(5, "metadata unavailable"), ValueError("bad path")):
        assert provider.path.lexists(RaisedPath()) is False


def test_lexists_owned_wrapper_preserves_bool_error_and_null_contract():
    provider = provider_with_lexists(True)
    operand = object()
    seen = []
    provider._path_lexists_result = lambda value: seen.append(value) or True
    assert provider.path.lexists(operand) is True
    assert seen == [operand]
    failure = TypeError("original path exception")
    provider._path_lexists_result = lambda value: failure
    with pytest.raises(TypeError) as captured:
        provider.path.lexists(operand)
    assert captured.value is failure
    provider._path_lexists_result = lambda value: None
    with pytest.raises(RuntimeError, match="NULL without an exception"):
        provider.path.lexists(operand)


def test_lexists_runtime_query_uses_no_follow_record_not_access():
    tree = ast.parse((ROOT / "pcc/runtime/py/py_os_path.py").read_text())
    query = next(node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == "_fs_link_query")
    calls = {node.func.id for node in ast.walk(query)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "lstat_kind" in calls and "is_symlink" in calls
    assert not calls.intersection({"access", "stat_kind", "stat_size", "exists"})


def test_lexists_direct_lowering_stays_owned(tmp_path):
    from pcc.frontends.python.pipeline import compile_python, count_py_cpy_fallback_calls

    source = tmp_path / "lexists.py"
    output = tmp_path / "lexists.ll"
    source.write_text("import os\ndef present(path):\n    return os.path.lexists(path)\n")
    compile_python(str(source), str(output), emit_llvm_only=True,
                   libpython_mode="off", ir_scaffold_mode="on")
    text = output.read_text()
    body = re.search(
        r"define [^\n]*@user_[^\n]*present\([^\n]*\)\s*[^\n]*\{(.*?)\n\}",
        text, re.S,
    )
    assert body is not None
    assert re.search(r"\bcall\b[^\n]*@py_os_path_lexists\(", body[1])
    assert count_py_cpy_fallback_calls(text) == 0
    assert "strict.nolib.stub" not in text


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc1",), indirect=True)
def test_lexists_native_public_surface(
    tmp_path, python_program_compiler, explicit_owned_runtime,
):
    """Execute the public API, not a host shim, with the admitted rebuilt pair."""
    regular = tmp_path / "regular"
    regular.write_bytes(b"unchanged")
    live = tmp_path / "live"
    broken = tmp_path / "broken"
    live.symlink_to(regular)
    broken.symlink_to(tmp_path / "absent")
    present = [str(regular), str(tmp_path), str(live), str(broken)]
    source = tmp_path / "lexists-native.py"
    source.write_text('''import os

class PathArg:
    def __init__(self, value):
        self.value = value
        self.calls = 0
    def __fspath__(self):
        self.calls += 1
        return self.value

class InvalidPath:
    def __fspath__(self):
        return 7

class RaisedPath:
    def __fspath__(self):
        raise RuntimeError("path callback failure")

def main():
    saved = os.path.lexists
    receiver = os.path
    for path in ''' + repr(present) + ''':
        assert os.path.lexists(path) is True
        assert saved(path) is True
        assert receiver.lexists(path) is True
        value = PathArg(path)
        assert saved(value) is True
        assert value.calls == 1
    assert saved(''' + repr(os.fsencode(regular)) + ''') is True
    assert saved(''' + repr(str(tmp_path / "missing")) + ''') is False
    assert saved("bad\\0path") is False
    for value in [None, 7, InvalidPath()]:
        try:
            saved(value)
        except TypeError:
            pass
        else:
            raise AssertionError("invalid path accepted")
    try:
        saved(RaisedPath())
    except RuntimeError as error:
        assert str(error) == "path callback failure"
    else:
        raise AssertionError("path callback error lost")
    print("LEXISTS_NATIVE_OK")
main()
''')
    output = tmp_path / ("lexists-native.exe" if os.name == "nt" else "lexists-native")
    python_program_compiler(
        str(source), str(output), backend="self", libpython_mode="off",
        ir_scaffold_mode="on", runtime_archive=str(explicit_owned_runtime),
    )
    result = run_process_group_timeout(
        [str(output)], timeout=20,
        env=dict(os.environ, PCC_HOST_PYTHON="/nonexistent/host-python",
                 PCC_HOST_PCC="/nonexistent/host-pcc", PATH=""),
    )
    receipt = {
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "runtime_sha256": hashlib.sha256(explicit_owned_runtime.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
    }
    (tmp_path / "lexists-native-result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    assert (result.returncode, result.stdout, result.stderr) == (0, "LEXISTS_NATIVE_OK\n", "")
    assert regular.read_bytes() == b"unchanged"
