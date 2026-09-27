"""Real frontend workers must compare live assembly after object encoding."""

import pytest

from pcc.py_frontend import pipeline
from pcc.py_frontend.codegen.layer1 import L1CodeGen


def _validation_worker(tmp_path, monkeypatch, target, text_control, validate=True):
    source = tmp_path / "probe.py"
    source.write_text(
        "def answer(value: int) -> int:\n"
        "    return value + 2\n"
        "print(answer(40))\n",
        encoding="utf-8",
    )
    generate = L1CodeGen.generate

    def generate_for_target(self, module=None):
        # A worker constructed this instance with the same inferred module.
        # Use the public no-override entry so its selected target survives;
        # this test concerns validation/object routing, not module replacement.
        assert module is self.ast_module
        self._target_triple = target
        return generate(self)

    monkeypatch.setattr(L1CodeGen, "generate", generate_for_target)
    for key in (
        "PCC_DIRECT_INDEXED_KERNEL_CAPTURE",
        "PCC_DIRECT_INDEXED_KERNEL_EMIT",
        "PCC_DIRECT_INDEXED_KERNEL_FUSE_USES",
        "PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK",
        "PCC_DIRECT_INDEXED_KERNEL_RELEASE_FRONTEND",
        "PCC_DIRECT_INDEXED_NATIVE_OBJECT",
    ):
        monkeypatch.setenv(key, "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_SIDECAR", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_VALIDATE", "1" if validate else "0")
    monkeypatch.setenv("PCC_TEXT_INDEXED_KERNEL_EMIT", "1" if text_control else "0")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.setenv("PCC_SELF_TARGET_PASSES", "off")
    manifest = tmp_path / "worker.manifest"
    result = tmp_path / "result.tsv"
    pipeline._write_python_frontend_worker_manifest(
        str(manifest), str(result), str(tmp_path), "", "", [str(source)],
        ["probe"], [0], entry_module="probe", sibling_inits=(),
        libpython_mode="off", ir_scaffold_mode="on", verbose=False,
    )
    return manifest, result


@pytest.mark.parametrize("target", [
    "arm64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "x86_64-pc-windows-msvc",
])
@pytest.mark.parametrize("validate,text_control", [(True, False), (True, True), (False, True)])
def test_native_object_worker_keeps_assembly_until_validation(
    tmp_path, monkeypatch, target, validate, text_control,
):
    from pcc.backend.target_objects import encode_assembly_object

    manifest, result = _validation_worker(tmp_path, monkeypatch, target, text_control, validate)
    status = pipeline.run_python_multi_codegen_worker(str(manifest))
    assert status == 0, result.read_text(encoding="utf-8")
    object_path = tmp_path / "module_0.direct.pco"
    assert object_path.stat().st_size > 0
    assert "\tPCO\t" + str(object_path) in result.read_text(encoding="utf-8")
    assert "define " in (tmp_path / "module_0.ll").read_text(encoding="utf-8")
    oracle = tmp_path / "module_0.text.s"
    if text_control:
        # Both the comparison and publication happened: compare the actual
        # object bytes against independent encoding of the retained oracle.
        assert object_path.read_bytes() == encode_assembly_object(
            oracle.read_text(encoding="utf-8"), target,
        )
    else:
        assert not oracle.exists()


def test_native_object_worker_still_rejects_a_real_oracle_mismatch(
    tmp_path, monkeypatch,
):
    from pcc.backend import self_backend_aarch64_darwin as emitter

    manifest, result = _validation_worker(
        tmp_path, monkeypatch, "arm64-apple-darwin", True,
    )
    original = emitter.emit_aarch64_darwin_asm

    def mismatching_oracle(text, optimize=True):
        return original(text, optimize=optimize) + "\n; oracle mismatch\n"

    monkeypatch.setattr(emitter, "emit_aarch64_darwin_asm", mismatching_oracle)
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 1
    assert "direct indexed kernel assembly differs from text oracle" in result.read_text(
        encoding="utf-8",
    )
