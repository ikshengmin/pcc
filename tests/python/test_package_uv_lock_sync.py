from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

import pcc.package.uv_lock_sync as uv_lock_sync
from pcc.package.uv_lock_sync import (
    UvLockSyncError,
    marker_applies,
    project_uv_lock,
    sync_uv_lock,
)
from pcc.package.environment import environment_info_json, resolve_package_environment


def _write_package(root: Path, name: str, value: int) -> None:
    package = root / name.replace("-", "_")
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    (package / "__init__.py").write_text(f"VALUE = {value}\n", encoding="utf-8")


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "home"),
        "VIRTUAL_ENV": str(tmp_path / "project" / ".venv"),
        "PCC_PACKAGE_TARGET_PYTHON": "3.11",
        "PCC_TARGET_TRIPLE": "aarch64-apple-darwin",
        "PCC_PACKAGE_CACHE": str(tmp_path / "cache"),
    }


def _graph_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "demo-project"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    for name, value in (
        ("dep-a", 1),
        ("dep-b", 2),
        ("dep-win", 3),
        ("dep-group", 4),
        ("dep-extra", 5),
    ):
        _write_package(project / "deps" / name, name, value)
    (project / "uv.lock").write_text(
        """version = 1
revision = 3
requires-python = ">=3.11"

[[package]]
name = "demo-project"
version = "0.0.0"
source = { editable = "." }
dependencies = [
  { name = "dep-a" },
  { name = "dep-win", marker = "sys_platform == 'win32'" },
]

[package.dev-dependencies]
test = [{ name = "dep-group" }]

[package.optional-dependencies]
speed = [{ name = "dep-extra" }]

[[package]]
name = "dep-a"
version = "1.0.0"
source = { directory = "deps/dep-a" }
dependencies = [{ name = "dep-b" }]

[[package]]
name = "dep-b"
version = "1.0.0"
source = { directory = "deps/dep-b" }

[[package]]
name = "dep-win"
version = "1.0.0"
source = { directory = "deps/dep-win" }

[[package]]
name = "dep-group"
version = "1.0.0"
source = { directory = "deps/dep-group" }

[[package]]
name = "dep-extra"
version = "1.0.0"
source = { directory = "deps/dep-extra" }
""",
        encoding="utf-8",
    )
    return project


def _fake_pcc1(
    path: Path,
    counter: Path,
    *,
    fail: bool = False,
    failure_diagnostic: str = "forced failure",
) -> None:
    script = f"""#!{sys.executable}
import json
from pathlib import Path
import shutil
import sys

counter = Path({str(counter)!r})
count = int(counter.read_text() if counter.exists() else "0") + 1
counter.write_text(str(count))
if {fail!r}:
    print({failure_diagnostic!r}, file=sys.stderr)
    raise SystemExit(7)
args = sys.argv[1:]
source = Path(args[args.index("install") + 1])
target = Path(args[args.index("--target") + 1])
target.mkdir(parents=True, exist_ok=True)
for child in source.iterdir():
    if child.name == "pyproject.toml":
        continue
    destination = target / child.name
    if child.is_dir():
        shutil.copytree(child, destination)
    elif child.suffix == ".py":
        shutil.copy2(child, destination)
print(json.dumps({{"ok": True, "installs": [{{"build_report": {{"skipped": True}}}}]}}))
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def test_project_uv_lock_selects_frozen_graph_groups_extras_and_markers(tmp_path):
    project = _graph_project(tmp_path)
    projection = project_uv_lock(
        project / "uv.lock",
        project_root=project,
        target_python="3.11",
        target_triple="aarch64-apple-darwin",
        groups=("test",),
        extras=("speed",),
    )

    assert projection["schema"] == "pcc.uv-lock-adapter.v1"
    assert projection["uv_lock_version"] == 1
    assert projection["uv_lock_revision"] == 3
    assert projection["root_packages"] == ["demo-project"]
    assert [row["name"] for row in projection["packages"]] == [
        "dep-b",
        "dep-a",
        "dep-group",
        "dep-extra",
    ]
    assert all(
        row["artifact"]["kind"] == "local-directory"
        and len(row["artifact"]["sha256"]) == 64
        for row in projection["packages"]
    )


def test_marker_evaluator_is_target_specific_and_fails_closed():
    environment = {
        "sys_platform": "darwin",
        "python_version": "3.11",
        "extra": "",
    }
    assert marker_applies(
        "sys_platform == 'darwin' and python_version >= '3.11'", environment
    )
    assert not marker_applies("sys_platform == 'win32'", environment)
    assert not marker_applies(
        "python_version >= '3.11'", {**environment, "python_version": "3.9"}
    )
    assert marker_applies("extra == 'speed'", environment, extras=("speed",))
    with pytest.raises(UvLockSyncError, match="unsupported marker variable") as exc:
        marker_applies("unknown_platform == 'x'", environment)
    assert exc.value.code == "PCC-PKG-UVLOCK-MARKER-UNSUPPORTED"


def test_uv_lock_rejects_schema_target_and_missing_artifact(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    lock = project / "uv.lock"
    lock.write_text(
        'version = 2\nrevision = 3\nrequires-python = ">=3.11"\npackage = []\n',
        encoding="utf-8",
    )
    with pytest.raises(UvLockSyncError) as schema:
        project_uv_lock(
            lock,
            target_python="3.11",
            target_triple="aarch64-apple-darwin",
        )
    assert schema.value.code == "PCC-PKG-UVLOCK-UNSUPPORTED-SCHEMA"

    lock.write_text(
        """version = 1
revision = 3
requires-python = ">=3.13"
[[package]]
name = "project"
version = "0"
source = { editable = "." }
""",
        encoding="utf-8",
    )
    with pytest.raises(UvLockSyncError) as target:
        project_uv_lock(
            lock,
            target_python="3.11",
            target_triple="aarch64-apple-darwin",
        )
    assert target.value.code == "PCC-PKG-UVLOCK-TARGET-PYTHON-MISMATCH"

    lock.write_text(
        """version = 1
revision = 3
requires-python = ">=3.11"
[[package]]
name = "project"
version = "0"
source = { editable = "." }
dependencies = [{ name = "native-only" }]
[[package]]
name = "native-only"
version = "1"
source = { registry = "https://example.invalid/simple" }
wheels = [{ url = "https://example.invalid/native_only-1-cp313-cp313-macosx_11_0_arm64.whl", hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }]
""",
        encoding="utf-8",
    )
    with pytest.raises(UvLockSyncError) as artifact:
        project_uv_lock(
            lock,
            target_python="3.11",
            target_triple="aarch64-apple-darwin",
        )
    assert artifact.value.code == "PCC-PKG-UVLOCK-INCOMPATIBLE-WHEEL"

    lock.write_text(
        """version = 1
revision = 3
requires-python = ">=3.11"
[[package]]
name = "project"
version = "0"
source = { editable = "." }
dependencies = [{ name = "missing-source" }]
[[package]]
name = "missing-source"
version = "1"
source = { directory = "does-not-exist" }
""",
        encoding="utf-8",
    )
    with pytest.raises(UvLockSyncError) as missing:
        project_uv_lock(
            lock,
            target_python="3.11",
            target_triple="aarch64-apple-darwin",
        )
    assert missing.value.code == "PCC-PKG-UVLOCK-MISSING-ARTIFACT"


def test_locked_sync_is_transactional_and_unchanged_repeat_skips_installer(tmp_path):
    project = _graph_project(tmp_path)
    stale_build = project / "deps" / "dep-b" / "build"
    stale_build.mkdir()
    (stale_build / "stale.cpython-314-darwin.so").write_bytes(b"stale")
    # Keep the transaction probe focused on one local dependency.
    text = (project / "uv.lock").read_text(encoding="utf-8")
    text = text.replace(
        'dependencies = [\n  { name = "dep-a" },\n  { name = "dep-win", marker = "sys_platform == \'win32\'" },\n]',
        'dependencies = [{ name = "dep-b" }]',
    )
    (project / "uv.lock").write_text(text, encoding="utf-8")
    original_lock = (project / "uv.lock").read_bytes()
    env = _environment(tmp_path)
    report = resolve_package_environment(env)
    environment_root = Path(str(report["root"]))
    old_site = environment_root / "site-packages"
    old_site.mkdir(parents=True)
    (old_site / "stale.py").write_text("VALUE = 0\n", encoding="utf-8")
    counter = tmp_path / "pcc1-count"
    pcc1 = tmp_path / "pcc1"
    _fake_pcc1(pcc1, counter)

    first = sync_uv_lock(
        project / "uv.lock",
        project_root=project,
        pcc1=str(pcc1),
        environ=env,
    )
    second = sync_uv_lock(
        project / "uv.lock",
        project_root=project,
        pcc1=str(pcc1),
        environ=env,
    )

    assert first["changed"] is True
    assert second["changed"] is False
    assert second["downloads"] == 0
    assert second["native_builds"] == 0
    assert first["packages"][0]["dependencies"] == []
    assert first["packages"][0]["artifact_path"].endswith("deps/dep-b")
    assert first["packages"][0]["artifact_url"] is None
    assert first["packages"][0]["downloaded"] is False
    assert counter.read_text(encoding="utf-8") == "1"
    assert not (old_site / "stale.py").exists()
    assert not (old_site / "build").exists()
    assert (old_site / "dep_b" / "__init__.py").is_file()
    assert (project / "uv.lock").read_bytes() == original_lock
    published = resolve_package_environment(env)
    assert published["lock_provenance"]["lock_sha256"] == first["lock_sha256"]
    inspected = json.loads(environment_info_json(env))
    assert inspected["lock_provenance"]["lock_sha256"] == first["lock_sha256"]
    assert not (Path(env["VIRTUAL_ENV"]) / "lib" / "python3.13").exists()


def test_locked_sync_reselects_python_markers_in_same_native_environment(tmp_path):
    project = _graph_project(tmp_path)
    lock_path = project / "uv.lock"
    lock_text = lock_path.read_text(encoding="utf-8")
    old_marker = "sys_platform == 'win32'"
    assert lock_text.count(old_marker) == 1
    lock_path.write_text(lock_text.replace(old_marker, "python_version >= '3.16'"))
    env = {**_environment(tmp_path), "PCC_PACKAGE_TARGET_PYTHON": "3.15"}
    pcc1 = tmp_path / "pcc1"
    _fake_pcc1(pcc1, tmp_path / "pcc1-count")

    first = sync_uv_lock(lock_path, project_root=project, pcc1=str(pcc1), environ=env)
    later_env = {**env, "PCC_PACKAGE_TARGET_PYTHON": "3.16"}
    second = sync_uv_lock(
        lock_path, project_root=project, pcc1=str(pcc1), environ=later_env
    )
    assert first["environment_root"] == second["environment_root"]
    assert "dep-win" not in {package["name"] for package in first["packages"]}
    assert second["changed"] is True, second
    assert "dep-win" in {package["name"] for package in second["packages"]}
    assert second["lock_provenance"]["target_python"] == "3.16"
    assert (
        Path(second["environment_root"]) / "site-packages/dep_win/__init__.py"
    ).is_file()
    repeated = sync_uv_lock(
        lock_path, project_root=project, pcc1=str(pcc1), environ=later_env
    )
    assert repeated["changed"] is False


def test_failed_locked_sync_preserves_previous_environment(tmp_path):
    project = _graph_project(tmp_path)
    env = _environment(tmp_path)
    environment_root = Path(str(resolve_package_environment(env)["root"]))
    site = environment_root / "site-packages"
    site.mkdir(parents=True)
    sentinel = site / "keep.py"
    sentinel.write_text("VALUE = 1\n", encoding="utf-8")
    pcc1 = tmp_path / "pcc1-fail"
    _fake_pcc1(pcc1, tmp_path / "failure-count", fail=True)

    with pytest.raises(UvLockSyncError) as failure:
        sync_uv_lock(
            project / "uv.lock",
            project_root=project,
            pcc1=str(pcc1),
            environ=env,
        )

    assert failure.value.code == "PCC-PKG-UVLOCK-INSTALL-FAILED"
    assert sentinel.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_build_isolation_failure_has_stable_diagnostic(tmp_path):
    project = _graph_project(tmp_path)
    env = _environment(tmp_path)
    pcc1 = tmp_path / "pcc1-build-isolation-fail"
    _fake_pcc1(
        pcc1,
        tmp_path / "build-isolation-count",
        fail=True,
        failure_diagnostic="PCC-PKG-ACQUIRE-BUILD-ISOLATION-UNSUPPORTED",
    )

    with pytest.raises(UvLockSyncError) as failure:
        sync_uv_lock(
            project / "uv.lock",
            project_root=project,
            pcc1=str(pcc1),
            environ=env,
        )

    assert failure.value.code == "PCC-PKG-UVLOCK-BUILD-ISOLATION-UNSUPPORTED"


def test_publish_failure_restores_complete_previous_environment(tmp_path, monkeypatch):
    project = _graph_project(tmp_path)
    env = _environment(tmp_path)
    environment_root = Path(str(resolve_package_environment(env)["root"]))
    site = environment_root / "site-packages"
    site.mkdir(parents=True)
    sentinel = site / "keep.py"
    sentinel.write_text("VALUE = 1\n", encoding="utf-8")
    old_manifest = environment_root / "installed.json"
    old_manifest.write_text('{"schema": "old"}\n', encoding="utf-8")
    pcc1 = tmp_path / "pcc1"
    _fake_pcc1(pcc1, tmp_path / "publish-failure-count")
    real_replace = uv_lock_sync.os.replace

    def fail_staging_publish(source, destination):
        source_path = Path(source)
        if (
            source_path.name.startswith(".pcc-sync-")
            and not source_path.name.endswith(".previous")
            and Path(destination) == environment_root
        ):
            raise OSError("forced atomic publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr(uv_lock_sync.os, "replace", fail_staging_publish)
    with pytest.raises(UvLockSyncError) as failure:
        sync_uv_lock(
            project / "uv.lock",
            project_root=project,
            pcc1=str(pcc1),
            environ=env,
        )

    assert failure.value.code == "PCC-PKG-UVLOCK-PUBLISH-FAILED"
    assert sentinel.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert old_manifest.read_text(encoding="utf-8") == '{"schema": "old"}\n'


# Trimmed from a real `uv lock` (uv 0.13.7, lock revision 5) of
# `dependencies = ["numpy==2.4.4"]` against PyPI: the sdist row and three of
# its 71 CPython-ABI wheel rows, unchanged.
PYPI_NUMPY_LOCK = """version = 1
revision = 5
requires-python = ">=3.11"

[[package]]
name = "numpy"
version = "2.4.4"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/packages/d7/9f/b8cef5bffa569759033adda9481211426f12f53299629b410340795c2514/numpy-2.4.4.tar.gz", hash = "sha256:2d390634c5182175533585cc89f3608a4682ccb173cc9bb940b2881c8d6f8fa0", size = 20731587, upload-time = "2026-03-29T13:22:01.298Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/cf/c5/9fcb7e0e69cef59cf10c746b84f7d58b08bc66a6b7d459783c5a4f6101a6/numpy-2.4.4-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl", hash = "sha256:df3775294accfdd75f32c74ae39fcba920c9a378a2fc18a12b6820aa8c1fb502", size = 16925137, upload-time = "2026-03-29T13:18:30.14Z" },
    { url = "https://files.pythonhosted.org/packages/e5/87/499737bfba066b4a3bebff24a8f1c5b2dee410b209bc6668c9be692580f0/numpy-2.4.4-cp313-cp313-macosx_14_0_arm64.whl", hash = "sha256:4a19d9dba1a76618dd86b164d608566f393f8ec6ac7c44f0cc879011c45e65af", size = 5199661, upload-time = "2026-03-29T13:19:28.31Z" },
    { url = "https://files.pythonhosted.org/packages/fa/22/4cc31a62a6c7b74a8730e31a4274c5dc80e005751e277a2ce38e675e4923/numpy-2.4.4-cp314-cp314-win_amd64.whl", hash = "sha256:715d1c092715954784bc79e1174fc2a90093dc4dc84ea15eb14dad8abdcdeb74", size = 12449181, upload-time = "2026-03-29T13:20:59.548Z" },
]

[[package]]
name = "numpy-pypi-app"
version = "0.0.0"
source = { virtual = "." }
dependencies = [
    { name = "numpy" },
]

[package.metadata]
requires-dist = [{ name = "numpy", specifier = "==2.4.4" }]
"""


def _project_lock(tmp_path: Path, text: str, name: str = "project") -> tuple[Path, dict[str, object]]:
    project = tmp_path / name
    project.mkdir(exist_ok=True)
    lock = project / "uv.lock"
    lock.write_text(text, encoding="utf-8")
    return project, project_uv_lock(lock, project_root=project, target_python="3.11",
                                    target_triple="x86_64-unknown-linux-gnu")


def test_pypi_numpy_lock_revision_5_selects_the_hashed_sdist(tmp_path):
    _project, projection = _project_lock(tmp_path, PYPI_NUMPY_LOCK)
    assert projection["uv_lock_revision"] == 5
    (row,) = projection["packages"]
    assert (row["name"], row["version"]) == ("numpy", "2.4.4")
    # Every wheel targets the CPython ABI; pcc-native builds from the sdist.
    assert row["artifact"] == {
        "kind": "source-artifact",
        "path": None,
        "url": "https://files.pythonhosted.org/packages/d7/9f/b8cef5bffa569759033adda9481211426f12f53299629b410340795c2514/numpy-2.4.4.tar.gz",
        "sha256": "2d390634c5182175533585cc89f3608a4682ccb173cc9bb940b2881c8d6f8fa0",
    }


@pytest.mark.parametrize("header,accepted", (
    ("version = 1\n", 0), ("version = 1\nrevision = 0\n", 0),
    ("version = 1\nrevision = 4\n", 4), ("version = 1\nrevision = 6\n", None),
))
def test_lock_revisions_follow_uv_definitions(tmp_path, header, accepted):
    text = header + PYPI_NUMPY_LOCK.split("\n", 2)[2]
    if accepted is None:
        with pytest.raises(UvLockSyncError) as error:
            _project_lock(tmp_path, text)
        assert error.value.code == "PCC-PKG-UVLOCK-UNSUPPORTED-SCHEMA"
        return
    _project, projection = _project_lock(tmp_path, text)
    assert projection["uv_lock_revision"] == accepted


def _demo_sdist(directory: Path, value: int) -> Path:
    import io
    import tarfile
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "demo_pkg-1.0.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, body in (("demo_pkg-1.0/pyproject.toml", '[project]\nname = "demo-pkg"\nversion = "1.0"\n'),
                           ("demo_pkg-1.0/demo_pkg/__init__.py", f"VALUE = {value}\n")):
            data = body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return archive


def _fake_archive_pcc1(path: Path, counter: Path) -> None:
    script = f"""#!{sys.executable}
import json, tarfile
from pathlib import Path
import sys
counter = Path({str(counter)!r})
counter.write_text(str(int(counter.read_text() if counter.exists() else "0") + 1))
args = sys.argv[1:]
source = Path(args[args.index("install") + 1])
target = Path(args[args.index("--target") + 1])
target.mkdir(parents=True, exist_ok=True)
with tarfile.open(source) as tar:
    for member in tar.getmembers():
        parts = member.name.split("/", 1)
        if member.isfile() and len(parts) == 2 and parts[1].startswith("demo_pkg/"):
            destination = target / parts[1]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(tar.extractfile(member).read())
print(json.dumps({{"ok": True, "installs": [{{"build_report": {{"skipped": True}}}}]}}))
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def _demo_lock(sdist_row: str) -> str:
    return f"""version = 1
revision = 5
requires-python = ">=3.11"

[[package]]
name = "demo-pkg"
version = "1.0"
source = {{ registry = "index" }}
sdist = {sdist_row}

[[package]]
name = "project"
version = "0.0.0"
source = {{ virtual = "." }}
dependencies = [{{ name = "demo-pkg" }}]
"""


def test_find_links_sdist_path_is_hashed_and_installed_from_a_verified_snapshot(tmp_path):
    import hashlib
    project = tmp_path / "project"
    archive = _demo_sdist(project / "index", 7)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    _project, projection = _project_lock(tmp_path, _demo_lock('{ path = "index/demo_pkg-1.0.tar.gz" }'))
    (row,) = projection["packages"]
    assert row["artifact"] == {"kind": "source-artifact", "path": str(archive.resolve()), "url": None,
                               "sha256": digest}
    counter, pcc1 = tmp_path / "count", tmp_path / "pcc1"
    _fake_archive_pcc1(pcc1, counter)
    env = _environment(tmp_path)
    report = sync_uv_lock(project / "uv.lock", project_root=project, pcc1=str(pcc1), environ=env)
    assert report["changed"] is True and report["downloads"] == 0
    assert counter.read_text() == "1"
    site = Path(str(resolve_package_environment(env)["root"])) / "site-packages"
    assert (site / "demo_pkg" / "__init__.py").read_text() == "VALUE = 7\n"
    # A recorded hash must match the local file.
    (project / "uv.lock").write_text(
        _demo_lock('{ path = "index/demo_pkg-1.0.tar.gz", hash = "sha256:' + "b" * 64 + '" }'), encoding="utf-8")
    with pytest.raises(UvLockSyncError) as mismatch:
        project_uv_lock(project / "uv.lock", project_root=project, target_python="3.11",
                        target_triple="x86_64-unknown-linux-gnu")
    assert mismatch.value.code == "PCC-PKG-UVLOCK-HASH-MISMATCH"


def test_registry_sdist_url_is_downloaded_once_with_its_locked_hash(tmp_path):
    import hashlib
    archive = _demo_sdist(tmp_path / "remote", 9)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    row = '{ url = "' + archive.resolve().as_uri() + '", hash = "sha256:' + digest + '" }'
    project, _projection = _project_lock(tmp_path, _demo_lock(row))
    counter, pcc1 = tmp_path / "count", tmp_path / "pcc1"
    _fake_archive_pcc1(pcc1, counter)
    env = _environment(tmp_path)
    first = sync_uv_lock(project / "uv.lock", project_root=project, pcc1=str(pcc1), environ=env)
    assert first["downloads"] == 1 and first["packages"][0]["downloaded"] is True
    second = sync_uv_lock(project / "uv.lock", project_root=project, pcc1=str(pcc1), environ=env)
    assert second["changed"] is False and second["downloads"] == 0
    bad = '{ url = "' + archive.resolve().as_uri() + '", hash = "sha256:' + "c" * 64 + '" }'
    (project / "uv.lock").write_text(_demo_lock(bad), encoding="utf-8")
    with pytest.raises(UvLockSyncError) as mismatch:
        sync_uv_lock(project / "uv.lock", project_root=project, pcc1=str(pcc1), environ=env)
    assert mismatch.value.code == "PCC-PKG-UVLOCK-HASH-MISMATCH"
