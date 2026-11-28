"""The Linux errno ABI follows the selected libc owner, not the C frontend.

Host-only preprocessing/IR controls are separate from the two execution
controls, which require an explicit system oracle or an admitted owned runtime.
"""
from __future__ import annotations

import ast
from functools import partial
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

import pytest

from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
from pcc.frontends.c.parse import make_c_parser
from pcc.frontends.c.preprocessor import preprocess
from pcc.frontends.python import owned_runtime_build, pipeline
from pcc.frontends.python.codegen.runtime_abi import (
    FREESTANDING_GC_CROSS_OBJECT_SIGNATURES,
)
from pcc.frontends.python.pipeline_freestanding import (
    freestanding_allowed_external_symbols,
    freestanding_module_scope_extern_bindings,
)
from pcc.ir.optimization.ir_mutator import MutableModule


pytestmark = pytest.mark.xdist_group("errno-link-ownership")
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "pcc/runtime/py/freestanding_linux_libc.py"
HEADERS = ROOT / "utils/fake_libc_include"
FIXTURE = Path(__file__).parent / "fixtures/owned_linux_c_exports/errno_ownership.c"
LINUX_TARGETS = ("x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu")


@pytest.fixture
def no_external_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("host errno control attempted an external process")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    for name in ("system", "fork", "posix_spawn", "posix_spawnp"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)


def _errno_native_route_owner():
    from pcc.frontends.c.evaluator import c_evaluator
    from tests import pcc1_route

    job = c_evaluator._compile_translation_unit_artifact_job
    if not (isinstance(job, partial) and job.func is pcc1_route.compile_c_artifact):
        assert not (os.environ.get("PCC_TEST_COMPILER")
                    and os.environ.get(pcc1_route.STRICT_ENV) == "1"), (
            "strict native C artifact route is not installed")
        return None
    # Only the installed strict native route may create a process. Environment
    # variables alone are not evidence that the evaluator has been redirected.
    assert os.environ.get(pcc1_route.STRICT_ENV) == "1"
    assert not job.args and set(job.keywords) == {"_compiler", "_runtime_archive"}
    compiler = job.keywords["_compiler"]
    runtime = job.keywords["_runtime_archive"]
    assert compiler == os.environ.get("PCC_TEST_COMPILER") and Path(compiler).is_file()
    assert runtime == os.environ.get("PCC_RUNTIME_ARCHIVE") and Path(runtime).is_file()
    return compiler, runtime


@pytest.fixture
def errno_translation_unit_api_owner(request, monkeypatch):
    owner = _errno_native_route_owner()
    if owner is None:
        request.getfixturevalue("no_external_processes")
        yield
        return
    compiler, runtime = owner
    popen = subprocess.Popen
    launches = 0

    def native_only(argv, *args, **kwargs):
        nonlocal launches
        assert (isinstance(argv, (list, tuple)) and len(argv) == 4
                and tuple(argv[:2]) == (compiler, "--pcc-c-unit-artifact-worker"))
        assert not args and not kwargs.get("shell") and kwargs.get("executable") is None
        env = kwargs["env"]
        assert env["PCC_RUNTIME_ARCHIVE"] == runtime
        assert env["PCC_HOST_PYTHON"] == env["PCC_RUNTIME_CC"] == "/usr/bin/false"
        assert env["PCC_NO_AUTO_PCC1"] == env["PCC_TEST_COMPILER_ROUTED"] == "1"
        launches += 1
        assert launches == 1, "errno API must launch exactly one native worker"
        return popen(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", native_only)
    yield
    assert launches == 1, "errno API must launch exactly one native worker"


@pytest.mark.parametrize("target,accessor", (
    (LINUX_TARGETS[0], "__errno_location"),
    (LINUX_TARGETS[1], "__errno_location"),
    ("aarch64-apple-darwin", "__error"),
    ("x86_64-pc-windows-msvc", None),
))
def test_errno_header_preserves_platform_owner(target, accessor, no_external_processes):
    text = preprocess(
        "#include <errno.h>\nint *location(void) { return &errno; }\n",
        include_dirs=[str(HEADERS)], target_triple=target,
    )
    assert "pcc_errno_location" not in text
    if accessor is None:
        # This header does not invent a Windows CRT errno ABI.
        assert "__errno_location" not in text and "__error" not in text
        assert re.search(r"return\s*&\s*errno\s*;", text)
    else:
        assert re.search(r"int\s*\*\s*" + accessor + r"\s*\(\s*void\s*\)\s*;", text)
        assert re.search(r"return\s*&\s*\(\s*\*\s*" + accessor + r"\s*\(\s*\)\s*\)", text)
        other = "__error" if accessor == "__errno_location" else "__errno_location"
        assert other not in text


@pytest.mark.parametrize("target", LINUX_TARGETS)
def test_c_errno_uses_platform_pointer_abi(target, no_external_processes):
    # This control promises host-only C preprocessing and IR lowering. Use
    # those APIs explicitly; CEvaluator's unit API is separately tested below
    # because strict CI correctly routes that API through the native compiler.
    source = preprocess(
        "#include <errno.h>\nint *location(void) { return &errno; }\n",
        include_dirs=[str(HEADERS)], target_triple=target,
    )
    tree = make_c_parser().parse(source)
    codegen = CCodeGenerator(translation_unit_name="errno.c")
    codegen.set_target_text(target, "")
    codegen.generate_code(tree)
    text = str(codegen.module)
    assert re.search(r"\bcall ptr(?: \(\))? @__errno_location\(\)", text)
    assert "@pcc_errno_location" not in text


@pytest.mark.parametrize("target", LINUX_TARGETS)
def test_c_errno_translation_unit_api_preserves_platform_pointer_abi(target, errno_translation_unit_api_owner):
    # Preserve the original evaluator/native-artifact obligation on the same
    # source. Do not disable strict routing or substitute a host-only result.
    evaluator = CEvaluator(backend="self", target_triple=target)
    units = evaluator.compile_translation_units(
        [TranslationUnit("errno.c", "errno.c", "#include <errno.h>\nint *location(void) { return &errno; }\n")],
        use_system_cpp=False, use_compile_cache=False,
        include_dirs=[str(HEADERS)],
    )
    assert len(units) == 1
    text = units[0][1]
    assert re.search(r"\bcall ptr(?: \(\))? @__errno_location\(\)", text)
    assert "@pcc_errno_location" not in text


@pytest.mark.parametrize("api", ("run", "Popen", "system"))
def test_host_errno_unit_api_cannot_start_external_processes(request, monkeypatch, api):
    from pcc.frontends.c.evaluator import c_evaluator

    def unexpected_process(*args, **kwargs):
        if api == "system":
            return os.system("exit 0")
        return getattr(subprocess, api)([sys.executable, "-c", "pass"])

    monkeypatch.setattr(c_evaluator, "_compile_translation_unit_artifact_job", unexpected_process)
    # Resolve ownership after installing the deliberately bad host adapter.
    # A native-required lane must reject this missing route even earlier.
    if os.environ.get("PCC_TEST_COMPILER") and os.environ.get("PCC_TEST_COMPILER_STRICT") == "1":
        with pytest.raises(AssertionError, match="strict native C artifact route is not installed"):
            request.getfixturevalue("errno_translation_unit_api_owner")
        return
    request.getfixturevalue("errno_translation_unit_api_owner")
    with pytest.raises(AssertionError, match="host errno control attempted an external process"):
        test_c_errno_translation_unit_api_preserves_platform_pointer_abi(LINUX_TARGETS[0], None)


def test_owned_errno_adapter_forwards_the_same_cell_in_host_model():
    module = ast.parse(SOURCE.read_text())
    function = next(node for node in module.body
                    if isinstance(node, ast.FunctionDef) and node.name == "errno_location_c")
    assert len(function.body) == 1 and isinstance(function.body[0], ast.Return)
    function.decorator_list = []
    function.returns = None
    model = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    cells = [object(), object()]
    calls = []
    def location():
        cell = cells[len(calls)]
        calls.append(cell)
        return cell
    namespace = {"pcc_errno_location": location}
    exec(compile(model, str(SOURCE), "exec"), namespace)
    assert namespace["errno_location_c"]() is cells[0]
    assert namespace["errno_location_c"]() is cells[1]
    assert calls == cells


def test_owned_errno_adapter_admits_only_exact_pointer_boundary():
    source = SOURCE.read_text()
    symbol = "pcc_errno_location"
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[symbol] == ((), "c_ptr")
    assert (symbol, "()", "c_ptr") in freestanding_module_scope_extern_bindings(source)
    assert symbol in freestanding_allowed_external_symbols(source)
    # Preserve the bounded literals used by the native compiler itself.
    abi_source = ROOT / "pcc/frontends/python/codegen/runtime_abi.py"
    chunks = [node.body[0].value for node in ast.parse(abi_source.read_text()).body
              if isinstance(node, ast.FunctionDef)
              and node.name.startswith("_cross_object_signatures_part_")]
    assert chunks and all(isinstance(chunk, ast.Dict) and len(chunk.keys) <= 50
                          for chunk in chunks)
    assert sum(key.value == symbol for chunk in chunks for key in chunk.keys) == 1


@pytest.mark.parametrize("parameters,result", (
    ("(c_ptr,)", "c_ptr"),
    ("(c_int32,)", "c_ptr"),
    ("()", "c_int32"),
    ("()", "c_int64"),
    ("()", "c_void"),
))
def test_owned_errno_adapter_rejects_wrong_boundary(parameters, result):
    source = 'location = extern("pcc_errno_location", ' + parameters + ', ' + result + ')\n'
    assert "pcc_errno_location" not in freestanding_allowed_external_symbols(source)


@pytest.mark.parametrize("target,included", (
    (LINUX_TARGETS[0], True),
    (LINUX_TARGETS[1], True),
    ("aarch64-apple-darwin", False),
    ("x86_64-pc-windows-msvc", False),
))
def test_standard_errno_provider_is_linux_only(target, included):
    modules = owned_runtime_build.runtime_modules(str(ROOT / "pcc/runtime"), target)
    assert ("freestanding_linux_libc" in modules) is included


def _compile_errno_runtime_ir(request, source, output, target):
    from tests import pcc1_route

    owner = _errno_native_route_owner()
    routed = pipeline.compile_python.__module__ == pcc1_route.__name__
    if owner is None:
        assert not routed, "Python route installed without the admitted native owner"
        request.getfixturevalue("no_external_processes")
        owned_runtime_build._compile_runtime_module(source.stem, str(source), str(output), target)
        return
    # install() binds both C and Python routes to the exact same compiler.
    # Admit only its single, source-specific IR command, never a host build.
    assert routed, "strict native Python route is not installed"
    compiler, runtime = owner
    expected = [compiler, "--backend", "self", "--ir-scaffold", "on", "--target", target,
                "--python-library", "--emit-llvm", str(output), str(source)]
    popen = subprocess.Popen
    launches = 0

    def native_only(argv, *args, **kwargs):
        nonlocal launches
        assert isinstance(argv, (tuple, list)) and list(argv) == expected
        assert not args and not kwargs.get("shell") and kwargs.get("executable") is None
        env = kwargs["env"]
        assert env["PCC_TEST_COMPILER"] == compiler and env["PCC_RUNTIME_ARCHIVE"] == runtime
        assert env[pcc1_route.STRICT_ENV] == "1"
        assert env["PCC_HOST_PYTHON"] == env["PCC_RUNTIME_CC"] == "/usr/bin/false"
        assert env["PCC_NO_AUTO_PCC1"] == env["PCC_TEST_COMPILER_ROUTED"] == "1"
        launches += 1
        assert launches == 1, "errno runtime IR must launch exactly one native compiler"
        return popen(argv, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("errno runtime IR attempted an unadmitted external process")

    monkeypatch = request.getfixturevalue("monkeypatch")
    with monkeypatch.context() as native:
        native.setattr(subprocess, "Popen", native_only)
        for name in ("system", "fork", "posix_spawn", "posix_spawnp"):
            if hasattr(os, name):
                native.setattr(os, name, forbidden)
        owned_runtime_build._compile_runtime_module(source.stem, str(source), str(output), target)
    assert launches == 1, "errno runtime IR must launch exactly one native compiler"


@pytest.mark.parametrize("mutation", (
    "none", "none-arm", "wrong-target", "wrong-output", "wrong-source", "wrong-compiler",
    "wrong-compiler-env", "wrong-runtime", "host-python", "host-cc", "unrouted", "no-auto",
    "nonstrict", "shell", "executable", "os-system", "two-launches", "no-launch",
    "missing-python-route",
))
def test_errno_runtime_ir_strict_route_launch_boundary_model(tmp_path, request, monkeypatch, mutation):
    # Exercise the REAL installed pcc1 route and its argv/env construction.
    # Only Popen is modeled; the sentinel file/result is not native IR or
    # a native compiler qualification. The complete provider test below
    # separately validates all actual emitted definitions on its real owner.
    from pcc.frontends.c.evaluator import c_evaluator
    from tests import pcc1_route

    compiler = sys.executable
    runtime = tmp_path / "modeled-runtime.a"
    runtime.touch()
    log = tmp_path / "modeled-routing.jsonl"
    output = tmp_path / "provider.ll"
    target = LINUX_TARGETS[1] if mutation == "none-arm" else LINUX_TARGETS[0]
    expected = [compiler, "--backend", "self", "--ir-scaffold", "on", "--target", target,
                "--python-library", "--emit-llvm", str(output), str(SOURCE)]
    # install() assigns these three APIs itself. Record their original
    # bindings first so even a strict outer suite gets its own route back.
    monkeypatch.setattr(pipeline, "compile_python", pipeline.compile_python)
    monkeypatch.setattr(pipeline, "compile_python_multi", pipeline.compile_python_multi)
    monkeypatch.setattr(c_evaluator, "_compile_translation_unit_artifact_job",
                        c_evaluator._compile_translation_unit_artifact_job)
    monkeypatch.setattr(pcc1_route, "_HOST_C_ARTIFACT", pcc1_route._HOST_C_ARTIFACT)
    monkeypatch.setenv("PCC_TEST_COMPILER", compiler)
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(runtime))
    monkeypatch.setenv(pcc1_route.LOG_ENV, str(log))
    monkeypatch.setenv(pcc1_route.STRICT_ENV, "1")
    monkeypatch.delenv("PCC_TEST_COMPILER_HASH_INPUTS", raising=False)
    pcc1_route.install()
    launches = []

    class ModelProcess:
        def __init__(self, argv, *args, **kwargs):
            launches.append(tuple(argv))
            assert argv == expected and not args
            self.args = argv
            output.write_text("modeled launch only")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def communicate(self, input=None, timeout=None):
            assert input is None and timeout == 900
            return "", ""

        def poll(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", ModelProcess)
    run = subprocess.run

    def mutated_run(argv, **kwargs):
        args = list(argv)
        if mutation == "wrong-target":
            args[6] = LINUX_TARGETS[1]
        elif mutation == "wrong-output":
            args[-2] = str(tmp_path / "other.ll")
        elif mutation == "wrong-source":
            args[-1] = str(tmp_path / "other.py")
        elif mutation == "wrong-compiler":
            args[0] = str(tmp_path / "other-compiler")
        elif mutation in ("wrong-compiler-env", "wrong-runtime", "host-python", "host-cc",
                          "unrouted", "no-auto", "nonstrict"):
            key = {"wrong-compiler-env": "PCC_TEST_COMPILER", "wrong-runtime": "PCC_RUNTIME_ARCHIVE",
                   "host-python": "PCC_HOST_PYTHON", "host-cc": "PCC_RUNTIME_CC",
                   "unrouted": "PCC_TEST_COMPILER_ROUTED", "no-auto": "PCC_NO_AUTO_PCC1",
                   "nonstrict": pcc1_route.STRICT_ENV}[mutation]
            kwargs["env"] = dict(kwargs["env"], **{key: "wrong"})
        elif mutation == "shell":
            kwargs["shell"] = True
        elif mutation == "executable":
            kwargs["executable"] = str(tmp_path / "other-compiler")
        elif mutation == "os-system":
            return os.system("exit 0")
        elif mutation == "no-launch":
            return subprocess.CompletedProcess(args, 0, "", "")
        result = run(args, **kwargs)
        if mutation == "two-launches":
            run(args, **kwargs)
        return result

    monkeypatch.setattr(subprocess, "run", mutated_run)
    if mutation == "missing-python-route":
        monkeypatch.setattr(pipeline, "compile_python", lambda *args, **kwargs: None)
    if mutation in ("none", "none-arm"):
        _compile_errno_runtime_ir(request, SOURCE, output, target)
        assert launches == [tuple(expected)]
        assert output.read_text() == "modeled launch only"
        summary = pcc1_route.summarize(str(log))["counts"]
        assert summary["native_attempted"] == summary["native_completed"] == summary["native"] == 1
        assert summary["host"] == summary["unsupported"] == summary["native_incomplete"] == 0
    else:
        message = ("exactly one native compiler" if mutation in ("two-launches", "no-launch") else
                   "strict native Python route is not installed" if mutation == "missing-python-route" else
                   "unadmitted external process" if mutation == "os-system" else None)
        with pytest.raises(AssertionError, match=message):
            _compile_errno_runtime_ir(request, SOURCE, output, target)
        assert launches == ([tuple(expected)] if mutation == "two-launches" else [])


@pytest.mark.parametrize("api", ("run", "Popen", "system"))
def test_host_errno_runtime_ir_cannot_start_external_processes(tmp_path, request, monkeypatch, api):
    def unexpected_process(*args, **kwargs):
        if api == "system":
            return os.system("exit 0")
        return getattr(subprocess, api)([sys.executable, "-c", "pass"])

    monkeypatch.setattr(pipeline, "compile_python", unexpected_process)
    message = ("strict native Python route is not installed" if os.environ.get("PCC_TEST_COMPILER")
               and os.environ.get("PCC_TEST_COMPILER_STRICT") == "1" else
               "host errno control attempted an external process")
    with pytest.raises(AssertionError, match=message):
        _compile_errno_runtime_ir(request, SOURCE, tmp_path / "provider.ll", LINUX_TARGETS[0])


@pytest.mark.parametrize("target", LINUX_TARGETS)
def test_complete_linux_libc_ir_exports_errno_pointer(tmp_path, target, request):
    # Complete production provider, not a declaration-only or extracted stub.
    output = tmp_path / "freestanding_linux_libc.ll"
    source = SOURCE
    if not pipeline._is_py_runtime_library_source(str(source)):
        directory = tmp_path / "py_runtime_errno_control/py"
        directory.mkdir()
        source = directory / SOURCE.name
        source.write_bytes(SOURCE.read_bytes())
    assert pipeline._is_py_runtime_library_source(str(source))
    _compile_errno_runtime_ir(request, source, output, target)
    text = output.read_text()
    module = MutableModule.parse(text)
    definitions = [node for node in ast.parse(SOURCE.read_text()).body
                   if isinstance(node, ast.FunctionDef)]
    for definition in definitions:
        exports = [decorator.args[0].value for decorator in definition.decorator_list
                   if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name)
                   and decorator.func.id in ("c_abi_export", "c_abi_typed_export")]
        assert len(exports) == 1, definition.name
        function = module.function(exports[0])
        assert function is not None and function.blocks, exports[0]
        assert all(block.terminator is not None and block.terminator.is_terminator()
                   for block in function.blocks), exports[0]
    adapter = module.function("__errno_location")
    assert adapter is not None and not adapter.args
    assert re.search(r"\bptr @__errno_location\(\)", adapter.header_line)
    calls = [instruction for block in adapter.blocks for instruction in block.instructions
             if instruction.opcode == "call"]
    assert len(calls) == 1
    assert re.search(r"\bcall ptr(?: \(\))? @pcc_errno_location\(\)", calls[0].text)
    assert calls[0].result_name
    assert any(instruction.text.strip() == "ret ptr %" + calls[0].result_name
               for block in adapter.blocks for instruction in block.instructions)
    assert not re.search(r"\bcall [^\n]*@py_cpy_", text)
    assert "strict.nolib.stub" not in text
    decoded = re.sub(r"\\([0-9A-Fa-f]{2})", lambda m: chr(int(m[1], 16)), text)
    assert "no-libpython function unavailable:" not in decoded
    assert "unsupported import" not in decoded


def _errno_units(*, owned):
    evaluator = CEvaluator(backend="self", target_triple=LINUX_TARGETS[0])
    units = evaluator.compile_translation_units(
        [TranslationUnit(FIXTURE.name, str(FIXTURE), FIXTURE.read_text())],
        use_system_cpp=False, use_compile_cache=False,
        include_dirs=[str(HEADERS)], cpp_args=["-DPCC_OWNED_ERRNO=1"] if owned else [],
    )
    return evaluator, units


@pytest.mark.pcc_gate(probe=lambda: sys.platform.startswith("linux") and platform.machine() in ("x86_64", "amd64"))
def test_errno_reads_system_libc_failure_through_system_link():
    # Explicit external system-cc oracle: no PCC runtime may supply its errno.
    evaluator, units = _errno_units(owned=False)
    result = evaluator.run_compiled_translation_units_with_system_cc(units, optimize=False)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.pcc_gate(probe=lambda: sys.platform.startswith("linux") and platform.machine() in ("x86_64", "amd64"))
def test_errno_reads_owned_failure_and_shares_runtime_tls(tmp_path, monkeypatch):
    from tests.owned_runtime_c_fixture import admitted_runtime

    archive = admitted_runtime()
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))
    binary = tmp_path / "errno_owned"
    def forbidden(*args, **kwargs):
        raise AssertionError("owned errno control attempted an external build")
    with monkeypatch.context() as owned:
        owned.setattr(subprocess, "Popen", forbidden)
        evaluator, units = _errno_units(owned=True)
        evaluator.emit_executable(units, str(binary), optimize=False)
    result = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
