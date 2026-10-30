"""Real provider bodies retain portable values and fail before unsupported NSS."""

import builtins
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
_FIELDS = ("pw_name", "pw_passwd", "pw_uid", "pw_gid",
           "pw_gecos", "pw_dir", "pw_shell")
_VALUES = ("record-name", "record-password", 17, 23,
           "record-gecos", "record-home", "record-shell")


def _load_source(path, name, platform):
    """Execute unchanged source with only its own sys import target-selected."""
    module = ModuleType(name)
    module.__file__ = str(path)
    real_import = builtins.__import__

    def target_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sys":
            return SimpleNamespace(platform=platform)
        if name == "pwd" and platform == "win32":
            raise ModuleNotFoundError("No module named 'pwd'", name="pwd")
        return real_import(name, globals, locals, fromlist, level)

    module.__dict__["__builtins__"] = dict(vars(builtins), __import__=target_import)
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


def _provider(name, platform):
    return _load_source(ROOT / "pcc" / "stdlib" / (name + ".py"),
                        "_pcc_" + name + "_" + platform, platform)


@pytest.mark.parametrize("platform", ("linux", "darwin", "win32"))
def test_pwd_record_contract_is_target_independent(platform):
    pwd = _provider("pwd", platform)
    record = pwd.struct_passwd(_VALUES)
    assert len(record) == 7
    assert tuple(record) == _VALUES and record == _VALUES
    assert record == pwd.struct_passwd(_VALUES)
    assert record != pwd.struct_passwd(_VALUES[:-1] + ("other-shell",))
    assert hash(record) == hash(_VALUES)
    assert repr(record) == "pwd.struct_passwd(" + ", ".join(
        name + "=" + repr(value) for name, value in zip(_FIELDS, _VALUES)
    ) + ")"
    assert (record.n_fields, record.n_sequence_fields, record.n_unnamed_fields) == (7, 7, 0)
    for name, value in zip(_FIELDS, _VALUES):
        assert getattr(record, name) == value
    assert record[2] == 17 and record[-1] == "record-shell"
    assert record[1:3] == _VALUES[1:3]
    assert record.index(23) == 3 and record.count("record-name") == 1
    with pytest.raises(AttributeError):
        record.pw_dir = "changed"
    for values in (_VALUES[:-1], _VALUES + ("extra",)):
        with pytest.raises(TypeError, match="seven-item"):
            pwd.struct_passwd(values)


@pytest.mark.parametrize("platform", ("linux", "darwin", "win32"))
def test_pwd_invalid_arguments_precede_any_lookup(platform):
    pwd = _provider("pwd", platform)

    def forbidden():
        pytest.fail("invalid arguments reached NSS allocation")

    pwd._allocate_lookup_state = forbidden
    with pytest.raises(TypeError):
        pwd.getpwnam(b"record-name")
    with pytest.raises(ValueError):
        pwd.getpwnam("record-name\0suffix")
    with pytest.raises(TypeError):
        pwd.getpwuid(1.5)
    for uid in (-1, 4294967296):
        with pytest.raises(KeyError):
            pwd.getpwuid(uid)


def test_windows_signal_import_reports_owned_target_boundary():
    with pytest.raises(NotImplementedError, match="only Darwin and Linux ABIs"):
        _provider("signal", "win32")


def test_build_tool_test_module_collects_without_windows_pwd_or_signal():
    path = ROOT / "tests/python/test_py_stdlib_build_tool_closure.py"
    module = _load_source(path, "_pcc_build_tool_windows_contract", "win32")
    assert module.host_pwd is None
    assert module.port_signal is None
    assert callable(module.test_pwd_struct_passwd_sequence_surface_matches_cpython)
    assert callable(module.test_signal_constants_names_and_valid_set_match_cpython)


def test_build_tool_windows_probe_excludes_only_explicitly_unsupported_providers(monkeypatch):
    path = ROOT / "tests/python/test_py_stdlib_build_tool_closure.py"
    module = _load_source(path, "_pcc_build_tool_windows_probe", "win32")
    module.port_pwd = _provider("pwd", "win32")

    def signal_boundary():
        with pytest.raises(NotImplementedError, match="only Darwin and Linux ABIs"):
            _provider("signal", "win32")

    monkeypatch.setattr(module, "_assert_signal_target_unsupported", signal_boundary)
    probe = module._build_tool_probe_for_target()
    assert "import pwd\n" not in probe and "import signal\n" not in probe
    assert "SIGKILL" not in probe and "SIGUSR1" not in probe
    for retained in ("runpy-api", "codec-decode", "uuid4-shape"):
        assert retained in probe
    module.host_pwd = object()
    assert module._build_tool_probe_for_target() == module._COMPILED_PROBE


def test_build_tool_windows_record_and_validation_execute_real_bodies():
    path = ROOT / "tests/python/test_py_stdlib_build_tool_closure.py"
    module = _load_source(path, "_pcc_build_tool_windows_values", "win32")
    module.port_pwd = _provider("pwd", "win32")
    module.test_pwd_struct_passwd_sequence_surface_matches_cpython()
    module.test_pwd_lookup_input_validation_matches_cpython()
