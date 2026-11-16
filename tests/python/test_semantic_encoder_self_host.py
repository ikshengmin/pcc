"""The semantic encoder must lower with its real typed record providers."""

from pathlib import Path

import pytest

from pcc.frontends.python import pipeline
from tests.python.test_direct_indexed_worker_validation import _validation_worker


@pytest.mark.parametrize("target", [
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
])
def test_semantic_encoder_lowers_with_typed_provider_closure(tmp_path, monkeypatch, target):
    # A singleton source silently treats imported records as dynamic objects and
    # misses incompatible local reuse across otherwise disjoint opcode branches.
    root = Path(__file__).resolve().parents[2]
    names = [
        "pcc.backend.self_backend_aarch64_semantic_encode",
        "pcc.backend.self_backend_value_arena",
        "pcc.backend.self_backend_aarch64_semantics",
        "pcc.backend.arm64_encode",
        "pcc.backend.arm64_encode_producer",
    ]
    paths = [str(root / (name.replace(".", "/") + ".py")) for name in names]
    manifest, result = _validation_worker(tmp_path, monkeypatch, target, False, False)
    pipeline._write_python_frontend_worker_manifest(
        str(manifest), str(result), str(tmp_path), "", "", paths, names, [0],
        entry_module=names[0], sibling_inits=(), ir_scaffold_mode="on", verbose=False,
    )
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0, result.read_text()
    assert "\tPCO\t" in result.read_text()
    assert (tmp_path / "module_0.direct.pco").stat().st_size > 0
