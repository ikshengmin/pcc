"""The real lowering entrypoint binds one target before any intrinsic runs."""
import pytest

from tests.python.test_frontend_initial_module_reuse import _new_codegen, _typed_module

TARGETS = (
    "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu",
    "x86_64-apple-darwin", "arm64-apple-darwin",
    "x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc",
    "riscv64-unknown-linux-gnu",
)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("route", ["public", "worker-initial"])
@pytest.mark.parametrize("selection", ["default", "owner", "module"])
def test_real_lowering_observes_one_concrete_target(monkeypatch, target, route, selection):
    from pcc.frontends.python import pipeline_targets
    from pcc.frontends.python.codegen.generation_lowering import GenerationLoweringMixin

    calls = []
    def host():
        calls.append("host")
        assert selection == "default", "explicit cross target consulted host"
        return target
    monkeypatch.setattr(pipeline_targets, "host_target_triple", host)
    cg = _new_codegen(_typed_module())
    if selection == "owner":
        cg._target_triple = target
    elif selection == "module":
        cg.module.triple = target
    original = GenerationLoweringMixin._generate_impl
    observed = []
    def lower(self, module, reuse_initial_ir):
        observed.append((self._target_triple, self.module.triple))
        return original(self, module, reuse_initial_ir)
    monkeypatch.setattr(GenerationLoweringMixin, "_generate_impl", lower)
    result = cg.generate() if route == "public" else cg._generate_initial_module()
    assert observed == [(target, target)]
    assert calls == (["host"] if selection == "default" else [])
    assert f'target triple = "{target}"' in result
    assert "define" in result


@pytest.mark.parametrize("route", ["public", "worker-initial"])
def test_conflicting_explicit_targets_fail_before_lowering(monkeypatch, route):
    from pcc.frontends.python.codegen.errors import L1CodegenError
    from pcc.frontends.python.codegen.generation_lowering import GenerationLoweringMixin
    cg = _new_codegen(_typed_module())
    cg._target_triple = "arm64-apple-darwin"
    cg.module.triple = "x86_64-unknown-linux-gnu"
    def forbidden(*args):
        pytest.fail("conflicting targets reached lowering")
    monkeypatch.setattr(GenerationLoweringMixin, "_generate_impl", forbidden)
    with pytest.raises(L1CodegenError, match="owner target conflicts"):
        cg.generate() if route == "public" else cg._generate_initial_module()
    assert not cg._initial_ir_available


@pytest.mark.parametrize("route", ["public", "worker-initial"])
def test_explicit_unknown_target_never_becomes_host(monkeypatch, route):
    from pcc.frontends.python import pipeline_targets
    cg = _new_codegen(_typed_module())
    cg._target_triple = "unknown-unknown-unknown"
    def forbidden():
        pytest.fail("explicit unknown target replaced by host")
    monkeypatch.setattr(pipeline_targets, "host_target_triple", forbidden)
    from pcc.frontends.python.codegen.errors import L1CodegenError
    with pytest.raises(L1CodegenError, match="explicit codegen target must be concrete"):
        cg.generate() if route == "public" else cg._generate_initial_module()
    assert cg._target_triple == "unknown-unknown-unknown"
    assert not cg._initial_ir_available


@pytest.mark.xdist_group("target_pipeline_routes")
@pytest.mark.parametrize("target", [None, "arm64-apple-darwin"])
@pytest.mark.parametrize("multiple", [False, True])
def test_public_pipeline_retains_default_and_cross_routes(tmp_path, monkeypatch, target, multiple):
    from pcc.frontends.python import pipeline, pipeline_targets

    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "2")
    source = tmp_path / "main.py"
    source.write_text("def answer() -> int:\n    return 42\n")
    output = tmp_path / "output.ll"
    parallel_calls = []
    original = pipeline._compile_python_multi_codegen_parallel
    def parallel(*args, **kwargs):
        parallel_calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(pipeline, "_compile_python_multi_codegen_parallel", parallel)
    options = dict(emit_llvm_only=True, backend="self", ir_scaffold_mode="on", target_triple=target)
    if multiple:
        other = tmp_path / "other.py"
        other.write_text("def other() -> int:\n    return 7\n")
        pipeline.compile_python_multi([str(source), str(other)], str(output),
                                     module_names=["main", "other"], entry_module="main", **options)
    else:
        pipeline.compile_python(str(source), str(output), **options)
    ir = output.read_text()
    expected = target or pipeline_targets.host_target_triple()
    assert f'target triple = "{expected}"' in ir
    assert "define" in ir and "@user_main_answer(" in ir
    assert bool(parallel_calls) == (multiple and target is None)
    if multiple:
        assert "@user_other_other(" in ir


@pytest.mark.xdist_group("target_pipeline_routes")
@pytest.mark.parametrize("multiple", [False, True])
def test_public_pipeline_rejects_explicit_unknown(tmp_path, multiple):
    from pcc.frontends.python import pipeline
    from pcc.frontends.python.codegen.errors import L1CodegenError

    source = tmp_path / "main.py"
    source.write_text("def answer() -> int:\n    return 42\n")
    output = tmp_path / "output.ll"
    options = dict(emit_llvm_only=True, backend="self", ir_scaffold_mode="on",
                   target_triple="unknown-unknown-unknown")
    with pytest.raises((L1CodegenError, pipeline.PyPipelineError), match="target must be concrete"):
        if multiple:
            pipeline.compile_python_multi([str(source)], str(output),
                                         module_names=["main"], entry_module="main", **options)
        else:
            pipeline.compile_python(str(source), str(output), **options)
    assert not output.exists()
