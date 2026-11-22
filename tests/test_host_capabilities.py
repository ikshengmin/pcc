"""The collection-time host probes that pcc_gate reads."""

from __future__ import annotations

import subprocess

import pytest

from tests import host_capabilities

GCC_BANNER = (
    "g++ (GCC) 14.2.0\n"
    "Copyright (C) 2024 Free Software Foundation, Inc.\n"
    "This is free software; see the source for copying conditions.\n"
)
APPLE_CLANG_BANNER = (
    "Apple clang version 21.0.0 (clang-2100.4.1.1)\n"
    "Target: arm64-apple-darwin\n"
    "Thread model: posix\n"
)


@pytest.fixture
def fake_gxx(monkeypatch):
    def install(banner, *, path="/usr/bin/g++", returncode=0):
        host_capabilities.gnu_gxx.cache_clear()
        monkeypatch.setattr(host_capabilities.shutil, "which",
                            lambda name: path if name == "g++" else None)

        def run(command, **kwargs):
            assert command == [path, "--version"]
            return subprocess.CompletedProcess(command, returncode, banner, "")

        monkeypatch.setattr(host_capabilities.subprocess, "run", run)

    yield install
    host_capabilities.gnu_gxx.cache_clear()


def test_gnu_gxx_accepts_gcc(fake_gxx):
    fake_gxx(GCC_BANNER)
    assert host_capabilities.gnu_gxx() == "/usr/bin/g++"


def test_gnu_gxx_rejects_a_clang_shim(fake_gxx):
    fake_gxx(APPLE_CLANG_BANNER)
    assert host_capabilities.gnu_gxx() is None


def test_gnu_gxx_rejects_a_failed_version_query(fake_gxx):
    fake_gxx(GCC_BANNER, returncode=1)
    assert host_capabilities.gnu_gxx() is None


def test_gnu_gxx_without_gxx(fake_gxx):
    fake_gxx(GCC_BANNER, path=None)
    assert host_capabilities.gnu_gxx() is None
