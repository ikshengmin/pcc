"""Explicit fixture admission uses real archive/receipt bytes, never a rebuild.

The small ELF members exercise provenance, not executable runtime behavior.
The integration fixture is called directly here; no native compiler is run.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
from pathlib import Path

import pytest

from pcc.backend.ar_writer import write_archive
from pcc.backend.elf_x86_64 import (
    ElfObject,
    ElfSection,
    ElfSymbol,
    SHF_ALLOC,
    SHF_WRITE,
    SHT_PROGBITS,
    STB_GLOBAL,
    STT_OBJECT,
    emit_relocatable,
)
from pcc.frontends.python import owned_runtime_build, pipeline_targets
from pcc.tools import runtime_archive_provenance as provenance
from tests import conftest as shared_fixtures
from tests import runtime_fixture_provenance as admission
from tests.integration import test_threaded_path_ownership_qualification as path_suite
from tests.python import conftest as python_fixtures
from tests.python import _gc_substrate_common as substrate
from tests.python import test_gc_concurrent_collection as concurrent


pytest_plugins = ["pytester"]
TARGET = "x86_64-unknown-linux-gnu"
_ABSENT = object()


def _forbidden(*_args, **_kwargs):
    pytest.fail("explicit runtime fixture attempted automatic provisioning")


@pytest.fixture
def runtime_tree(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    root = source_root / "pcc/runtime"
    (root / "py").mkdir(parents=True)
    (root / "Makefile").write_text(
        "PY_MODULES = py_obj py_os_path\n"
        "FREESTANDING_PY_MODULES = freestanding_gc_root_operations\n",
        encoding="utf-8",
    )
    for relative in (
        "pcc/frontends/python/codegen/native_os.py",
        "pcc/runtime/include/py_runtime.h",
    ):
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// fixture identity\n", encoding="utf-8")
    monkeypatch.setattr(admission, "_RUNTIME_ROOT", root)
    monkeypatch.setattr(substrate, "RUNTIME_DIR", root)
    monkeypatch.setattr(substrate, "cached_pcc_python_runtime", _forbidden)
    monkeypatch.setattr(substrate, "cached_threaded_pcc_python_runtime", _forbidden)
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: TARGET)
    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    monkeypatch.setenv("PCC_REFCOUNT_KIND", "atomic")
    monkeypatch.setattr(owned_runtime_build, "build_runtime_archive", _forbidden)
    monkeypatch.setattr(owned_runtime_build, "ensure_target_runtime", _forbidden)
    monkeypatch.setattr(shared_fixtures, "require_native_provisioning_allowed", _forbidden)
    monkeypatch.setattr(shared_fixtures, "cached_pcc_python_runtime", _forbidden)
    monkeypatch.setattr(shared_fixtures, "cached_threaded_pcc_python_runtime", _forbidden)
    compiler = tmp_path / "pcc1"
    compiler.write_bytes(b"\x7fELFfixture-prefix-only")
    compiler.chmod(0o700)
    monkeypatch.setenv("PCC_PATH_OWNERSHIP_COMPILER", str(compiler))
    monkeypatch.setenv("PCC_PATH_OWNERSHIP_SOURCE_ROOT", str(source_root))
    return root


def _archive(root, *, threads, refcount="atomic", target=TARGET):
    """Construct a small inventory using the production archive/receipt APIs."""
    objects = []
    members = []
    symbols = []
    for name in owned_runtime_build.runtime_modules(str(root), target, threads):
        source = root / "py" / (name + ".py")
        source.write_text("# source for " + name + "\n", encoding="utf-8")
        ir = root / (name + ".ll")
        ir.write_text("; provenance-only fixture\n", encoding="utf-8")
        symbol = "PyFixture_" + name
        symbols.append(symbol)
        data = emit_relocatable(ElfObject(
            (ElfSection(".data", SHT_PROGBITS, SHF_ALLOC | SHF_WRITE, 8, data=bytes(8)),),
            (ElfSymbol.null(), ElfSymbol(symbol, 1, 0, 8, STB_GLOBAL, STT_OBJECT)),
        ))
        obj = root / (name + ".o")
        obj.write_bytes(data)
        provenance.write_pcc_python_receipt(
            object_path=obj,
            ir_path=ir,
            source_path=source,
            runtime_root=root,
            target_triple=target,
            object_emitter="pcc",
            runtime_build_config={"threads": threads, "refcount": refcount},
        )
        objects.append(obj)
        members.append((obj.name, data))
    archive = root / "libpy_runtime_pcc_py.a"
    archive.write_bytes(write_archive(members))
    Path(str(archive) + ".capi_syms").write_text(
        "\n".join(sorted(symbols)) + "\n", encoding="ascii",
    )
    provenance.assemble_runtime_archive_manifest(archive, objects, runtime_root=root)
    return archive


def _change_manifest(archive, change):
    path = Path(str(archive) + ".provenance.json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    change(manifest)
    # Keep the receipt digest coherent so semantic defects reach admission.
    manifest["members_sha256"] = provenance._members_sha256(manifest["members"])
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _select(route, archive, monkeypatch):
    if route == "default":
        monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))
    else:
        monkeypatch.setenv("PCC_THREADED_RUNTIME_ARCHIVE", str(archive))
    environment = dict(os.environ)
    try:
        if route == "default":
            return shared_fixtures.pcc_runtime_archive.__wrapped__(None)
        if route == "threaded":
            return shared_fixtures.threaded_pcc_runtime_archive.__wrapped__()
        if route == "concurrent":
            runtime, selected = concurrent._build_threaded_runtime(
                archive.parent, cc="must-not-run",
            )
            assert runtime == admission._RUNTIME_ROOT
            return selected
        if route == "substrate":
            runtime, selected = substrate._runtime_variant("pcc_python", threaded=True)
            assert runtime == archive.parent
            return selected
        fixture = path_suite.path_toolchain.__wrapped__()
        compiler, selected, source_root, records, identities = next(fixture)
        assert "freestanding_thread_kernel_pthread.o" in records
        assert str(compiler) in identities
        assert source_root / "pcc/runtime" == archive.parent
        with pytest.raises(StopIteration):
            next(fixture)
        return selected
    finally:
        assert dict(os.environ) == environment


@pytest.mark.parametrize("route", ["default", "threaded", "integration", "substrate", "concurrent"])
def test_explicit_routes_accept_current_complete_archive_without_provisioning(
    runtime_tree, monkeypatch, route,
):
    archive = _archive(runtime_tree, threads=True)
    assert _select(route, archive, monkeypatch) == archive



def test_concurrent_fixture_keeps_source_headers_separate_from_explicit_archive(
    runtime_tree, tmp_path, monkeypatch,
):
    archive = _archive(runtime_tree, threads=True)
    bundle = tmp_path / "native-bundle"
    bundle.mkdir()
    selected = bundle / archive.name
    for suffix in ("", ".provenance.json", ".capi_syms"):
        shutil.move(str(archive) + suffix, str(selected) + suffix)
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    assert _select("concurrent", selected, monkeypatch) == selected
    assert not (bundle / "include").exists()


@pytest.mark.parametrize("defect", [
    None, "missing-manifest", "changed-source", "wrong-target",
    "unthreaded", "local-refcount", "in-runtime",
])
def test_carrier_fixture_delegates_verified_parent_without_provisioning(
    runtime_tree, monkeypatch, pytester, defect,
):
    """Resolve the real overriding fixture, never collect its native consumers."""
    # Match the sibling-import path provided when pytest collects tests/python.
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "python"))
    carriers = importlib.import_module("tests.python.test_virtual_thread_pcc_py_carriers")

    archive = _archive(
        runtime_tree,
        threads=True,
        refcount="local" if defect == "local-refcount" else "atomic",
        target="aarch64-unknown-linux-gnu" if defect == "wrong-target" else TARGET,
    )
    bundle = pytester.path / "native-bundle"
    bundle.mkdir()
    selected = bundle / archive.name
    for suffix in ("", ".provenance.json", ".capi_syms"):
        shutil.move(str(archive) + suffix, str(selected) + suffix)
    if defect == "missing-manifest":
        Path(str(selected) + ".provenance.json").unlink()
    elif defect == "changed-source":
        (runtime_tree / "py/py_obj.py").write_text("# changed\n", encoding="utf-8")
    elif defect == "unthreaded":
        # A stale receipt, not a request to build the removed single-thread kernel.
        def change(manifest):
            for member in manifest["members"]:
                member["runtime_build_config"]["threads"] = False

        _change_manifest(selected, change)
    elif defect == "in-runtime":
        monkeypatch.setattr(carriers, "RUNTIME", bundle)

    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    monkeypatch.setenv("PCC_THREADED_RUNTIME_ARCHIVE", str(selected))
    builder_calls = []

    def forbidden_builder(*_args, **_kwargs):
        builder_calls.append(True)
        pytest.fail("carrier fixture attempted automatic provisioning")

    # Also catches the old module-local imported builder if it is reintroduced.
    monkeypatch.setattr(
        carriers, "cached_threaded_pcc_python_runtime", forbidden_builder,
        raising=False,
    )
    monkeypatch.setattr(shared_fixtures, "cached_threaded_pcc_python_runtime", forbidden_builder)
    monkeypatch.setattr(shared_fixtures, "require_native_provisioning_allowed", forbidden_builder)
    pytester.makeini("[pytest]\n")
    pytester.makeconftest(
        "from tests.conftest import threaded_pcc_runtime_archive\n"
    )
    body_marker = pytester.path / "fixture-consumer-ran"
    pytester.makepyfile(
        "from pathlib import Path\n"
        "from tests.python.test_virtual_thread_pcc_py_carriers import threaded_pcc_runtime_archive\n"
        "def test_selected_archive(threaded_pcc_runtime_archive):\n"
        f"    assert threaded_pcc_runtime_archive == Path({str(selected)!r})\n"
        f"    Path({str(body_marker)!r}).write_text('verified', encoding='utf-8')\n"
    )
    # The outer suite keeps its normal worker policy. This isolated in-process
    # pytest harness exercises fixture resolution only, without tool subprocesses.
    environment = {key: value for key, value in os.environ.items() if key.startswith("PCC_")}
    result = pytester.runpytest_inprocess("-x", "-q", "--confcutdir", str(pytester.path))
    assert {key: value for key, value in os.environ.items() if key.startswith("PCC_")} == environment
    assert not builder_calls
    if defect is None:
        result.assert_outcomes(passed=1)
        assert body_marker.read_text(encoding="utf-8") == "verified"
    else:
        result.assert_outcomes(errors=1)
        assert not body_marker.exists()
        expected = (
            "cannot read provenance JSON" if defect == "missing-manifest"
            else "AssertionError" if defect == "in-runtime"
            else "source does not match its receipt" if defect == "changed-source"
            else (
                f"does not match {TARGET}: "
                "{'threads': True, 'refcount': 'atomic'}"
            )
        )
        assert expected in result.stdout.str()


def test_default_fixture_honors_effective_thread_and_refcount_configuration(runtime_tree, monkeypatch):
    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    monkeypatch.setenv("PCC_REFCOUNT_KIND", "local")
    archive = _archive(runtime_tree, threads=True, refcount="local")
    assert _select("default", archive, monkeypatch) == archive


@pytest.mark.parametrize("route", ["default", "threaded", "integration", "substrate", "concurrent"])
@pytest.mark.parametrize("config", [
    pytest.param(_ABSENT, id="missing"),
    pytest.param(None, id="null"),
    pytest.param({"threads": False}, id="missing-refcount"),
    pytest.param({"threads": 1, "refcount": "atomic"}, id="integer-threads"),
    pytest.param({"threads": "1", "refcount": "atomic"}, id="string-threads"),
])
def test_all_routes_reject_unproven_member_configuration(runtime_tree, monkeypatch, route, config):
    archive = _archive(runtime_tree, threads=True)

    def change(manifest):
        member = manifest["members"][0]
        if config is _ABSENT:
            del member["runtime_build_config"]
        else:
            member["runtime_build_config"] = config

    _change_manifest(archive, change)
    with pytest.raises(ValueError, match="configuration|threads"):
        _select(route, archive, monkeypatch)


@pytest.mark.parametrize("route", ["default", "threaded", "integration", "substrate", "concurrent"])
@pytest.mark.parametrize("field", ["threads", "refcount"])
def test_all_routes_reject_mixed_member_configuration(runtime_tree, monkeypatch, route, field):
    threaded = True
    archive = _archive(runtime_tree, threads=threaded)

    def change(manifest):
        manifest["members"][0]["runtime_build_config"][field] = (
            not threaded if field == "threads" else "local"
        )

    _change_manifest(archive, change)
    with pytest.raises(ValueError, match="build configuration"):
        _select(route, archive, monkeypatch)


@pytest.mark.parametrize("route", ["default", "threaded", "integration", "substrate", "concurrent"])
@pytest.mark.parametrize("checksum", [pytest.param(_ABSENT, id="missing"), None, "unknown", "0" * 64])
def test_all_routes_reject_missing_or_stale_codegen(runtime_tree, monkeypatch, route, checksum):
    archive = _archive(runtime_tree, threads=True)

    def change(manifest):
        member = manifest["members"][0]
        if checksum is _ABSENT:
            del member["codegen_checksum"]
        else:
            member["codegen_checksum"] = checksum

    _change_manifest(archive, change)
    with pytest.raises(ValueError, match="current codegen"):
        _select(route, archive, monkeypatch)


@pytest.mark.parametrize("defect", [
    "wrong-target", "duplicate-member", "missing-member", "unexpected-member",
    "host-owner", "foreign-emitter", "foreign-producer", "foreign-source", "wrong-policy",
    "changed-source", "changed-object", "changed-archive", "changed-capi",
    "missing-manifest", "unknown-current-codegen",
])
def test_integration_route_rejects_invalid_runtime_provenance(runtime_tree, monkeypatch, defect):
    archive = _archive(runtime_tree, threads=True)

    def change(manifest):
        member = manifest["members"][0]
        if defect == "wrong-target":
            manifest["target_triple"] = "aarch64-unknown-linux-gnu"
            for row in manifest["members"]:
                row["target_triple"] = manifest["target_triple"]
        elif defect == "duplicate-member":
            manifest["members"][-1] = dict(member)
        elif defect == "host-owner":
            member["uses_host_cc"] = True
        elif defect == "foreign-emitter":
            member["object_emitter"] = "llvmlite"
        elif defect == "foreign-producer":
            member["producer_kind"] = "host-cc"
        elif defect == "foreign-source":
            member["source_kind"] = "c"
        elif defect == "wrong-policy":
            manifest["policy"] = "diagnostic-only"
        elif defect == "changed-object":
            member["object_sha256"] = "0" * 64

    _change_manifest(archive, change)
    if defect == "missing-member":
        with (runtime_tree / "Makefile").open("a", encoding="utf-8") as stream:
            stream.write("PY_MODULES += newly_required_member\n")
    elif defect == "unexpected-member":
        text = (runtime_tree / "Makefile").read_text(encoding="utf-8")
        (runtime_tree / "Makefile").write_text(text.replace("py_obj ", ""), encoding="utf-8")
    elif defect == "changed-source":
        (runtime_tree / "py/py_obj.py").write_text("# source changed\n", encoding="utf-8")
    elif defect == "changed-archive":
        payload = bytearray(archive.read_bytes())
        payload[payload.index(b"\x7fELF") + 5] ^= 1
        archive.write_bytes(payload)
    elif defect == "changed-capi":
        Path(str(archive) + ".capi_syms").write_text("PyOther\n", encoding="ascii")
    elif defect == "missing-manifest":
        Path(str(archive) + ".provenance.json").unlink()
    elif defect == "unknown-current-codegen":
        monkeypatch.setattr(provenance, "codegen_checksum", lambda: "unknown")
    with pytest.raises((ValueError, FileNotFoundError)):
        _select("integration", archive, monkeypatch)


@pytest.mark.parametrize("magic", [b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf", b"MZ\x90\x00"])
def test_native_compiler_fixture_recognizes_image_prefixes(tmp_path, monkeypatch, magic):
    from tests.python import pcc1_gate

    compiler = tmp_path / "pcc1"
    compiler.write_bytes(magic + b"prefix-check-only")
    monkeypatch.setattr(pcc1_gate, "find_current_pcc1", lambda _root: compiler)
    assert python_fixtures.native_pcc1_compiler.__wrapped__() == compiler


@pytest.mark.parametrize("payload", [b"#!/bin/sh\n", b"python wrapper", b"", b"M"])
def test_native_compiler_fixture_rejects_launchers_and_truncated_prefixes(tmp_path, monkeypatch, payload):
    from tests.python import pcc1_gate

    compiler = tmp_path / "pcc1"
    compiler.write_bytes(payload)
    monkeypatch.setattr(pcc1_gate, "find_current_pcc1", lambda _root: compiler)
    with pytest.raises(AssertionError, match="native executable"):
        python_fixtures.native_pcc1_compiler.__wrapped__()
