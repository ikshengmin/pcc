"""Pure command/report controls; actual native/xdist gates run in CI."""

import ast
import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

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


@pytest.mark.parametrize("target,failing", [
    ("win32", None), ("linux", None), ("darwin", None),
    ("win32", "windows-exit-host"), ("win32", "windows-exit-native"),
])
def test_windows_exit_gates_follow_verified_runtime_and_stop_on_failure(tmp_path, monkeypatch, target, failing):
    out = tmp_path / "preflight"
    archive = out / "runtime/libpy_runtime_pcc_py.a"
    source, runtime = {"source": "fixed"}, {"runtime": "matched"}
    events = []
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform=target, executable="chosen-python"))
    monkeypatch.setattr(gate, "platform", SimpleNamespace(machine=lambda: "arm64" if target == "darwin" else "x86_64"))
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
        elif name == "default-xdist":
            assert nodes == gate.HOST_FILES and counts == (80, 3, 64, 0) and not integration
        else:
            assert name == "strict-closure" and integration
        if name == failing:
            raise RuntimeError("original exit gate failure")

    def native(directory, owner, env):
        assert directory == out and owner == "pcc0"
        assert env["PCC_RUNTIME_ARCHIVE"] == str(archive)
        events.append("native-pcc0")

    monkeypatch.setattr(gate, "guarded", guarded)
    monkeypatch.setattr(gate, "runtime_identity", identity)
    monkeypatch.setattr(gate, "run_pytest", pytest_gate)
    monkeypatch.setattr(gate, "native_gate", native)
    expected = ["default-xdist", "runtime-build", "runtime-identity"]
    if target == "win32":
        expected += ["windows-exit-host", "windows-exit-native"]
    expected += ["native-pcc0", "strict-closure", "runtime-identity"]
    if failing:
        with pytest.raises(RuntimeError, match="original exit gate failure"):
            gate.run(out, "preflight")
        expected = expected[:expected.index(failing) + 1]
    else:
        gate.run(out, "preflight")
    assert events == expected
    receipt = json.loads((out / "preflight.json").read_text())
    assert receipt["status"] == ("FAIL" if failing else "PASS")
    assert receipt["source"] == source and receipt["runtime"] == runtime


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
