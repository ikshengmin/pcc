"""package_site_roots must not re-resolve the package environment per call.

The stage2 coordinator profile attributed 34% of its sampled window to
resolve_pcc_native_extension_path, whose every call re-ran
resolve_package_environment (13 env reads + environment.json open/read/parse)
via package_site_roots.  The roots are a pure function of the environment
fingerprint; the per-call filesystem PROBES (isdir/isfile/listdir) stay live
so freshly installed packages are still found within one process.
"""
from __future__ import annotations

import os

import pytest

from pcc.package import environment as package_environment
from pcc.frontends.python import pipeline_packages


def test_site_roots_resolve_once_per_environment(monkeypatch, tmp_path):
    calls = {"n": 0}
    real = pipeline_packages.environment_site_roots

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(pipeline_packages, "environment_site_roots", counting)
    pipeline_packages._SITE_ROOTS_CACHE.clear()

    first = pipeline_packages.package_site_roots()
    again = pipeline_packages.package_site_roots()
    assert again == first
    assert calls["n"] == 1

    # A changed environment fingerprint must re-resolve, not serve stale
    # roots (test isolation: monkeypatched sites between compiles).
    site = tmp_path / "fresh-site"
    site.mkdir()
    monkeypatch.setenv("PCC_PACKAGE_SITE", str(site))
    changed = pipeline_packages.package_site_roots()
    assert calls["n"] == 2
    assert str(site) in changed


def test_environment_fingerprint_tracks_inputs(monkeypatch):
    base = package_environment.package_environment_fingerprint()
    monkeypatch.setenv("PCC_PACKAGE_SITE", "/nonexistent/site-a")
    changed = package_environment.package_environment_fingerprint()
    assert changed != base
    monkeypatch.setenv("PCC_PACKAGE_SITE", "/nonexistent/site-b")
    assert package_environment.package_environment_fingerprint() != changed


def test_cached_roots_still_probe_the_filesystem(monkeypatch, tmp_path):
    # The isdir filter runs per call: a site dir created after the first
    # call becomes visible without an environment change... by design the
    # RESOLUTION is cached, existence filtering is not.
    site = tmp_path / "lazy-site"
    monkeypatch.setenv("PCC_PACKAGE_SITE", str(site))
    pipeline_packages._SITE_ROOTS_CACHE.clear()
    before = pipeline_packages.package_site_roots()
    assert str(site) not in before
    site.mkdir()
    after = pipeline_packages.package_site_roots()
    assert str(site) in after


# Windows forbids U+001F in directory names
@pytest.mark.pcc_gate(probe=lambda: os.name != "nt")
def test_resolver_switches_between_environments_with_the_same_flat_key(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").touch()
    monkeypatch.delenv("PCC_PACKAGE_SITE", raising=False)
    base = tmp_path / "env"
    first = tmp_path / "env\x1fblue"
    name = "boundary_demo"
    expected = {}
    for root in (first, base):
        path = root / "site-packages" / name / "__init__.py"
        path.parent.mkdir(parents=True)
        path.write_text("# installed package\n", encoding="utf-8")
        expected[root] = str(path)

    # (first, "tail") and (base, "blue\x1ftail") have the same flat join.
    # Each selected environment owns a distinct, existing source for name.
    for root, virtual in ((first, "tail"), (base, "blue\x1ftail"), (first, "tail")):
        monkeypatch.setenv("PCC_ENVIRONMENT", str(root))
        monkeypatch.setenv("VIRTUAL_ENV", virtual)
        assert package_environment.package_site_roots() == [os.path.join(root, "site-packages")]
        assert pipeline_packages.resolve_module_src_for_import(str(project), name) == expected[root]


def test_fingerprint_keeps_adjacent_fields_distinct(monkeypatch):
    # Include field-edge and repeated separators, framing punctuation,
    # supplementary code points, and empty values. No paths are opened.
    values = ("", "\x1f", "\x1f\x1f", "x", "x\x1f", "\x1fx", "2:x", "🦦", "🦦\x1f")
    seen = set()
    for left in values:
        monkeypatch.setenv("PCC_ENVIRONMENT", left)
        for right in values:
            monkeypatch.setenv("VIRTUAL_ENV", right)
            key = package_environment.package_environment_fingerprint()
            assert type(key) is str
            assert key not in seen, (left, right)
            seen.add(key)


def test_fingerprint_preserves_plain_keys_and_reads_each_field_on_every_call(monkeypatch):
    names = (
        "HOME", "PCC_PACKAGE_TARGET_PYTHON", "PCC_NATIVE_ABI_VERSION",
        "PCC_PACKAGE_ABI_MODE", "PCC_TARGET_TRIPLE", "PCC_TARGET_ARCH",
        "PCC_ENVIRONMENT", "VIRTUAL_ENV", "PCC_DATA_HOME", "XDG_DATA_HOME",
        "PCC_PACKAGE_SITE", "PCC_PACKAGE_CACHE", "XDG_CACHE_HOME",
    )
    reads = []

    class Observed(dict):
        def get(self, name):
            reads.append(name)
            return super().get(name)

    env = Observed(HOME="/🦦", VIRTUAL_ENV="plain")
    with monkeypatch.context() as patch:
        patch.setattr(package_environment.os, "environ", env)
        first = package_environment.package_environment_fingerprint()
        env["VIRTUAL_ENV"] = ""
        again = package_environment.package_environment_fingerprint()
    assert reads == list(names) * 2
    plain = [""] * len(names)
    plain[names.index("HOME")] = "/🦦"
    plain[names.index("VIRTUAL_ENV")] = "plain"
    assert first == "\x1f".join(plain)
    plain[names.index("VIRTUAL_ENV")] = ""
    assert again == "\x1f".join(plain)


def test_resolver_keeps_filesystem_probes_live_with_framed_keys(monkeypatch, tmp_path):
    env = tmp_path / "env"
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").touch()
    monkeypatch.delenv("PCC_PACKAGE_SITE", raising=False)
    monkeypatch.setenv("PCC_ENVIRONMENT", str(env))
    monkeypatch.setenv("VIRTUAL_ENV", "otherwise\x1fignored")
    mod = env / "site-packages" / "late_install.py"
    resolve = pipeline_packages.resolve_module_src_for_import
    assert resolve(str(project), "late_install") is None
    mod.parent.mkdir(parents=True)
    mod.write_text("# late install\n", encoding="utf-8")
    assert resolve(str(project), "late_install") == str(mod)
    mod.rename(mod.with_name("moved.py"))
    assert resolve(str(project), "late_install") is None
    assert resolve(str(project), "moved") == str(mod.with_name("moved.py"))
