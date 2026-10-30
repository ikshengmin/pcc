"""Portable verification versus explicit unsupported native/durable operations.

These checks do not qualify Windows installation. The existing POSIX oracle
bodies remain separate and unchanged; no test is skipped or deselected here.
"""

import builtins
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from scripts import install_pcc1_toolchain as installer

ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = (
    "run_logged", "build_from_source", "record_gate", "qualify",
    "sync_directory", "write_history", "activation_lock",
    "replace_managed_entry", "promote", "rollback", "activate_initial", "install",
)
ACTIVATION = ("activation_lock", "replace_managed_entry", "promote", "rollback", "activate_initial")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _without_fcntl(monkeypatch, missing="fcntl"):
    original = builtins.__import__

    def import_without_fcntl(name, *args, **kwargs):
        if name == "fcntl":
            raise ModuleNotFoundError("missing " + missing, name=missing)
        return original(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", import_without_fcntl)
        return _load(ROOT / "scripts/install_pcc1_toolchain.py", "installer_without_fcntl")


class NeverTouch:
    def __getattr__(self, name):
        raise AssertionError("unsupported entry touched input: " + name)

    def __truediv__(self, other):
        raise AssertionError("unsupported entry formed a path: " + str(other))

    def __fspath__(self):
        raise AssertionError("unsupported entry accessed a filesystem path")


def _invoke(module, name):
    path = NeverTouch()
    arguments = {
        "run_logged": (path, "must-not-run", ["must-not-run"]),
        "build_from_source": (path,), "record_gate": ({}, path, path),
        "qualify": (path, {}, path, path), "sync_directory": (path,),
        "write_history": (path, {}), "activation_lock": (path,),
        "replace_managed_entry": (path, path, {}, {}),
        "promote": (path, path, path, path), "rollback": ("0" * 32, path, path),
        "activate_initial": (path, path), "install": (path,),
    }
    kwargs = {"operation": "promote"} if name == "replace_managed_entry" else {}
    result = getattr(module, name)(*arguments[name], **kwargs)
    with result if name == "activation_lock" else nullcontext(result):
        pass


def test_missing_fcntl_does_not_block_portable_module_import(monkeypatch):
    module = _without_fcntl(monkeypatch)
    assert module.fcntl is None
    assert callable(module.digest) and callable(module.verify_installation)


def test_unrelated_missing_import_is_not_hidden(monkeypatch):
    with pytest.raises(ModuleNotFoundError) as caught:
        _without_fcntl(monkeypatch, missing="unexpected_dependency")
    assert caught.value.name == "unexpected_dependency"


def test_original_oracle_module_imports_without_fcntl(monkeypatch):
    import scripts
    import sys

    module = _without_fcntl(monkeypatch)
    monkeypatch.setitem(sys.modules, "scripts.install_pcc1_toolchain", module)
    monkeypatch.setattr(scripts, "install_pcc1_toolchain", module)
    original = _load(ROOT / "tests/python/test_install_pcc1_toolchain.py", "installer_original_oracles")
    assert original.activate_initial is module.activate_initial
    assert original.installer is module
    assert len([name for name, value in vars(original).items()
                if name.startswith("test_") and callable(value)]) == 34


def test_portable_digest_copy_and_receipt_verification_remain_real(monkeypatch, tmp_path):
    module = _without_fcntl(monkeypatch)
    source = tmp_path / "source"
    source.write_bytes(b"receipt payload, not an executable\n")
    digest = module.digest(source)
    root = tmp_path / "candidate"
    root.mkdir()
    output = root / "payload"
    module.checked_copy(source, output, digest)
    receipt = {"status": "VERIFIED_BASELINE", "files": {"payload": digest}}
    (root / "installation.json").write_text(json.dumps(receipt))
    assert output.read_bytes() == source.read_bytes()
    assert module.verify_installation(root) == receipt
    assert module.file_reference(output) == {"path": str(output.resolve()), "sha256": digest}
    # checked_copy deliberately seals its output read-only. Keep that verified
    # copy intact; a separate corrupt candidate exercises real content hashing
    # without changing permissions or pretending the sealed payload is writable.
    corrupt = tmp_path / "corrupt-candidate"
    corrupt.mkdir()
    (corrupt / "payload").write_bytes(b"tampered")
    (corrupt / "installation.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="identity mismatch"):
        module.verify_installation(corrupt)
    assert output.read_bytes() == source.read_bytes()
    for bad in ("../outside", "/absolute", "payload/../outside", "./payload"):
        with pytest.raises(ValueError, match="unsafe receipt path"):
            module.receipt_child(root, bad)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_unsupported_native_or_durable_entry_rejects_before_side_effects(monkeypatch, operation):
    module = _without_fcntl(monkeypatch)
    # Change only this module's capability view, never global os/pathlib state.
    monkeypatch.setattr(module, "os", SimpleNamespace(**dict(vars(os), name="nt")))
    with pytest.raises(NotImplementedError, match="requires"):
        _invoke(module, operation)


@pytest.mark.parametrize("operation", ACTIVATION)
def test_activation_without_fcntl_rejects_before_touching_paths(monkeypatch, operation):
    module = _without_fcntl(monkeypatch)
    monkeypatch.setattr(module, "os", SimpleNamespace(**dict(vars(os), name="posix")))
    with pytest.raises(NotImplementedError, match="requires POSIX flock and O_NOFOLLOW"):
        _invoke(module, operation)


def test_activation_preserves_nofollow_or_explicitly_rejects_unsupported(tmp_path):
    bin_dir = tmp_path / "bin"
    if os.name != "posix" or installer.fcntl is None or not hasattr(os, "O_NOFOLLOW"):
        with pytest.raises(NotImplementedError, match="requires"):
            with installer.activation_lock(bin_dir):
                pytest.fail("unsupported activation entered its critical section")
        assert not bin_dir.exists()
        return
    with installer.activation_lock(bin_dir) as state:
        lock = state / "lock"
        assert stat.S_IMODE(lock.stat().st_mode) == 0o600
        with pytest.raises(ValueError, match="another toolchain activation"):
            with installer.activation_lock(bin_dir):
                pytest.fail("overlapping activation acquired the lock")
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    lock.unlink()
    lock.symlink_to(outside)
    with pytest.raises(OSError):
        with installer.activation_lock(bin_dir):
            pytest.fail("O_NOFOLLOW accepted the lock symlink")
    assert outside.read_bytes() == b"untouched"


def test_history_uses_real_file_and_directory_fsync_or_rejects(monkeypatch, tmp_path):
    target = tmp_path / "journal" / "record.json"
    if os.name != "posix":
        with pytest.raises(NotImplementedError, match="directory fsync"):
            installer.write_history(target, {"state": "prepared"})
        assert not target.parent.exists()
        return
    target.parent.mkdir()
    calls = []

    def fsync(fd):
        calls.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        return os.fsync(fd)

    monkeypatch.setattr(installer, "os", SimpleNamespace(**dict(vars(os), fsync=fsync)))
    installer.write_history(target, {"state": "prepared"})
    assert json.loads(target.read_text()) == {"state": "prepared"}
    assert calls == [False, True]
    with pytest.raises(FileExistsError):
        installer.write_history(target, {"state": "overwritten"})
    assert json.loads(target.read_text()) == {"state": "prepared"}
