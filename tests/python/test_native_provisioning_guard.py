"""These orchestration checks never provision or execute native artifacts."""

import pytest

from tests.native_provisioning import GUARD_NAME, require_native_provisioning_allowed


def test_guard_file_blocks_even_without_environment_inheritance(tmp_path, monkeypatch):
    monkeypatch.delenv("PCC_TEST_NO_NATIVE_PROVISIONING", raising=False)
    (tmp_path / "build").mkdir()
    guard = tmp_path / "build" / GUARD_NAME
    guard.write_text("central compiler qualification is active\n")
    with pytest.raises(RuntimeError, match="automatic native test provisioning is disabled"):
        require_native_provisioning_allowed(tmp_path)
    guard.unlink()
    require_native_provisioning_allowed(tmp_path)


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_environment_guard_blocks_without_marker(tmp_path, monkeypatch, value):
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", value)
    with pytest.raises(RuntimeError, match="automatic native test provisioning is disabled"):
        require_native_provisioning_allowed(tmp_path)


def test_runtime_builder_guard_fails_before_builder_call(tmp_path, monkeypatch):
    from tests.runtime_build_cache import cache_runtime_build

    calls = []

    @cache_runtime_build
    def builder(_temporary):
        calls.append(True)
        return object()

    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    with pytest.raises(RuntimeError, match="automatic native test provisioning is disabled"):
        builder(tmp_path)
    assert calls == []


@pytest.mark.parametrize("fixture_name", ["pcc_runtime_archive", "threaded_pcc_runtime_archive"])
def test_shared_fixture_guard_precedes_automatic_builder(monkeypatch, fixture_name):
    from tests import conftest as fixtures
    from pcc.frontends.python import owned_runtime_build

    monkeypatch.delenv("PCC_RUNTIME_ARCHIVE", raising=False)
    monkeypatch.delenv("PCC_THREADED_RUNTIME_ARCHIVE", raising=False)
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")

    def unexpected_builder(*_args, **_kwargs):
        raise AssertionError("runtime builder was entered")

    monkeypatch.setattr(owned_runtime_build, "ensure_target_runtime", unexpected_builder)
    monkeypatch.setattr(fixtures, "cached_pcc_python_runtime", unexpected_builder)
    monkeypatch.setattr(fixtures, "cached_threaded_pcc_python_runtime", unexpected_builder)
    fixture = getattr(fixtures, fixture_name).__wrapped__
    with pytest.raises(RuntimeError, match="automatic native test provisioning is disabled"):
        if fixture_name == "pcc_runtime_archive":
            fixture(None)
        else:
            fixture()
