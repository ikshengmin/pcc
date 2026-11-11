"""Managed dependency proof crosses real codegen, never an unproved cache.

IR/worker contract tests are distinct from native collector execution. Static
Linux link ownership remains mandatory even when every source is managed.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from pcc.frontends.python.closed_young_closure import prepare_closed_young_closure
from pcc.frontends.python import pipeline_frontend_workers as workers
from pcc.frontends.python import pipeline_frontend_parallel as parallel

_ENABLE = "@pcc_gc_backend3_enable_closed_young_domain("


def _sources(tmp_path):
    entry = tmp_path / "entry.py"
    helper = tmp_path / "helper.py"
    entry.write_text("from helper import render\nprint(render(42))\n")
    helper.write_text("def render(value):\n    return str(value) + 'x'\n")
    paths = [str(entry), str(helper)]
    names = ["entry", "helper"]
    proof, denial = prepare_closed_young_closure(paths, names, "entry")
    assert not denial
    return paths, names, proof


def _manifest(tmp_path, paths, names, proof=None, **extra):
    path = tmp_path / "worker.manifest"
    workers.write_worker_manifest(
        str(path), str(tmp_path / "result"), str(tmp_path / "ir"), "", "",
        paths, names, [0], entry_module="entry", sibling_inits=("helper",),
        ir_scaffold_mode="on", verbose=False, closed_young_proof=proof, **extra,
    )
    return path


def test_worker_receipt_roundtrip_and_legacy_default(tmp_path):
    paths, names, proof = _sources(tmp_path)
    path = _manifest(tmp_path, paths, names, proof)
    assert path.read_text().splitlines()[0] == workers.WORKER_MANIFEST_V6
    read = workers.read_worker_manifest(str(path))
    assert read["closed_young_proof"] == proof
    assert read["src_paths"] == paths and read["module_names"] == names
    path = _manifest(tmp_path, paths, names)
    assert path.read_text().splitlines()[0] == workers.WORKER_MANIFEST_V4
    assert "closed_young_proof" not in workers.read_worker_manifest(str(path))


@pytest.mark.parametrize("record", ["true", "null", "[]", "{", "{}\n{}"])
def test_worker_receipt_rejects_malformed_or_trailing_record(tmp_path, record):
    paths, names, proof = _sources(tmp_path)
    path = _manifest(tmp_path, paths, names, proof)
    lines = path.read_text().splitlines()
    lines[-1] = record
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(workers.FrontendWorkerContractError):
        workers.read_worker_manifest(str(path))


def test_worker_receipt_does_not_cross_exports_or_checkpoints(tmp_path):
    paths, names, proof = _sources(tmp_path)
    for extra in ({"job_kind": "export"}, {"checkpoint_root": str(tmp_path)}):
        with pytest.raises(workers.FrontendWorkerContractError, match="ordinary codegen"):
            _manifest(tmp_path, paths, names, proof, **extra)


def test_proved_parallel_route_never_reads_or_publishes_proofless_cache(tmp_path):
    paths, names, proof = _sources(tmp_path)
    seen = []
    expected = ([("entry", "compiled")], False, False, 8, [])

    def forbidden(*args, **kwargs):
        pytest.fail("source proof crossed an unproved cache boundary")

    def compile_sources(*args, **kwargs):
        seen.append((args, kwargs))
        return expected

    result = parallel.compile_parallel(
        paths, names, jobs=2, entry_module="entry", sibling_inits=("helper",),
        ir_scaffold_mode="on", verbose=False, closed_young_proof=proof,
        can_spawn_worker=forbidden, worker_command_prefix=forbidden,
        compile_uncached=compile_sources, plan_cache=forbidden, load_cache=forbidden,
        acquire_cache=forbidden, wait_cache=forbidden, publish_cache=forbidden,
        release_cache=forbidden, host_python_command=forbidden, source_root=forbidden,
        profile_begin=lambda *_: 0, profile_end=lambda *_: None,
        profile_counter=lambda *_: None,
    )
    assert result is expected and len(seen) == 1
    assert seen[0][0] == (paths, names)
    assert seen[0][1]["closed_young_proof"] is proof
    assert seen[0][1]["action_cache_plan"] is None
    assert seen[0][1]["jobs"] == 2


def _driver_setup(monkeypatch):
    from pcc.frontends.python import pipeline

    for name in (
        "PCC_RUNTIME_ARCHIVE", "PCC_RUNTIME_DIR", "PCC_DEFER_SELF_LINK_PLAN",
        "PCC_STAGE1_CHECKPOINT_DIR", "PCC_DEFER_FRONTEND_CODEGEN_PLAN",
        "PCC_INDEXED_HANDOFF_REQUEST", "PCC_DIRECT_INDEXED_SIDECAR",
        "PCC_HOST_INDEXED_PROCESS_SPLIT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PCC_RUNTIME_BUILD", "owned")
    monkeypatch.setenv("PCC_SELF_LINK", "pcc")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "")
    monkeypatch.setattr(pipeline, "_ensure_runtime", lambda *a, **k: "owned.a")
    monkeypatch.setattr(pipeline, "_explicit_runtime_archive", lambda *a, **k: "explicit.a")
    monkeypatch.setattr(pipeline, "_admit_python_object_inputs", lambda values, *a: values)
    monkeypatch.setattr(pipeline, "_self_backend_skip_ll_temp", lambda: True)
    captured = []
    monkeypatch.setattr(pipeline, "_link_with_self_backend_ir_texts", lambda texts, *a, **k: captured.extend(texts))
    return pipeline, captured


@pytest.mark.parametrize("extra,admitted", [
    ({}, True),
    ({"runtime_archive": "explicit.a"}, False),
    ({"link_args": ("-lforeign",)}, False),
    ({"object_inputs": ("foreign.o",)}, False),
    ({"target_triple": "arm64-apple-darwin"}, False),
    ({"target_triple": "x86_64-pc-windows-msvc"}, False),
    ({"emit_llvm_only": True}, False),
])
def test_serial_terminal_driver_consumes_full_source_proof(tmp_path, monkeypatch, extra, admitted):
    paths, names, _ = _sources(tmp_path)
    pipeline, captured = _driver_setup(monkeypatch)
    out = tmp_path / "program"
    options = dict(target_triple="x86_64-unknown-linux-gnu")
    options.update(extra)
    profile = {}
    pipeline.compile_python_multi(
        paths, str(out), module_names=names, entry_module="entry",
        backend="self", ir_scaffold_mode="on", profile=profile, **options,
    )
    texts = [out.read_text()] if extra.get("emit_llvm_only") else captured
    calls = [line for text in texts for line in text.splitlines() if "call " in line and _ENABLE in line]
    assert len(calls) == int(admitted)
    assert profile["counters"]["gc3_closed_young_domain_admitted"] == int(admitted)



@pytest.mark.parametrize("statement", [
    "import pkg.child", "from pkg.child import value", "from pkg import child",
])
def test_terminal_driver_does_not_enable_implicit_package_cycle(tmp_path, monkeypatch, statement):
    package = tmp_path / "pkg"
    package.mkdir()
    entry = tmp_path / "entry.py"
    initializer = package / "__init__.py"
    child = package / "child.py"
    entry.write_text(statement + "\nlater = 1\n")
    initializer.write_text("from entry import later\n")
    child.write_text("value = 1\n")
    pipeline, captured = _driver_setup(monkeypatch)
    profile = {}
    pipeline.compile_python_multi(
        [str(entry), str(initializer), str(child)], str(tmp_path / "program"),
        module_names=["entry", "pkg", "pkg.child"], entry_module="entry",
        backend="self", target_triple="x86_64-unknown-linux-gnu",
        ir_scaffold_mode="on", profile=profile,
    )
    assert captured
    assert not any("call " in line and _ENABLE in line
                   for text in captured for line in text.splitlines())
    assert profile["counters"]["gc3_closed_young_domain_admitted"] == 0


def test_inferred_ast_substitution_aborts_before_link(tmp_path, monkeypatch):
    paths, names, _ = _sources(tmp_path)
    pipeline, captured = _driver_setup(monkeypatch)
    from pcc.frontends.python import type_infer
    original = type_infer.infer_module

    def substitute(module, **kwargs):
        typed = original(module, **kwargs)
        return replace(typed, name="other")

    monkeypatch.setattr(type_infer, "infer_module", substitute)
    with pytest.raises(pipeline.PyPipelineError, match="closed young typed module changed"):
        pipeline.compile_python_multi(
            paths, str(tmp_path / "program"), module_names=names,
            backend="self", target_triple="x86_64-unknown-linux-gnu", ir_scaffold_mode="on",
        )
    assert not captured


def test_proof_bearing_generation_cannot_silently_downgrade(tmp_path, monkeypatch):
    from pcc.frontends.python import closed_young_closure
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module

    paths, names, proof = _sources(tmp_path)
    source = Path(paths[0]).read_text()
    module = infer_module(parse_and_lift(source, paths[0], "entry"))
    host = L1CodeGen(module, False, "on")
    host._strict_no_libpython = True
    host._module_source_path = paths[0]
    host._sibling_module_inits = ("helper",)
    host._closed_young_domain_input = module
    host._closed_young_closure = proof
    # A late extension/provider failure must abort even if the source is
    # restored before the final link-time source check.
    monkeypatch.setattr(closed_young_closure, "closed_young_typed_module_denial",
                        lambda *args: "native extension provider appeared")
    with pytest.raises(RuntimeError, match="generation proof changed"):
        host.generate(module)
    assert not host._closed_young_domain_enabled


@pytest.mark.parametrize("method", ["_emit_native_extension_import", "_emit_native_extension_import_from"])
def test_proved_unit_rejects_actual_native_extension_emission(method):
    from types import SimpleNamespace
    from pcc.frontends.python.codegen.import_lowering import ImportLoweringMixin

    host = SimpleNamespace(_closed_young_imports_proved=True)
    # The guard is before any IR, symbol lookup or foreign module execution,
    # and applies to sibling units as well as the program entry.
    with pytest.raises(RuntimeError, match="selected a native extension"):
        getattr(ImportLoweringMixin, method)(host, "provider", "provider.so", None)


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("use_wire", [False, True])
def test_actual_worker_reconstructs_context_and_emits_only_proved_entry(tmp_path, monkeypatch, index, use_wire):
    paths, names, proof = _sources(tmp_path)
    pipeline, _ = _driver_setup(monkeypatch)
    monkeypatch.setattr(pipeline, "_python_frontend_worker_executable", lambda: "")
    ir_dir = tmp_path / "worker-ir"
    ir_dir.mkdir()
    manifest = tmp_path / "actual-worker.manifest"
    result = tmp_path / "actual-worker.tsv"
    exports_path = ""
    ast_dir = ""
    if use_wire:
        modules, exports, derived = pipeline.build_closed_world_context(paths, names, None)
        exports_path = str(tmp_path / "exports.json")
        ast_dir = str(tmp_path / "asts")
        Path(ast_dir).mkdir()
        pipeline._write_native_exports_wire(exports_path, exports, derived)
        for number, module in enumerate(modules):
            pipeline._write_py_ast_wire(str(Path(ast_dir) / ("module_" + str(number) + ".json")), module)
        def no_reopen(*args, **kwargs):
            pytest.fail("proof-bearing worker reopened transport after verified snapshot load")
        monkeypatch.setattr(pipeline, "_read_native_exports_wire", no_reopen)
        monkeypatch.setattr(pipeline, "_read_native_exports_wire_for_module", no_reopen)
        monkeypatch.setattr(pipeline, "_read_py_ast_wire", no_reopen)
    workers.write_worker_manifest(
        str(manifest), str(result), str(ir_dir), exports_path, ast_dir, paths, names,
        [index], entry_module="entry", sibling_inits=("helper",),
        ir_scaffold_mode="on", verbose=False, closed_young_proof=proof,
    )
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0, result.read_text()
    text = (ir_dir / ("module_" + str(index) + ".ll")).read_text()
    calls = [line for line in text.splitlines() if "call " in line and _ENABLE in line]
    assert len(calls) == int(index == 0)
    assert result.read_text().startswith("OK\t" + str(index) + "\t" + names[index])


def test_serial_pre_inference_ast_substitution_aborts(tmp_path, monkeypatch):
    paths, names, _ = _sources(tmp_path)
    pipeline, captured = _driver_setup(monkeypatch)
    original = pipeline.build_closed_world_context
    # The receipt is constructed before context export. Alter only its actual
    # returned AST, not the source parser used for independent reconstruction.
    def substitute(*args, **kwargs):
        modules, exports, derived = original(*args, **kwargs)
        modules = list(modules)
        func = modules[1].body[0]
        modules[1] = replace(modules[1], body=(replace(func, is_method=True),))
        return modules, exports, derived

    monkeypatch.setattr(pipeline, "build_closed_world_context", substitute)
    with pytest.raises(pipeline.PyPipelineError, match="closed young (untyped module|generation proof) changed"):
        pipeline.compile_python_multi(
            paths, str(tmp_path / "program"), module_names=names,
            backend="self", target_triple="x86_64-unknown-linux-gnu", ir_scaffold_mode="on",
        )
    assert not captured


def test_native_module_fixture_has_a_complete_managed_source_proof():
    root = Path(__file__).with_name("fixtures") / "gc3_closed_young_modules"
    paths = [str(root / "entry.py"), str(root / "young_values.py")]
    proof, denial = prepare_closed_young_closure(paths, ["entry", "young_values"], "entry")
    assert not denial
    assert proof is not None
