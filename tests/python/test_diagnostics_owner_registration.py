"""Fresh imports retain diagnostic contracts without initializing other owners.

Language Reference 5.3, 5.4 and 7.11: initialization publishes a live module,
finishes before from-import binds an attribute, and uses the executing module.
These are host import/diagnostic checks, not native self-host qualification.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[2]
OWNERS = (
    ("pcc.driver.cli_core", "parse_cli_args", "_install_cli_core"),
    ("pcc.frontends.python.pipeline", "compile_python", "_install_pipeline_profile"),
    ("pcc.frontends.python.ir_pass_pipeline", "run_python_ir_pass_pipeline",
     "_install_ir_pass_explain_and_tailcall"),
    ("pcc.driver.project", "collect_translation_units", "_install_project_cache_explain"),
)
DIAGNOSTIC_ENV = (
    "PCC_DISABLE_ROADMAP_DEEPWIRE", "PCC_PROFILE_JSON", "PCC_DIAGNOSTIC_FORMAT",
    "PCC_EXPLAIN_FALLBACK", "PCC_PASSES_EXPLAIN", "PCC_PASSES_EXPLAIN_FORMAT",
    "PCC_PASSES_EXPLAIN_PATH", "PCC_ENABLE_TAILCALL_REWRITE", "PCC_TAILCALL_REPORT",
    "PCC_EXPLAIN_CACHE",
)


def _environment():
    env = dict(os.environ)
    for name in DIAGNOSTIC_ENV:
        env.pop(name, None)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(tmp_path, code, *, env=None):
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(code)],
        cwd=tmp_path, env=_environment() if env is None else env,
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == "registration-ok\n", result.stdout
    assert result.stderr == "", result.stderr


def test_leaf_imports_do_not_initialize_compiler_owners(tmp_path):
    names = tuple(owner[0] for owner in OWNERS)
    _run(tmp_path, f'''
        import importlib
        import sys
        import pcc
        owners = {names!r}
        assert not any(name in sys.modules for name in owners)
        for name in ("pcc.frontends.python.worker_resource_plan",
                     "pcc.frontends.python.pipeline_frontend_workers",
                     "pcc.frontends.python.worker_process_pool"):
            importlib.import_module(name)
            assert not any(owner in sys.modules for owner in owners)
        from pcc.frontends.python.worker_resource_plan import publish_worker_resource
        assert callable(publish_worker_resource)
        print("registration-ok")
    ''')


@pytest.mark.parametrize("first", range(len(OWNERS)))
def test_each_owner_registers_before_import_returns_and_only_once(tmp_path, first):
    order = OWNERS[first:] + OWNERS[:first]
    _run(tmp_path, f'''
        import importlib
        from pcc.diagnostics import wiring
        loaded = []
        for name, attribute, installer in {order!r}:
            owner = importlib.import_module(name)
            function = getattr(owner, attribute)
            assert hasattr(function, "__wrapped__"), name
            assert not hasattr(function.__wrapped__, "__wrapped__"), name
            loaded.append((owner, attribute, function))
            getattr(wiring, installer)(owner)
            assert getattr(owner, attribute) is function
        wiring._LAST_CLI_OBSERVABILITY["profile_json"] = "kept.json"
        for _ in range(3):
            wiring.install()
        assert wiring._LAST_CLI_OBSERVABILITY == {{"profile_json": "kept.json"}}
        for owner, attribute, function in loaded:
            assert getattr(owner, attribute) is function
        from pcc.driver import cli_core
        assert cli_core._HELP_TEXT.count("--diagnostic-format FMT") == 1
        print("registration-ok")
    ''')


@pytest.mark.parametrize("disabled", (False, True))
def test_disable_decision_is_frozen_before_late_owners(tmp_path, disabled):
    env = _environment()
    if disabled:
        env["PCC_DISABLE_ROADMAP_DEEPWIRE"] = "1"
    _run(tmp_path, f'''
        import importlib
        import os
        import pcc
        from pcc.diagnostics import wiring
        if {disabled!r}:
            os.environ.pop("PCC_DISABLE_ROADMAP_DEEPWIRE")
        else:
            os.environ["PCC_DISABLE_ROADMAP_DEEPWIRE"] = "1"
        importlib.reload(pcc)
        wiring.install()
        for name, attribute, _ in {OWNERS!r}:
            owner = importlib.import_module(name)
            assert hasattr(getattr(owner, attribute), "__wrapped__") is {not disabled!r}, name
        print("registration-ok")
    ''', env=env)


def test_from_import_and_cli_api_aliases_observe_wrappers(tmp_path):
    _run(tmp_path, '''
        from pcc.frontends.python.pipeline import compile_python
        from pcc.driver.cli_core import parse_cli_args
        from pcc.driver.project import collect_translation_units
        from pcc.driver import cli_bootstrap
        from pcc import api
        assert compile_python._pcc_profiled
        assert hasattr(parse_cli_args, "__wrapped__")
        assert hasattr(collect_translation_units, "__wrapped__")
        assert cli_bootstrap._compile_python is compile_python
        assert api.collect_translation_units is collect_translation_units
        print("registration-ok")
    ''')


@pytest.mark.parametrize("owner", OWNERS, ids=lambda owner: owner[0])
def test_ordinary_owner_reload_does_not_reinstall_wrappers(tmp_path, owner):
    _run(tmp_path, f'''
        import importlib
        from pcc.diagnostics import wiring
        owner = importlib.import_module({owner[0]!r})
        wrapped = getattr(owner, {owner[1]!r})
        assert hasattr(wrapped, "__wrapped__")
        assert importlib.reload(owner) is owner
        replacement = getattr(owner, {owner[1]!r})
        assert replacement is not wrapped
        assert not hasattr(replacement, "__wrapped__")
        wiring.install()
        assert getattr(owner, {owner[1]!r}) is replacement
        print("registration-ok")
    ''')


@pytest.mark.parametrize("owner", OWNERS, ids=lambda owner: owner[0])
def test_optional_installer_failure_does_not_break_owner_import(tmp_path, owner):
    _run(tmp_path, f'''
        import importlib
        import sys
        from pcc.diagnostics import wiring
        attempted = []
        def fail(owner):
            attempted.append(owner)
            assert owner is sys.modules[owner.__name__]
            assert callable(getattr(owner, {owner[1]!r}))
            raise RuntimeError("optional diagnostics unavailable")
        setattr(wiring, {owner[2]!r}, fail)
        owner = importlib.import_module({owner[0]!r})
        assert attempted == [owner]
        assert callable(getattr(owner, {owner[1]!r}))
        print("registration-ok")
    ''')


def test_required_owner_import_failure_is_not_suppressed(tmp_path):
    _run(tmp_path, '''
        import sys
        import pcc
        sys.modules["pcc.driver.cli_contract"] = None
        try:
            import pcc.driver.cli_core
        except ModuleNotFoundError:
            pass
        else:
            raise AssertionError("required dependency failure was suppressed")
        assert "pcc.driver.cli_core" not in sys.modules
        print("registration-ok")
    ''')


def test_diagnostic_flags_stay_dynamic_after_owner_import(tmp_path):
    _run(tmp_path, '''
        import json
        import os
        from pathlib import Path
        from pcc.frontends.python import pipeline, ir_pass_pipeline
        from pcc.driver import cli_core, project

        # Call the real profile wrapper, but stop at the existing routed boundary
        # before compiler/native work. Its original callable and exception survive.
        def fail(*args, **kwargs):
            raise ValueError("routed sentinel")
        pipeline._compile_python_routed = fail
        os.environ["PCC_PROFILE_JSON"] = "profile.json"
        try:
            pipeline.compile_python("missing.py", "unused")
        except ValueError as error:
            assert str(error) == "routed sentinel"
        else:
            raise AssertionError("profile wrapper swallowed the exception")
        data = json.loads(Path("profile.json").read_text())
        assert data["metadata"]["entry"] == "pcc.frontends.python.pipeline.compile_python"
        os.environ.pop("PCC_PROFILE_JSON")
        Path("profile.json").unlink()
        try:
            pipeline.compile_python("missing.py", "unused")
        except ValueError:
            pass
        assert not Path("profile.json").exists()

        ir = "define void @spin() {\\nentry:\\n  call void @spin()\\n  ret void\\n}\\n"
        assert ir_pass_pipeline.run_python_ir_pass_pipeline(
            ir, pass_names=(), module_name="plain") == ir
        os.environ["PCC_ENABLE_TAILCALL_REWRITE"] = "1"
        os.environ["PCC_PASSES_EXPLAIN"] = "1"
        os.environ["PCC_PASSES_EXPLAIN_PATH"] = "passes.txt"
        os.environ["PCC_TAILCALL_REPORT"] = "tailcall.json"
        out = ir_pass_pipeline.run_python_ir_pass_pipeline(
            ir, pass_names=(), module_name="dynamic")
        assert "pcc.tailcall.self" in out
        assert "tailcall:" in Path("passes.txt").read_text()
        assert Path("tailcall.json").exists()

        Path("input.c").write_text("int value;\\n")
        original = project.collect_project("input.c")
        os.environ["PCC_EXPLAIN_CACHE"] = "cache.jsonl"
        assert project.collect_project("input.c") == original
        records = Path("cache.jsonl").read_text().splitlines()
        assert len(records) == 1
        assert json.loads(records[0])["event"] == "collect_project"
        os.environ.pop("PCC_EXPLAIN_CACHE")
        assert project.collect_project("input.c") == original
        assert Path("cache.jsonl").read_text().splitlines() == records

        parsed, status, error = cli_core.parse_cli_args([
            "--diagnostic-format=json", "--profile-json", "cli.json", "input.py"])
        assert status == 0 and parsed is not None, error
        assert os.environ["PCC_DIAGNOSTIC_FORMAT"] == "json"
        assert os.environ["PCC_PROFILE_JSON"] == "cli.json"
        print("registration-ok")
    ''')


def test_ir_pass_module_main_wraps_the_executing_module(tmp_path):
    source = tmp_path / "tail.ll"
    source.write_text("define void @spin() {\nentry:\n  call void @spin()\n  ret void\n}\n")
    env = _environment()
    env["PCC_PASSES_EXPLAIN"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pcc.frontends.python.ir_pass_pipeline",
         "main-owner", "tailcall", str(source)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "pcc.tailcall.self" in result.stdout
    assert "call void @spin()" not in result.stdout
    assert "module=main-owner" in result.stderr
    assert "tailcall:" in result.stderr
    assert "RuntimeWarning" not in result.stderr
