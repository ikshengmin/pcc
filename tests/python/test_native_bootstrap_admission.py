"""Host rejection and native path-resolution models, not binary attestation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pcc.driver import cli_bootstrap
from tests.python.process_timeout import run_process_group_timeout


def _meson_source(root: Path) -> Path:
    root.mkdir()
    (root / "meson.build").write_text("project('demo', 'c')\n", encoding="utf-8")
    entry = root / "vendored-meson" / "meson" / "meson.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("raise AssertionError('must not run Meson')\n", encoding="utf-8")
    return root


def _assert_host_rejected(report):
    assert report["ok"] is False
    assert report["build_compiler"] is None
    assert report["diagnostics"] == ["PCC-PKG-OWNED-BUILD-COMPILER-REQUIRED"]
    assert report["reason"] == "owned_build_compiler_required"
    assert report["actions"] == []
    assert report["host_free_build_claim"] is False


@pytest.mark.parametrize("name", ["pcc", "pcc1", "pcc.py", "__main__.py"])
def test_host_console_and_module_are_not_native_compilers(tmp_path, monkeypatch, name):
    # Leave the real execution-owner identity intact: these are host negatives.
    assert sys.implementation.name == "cpython"
    launcher = tmp_path / name
    launcher.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    calls = []

    def unexpected_process(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("host entry must not start an owned build process")

    monkeypatch.setattr(cli_bootstrap, "_bootstrap_subprocess_run", unexpected_process)
    monkeypatch.setattr(cli_bootstrap.subprocess, "run", unexpected_process)
    assert cli_bootstrap._native_bootstrap_executable() == ""
    project = _meson_source(tmp_path / "demo")
    report = json.loads(cli_bootstrap._native_owned_meson_build_json("demo", str(project)))
    _assert_host_rejected(report)
    assert calls == []
    assert not (project / "build").exists()


def test_host_identity_rejects_before_path_search(tmp_path, monkeypatch):
    assert sys.implementation.name == "cpython"
    launcher = tmp_path / "pcc"
    launcher.write_text("host console script\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.setattr(sys, "argv", ["pcc"])
    monkeypatch.setenv("PATH", str(tmp_path))

    def unexpected_path_search():
        raise AssertionError("host ownership must be rejected before PATH resolution")

    monkeypatch.setattr(cli_bootstrap, "_native_path_search_dirs", unexpected_path_search)
    assert cli_bootstrap._native_bootstrap_executable() == ""


@pytest.mark.parametrize("kind", ["absolute", "relative", "path", "missing", "script", "other", "empty"])
def test_native_identity_model_retains_path_resolution(tmp_path, monkeypatch, kind):
    # This substitutes only the runtime identity/argv projection. It does not
    # execute native pcc1 or establish source/binary/owner provenance.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    name = {"script": "pcc.py", "other": "python"}.get(kind, "pcc1")
    compiler = bindir / name
    if kind != "missing":
        compiler.write_text("path-resolution model, not a native image\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(bindir))
    argv0 = str(compiler)
    if kind == "relative":
        argv0 = "bin/" + name
    elif kind == "path":
        argv0 = name
    argv = [] if kind == "empty" else [argv0]
    monkeypatch.setattr(cli_bootstrap, "sys", SimpleNamespace(
        implementation=SimpleNamespace(name="pcc"), argv=argv, platform=sys.platform,
    ))
    expected = str(compiler) if kind in ("absolute", "relative", "path") else ""
    assert cli_bootstrap._native_bootstrap_executable() == expected


@pytest.mark.parametrize("entry", ["console", "module"])
def test_public_host_entry_rejects_owned_meson_before_child_process(tmp_path, entry):
    assert sys.implementation.name == "cpython"
    root = Path(__file__).resolve().parents[2]
    project = _meson_source(tmp_path / "demo")
    target = tmp_path / "site"
    cache = tmp_path / "cache"
    probe = tmp_path / "probe"
    probe.mkdir()
    audit_log = tmp_path / "process-events.jsonl"
    startup = tmp_path / "startup.json"
    (probe / "sitecustomize.py").write_text(
        "import json, os, sys\n"
        "with open(os.environ['PCC_ADMISSION_STARTUP'], 'w') as stream:\n"
        "    json.dump({'implementation': sys.implementation.name}, stream)\n"
        "def reject_process(event, args):\n"
        "    if event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn', 'os.fork', 'os.forkpty', 'os.spawn'):\n"
        "        with open(os.environ['PCC_ADMISSION_AUDIT'], 'a') as stream:\n"
        "            stream.write(json.dumps({'event': event}) + '\\n')\n"
        "        raise RuntimeError('owned host entry attempted a child process')\n"
        "sys.addaudithook(reject_process)\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    # This regression deliberately exercises host entrypoints, not the
    # separately selected native-test compiler route.
    environment.pop("PCC_TEST_COMPILER", None)
    environment.update(
        PYTHONPATH=os.pathsep.join((str(probe), str(root))),
        PCC_ADMISSION_STARTUP=str(startup),
        PCC_ADMISSION_AUDIT=str(audit_log),
        PCC_TEST_NO_NATIVE_PROVISIONING="1",
        PCC_NO_AUTO_PCC1="1",
    )
    if entry == "console":
        launcher = tmp_path / "pcc"
        launcher.write_text(
            "from pcc.driver.cli_launcher import main\n"
            "raise SystemExit(main())\n",
            encoding="utf-8",
        )
        command = [sys.executable, str(launcher)]
    else:
        command = [sys.executable, "-m", "pcc"]
    result = run_process_group_timeout(
        command + ["-m", "pcc.package.install", str(project), "--target", str(target),
                   "--cache-dir", str(cache), "--no-index", "--build=owned", "--json"],
        cwd=root, env=environment, timeout=20,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert json.loads(startup.read_text(encoding="utf-8")) == {"implementation": "cpython"}
    assert not audit_log.exists(), audit_log.read_text(encoding="utf-8")
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert report["install_success"] is False
    assert report["native_package_claim"] is False
    _assert_host_rejected(report["build_report"])
    assert not target.exists()
    assert not cache.exists()
    assert not (project / "build").exists()


def test_public_host_console_installs_pure_package_and_reuses_cache(tmp_path):
    """Real host console success; file-copy helpers are not a native build."""
    assert sys.implementation.name == "cpython"
    root = Path(__file__).resolve().parents[2]
    project = tmp_path / "demo"
    payload = project / "demo"
    payload.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1'\n", encoding="utf-8"
    )
    package_text = "def answer():\n    return 42\n"
    (payload / "__init__.py").write_text(package_text, encoding="utf-8")
    cache = tmp_path / "cache"
    sites = [tmp_path / "site", tmp_path / "cached-site"]
    commands = []
    for index, site in enumerate(sites):
        source = payload if index == 0 else cache / "demo"
        expected = [
            ["mkdir", "-p", str(site)],
            ["rm", "-rf", str(site / "demo")],
            ["cp", "-R", str(source), str(site)],
        ]
        if index == 0:
            expected.extend([
                ["mkdir", "-p", str(cache)],
                ["rm", "-rf", str(cache / "demo")],
                ["cp", "-R", str(payload), str(cache)],
            ])
        expected.append(["mkdir", "-p", str(cache / "demo")])
        commands.append(expected)
    allowed_path = tmp_path / "allowed-commands.json"
    allowed_path.write_text(json.dumps(commands[0] + commands[1]), encoding="utf-8")
    probe = tmp_path / "probe"
    probe.mkdir()
    audit_log = tmp_path / "process-events.jsonl"
    entry_log = tmp_path / "entry.json"
    (probe / "sitecustomize.py").write_text(
        "import json, os, sys\n"
        "with open(os.environ['PCC_ADMISSION_COMMANDS']) as stream:\n"
        "    allowed = json.load(stream)\n"
        "def audit_process(event, args):\n"
        "    if event not in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn', 'os.fork', 'os.forkpty', 'os.spawn'):\n"
        "        return\n"
        "    command = list(args[1]) if event in ('subprocess.Popen', 'os.posix_spawn') else []\n"
        "    accepted = command in allowed\n"
        "    with open(os.environ['PCC_ADMISSION_AUDIT'], 'a') as stream:\n"
        "        stream.write(json.dumps({'event': event, 'command': command, 'accepted': accepted}) + '\\n')\n"
        "    if not accepted:\n"
        "        raise RuntimeError('unexpected process in pure-package host install')\n"
        "sys.addaudithook(audit_process)\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "pcc"
    launcher.write_text(
        "import json, os, sys\n"
        "from pcc.driver.cli_launcher import main\n"
        "with open(os.environ['PCC_ADMISSION_ENTRY'], 'w') as stream:\n"
        "    json.dump({'implementation': sys.implementation.name, 'argv0': sys.argv[0], 'entry_owner': main.__module__}, stream)\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("PCC_TEST_COMPILER", None)
    environment.update(
        PYTHONPATH=os.pathsep.join((str(probe), str(root))),
        PCC_ADMISSION_COMMANDS=str(allowed_path),
        PCC_ADMISSION_AUDIT=str(audit_log),
        PCC_ADMISSION_ENTRY=str(entry_log),
        PCC_TEST_NO_NATIVE_PROVISIONING="1",
        PCC_NO_AUTO_PCC1="1",
    )
    for index, site in enumerate(sites):
        spec = str(project) if index == 0 else "demo"
        result = run_process_group_timeout(
            [sys.executable, str(launcher), "-m", "pcc.package.install", spec,
             "--target", str(site), "--cache-dir", str(cache), "--no-index",
             "--build=owned", "--json"],
            cwd=root, env=environment, timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(entry_log.read_text(encoding="utf-8")) == {
            "implementation": "cpython", "argv0": str(launcher),
            "entry_owner": "pcc.driver.cli_launcher",
        }
        report = json.loads(result.stdout)
        assert report["ok"] is True
        assert report["install_success"] is True
        assert report["resolved_from"] == ("direct" if index == 0 else "cache")
        assert (site / "demo" / "__init__.py").read_text(encoding="utf-8") == package_text
        assert (cache / "demo" / "__init__.py").read_text(encoding="utf-8") == package_text
        manifest = json.loads(Path(report["manifest_path"]).read_text(encoding="utf-8"))
        for record in (report, manifest):
            assert record["native_package_claim"] is False
            assert record["install_native_package_claim"] is False
            assert record["linkage_native_package_claim"] is False
            assert record["build_report"]["skipped"] is True
            assert record["build_report"]["build_ownership"] == "not-required"
            assert record["build_report"]["actions"] == []
            assert record["build_report"].get("build_compiler") is None
    events = [json.loads(line) for line in audit_log.read_text(encoding="utf-8").splitlines()]
    assert all(event["accepted"] for event in events), events
    assert [event["command"] for event in events if event["event"] == "subprocess.Popen"] == commands[0] + commands[1]
    assert not (project / "build").exists()
