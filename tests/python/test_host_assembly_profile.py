"""Pure fake host controls: never import PCC or use a real profiler/compiler."""

import ast
import json
from pathlib import Path
import sys
from types import FunctionType, ModuleType, SimpleNamespace
import weakref

import pytest

from scripts import host_assembly_profile as adapter


def _manifest(root, names=(adapter.MODULE,), assigned=0):
    rows = ["pcc.frontends.python.codegen_worker.v4", "result", "ir", "exports",
            "codegen", "ast", "pcc.__main__", "off", "on", "0", "0", str(len(names))]
    rows += [str(index) + "\t" + name + "\t" + str(root / (name.replace(".", "/") + ".py"))
             for index, name in enumerate(names)]
    return "\n".join(rows + ["1", str(assigned)]) + "\n"


def _argv(path):
    return [sys.executable, "-m", "pcc", "--pcc-python-multi-codegen-worker", str(path)]


def _entry(code, calls=(), own=1.0, cumulative=2.0, count=1):
    return SimpleNamespace(code=code, calls=list(calls), inlinetime=own,
                           totaltime=cumulative, callcount=count, reccallcount=0)


def _fake(tmp_path, monkeypatch, original=None):
    """Install profiler and module fakes BEFORE any adapter entry invocation."""
    events = []
    control = SimpleNamespace(active=None, slot=None, enable_error=None, disable_error=None,
                              stats_error=None, report_error=None, construct_error=None)
    if original is None:
        def original(*args, **kwargs):
            events.append(("original", args, kwargs))
            return args[0] if args else None

    def measure():
        pass

    def encode():
        pass

    def parse():
        pass

    class Phase:
        def __init__(self, module=adapter.MODULE, target=adapter.TARGET, route=adapter.ROUTE):
            self.module_name, self.target, self.route = module, target, route

        def report(self, *args, **kwargs):
            events.append(("ordinary_report",))
            if control.report_error is not None:
                raise control.report_error
            return "ordinary-report-result"

    class Profile:
        def __init__(self, *, subcalls, builtins):
            events.append(("construct", subcalls, builtins))
            self.enabled = False
            if control.construct_error is not None:
                raise control.construct_error

        def enable(self):
            events.append(("enable",))
            control.slot = "cProfile"
            if control.enable_error is not None:
                raise control.enable_error
            self.enabled = True

        def disable(self):
            events.append(("disable",))
            if control.disable_error is not None:
                raise control.disable_error
            if self.enabled:
                control.slot = None
                self.enabled = False

        def getstats(self):
            events.append(("getstats",))
            if control.stats_error is not None:
                raise control.stats_error
            return [_entry(original.__code__, [_entry(encode.__code__, count=8)]),
                    _entry(measure.__code__, [_entry(encode.__code__, count=3)]),
                    _entry(encode.__code__), _entry(parse.__code__)]

        def clear(self):
            events.append(("clear",))

    profiler_module = ModuleType("cProfile")
    profiler_module.Profile = Profile
    monkeypatch.setitem(sys.modules, "cProfile", profiler_module)
    monkeypatch.setattr(adapter.sys, "getprofile", lambda: control.active)
    monkeypatch.setattr(adapter.sys, "monitoring", SimpleNamespace(
        PROFILER_ID=2, get_tool=lambda tool_id: control.slot), raising=False)
    monkeypatch.setattr(adapter, "_FAILED_PROFILER", None)
    modules = [ModuleType(name) for name in adapter.OWNERS]
    root = tmp_path / "source"
    owner_sources = {}
    for module in modules:
        relative = module.__name__.replace(".", "/") + ".py"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fake source identity only\n")
        module.__file__ = str(path)
        owner_sources[relative] = adapter._digest(path)
    assembly, timing, encoder, elf = modules
    assembly._assemble_file, assembly._measure_sections, assembly._parse_file = original, measure, parse
    timing.ModulePhaseTiming = Phase
    encoder.encode_instruction = encode
    directory = tmp_path / "capture"
    directory.mkdir()
    config = {"root": str(root), "directory": str(directory),
              "identity": {"target": adapter.TARGET, "module": adapter.MODULE,
                           "owner_sources": owner_sources,
                           "adapter_sha256": adapter._digest(adapter.__file__)}}
    return SimpleNamespace(events=events, control=control, modules=modules, config=config,
                           directory=directory, root=root, original=original, phase=Phase(),
                           assembly=assembly, timing=timing, encoder=encoder, elf=elf)


def _attach(fake):
    adapter._attach(fake.config, "a" * 64, *fake.modules)


def _result(fake):
    return json.loads((fake.directory / "result.json").read_text())


def _clean_host(monkeypatch):
    monkeypatch.setattr(adapter.sys, "platform", "linux")
    monkeypatch.setattr(adapter.sys, "version_info", (3, 13, 0, "final", 0))
    monkeypatch.setattr(adapter.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(adapter.sys, "getprofile", lambda: None)
    monkeypatch.setattr(adapter.sys, "monitoring", SimpleNamespace(
        PROFILER_ID=2, get_tool=lambda tool_id: None), raising=False)
    monkeypatch.delitem(sys.modules, "sitecustomize", raising=False)
    # These are process-global import helpers. Fake only the startup hook;
    # unrelated lazy imports (including encodings.ascii on 3.13) must work.
    util_find_spec = adapter.importlib.util.find_spec
    path_find_spec = adapter.importlib.machinery.PathFinder.find_spec
    monkeypatch.setattr(adapter.importlib.util, "find_spec",
                        lambda name, *args, **kwargs: None if name == "sitecustomize"
                        else util_find_spec(name, *args, **kwargs))
    monkeypatch.setattr(adapter.importlib.machinery.PathFinder, "find_spec",
                        lambda fullname, *args, **kwargs: None if fullname == "sitecustomize"
                        else path_find_spec(fullname, *args, **kwargs))


def test_disabled_prepare_has_no_installation_or_import_disturbance(tmp_path, monkeypatch):
    util_find_spec = adapter.importlib.util.find_spec
    monkeypatch.setattr(adapter.importlib.util, "find_spec",
                        lambda name, *args, **kwargs: pytest.fail("lookup while disabled")
                        if name == "sitecustomize" else util_find_spec(name, *args, **kwargs))
    env = {"KEEP": "value"}
    before = tuple(sys.modules)
    assert adapter.prepare_environment(tmp_path, tmp_path / "absent", env, {}, {}, "", "") is env
    assert tuple(sys.modules) == before and list(tmp_path.iterdir()) == []


def test_loaded_sitecustomize_is_never_shadowed(tmp_path, monkeypatch):
    _clean_host(monkeypatch)
    hook = ModuleType("sitecustomize")
    monkeypatch.setitem(sys.modules, "sitecustomize", hook)
    env = {adapter.FLAG: "1"}
    assert adapter.prepare_environment(tmp_path, tmp_path / "capture", env, {}, {}, "", adapter.TARGET) is env
    assert sys.modules["sitecustomize"] is hook and env == {adapter.FLAG: "1"}
    assert json.loads((tmp_path / "capture/install-status.json").read_text())["status"] == "SITECUSTOMIZE_CONFLICT"
    assert not (tmp_path / "capture/sitecustomize.py").exists()


def test_discoverable_sitecustomize_and_uninspected_path_fail_closed(tmp_path, monkeypatch):
    _clean_host(monkeypatch)
    env = {adapter.FLAG: "1", "PYTHONPATH": adapter.os.environ.get("PYTHONPATH", "")}
    path_find_spec = adapter.importlib.machinery.PathFinder.find_spec
    monkeypatch.setattr(adapter.importlib.machinery.PathFinder, "find_spec",
                        lambda fullname, *args, **kwargs: object() if fullname == "sitecustomize"
                        else path_find_spec(fullname, *args, **kwargs))
    # Exercise delegation even when another test has already cached this codec.
    assert adapter.importlib.util.find_spec("encodings.ascii").name == "encodings.ascii"
    assert adapter.importlib.machinery.PathFinder.find_spec(
        "encodings.ascii", sys.modules["encodings"].__path__).name == "encodings.ascii"
    assert adapter.importlib.machinery.PathFinder.find_spec(
        fullname="encodings.ascii", path=sys.modules["encodings"].__path__,
        target=None).name == "encodings.ascii"
    assert adapter.prepare_environment(tmp_path, tmp_path / "capture", env, {}, {}, "", adapter.TARGET) is env
    assert json.loads((tmp_path / "capture/install-status.json").read_text())["status"] == "SITECUSTOMIZE_CONFLICT"
    monkeypatch.setattr(adapter.importlib.machinery.PathFinder, "find_spec", path_find_spec)
    changed = dict(env, PYTHONPATH="uninspected")
    assert adapter.prepare_environment(tmp_path, tmp_path / "changed", changed, {}, {}, "", adapter.TARGET) is changed
    assert json.loads((tmp_path / "changed/install-status.json").read_text())["status"] == "STARTUP_PATH_CONFLICT"
    assert not (tmp_path / "changed/sitecustomize.py").exists()


def test_wrong_platform_and_target_disable_only_observation(tmp_path, monkeypatch):
    _clean_host(monkeypatch)
    env = {adapter.FLAG: "1"}
    for index, target in enumerate(("aarch64-unknown-linux-gnu", "x86_64-pc-windows-msvc")):
        directory = tmp_path / str(index)
        assert adapter.prepare_environment(tmp_path, directory, env, {}, {}, "", target) is env
        assert json.loads((directory / "install-status.json").read_text())["status"] == "UNSUPPORTED_HOST"
    monkeypatch.setattr(adapter.sys, "platform", "win32")
    assert adapter.prepare_environment(tmp_path, tmp_path / "windows", env, {}, {}, "", adapter.TARGET) is env
    assert json.loads((tmp_path / "windows/install-status.json").read_text())["status"] == "UNSUPPORTED_HOST"


def test_prepare_preserves_environment_and_writes_bounded_safe_identity(tmp_path, monkeypatch):
    _clean_host(monkeypatch)
    for name in (*adapter.OWNERS, adapter.MODULE):
        path = tmp_path / (name.replace(".", "/") + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# source identity\n")
    env = {adapter.FLAG: "1", "PYTHONPATH": adapter.os.environ.get("PYTHONPATH", ""), "KEEP": "value"}
    before = dict(env)
    directory = tmp_path / "capture"
    result = adapter.prepare_environment(tmp_path, directory, env, {"commit": "a" * 40},
                                         {"path": "/private/runtime", "sha256": "b" * 64}, "c" * 64, adapter.TARGET)
    assert env == before and result is not env and result["KEEP"] == "value"
    assert result["PYTHONPATH"] == str(directory) + adapter.os.pathsep + env["PYTHONPATH"]
    setup = (directory / "setup.json").read_text()
    assert "/private" not in setup and str(tmp_path) not in setup
    assert json.loads(setup)["identity"]["runtime"] == {"sha256": "b" * 64}
    assert (directory / "_pcc_host_assembly_profile.py").read_bytes() == Path(adapter.__file__).read_bytes()
    (directory / "claim.json").write_text("stale claim")
    (directory / "result.json").write_text("stale result")
    assert adapter.prepare_environment(tmp_path, directory, env, {}, {}, "", adapter.TARGET) is env
    assert (directory / "result.json").read_text() == "stale result"
    monkeypatch.setattr(adapter.sys.monitoring, "get_tool", lambda tool_id: "occupied")
    blocked = tmp_path / "occupied"
    assert adapter.prepare_environment(tmp_path, blocked, env, {}, {}, "", adapter.TARGET) is env
    assert json.loads((blocked / "install-status.json").read_text())["status"] == "PROFILE_HOOK_CONFLICT"
    assert not (blocked / "sitecustomize.py").exists()


def test_admission_resolves_assignment_instead_of_searching_all_names(tmp_path):
    manifest = tmp_path / "worker"
    manifest.write_text(_manifest(tmp_path, (adapter.MODULE, "other"), 1))
    assert adapter._admit(_argv(manifest), tmp_path) is None
    manifest.write_text(_manifest(tmp_path, ("other", adapter.MODULE), 1))
    assert adapter._admit(_argv(manifest), tmp_path) == adapter._digest(manifest)
    assert adapter._admit([sys.executable, "wrapper", *_argv(manifest)[2:]], tmp_path) is None


def test_admission_rejects_nonordinary_or_mismatched_manifest(tmp_path):
    manifest = tmp_path / "worker"
    original = _manifest(tmp_path)
    cases = [original.replace("worker.v4", "worker.v5"), original.replace("\ncodegen\n", "\npreload\n"),
             original.replace("\noff\non\n", "\non\non\n"), original + "extra\n",
             original.replace("0\t" + adapter.MODULE, "1\t" + adapter.MODULE),
             original.replace(str(tmp_path / "pcc/driver/cli_bootstrap.py"), "/foreign/source.py")]
    for text in cases:
        manifest.write_text(text)
        assert adapter._admit(_argv(manifest), tmp_path) is None


def test_admission_caps_input_bytes_and_module_counts(tmp_path):
    manifest = tmp_path / "worker"
    manifest.write_bytes(b"x" * (adapter.MANIFEST_LIMIT + 1))
    assert adapter._admit(_argv(manifest), tmp_path) is None
    for replacement in ("-1", "4097"):
        lines = _manifest(tmp_path).splitlines()
        lines[11] = replacement
        manifest.write_text("\n".join(lines) + "\n")
        assert adapter._admit(_argv(manifest), tmp_path) is None


def test_disabled_install_leaves_modules_argv_and_hooks_unchanged(monkeypatch):
    monkeypatch.delenv(adapter.FLAG, raising=False)
    monkeypatch.setattr(adapter.importlib, "import_module", lambda name: pytest.fail("disabled import"))
    before = (tuple(sys.modules), tuple(sys.argv), tuple(sys.meta_path), sys.getprofile())
    adapter.install()
    assert before == (tuple(sys.modules), tuple(sys.argv), tuple(sys.meta_path), sys.getprofile())


def test_nonselected_install_never_imports_pcc_or_profiler(tmp_path, monkeypatch):
    fake = _fake(tmp_path, monkeypatch)
    _clean_host(monkeypatch)
    setup = tmp_path / "setup.json"
    setup.write_text(json.dumps({"identity": fake.config["identity"]}))
    manifest = tmp_path / "worker"
    manifest.write_text(_manifest(fake.root, (adapter.MODULE, "other"), 1))
    monkeypatch.setenv(adapter.FLAG, "1")
    monkeypatch.setenv(adapter.CONFIG, str(setup))
    monkeypatch.setenv(adapter.SOURCE_ROOT, str(fake.root))
    monkeypatch.setattr(sys, "orig_argv", _argv(manifest))
    monkeypatch.setattr(adapter.importlib, "import_module", lambda name: pytest.fail("nonselected import"))
    before = (tuple(sys.modules), tuple(sys.meta_path), tuple(sys.orig_argv))
    adapter.install()
    assert before == (tuple(sys.modules), tuple(sys.meta_path), tuple(sys.orig_argv))
    assert fake.events == []


def test_selected_install_uses_only_verified_fake_module_origins(tmp_path, monkeypatch):
    fake = _fake(tmp_path, monkeypatch)
    _clean_host(monkeypatch)
    setup = fake.directory / "setup.json"
    setup.write_text(json.dumps({"identity": fake.config["identity"]}))
    manifest = tmp_path / "worker"
    manifest.write_text(_manifest(fake.root))
    monkeypatch.setenv(adapter.FLAG, "1")
    monkeypatch.setenv(adapter.CONFIG, str(setup))
    monkeypatch.setenv(adapter.SOURCE_ROOT, str(fake.root))
    monkeypatch.setattr(sys, "orig_argv", _argv(manifest))
    imported = []

    def load(name):
        imported.append(name)
        return dict(zip(adapter.OWNERS, fake.modules))[name]

    monkeypatch.setattr(adapter.importlib, "import_module", load)
    hooks = tuple(sys.meta_path)
    fake.assembly.__file__ = str(tmp_path / "foreign.py")
    adapter.install()
    assert fake.assembly._assemble_file is fake.original
    assert json.loads((fake.directory / "install-status.json").read_text())["status"] == "OWNER_IDENTITY_MISMATCH"
    fake.assembly.__file__ = str(fake.root / "pcc/backend/x86_64_asm_driver.py")
    adapter.install()
    assert fake.assembly._assemble_file is not fake.original
    assert imported == list(adapter.OWNERS) * 2 and tuple(sys.meta_path) == hooks
    assert fake.events == []


def test_return_identity_arguments_and_lifetimes_survive_deferred_report(tmp_path, monkeypatch):
    class Value:
        pass

    calls = []

    def original(value, *args, **kwargs):
        calls.append((id(value), tuple(map(id, args)), {key: id(item) for key, item in kwargs.items()}))
        return value

    fake = _fake(tmp_path, monkeypatch, original)
    _attach(fake)
    Phase = fake.timing.ModulePhaseTiming
    for phase in (None, Phase(module="other"), Phase(target="aarch64-unknown-linux-gnu"),
                  Phase(route="indexed-sidecar")):
        marker = object()
        assert fake.assembly._assemble_file(marker, phase_timing=phase) is marker
    assert fake.events == [] and not (fake.directory / "claim.json").exists()
    calls.clear()
    value, argument = Value(), Value()
    value_ref, arg_ref = weakref.ref(value), weakref.ref(argument)
    result = fake.assembly._assemble_file(value, argument, phase_timing=fake.phase)
    assert result is value and calls == [(id(value), (id(argument),), {"phase_timing": id(fake.phase)})]
    assert not (fake.directory / "result.json").exists()
    assert [row[0] for row in fake.events] == ["construct", "enable", "disable"]
    del value, argument, result
    assert value_ref() is None and arg_ref() is None
    assert fake.phase.report(None, True) == "ordinary-report-result"
    assert _result(fake)["status"] == "ASSEMBLY_RETURNED"
    assert fake.assembly._assemble_file is original


def test_original_baseexception_identity_is_preserved(tmp_path, monkeypatch):
    for index, error in enumerate((ValueError("original"), KeyboardInterrupt(), SystemExit(7), GeneratorExit())):
        calls = []

        def original(*args, **kwargs):
            calls.append(1)
            raise error

        directory = tmp_path / str(index)
        directory.mkdir()
        fake = _fake(directory, monkeypatch, original)
        _attach(fake)
        with pytest.raises(type(error)) as caught:
            fake.assembly._assemble_file(phase_timing=fake.phase)
        assert caught.value is error and calls == [1]
        fake.phase.report(None, False)
        assert _result(fake)["status"] == "ASSEMBLY_RAISED"


def test_constructor_failure_does_not_skip_or_repeat_original(tmp_path, monkeypatch):
    for fault in ("constructor", "import"):
        directory = tmp_path / fault
        directory.mkdir()
        fake = _fake(directory, monkeypatch)
        if fault == "constructor":
            fake.control.construct_error = KeyboardInterrupt()
        else:
            monkeypatch.delattr(sys.modules["cProfile"], "Profile")
        _attach(fake)
        sentinel = object()
        assert fake.assembly._assemble_file(sentinel, phase_timing=fake.phase) is sentinel
        fake.phase.report(None, True)
        assert [row[0] for row in fake.events].count("original") == 1
        assert _result(fake)["status"] == "PROFILE_SETUP_FAILED"


def test_partial_enable_failure_calls_original_once_and_reports_unrestored_state(tmp_path, monkeypatch):
    fake = _fake(tmp_path, monkeypatch)
    fake.control.enable_error = SystemExit(8)
    _attach(fake)
    sentinel = object()
    assert fake.assembly._assemble_file(sentinel, phase_timing=fake.phase) is sentinel
    fake.phase.report(None, True)
    assert fake.control.active is None and fake.control.slot == "cProfile"
    assert [row[0] for row in fake.events].count("original") == 1
    assert "enable_failed" in _result(fake)["diagnostic_errors"]
    assert "profile_state_not_restored" in _result(fake)["diagnostic_errors"]
    assert adapter._FAILED_PROFILER is not None and not _result(fake)["capture_valid"]
    assert ("getstats",) not in fake.events and ("clear",) not in fake.events


def test_disable_failure_cannot_replace_original_return_or_exception(tmp_path, monkeypatch):
    for index, error in enumerate((None, KeyboardInterrupt())):
        calls = []
        sentinel = object()

        def original(*args, **kwargs):
            calls.append(1)
            if error is not None:
                raise error
            return sentinel

        directory = tmp_path / str(index)
        directory.mkdir()
        fake = _fake(directory, monkeypatch, original)
        fake.control.disable_error = SystemExit(9)
        _attach(fake)
        if error is None:
            assert fake.assembly._assemble_file(phase_timing=fake.phase) is sentinel
        else:
            with pytest.raises(KeyboardInterrupt) as caught:
                fake.assembly._assemble_file(phase_timing=fake.phase)
            assert caught.value is error
        fake.phase.report(None, error is None)
        assert calls == [1]
        assert "disable_failed_state_not_restored" in _result(fake)["diagnostic_errors"]
        assert not _result(fake)["capture_valid"] and "functions" not in _result(fake)
        assert [row[0] for row in fake.events].count("disable") == 1
        assert adapter._FAILED_PROFILER is not None and fake.control.slot == "cProfile"
        assert ("getstats",) not in fake.events and ("clear",) not in fake.events


def test_existing_profile_hook_is_preserved_without_profiler_construction(tmp_path, monkeypatch):
    for kind in ("legacy", "monitoring"):
        directory = tmp_path / kind
        directory.mkdir()
        fake = _fake(directory, monkeypatch)
        hook = object()
        if kind == "legacy":
            fake.control.active = hook
        else:
            fake.control.slot = "foreign-profiler"
        _attach(fake)
        sentinel = object()
        assert fake.assembly._assemble_file(sentinel, phase_timing=fake.phase) is sentinel
        fake.phase.report(None, True)
        assert (fake.control.active is hook if kind == "legacy" else fake.control.slot == "foreign-profiler")
        assert ("construct", True, True) not in fake.events
        assert _result(fake)["status"] == "PROFILE_HOOK_CONFLICT"


def test_new_foreign_hook_is_not_disabled_or_replaced(tmp_path, monkeypatch):
    for kind in ("legacy", "monitoring"):
        hook = object()

        def original(*args, **kwargs):
            if kind == "legacy":
                fake.control.active = hook
            else:
                fake.control.slot = "foreign-monitoring"
            return hook

        directory = tmp_path / kind
        directory.mkdir()
        fake = _fake(directory, monkeypatch, original)
        _attach(fake)
        assert fake.assembly._assemble_file(phase_timing=fake.phase) is hook
        fake.phase.report(None, True)
        if kind == "legacy":
            assert fake.control.active is hook and ("disable",) in fake.events
            assert fake.control.slot is None
            assert "foreign_legacy_hook_preserved" in _result(fake)["diagnostic_errors"]
        else:
            assert fake.control.slot == "foreign-monitoring" and ("disable",) not in fake.events
            assert "foreign_monitoring_slot_preserved" in _result(fake)["diagnostic_errors"]
            assert adapter._FAILED_PROFILER is not None
        assert not _result(fake)["capture_valid"]


def test_claim_is_once_across_retries_and_missing_result_stays_missing(tmp_path, monkeypatch):
    first = _fake(tmp_path, monkeypatch)
    _attach(first)
    first.assembly._assemble_file(None, phase_timing=first.phase)
    claim = (first.directory / "claim.json").read_bytes()
    # Simulate a kill before report, then a new worker with the same output root.
    second_dir = tmp_path / "retry"
    second_dir.mkdir()
    second = _fake(second_dir, monkeypatch)
    second.config["directory"] = str(first.directory)
    _attach(second)
    second.assembly._assemble_file(None, phase_timing=second.phase)
    second.phase.report(None, True)
    assert not (first.directory / "result.json").exists()
    assert (first.directory / "claim.json").read_bytes() == claim
    assert ("construct", True, True) not in second.events
    first.phase.report(None, True)
    assert _result(first)["status"] == "ASSEMBLY_RETURNED"


def test_original_report_exception_wins_and_wrappers_are_cleaned(tmp_path, monkeypatch):
    fake = _fake(tmp_path, monkeypatch)
    ordinary_report = fake.timing.ModulePhaseTiming.report
    error = KeyboardInterrupt()
    fake.control.report_error = error
    _attach(fake)
    fake.assembly._assemble_file(None, phase_timing=fake.phase)
    with pytest.raises(KeyboardInterrupt) as caught:
        fake.phase.report(None, True)
    assert caught.value is error and ("clear",) in fake.events
    assert fake.timing.ModulePhaseTiming.report is ordinary_report
    assert fake.assembly._assemble_file is fake.original


def test_stats_and_publication_failures_never_change_business_outcome(tmp_path, monkeypatch):
    fake = _fake(tmp_path, monkeypatch)
    fake.control.stats_error = SystemExit(10)
    _attach(fake)
    sentinel = object()
    assert fake.assembly._assemble_file(sentinel, phase_timing=fake.phase) is sentinel
    fake.phase.report(None, True)
    assert _result(fake)["status"] == "PROFILE_REPORT_FAILED"
    assert ("clear",) in fake.events
    with pytest.raises(ValueError, match="byte cap"):
        adapter._write(fake.directory / "too-large.json", {"value": "x" * adapter.REPORT_LIMIT})
    assert not (fake.directory / "too-large.json").exists()


def test_raw_code_identity_preserves_colliding_constructors_and_pass_edges(tmp_path):
    def init(self):
        pass

    first = init.__code__.replace(co_filename="<string>", co_firstlineno=2,
                                  co_name="__init__", co_qualname="__init__")
    second = first.replace()
    assert first == second and first is not second
    module = ModuleType("pcc.backend.x86_64_encode")
    module.First = type("First", (), {"__module__": module.__name__, "__init__": FunctionType(first, {})})
    module.Second = type("Second", (), {"__module__": module.__name__, "__init__": FunctionType(second, {})})
    labels = adapter._labels((module,))
    assert labels[id(first)].endswith("First.__init__") and labels[id(second)].endswith("Second.__init__")
    root, measure, encode = (init.__code__.replace(co_name=name) for name in ("root", "measure", "encode"))
    entries = [_entry(first), _entry(second), _entry(root, [_entry(encode, count=10)]),
               _entry(measure, [_entry(encode, count=3)]), _entry(encode)]
    result = adapter._summary(entries, tmp_path, labels, (id(root), id(measure), id(encode), 0))
    assert result["coverage"]["functions_total"] == 5
    assert len({row["id"] for row in result["functions"]}) == 5
    assert sorted(row["calls"] for row in result["key_caller_edges"]) == [3, 10]
    unknown_builtin = "".join(("<built-in ", "unknown>"))
    entries[2].calls.append(_entry(unknown_builtin))
    result = adapter._summary(entries, tmp_path, labels, (id(root), id(measure), id(encode), 0))
    assert result["coverage"]["key_edges_identity_unresolved"] == 1
    assert result["coverage"]["edges_omitted"] == 1


def test_summary_is_bounded_with_coverage_safe_labels_and_overlapping_times(tmp_path):
    def function():
        pass

    entries = [_entry(function.__code__.replace(co_filename="/private/secret/file.py",
                                               co_name="function", co_qualname="function"), own=index)
               for index in range(100)]
    entries.append(_entry("<built-in method at 0xABCDEF>"))
    result = adapter._summary(entries, tmp_path, {}, (0, 1, 2, 3))
    assert len(result["functions"]) <= 2 * adapter.TOP
    coverage = result["coverage"]
    assert coverage["functions_shown"] + coverage["functions_omitted"] == 101
    assert coverage["self_s_total"] == coverage["self_s_shown"] + coverage["self_s_omitted"]
    text = json.dumps(result)
    assert "/private" not in text and "0xABCDEF" not in text
    assert "must not be summed" in result["attribution"]
    adapter._write(tmp_path / "summary.json", result)
    assert (tmp_path / "summary.json").stat().st_size <= adapter.REPORT_LIMIT


def test_stage1_wiring_is_opt_in_surgical_and_uses_existing_report_boundary():
    root = Path(__file__).resolve().parents[2]
    source = (root / "scripts/bootstrap_platform.py").read_text()
    assert 'if stage == 1 and stage_env.get("PCC_HOST_ASSEMBLY_PROFILE") == "1":' in source
    assert source.count("prepare_environment(") == 1
    assert '"claimed": profile_installed and (profile_directory / "claim.json").is_file()' in source
    assert '"result_present": profile_installed and (profile_directory / "result.json").is_file()' in source
    assert '"installation_status_present": profile_fresh and ' in source
    assert "profile_installed = profiled_env is not stage_env" in source
    assert 'timeout=timeout, rss_limit=rss_limit, native=stage > 1)' in source
    worker = (root / "pcc/frontends/python/pipeline_frontend_worker_execution.py").read_text()
    assert worker.index("codegen_ms = int((time.monotonic() - codegen_started)") < worker.index("phase_timing.report(sys.stderr, phase_timing_complete)")
    workflow = (root / ".github/workflows/pcc1-package-parity.yml").read_text()
    assert "PCC_HOST_ASSEMBLY_PROFILE: ${{ github.event_name == 'push' && matrix.platform == 'linux-x86_64' && contains(github.event.head_commit.message, '[stage1-assembly-profile]') && '1' || '0' }}" in workflow
    assert workflow.count("PCC_HOST_ASSEMBLY_PROFILE:") == 1
    assert workflow.count("--out-dir build/worker-prior-gate --phase stage1") == 2
    tree = ast.parse(Path(adapter.__file__).read_text())
    top_imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any(getattr(node, "module", "").startswith(("pcc", "cProfile", "_lsprof"))
                   for node in top_imports if isinstance(node, ast.ImportFrom))
    assert not any(alias.name.startswith(("pcc", "cProfile", "_lsprof"))
                   for node in top_imports if isinstance(node, ast.Import) for alias in node.names)
