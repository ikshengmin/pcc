"""Pure command/report controls; actual native/xdist gates run in CI."""

import ast
import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import weakref

import pytest

from scripts import ci_worker_size_prior_gate as gate
from tests.integration.test_worker_size_prior_closed_world import require_definition

ROOT = Path(__file__).resolve().parents[1]


def reports(directory, nodes):
    rows = [{"event": "start", "collect_only": False, "override_ini": []},
            {"event": "collected", "nodeids": nodes}]
    rows.extend({"event": "report", "nodeid": node, "when": when, "outcome": "passed"}
                for node in nodes for when in ("setup", "call", "teardown"))
    rows.append({"event": "finish", "exitstatus": 0, "testsfailed": 0, "testscollected": len(nodes)})
    noun = "item" if len(nodes) == 1 else "items"
    (directory / "target.stdout").write_text(
        "created: 6/6 workers\n6 workers [" + str(len(nodes)) + " " + noun
        + "]\nscheduling tests via LoadGroupScheduling\n")
    return rows


def put(directory, rows):
    (directory / "live.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_default_command_retains_project_xdist(tmp_path):
    assert gate.HOST_FILES[-2:] == (
        "tests/python/test_host_indexed_process_split.py",
        "tests/python/test_dynamic_handoff_slots.py",
    )
    assert gate.HOST_FILES[4] == "tests/python/test_worker_guard_cadence.py"
    assert gate.HOST_FILES[5] == "tests/c/test_self_backend_function_body_lines.py"
    assert gate.HOST_FILES[6] == "tests/python/test_compiled_default_pass_tier.py"
    assert gate.HOST_FILES[7] == "tests/python/test_owned_mem2reg_frontiers.py"
    assert gate.HOST_FILES[8] == "tests/c/test_self_backend_text_index.py"
    assert gate.HOST_FILES[9] == "tests/c/test_self_backend_type_parser.py"
    assert gate.HOST_COUNTS == (80, 3, 145, 0, 4, 29, 25, 7, 11, 49, 20, 21)
    command = gate.pytest_command(tmp_path, gate.HOST_FILES)
    assert command[:4] == [sys.executable, "-m", "pytest", "-x"]
    assert command[-len(gate.HOST_FILES):] == list(gate.HOST_FILES)
    assert not any(arg in ("-o", "-n", "-n0", "--dist", "--override-ini")
                   or arg.startswith(("addopts=", "--numprocesses", "--dist=")) for arg in command)
    assert command.count("-m") == 1


def test_integration_selects_one_owner_without_changing_workers(tmp_path):
    node = gate.PROCESS_TEST + "::test_size_prior_native_processes_five_collectors[pcc0]"
    command = gate.pytest_command(tmp_path, [node], integration=True)
    assert command[-3:] == ["-m", "integration", node]
    assert "-n0" not in command and "-o" not in command


@pytest.mark.parametrize(("nodes", "summary"), [
    (["a.py::one"], "6 workers [1 item]"),
    (["a.py::one", "b.py::two"], "6 workers [2 items]"),
])
def test_exact_six_worker_reports_are_required(tmp_path, nodes, summary):
    put(tmp_path, reports(tmp_path, nodes))
    # Literal xdist output keeps the fixture from repeating a parser spelling bug.
    (tmp_path / "target.stdout").write_text(
        "created: 6/6 workers\n" + summary + "\nscheduling tests via LoadGroupScheduling\n")
    assert gate.verify_pytest(tmp_path, nodes) == nodes
    assert gate.verify_pytest(tmp_path, [node.split("::")[0] for node in nodes],
                              [1] * len(nodes)) == nodes


@pytest.mark.parametrize("summary", [
    "5 workers [1 item]", "16 workers [1 item]", "6 workers [0 items]",
    "6 workers [2 items]", "6 workers [1 items]", "6 workers [1 item] extra",
    "prefix 6 workers [1 item]", "",
])
def test_worker_summary_requires_exact_worker_count_item_count_and_line(tmp_path, summary):
    nodes = ["a.py::one"]
    put(tmp_path, reports(tmp_path, nodes))
    (tmp_path / "target.stdout").write_text(
        "created: 6/6 workers\n" + summary + "\nscheduling tests via LoadGroupScheduling\n")
    with pytest.raises(AssertionError):
        gate.verify_pytest(tmp_path, nodes)


@pytest.mark.parametrize("fault", ["collect-only", "override", "missing", "duplicate", "failed",
                                   "skip", "xfail", "no-call", "no-finish", "five-workers", "wrong-count",
                                   "wrong-scheduler", "exit-status", "failure-count"])
def test_no_partial_or_serial_run_can_pass(tmp_path, fault):
    nodes = ["a.py::one"]
    rows = reports(tmp_path, nodes)
    if fault == "collect-only": rows[0]["collect_only"] = True
    elif fault == "override": rows[0]["override_ini"] = ["addopts="]
    elif fault == "missing": rows[1]["nodeids"] = []
    elif fault == "duplicate": rows[1]["nodeids"] = nodes + nodes
    elif fault == "failed": rows[3]["outcome"] = "failed"
    elif fault == "skip": rows[3]["outcome"] = "skipped"
    elif fault == "xfail": rows[3]["wasxfail"] = "not allowed"
    elif fault == "no-call": rows.pop(3)
    elif fault == "no-finish": rows.pop()
    elif fault in ("five-workers", "wrong-scheduler"):
        path = tmp_path / "target.stdout"
        before, after = (("created: 6/6 workers", "created: 5/5 workers") if fault == "five-workers"
                         else ("LoadGroupScheduling", "LoadScheduling"))
        path.write_text(path.read_text().replace(before, after))
    elif fault == "exit-status": rows[-1]["exitstatus"] = 1
    elif fault == "failure-count": rows[-1]["testsfailed"] = 1
    else: rows[-1]["testscollected"] = 2
    put(tmp_path, rows)
    with pytest.raises(AssertionError):
        gate.verify_pytest(tmp_path, nodes)


@pytest.mark.parametrize("replacement", [
    "", "declare ptr @user_test()\n", "define ptr @user_test() {\nentry:\n ret ptr null\n}",
    "define ptr @user_test() {\nstrict.nolib.stub:\n call void @x()\n ret ptr null\n}",
    "define ptr @user_test() {\nentry:\n call ptr @py_cpy_import(ptr null)\n ret ptr null\n}",
    "define ptr @user_test() {\nentry:\n call ptr @py_exc_new(i64 11, ptr null)\n ret ptr null\n}",
])
def test_strict_body_rejects_fallback_or_missing_definition(replacement):
    following = "\ndefine ptr @user_next() {\nentry:\n call void @x()\n ret ptr null\n}\n"
    with pytest.raises(AssertionError):
        require_definition(replacement + following, "user_test")


def load_file(name, path, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("enabled", [False, True])
def test_windows_default_collection_never_imports_fcntl_and_autoclean_fails_closed(monkeypatch, enabled):
    import builtins
    original = builtins.__import__
    imports = []
    def importing(name, *args, **kwargs):
        if name == "fcntl":
            imports.append(name)
            raise AssertionError("Windows has no fcntl")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", importing)
    module = load_file("prior_root_conftest", ROOT / "conftest.py", monkeypatch)
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "_auto_clean_enabled", lambda: enabled)
    config = SimpleNamespace(option=SimpleNamespace(numprocesses=0))
    if enabled:
        with pytest.raises(RuntimeError, match="POSIX shared file locks"):
            module.pytest_configure(config)
    else:
        module.pytest_configure(config)
    assert imports == []


def load_platform(monkeypatch):
    # This test verifies orchestration, not ctypes/Job Object behavior. The real
    # platform guard is executed by premerge CI; no FFI is imported in this unit.
    watchdog = ModuleType("scripts.platform_process_watchdog")
    watchdog.run = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "scripts.platform_process_watchdog", watchdog)
    return load_file("prior_platform_bootstrap", ROOT / "scripts/bootstrap_platform.py", monkeypatch)


@pytest.mark.parametrize("mismatch", ["source", "codegen", "missing"])
def test_explicit_runtime_rejection_never_builds_or_launches(tmp_path, monkeypatch, mismatch):
    module = load_platform(monkeypatch)
    from pcc.frontends.python import owned_runtime_build as owner, pipeline_targets
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "source_identity", lambda *_args: {"commit": "fixed"})
    monkeypatch.setattr(module, "build_configuration", lambda target: {"target": target})
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: "x86_64-unknown-linux-gnu")
    def reject(*args, **kwargs):
        assert kwargs == {"explicit_archive": str(tmp_path / "input.a")}
        raise ValueError(mismatch)
    monkeypatch.setattr(owner, "ensure_target_runtime", reject)
    calls = []
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match=mismatch):
        module.run_chain("0", tmp_path / "out", stage_limit=1, lock_held=True,
                         runtime_archive=tmp_path / "input.a")
    assert calls == []


def test_explicit_runtime_cli_reaches_existing_matrix_owner(tmp_path, monkeypatch):
    module = load_platform(monkeypatch)
    calls = []
    monkeypatch.setattr(module, "run_matrix", lambda *args, **kwargs: calls.append((args, kwargs)))
    module.main(["--gc", "0", "--stage", "1", "--out-dir", str(tmp_path),
                 "--runtime-archive", "source-matched.a", "--inside-matrix", "--lock-held"])
    assert calls[0][1]["runtime_archive"] == "source-matched.a"
    assert calls[0][1]["timeout"] == 2400 and calls[0][1]["rss_limit"] == 17179869184


def test_live_exclusive_has_one_existing_format_diagnostic():
    path = ROOT / "pcc/frontends/python/worker_process_pool.py"
    tree = ast.parse(path.read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "_resource_diagnostic"
             and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
             and node.args[1].value == "exclusive"]
    assert len(calls) == 1 and len(calls[0].args) == 8


def test_verified_explicit_runtime_is_reused_without_build(tmp_path, monkeypatch):
    module = load_platform(monkeypatch)
    from pcc.frontends.python import owned_runtime_build as owner, pipeline_targets
    archive = tmp_path / "libpy_runtime_pcc_py.a"
    archive.write_bytes(b"frozen-runtime")
    Path(str(archive) + ".provenance.json").write_text("{}")
    Path(str(archive) + ".capi_syms").write_text("py_test\n")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "source_identity", lambda *_args: {"commit": "fixed"})
    monkeypatch.setattr(module, "build_configuration", lambda target: {"target": target})
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: "x86_64-unknown-linux-gnu")
    monkeypatch.setattr(module, "dependency_receipt", lambda path: {"format": "ELF"})
    verified, launched = [], []
    def verify(runtime_root, target, **kwargs):
        verified.append((runtime_root, target, kwargs))
        return str(archive)
    monkeypatch.setattr(owner, "ensure_target_runtime", verify)
    def run(command, *, log_path, **kwargs):
        launched.append(command)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if "-o" in command:
            Path(command[command.index("-o") + 1]).write_bytes(b"native")
        else:
            log_path.write_text("0\n" + str(2 ** 80 + 42) + "\n")
        return {"seconds": 1, "peak_tree_rss": 1, "command": command, "log": str(log_path)}
    monkeypatch.setattr(module, "run", run)
    receipt = module.run_chain("0", tmp_path / "out", stage_limit=1, lock_held=True, runtime_archive=archive)
    assert verified == [(str(tmp_path / "pcc/runtime"), "x86_64-unknown-linux-gnu", {"explicit_archive": str(archive)})]
    assert not any("pcc.frontends.python.owned_runtime_build" in command for command in launched)
    assert receipt["runtime"]["path"] == str(archive) and len(launched) == 3


def event(event, phase, task, pid, stamp, indices, size=10):
    return ("pcc frontend admission event=" + event + " task=" + str(task) + " pid=" + str(pid)
            + " phase=" + repr(phase) + " indices=" + repr(indices) + " inputs=" + repr([size, size, 1, 1, 1, len(indices)])
            + " monotonic_s=" + str(stamp) + " mapping_offset=0\n")


def test_metrics_keep_wall_worker_time_retry_and_bytes_separate():
    text = (event("start", "export", 0, 1, 0, [0, 1], 30)
            + event("retire", "export", 0, 1, 1, [0, 1], 30)
            + event("start", "codegen", 0, 2, 1, [0], 10)
            + event("start", "codegen", 1, 3, 1, [1], 20)
            + event("cancel", "codegen", 1, 3, 2, [1], 20)
            + event("exclusive", "codegen", 0, 2, 2, [0], 10)
            + event("retire", "codegen", 0, 2, 4, [0], 10)
            + event("start", "codegen", 1, 4, 4, [1], 20)
            + event("retire", "codegen", 1, 4, 6, [1], 20))
    result = gate.admission_metrics(text + text, 6)
    assert result["worker_seconds"] == 7 and result["average_workers"] == 7 / 6
    assert result["exclusive_wall_s"] == 4
    assert result["source_bytes_total"] == result["source_bytes_complete"] == 30
    assert result["completed_modules"] == result["planned_modules"] == 2
    assert result["events"]["cancel"] == 1 and result["open_attempts"] == 0


def test_partial_metrics_never_count_unfinished_worker_or_source_as_complete():
    text = (event("start", "export", 0, 1, 0, [0], 10)
            + event("retire", "export", 0, 1, 1, [0], 10)
            + event("calibrate", "indexed-frontend", 0, 2, 1, [0], 10))
    result = gate.admission_metrics(text, 6)
    assert result["worker_seconds"] == 1 and result["open_attempts"] == 1
    assert result["source_bytes_complete"] == 0 and result["source_bytes_total"] == 10


def test_explicit_ambient_cap_cannot_override_automatic_budget(monkeypatch):
    monkeypatch.setenv("PCC_BOOTSTRAP_MAX_TREE_RSS_BYTES", "17179869184")
    with pytest.raises(AssertionError, match="explicit cap overrides"):
        gate.environment()


@pytest.mark.parametrize("fault", [None, "status", "returncode", "timeout_s", "cap", "peak", "cleanup"])
def test_actual_posix_guard_contract_is_required(monkeypatch, fault):
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform="darwin"))
    result = {"status": "COMPLETE", "returncode": 0, "timeout_s": 300,
              "max_tree_rss_bytes": 3 * 1024**3, "peak_tree_rss_bytes": 1024**3,
              "darwin_preflight_reserve_bytes": 536870912,
              "terminal_processes": [{"pid": 123, "scope": "last sample"}]}
    if fault == "status": result["status"] = "TIMEOUT"
    elif fault == "returncode": result["returncode"] = 1
    elif fault == "timeout_s": result["timeout_s"] = 301
    elif fault == "cap": result["max_tree_rss_bytes"] = 16 * 1024**3
    elif fault == "peak": result["peak_tree_rss_bytes"] = 4 * 1024**3
    elif fault == "cleanup": result["post_exit_cleanup_pids"] = [123]
    if fault is None:
        assert gate.verify_posix_guard(result, 300)["max_tree_rss_bytes"] == 3 * 1024**3
    else:
        with pytest.raises(AssertionError):
            gate.verify_posix_guard(result, 300)


def test_pcc1_compiler_is_rechecked_after_native_use(tmp_path, monkeypatch):
    binary = tmp_path / "pcc1"
    binary.write_bytes(b"original compiler")
    source, runtime = {"source": "fixed"}, {"runtime": "fixed"}
    stage = {"status": "PASS", "source": source, "runtime": runtime,
             "compiler": {"path": str(binary), "sha256": gate.sha(binary)}}
    gate.save(tmp_path / "preflight.json", {"status": "PASS", "source": source, "runtime": runtime})
    gate.save(tmp_path / "stage1.json", stage)
    monkeypatch.setattr(gate, "source_identity", lambda: source)
    monkeypatch.setattr(gate, "environment", lambda: {})
    monkeypatch.setattr(gate, "runtime_identity", lambda archive: runtime)
    monkeypatch.setattr(gate, "native_gate", lambda *args: binary.write_bytes(b"replaced compiler"))
    with pytest.raises(AssertionError, match="pcc1 changed during use"):
        gate.run(tmp_path, "pcc1")
    assert json.loads((tmp_path / "pcc1.json").read_text())["status"] == "FAIL"


@pytest.mark.parametrize("kind", ["pco", "elf", "coff"])
def test_object_census_requires_code_definitions(monkeypatch, kind):
    from tests.integration.test_worker_size_prior_closed_world import require_object_symbols
    module_name, reader, target = {
        "pco": ("native_object", "decode_native_object", "arm64-apple-darwin"),
        "elf": ("elf_x86_64", "parse_relocatable", "x86_64-unknown-linux-gnu"),
        "coff": ("coff_x86_64", "parse_object", "x86_64-pc-windows-msvc"),
    }[kind]
    module = ModuleType("pcc.backend." + module_name)
    symbol = SimpleNamespace(name=("_" if kind == "pco" else "") + "user_test",
                             section=1, section_index=1, offset=0, value=0,
                             external=True, function=True, binding=1, type=2)
    section = SimpleNamespace(data=b"code", flags=0x20000004, segname="__TEXT", sectname="__text")
    obj = SimpleNamespace(machine=62, symbols=[symbol], sections=[section])
    setattr(module, reader, lambda payload: obj)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert require_object_symbols(b"reader-owned", target, ["user_test"])["required_defined_symbols"] == ["user_test"]
    obj.symbols = []
    with pytest.raises(AssertionError, match="missing/duplicate object definition"):
        require_object_symbols(b"reader-owned", target, ["user_test"])
    if kind == "elf":
        obj.symbols, obj.machine = [symbol], 183
        with pytest.raises(AssertionError, match="wrong ELF machine"):
            require_object_symbols(b"reader-owned", target, ["user_test"])


def test_mapping_pages_preserve_all_modules_and_count_scalars_once():
    indices = list(range(20))
    def pages(action, phase, task, pid, stamp):
        text = ""
        for offset in (0, 16):
            row = event(action, phase, task, pid, stamp, indices, 200)
            row = row.replace("indices=" + repr(indices), "indices=" + repr(indices[offset:offset + 16]))
            row = row.replace("mapping_offset=0", "mapping_offset=" + str(offset))
            text += row.rstrip() + " modules=" + repr(["m" + str(i) for i in indices[offset:offset + 16]]) + " module_count=20\n"
        return text
    text = (pages("start", "export", 0, 1, 0) + pages("retire", "export", 0, 1, 1)
            + pages("start", "codegen", 0, 2, 1) + pages("retire", "codegen", 0, 2, 3))
    result = gate.admission_metrics(text, 3)
    assert result["planned_modules"] == result["completed_modules"] == 20
    assert result["source_bytes_total"] == result["source_bytes_complete"] == 200
    assert result["events"]["start"] == result["events"]["retire"] == 2
    assert result["worker_seconds"] == 3
    with pytest.raises(AssertionError):
        gate.admission_metrics(text.replace("mapping_offset=16", "mapping_offset=17", 1), 3)



def test_preload_module_only_pages_keep_duration_without_source_double_count():
    text = ""
    for action, stamp in (("start", 0), ("retire", 1)):
        for offset in (0, 16):
            modules = ["m" + str(i) for i in range(offset, min(offset + 16, 20))]
            row = event(action, "preload-delta", 0, 1, stamp, [])
            row = row.replace("mapping_offset=0", "mapping_offset=" + str(offset))
            text += row.rstrip() + " modules=" + repr(modules) + " module_count=20\n"
    text += (event("start", "export", 0, 2, 1, [0]) + event("retire", "export", 0, 2, 2, [0])
             + event("start", "codegen", 0, 3, 2, [0]) + event("retire", "codegen", 0, 3, 4, [0]))
    result = gate.admission_metrics(text, 4)
    assert result["worker_seconds"] == 4 and result["events"]["retire"] == 3
    assert result["planned_modules"] == result["completed_modules"] == 1
    assert result["source_bytes_total"] == result["source_bytes_complete"] == 10


def test_workflow_reuses_existing_jobs_in_required_order_and_limits():
    text = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    for path in (*gate.HOST_FILES, "tests/fixtures/native/worker_size_priors.py",
                 "scripts/ci_worker_size_prior_gate.py", "conftest.py", "uv.lock", ".python-version"):
        assert text.count('      - "' + path + '"') == 2, path
    other = text.split("  other-platform-native-wheel:", 1)[1].split("  pcc1-package-parity:", 1)[0]
    mac = text.split("  pcc1-package-parity:", 1)[1]
    assert "timeout-minutes: 360" in other and "timeout-minutes: 45" in mac
    assert other.count("- { platform:") == 3
    assert "uv sync --frozen --dev --python 3.13" in other
    assert "uv sync --frozen --dev\n" in mac
    for body in (other, mac):
        assert body.index("--phase preflight") < body.index("--phase stage1") < body.index("--phase pcc1")
        assert body.count("--phase preflight") == body.count("--phase stage1") == body.count("--phase pcc1") == 1
        assert "build/worker-prior-gate/**/*.json" in body
    for name in ("macos-pcc0-regressions", "macos-pcc0-float-doc-regressions", "macos-pcc0-indexed-split"):
        match = re.search(r"(?ms)^  " + name + r":\n(.*?)(?=^  [A-Za-z0-9_-]+:|\Z)", text)
        assert match is not None
        body = match.group(1)
        assert "timeout-minutes: 25" in body and "scripts/ci_macos_regression_gate.py" in body
        assert "ci_worker_size_prior_gate.py" not in body
    assert "PCC_BOOTSTRAP_STAGE_TIMEOUT: \"2400\"" in mac
    assert "PCC_BOOTSTRAP_AUTO_TREE_RSS_CEILING_BYTES: \"4294967296\"" in mac


def test_other_platform_entry_keeps_the_original_full_gc_matrix_once():
    tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    commands = [node for node in ast.walk(tree) if isinstance(node, ast.List)
                and any(isinstance(item, ast.Constant) and item.value == "scripts/bootstrap_platform.py" for item in node.elts)]
    assert len(commands) == 1
    constants = [item.value for item in commands[0].elts if isinstance(item, ast.Constant)]
    assert constants[:5] == ["scripts/bootstrap_platform.py", "--gc", "all", "--stage", "3"]
    assert "--runtime-archive" in constants and "2400" in constants and "17179869184" in constants


@pytest.mark.parametrize("target,machine,failing", [
    ("win32", "x86_64", None), ("linux", "x86_64", None), ("darwin", "arm64", None),
    ("win32", "x86_64", "windows-exit-host"), ("win32", "x86_64", "windows-exit-native"),
    ("linux", "aarch64", None), ("linux", "arm64", None),
    ("linux", "aarch64", "linux-arm-transport-host"),
    ("linux", "aarch64", "linux-arm-transport-routes"),
    ("linux", "aarch64", "linux-arm-transport-native"),
    ("linux", "aarch64", "runtime-identity"),
    ("darwin", "arm64", "aarch64-regalloc-host"),
    ("darwin", "arm64", "aarch64-regalloc-native"),
    ("linux", "x86_64", "linux-elf-format"),
    ("linux", "x86_64", "linux-elf-staging"),
    ("linux", "x86_64", "linux-elf-owner"),
    ("linux", "aarch64", "linux-elf-format"),
    ("linux", "aarch64", "linux-elf-staging"),
    ("linux", "aarch64", "linux-elf-owner"),
])
def test_platform_gates_follow_verified_runtime_and_stop_on_failure(tmp_path, monkeypatch, target, machine, failing):
    out = tmp_path / "preflight"
    archive = out / "runtime/libpy_runtime_pcc_py.a"
    source, runtime = {"source": "fixed"}, {"runtime": "matched"}
    events = []
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform=target, executable="chosen-python"))
    monkeypatch.setattr(gate, "platform", SimpleNamespace(machine=lambda: machine))
    monkeypatch.setattr(gate, "source_identity", lambda: source)
    monkeypatch.setattr(gate, "environment", lambda: {"PCC_RUNTIME_ARCHIVE": "stale", "PCC_TEST_NO_NATIVE_PROVISIONING": "1"})

    def guarded(command, directory, timeout, env):
        assert command == ["chosen-python", "-m", "pcc.frontends.python.owned_runtime_build", "--output", str(archive)]
        assert directory == out / "runtime-build" and timeout == 1200
        assert "PCC_RUNTIME_ARCHIVE" not in env and "PCC_TEST_NO_NATIVE_PROVISIONING" not in env
        events.append("runtime-build")

    def identity(path):
        assert path == archive
        events.append("runtime-identity")
        if failing == "runtime-identity":
            raise RuntimeError("original platform gate failure")
        return runtime

    def pytest_gate(directory, name, nodes, env, *, integration=False, counts=None):
        assert directory == out
        events.append(name)
        if name.startswith("windows-exit-"):
            assert target == "win32" and nodes == [gate.EXIT_TEST]
            assert env["PCC_RUNTIME_ARCHIVE"] == str(archive)
            assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
            assert integration == (name == "windows-exit-native")
            assert counts == ((6,) if integration else (8,))
            assert "runtime-identity" in events
        elif name.startswith("linux-arm-transport-"):
            assert target == "linux" and machine in ("aarch64", "arm64")
            assert env["PCC_RUNTIME_ARCHIVE"] == str(archive)
            assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
            assert "runtime-identity" in events
            assert integration == (name != "linux-arm-transport-host")
            if name == "linux-arm-transport-native":
                assert nodes == [gate.ARM_TRANSPORT_NATIVE_TEST
                                 + "::test_linux_aarch64_transport_executes_tls_varargs_and_managed_reload"]
                assert counts is None
            else:
                assert nodes == [gate.ARM_TRANSPORT_TEST]
                assert counts == ((8,) if integration else (7,))
        elif name.startswith("aarch64-regalloc-"):
            assert target == "darwin" and machine == "arm64"
            assert env["PCC_RUNTIME_ARCHIVE"] == str(archive)
            assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
            assert "runtime-identity" in events
            assert not integration and counts is None
            assert nodes == (gate.REGALLOC_HOST_NODES if name.endswith("-host")
                             else gate.REGALLOC_NATIVE_NODES)
        elif name.startswith("linux-elf-"):
            assert target == "linux" and not integration
            assert env["PCC_RUNTIME_ARCHIVE"] == str(archive)
            assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
            assert "runtime-identity" in events
            if name == "linux-elf-format":
                assert nodes == [gate.ELF_FORMAT_TEST] and counts == (31,)
            elif name == "linux-elf-staging":
                assert nodes == [gate.ELF_STAGING_TEST]
                assert counts == ((69,) if machine in ("x86_64", "amd64") else (67,))
            else:
                assert name == "linux-elf-owner"
                assert nodes == [gate.ELF_OWNER_NODE] and counts is None
        elif name == "default-xdist":
            assert nodes == gate.HOST_FILES and counts == (80, 3, 145, 0, 4, 29, 25, 7, 11, 49, 20, 21) and not integration
        else:
            assert name == "strict-closure" and integration
        if name == failing:
            raise RuntimeError("original platform gate failure")

    def native(directory, owner, env):
        assert directory == out and owner == "pcc0"
        assert env["PCC_RUNTIME_ARCHIVE"] == str(archive)
        events.append("native-pcc0")

    monkeypatch.setattr(gate, "guarded", guarded)
    monkeypatch.setattr(gate, "runtime_identity", identity)
    monkeypatch.setattr(gate, "run_pytest", pytest_gate)
    monkeypatch.setattr(gate, "native_gate", native)
    expected = ["default-xdist", "runtime-build", "runtime-identity"]
    if target == "darwin":
        expected += ["aarch64-regalloc-host", "aarch64-regalloc-native"]
    if target == "win32":
        expected += ["windows-exit-host", "windows-exit-native"]
    elif target == "linux" and machine in ("aarch64", "arm64"):
        expected += ["linux-arm-transport-host", "linux-arm-transport-routes", "linux-arm-transport-native"]
    if target == "linux":
        expected += ["linux-elf-format", "linux-elf-staging", "linux-elf-owner"]
    expected += ["native-pcc0", "strict-closure", "runtime-identity"]
    if failing:
        with pytest.raises(RuntimeError, match="original platform gate failure"):
            gate.run(out, "preflight")
        expected = expected[:expected.index(failing) + 1]
    else:
        gate.run(out, "preflight")
    assert events == expected
    receipt = json.loads((out / "preflight.json").read_text())
    assert receipt["status"] == ("FAIL" if failing else "PASS")
    assert receipt["source"] == source
    if failing == "runtime-identity":
        assert "runtime" not in receipt
    else:
        assert receipt["runtime"] == runtime


def test_windows_exit_changed_shape_inputs_are_bound_and_trigger_ci():
    assert gate.EXIT_INPUTS == (
        "tests/python/test_owned_windows_exit.py",
        "tests/c/test_owned_linux_c_exports.py",
        "tests/c/fixtures/owned_linux_c_exports/exit_lifecycle.c",
        "tests/c/fixtures/owned_linux_c_exports/immediate_bypass.c",
    )
    tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    identity = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "source_identity")
    hashes = next(node.value for node in identity.body if isinstance(node, ast.Assign)
                  and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].slice, ast.Constant)
                  and node.targets[0].slice.value == "test_inputs")
    assert isinstance(hashes, ast.DictComp)
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "EXIT_INPUTS" for node in hashes.generators[0].iter.elts)
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    for name in gate.EXIT_INPUTS:
        assert workflow.count('      - "' + name + '"') == 2


def test_linux_arm_transport_inputs_markers_and_exact_inventory_are_bound():
    assert gate.ARM_TRANSPORT_INPUTS == (
        "tests/python/test_linux_aarch64_worker_transport.py",
        "tests/python/test_linux_aarch64_transport_native.py",
    )
    tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    identity = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "source_identity")
    hashes = next(node.value for node in identity.body if isinstance(node, ast.Assign)
                  and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].slice, ast.Constant)
                  and node.targets[0].slice.value == "test_inputs")
    assert isinstance(hashes, ast.DictComp)
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "ARM_TRANSPORT_INPUTS" for node in hashes.generators[0].iter.elts)
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    for name in gate.ARM_TRANSPORT_INPUTS:
        assert workflow.count('      - "' + name + '"') == 2

    # Read source only: this control must never import or emit the target IR.
    worker = ast.parse((ROOT / gate.ARM_TRANSPORT_TEST).read_text())
    counts = {False: 0, True: 0}
    for node in worker.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        integration = any(isinstance(mark, ast.Attribute) and mark.attr == "integration"
                          for mark in node.decorator_list)
        count = 1
        for mark in node.decorator_list:
            if isinstance(mark, ast.Call) and isinstance(mark.func, ast.Attribute) and mark.func.attr == "parametrize":
                assert isinstance(mark.args[1], (ast.List, ast.Tuple))
                count *= len(mark.args[1].elts)
        counts[integration] += count
    assert counts == {False: 7, True: 8}
    native = ast.parse((ROOT / gate.ARM_TRANSPORT_NATIVE_TEST).read_text())
    tests = [node for node in native.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
    assert [node.name for node in tests] == ["test_linux_aarch64_transport_executes_tls_varargs_and_managed_reload"]
    assert any(isinstance(mark, ast.Attribute) and mark.attr == "integration"
               for mark in tests[0].decorator_list)
    assert any(isinstance(mark, ast.Call) and isinstance(mark.func, ast.Attribute) and mark.func.attr == "pcc_gate"
               for mark in tests[0].decorator_list)


def test_aarch64_regalloc_inputs_and_exact_nodes_are_bound(tmp_path):
    assert len(gate.REGALLOC_HOST_NODES) == 80
    assert len(set(gate.REGALLOC_HOST_NODES)) == 80
    assert len(gate.REGALLOC_NATIVE_NODES) == 3
    assert gate.REGALLOC_NATIVE_NODES == tuple(
        "tests/c/test_self_backend_aarch64_regalloc.py::"
        "test_aarch64_call_result_executes_through_indexed_emission[" + mode + "]"
        for mode in ("default", "function", "local")
    )
    tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    identity = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "source_identity")
    hashes = next(node.value for node in identity.body if isinstance(node, ast.Assign)
                  and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].slice, ast.Constant)
                  and node.targets[0].slice.value == "test_inputs")
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "REGALLOC_INPUTS" for node in hashes.generators[0].iter.elts)
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    for path in gate.REGALLOC_INPUTS:
        assert workflow.count('      - "' + path + '"') == 2
    for nodes in (gate.REGALLOC_HOST_NODES, gate.REGALLOC_NATIVE_NODES):
        command = gate.pytest_command(tmp_path, nodes)
        assert command[-len(nodes):] == list(nodes)
        assert command.count("-m") == 1
        assert not any(arg in ("-o", "-n", "-n0", "--dist", "--override-ini")
                       or arg.startswith(("addopts=", "--numprocesses", "--dist=")) for arg in command)


@pytest.mark.parametrize("nodes", (gate.REGALLOC_HOST_NODES, gate.REGALLOC_NATIVE_NODES))
def test_aarch64_regalloc_gates_keep_tree_and_execution_bounds(tmp_path, monkeypatch, nodes):
    calls = []
    monkeypatch.setattr(gate, "guarded", lambda command, directory, timeout, env:
                        calls.append((command, directory, timeout, env)))
    monkeypatch.setattr(gate, "verify_pytest", lambda directory, expected, counts:
                        (directory, expected, counts))
    environment = {"unchanged": "yes"}
    actual = gate.run_pytest(tmp_path, "regalloc", nodes, environment)
    assert actual == (tmp_path / "regalloc", nodes, None)
    assert calls == [(gate.pytest_command(tmp_path / "regalloc", nodes),
                      tmp_path / "regalloc", 300, environment)]
    source = ast.parse((ROOT / gate.REGALLOC_TEST).read_text())
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                    and node.name == "test_aarch64_call_result_executes_through_indexed_emission")
    runs = [node for node in ast.walk(function) if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "subprocess.run"]
    assert len(runs) == 1
    assert any(keyword.arg == "timeout" and isinstance(keyword.value, ast.Constant)
               and keyword.value.value == 10 for keyword in runs[0].keywords)



def test_strict_closure_explicitly_compiles_the_changed_regalloc_module():
    from tests.integration.test_worker_size_prior_closed_world import MODULES

    assert MODULES == tuple("pcc.frontends.python." + name for name in (
        "worker_resource_plan", "pipeline_frontend_workers",
        "pipeline_frontend_indexed_stage", "pipeline_indexed_handoff", "worker_process_pool",
    )) + ("pcc.backend.self_backend_aarch64_darwin_regalloc",
          "pcc.backend.owned_elf_inputs", "pcc.backend.elf_x86_64")
    source = ROOT / ("pcc.backend.self_backend_aarch64_darwin_regalloc".replace(".", "/") + ".py")
    definitions = [node.name for node in ast.parse(source.read_text()).body
                   if isinstance(node, ast.FunctionDef)]
    assert "allocate_aarch64_block_registers" in definitions
    gate_tree = ast.parse((ROOT / gate.CLOSURE_TEST).read_text())
    closure = next(node for node in gate_tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "test_worker_size_prior_modules_strict_target_emission")
    module_loop = next(node for node in ast.walk(closure) if isinstance(node, ast.For)
                       and ast.unparse(node.iter) == "zip(MODULES, sources)")
    calls = {ast.unparse(node) for node in ast.walk(module_loop) if isinstance(node, ast.Call)}
    assert "_index_definition_bodies(section)" in calls
    assert "require_definition(section, symbol, bodies=definition_bodies)" in calls
    assert "verify_ir_text(section)" in calls
    assert "require_object_symbols(obj.read_bytes(), target, required)" in calls
    assert "emit_indexed_module_file(str(sidecar), str(obj), 'PCO', optimize=False)" in calls


def test_elf_input_sources_strict_modules_and_workflow_are_bound():
    from tests.integration.test_worker_size_prior_closed_world import MODULES

    assert gate.ELF_INPUTS == (
        "tests/python/test_elf_x86_64.py",
        "tests/python/test_owned_elf_staging.py",
        "tests/python/test_pipeline_self_backend_link_owner.py",
        "pcc/backend/owned_elf_inputs.py", "pcc/backend/elf_x86_64.py",
    )
    assert gate.ELF_OWNER_NODE == (
        "tests/python/test_pipeline_self_backend_link_owner.py::"
        "test_linux_pcc_link_route_uses_owned_elf_driver_and_internal_assembly"
    )
    assert MODULES[-2:] == ("pcc.backend.owned_elf_inputs", "pcc.backend.elf_x86_64")
    tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    identity = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "source_identity")
    hashes = next(node.value for node in identity.body if isinstance(node, ast.Assign)
                  and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].slice, ast.Constant)
                  and node.targets[0].slice.value == "test_inputs")
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "ELF_INPUTS" for node in hashes.generators[0].iter.elts)
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    for path in gate.ELF_INPUTS:
        assert workflow.count('      - "' + path + '"') == 2, path
    # This control reads the final test source; it neither imports the ELF
    # test modules nor calls their object emitters or native probes.
    for path, expected, native_count in ((gate.ELF_FORMAT_TEST, 31, 0),
                                          (gate.ELF_STAGING_TEST, 69, 2)):
        total = gated = 0
        for node in ast.parse((ROOT / path).read_text()).body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            count = 1
            is_gated = False
            for mark in node.decorator_list:
                assert not (isinstance(mark, ast.Attribute) and mark.attr == "integration")
                if isinstance(mark, ast.Call) and isinstance(mark.func, ast.Attribute):
                    if mark.func.attr == "parametrize":
                        assert isinstance(mark.args[1], (ast.List, ast.Tuple))
                        count *= len(mark.args[1].elts)
                    elif mark.func.attr == "pcc_gate":
                        is_gated = True
                        assert node.name == "test_staged_metadata_image_executes_data_and_bss_relocations"
            total += count
            if is_gated:
                gated += count
        assert (total, gated) == (expected, native_count)


@pytest.mark.parametrize("name,nodes,counts", [
    ("linux-elf-format", [gate.ELF_FORMAT_TEST], (31,)),
    ("linux-elf-staging", [gate.ELF_STAGING_TEST], (69,)),
    ("linux-elf-staging", [gate.ELF_STAGING_TEST], (67,)),
    ("linux-elf-owner", [gate.ELF_OWNER_NODE], None),
])
def test_elf_runtime_slices_keep_original_workers_and_bounds(tmp_path, monkeypatch, name, nodes, counts):
    calls = []
    monkeypatch.setattr(gate, "guarded", lambda command, directory, timeout, env:
                        calls.append((command, directory, timeout, env)))
    monkeypatch.setattr(gate, "verify_pytest", lambda directory, expected, file_counts:
                        (directory, expected, file_counts))
    environment = {"PCC_RUNTIME_ARCHIVE": "verified.a", "PCC_TEST_NO_NATIVE_PROVISIONING": "1"}
    actual = gate.run_pytest(tmp_path, name, nodes, environment, counts=counts)
    assert actual == (tmp_path / name, nodes, counts)
    command = gate.pytest_command(tmp_path / name, nodes)
    assert command[-len(nodes):] == nodes and command.count("-m") == 1
    assert not any(arg in ("-o", "-n", "-n0", "--dist", "--override-ini")
                   or arg.startswith(("addopts=", "--numprocesses", "--dist=")) for arg in command)
    assert calls == [(command, tmp_path / name, 300, environment)]


@pytest.mark.parametrize("machine,expected", [("x86_64", 69), ("aarch64", 67)])
def test_elf_staging_collection_deselects_only_unavailable_native_cases(tmp_path, machine, expected):
    from tests import conftest as test_gates

    tree = ast.parse((ROOT / gate.ELF_STAGING_TEST).read_text())
    native = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "test_staged_metadata_image_executes_data_and_bss_relocations")
    marker = next(mark for mark in native.decorator_list if isinstance(mark, ast.Call)
                  and isinstance(mark.func, ast.Attribute) and mark.func.attr == "pcc_gate")
    predicate = next(keyword.value for keyword in marker.keywords if keyword.arg == "unavailable")
    assert isinstance(predicate, ast.IfExp)
    assert isinstance(predicate.body, ast.Constant) and predicate.body.value is None
    assert isinstance(predicate.orelse, ast.Constant) and predicate.orelse.value
    condition = ast.parse("sys.platform.startswith('linux') and platform.machine() in ('x86_64', 'amd64')",
                          mode="eval").body
    assert ast.dump(predicate.test) == ast.dump(condition)
    native_ids = [gate.ELF_STAGING_TEST + "::" + native.name + "[" + value + "]"
                  for value in ("False", "True")]
    ordinary_ids = [gate.ELF_STAGING_TEST + "::ordinary_" + str(index) for index in range(67)]
    reason = None if machine in ("x86_64", "amd64") else predicate.orelse.value
    items = []
    for node in ordinary_ids + native_ids:
        marks = (SimpleNamespace(kwargs={"unavailable": reason}),) if node in native_ids else ()
        items.append(SimpleNamespace(nodeid=node,
                                     iter_markers=lambda _name, marks=marks: iter(marks)))
    deselected = []
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)))
    # Exercise the real hook and report verifier with synthetic reports only.
    # No native test module, compiler, emitter, runtime or child is invoked.
    test_gates.pytest_collection_modifyitems(config, items)
    assert [item.nodeid for item in deselected] == (native_ids if expected == 67 else [])
    nodes = [item.nodeid for item in items]
    assert nodes == ordinary_ids + (native_ids if expected == 69 else [])
    rows = reports(tmp_path, nodes)
    put(tmp_path, rows)
    assert gate.verify_pytest(tmp_path, [gate.ELF_STAGING_TEST], (expected,)) == nodes
    rows[3]["outcome"] = "skipped"
    put(tmp_path, rows)
    with pytest.raises(AssertionError):
        gate.verify_pytest(tmp_path, [gate.ELF_STAGING_TEST], (expected,))
    if expected == 67:
        put(tmp_path, reports(tmp_path, ordinary_ids + native_ids))
        with pytest.raises(AssertionError):
            gate.verify_pytest(tmp_path, [gate.ELF_STAGING_TEST], (expected,))


# Frozen test-only controls for the original repeated scans.
def _original_require_definition(text, symbol):
    pattern = (r'^define[^\n]*@"?' + re.escape(symbol)
               + r'"?\([^\n]*\)[^\n]*\{\n(?P<body>.*?)^\}')
    matches = list(re.finditer(pattern, text, re.M | re.S))
    assert len(matches) == 1, (symbol, "missing/duplicate definition")
    body = matches[0].group("body")
    assert not re.search(r"^define\b", body, re.M), (symbol, "cross-function match")
    assert "strict.nolib" not in body and "@py_cpy_" not in body and '@"py_cpy_' not in body
    assert not re.search(r'@"?py_exc_new"?\(\s*i64\s+11\s*,', body), symbol
    assert "NotImplementedError" not in body, symbol
    assert re.search(r"\bcall\b", body), (symbol, "empty/return-only replacement")

def _original_require_object_symbols(payload, target, required):
    """Owned readers validate the emitted container and every required code symbol."""
    darwin, windows = "-apple-" in target, "-windows-" in target
    if darwin:
        from pcc.backend.native_object import decode_native_object
        obj = decode_native_object(payload)
        machine = "PCO has no machine field; verified PIDX target and Darwin emitter route"
    elif windows:
        from pcc.backend.coff_x86_64 import parse_object
        obj = parse_object(payload)  # The reader requires AMD64 machine 0x8664.
        machine = 0x8664
    else:
        from pcc.backend.elf_x86_64 import parse_relocatable
        obj = parse_relocatable(payload)
        machine = 62 if target.startswith("x86_64-") else 183
        assert obj.machine == machine, "wrong ELF machine"
    for name in required:
        symbols = [symbol for symbol in obj.symbols if symbol.name == ("_" if darwin else "") + name]
        assert len(symbols) == 1, (name, "missing/duplicate object definition")
        symbol = symbols[0]
        index = symbol.section if windows else symbol.section_index
        assert 1 <= index <= len(obj.sections), (name, "undefined object symbol")
        section = obj.sections[index - 1]
        offset = symbol.offset if darwin else symbol.value
        assert 0 <= offset < len(section.data), (name, "symbol outside code")
        if darwin:
            assert symbol.external and (section.segname, section.sectname) == ("__TEXT", "__text")
        elif windows:
            assert symbol.external and symbol.function and section.flags & 0x20000000
        else:
            assert symbol.binding == 1 and symbol.type == 2 and section.flags & 4
    return {"machine": machine, "required_defined_symbols": list(required)}


def _strict_check_result(check):
    try:
        return "return", check()
    except Exception as error:
        return "raise", type(error), str(error)


@pytest.mark.parametrize("shape", (
    "plain", "quoted", "duplicate", "mixed-duplicate", "missing", "empty",
    "stub", "cpy-bare", "cpy-quoted", "not-implemented", "exc11", "nested",
    "nested-and-later", "noncanonical-name", "multi-symbol-header", "multi-symbol-duplicate",
))
def test_strict_definition_index_matches_original_failures(shape):
    from tests.integration import test_worker_size_prior_closed_world as closure

    body = " call ptr @real()\n ret ptr null\n"
    text = "define ptr @user_test() {\n" + body + "}\n"
    symbol = "user_test"
    if shape == "quoted":
        text = text.replace("@user_test(", '@"user_test"(')
    elif shape == "duplicate":
        text *= 2
    elif shape == "mixed-duplicate":
        text += text.replace("@user_test(", '@"user_test"(')
    elif shape == "missing":
        symbol = "user_absent"
    elif shape == "empty":
        text = text.replace(" call ptr @real()\n", "")
    elif shape == "stub":
        text = text.replace(" call ptr", " strict.nolib.stub:\n call ptr")
    elif shape == "cpy-bare":
        text = text.replace("@real", "@py_cpy_import")
    elif shape == "cpy-quoted":
        text = text.replace("@real", '@"py_cpy_import"')
    elif shape == "not-implemented":
        text = text.replace("@real", "@NotImplementedError")
    elif shape == "exc11":
        text = text.replace("@real()", "@py_exc_new(i64 11, ptr null)")
    elif shape in ("nested", "nested-and-later"):
        text = "define ptr @outer() {\n" + text
        if shape == "nested-and-later":
            text += "define ptr @user_test() {\n" + body + "}\n"
        else:
            symbol = "outer"
    elif shape == "noncanonical-name":
        text = text.replace("user_test", "user-test")
        symbol = "user-test"
    elif shape in ("multi-symbol-header", "multi-symbol-duplicate"):
        ambiguous = text.replace("@user_test()", "@user_test(ptr @other())")
        text = ambiguous if shape == "multi-symbol-header" else text + ambiguous
    indexed = closure._index_definition_bodies(text)
    assert _strict_check_result(lambda: closure.require_definition(text, symbol, bodies=indexed)) == (
        _strict_check_result(lambda: _original_require_definition(text, symbol))
    )


@pytest.mark.parametrize("kind", ("pco", "elf", "coff"))
def test_strict_symbol_index_matches_original_failures(monkeypatch, kind):
    from tests.integration.test_worker_size_prior_closed_world import require_object_symbols

    module_name, reader, target = {
        "pco": ("native_object", "decode_native_object", "arm64-apple-darwin"),
        "elf": ("elf_x86_64", "parse_relocatable", "x86_64-unknown-linux-gnu"),
        "coff": ("coff_x86_64", "parse_object", "x86_64-pc-windows-msvc"),
    }[kind]
    module = ModuleType("pcc.backend." + module_name)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    for fault in ("none", "missing", "duplicate", "undefined", "negative-offset",
                  "past-code", "private", "wrong-type", "wrong-section", "wrong-machine"):
        symbol = SimpleNamespace(name=("_" if kind == "pco" else "") + "user_test",
                                 section=1, section_index=1, offset=0, value=0,
                                 external=True, function=True, binding=1, type=2)
        section = SimpleNamespace(data=b"code", flags=0x20000004, segname="__TEXT", sectname="__text")
        obj = SimpleNamespace(machine=62, symbols=[symbol], sections=[section])
        if fault == "missing": obj.symbols = []
        elif fault == "duplicate": obj.symbols.append(symbol)
        elif fault == "undefined": symbol.section = symbol.section_index = 0
        elif fault == "negative-offset": symbol.offset = symbol.value = -1
        elif fault == "past-code": symbol.offset = symbol.value = len(section.data)
        elif fault == "private": symbol.external = False; symbol.binding = 0
        elif fault == "wrong-type": symbol.function = False; symbol.type = 0
        elif fault == "wrong-section": section.sectname = "__data"; section.flags = 0
        elif fault == "wrong-machine": obj.machine = 183
        setattr(module, reader, lambda payload: obj)
        actual = _strict_check_result(lambda: require_object_symbols(b"reader-owned", target, ["user_test"]))
        expected = _strict_check_result(lambda: _original_require_object_symbols(b"reader-owned", target, ["user_test"]))
        assert actual == expected, (kind, fault)


def test_strict_lookup_indexes_scan_each_input_once(monkeypatch):
    from tests.integration import test_worker_size_prior_closed_world as closure

    source = "".join("define ptr @user_" + name + "() {\n call ptr @real()\n ret ptr null\n}\n"
                     for name in ("first", "second"))
    pattern = closure._DEFINITION_BODY
    scans = []

    def finditer(text):
        scans.append(text)
        return pattern.finditer(text)

    monkeypatch.setattr(closure, "_DEFINITION_BODY", SimpleNamespace(finditer=finditer))
    bodies = closure._index_definition_bodies(source)

    def unexpected(*args, **kwargs):
        raise AssertionError("indexed definition performed another section scan")

    monkeypatch.setattr(closure.re, "finditer", unexpected)
    for name in ("first", "second"):
        closure.require_definition(source, "user_" + name, bodies=bodies)
    assert scans == [source]

    class CountedSymbols(list):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    symbols = CountedSymbols(SimpleNamespace(name="_user_" + name, section_index=1,
                                             offset=0, external=True)
                             for name in ("first", "second"))
    obj = SimpleNamespace(symbols=symbols, sections=[SimpleNamespace(
        data=b"code", segname="__TEXT", sectname="__text",
    )])
    reader = ModuleType("pcc.backend.native_object")
    reader.decode_native_object = lambda payload: obj
    monkeypatch.setitem(sys.modules, reader.__name__, reader)
    closure.require_object_symbols(b"reader-owned", "arm64-apple-darwin", ["user_first", "user_second"])
    assert symbols.iterations == 1


def test_strict_progress_is_atomic_bounded_and_keeps_identity(tmp_path, monkeypatch):
    from tests.integration import test_worker_size_prior_closed_world as closure

    path = tmp_path / "worker-size-prior-closed-world.json"
    receipt = {"status": "RUNNING", "target": "chosen-target", "sources": {"original": "sha"},
               "codegen_sha256": "original-codegen", "modules": {"done": {"object_sha256": "bytes"}}}
    replace = closure.os.replace
    publications = []

    def atomic_replace(temporary, destination):
        assert destination == path and temporary == path.with_name(path.name + ".tmp")
        payload = json.loads(temporary.read_text())
        if path.exists():
            assert json.loads(path.read_text()) == publications[-1]
        publications.append(payload)
        replace(temporary, destination)

    monkeypatch.setattr(closure.os, "replace", atomic_replace)
    monkeypatch.setattr(closure, "time", SimpleNamespace(monotonic=lambda: 101.25))
    for phase, module in (("compile", ""), ("whole-verify", ""),
                          ("whole-parse", ""), ("whole-verify-parsed", ""),
                          *((phase, name) for name in closure.MODULES
                            for phase in ("module-verify", "module-emit"))):
        for state in ("started", "complete"):
            closure._record_closed_world_progress(path, receipt, 100.0, phase, state, module)
            assert json.loads(path.read_text()) == receipt
            assert receipt["progress"][-1] == {"phase": phase, "state": state,
                                                "module": module, "elapsed_s": 1.25}
    assert len(publications) == 8 + 4 * len(closure.MODULES) == 40
    assert receipt["status"] == "RUNNING" and receipt["sources"] == {"original": "sha"}
    assert receipt["codegen_sha256"] == "original-codegen"
    assert receipt["modules"] == {"done": {"object_sha256": "bytes"}}
    assert not path.with_name(path.name + ".tmp").exists()
    previous = path.read_bytes()
    with pytest.raises(AssertionError, match="phase bound"):
        closure._record_closed_world_progress(path, receipt, 100.0, "compile", "started")
    assert path.read_bytes() == previous


@pytest.mark.parametrize("fault", ("oversize", "replace-failed"))
def test_strict_progress_preserves_last_complete_json_on_write_failure(tmp_path, monkeypatch, fault):
    from tests.integration import test_worker_size_prior_closed_world as closure

    path = tmp_path / "worker-size-prior-closed-world.json"
    previous = b'{"status": "RUNNING", "phase": "compile"}\n'
    path.write_bytes(previous)
    if fault == "oversize":
        monkeypatch.setattr(closure, "_RECEIPT_MAX_BYTES", 8)
        error = AssertionError
    else:
        def failed_replace(*args):
            raise OSError("synthetic replace failure")
        monkeypatch.setattr(closure.os, "replace", failed_replace)
        error = OSError
    with pytest.raises(error):
        closure._publish_closed_world_receipt(path, {"status": "PASS"})
    assert path.read_bytes() == previous
    assert not path.with_name(path.name + ".tmp").exists()


def test_strict_progress_surrounds_real_phases_and_uses_collected_receipt():
    source = (ROOT / gate.CLOSURE_TEST).read_text()
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "test_worker_size_prior_modules_strict_target_emission")
    events = []

    class Calls(ast.NodeVisitor):
        def visit_Lambda(self, node):
            # This callback is executed by the helper, not at construction.
            pass

        def visit_Call(self, node):
            name = ast.unparse(node.func)
            if name == "_record_closed_world_progress":
                events.append((ast.literal_eval(node.args[3]), ast.literal_eval(node.args[4])))
            elif name in ("compile_python_multi", "verify_ir_text", "encode_indexed_module_file",
                          "emit_indexed_module_file", "require_object_symbols"):
                events.append(name)
            self.generic_visit(node)

    Calls().visit(function)
    verify_calls = [node for node in ast.walk(function)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "verify_ir_text"]
    whole_call = next(node for node in verify_calls if ast.unparse(node.args[0]) == "text")
    section_call = next(node for node in verify_calls if ast.unparse(node.args[0]) == "section")
    assert not section_call.keywords
    assert [keyword.arg for keyword in whole_call.keywords] == ["progress"]
    callback = whole_call.keywords[0].value
    assert isinstance(callback, ast.Lambda)
    assert ast.unparse(callback.body) == (
        "_record_closed_world_progress(receipt_path, receipt, started, "
        "{'parse': 'whole-parse', 'verify': 'whole-verify-parsed'}[phase], state)"
    )
    assert events == [
        ("compile", "started"), "compile_python_multi", ("compile", "complete"),
        ("whole-verify", "started"), "verify_ir_text", ("whole-verify", "complete"),
        ("module-verify", "started"), "verify_ir_text", ("module-verify", "complete"),
        ("module-emit", "started"), "encode_indexed_module_file", "emit_indexed_module_file",
        "require_object_symbols", ("module-emit", "complete"),
    ]
    assert 'receipt_path = tmp_path / "worker-size-prior-closed-world.json"' in source
    finalizer = next(node for node in function.body if isinstance(node, ast.Try)).finalbody
    assert isinstance(finalizer[-1], ast.Try)
    assert ast.unparse(finalizer[-1].body[-1]) == "_publish_closed_world_receipt(receipt_path, receipt)"
    assert ast.unparse(finalizer[-1].handlers[0].body[0]) == "if not primary_failed:\n    raise"
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    assert workflow.count("build/worker-prior-gate/**/*.json\n") == 2
    helper_path = "tests/owned_ir_validation.py"
    assert workflow.count('      - "' + helper_path + '"') == 2
    gate_tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    identity = next(node for node in gate_tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "source_identity")
    hashes = next(node.value for node in ast.walk(identity) if isinstance(node, ast.Assign)
                  and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].slice, ast.Constant)
                  and node.targets[0].slice.value == "test_inputs")
    assert any(isinstance(node, ast.Constant) and node.value == helper_path
               for node in hashes.generators[0].iter.elts)
    command = gate.pytest_command(Path("build/worker-prior-gate/strict-closure"),
                                  [gate.CLOSURE_TEST + "::" + function.name], integration=True)
    assert command[command.index("--basetemp") + 1] == str(Path("build/worker-prior-gate/strict-closure") / "tests")
    assert "-n0" not in command and "-o" not in command


@pytest.mark.parametrize("failure", ("none", "compile", "compile-summary", "compile-publish", "parse", "verify",
                                     "parse-publish", "verify-publish", "encode", "emit", "publish"))
def test_strict_closure_releases_verified_inputs_before_next_consumer(tmp_path, monkeypatch, failure):
    import hashlib
    import weakref
    from tests.integration import test_worker_size_prior_closed_world as closure

    root, output = tmp_path / "source", tmp_path / "output"
    output.mkdir()
    names = ("pcc.frontends.python.mock_alpha", "pcc.frontends.python.mock_beta")
    dependency = "pcc.frontends.python.mock_dependency"
    for name in names:
        source = root / (name.replace(".", "/") + ".py")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("def entry():\n    return 1\n")
    monkeypatch.setattr(closure, "MODULES", names)
    monkeypatch.setattr(closure, "__file__", str(root / "tests/integration/closure.py"))
    target = "x86_64-unknown-linux-gnu"
    error_type = OSError if failure == "publish" else RuntimeError
    original_error = error_type("synthetic " + failure + " failure")
    events, whole_refs, section_refs, module_refs, index_refs = [], [], [], [], []
    symbols = {name: "user_" + name.replace(".", "_") + "_entry"
               for name in (*names, dependency)}
    text = "".join('; ---- module: ' + name + ' ----\n'
                   + 'define ptr @' + symbols[name] + '() {\n call ptr @real()\n ret ptr null\n}\n'
                   for name in (*names, dependency))

    class TrackedSection(str):
        pass

    class TrackedIR(str):
        def __getitem__(self, key):
            assert isinstance(key, slice)
            assert events == ["compile", "whole-verify"] or events[-1] == "slice"
            assert module_refs[0]() is None, "whole parsed module still owns its arenas while sections are copied"
            value = TrackedSection(super().__getitem__(key))
            section_refs.append(weakref.ref(value))
            events.append("slice")
            return value

    class Parsed:
        pass

    class DefinitionIndex(dict):
        pass

    read_text = Path.read_text

    def tracked_read(path, *args, **kwargs):
        if path == output / "closed-world.ll":
            value = TrackedIR(read_text(path, *args, **kwargs))
            whole_refs.append(weakref.ref(value))
            return value
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read)
    original_index = closure._index_definition_bodies

    def indexed(section):
        value = DefinitionIndex(original_index(section))
        index_refs.append(weakref.ref(value))
        return value

    monkeypatch.setattr(closure, "_index_definition_bodies", indexed)

    def compile_multi(paths, destination, **kwargs):
        assert paths == [str(root / (name.replace(".", "/") + ".py")) for name in names]
        assert kwargs["module_names"] == list(names) and kwargs["libpython_mode"] == "off"
        assert kwargs["emit_llvm_only"] and kwargs["backend"] == "self"
        events.append("compile")
        profile = kwargs["profile"]
        assert profile == {}
        profile.update(phase_totals_ms={"multi_type_infer": 20, "python_ir_pass_pipeline_many": 30},
                       counters={"multi_files": 3},
                       events=[{"name": "multi_type_infer", "detail": names[0], "ms": 20}])
        # Existing timers update memory only. The pre-call receipt remains a
        # valid pending snapshot until normal return or an ordinary exception.
        pending = json.loads((output / "worker-size-prior-closed-world.json").read_text())
        assert pending["progress"][-1]["state"] == "started"
        assert pending["compile_profile"]["complete"] is False
        assert pending["compile_profile"]["phase_totals_ms"] == {}
        if failure in ("compile-summary", "compile-publish"):
            def fail_reporting(*args, **kwargs):
                raise OSError("secondary reporting failure")
            monkeypatch.setattr(closure, "_bounded_compile_profile" if failure == "compile-summary"
                                else "_publish_closed_world_receipt", fail_reporting)
        if failure.startswith("compile"):
            raise original_error
        if failure == "publish":
            publish = closure._publish_closed_world_receipt

            def fail_final_publication(path, receipt):
                if receipt["status"] == "PASS":
                    raise original_error
                publish(path, receipt)

            monkeypatch.setattr(closure, "_publish_closed_world_receipt", fail_final_publication)
        Path(destination).write_text(text)

    def verify(value, *, progress=None):
        is_whole = isinstance(value, TrackedIR)
        if is_whole:
            assert not section_refs, "section copies overlap whole-module parsing"
            assert callable(progress)
            events.append("whole-verify")
            for phase in ("parse", "verify"):
                progress(phase, "started")
                pending = json.loads((output / "worker-size-prior-closed-world.json").read_text())
                assert pending["status"] == "RUNNING"
                assert pending["modules"] == {}
                assert pending["progress"][-1]["phase"] == (
                    "whole-parse" if phase == "parse" else "whole-verify-parsed"
                )
                assert pending["progress"][-1]["state"] == "started"
                if failure in (phase, phase + "-publish"):
                    if failure.endswith("-publish"):
                        def failed_final_report(*args, **kwargs):
                            raise OSError("secondary phase reporting failure")
                        monkeypatch.setattr(closure, "_publish_closed_world_receipt", failed_final_report)
                    raise original_error
                progress(phase, "complete")
        else:
            assert progress is None, "section validation unexpectedly publishes whole phases"
            assert len(section_refs) == len(names), "unselected closure sections were copied"
            assert whole_refs[0]() is None, "whole text or regex markers are still retained"
            assert index_refs[-1]() is None, "definition Match index survived its last use"
            if len(module_refs) > 1:
                assert section_refs[len(module_refs) - 2]() is None, "previous section remains retained"
            events.append("section-verify")
        module = Parsed()
        module.triple = target
        module.functions = [SimpleNamespace(name=name, indexed_kernel=object())
                            for name in closure.DEFINITION.findall(value)]
        module_refs.append(weakref.ref(module))
        return module

    def encode(path, module):
        assert module_refs[-1]() is module
        events.append("encode")
        if failure == "encode":
            raise original_error
        Path(path).write_bytes(b"encoded-sidecar")

    def emit(path, destination, kind, *, optimize):
        assert Path(path).read_bytes() == b"encoded-sidecar" and kind == "PCO" and optimize is False
        assert module_refs[-1]() is None, "original parsed module overlaps decoded emitter module"
        events.append("emit")
        if failure == "emit":
            raise original_error
        Path(destination).write_bytes(b"owned-object")

    def require_symbols(payload, triple, required):
        assert payload == b"owned-object" and triple == target
        assert required == [symbols[names[len([event for event in events if event == "object-check"])]]]
        events.append("object-check")
        return {"required_defined_symbols": list(required)}

    monkeypatch.setattr(closure, "require_object_symbols", require_symbols)
    for module_name, exports in {
        "pcc.backend.self_backend_indexed_codec": {"encode_indexed_module_file": encode},
        "pcc.backend.self_backend_indexed_emit": {"emit_indexed_module_file": emit},
        "pcc.frontends.python.pipeline": {"compile_python_multi": compile_multi},
        "pcc.frontends.python.pipeline_targets": {"host_target_triple": lambda: target},
        "pcc.tools.runtime_archive_provenance": {"codegen_checksum": lambda: "fixed-codegen"},
        "tests.owned_ir_validation": {"verify_ir_text": verify},
    }.items():
        module = ModuleType(module_name)
        for key, value in exports.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, module_name, module)
    if failure == "none":
        closure.test_worker_size_prior_modules_strict_target_emission(output, monkeypatch)
    else:
        with pytest.raises(error_type, match="synthetic " + failure + " failure") as caught:
            closure.test_worker_size_prior_modules_strict_target_emission(output, monkeypatch)
        assert caught.value is original_error
    receipt = json.loads((output / "worker-size-prior-closed-world.json").read_text())
    assert receipt["target"] == target and receipt["codegen_sha256"] == "fixed-codegen"
    assert receipt["sources"] == {name: hashlib.sha256(
        (root / (name.replace(".", "/") + ".py")).read_bytes()).hexdigest() for name in names}
    if failure in ("compile-summary", "compile-publish"):
        assert receipt["status"] == "RUNNING" and receipt["modules"] == {}
        assert receipt["compile_profile"]["complete"] is False
        assert receipt["compile_profile"]["phase_totals_ms"] == {}
        assert receipt["progress"][-1]["phase"] == "compile" and receipt["progress"][-1]["state"] == "started"
        assert "closure_modules" not in receipt and events == ["compile"]
        return
    assert receipt["compile_profile"]["complete"] == (failure != "compile")
    assert receipt["compile_profile"]["phase_totals_ms"] == {
        "multi_type_infer": 20, "python_ir_pass_pipeline_many": 30,
    }
    assert receipt["compile_profile"]["module_events"]["multi_type_infer"] == [
        {"module": names[0], "ms": 20},
    ]
    if failure == "compile":
        assert receipt["status"] == "FAIL" and receipt["modules"] == {}
        assert receipt["error"] == "RuntimeError: synthetic compile failure"
        assert "closure_modules" not in receipt and events == ["compile"]
        return
    if failure in ("parse", "verify", "parse-publish", "verify-publish"):
        phase = "whole-parse" if failure.startswith("parse") else "whole-verify-parsed"
        assert receipt["progress"][-1]["phase"] == phase
        assert receipt["progress"][-1]["state"] == "started"
        assert receipt["status"] == ("RUNNING" if failure.endswith("-publish") else "FAIL")
        if not failure.endswith("-publish"):
            assert receipt["error"] == "RuntimeError: synthetic " + failure + " failure"
        assert "closure_modules" not in receipt and receipt["modules"] == {}
        assert events == ["compile", "whole-verify"]
        return
    assert receipt["closure_modules"] == [*names, dependency]
    assert receipt["closure_definitions"] == 3
    if failure == "publish":
        assert receipt["status"] == "RUNNING" and list(receipt["modules"]) == list(names)
        assert receipt["progress"][-1]["phase"] == "module-emit" and receipt["progress"][-1]["state"] == "complete"
        return
    if failure == "none":
        assert receipt["status"] == "PASS" and list(receipt["modules"]) == list(names)
        for name in names:
            section = '\ndefine ptr @' + symbols[name] + '() {\n call ptr @real()\n ret ptr null\n}\n'
            assert receipt["modules"][name] == {
                "object_contract": {"required_defined_symbols": [symbols[name]]},
                "functions": ["entry"], "ir_sha256": hashlib.sha256(section.encode()).hexdigest(),
                "object_sha256": hashlib.sha256(b"owned-object").hexdigest(), "object_bytes": 12,
            }
        assert all(reference() is None for reference in whole_refs + section_refs + module_refs + index_refs)
        assert events == ["compile", "whole-verify", "slice", "slice",
                          *(event for _ in names for event in ("section-verify", "encode", "emit", "object-check"))]
    else:
        assert receipt["status"] == "FAIL" and receipt["modules"] == {}
        assert receipt["error"] == "RuntimeError: synthetic " + failure + " failure"
        assert events == ["compile", "whole-verify", "slice", "slice", "section-verify", "encode"] + (
            ["emit"] if failure == "emit" else []
        )


def _load_owned_ir_validation_control(monkeypatch, parse, verify, target):
    # Install all PCC imports before executing this small test helper module.
    # This control never imports the real parser, verifier or frontend.
    for name, exports in {
        "pcc.backend.self_backend_parse": {"parse_self_backend_module": parse},
        "pcc.backend.self_backend_verify": {"verify_parsed_module": verify},
        "pcc.frontends.python.pipeline_targets": {"host_target_triple": target},
    }.items():
        module = ModuleType(name)
        for key, value in exports.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(
        "_owned_ir_validation_control", ROOT / "tests/owned_ir_validation.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("text", (
    'target triple = "chosen-target"\ndefine void @f() {}\n',
    '  target \ttriple = "chosen-target"\ndefine void @f() {}\n',
    'define void @f() {}\n',
))
@pytest.mark.parametrize("with_progress", (False, True))
def test_owned_ir_validation_progress_preserves_normalization_and_identity(monkeypatch, text, with_progress):
    events = []
    parsed = object()
    needs_target = not text.lstrip().startswith("target")
    normalized = ('target triple = "host-target"\n' if needs_target else '') + text

    class Text:
        def __str__(self):
            events.append("normalize")
            return text

    def target():
        events.append("target")
        return "host-target"

    def parse(value):
        assert type(value) is str and value == normalized
        events.append("parse")
        return parsed

    def verify(value):
        assert value is parsed
        events.append("verify")

    helper = _load_owned_ir_validation_control(monkeypatch, parse, verify, target)
    def progress(phase, state):
        events.append((phase, state))

    if with_progress:
        result = helper.verify_ir_text(Text(), progress=progress)
    else:
        result = helper.verify_ir_text(Text())
    assert result is parsed
    expected = ["normalize"] + (["target"] if needs_target else [])
    if with_progress:
        expected += [("parse", "started"), "parse", ("parse", "complete"),
                     ("verify", "started"), "verify", ("verify", "complete")]
    else:
        expected += ["parse", "verify"]
    assert events == expected


@pytest.mark.parametrize("failure", (
    "normalize", "target", "parse", "verify",
    "parse-started", "parse-complete", "verify-started", "verify-complete",
))
def test_owned_ir_validation_progress_preserves_first_failure(monkeypatch, failure):
    events = []
    original_error = RuntimeError("original " + failure)
    parsed = object()

    def event(name):
        events.append(name)
        if name == failure:
            raise original_error

    class Text:
        def __str__(self):
            event("normalize")
            return "define void @f() {}\n"

    def target():
        event("target")
        return "host-target"

    def parse(value):
        assert value == 'target triple = "host-target"\ndefine void @f() {}\n'
        event("parse")
        return parsed

    def verify(value):
        assert value is parsed
        event("verify")

    helper = _load_owned_ir_validation_control(monkeypatch, parse, verify, target)
    def progress(phase, state):
        event(phase + "-" + state)

    with pytest.raises(RuntimeError) as caught:
        helper.verify_ir_text(Text(), progress=progress)
    assert caught.value is original_error
    full_order = ["normalize", "target", "parse-started", "parse", "parse-complete",
                  "verify-started", "verify", "verify-complete"]
    assert events == full_order[:full_order.index(failure) + 1]


def test_strict_compile_profile_keeps_only_bounded_phase_data():
    from tests.integration import test_worker_size_prior_closed_world as closure

    profile = {
        "phase_totals_ms": {name: 123 for name in closure._COMPILE_PROFILE_PHASES},
        "counters": {name: 456 for name in closure._COMPILE_PROFILE_COUNTERS},
        "events": [], "secret": "/private/source/path",
    }
    module_names = {index: ("pcc.module_" + str(index) + "_").ljust(160, "x") for index in range(20)}
    for phase in closure._COMPILE_PROFILE_MODULE_PHASES:
        profile["events"].extend({"name": phase, "detail": module_names[index], "ms": index}
                                 for index in range(20))
        for detail, duration in (("/private/source/path", 1), ("pcc." + "x" * 160, 1),
                                 ("pcc.hidden", True), ("pcc.hidden", -1),
                                 ("pcc.hidden", 9223372036854775808), ("pcc.hidden", float("inf"))):
            profile["events"].append({"name": phase, "detail": detail, "ms": duration})
    profile["events"] += [None, {"name": [], "detail": "pcc.hidden", "ms": 1},
                          {"name": "unapproved", "detail": "/private/path", "ms": 1}]
    profile["phase_totals_ms"].update(unapproved="/private/path", multi_type_infer=True)
    profile["counters"].update(unapproved="/private/path", multi_files=-1)
    snapshot = closure._bounded_compile_profile(profile, complete=True)
    assert snapshot["complete"] is True and snapshot["module_events_truncated"] is True
    assert set(snapshot["phase_totals_ms"]) == set(closure._COMPILE_PROFILE_PHASES) - {"multi_type_infer"}
    assert set(snapshot["counters"]) == set(closure._COMPILE_PROFILE_COUNTERS) - {"multi_files"}
    for phase in closure._COMPILE_PROFILE_MODULE_PHASES:
        assert snapshot["module_event_counts"][phase] == 20
        assert snapshot["module_events"][phase] == [
            {"module": module_names[index], "ms": index} for index in range(19, 11, -1)
        ]
    encoded = json.dumps(snapshot, allow_nan=False)
    assert "/private" not in encoded and "hidden" not in encoded and len(encoded.encode()) < 16384


def test_strict_compile_profile_snapshot_is_detached_and_handles_empty_fields():
    from tests.integration import test_worker_size_prior_closed_world as closure

    profile = {"phase_totals_ms": {"multi_codegen_layer1": 5}, "counters": {"multi_files": 2},
               "events": [{"name": "multi_codegen_layer1", "detail": "pcc.z", "ms": 5},
                          {"name": "multi_codegen_layer1", "detail": "pcc.a", "ms": 5}]}
    snapshot = closure._bounded_compile_profile(profile, complete=False)
    saved = json.dumps(snapshot, sort_keys=True)
    assert snapshot["capture"] == "return-or-python-exception" and snapshot["complete"] is False
    assert snapshot["module_events"]["multi_codegen_layer1"] == [
        {"module": "pcc.a", "ms": 5}, {"module": "pcc.z", "ms": 5},
    ]
    profile["phase_totals_ms"].clear()
    profile["counters"].clear()
    profile["events"][0]["detail"] = "/private/changed"
    profile.clear()
    assert json.dumps(snapshot, sort_keys=True) == saved
    empty = closure._bounded_compile_profile(
        {"phase_totals_ms": [], "counters": None, "events": "not-events"}, complete=False,
    )
    assert empty["phase_totals_ms"] == {} and empty["counters"] == {}
    assert not any(empty["module_event_counts"].values())
    assert not any(empty["module_events"].values()) and not empty["module_events_truncated"]


@pytest.mark.parametrize("retaining", [False, True], ids=["retired", "original-reference"])
@pytest.mark.parametrize("fail_freeze", [False, True], ids=["success", "freeze-failure"])
def test_parser_input_lifetimes_release_only_after_successful_freeze(retaining, fail_freeze):
    # Execute the actual traversal with synthetic owners, not the PCC parser,
    # unsafe arenas, codec, emitter, or native code. The old-loop control must
    # exhibit the opposite lifetime at the second function.
    path = ROOT / "pcc/backend/self_backend_parse.py"
    source = ast.parse(path.read_text())
    function, = [node for node in source.body
                 if isinstance(node, ast.FunctionDef) and node.name == "_parse_functions"]
    tree = ast.Module(body=[function], type_ignores=[])
    if retaining:
        reference = ast.parse((ROOT / "tests/c/test_self_backend_function_body_lines.py").read_text())
        restore, = [node for node in reference.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_restore_retained_parse_inputs"]
        scope = {"ast": ast}
        exec(compile(ast.Module(body=[restore], type_ignores=[]), "<pure-parser-reference>", "exec"), scope)
        tree = scope["_restore_retained_parse_inputs"](tree)

    class Owner:
        def __init__(self, name):
            self.name = name
        def close(self):
            raise AssertionError("shared parser owners must not be closed")

    class Definitions(list):
        pass

    class Function:
        def __init__(self, **values):
            self.__dict__.update(values)

    header_refs, body_refs, seed_refs, arenas, functions, visits = [], [], [], [], [], []
    scan_refs = []
    failure = RuntimeError("original freeze failure")

    def scan(text):
        assert text == "all-input" and visits == []
        definitions = Definitions()
        for name in ("first", "second"):
            header, body = Owner(name), Owner(name)
            header_refs.append(weakref.ref(header))
            body_refs.append(weakref.ref(body))
            definitions.append((header, body))
        scan_refs.append(weakref.ref(definitions))
        return definitions

    def parse_blocks(name, body, args, **kwargs):
        assert body.name == name and args == []
        if name == "second":
            assert (header_refs[0]() is not None) is retaining
            assert (body_refs[0]() is not None) is retaining
            assert (seed_refs[0]() is not None) is retaining
            assert functions[0].indexed_kernel.arena is arenas[0]
        visits.append(name)
        assert len(scan_refs[0]()) == (2 if retaining else 2 - len(visits))
        seed = Owner(name)
        seed.construction = Owner("temporary columns")
        seed.arena = Owner("shared final arena")
        seed_refs.append(weakref.ref(seed))
        arenas.append(seed.arena)
        return [], seed

    def freeze(function):
        assert header_refs[-1 if function.name == "second" else 0]() is not None
        assert body_refs[-1 if function.name == "second" else 0]() is not None
        if fail_freeze and function.name == "second":
            raise failure
        function.indexed_kernel = SimpleNamespace(arena=function.indexed_seed.arena)
        function.indexed_seed = None
        functions.append(function)
        return function.indexed_kernel

    scope = {
        "_iter_function_defs": scan,
        "_parse_function_header": lambda header, **kwargs: ("", "i64", header.name, ""),
        "decode_global_name": lambda name: name,
        "check_simple_symbol_name": lambda name: None,
        "_parse_type": lambda text, **kwargs: "i64",
        "_parse_arg_infos": lambda name, text, **kwargs: [],
        "_parse_blocks": parse_blocks,
        "ParsedFunction": Function,
        "_arg_list_is_vararg": lambda text: False,
        "get_indexed_function_kernel": freeze,
    }
    tree.body[:0] = ast.parse("from __future__ import annotations").body
    exec(compile(ast.fix_missing_locations(tree), "<pure-parser-input-lifetimes>", "exec"), scope)
    parse = scope["_parse_functions"]
    if fail_freeze:
        with pytest.raises(RuntimeError) as caught:
            parse("all-input", type_context=object())
        assert caught.value is failure
        # The failed frame still owns the unadopted seed and current texts.
        assert seed_refs[1]() is not None
        assert header_refs[1]() is not None and body_refs[1]() is not None
        assert (seed_refs[0]() is not None) is False
    else:
        result = parse("all-input", type_context=object())
        assert result == functions and [item.name for item in result] == ["first", "second"]
        assert all(ref() is None for ref in (*header_refs, *body_refs, *seed_refs))
        assert scan_refs[0]() is None
        assert all(item.indexed_kernel.arena is arena for item, arena in zip(result, arenas))
    assert visits == ["first", "second"]
