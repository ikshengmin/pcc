"""The explicit C-extension linker oracle selects a compatible frontend route."""

import importlib.util
import os
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline_modes


@pytest.mark.parametrize("inherited", (None, "0", "1"))
def test_cext_oracle_environment_selects_text_without_changing_host(
    monkeypatch, tmp_path, inherited,
):
    source = Path(__file__).with_name("test_cext_inplace_number_dispatch.py")
    spec = importlib.util.spec_from_file_location("_cext_oracle_fixture", source)
    assert spec is not None and spec.loader is not None
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    selectors = (
        "PCC_DIRECT_INDEXED_KERNEL_CAPTURE",
        "PCC_DIRECT_INDEXED_KERNEL_EMIT",
    )
    for name in selectors:
        if inherited is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, inherited)
    monkeypatch.setenv("PCC_SELF_LINK", "pcc")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    original = dict(os.environ)
    site, archive = tmp_path / "site", tmp_path / "runtime.a"
    environment = fixture._cext_oracle_environment(site, archive)
    assert dict(os.environ) == original
    assert environment["PCC_SELF_LINK"] == "cc"
    assert environment["PCC_RUNTIME_CC"] == "pcc"
    assert environment["PCC_PACKAGE_SITE"] == str(site)
    assert environment["PCC_RUNTIME_ARCHIVE"] == str(archive)
    assert all(environment[name] == "0" for name in selectors)
    selected_environment = dict(environment)
    with monkeypatch.context() as target:
        target.setattr(pipeline_modes.os, "environ", environment)
        chosen = pipeline_modes.select_direct_indexed_route(
            backend="self", emit_llvm_only=False, python_library=False,
            target_triple=None, gpu_backend="none", libpython_mode="off",
        )
        assert chosen == []
        pipeline_modes.restore_direct_indexed_route(chosen)
    assert environment == selected_environment
    assert dict(os.environ) == original
