"""The host direct worker must use the same instruction owner as pcc1."""

from pcc.backend import self_backend_aarch64_darwin as arm_emitter, target_objects as x86_emitter
from pcc.frontends.python.pipeline_targets import host_target_triple
from pcc.frontends.python import pipeline, pipeline_frontend_workers as workers


def test_host_worker_emits_final_native_instructions(tmp_path, monkeypatch):
    for name in (
        "PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "PCC_DIRECT_INDEXED_KERNEL_EMIT",
        "PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK",
        "PCC_DIRECT_INDEXED_KERNEL_RELEASE_FRONTEND",
    ):
        monkeypatch.setenv(name, "1")
    source = tmp_path / "probe.py"
    source.write_text("def add(a: int, b: int) -> int:\n    return a + b\nprint(add(20, 22))\n")
    manifest = tmp_path / "worker.manifest"
    workers.write_worker_manifest(
        str(manifest), str(tmp_path / "result.tsv"), str(tmp_path), "", "",
        [str(source)], ["probe"], [0], entry_module="probe", sibling_inits=(),
        ir_scaffold_mode="on", verbose=False,
    )
    observed = []
    target = host_target_triple()
    x86 = target.startswith("x86_64")
    emitter, name = ((x86_emitter, "emit_x86_64_streamed_module") if x86 else
                     (arm_emitter, "emit_aarch64_darwin_indexed_transport"))
    emit = getattr(emitter, name)

    def traced(*args, **kwargs):
        result = emit(*args, **kwargs)
        assert args[0].triple == target
        if x86:
            observed.append((result.windows == ("windows" in target),
                             result.builder.sections[".text"].size > 0))
        else:
            observed.append((kwargs.get("structured_instructions"), result.native_finalized))
        return result

    monkeypatch.setattr(emitter, name, traced)
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0
    assert observed == [(True, True)]
    path = tmp_path / "module_0.direct.pco"
    raw = path.read_bytes()
    if "linux" in target:
        from pcc.backend.elf_x86_64 import parse_relocatable
        assert parse_relocatable(raw).machine == (62 if x86 else 183)
    elif "windows" in target:
        from pcc.backend.coff_x86_64 import parse_object
        assert parse_object(raw).sections
    else:
        from pcc.backend.native_object import decode_packed_native_object
        assert decode_packed_native_object(raw).sections
