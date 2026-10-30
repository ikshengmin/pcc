"""Linux host-table plumbing, not public import or extension execution evidence.

Small owned ELF objects and real provenance bundles exercise the file owner,
archive roots, relocations and publication. Fixture object bytes are constructed,
not compiled from the receipt source: no runtime/bootstrap/compiler is built.
"""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from unittest.mock import Mock

import pytest

from pcc.backend import elf_x86_64 as elf
from pcc.backend import owned_elf_link
from pcc.backend.elf_host_exports import HOST_EXPORT_SYMBOL
from pcc.frontends.python import pipeline
from pcc.frontends.python import pipeline_self_backend_link as owner
from pcc.frontends.python import pipeline_targets
from pcc.tools import runtime_archive_provenance as provenance
from tests import test_elf_host_export_link as formats


TARGET = "x86_64-unknown-linux-gnu"
DARWIN = "aarch64-apple-darwin"
EXPORTS = ("PyFixture_Api", "PyFixture_Counter")


def _forbidden(*args, **kwargs):
    raise AssertionError("unexpected host tool, runtime build or fallback")


@pytest.fixture
def owned(monkeypatch):
    monkeypatch.setattr(pipeline, "_resolve_self_link_mode", lambda **kwargs: "pcc")
    monkeypatch.setattr(pipeline, "_macho_semantic_layout_enabled", lambda: False)
    monkeypatch.setattr(pipeline, "_host_python_command", _forbidden)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "check_output", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.delenv("PCC_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("PCC_DEFER_SELF_LINK_PLAN", raising=False)


def _files(tmp_path, monkeypatch, *, target=TARGET):
    root = tmp_path / "runtime"
    (root / "py").mkdir(parents=True)
    api = formats._function(EXPORTS[0], b"\xe8\0\0\0\0\xc3")
    api = replace(api, symbols=api.symbols + (
        elf.ElfSymbol("private_dependency", elf.SHN_UNDEF, 0, 0, elf.STB_GLOBAL, elf.STT_FUNC),
    ), sections=(replace(api.sections[0], relocations=(
        elf.ElfRelocation(1, 2, elf.R_X86_64_PLT32, -4),
    )),))
    counter = elf.ElfObject((elf.ElfSection(
        ".data", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE, 4, struct.pack("<I", 42),
    ),), (elf.ElfSymbol.null(), elf.ElfSymbol(
        EXPORTS[1], 1, 0, 4, elf.STB_GLOBAL, elf.STT_OBJECT,
    )))
    members = [("api.o", api), ("counter.o", counter),
               ("dependency.o", formats._function("private_dependency", b"\xb8\x2a\0\0\0\xc3")),
               ("unused.o", formats._function("unused_runtime_member"))]
    objects = []
    for name, obj in members:
        source = root / "py" / (name[:-2] + ".py")
        source.write_text("# constructed ELF provenance fixture: " + name + "\n", encoding="utf-8")
        ir = root / (name + ".ll")
        ir.write_text("; constructed provenance fixture\n", encoding="utf-8")
        path = root / name
        path.write_bytes(elf.emit_relocatable(obj))
        provenance.write_pcc_python_receipt(
            object_path=path, ir_path=ir, source_path=source, runtime_root=root,
            target_triple=target, object_emitter="pcc",
        )
        objects.append(path)
    archive = root / "arbitrary-runtime-name.a"
    archive.write_bytes(formats._archive(members))
    Path(str(archive) + ".capi_syms").write_text("\n".join(EXPORTS) + "\n", encoding="ascii")
    provenance.assemble_runtime_archive_manifest(archive, objects, runtime_root=root)
    entry = tmp_path / "entry.o"
    entry.write_bytes(elf.emit_relocatable(formats._entry()))
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_DIR", str(root))
    return entry, archive, tmp_path / "program", root


def _link(entry, archive, output, **kwargs):
    options = dict(target_triple=TARGET, needs_native_extension_exports=True,
                   pcc_native_object_inputs=(str(entry),))
    options.update(kwargs)
    pipeline._run_self_link_command(
        ["reference-must-not-run"], None, str(output),
        None if archive is None else str(archive), (), False, **options,
    )


@pytest.mark.parametrize("target,table,imports", [
    (TARGET, True, False), ("amd64-linux-musl", True, False),
    (DARWIN, True, True), ("arm64-apple-macosx14.0", True, True),
    ("aarch64-unknown-linux-gnu", False, False),
    ("x86_64-pc-windows-gnu", False, False),
    ("x86_64-linux-windows-gnu", False, False),
    ("x86_64-unknown-freebsd", False, False), ("not-a-target", False, False),
])
def test_table_capability_does_not_enable_public_imports(target, table, imports):
    assert owner.native_extension_export_table_supported(target) is table
    assert owner.native_extension_exports_supported(target) is imports


@pytest.mark.parametrize("host,requested,table_only,admitted", [
    (DARWIN, TARGET, False, False), (TARGET, DARWIN, False, True),
    (DARWIN, TARGET, True, True), (TARGET, DARWIN, True, True),
    (DARWIN, "aarch64-unknown-linux-gnu", True, False),
    (TARGET, "x86_64-pc-windows-msvc", True, False),
])
def test_surface_uses_effective_target(owned, monkeypatch, host, requested, table_only, admitted):
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: host)
    options = dict(needs_native_extension_exports=True, target_triple=requested,
                   export_table_only=table_only)
    if admitted:
        pipeline._validate_pcc_self_link_surface(**options)
    else:
        with pytest.raises(pipeline.PyPipelineError, match="native-extension"):
            pipeline._validate_pcc_self_link_surface(**options)


@pytest.mark.parametrize("timed", [False, True])
def test_pipeline_publishes_real_verified_elf_exports(owned, tmp_path, monkeypatch, timed):
    entry, archive, output, root = _files(tmp_path, monkeypatch)
    # The cross-host selector is intentional: the requested ELF target wins.
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: DARWIN)
    verify = Mock(wraps=provenance.verify_runtime_archive_manifest)
    link = Mock(wraps=owned_elf_link.link_inputs)
    monkeypatch.setattr(provenance, "verify_runtime_archive_manifest", verify)
    monkeypatch.setattr(owned_elf_link, "link_inputs", link)
    timing = Mock() if timed else None
    _link(entry, archive, output, coordinator_timing=timing)
    assert verify.call_count == 2
    assert all(call.kwargs["runtime_root"] == root for call in verify.call_args_list)
    expected_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert all(call.kwargs["expected_archive_sha256"] == expected_hash
               for call in verify.call_args_list)
    assert link.call_args.kwargs["target"] == TARGET
    assert link.call_args.kwargs["export_symbols"] == EXPORTS
    assert link.call_args.kwargs["archives"] == [str(archive)]
    assert link.call_args.kwargs["expected_archive_identity"] == (
        str(archive), expected_hash,
    )
    assert link.call_args.kwargs["coordinator_timing"] is timing
    image = output.read_bytes()
    rows = formats._table(image)
    assert tuple(name for name, _ in rows) == EXPORTS
    addresses = dict(rows)
    assert struct.unpack_from("<I", image, formats._file_offset(image, addresses[EXPORTS[1]], 4))[0] == 42
    offset = formats._file_offset(image, addresses[EXPORTS[0]], 6)
    displacement = struct.unpack_from("<i", image, offset + 1)[0]
    dependency = addresses[EXPORTS[0]] + 5 + displacement
    assert image[formats._file_offset(image, dependency, 6):][:6] == b"\xb8\x2a\0\0\0\xc3"
    assert elf.parse_static_executable(image)["entry"]
    assert all(row[0] in (elf.PT_LOAD, elf.PT_TLS) for row in formats._segments(image))
    assert output.stat().st_mode & 0o111
    assert not Path(str(output) + ".pcc-link.tmp").exists()


@pytest.mark.parametrize("defect", ["missing", "empty", "noncanonical", "inventory", "archive",
                                    "source", "target", "codegen", "source_root"])
def test_linux_inventory_rejects_unverified_identity_before_link(owned, tmp_path, monkeypatch, defect):
    entry, archive, output, root = _files(tmp_path, monkeypatch)
    sidecar = Path(str(archive) + ".capi_syms")
    if defect == "missing":
        sidecar.unlink()
    elif defect == "empty":
        sidecar.write_text("", encoding="ascii")
    elif defect == "noncanonical":
        sidecar.write_text(" " + "\n".join(EXPORTS) + "\n", encoding="ascii")
    elif defect == "inventory":
        sidecar.write_text("PyForeign\n", encoding="ascii")
    elif defect == "archive":
        data = bytearray(archive.read_bytes())
        data[-8] ^= 1
        archive.write_bytes(data)
    elif defect == "source":
        (root / "py/api.py").write_text("# changed source\n", encoding="utf-8")
    elif defect in ("target", "codegen"):
        path = Path(str(archive) + ".provenance.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for member in manifest["members"]:
            member["target_triple" if defect == "target" else "codegen_checksum"] = (
                "x86_64-linux-musl" if defect == "target" else "0" * 64
            )
        if defect == "target":
            manifest["target_triple"] = "x86_64-linux-musl"
        manifest["members_sha256"] = provenance._members_sha256(manifest["members"])
        path.write_text(json.dumps(manifest), encoding="utf-8")
        # These bundles are internally sound; only current target/codegen admission rejects them.
        provenance.verify_runtime_archive_manifest(archive, runtime_root=root)
    else:
        monkeypatch.setenv("PCC_RUNTIME_DIR", str(tmp_path / "another-runtime"))
    monkeypatch.setattr(owned_elf_link, "link_inputs", _forbidden)
    with pytest.raises(pipeline.PyPipelineError, match="inventory"):
        _link(entry, archive, output)
    assert not output.exists()


def test_inventory_mutation_between_deep_checks_is_rejected(owned, tmp_path, monkeypatch):
    entry, archive, output, root = _files(tmp_path, monkeypatch)
    verify = provenance.verify_runtime_archive_manifest
    calls = []

    def mutate(*args, **kwargs):
        receipt = verify(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            (root / "py/api.py").write_text("# changed during inventory read\n", encoding="utf-8")
        return receipt

    monkeypatch.setattr(provenance, "verify_runtime_archive_manifest", mutate)
    monkeypatch.setattr(owned_elf_link, "link_inputs", _forbidden)
    with pytest.raises(pipeline.PyPipelineError, match="changed during link"):
        _link(entry, archive, output)
    assert not output.exists()


def test_transient_sidecar_names_cannot_bypass_verified_inventory(owned, tmp_path, monkeypatch):
    entry, archive, output, root = _files(tmp_path, monkeypatch)
    sidecar = Path(str(archive) + ".capi_syms")
    original = sidecar.read_bytes()
    verify = provenance.verify_runtime_archive_manifest
    calls = []

    def transient_inventory(*args, **kwargs):
        if calls:
            sidecar.write_bytes(original)
        receipt = verify(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            # A bool-before/read/bool-after reader would export this arbitrary
            # defined private symbol even though neither verifier saw it.
            sidecar.write_text("private_dependency\n", encoding="ascii")
        return receipt

    monkeypatch.setattr(provenance, "verify_runtime_archive_manifest", transient_inventory)
    _link(entry, archive, output)
    assert calls == [True, True]
    assert tuple(name for name, _ in formats._table(output.read_bytes())) == EXPORTS
    assert sidecar.read_bytes() == original


def test_changed_but_individually_valid_inventory_is_rejected(owned, tmp_path, monkeypatch):
    entry, archive, output, root = _files(tmp_path, monkeypatch)
    verify = provenance.verify_runtime_archive_manifest
    calls = []

    def change_inventory(*args, **kwargs):
        receipt = verify(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            sidecar = Path(str(archive) + ".capi_syms")
            new_inventory = EXPORTS[0] + "\n"
            sidecar.write_text(new_inventory, encoding="ascii")
            path = Path(str(archive) + ".provenance.json")
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["capi_symbols"] = [EXPORTS[0]]
            manifest["capi_symbol_count"] = 1
            manifest["capi_inventory_sha256"] = hashlib.sha256(new_inventory.encode("ascii")).hexdigest()
            path.write_text(json.dumps(manifest), encoding="utf-8")
        return receipt

    monkeypatch.setattr(provenance, "verify_runtime_archive_manifest", change_inventory)
    monkeypatch.setattr(owned_elf_link, "link_inputs", _forbidden)
    with pytest.raises(pipeline.PyPipelineError, match="C-API inventory changed during link"):
        _link(entry, archive, output)
    assert calls == [True, True]
    assert not output.exists()


@pytest.mark.parametrize("timed", [False, True])
def test_archive_mutation_after_inventory_verification_preserves_output(owned, tmp_path, monkeypatch, timed):
    entry, archive, output, root = _files(tmp_path, monkeypatch)
    output.write_bytes(b"existing output must remain intact")
    reader = pipeline._verified_capi_export_anchor_symbols

    def replace_archive(path, **kwargs):
        symbols = reader(path, **kwargs)
        original = archive.read_bytes()
        changed = original.replace(b"\xb8\x2a\0\0\0\xc3", b"\xb8\x63\0\0\0\xc3")
        assert changed != original
        archive.write_bytes(changed)
        return symbols

    monkeypatch.setattr(pipeline, "_verified_capi_export_anchor_symbols", replace_archive)
    with pytest.raises(elf.ElfError, match="verified ELF archive identity changed before staging"):
        _link(entry, archive, output, coordinator_timing=Mock() if timed else None)
    assert output.read_bytes() == b"existing output must remain intact"
    assert not Path(str(output) + ".pcc-link.tmp").exists()


@pytest.mark.parametrize("identity", [
    None, [], "", False,
    ("missing.a", "0" * 64), ("runtime.a", "wrong"), ("runtime.a", "G" * 64),
    ("runtime.a", "0" * 64, "extra"), ["runtime.a", "0" * 64],
])
def test_owned_file_owner_rejects_malformed_expected_archive_identity(tmp_path, identity):
    with pytest.raises(elf.ElfError, match="invalid expected ELF archive identity"):
        owned_elf_link.link_inputs(
            target=TARGET, output=str(tmp_path / "out"), archives=["runtime.a"],
            expected_archive_identity=identity,
        )


def test_inventory_snapshot_hash_rejects_aba_restoration(owned, tmp_path, monkeypatch):
    entry, archive, output, root = _files(tmp_path, monkeypatch)
    good = archive.read_bytes()
    unverified = good.replace(b"\xb8\x2a\0\0\0\xc3", b"\xb8\x63\0\0\0\xc3")
    assert unverified != good
    # Hash A, temporarily present valid B to the provenance verifier, then
    # restore unverified A before the linker could stage it.
    archive.write_bytes(unverified)
    reader = pipeline._verified_capi_export_anchor_symbols

    def restore_around_verification(path, **kwargs):
        archive.write_bytes(good)
        try:
            return reader(path, **kwargs)
        finally:
            archive.write_bytes(unverified)

    monkeypatch.setattr(pipeline, "_verified_capi_export_anchor_symbols", restore_around_verification)
    monkeypatch.setattr(owned_elf_link, "link_inputs", _forbidden)
    with pytest.raises(pipeline.PyPipelineError, match="invalid C-API inventory bundle"):
        _link(entry, archive, output)
    assert archive.read_bytes() == unverified
    assert not output.exists()


def test_snapshot_digest_is_checked_on_the_same_bytes_that_are_parsed(owned, tmp_path, monkeypatch):
    entry, archive, output, root = _files(tmp_path, monkeypatch)
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    parsed = []
    read_members = provenance.read_members

    def checked_parse(data):
        parsed.append(hashlib.sha256(data).hexdigest())
        return read_members(data)

    monkeypatch.setattr(provenance, "read_members", checked_parse)
    receipt = provenance.verify_runtime_archive_manifest(
        archive, runtime_root=root, expected_archive_sha256=expected,
    )
    assert parsed == [expected]
    assert receipt["target_triple"] == TARGET
    with pytest.raises(provenance.ProvenanceError, match="expected verified snapshot"):
        provenance.verify_runtime_archive_manifest(
            archive, runtime_root=root, expected_archive_sha256="0" * 64,
        )
    assert parsed == [expected]


@pytest.mark.parametrize("options", [
    {"target_triple": "aarch64-unknown-linux-gnu"},
    {"target_triple": "x86_64-pc-windows-msvc"},
    {"needs_libpython": True}, {"extra_link_args": ("-rdynamic",)},
    {"semantic_layout_policy": "layout"},
])
def test_invalid_routes_fail_before_inventory(owned, monkeypatch, options):
    monkeypatch.setattr(pipeline, "_verified_capi_export_anchor_symbols", _forbidden)
    with pytest.raises(pipeline.PyPipelineError):
        _link("entry.o", "runtime.a", "output", **options)


@pytest.mark.parametrize("route", ["ir", "assembly", "artifacts", "native_objects"])
def test_public_linux_import_intent_stays_failclosed(owned, tmp_path, monkeypatch, route):
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: DARWIN)
    monkeypatch.setattr(pipeline, "_run_self_link_command", _forbidden)
    monkeypatch.setattr(pipeline, "_emit_self_asm_via_host_python", _forbidden)
    options = dict(needs_native_extension_exports=True)
    with pytest.raises(pipeline.PyPipelineError, match="native-extension"):
        if route == "ir":
            pipeline._link_with_self_backend_ir_texts(
                ['target triple = "' + TARGET + '"\n'], str(tmp_path / "out"),
                "runtime.a", False, **options,
            )
        else:
            options["target_triple"] = TARGET
            function = {
                "assembly": pipeline._link_with_self_backend_assembly_texts,
                "artifacts": pipeline._link_with_self_backend_direct_artifacts,
                "native_objects": pipeline._link_with_self_backend_native_objects,
            }[route]
            function([], str(tmp_path / "out"), "runtime.a", False, **options)


@pytest.mark.parametrize("timed", [False, True])
def test_no_export_intent_preserves_empty_reserved_table(owned, tmp_path, monkeypatch, timed):
    entry, archive, output, root = _files(tmp_path, monkeypatch)
    obj = formats._entry()
    obj = replace(obj, sections=obj.sections + (elf.ElfSection(
        ".data", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE, 8,
        bytes(8), relocations=(elf.ElfRelocation(0, 2, elf.R_X86_64_64),),
    ),), symbols=obj.symbols + (elf.ElfSymbol(
        HOST_EXPORT_SYMBOL, elf.SHN_UNDEF, 0, 0, elf.STB_GLOBAL, elf.STT_OBJECT,
    ),))
    entry.write_bytes(elf.emit_relocatable(obj))
    monkeypatch.setattr(pipeline, "_verified_capi_export_anchor_symbols", _forbidden)
    _link(entry, archive, output, needs_native_extension_exports=False,
          coordinator_timing=Mock() if timed else None)
    assert formats._table(output.read_bytes()) == []


def test_cross_target_deferred_plan_is_not_silently_host_linked(owned, tmp_path, monkeypatch):
    entry = tmp_path / "entry.o"
    entry.write_bytes(elf.emit_relocatable(formats._entry()))
    plan = tmp_path / "plan"
    monkeypatch.setenv("PCC_DEFER_SELF_LINK_PLAN", str(plan))
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: DARWIN)
    with pytest.raises(pipeline.PyPipelineError, match="cross-target"):
        pipeline._link_with_self_backend_direct_artifacts(
            [("entry", "PCO", str(entry))], str(tmp_path / "out"), None, False,
            target_triple=TARGET,
        )
    assert not plan.exists()
