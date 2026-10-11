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
    imported = []

    def unexpected_import(name):
        imported.append(name)
        pytest.fail("nonselected import")

    monkeypatch.setattr(adapter.importlib, "import_module", unexpected_import)

    def assert_admission_modules(before, after):
        # Reading the ASCII setup can lazily import precisely this codec.
        # No other addition, removal, reordering or replacement is allowed.
        assert set(after).difference(before) <= {"encodings.ascii"}
        assert tuple(name for name in after if name in before) == tuple(before)
        assert all(after[name] is module for name, module in before.items())

    modules_before = dict(sys.modules)
    before = (tuple(sys.meta_path), tuple(sys.orig_argv))
    adapter.install()
    assert_admission_modules(modules_before, dict(sys.modules))
    assert before == (tuple(sys.meta_path), tuple(sys.orig_argv))
    assert imported == [] and fake.events == []

    # Pure snapshots exercise both cache states without touching codec caches.
    first, second, codec = object(), object(), object()
    baseline = {"first": first, "second": second}
    assert_admission_modules(baseline, dict(baseline))
    with_codec = dict(baseline, **{"encodings.ascii": codec})
    assert_admission_modules(baseline, with_codec)
    assert_admission_modules(with_codec, dict(with_codec))
    for name in ("unexpected_dependency", "pcc", "pcc.unexpected", "cProfile",
                 "_lsprof", "pstats", "profile", "encodings.utf_16"):
        with pytest.raises(AssertionError):
            assert_admission_modules(baseline, dict(baseline, **{name: object()}))
    for changed in ({"first": first}, {"second": second, "first": first},
                    {"first": object(), "second": second}):
        with pytest.raises(AssertionError):
            assert_admission_modules(baseline, changed)
    with pytest.raises(AssertionError):
        assert_admission_modules(with_codec, dict(with_codec, **{"encodings.ascii": object()}))


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
    stage1 = workflow.split("      - name: Qualify all five GC self-host chains and Stage1 admission metrics\n", 1)[1].split("\n      - name:", 1)[0]
    assert 'PCC_HOST_ASSEMBLY_PROFILE: "0"' in stage1
    assert "PCC_HOST_ELF_STAGE_PROFILE: ${{ github.event_name == 'push' && matrix.platform == 'linux-aarch64' && contains(github.event.head_commit.message, '[stage1-arm-elf-profile]') && '1' || '0' }}" in stage1
    assert "--out-dir build/worker-prior-gate --phase stage1" in stage1
    assert workflow.count("PCC_HOST_ASSEMBLY_PROFILE:") == 1
    assert workflow.count("PCC_HOST_ELF_STAGE_PROFILE:") == 1
    assert "[stage1-assembly-profile]" not in workflow
    assert workflow.count("--out-dir build/worker-prior-gate --phase stage1") == 2
    tree = ast.parse(Path(adapter.__file__).read_text())
    top_imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any(getattr(node, "module", "").startswith(("pcc", "cProfile", "_lsprof"))
                   for node in top_imports if isinstance(node, ast.ImportFrom))
    assert not any(alias.name.startswith(("pcc", "cProfile", "_lsprof"))
                   for node in top_imports if isinstance(node, ast.Import) for alias in node.names)


def _elf_ordinary_environment():
    return {adapter.ELF_FLAG: "1", "PCC_SELF_LINK": "pcc", "PCC_SELF_OBJ": "pcc",
            "PCC_DIRECT_INDEXED_KERNEL_CAPTURE": "1", "PCC_DIRECT_INDEXED_KERNEL_EMIT": "1",
            "PCC_DEFER_FRONTEND_CODEGEN": "0", "PCC_DEFER_FRONTEND_CODEGEN_PLAN": "",
            "PCC_DEFER_FRONTEND_OUTPUT": "", "PCC_DEFER_SELF_LINK_PLAN": "",
            "PYTHONPATH": adapter.os.environ.get("PYTHONPATH", "")}


def _elf_argv(root):
    return [sys.executable, "-m", "pcc", "--backend", "self", "--python-libpython", "off",
            "--ir-scaffold=on", str(root / "pcc/__main__.py"), "-o", str(root / "stage1")]


def _elf_owned_function(function, module, qualname):
    code = function.__code__.replace(co_filename=module.__file__,
                                     co_name=qualname.rsplit(".", 1)[-1], co_qualname=qualname)
    owned = FunctionType(code, function.__globals__, code.co_name,
                         function.__defaults__, function.__closure__)
    owned.__kwdefaults__ = function.__kwdefaults__
    owned.__module__, owned.__qualname__ = module.__name__, qualname
    return owned


def _elf_fake(tmp_path, monkeypatch):
    """Build only owned Python fakes; no PCC import, payload IO or real profiler."""
    _clean_host(monkeypatch)
    fake = _fake(tmp_path, monkeypatch)
    monkeypatch.setattr(adapter.platform, "machine", lambda: "aarch64")
    monkeypatch.delenv(adapter.FLAG, raising=False)
    monkeypatch.delenv(adapter.ELF_FLAG, raising=False)
    modules = [ModuleType(name) for name in adapter.ELF_OWNERS]
    for module in modules:
        path = fake.root / (module.__name__.replace(".", "/") + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fake ARM owner source identity only\n")
        module.__file__ = str(path)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    module_source = fake.root / "pcc/driver/cli_bootstrap.py"
    module_source.parent.mkdir(parents=True, exist_ok=True)
    module_source.write_text("# fake nominated module identity only\n")
    pipeline, inputs, linker, elf = modules
    control, events = fake.control, fake.events
    control.stage_error = control.stage_callback = control.link_callback = None
    control.source_factory = control.result_factory = control.store_observer = None
    control.source_count_delta, control.stage_result = 1, None
    control.link_result = object()

    class Result:
        @property
        def data(self):
            pytest.fail("adapter must not read a result payload")

        @property
        def relocations(self):
            pytest.fail("adapter must not read result relocations")

        @property
        def sections(self):
            pytest.fail("adapter must not traverse result sections")

    class Store:
        def __init__(self, target=adapter.ELF_TARGET):
            self.target, self.sources = target, []
            if control.store_observer is not None:
                control.store_observer(self)

        def stage(self, path, *, kind="PCO", offset=0, size=None, label=""):
            events.append(("stage", id(self), path, kind, offset, size, label, control.slot))
            if control.stage_callback is not None:
                control.stage_callback(self, path, kind, offset, size, label)
            if control.stage_error is not None:
                raise control.stage_error
            for _ in range(control.source_count_delta):
                if control.source_factory is None:
                    source = SimpleNamespace(path=path, kind=kind, whole_file=size is None,
                                             size=37, sha256="d" * 64)
                else:
                    source = control.source_factory(path, kind, offset, size, label)
                self.sources.append(source)
            if control.result_factory is not None:
                return control.result_factory()
            return control.stage_result if control.stage_result is not None else Result()

        def _read(self, source):
            pytest.fail("fake stage never reads a file")

    Store.__module__, Store.__qualname__ = inputs.__name__, "ElfInputStore"
    Store.stage = _elf_owned_function(Store.stage, inputs, "ElfInputStore.stage")
    Store._read = _elf_owned_function(Store._read, inputs, "ElfInputStore._read")
    inputs.ElfInputStore = linker.ElfInputStore = Store

    def parse_indexed(data):
        pytest.fail("fake stage never parses ELF")

    def parse(data):
        pytest.fail("fake stage never parses ELF")

    elf._parse_relocatable_indexed = _elf_owned_function(parse_indexed, elf, "_parse_relocatable_indexed")
    elf._parse_relocatable = _elf_owned_function(parse, elf, "_parse_relocatable")

    def link(*args, **kwargs):
        events.append(("link", tuple(map(id, args)), {key: id(value) for key, value in kwargs.items()}))
        if control.link_callback is not None:
            return control.link_callback(*args, **kwargs)
        artifacts = args[0] if args else kwargs["artifacts"]
        store = Store()
        for _module, kind, path in artifacts:
            store.stage(adapter.os.path.abspath(path), kind=kind)
            events.append(("after_stage", path, (fake.directory / "result.json").exists()))
        events.append(("link_return",))
        return control.link_result

    pipeline._link_with_self_backend_direct_artifacts = _elf_owned_function(
        link, pipeline, "_link_with_self_backend_direct_artifacts")
    original_link, original_stage = pipeline._link_with_self_backend_direct_artifacts, Store.stage

    def getstats(self):
        events.append(("getstats",))
        if control.stats_error is not None:
            raise control.stats_error
        read, indexed, parsed = (Store._read.__code__, elf._parse_relocatable_indexed.__code__,
                                 elf._parse_relocatable.__code__)
        return [_entry(original_stage.__code__, [_entry(read, count=2), _entry(indexed, count=1)]),
                _entry(read), _entry(indexed, [_entry(parsed, count=1)]), _entry(parsed)]

    monkeypatch.setattr(sys.modules["cProfile"].Profile, "getstats", getstats)
    fake.modules, fake.pipeline, fake.inputs, fake.linker, fake.elf = modules, pipeline, inputs, linker, elf
    fake.store_type, fake.original_link, fake.original_stage = Store, original_link, original_stage
    fake.config["identity"].update(
        target=adapter.ELF_TARGET, capture="ElfInputStore.stage",
        owner_sources={name.replace(".", "/") + ".py":
                       adapter._digest(fake.root / (name.replace(".", "/") + ".py"))
                       for name in (*adapter.ELF_OWNERS, adapter.MODULE)})
    fake.artifacts = [("first", "PCO", str(fake.root / "first.pco")),
                      (adapter.MODULE, "PCO", str(fake.root / "selected.pco")),
                      ("last", "ASM", str(fake.root / "last.s"))]
    return fake


def _elf_install_environment(fake, monkeypatch):
    setup = fake.directory / "setup.json"
    setup.write_text(json.dumps({"identity": fake.config["identity"]}))
    for key, value in _elf_ordinary_environment().items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(adapter.FLAG, raising=False)
    monkeypatch.setenv(adapter.CONFIG, str(setup))
    monkeypatch.setenv(adapter.SOURCE_ROOT, str(fake.root))
    command = _elf_argv(fake.root)
    monkeypatch.setenv(adapter.ELF_ARGV, json.dumps(command, separators=(",", ":")))
    monkeypatch.setattr(sys, "orig_argv", command)
    return command


def _elf_call(fake, **kwargs):
    return fake.pipeline._link_with_self_backend_direct_artifacts(
        fake.artifacts, "ordinary-output", None, False, **kwargs)


def test_arm_disabled_workers_mismatched_argv_and_occupied_hooks_never_import(tmp_path, monkeypatch):
    cases = ("disabled", "worker", "changed_output", "changed_command", "long_argv", "both_modes",
             "wrong_host", "wrong_python", "nonordinary", "legacy", "monitoring")
    for case in cases:
        directory = tmp_path / case
        directory.mkdir()
        with monkeypatch.context() as patch:
            fake = _elf_fake(directory, patch)
            command = _elf_install_environment(fake, patch)
            if case == "disabled":
                patch.delenv(adapter.ELF_FLAG)
            elif case == "worker":
                patch.setattr(sys, "orig_argv", _argv(directory / "never-open-manifest"))
            elif case == "changed_output":
                patch.setattr(sys, "orig_argv", command[:-1] + ["different-output"])
            elif case == "changed_command":
                patch.setattr(sys, "orig_argv", command + ["--extra"])
            elif case == "long_argv":
                long_command = command[:-1] + ["x" * 16385]
                patch.setattr(sys, "orig_argv", long_command)
                patch.setenv(adapter.ELF_ARGV, json.dumps(long_command, separators=(",", ":")))
            elif case == "both_modes":
                patch.setenv(adapter.FLAG, "1")
            elif case == "wrong_host":
                patch.setattr(adapter.platform, "machine", lambda: "x86_64")
            elif case == "wrong_python":
                patch.setattr(sys, "version_info", (3, 14, 0, "final", 0))
            elif case == "nonordinary":
                patch.setenv("PCC_DEFER_SELF_LINK_PLAN", "a-plan")
            elif case == "legacy":
                fake.control.active = object()
            elif case == "monitoring":
                fake.control.slot = "foreign-profiler"
            imported = []

            def forbidden_import(name):
                imported.append(name)
                pytest.fail("rejected ARM coordinator imported a module")

            patch.setattr(adapter.importlib, "import_module", forbidden_import)
            before = dict(sys.modules)
            hooks, argv, active, slot = (tuple(sys.meta_path), tuple(sys.orig_argv),
                                        fake.control.active, fake.control.slot)
            adapter.install()
            after = dict(sys.modules)
            assert set(after).difference(before) <= {"encodings.ascii"}
            assert tuple(name for name in after if name in before) == tuple(before)
            assert all(after[name] is module for name, module in before.items())
            assert tuple(sys.meta_path) == hooks and tuple(sys.orig_argv) == argv
            assert fake.control.active is active and fake.control.slot == slot
            assert imported == [] and fake.events == []
            assert fake.pipeline._link_with_self_backend_direct_artifacts is fake.original_link
            assert fake.store_type.stage is fake.original_stage
            assert not (fake.directory / "claim.json").exists()
            if case in ("legacy", "monitoring"):
                assert json.loads((fake.directory / "install-status.json").read_text())["status"] == "PROFILE_HOOK_CONFLICT"


def test_arm_prepare_requires_exclusive_mode_exact_command_environment_and_sources(tmp_path, monkeypatch):
    fake = _elf_fake(tmp_path, monkeypatch)
    env, command = _elf_ordinary_environment(), _elf_argv(fake.root)
    env["KEEP"] = "ordinary"
    before = dict(env)
    directory = tmp_path / "prepared"
    prepared = adapter.prepare_environment(fake.root, directory, env, {"commit": "a" * 40},
                                            {"path": "/private/runtime", "sha256": "b" * 64},
                                            "c" * 64, adapter.ELF_TARGET, command=command)
    assert prepared is not env and env == before and prepared["KEEP"] == "ordinary"
    assert json.loads(prepared[adapter.ELF_ARGV]) == command
    assert prepared[adapter.SOURCE_ROOT] == str(fake.root)
    assert prepared["PYTHONPATH"] == str(directory) + adapter.os.pathsep + env["PYTHONPATH"]
    setup = (directory / "setup.json").read_text()
    identity = json.loads(setup)["identity"]
    assert str(fake.root) not in setup and "/private" not in setup
    assert identity["capture"] == "ElfInputStore.stage" and identity["target"] == adapter.ELF_TARGET
    assert set(identity["owner_sources"]) == {name.replace(".", "/") + ".py"
                                              for name in (*adapter.ELF_OWNERS, adapter.MODULE)}
    assert (directory / "_pcc_host_assembly_profile.py").read_bytes() == Path(adapter.__file__).read_bytes()
    rejected = [(dict(env, **{adapter.FLAG: "1"}), command, "PROFILE_MODE_CONFLICT"),
                (env, None, "COORDINATOR_ADMISSION_CONFLICT"),
                (env, command + ["--extra"], "COORDINATOR_ADMISSION_CONFLICT"),
                (env, command[:6] + ["on"] + command[7:], "COORDINATOR_ADMISSION_CONFLICT"),
                (env, command[:8] + ["foreign-main.py"] + command[9:], "COORDINATOR_ADMISSION_CONFLICT"),
                (env, command[:-1] + [""], "COORDINATOR_ADMISSION_CONFLICT"),
                (env, command[:-1] + ["x" * 16385], "COORDINATOR_ADMISSION_CONFLICT")]
    for name in ("PCC_SELF_LINK", "PCC_SELF_OBJ", "PCC_DIRECT_INDEXED_KERNEL_CAPTURE",
                 "PCC_DIRECT_INDEXED_KERNEL_EMIT", "PCC_DEFER_FRONTEND_CODEGEN",
                 "PCC_DEFER_FRONTEND_CODEGEN_PLAN", "PCC_DEFER_FRONTEND_OUTPUT", "PCC_DEFER_SELF_LINK_PLAN"):
        missing = dict(env)
        del missing[name]
        rejected.append((missing, command, "COORDINATOR_ADMISSION_CONFLICT"))
    for index, (environment, argv, status) in enumerate(rejected):
        blocked = tmp_path / ("rejected-" + str(index))
        assert adapter.prepare_environment(fake.root, blocked, environment, {}, {}, "", adapter.ELF_TARGET,
                                           command=argv) is environment
        assert json.loads((blocked / "install-status.json").read_text())["status"] == status
        assert not (blocked / "sitecustomize.py").exists()
    for kind in ("legacy", "monitoring", "startup"):
        with monkeypatch.context() as patch:
            if kind == "legacy":
                patch.setattr(fake.control, "active", object())
            elif kind == "monitoring":
                patch.setattr(fake.control, "slot", "occupied")
            else:
                patch.setitem(sys.modules, "sitecustomize", ModuleType("sitecustomize"))
            blocked = tmp_path / kind
            assert adapter.prepare_environment(fake.root, blocked, env, {}, {}, "", adapter.ELF_TARGET,
                                               command=command) is env
            expected = "SITECUSTOMIZE_CONFLICT" if kind == "startup" else "PROFILE_HOOK_CONFLICT"
            assert json.loads((blocked / "install-status.json").read_text())["status"] == expected
    for kind in ("machine", "python", "platform", "target"):
        with monkeypatch.context() as patch:
            target = adapter.ELF_TARGET
            if kind == "machine":
                patch.setattr(adapter.platform, "machine", lambda: "x86_64")
            elif kind == "python":
                patch.setattr(sys, "version_info", (3, 14, 0, "final", 0))
            elif kind == "platform":
                patch.setattr(sys, "platform", "win32")
            else:
                target = adapter.TARGET
            blocked = tmp_path / kind
            assert adapter.prepare_environment(fake.root, blocked, env, {}, {}, "", target,
                                               command=command) is env
            assert json.loads((blocked / "install-status.json").read_text())["status"] == "UNSUPPORTED_HOST"
    assert fake.events == []


def test_arm_install_verifies_fake_owner_origins_hashes_and_original_function_identity(tmp_path, monkeypatch):
    cases = ("origin", "sha", "link_module", "link_qualname", "link_filename",
             "stage_module", "stage_qualname", "stage_filename", "store_alias", "valid")
    for case in cases:
        directory = tmp_path / case
        directory.mkdir()
        with monkeypatch.context() as patch:
            fake = _elf_fake(directory, patch)
            _elf_install_environment(fake, patch)
            if case == "origin":
                fake.elf.__file__ = str(directory / "foreign.py")
            elif case == "sha":
                Path(fake.linker.__file__).write_text("# changed fake owner\n")
            elif case == "store_alias":
                fake.linker.ElfInputStore = type("OtherStore", (), {})
            elif case != "valid":
                function = fake.original_link if case.startswith("link_") else fake.original_stage
                if case.endswith("module"):
                    function.__module__ = "foreign.module"
                elif case.endswith("qualname"):
                    function.__qualname__ = "foreign_wrapper"
                else:
                    function.__code__ = function.__code__.replace(co_filename=str(directory / "foreign.py"))
            imported = []
            owners = dict(zip(adapter.ELF_OWNERS, fake.modules))

            def load(name):
                imported.append(name)
                return owners[name]

            patch.setattr(adapter.importlib, "import_module", load)
            adapter.install()
            status = json.loads((fake.directory / "install-status.json").read_text())["status"]
            assert imported == list(adapter.ELF_OWNERS) and fake.events == []
            assert fake.store_type.stage is fake.original_stage
            if case == "valid":
                assert status == "INSTALLED"
                assert fake.pipeline._link_with_self_backend_direct_artifacts is not fake.original_link
                assert _elf_call(fake) is fake.control.link_result
                assert _result(fake)["capture_valid"]
            else:
                assert status == ("OWNER_IDENTITY_MISMATCH" if case in ("origin", "sha") else "OWNER_HOOK_CONFLICT")
                assert fake.pipeline._link_with_self_backend_direct_artifacts is fake.original_link
                assert not (fake.directory / "claim.json").exists()


def test_arm_selection_uses_exact_containers_and_rejects_selected_path_aliases(tmp_path, monkeypatch):
    selected, first = str(tmp_path / "selected.pco"), str(tmp_path / "first.pco")
    valid = [("first", "PCO", first), (adapter.MODULE, "PCO", selected)]
    touched = []

    class Iterator:
        def __iter__(self):
            touched.append("iter")
            raise AssertionError("custom iterator consumed")

    class List(list):
        def __iter__(self):
            touched.append("list_iter")
            raise AssertionError("custom list consumed")

    class Tuple(tuple):
        pass

    class Text(str):
        def __str__(self):
            touched.append("str")
            raise AssertionError("custom text converted")

    class PathLike:
        def __fspath__(self):
            touched.append("fspath")
            raise AssertionError("custom path converted")

    def forbidden_file_read(*args, **kwargs):
        touched.append("file")
        raise AssertionError("selection inspected an input file")

    monkeypatch.setattr(Path, "open", forbidden_file_read)
    monkeypatch.setattr(adapter.os.path, "isfile", forbidden_file_read)
    assert adapter._elf_selected_path(valid) == selected
    assert adapter._elf_selected_path(valid + [("other", "PCO", first)]) == selected
    invalid = [Iterator(), List(valid), tuple(valid), [],
               [Tuple(valid[1])], [[adapter.MODULE, "PCO", selected]],
               [(Text(adapter.MODULE), "PCO", selected)], [(adapter.MODULE, Text("PCO"), selected)],
               [(adapter.MODULE, "PCO", Text(selected))], [(adapter.MODULE, "PCO", PathLike())],
               [(adapter.MODULE, "PCO", "x" * 4097)],
               [(adapter.MODULE, "ASM", selected)], valid + [valid[1]],
               valid + [("alias", "ASM", str(tmp_path / "alias" / ".." / "selected.pco"))],
               [("other", "PCO", selected)], valid + [("broken", "PCO")],
               [("other", "PCO", first)] * 4096 + [valid[1]]]
    for artifacts in invalid:
        assert adapter._elf_selected_path(artifacts) is None
    assert touched == []


def test_arm_only_nominated_natural_stage_is_profiled_and_published_before_link_finishes(tmp_path, monkeypatch):
    fake = _elf_fake(tmp_path, monkeypatch)
    marker, profile, runtime, output = object(), object(), object(), object()
    fake.control.stage_result = marker
    before_artifacts = list(fake.artifacts)
    selected = fake.artifacts[1][2]
    calls = []

    def ordinary_link(artifacts, out_path, runtime_archive, verbose, **kwargs):
        assert artifacts is fake.artifacts and out_path is output and runtime_archive is runtime
        assert verbose is True and kwargs == {"profile": profile, "extra_link_args": ("ordinary",)}
        store = fake.store_type()
        assert store.stage(fake.artifacts[0][2], kind="PCO") is marker
        assert not (fake.directory / "claim.json").exists()
        rejected_calls = [((selected,), {"kind": "ASM"}), ((selected,), {"offset": 1}),
                          ((selected,), {"offset": False}), ((selected,), {"size": 0}),
                          ((selected,), {"label": "archive-member"})]
        for args, keywords in rejected_calls:
            assert store.stage(*args, **keywords) is marker
            assert not (fake.directory / "claim.json").exists()
        assert fake.store_type(adapter.TARGET).stage(selected) is marker
        Derived = type("Derived", (fake.store_type,), {})
        assert Derived().stage(selected) is marker
        assert not (fake.directory / "claim.json").exists()
        count = len(store.sources)
        assert store.stage(path=selected, kind="PCO", offset=0, size=None, label="") is marker
        calls.append(count)
        assert (fake.directory / "result.json").exists()
        assert _result(fake)["input"] == {"module": adapter.MODULE, "kind": "PCO", "bytes": 37, "sha256": "d" * 64}
        assert fake.store_type.stage is fake.original_stage
        assert store.stage(fake.artifacts[2][2], kind="ASM") is marker
        return marker

    fake.control.link_callback = ordinary_link
    adapter._attach_elf(fake.config, *fake.modules)
    assert fake.pipeline._link_with_self_backend_direct_artifacts(
        fake.artifacts, output, runtime, True, profile=profile, extra_link_args=("ordinary",)) is marker
    assert fake.artifacts == before_artifacts and calls == [6]
    assert fake.pipeline._link_with_self_backend_direct_artifacts is fake.original_link
    names = [row[0] for row in fake.events]
    assert names.count("construct") == names.count("enable") == names.count("disable") == 1
    assert names.count("getstats") == names.count("clear") == 1
    stages = [row for row in fake.events if row[0] == "stage"]
    assert [row[2] for row in stages] == [fake.artifacts[0][2]] + [selected] * 8 + [fake.artifacts[2][2]]
    assert [row[-1] for row in stages] == [None] * 8 + ["cProfile", None]
    result = _result(fake)
    assert result["status"] == "STAGE_RETURNED" and result["capture_valid"]
    assert "PCO input" in result["attribution"] and "must not be summed" in result["attribution"]
    assert sorted(edge["calls"] for edge in result["key_caller_edges"]) == [1, 1, 2]
    assert len(result["functions"]) <= 2 * adapter.TOP
    assert (fake.directory / "result.json").stat().st_size <= adapter.REPORT_LIMIT


def test_arm_original_baseexception_identity_survives_immediate_report_failures(tmp_path, monkeypatch):
    for index, error in enumerate((ValueError("ordinary"), KeyboardInterrupt(), SystemExit(7), GeneratorExit())):
        directory = tmp_path / str(index)
        directory.mkdir()
        with monkeypatch.context() as patch:
            fake = _elf_fake(directory, patch)
            fake.artifacts = [fake.artifacts[1]]
            fake.control.stage_error = error
            if index % 2:
                fake.control.stats_error = SystemExit(9)
            adapter._attach_elf(fake.config, *fake.modules)
            with pytest.raises(type(error)) as caught:
                _elf_call(fake)
            assert caught.value is error
            assert [row[0] for row in fake.events].count("stage") == 1
            result = _result(fake)
            if index % 2:
                assert result["status"] == "PROFILE_REPORT_FAILED" and result["stage_status"] == "STAGE_RAISED"
                assert not result["capture_valid"] and "functions" not in result
            else:
                assert result["status"] == "STAGE_RAISED"
            assert ("disable",) in fake.events and ("clear",) in fake.events
            assert fake.store_type.stage is fake.original_stage
            assert fake.pipeline._link_with_self_backend_direct_artifacts is fake.original_link


def test_arm_claim_prevents_retry_capture_and_unreached_call_leaves_no_stats(tmp_path, monkeypatch):
    fake = _elf_fake(tmp_path, monkeypatch)
    fake.artifacts = [fake.artifacts[1]]
    write = adapter._write

    def unavailable_publication(path, value):
        if Path(path).name == "result.json":
            raise SystemExit("simulated publication loss; no process is killed")
        return write(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(adapter, "_write", unavailable_publication)
        adapter._attach_elf(fake.config, *fake.modules)
        assert _elf_call(fake) is fake.control.link_result
    claim = (fake.directory / "claim.json").read_bytes()
    assert not (fake.directory / "result.json").exists()
    retry_path = tmp_path / "retry"
    retry_path.mkdir()
    retry = _elf_fake(retry_path, monkeypatch)
    retry.config["directory"] = str(fake.directory)
    retry.artifacts = [retry.artifacts[1]]
    adapter._attach_elf(retry.config, *retry.modules)
    assert _elf_call(retry) is retry.control.link_result
    assert [row[0] for row in retry.events].count("stage") == 1
    assert ("construct", True, True) not in retry.events
    assert (fake.directory / "claim.json").read_bytes() == claim
    assert not (fake.directory / "result.json").exists()
    assert retry.store_type.stage is retry.original_stage
    missing_path = tmp_path / "unreached"
    missing_path.mkdir()
    missing = _elf_fake(missing_path, monkeypatch)
    missing.control.link_callback = lambda *args, **kwargs: missing.control.link_result
    adapter._attach_elf(missing.config, *missing.modules)
    assert _elf_call(missing) is missing.control.link_result
    assert json.loads((missing.directory / "selection-status.json").read_text())["status"] == "SELECTED_STAGE_NOT_REACHED"
    assert not (missing.directory / "claim.json").exists() and not (missing.directory / "result.json").exists()
    assert missing.store_type.stage is missing.original_stage
    assert missing.pipeline._link_with_self_backend_direct_artifacts is missing.original_link


def test_arm_profile_failures_and_foreign_hooks_never_change_stage_outcome_or_clear_foreign_slots(tmp_path, monkeypatch):
    cases = ("constructor", "missing_profile", "enable", "disable", "occupied_legacy",
             "occupied_monitoring", "foreign_legacy", "foreign_monitoring")
    for case in cases:
        directory = tmp_path / case
        directory.mkdir()
        with monkeypatch.context() as patch:
            fake = _elf_fake(directory, patch)
            fake.artifacts = [fake.artifacts[1]]
            marker, hook = object(), object()
            fake.control.stage_result = marker
            forbidden = []
            patch.setattr(sys, "setprofile", lambda *args: forbidden.append("setprofile"))
            patch.setattr(sys.monitoring, "free_tool_id", lambda *args: forbidden.append("free_tool_id"), raising=False)
            patch.setattr(sys.monitoring, "set_events", lambda *args: forbidden.append("set_events"), raising=False)
            patch.setattr(sys.monitoring, "register_callback", lambda *args: forbidden.append("register_callback"), raising=False)
            if case == "constructor":
                fake.control.construct_error = KeyboardInterrupt()
            elif case == "missing_profile":
                patch.delattr(sys.modules["cProfile"], "Profile")
            elif case == "enable":
                fake.control.enable_error = SystemExit(3)
            elif case == "disable":
                fake.control.disable_error = SystemExit(4)

            def ordinary_link(artifacts, *args, **kwargs):
                if case == "occupied_legacy":
                    fake.control.active = hook
                elif case == "occupied_monitoring":
                    fake.control.slot = "foreign-profiler"
                return fake.store_type().stage(artifacts[0][2], kind="PCO")

            def during_stage(*args):
                if case == "foreign_legacy":
                    fake.control.active = hook
                elif case == "foreign_monitoring":
                    fake.control.slot = "foreign-profiler"

            fake.control.link_callback, fake.control.stage_callback = ordinary_link, during_stage
            adapter._attach_elf(fake.config, *fake.modules)
            assert _elf_call(fake) is marker
            result = _result(fake)
            assert result["status"] == "STAGE_RETURNED" and not result["capture_valid"]
            assert "functions" not in result and ("getstats",) not in fake.events
            assert [row[0] for row in fake.events].count("stage") == 1 and forbidden == []
            errors = result["diagnostic_errors"]
            if case in ("constructor", "missing_profile"):
                assert "profile_setup_failed" in errors and ("enable",) not in fake.events
            elif case.startswith("occupied_"):
                assert "profile_hook_conflict" in errors and ("construct", True, True) not in fake.events
            elif case == "enable":
                assert "enable_failed" in errors and "profile_state_not_restored" in errors
            elif case == "disable":
                assert "disable_failed_state_not_restored" in errors
            elif case == "foreign_legacy":
                assert "foreign_legacy_hook_preserved" in errors
                assert fake.control.active is hook and fake.control.slot is None and ("disable",) in fake.events
            else:
                assert "foreign_monitoring_slot_preserved" in errors and ("disable",) not in fake.events
            if case in ("enable", "disable", "foreign_monitoring"):
                assert adapter._FAILED_PROFILER is not None and ("clear",) not in fake.events
            if case in ("occupied_monitoring", "foreign_monitoring"):
                assert fake.control.slot == "foreign-profiler"
            if case == "occupied_legacy":
                assert fake.control.active is hook
            assert fake.store_type.stage is fake.original_stage
            assert fake.pipeline._link_with_self_backend_direct_artifacts is fake.original_link


def test_arm_metadata_and_report_failures_are_bounded_and_restore_only_owned_wrappers(tmp_path, monkeypatch):
    cases = ("count_zero", "count_two", "path", "kind", "whole_file", "size", "sha", "stats", "foreign_wrappers")
    for case in cases:
        directory = tmp_path / case
        directory.mkdir()
        with monkeypatch.context() as patch:
            fake = _elf_fake(directory, patch)
            fake.artifacts = [fake.artifacts[1]]
            if case in ("count_zero", "count_two"):
                fake.control.source_count_delta = 0 if case == "count_zero" else 2
            elif case == "stats":
                fake.control.stats_error = KeyboardInterrupt()
            elif case != "foreign_wrappers":
                def source_factory(path, kind, offset, size, label):
                    values = dict(path=path, kind=kind, whole_file=True, size=37, sha256="d" * 64)
                    values[case] = {"path": "other", "kind": "ASM", "whole_file": False,
                                    "size": True, "sha": "not-a-digest"}[case]
                    if case == "sha":
                        values["sha256"] = values.pop("sha")
                    return SimpleNamespace(**values)
                fake.control.source_factory = source_factory
            foreign_stage = lambda *args, **kwargs: None
            foreign_link = lambda *args, **kwargs: None
            if case == "foreign_wrappers":
                def replace_wrappers(*args):
                    fake.store_type.stage = foreign_stage
                    fake.pipeline._link_with_self_backend_direct_artifacts = foreign_link
                fake.control.stage_callback = replace_wrappers
            adapter._attach_elf(fake.config, *fake.modules)
            assert _elf_call(fake) is fake.control.link_result
            result = _result(fake)
            if case == "foreign_wrappers":
                assert result["status"] == "STAGE_RETURNED" and result["capture_valid"]
                assert fake.store_type.stage is foreign_stage
                assert fake.pipeline._link_with_self_backend_direct_artifacts is foreign_link
            else:
                assert result["status"] == "PROFILE_REPORT_FAILED" and not result["capture_valid"]
                assert result["stage_status"] == "STAGE_RETURNED" and "functions" not in result
                assert fake.store_type.stage is fake.original_stage
                assert fake.pipeline._link_with_self_backend_direct_artifacts is fake.original_link
            assert [row[0] for row in fake.events].count("stage") == 1
            assert ("clear",) in fake.events
            assert (fake.directory / "result.json").stat().st_size <= adapter.REPORT_LIMIT

    predicate_path = tmp_path / "predicate_baseexception"
    predicate_path.mkdir()
    with monkeypatch.context() as patch:
        fake = _elf_fake(predicate_path, patch)
        marker = object()
        fake.control.stage_result = marker

        class RaisingTarget(fake.store_type):
            @property
            def target(self):
                raise KeyboardInterrupt("observation-only target lookup")

            @target.setter
            def target(self, value):
                self._ordinary_target = value

        fake.inputs.ElfInputStore = fake.linker.ElfInputStore = RaisingTarget
        fake.control.link_callback = lambda *args, **kwargs: RaisingTarget().stage(fake.artifacts[1][2])
        adapter._attach_elf(fake.config, *fake.modules)
        assert _elf_call(fake) is marker
        assert [row[0] for row in fake.events].count("stage") == 1
        assert ("construct", True, True) not in fake.events
        assert not (fake.directory / "claim.json").exists() and not (fake.directory / "result.json").exists()
        assert RaisingTarget.stage is fake.original_stage
        assert fake.pipeline._link_with_self_backend_direct_artifacts is fake.original_link
        assert json.loads((fake.directory / "selection-status.json").read_text())["status"] == "SELECTED_STAGE_NOT_REACHED"

    restore_path = tmp_path / "restore_failure"
    restore_path.mkdir()
    with monkeypatch.context() as patch:
        fake = _elf_fake(restore_path, patch)
        marker, restoration = object(), {"deny": False}
        fake.control.stage_result = marker
        original_stage = fake.original_stage

        class GuardedType(type):
            def __setattr__(cls, name, value):
                if name == "stage" and value is original_stage and restoration["deny"]:
                    raise SystemExit("fake owner refuses wrapper restoration")
                return super().__setattr__(name, value)

        GuardedStore = GuardedType("GuardedStore", (fake.store_type,), {"stage": original_stage})
        fake.inputs.ElfInputStore = fake.linker.ElfInputStore = GuardedStore
        saved = []

        def link_without_selected_call(*args, **kwargs):
            saved.append(GuardedStore.stage)
            restoration["deny"] = True
            return marker

        fake.control.link_callback = link_without_selected_call
        adapter._attach_elf(fake.config, *fake.modules)
        assert _elf_call(fake) is marker
        assert fake.pipeline._link_with_self_backend_direct_artifacts is fake.original_link
        assert GuardedStore.stage is saved[0] and saved[0] is not original_stage
        assert json.loads((fake.directory / "selection-status.json").read_text())["status"] == "OWNER_RESTORE_FAILED"
        # The first link never claimed. Its failed restoration must not let
        # the leftover wrapper capture a later call after its path is dropped.
        store, selected = GuardedStore(), fake.artifacts[1][2]
        assert store.stage(selected, kind="PCO") is marker
        assert saved[0](store, path=selected, kind="PCO") is marker
        restoration["deny"] = False
        GuardedStore.stage = original_stage
        assert saved[0](store, selected) is marker
        assert [row[0] for row in fake.events].count("stage") == 3
        assert ("construct", True, True) not in fake.events
        assert not (fake.directory / "claim.json").exists() and not (fake.directory / "result.json").exists()


def test_arm_adapter_retains_no_store_source_result_or_original_traceback(tmp_path, monkeypatch):
    fake = _elf_fake(tmp_path, monkeypatch)
    fake.artifacts = [fake.artifacts[1]]
    refs = []

    class Source:
        pass

    class Result:
        @property
        def data(self):
            pytest.fail("payload property was inspected")

        @property
        def relocations(self):
            pytest.fail("relocations property was inspected")

        @property
        def sections(self):
            pytest.fail("sections property was inspected")

    def source_factory(path, kind, offset, size, label):
        source = Source()
        source.path, source.kind, source.whole_file = path, kind, True
        source.size, source.sha256 = 91, "e" * 64
        refs.append(weakref.ref(source))
        return source

    def result_factory():
        result = Result()
        refs.append(weakref.ref(result))
        return result

    fake.control.source_factory, fake.control.result_factory = source_factory, result_factory
    fake.control.store_observer = lambda store: refs.append(weakref.ref(store))
    adapter._attach_elf(fake.config, *fake.modules)
    wrapper = fake.pipeline._link_with_self_backend_direct_artifacts
    assert _elf_call(fake) is fake.control.link_result
    assert len(refs) == 3 and all(reference() is None for reference in refs)
    assert _result(fake)["input"]["bytes"] == 91
    # Keep the detached wrapper alive: its closure must still retain no inputs.
    assert wrapper is not fake.original_link
    failure_path = tmp_path / "exception"
    failure_path.mkdir()
    failure = _elf_fake(failure_path, monkeypatch)
    failure.artifacts = [failure.artifacts[1]]
    failure_refs = []

    class Failure(BaseException):
        pass

    def raise_original(store, *args):
        local = Source()
        failure_refs.extend((weakref.ref(store), weakref.ref(local)))
        raise Failure("ordinary failure")

    failure.control.stage_callback = raise_original
    adapter._attach_elf(failure.config, *failure.modules)
    failed_wrapper = failure.pipeline._link_with_self_backend_direct_artifacts
    try:
        _elf_call(failure)
    except Failure as error:
        # Clear only the caller-owned traceback after checking identity/type;
        # any adapter-held traceback would still retain its frame locals.
        error.__traceback__ = None
    else:
        pytest.fail("ordinary BaseException was swallowed")
    assert len(failure_refs) == 2 and all(reference() is None for reference in failure_refs)
    assert failed_wrapper is not failure.original_link
    assert failure.store_type.stage is failure.original_stage
    assert _result(failure)["status"] == "STAGE_RAISED"
