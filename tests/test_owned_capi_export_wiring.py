"""Owned C-API export routing on real object files, without native execution.

The native identity and runtime-bundle verifier are mocked at their boundaries.
The owned object decoder, archive selection, consuming source view, relocation,
signature validation and output publication are real. No IR/compiler process,
external linker, Mach-O program, extension initializer or GC gate runs here.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from pcc.backend import macho_exec
from pcc.backend.macho_codesign import parse_signature
from pcc.backend.native_object import OwnedMergedSourceView, encode_native_object
from pcc.frontends.python import pipeline
from pcc.frontends.python import pipeline_native_link as native_link
from pcc.frontends.python import pipeline_self_backend_link as owner
from pcc.frontends.python import pipeline_self_link as contract
from pcc.frontends.python import pipeline_targets
from tests import test_macho_extension_symbols as formats


_TARGET = "aarch64-apple-darwin"


def _forbidden(*args, **kwargs):
    raise AssertionError("unexpected host/reference execution")


def _strict_read(archive, verify):
    return native_link.capi_export_anchor_symbols(
        str(archive), archive_requires_provenance=lambda path: False,
        archive_bundle_valid=verify, host_python_command=_forbidden,
        require_verified_inventory=True,
    )


def _files(tmp_path):
    obj = tmp_path / "input.pco"
    obj.write_bytes(encode_native_object(formats._object()))
    archive = tmp_path / "libfixture.a"
    archive.write_bytes(formats._archive([
        ("api.o", formats._unit("_PyFixture_Api")),
        ("unused.o", formats._unit("_unused_runtime_member")),
    ]))
    Path(str(archive) + ".capi_syms").write_text("_PyFixture_Api\n", encoding="utf-8")
    return obj, archive, tmp_path / "application"


def _validate(**kwargs):
    contract.validate_pcc_self_link_surface(
        "pcc", native_extension_exports_supported=True, **kwargs,
    )


def _run(obj, archive, output, **changes):
    options = dict(
        target_triple=_TARGET, needs_native_extension_exports=True,
        pcc_native_object_inputs=(str(obj),),
        resolve_self_link_mode=lambda **kwargs: "pcc",
        validate_pcc_self_link_surface=_validate,
        repo_root_for_link=_forbidden, host_python_command=_forbidden,
        build_pcc_link_command=_forbidden, log=lambda *args: None,
        join_strings=lambda parts, separator: separator.join(parts),
        verified_capi_export_symbols=lambda path: _strict_read(path, lambda _: True),
    )
    options.update(changes)
    owner.run_link_command(
        ["reference-must-not-run"], None, str(output),
        None if archive is None else str(archive), (), False, **options,
    )


def test_strict_inventory_verifies_nonstandard_archive_name_twice(tmp_path, monkeypatch):
    _, archive, _ = _files(tmp_path)
    verify = Mock(return_value=True)
    monkeypatch.setattr(subprocess, "check_output", _forbidden)
    assert _strict_read(archive, verify) == ["_PyFixture_Api"]
    assert [call.args for call in verify.call_args_list] == [(str(archive),)] * 2


@pytest.mark.parametrize("contents", [None, "", "_PyFixture_Api\n_PyFixture_Api\n",
                                      " _PyFixture_Api\n", "_PyFixture_Api"])
def test_strict_inventory_failures_never_fall_back(tmp_path, monkeypatch, contents):
    archive = tmp_path / "arbitrary-name.a"
    if contents is not None:
        Path(str(archive) + ".capi_syms").write_text(contents, encoding="utf-8")
    monkeypatch.setattr(subprocess, "check_output", _forbidden)
    with pytest.raises(native_link.NativeLinkError):
        _strict_read(archive, lambda _: True)


@pytest.mark.parametrize("states", [(False,), (True, False)])
def test_invalid_or_changed_bundle_is_rejected(tmp_path, monkeypatch, states):
    _, archive, _ = _files(tmp_path)
    verify = Mock(side_effect=states)
    monkeypatch.setattr(subprocess, "check_output", _forbidden)
    with pytest.raises(native_link.NativeLinkError):
        _strict_read(archive, verify)
    assert verify.call_count == len(states)


def test_reference_reader_default_retains_explicit_oracle_contract(tmp_path, monkeypatch):
    archive = str(tmp_path / "reference.a")
    host = Mock(return_value="reference-python")
    check = Mock(return_value="00000001 T _PyFixture_Api\n")
    monkeypatch.setattr(subprocess, "check_output", check)
    result = native_link.capi_export_anchor_symbols(
        archive, archive_requires_provenance=lambda _: False,
        archive_bundle_valid=_forbidden, host_python_command=host,
    )
    assert result == ["_PyFixture_Api"]
    host.assert_called_once_with()
    command = check.call_args.args[0]
    assert command[0:2] == ["reference-python", "-c"]
    assert command[-1] == archive
    assert "['nm', '-g', sys.argv[1]]" in command[2]


def test_unused_owned_reference_command_does_not_resolve_legacy_anchors():
    assert native_link.runtime_archive_link_args_for_owner(
        "runtime.a", True, owned_link=True, reference_link_args=_forbidden,
    ) == ["runtime.a"]


def test_empty_and_reference_command_callbacks_keep_their_arguments():
    reference = Mock(side_effect=lambda archive, needed: [archive, str(needed)])
    cases = ((True, False), (False, False), (False, True))
    for owned, needed in cases:
        assert native_link.runtime_archive_link_args_for_owner(
            "runtime.a", needed, owned_link=owned, reference_link_args=reference,
        ) == ["runtime.a", str(needed)]
    assert [call.args for call in reference.call_args_list] == [
        ("runtime.a", False), ("runtime.a", False), ("runtime.a", True),
    ]


def test_capability_admission_does_not_relax_other_link_contracts():
    with pytest.raises(contract.SelfLinkContractError):
        contract.validate_pcc_self_link_surface("pcc", needs_native_extension_exports=True)
    contract.validate_pcc_self_link_surface(
        "pcc", needs_native_extension_exports=True,
        native_extension_exports_supported=True,
    )
    for extra in ({"needs_libpython": True}, {"extra_link_args": ("-lpython",)}):
        with pytest.raises(contract.SelfLinkContractError):
            contract.validate_pcc_self_link_surface(
                "pcc", needs_native_extension_exports=True,
                native_extension_exports_supported=True, **extra,
            )


def test_pipeline_wrapper_runs_real_verified_consuming_object_link(tmp_path, monkeypatch):
    obj, archive, output = _files(tmp_path)
    prepare = Mock(wraps=macho_exec.prepare_executable_object)
    finish = Mock(wraps=macho_exec.link_prepared_executable)
    verify = Mock(return_value=True)
    monkeypatch.setattr(macho_exec, "prepare_executable_object", prepare)
    monkeypatch.setattr(macho_exec, "link_prepared_executable", finish)
    monkeypatch.setattr(pipeline, "_runtime_archive_wheel_stamp_matches", lambda _: False)
    monkeypatch.setattr(pipeline, "_runtime_archive_provenance_valid", verify)
    monkeypatch.setattr(pipeline, "_host_python_command", _forbidden)
    monkeypatch.setattr(pipeline, "_resolve_self_link_mode", lambda **kwargs: "pcc")
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: _TARGET)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "check_output", _forbidden)
    with monkeypatch.context() as native:
        native.setattr(sys.implementation, "name", "pcc")
        pipeline._run_self_link_command(
            ["reference-must-not-run"], None, str(output), str(archive), (), False,
            needs_native_extension_exports=True,
            pcc_native_object_inputs=(str(obj),), target_triple=_TARGET,
        )
    assert output.is_file()
    image = output.read_bytes()
    assert set(formats._exports(image)) == {"_PyFixture_Api"}
    assert "_unused_runtime_member" not in {
        row["name"] for row in formats.spec.parse_object(image).symbols()
    }
    signature = parse_signature(image)
    assert signature.dataoff + signature.datasize == len(image)
    assert verify.call_count == 2
    assert prepare.call_count == 1 and finish.call_count == 1
    assert prepare.call_args.kwargs["_source_view"] is True
    assert prepare.call_args.kwargs["_consume_inputs"] is True
    roots = prepare.call_args.kwargs["required_symbols"]
    assert roots == ("_PyFixture_Api",)
    assert finish.call_args.kwargs["export_symbols"] is roots
    assert isinstance(finish.call_args.args[0], OwnedMergedSourceView)
    assert not Path(str(output) + ".owned.tmp").exists()


@pytest.mark.parametrize("changes", [
    {"target_triple": "aarch64-unknown-linux-gnu"},
    {"semantic_layout_policy": "unsupported-policy"},
    {"verified_capi_export_symbols": None},
    {"pcc_asm_inputs": ("unused.s",)},
])
def test_unsupported_export_routes_fail_before_reading_inventory(tmp_path, monkeypatch, changes):
    obj, archive, output = _files(tmp_path)
    reader = Mock(side_effect=_forbidden)
    options = {"verified_capi_export_symbols": reader}
    options.update(changes)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    with pytest.raises(owner.SelfBackendLinkError):
        _run(obj, archive, output, **options)
    reader.assert_not_called()
    assert not output.exists()


def test_exports_require_runtime_and_nonempty_inventory(tmp_path, monkeypatch):
    obj, archive, output = _files(tmp_path)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    with pytest.raises(owner.SelfBackendLinkError):
        _run(obj, None, output)
    with pytest.raises(owner.SelfBackendLinkError):
        _run(obj, archive, output, verified_capi_export_symbols=lambda _: [])
    assert not output.exists()


def test_existing_host_compact_budget_is_not_disabled_for_exports(tmp_path, monkeypatch):
    from pcc.backend import stackmap_host_budget

    obj, archive, output = _files(tmp_path)
    budget = object()
    provider = Mock(return_value=budget)
    monkeypatch.setattr(stackmap_host_budget, "host_compact_stackmap_budget", provider)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    with pytest.raises(owner.SelfBackendLinkError, match="compact"):
        _run(obj, archive, output)
    provider.assert_called_once_with()
    assert not output.exists()


def test_deferred_plan_rejects_export_intent_before_writing_files(tmp_path, monkeypatch):
    obj, archive, output = _files(tmp_path)
    plan = tmp_path / "deferred.plan"
    monkeypatch.setenv("PCC_DEFER_SELF_LINK_PLAN", str(plan))
    monkeypatch.setattr(pipeline, "_macho_semantic_layout_enabled", lambda: False)
    monkeypatch.setattr(pipeline, "_resolve_self_link_mode", lambda **kwargs: "pcc")
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: _TARGET)
    monkeypatch.setattr(pipeline, "_write_direct_input_manifest", _forbidden)
    monkeypatch.setattr(pipeline, "_write_deferred_self_link_plan", _forbidden)
    with pytest.raises(pipeline.PyPipelineError, match="export"):
        pipeline._link_with_self_backend_direct_artifacts(
            [("fixture", "PCO", str(obj))], str(output), str(archive), False,
            needs_native_extension_exports=True,
        )
    assert not plan.exists()
    assert not Path(str(plan) + ".inputs").exists()
    assert not output.exists()


def test_default_pipeline_callback_shape_does_not_gain_export_arguments(monkeypatch):
    call = Mock()
    monkeypatch.setattr(owner, "run_link_command", call)
    pipeline._run_self_link_command(["unused"], None, "out", "runtime.a", (), False)
    assert "verified_capi_export_symbols" not in call.call_args.kwargs
    assert "coordinator_timing" not in call.call_args.kwargs


def test_direct_assembly_command_preparation_avoids_legacy_anchor_side_effects(tmp_path, monkeypatch):
    _, archive, output = _files(tmp_path)
    run = Mock()
    finish = Mock()
    monkeypatch.setattr(pipeline, "_macho_semantic_layout_enabled", lambda: False)
    monkeypatch.setattr(pipeline, "_resolve_self_link_mode", lambda **kwargs: "pcc")
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: _TARGET)
    monkeypatch.setattr(pipeline, "_runtime_archive_link_args_for_native_extensions", _forbidden)
    monkeypatch.setattr(pipeline, "_host_python_command", _forbidden)
    monkeypatch.setattr(pipeline, "_run_self_link_command", run)
    monkeypatch.setattr(pipeline, "_finish_self_backend_executable", finish)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "check_output", _forbidden)
    pipeline._link_with_self_backend_assembly_texts(
        [("fixture", "owned command-preparation fixture\n")],
        str(output), str(archive), False, needs_native_extension_exports=True,
    )
    assert str(archive) in run.call_args.args[0]
    assert run.call_args.kwargs["needs_native_extension_exports"] is True
    assert len(run.call_args.kwargs["pcc_asm_inputs"]) == 1
    finish.assert_called_once()
    # This test only prepares the assembly-path command; no assembler runs.


def test_ir_orchestration_source_uses_the_same_tested_owner_argument_selector():
    # Supplementary call-site contract only: no IR is generated or compiled.
    tree = ast.parse(inspect.getsource(owner.link_ir_texts_run))
    selectors = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "runtime_archive_link_args_for_owner"
    ]
    legacy_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "runtime_archive_link_args"
    ]
    assert len(selectors) == 2
    assert legacy_calls == []
