"""Exercise real pipeline directory/handoff ownership with modeled worker results.

The worker and runtime outputs below are orchestration fixtures, not executable
objects or runtime qualification. Directory cleanup and deferred-plan writing
use the production implementations; no native builder or program is executed.
"""

from pathlib import Path
from types import SimpleNamespace
import os

import pytest

from pcc.frontends.python import pipeline
from tests.python.test_native_provisioning_guard import container_runtime_model


def _inventory(root):
    return {
        str(path.relative_to(root)): None if path.is_dir() else path.read_bytes()
        for path in root.rglob("*")
        if path.is_dir() or path.is_file()
    }


def _case(tmp_path, monkeypatch, route):
    source = tmp_path / "entry.py"
    source.write_text("pass\n")
    output = tmp_path / "program"
    output.write_bytes(b"existing user output")
    sibling = tmp_path / "program.pcc-pco.other-owner"
    sibling.mkdir()
    (sibling / "sentinel").write_bytes(b"another owner's work")
    external = tmp_path / "user-input.pco"
    external.write_bytes(b"user-owned input")
    archive = tmp_path / "runtime.a"
    archive.write_bytes(b"modeled admitted runtime")
    case = SimpleNamespace(
        source=source, output=output, archive=archive, external=external,
        root=Path(str(output.resolve()) + ".pcc-pco." + str(os.getpid())),
        missing=False, kind="PCO", needs_libpython=False, use_external=False,
        frontend_error=None, deferred_frontend=False, calls=[],
    )
    for name in (
        "PCC_RUNTIME_ARCHIVE", "PCC_DEFER_SELF_LINK_PLAN",
        "PCC_DEFER_FRONTEND_CODEGEN_PLAN", "PCC_STAGE1_CHECKPOINT_DIR",
        "PCC_MACHO_SEMANTIC_LAYOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_NATIVE_OBJECT", "1")
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "0")
    monkeypatch.setenv("PCC_SELF_LINK", "pcc")
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    monkeypatch.setattr(
        pipeline, "_prepare_multi_source_compile_closure",
        lambda paths, names, **kwargs: (paths, names),
    )
    monkeypatch.setattr(pipeline, "_order_module_inits", lambda *args: [])

    def frontend(*args, artifact_dir, **kwargs):
        case.calls.append("frontend")
        assert Path(artifact_dir) == case.root and case.root.is_dir()
        (case.root / "module_0.ll").write_text("modeled worker IR\n")
        artifact = case.external if case.use_external else case.root / "module_0.direct.pco"
        if not case.missing and not case.use_external:
            artifact.write_bytes(b"modeled worker object")
        if case.frontend_error is not None:
            raise case.frontend_error
        if case.deferred_frontend:
            return ("PCC_DEFERRED_FRONTEND_CODEGEN",)
        common = ([], case.needs_libpython, False, 0, [])
        if route == "direct":
            return (*common, None, None, [("entry", case.kind, str(artifact))])
        return (*common, None, [("entry", str(artifact))])

    monkeypatch.setattr(pipeline, "_compile_python_multi_codegen_parallel", frontend)

    def runtime(*args, **kwargs):
        case.calls.append("runtime")
        return str(archive)

    monkeypatch.setattr(pipeline, "_ensure_runtime_without_direct_indexed_env", runtime)

    def compile_case(**kwargs):
        pipeline.compile_python_multi(
            [str(source)], str(output), module_names=["entry"],
            backend="self", libpython_mode="off", ir_scaffold_mode="on", **kwargs,
        )

    case.compile = compile_case
    return case


_FAILURES = [
    (route, failure)
    for route in ("direct", "native-objects")
    for failure in ("runtime", "explicit-runtime", "environment-runtime", "libpython", "emit-only", "missing", "link")
] + [("direct", "checkpoint"), ("direct", "unknown-kind")]


@pytest.mark.parametrize("route,failure", _FAILURES)
@pytest.mark.parametrize("defer", (False, True))
def test_pre_handoff_failures_remove_only_owned_directory(tmp_path, monkeypatch, route, failure, defer):
    case = _case(tmp_path, monkeypatch, route)
    if defer:
        monkeypatch.setenv("PCC_DEFER_SELF_LINK_PLAN", str(tmp_path / "link.plan"))
    error = RuntimeError("original rejected operation")

    def reject(*args, **kwargs):
        raise error

    kwargs = {}
    expected_type = RuntimeError
    if failure == "runtime":
        monkeypatch.setattr(pipeline, "_ensure_runtime_without_direct_indexed_env", reject)
    elif failure == "explicit-runtime":
        kwargs["runtime_archive"] = str(case.archive)
        monkeypatch.setattr(pipeline, "_explicit_runtime_archive", reject)
    elif failure == "environment-runtime":
        monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(case.archive))
        monkeypatch.setattr(pipeline, "_ensure_runtime", reject)
    elif failure == "libpython":
        case.needs_libpython = True
    elif failure == "emit-only":
        kwargs["emit_llvm_only"] = True
    elif failure == "missing":
        case.missing = True
        expected_type = FileNotFoundError
    elif failure == "checkpoint":
        monkeypatch.setattr(pipeline, "_validate_stage1_checkpoint_link", reject)
    elif failure == "unknown-kind":
        case.kind = "UNKNOWN"
    else:
        name = "_link_with_self_backend_direct_artifacts" if route == "direct" else "_link_with_self_backend_native_objects"
        monkeypatch.setattr(pipeline, name, reject)
    before = _inventory(tmp_path)
    with pytest.raises(expected_type) as caught:
        case.compile(**kwargs)
    if failure in ("runtime", "explicit-runtime", "environment-runtime", "checkpoint", "link"):
        assert caught.value is error
    assert case.calls[0] == "frontend"
    assert not case.root.exists()
    assert _inventory(tmp_path) == before


@pytest.mark.parametrize("route", ("direct", "native-objects"))
def test_runtime_interrupt_cleans_owned_directory_and_preserves_external_artifact(tmp_path, monkeypatch, route):
    case = _case(tmp_path, monkeypatch, route)
    case.use_external = True
    error = KeyboardInterrupt("interrupted admission")

    def interrupt(*args, **kwargs):
        raise error

    monkeypatch.setattr(pipeline, "_ensure_runtime_without_direct_indexed_env", interrupt)
    before = _inventory(tmp_path)
    with pytest.raises(KeyboardInterrupt) as caught:
        case.compile()
    assert caught.value is error
    assert _inventory(tmp_path) == before


@pytest.mark.parametrize("route", ("direct", "native-objects"))
def test_successful_deferred_self_link_retains_its_real_plan_inputs(tmp_path, monkeypatch, route):
    case = _case(tmp_path, monkeypatch, route)
    plan = tmp_path / "link.plan"
    monkeypatch.setenv("PCC_DEFER_SELF_LINK_PLAN", str(plan))
    case.compile()
    rows = plan.read_text().splitlines()
    assert rows[0] == "pcc.deferred-self-link.v1"
    assert rows[1] == str(case.output.resolve())
    assert rows[2] == str(case.archive.resolve())
    assert rows[5] == str(case.root)
    manifest = Path(rows[3])
    assert manifest.read_text().splitlines() == [
        "pcc.macho-internal-inputs.v1", "1", "PCO\t" + str(case.root / "module_0.direct.pco"),
    ]
    assert (case.root / "module_0.direct.pco").read_bytes() == b"modeled worker object"
    assert case.output.read_bytes() == b"existing user output"
    assert case.external.read_bytes() == b"user-owned input"


@pytest.mark.parametrize("route", ("direct", "native-objects"))
def test_failed_deferred_plan_write_does_not_transfer_ownership(tmp_path, monkeypatch, route):
    case = _case(tmp_path, monkeypatch, route)
    monkeypatch.setenv("PCC_DEFER_SELF_LINK_PLAN", str(tmp_path / "missing" / "link.plan"))
    before = _inventory(tmp_path)
    with pytest.raises(FileNotFoundError):
        case.compile()
    assert _inventory(tmp_path) == before


def test_successful_frontend_handoff_marker_keeps_owned_directory(tmp_path, monkeypatch):
    """The frontend publisher's existing success marker transfers its ownership."""
    case = _case(tmp_path, monkeypatch, "direct")
    case.deferred_frontend = True
    monkeypatch.setenv("PCC_DEFER_FRONTEND_CODEGEN_PLAN", str(tmp_path / "frontend.plan"))
    case.compile()
    assert case.calls == ["frontend"]
    assert (case.root / "module_0.direct.pco").is_file()
    assert case.output.read_bytes() == b"existing user output"


def test_frontend_failure_before_handoff_removes_owned_directory(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, "direct")
    case.frontend_error = ValueError("frontend failed before publishing")
    monkeypatch.setenv("PCC_DEFER_FRONTEND_CODEGEN_PLAN", str(tmp_path / "frontend.plan"))
    before = _inventory(tmp_path)
    with pytest.raises(ValueError) as caught:
        case.compile()
    assert caught.value is case.frontend_error
    assert _inventory(tmp_path) == before


def test_failed_directory_creation_does_not_remove_preexisting_file(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, "direct")
    case.root.write_bytes(b"preexisting non-directory collision")
    before = _inventory(tmp_path)
    with pytest.raises(FileExistsError):
        case.compile()
    assert case.calls == []
    assert _inventory(tmp_path) == before


@pytest.mark.parametrize("route", ("direct", "native-objects"))
def test_successful_link_cleans_replaced_stale_owned_directory(tmp_path, monkeypatch, route):
    case = _case(tmp_path, monkeypatch, route)
    # Existing creation contract replaces this exact output/PID scratch path;
    # a sibling owner directory and the user output are not scratch ownership.
    case.root.mkdir()
    (case.root / "stale-sentinel").write_bytes(b"previous owned scratch")
    seen = []

    def link(*args, **kwargs):
        assert not (case.root / "stale-sentinel").exists()
        assert (case.root / "module_0.direct.pco").is_file()
        seen.append(args[2])

    name = "_link_with_self_backend_direct_artifacts" if route == "direct" else "_link_with_self_backend_native_objects"
    monkeypatch.setattr(pipeline, name, link)
    case.compile()
    assert seen == [str(case.archive)]
    assert not case.root.exists()
    assert case.output.read_bytes() == b"existing user output"
    assert (tmp_path / "program.pcc-pco.other-owner" / "sentinel").read_bytes() == b"another owner's work"


@pytest.mark.parametrize("route", ("direct", "native-objects"))
@pytest.mark.parametrize("selection", ("argument", "environment", "cached"))
def test_matching_real_archive_is_allowed_without_provisioning(
    tmp_path, monkeypatch, container_runtime_model, route, selection,
):
    """Real deep admission with tiny owned bundle; final native link is modeled."""
    normal_runtime_selection = pipeline._ensure_runtime_without_direct_indexed_env
    case = _case(tmp_path, monkeypatch, route)
    monkeypatch.setattr(pipeline, "_ensure_runtime_without_direct_indexed_env", normal_runtime_selection)
    archive = container_runtime_model
    monkeypatch.setenv("PCC_RUNTIME_DIR", str(archive.parent))
    monkeypatch.setenv("PCC_RUNTIME_BUILD", "owned")
    kwargs = {}
    if selection == "argument":
        kwargs["runtime_archive"] = str(archive)
    elif selection == "environment":
        monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))
    seen = []

    def link(*args, **kwargs):
        assert (case.root / "module_0.direct.pco").is_file()
        seen.append(args[2])

    name = "_link_with_self_backend_direct_artifacts" if route == "direct" else "_link_with_self_backend_native_objects"
    monkeypatch.setattr(pipeline, name, link)
    before = _inventory(tmp_path)
    case.compile(**kwargs)
    assert seen == [str(archive)]
    assert _inventory(tmp_path) == before
