"""Generic C++ package dispatch; no NumPy-specific compiler or build rules."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.package import build_exec
from pcc.package.build_plan import load_compile_commands, load_meson_introspection_commands


SOURCE = '''namespace arithmetic { int value() { return 40; } }
extern "C" int answer() { return arithmetic::value() + 2; }
'''


def project(tmp_path, suffix=".cpp", *, standard=None):
    root = tmp_path / "project"
    root.mkdir()
    source = root / ("answer" + suffix)
    source.write_text(SOURCE)
    argv = ["untrusted-c++"]
    if standard:
        argv.append("-std=" + standard)
    argv += ["-c", source.name, "-o", "answer.o"]
    (root / "compile_commands.json").write_text(json.dumps([
        {"directory": str(root), "file": source.name, "arguments": argv},
    ]))
    return root, source


@pytest.mark.parametrize("suffix", [".cc", ".cpp", ".cxx", ".c++", ".C"])
@pytest.mark.parametrize("graph", [False, True])
def test_generic_owned_cxx_plan_selects_public_frontend_without_host_tool_discovery(
    tmp_path, monkeypatch, suffix, graph,
):
    root, source = project(tmp_path, suffix)
    monkeypatch.setattr(build_exec, "toolchain_report", lambda **kw: pytest.fail("host toolchain"))
    monkeypatch.setattr(build_exec.subprocess, "run", lambda *a, **kw: pytest.fail("plan executed"))
    report = build_exec.execute_build_actions(
        "generic-project", root, from_compile_commands=graph, abi_mode="cpython-compat",
    )
    assert report["ok"], report
    row, = report["actions"]
    assert row["kind"] == "cxx_compile" and row["language"] == "cxx"
    assert row["command"][:3] == [sys.executable, "-m", "pcc"]
    assert row["command"][-1] == str(source)
    assert "untrusted-c++" not in row["command"]
    assert report["host_free_build_claim"] is False
    assert source.read_text() == SOURCE
    assert not (root / "build").exists()


def test_uppercase_c_source_is_not_relabelled_as_c_in_build_graph(tmp_path):
    root, _source = project(tmp_path, ".C")
    command, = load_compile_commands(root / "compile_commands.json")
    assert command.language == "cxx"


@pytest.mark.parametrize("suffix", [".CPP", ".CC", ".CXX"])
def test_graph_cxx_label_cannot_select_a_different_public_frontend(tmp_path, monkeypatch, suffix):
    root, _source = project(tmp_path, suffix)
    monkeypatch.setattr(build_exec.subprocess, "run", lambda *a, **kw: pytest.fail("wrong frontend executed"))
    report = build_exec.execute_build_actions(
        "generic-project", root, from_compile_commands=True, execute=True, abi_mode="cpython-compat",
    )
    assert not report["ok"]
    assert "source language does not match owned C/C++ suffix dispatch" in report["actions"][0]["stderr"]


@pytest.mark.parametrize("language,suffix", [
    ("c", ".cpp"), ("cpp", ".c"), ("cxx", ".c"),
    ("c", ".rs"), ("c", ".py"), ("rust", ".cpp"),
])
def test_meson_graph_language_is_not_replaced_by_source_suffix(
    tmp_path, monkeypatch, language, suffix,
):
    root, source = project(tmp_path, suffix)
    info = root / "meson-info"
    info.mkdir()
    (info / "intro-targets.json").write_text(json.dumps([{"target_sources": [{
        "language": language, "compiler": ["untrusted-compiler"],
        "sources": [str(source)], "parameters": [],
    }]}]))
    command, = load_meson_introspection_commands(root)
    assert command.language == {"cpp": "cxx", "rust": "unknown"}.get(language, language)
    monkeypatch.setattr(build_exec.subprocess, "run", lambda *a, **kw: pytest.fail("wrong language executed"))
    report = build_exec.execute_build_actions(
        "generic-project", root, from_meson_introspection=True, execute=True,
        abi_mode="cpython-compat",
    )
    assert not report["ok"] and report["actions"][0]["status"] == "blocked"
    assert not (root / "build").exists()


def test_resolved_source_suffix_must_match_the_public_cli_dispatch(tmp_path, monkeypatch):
    root, source = project(tmp_path)
    original = root / "original.c"
    original.write_text(SOURCE)
    source.unlink()
    source.symlink_to(original)
    monkeypatch.setattr(build_exec.subprocess, "run", lambda *a, **kw: pytest.fail("wrong resolved frontend"))
    report = build_exec.execute_build_actions(
        "generic-project", root, from_compile_commands=True, execute=True,
        abi_mode="cpython-compat",
    )
    assert not report["ok"]
    assert "source language does not match" in report["actions"][0]["stderr"]
    assert original.read_text() == SOURCE


@pytest.mark.parametrize("target", ["other.cpp", "header.h", "include/extensionless"])
def test_all_outputs_are_admitted_before_any_compiler_or_header_write(tmp_path, monkeypatch, target):
    root, source = project(tmp_path)
    protected = root / target
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text(SOURCE)
    entries = json.loads((root / "compile_commands.json").read_text())
    entries.append({"directory": str(root), "file": source.name,
                    "arguments": ["untrusted-c++", "-c", source.name, "-o", target]})
    if target.endswith(".cpp"):
        entries.append({"directory": str(root), "file": target,
                        "arguments": ["untrusted-c++", "-c", target, "-o", "other.o"]})
    (root / "compile_commands.json").write_text(json.dumps(entries))
    writes = []

    def materialize(directory, *, execute):
        writes.append(execute)
        assert not execute, "headers copied before whole-batch admission"
        return directory / "pcc-capi-include"

    monkeypatch.setattr(build_exec, "_materialize_pcc_capi_include", materialize)
    monkeypatch.setattr(build_exec, "_pcc_runtime_include_dir", lambda: root / "runtime-headers")
    monkeypatch.setattr(build_exec.subprocess, "run", lambda *a, **kw: pytest.fail("partial batch executed"))
    report = build_exec.execute_build_actions(
        "generic-project", root, from_compile_commands=True, execute=True,
    )
    assert not report["ok"] and all(row["status"] == "blocked" for row in report["actions"])
    assert writes == [False] and not (root / "build").exists()
    assert source.read_text() == protected.read_text() == SOURCE


def test_empty_staged_object_is_not_published_over_an_old_output(tmp_path, monkeypatch):
    root, source = project(tmp_path)
    output = root / "answer.o"
    output.write_bytes(b"old object stays intact")

    def empty_compile(command, **kwargs):
        Path(command[command.index("--emit-obj") + 1]).write_bytes(b"")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(build_exec.subprocess, "run", empty_compile)
    report = build_exec.execute_build_actions(
        "generic-project", root, from_compile_commands=True, execute=True,
        abi_mode="cpython-compat",
    )
    assert not report["ok"] and report["actions"][0]["status"] == "failed"
    assert output.read_bytes() == b"old object stays intact"
    assert source.read_text() == SOURCE


@pytest.mark.parametrize("hardlink", [False, True])
def test_owned_header_destination_cannot_rewrite_original_input(tmp_path, monkeypatch, hardlink):
    root, source = project(tmp_path)
    headers = root / "build/pcc-package/pcc-capi-include"
    headers.mkdir(parents=True)
    protected = root / "original-header.h"
    protected.write_text("/* original header bytes */\n")
    if hardlink:
        os.link(protected, headers / "Python.h")
    else:
        (headers / "Python.h").write_bytes(protected.read_bytes())
    monkeypatch.setattr(build_exec.subprocess, "run", lambda *a, **kw: pytest.fail("alias batch executed"))
    report = build_exec.execute_build_actions(
        "generic-project", root, from_compile_commands=True, execute=True,
    )
    assert not report["ok"]
    assert "PCC-PKG-OWNED-HEADER-OUTPUT-ALIASES-INPUT" in [row["code"] for row in report["diagnostics"]]
    assert protected.read_text() == "/* original header bytes */\n"
    assert (headers / "Python.h").read_bytes() == protected.read_bytes()
    assert source.read_text() == SOURCE


def test_object_suffix_does_not_authorize_overwriting_a_text_header(tmp_path, monkeypatch):
    root, source = project(tmp_path)
    header = root / "config.o"
    header.write_text("#define VALUE 42\n")
    source.write_text('#include "config.o"\nextern "C" int answer() { return VALUE; }\n')
    entries = json.loads((root / "compile_commands.json").read_text())
    entries[0]["arguments"][-1] = header.name
    (root / "compile_commands.json").write_text(json.dumps(entries))

    def fake_compile(command, **kwargs):
        Path(command[command.index("--emit-obj") + 1]).write_bytes(b"mock newly compiled object")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(build_exec.subprocess, "run", fake_compile)
    report = build_exec.execute_build_actions(
        "generic-project", root, from_compile_commands=True, execute=True,
        abi_mode="cpython-compat",
    )
    assert not report["ok"] and report["actions"][0]["status"] == "failed"
    assert "refuse to replace a non-object source/header input" in report["actions"][0]["stderr"]
    assert header.read_text() == "#define VALUE 42\n"


def test_cpp17_standard_reaches_existing_owned_preprocessor(tmp_path):
    root, _source = project(tmp_path, standard="c++17")
    report = build_exec.execute_build_actions(
        "generic-project", root, from_compile_commands=True, abi_mode="cpython-compat",
    )
    assert report["ok"], report
    assert "--cpp-arg=-std=c++17" in report["actions"][0]["command"]


@pytest.mark.parametrize("standard", ["gnu++17", "c++20", "c11"])
def test_unsupported_cxx_dialect_is_not_dropped_or_externally_compiled(tmp_path, monkeypatch, standard):
    root, _source = project(tmp_path, standard=standard)
    monkeypatch.setattr(build_exec.subprocess, "run", lambda *a, **kw: pytest.fail("external fallback"))
    report = build_exec.execute_build_actions(
        "generic-project", root, from_compile_commands=True, execute=True, abi_mode="cpython-compat",
    )
    assert not report["ok"]
    assert report["actions"][0]["status"] == "blocked"
    assert "PCC-PKG-OWNED-COMPILE-OPTION-UNAVAILABLE" in [row["code"] for row in report["diagnostics"]]


def test_cxx_object_dispatch_does_not_relax_shared_link_boundary(tmp_path, monkeypatch):
    root, _source = project(tmp_path)
    monkeypatch.setattr(build_exec.subprocess, "run", lambda *a, **kw: pytest.fail("external fallback"))
    report = build_exec.execute_build_actions(
        "generic-project", root, execute=True, link_output=tmp_path / "module.so",
        abi_mode="cpython-compat",
    )
    assert not report["ok"]
    assert "PCC-PKG-OWNED-SHARED-LINK-UNAVAILABLE" in [row["code"] for row in report["diagnostics"]]
    assert report["actions"][0]["kind"] == "cxx_compile"
    assert report["actions"][0]["status"] == "blocked"


def test_owned_cxx_child_cannot_inherit_the_external_object_emitter(tmp_path, monkeypatch):
    root, source = project(tmp_path)
    monkeypatch.setenv("PCC_SELF_OBJ", "system-as")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("PCC_SOURCE_ROOT", "/old/source")
    monkeypatch.setenv("PCC_REPO_ROOT", "/old/repository")
    monkeypatch.setenv("PCC_PY_STDLIB_ROOT", "/old/stdlib")
    observed = []

    def fake_compile(command, **kwargs):
        # This is only a subprocess-boundary contract test, not compiler proof.
        observed.append(kwargs["env"])
        assert kwargs["env"]["PCC_SELF_OBJ"] == "pcc"
        assert kwargs["env"]["LC_ALL"] == "C.UTF-8"
        assert kwargs["env"]["PCC_SOURCE_ROOT"] == kwargs["env"]["PCC_REPO_ROOT"] == str(build_exec._repo_root())
        assert kwargs["cwd"] == build_exec._repo_root()
        assert "--backend=self" in command
        Path(command[command.index("--emit-obj") + 1]).write_bytes(b"mock object")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(build_exec.subprocess, "run", fake_compile)
    report = build_exec.execute_build_actions(
        "generic-project", root, execute=True, from_compile_commands=True,
        abi_mode="cpython-compat",
    )
    assert report["ok"] and len(observed) == 1
    assert os.environ["PCC_SELF_OBJ"] == "system-as"
    assert os.environ["PCC_SOURCE_ROOT"] == "/old/source"
    assert os.environ["PCC_REPO_ROOT"] == "/old/repository"
    assert report["host_free_build_claim"] is False
    assert source.read_text() == SOURCE


@pytest.mark.integration
def test_actual_package_cpp_object_executes_namespace_body_through_owned_linker(tmp_path, monkeypatch):
    """CI execution obligation, not an assertion that native pcc1 built NumPy."""
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_elf_link import assemble

    assert sys.platform.startswith("linux"), "ELF owned-package execution requires Linux"
    root, source = project(tmp_path, standard="c++17")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("PCC_HOST_PYTHON", str(tmp_path / "forbidden-python"))
    monkeypatch.setenv("PCC_HOST_PCC", str(tmp_path / "forbidden-pcc"))
    report = build_exec.execute_build_actions(
        "generic-project", root, execute=True, from_compile_commands=True,
        abi_mode="cpython-compat", timeout=30,
    )
    assert report["ok"], report
    row, = report["actions"]
    assert row["status"] == "passed" and row["kind"] == "cxx_compile"
    assert report["compiler_execution"] == "cpython-hosted"
    assert report["host_free_build_claim"] is False
    obj = parse_relocatable(Path(row["output"]).read_bytes())
    assert any(symbol.name == "answer" and symbol.section_index != 0 for symbol in obj.symbols)
    if obj.machine == 62:
        target = "x86_64-unknown-linux-gnu"
        entry = ".intel_syntax noprefix\n.text\n.globl _start\n_start:\n  call answer\n  mov rdi, rax\n  mov eax, 60\n  syscall\n"
    else:
        assert obj.machine == 183
        target = "aarch64-unknown-linux-gnu"
        entry = ".text\n.globl _start\n_start:\n  bl answer\n  mov x8, #93\n  svc #0\n"
    binary = tmp_path / "answer"
    binary.write_bytes(link_static_executable([assemble(entry, target), obj], entry="_start"))
    binary.chmod(0o755)
    result = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (42, b"", b"")
    assert source.read_text() == SOURCE
