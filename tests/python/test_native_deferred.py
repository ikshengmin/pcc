import os
import subprocess
import sys
from pathlib import Path

import pytest

from pcc.py_frontend import native_deferred as driver


def test_native_entry_rejects_host_python():
    with pytest.raises(RuntimeError, match="host Python is not supported"):
        driver.main()


def test_cli_dispatch_checks_native_owner_and_forwards_plan(monkeypatch, capsys):
    from types import SimpleNamespace
    from pcc import cli_bootstrap as cli

    calls = []
    monkeypatch.setattr(driver, "run", calls.append)
    assert cli.bootstrap_cli_main(["--pcc-native-deferred-worker", "--check"]) == 2
    assert "requires pcc1" in capsys.readouterr().err
    monkeypatch.setattr(cli, "sys", SimpleNamespace(
        implementation=SimpleNamespace(name="pcc"),
    ))
    assert cli.bootstrap_cli_main(["--pcc-native-deferred-worker", "--check"]) == 0
    assert calls == []
    assert cli.bootstrap_cli_main(["--pcc-native-deferred-worker", "/plan with spaces"]) == 0
    assert calls == ["/plan with spaces"]


def test_shell_rejects_old_compiler_before_starting_compile(tmp_path):
    compiler = tmp_path / "old compiler"
    compiler.write_text("#!/bin/sh\nexit 2\n")
    compiler.chmod(0o755)
    touched = tmp_path / "started"
    script = Path(driver.__file__).parents[2] / "scripts/run_pcc_native_deferred.sh"
    result = subprocess.run(
        ["/bin/bash", str(script), str(compiler), "codegen", "link", "--",
         "/usr/bin/touch", str(touched)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "host Python fallback is forbidden" in result.stderr
    assert not touched.exists()


def test_bootstrap_rejects_missing_deferred_runtime_before_compilation(tmp_path):
    compiler = tmp_path / "pcc1"
    started = tmp_path / "compiler-started"
    compiler.write_text('#!/bin/sh\n/usr/bin/touch "' + str(started) + '"\n')
    compiler.chmod(0o755)
    environment = dict(os.environ,
        PCC_RUNTIME_ARCHIVE=str(tmp_path / "absent-runtime.a"),
        PCC_PY_FRONTEND_IR_CACHE_IDENTITY="test", PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY="test",
        PCC_BOOTSTRAP_DEFER_FRONTEND_CODEGEN="1", PCC_BOOTSTRAP_DEFER_SELF_LINK="1",
    )
    environment.pop("LC_ALL", None)
    script = Path(driver.__file__).parents[2] / "scripts/bootstrap.sh"
    result = subprocess.run(
        ["/bin/bash", str(script), "--backend", "self", "--from-stage", "2", "--stage", "2",
         "--out-dir", str(tmp_path)],
        env=environment, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "requires a runtime archive before compilation" in result.stderr
    assert not started.exists()


def test_bootstrap_continuation_inherits_effective_stage_environment(tmp_path):
    import shlex

    root = Path(driver.__file__).parents[2]
    bootstrap = (root / "scripts/bootstrap.sh").read_text()
    body = bootstrap[bootstrap.index("run_stage() {"):bootstrap.index("# stage 1: CPython-hosted")]
    compiler = tmp_path / "compiler"
    compiler.write_text('''#!/bin/bash
set -eu
printf '%s|%s|%s|%s|%s|%s\\n' "$1" "$PCC_PYTHON_IR_PASSES" "$PCC_SELF_BACKEND_JOBS" "$PCC_RUNTIME_ARCHIVE" "$PCC_DIRECT_INDEXED_KERNEL_CAPTURE" "$PCC_DIRECT_INDEXED_KERNEL_EMIT" >> "$TEST_LOG"
if [[ "$1" == --pcc-native-deferred-worker ]]; then
    if [[ "$2" == --check ]]; then exit 0; fi
    printf '#!/bin/sh\\nexit 0\\n' > "$PCC_DEFER_FRONTEND_OUTPUT"
    chmod +x "$PCC_DEFER_FRONTEND_OUTPUT"
else
    : > "$PCC_DEFER_FRONTEND_CODEGEN_PLAN"
fi
''')
    compiler.chmod(0o755)
    runtime = tmp_path / "runtime.a"
    runtime.touch()
    config = {
        "REPO_ROOT": str(root), "OUT_DIR": str(tmp_path), "MAIN_PY": "input.py",
        "BACKEND": "self", "BACKEND_EXPLICIT": "1", "BOOTSTRAP_PROFILE_DIR": "",
        "BOOTSTRAP_RUNTIME_CC": "pcc", "BOOTSTRAP_RUNTIME_HIGH": "py",
        "BOOTSTRAP_PYTHON_LIBPYTHON": "off", "BOOTSTRAP_PYTHON_IR_PASSES": "off",
        "BOOTSTRAP_PY_FRONTEND_JOBS": "auto", "BOOTSTRAP_SELF_BACKEND_JOBS": "2",
        "BOOTSTRAP_MACHO_LINK_JOBS": "2", "BOOTSTRAP_MAX_TREE_RSS_BYTES": "8589934592",
        "BOOTSTRAP_IN_PROCESS_CODEGEN": "0", "BOOTSTRAP_DEFER_FRONTEND_CODEGEN": "1",
        "BOOTSTRAP_DEFER_SELF_LINK": "1", "BOOTSTRAP_EXTERNAL_MEMORY_GUARD": "1",
        "PCC_RUNTIME_ARCHIVE": str(runtime),
    }
    source = "set -eu\n" + "\n".join(k + "=" + shlex.quote(v) for k, v in config.items())
    source += "\nbanner() { :; }; now_ms() { echo 0; }; stage_exec_barrier() { :; }; write_stage_result_json() { :; };\n"
    source += body + "\nrun_stage 2 " + shlex.quote(str(tmp_path / "output")) + " " + shlex.quote(str(compiler))
    log = tmp_path / "calls"
    env = dict(os.environ, TEST_LOG=str(log), PCC_PYTHON_IR_PASSES="default", PCC_SELF_BACKEND_JOBS="1")
    env.pop("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", None)
    env.pop("PCC_DIRECT_INDEXED_KERNEL_EMIT", None)
    result = subprocess.run(["/bin/bash", "-c", source], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    rows = log.read_text().splitlines()
    assert len(rows) == 3
    assert [row.split("|")[0] for row in rows] == ["--pcc-native-deferred-worker", "--backend", "--pcc-native-deferred-worker"]
    assert all(row.split("|")[1:] == ["off", "2", str(runtime), "1", "1"] for row in rows)


@pytest.mark.parametrize("auto_pco", [False, True])
def test_native_codegen_orders_results_and_rejects_stale_artifacts(tmp_path, monkeypatch, auto_pco):
    manifests = []
    for index in (1, 0):
        manifest = tmp_path / ("worker" + str(index))
        result = tmp_path / ("result" + str(index))
        manifest.write_text("\n".join([
            "pcc.py_frontend.codegen_worker.v4", str(result),
            str(tmp_path), "", "", "", "", "", "", "", "1", str(index),
        ]) + "\n")
        result.write_text("stale result")
        stem = tmp_path / ("module_" + str(index) + ".direct")
        stem.with_suffix(".pco").write_bytes(b"stale pco")
        stem.with_suffix(".pidx").write_bytes(b"stale sidecar")
        manifests.append(str(manifest))
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"archive")
    plan = [
        "pcc.frontend-codegen-plan.v2", "/native/pcc1", str(tmp_path / "output"),
        str(runtime), str(tmp_path / "profile"), str(tmp_path / "inputs"),
        str(tmp_path), "2", "1", "2", "2", "pidx-pco-v1",
    ] + manifests
    calls = []

    def run_one(command):
        import shlex
        arguments = shlex.split(command)
        if "--pcc-python-multi-codegen-worker" in arguments:
            path = arguments[-1]
            index = int(Path(path).read_text().splitlines()[-1])
            result = tmp_path / ("result" + str(index))
            assert not result.exists()
            sidecar = tmp_path / ("module_" + str(index) + ".direct.pidx")
            assert not sidecar.exists()
            sidecar.write_bytes(b"sidecar")
            result.write_text("OK\t" + str(index) + "\tmodule\t0\t0\t0\tunused\tPIDX\t" + str(sidecar) + "\n")
        else:
            assert Path(arguments[-3]).read_bytes() == b"sidecar"
            packed = Path(arguments[-2])
            assert not packed.exists()
            packed.write_bytes(b"packed")

    def run_commands(commands, width):
        calls.append((len(commands), width))
        for command in commands:
            run_one(command)

    def run_chained(commands, reservations, followups, paths, floor, width, budget):
        calls.append(("chained", len(commands), width))
        for command, followup, path in zip(commands, followups, paths):
            run_one(command)
            assert Path(path).is_file()
            run_one(followup)

    linked = []
    monkeypatch.setattr(driver, "_native_worker", lambda _path: None)
    from pcc.py_frontend import deferred_frontend_schedule
    monkeypatch.setattr(deferred_frontend_schedule, "run_worker_processes", run_commands)
    monkeypatch.setattr(deferred_frontend_schedule, "run_chained_worker_processes", run_chained)
    monkeypatch.setattr(
        deferred_frontend_schedule, "_frontend_floors_and_order",
        lambda commands, _manifests: ([1] * len(commands), list(range(len(commands)))),
    )
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "auto" if auto_pco else "2")
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(6 * 1073741824))
    monkeypatch.setattr(deferred_frontend_schedule, "parallel_cpu_budget", lambda: 12)
    # Memory admission reads this process's RSS; ordering is the contract here.
    monkeypatch.setattr(deferred_frontend_schedule, "compiled_native_auto_jobs", lambda jobs: jobs)
    monkeypatch.setattr(driver, "_link", lambda *args: linked.append(args))
    driver._codegen(plan)
    assert calls == ([("chained", 2, 12)] if auto_pco else [(1, 1), (1, 2), (1, 1), (1, 2)])
    assert Path(linked[0][1]).read_text().splitlines()[2:] == [
        "PCO\t" + str(tmp_path / "module_0.direct.pco"),
        "PCO\t" + str(tmp_path / "module_1.direct.pco"),
    ]


def test_native_codegen_rejects_a_sidecar_other_than_the_scheduled_one(tmp_path, monkeypatch):
    manifest = tmp_path / "worker0"
    result = tmp_path / "result0"
    manifest.write_text("\n".join([
        "pcc.py_frontend.codegen_worker.v4", str(result),
        str(tmp_path), "", "", "", "", "", "", "", "1", "0",
    ]) + "\n")
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"archive")
    plan = [
        "pcc.frontend-codegen-plan.v2", "/native/pcc1", str(tmp_path / "output"),
        str(runtime), str(tmp_path / "profile"), str(tmp_path / "inputs"),
        str(tmp_path), "1", "0", "1", "1", "pidx-pco-v1", str(manifest),
    ]

    def run_commands(commands, _width):
        import shlex
        for command in commands:
            arguments = shlex.split(command)
            if "--pcc-python-multi-codegen-worker" in arguments:
                other = tmp_path / "elsewhere.pidx"
                other.write_bytes(b"sidecar")
                result.write_text("OK\t0\tmodule\t0\t0\t0\tunused\tPIDX\t" + str(other) + "\n")
                (tmp_path / "module_0.direct.pidx").write_bytes(b"sidecar")
            else:
                Path(arguments[-2]).write_bytes(b"packed")

    monkeypatch.setattr(driver, "_native_worker", lambda _path: None)
    from pcc.py_frontend import deferred_frontend_schedule
    monkeypatch.setattr(deferred_frontend_schedule, "run_worker_processes", run_commands)
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "1")
    monkeypatch.setattr(driver, "_link", lambda *args: None)
    with pytest.raises(ValueError, match="not the scheduled PCO input"):
        driver._codegen(plan)


def test_native_codegen_refuses_script_worker(tmp_path):
    worker = tmp_path / "python launcher"
    worker.write_text("#!/usr/bin/python3\n")
    worker.chmod(0o755)
    with pytest.raises(ValueError, match="must be a native executable"):
        driver._native_worker(str(worker))


def test_native_unsupported_link_surface_never_resolves_host_python(monkeypatch):
    from types import SimpleNamespace
    from pcc.py_frontend import pipeline_self_backend_link as linker

    monkeypatch.setattr(linker, "sys", SimpleNamespace(
        platform="linux", implementation=SimpleNamespace(name="pcc"),
    ))
    def forbidden():
        pytest.fail("native compiler requested host Python")

    # Plain internal inputs link in process; semantic layout is a surface the
    # in-process owner does not cover, so it reaches the native refusal.
    with pytest.raises(linker.SelfBackendLinkError, match="host Python fallback is forbidden"):
        linker.run_link_command(
            [], "input.s", "out", None, (), False,
            semantic_layout_policy="policy.json",
            target_triple="arm64-apple-darwin23.6.0",
            resolve_self_link_mode=lambda **_kwargs: "pcc",
            validate_pcc_self_link_surface=lambda **kwargs: None,
            repo_root_for_link=forbidden, host_python_command=forbidden,
            build_pcc_link_command=None, log=None, join_strings=None,
        )


@pytest.mark.integration
def test_native_deferred_link_plan_executes_under_all_collectors(
    tmp_path, python_program_compiler, pcc_py_runtime_archive,
):
    from pcc.backend.arm64_asm_driver import assemble_file
    from pcc.backend.native_object import NativeObject, encode_native_object

    helper = tmp_path / "native-deferred"
    python_program_compiler(
        str(Path(driver.__file__)), str(helper), backend="self",
        libpython_mode="off", runtime_archive=str(pcc_py_runtime_archive),
    )
    sections, undefined = assemble_file(
        ".section __TEXT,__text,regular,pure_instructions\n"
        ".globl _main\n.p2align 2\n_main:\n movz w0, #42\n ret\n"
    )
    packed = tmp_path / "main.pco"
    packed.write_bytes(encode_native_object(
        NativeObject.from_sections(sections, undefined=undefined),
    ))
    inputs = tmp_path / "inputs"
    inputs.write_text("pcc.macho-internal-inputs.v1\n1\nPCO\t" + str(packed) + "\n")
    for gc in range(5):
        output = tmp_path / ("out" + str(gc))
        plan = tmp_path / ("plan" + str(gc))
        plan.write_text("\n".join([
            "pcc.deferred-self-link.v1", str(output), "", str(inputs),
            str(tmp_path / "profile"), "", "0",
        ]) + "\n")
        env = dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc),
                   PCC_HOST_PYTHON="/usr/bin/false", PCC_HOST_PCC="/usr/bin/false")
        linked = subprocess.run([str(helper), str(plan)], env=env, capture_output=True, timeout=30)
        assert linked.returncode == 0, linked.stderr
        executed = subprocess.run([str(output)], env=env, capture_output=True, timeout=10)
        assert executed.returncode == 42, executed.stderr


@pytest.mark.integration
def test_pcc1_native_deferred_cli_compiles_and_executes_two_modules(
    tmp_path, native_pcc1_compiler, pcc_py_runtime_archive,
):
    from tests.python.process_timeout import run_process_group_timeout

    package = tmp_path / "two_modules"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "lib.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    source = package / "__main__.py"
    source.write_text("from two_modules.lib import add\nprint(add(20, 22))\n")
    tools = tmp_path / "file-tools"
    tools.mkdir()
    for name in ("ls", "mkdir", "cp", "rm", "sh", "cat"):
        (tools / name).symlink_to("/bin/" + name)
    output = tmp_path / "native output"
    codegen = tmp_path / "codegen plan"
    link = tmp_path / "link plan"
    env = dict(os.environ, PATH=str(tools), PYTHONPATH=str(tmp_path),
               PCC_RUNTIME_ARCHIVE=str(pcc_py_runtime_archive),
               PCC_RUNTIME_CC="/usr/bin/false", PCC_HOST_PYTHON="/usr/bin/false",
               PCC_HOST_PCC="/usr/bin/false", PCC_PY_FRONTEND_JOBS="auto",
               PCC_WORKER_TREE_BUDGET_BYTES="4294967296",
               PCC_SELF_BACKEND_JOBS="2", PCC_PYTHON_IR_PASSES="off",
               PCC_DIRECT_INDEXED_KERNEL_CAPTURE="1", PCC_DIRECT_INDEXED_KERNEL_EMIT="1",
               PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK="1",
               PCC_DIRECT_INDEXED_KERNEL_FUSE_USES="1",
               PCC_DIRECT_INDEXED_KERNEL_RELEASE_FRONTEND="1",
               PCC_DIRECT_INDEXED_NATIVE_OBJECT="1",
               PCC_DEFER_FRONTEND_CODEGEN_PLAN=str(codegen),
               PCC_DEFER_FRONTEND_OUTPUT=str(output), PCC_DEFER_SELF_LINK_PLAN=str(link))
    env.pop("LC_ALL", None)
    script = Path(driver.__file__).parents[2] / "scripts/run_pcc_native_deferred.sh"
    result = run_process_group_timeout(
        ["/bin/bash", str(script), str(native_pcc1_compiler), str(codegen), str(link), "--",
         str(native_pcc1_compiler), "--backend", "self", "--python-libpython", "off",
         str(source), "-o", str(output)],
        env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert codegen.read_text().splitlines()[0] == "pcc.frontend-codegen-plan.v2"
    inputs = Path(str(codegen) + ".internal-inputs").read_text().splitlines()[2:]
    assert len(inputs) == 2 and all(item.startswith("PCO\t") for item in inputs)
    executed = subprocess.run([str(output)], env=env, capture_output=True, text=True, timeout=10)
    assert executed.returncode == 0, executed.stderr
    assert executed.stdout == "42\n"


@pytest.mark.integration
@pytest.mark.parametrize("gc_backend", range(5))
def test_pcc1_native_deferred_link_entry_executes(
    tmp_path, native_pcc1_compiler, gc_backend,
):
    from pcc.backend.arm64_asm_driver import assemble_file
    from pcc.backend.native_object import NativeObject, encode_native_object
    from tests.python.process_timeout import run_process_group_timeout

    sections, undefined = assemble_file(
        ".section __TEXT,__text,regular,pure_instructions\n"
        ".globl _main\n.p2align 2\n_main:\n movz w0, #42\n ret\n"
    )
    packed = tmp_path / "main.pco"
    packed.write_bytes(encode_native_object(NativeObject.from_sections(sections, undefined=undefined)))
    inputs = tmp_path / "inputs"
    inputs.write_text("pcc.macho-internal-inputs.v1\n1\nPCO\t" + str(packed) + "\n")
    output = tmp_path / "out"
    plan = tmp_path / "link.plan"
    plan.write_text("\n".join([
        "pcc.deferred-self-link.v1", str(output), "", str(inputs),
        str(tmp_path / "profile"), "", "0",
    ]) + "\n")
    env = dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc_backend),
               PCC_HOST_PYTHON="/usr/bin/false", PCC_HOST_PCC="/usr/bin/false")
    linked = run_process_group_timeout(
        [str(native_pcc1_compiler), "--pcc-native-deferred-worker", str(plan)],
        env=env, timeout=30,
    )
    assert linked.returncode == 0, linked.stderr
    executed = subprocess.run([str(output)], env=env, capture_output=True, timeout=10)
    assert executed.returncode == 42, executed.stderr
