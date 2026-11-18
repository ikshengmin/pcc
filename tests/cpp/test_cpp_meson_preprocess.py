"""Stock Meson header checks and owned stdout-only preprocessing, not package setup."""
from pathlib import Path
import argparse
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
MESON = ROOT / "projects/numpy-2.4.4/vendored-meson/meson"
DRIVER = ROOT / "scripts/pcc_meson_setup.py"
pytestmark = pytest.mark.pcc_gate(platforms=("linux",), machines=("x86_64",))
LANGUAGES = (("c", "c", "c11"), ("cpp", "cpp", "c++17"))


def owned(lang, args, monkeypatch):
    from pcc.tools.meson_cc import run
    def forbidden(*a, **k):
        pytest.fail("a preprocessor probe entered compilation, linking, or an external tool")
    with monkeypatch.context() as guard:
        guard.delenv("PCC_RUNTIME_ARCHIVE", raising=False)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setitem(sys.modules, "pcc.frontends.c.evaluator.c_evaluator", None)
        return run(lang, args)


def tokens(text):
    # For our sources, compare meaningful tokens, not blank-line formatting.
    return re.findall(r'"(?:\\.|[^"\\])*"|[A-Za-z_]\w*|\d+|\S', text)


@pytest.mark.parametrize("lang,ext,std", LANGUAGES)
def test_owned_arguments_physical_includes_logical_line_and_original_spelling(
        tmp_path, monkeypatch, capsys, lang, ext, std):
    monkeypatch.chdir(tmp_path)
    for d in ["first", "second", "inc", "actual", "named"]:
        Path(d).mkdir()
    Path("first/pick.h").write_text("#define SELECTED 11\n")
    Path("second/pick.h").write_text("#define SELECTED 22\n")
    Path("inc/forced.h").write_text("#define FORCED 7\n")
    Path("named/near.h").write_text("#define NEAR 9\n")
    Path("actual/near.h").write_text("#error followed resolved symlink instead of its specified directory\n")
    source = (
        '#include "near.h"\n#include <pick.h>\n'
        'origin __FILE__ __LINE__\n'
        '#line 71 "logical file"\n'
        'logical __FILE__ __LINE__ SELECTED FORCED NEAR SWITCH\n'
        '#line 15 "<string>"\n'
        'sentinel __FILE__ __LINE__\n'
        '#if 0\n#error inactive\n#endif\n'
        '#if defined(__cplusplus)\ncpp_standard __cplusplus\n'
        '#else\nc_standard __STDC_VERSION__\n#endif\n'
        'not legal target language syntax; // preprocessing never parses\n'
        'const char *literal = "# 1 still inside a string";\n'
    )
    real = Path("actual") / ("unit." + ext)
    named = Path("named") / ("unit." + ext)
    real.write_text(source)
    named.symlink_to(real.resolve())
    spelling = "./" + named.as_posix()
    args = ["-isystem", "first", "-nostdinc", "-isystem", "second", "-I", "inc",
            "-include", "forced.h", "-D", "SWITCH=100", "-USWITCH", "-DSWITCH=8",
            "-std=" + std, "-E", "-P", spelling]
    assert owned(lang, args, monkeypatch) == 0
    text = capsys.readouterr().out
    assert not any(line.lstrip().startswith("#") for line in text.splitlines())
    assert tokens(text) == tokens(
        f'origin "{spelling}" 3\nlogical "logical file" 71 11 7 9 8\nsentinel "<string>" 15\n'
        + ('cpp_standard 201703L\n' if lang == 'cpp' else 'c_standard 201112L\n')
        + 'not legal target language syntax;\nconst char *literal = "# 1 still inside a string";'
    )
    assert not Path("a.out").exists() and not named.with_suffix(".o").exists()
    assert real.read_text() == source


@pytest.mark.parametrize("lang,ext,std", LANGUAGES)
def test_preprocessor_reference_same_source_and_switch(tmp_path, monkeypatch, capsys, lang, ext, std):
    oracle = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++") if lang == "cpp" else "gcc")
    assert oracle, "a real C/C++ preprocessing reference is required"
    monkeypatch.chdir(tmp_path)
    Path("a").mkdir(); Path("b").mkdir()
    Path("a/tokens.h").write_text('#define VALUE 41\n#line 24 "from header"\nheader __FILE__ __LINE__\n')
    Path("b/tokens.h").write_text("#error wrong -isystem order\n")
    source = Path("source." + ext)
    source.write_text(
        '#if CHECK\n#include <missing-for-negative.h>\n#else\n#include <tokens.h>\n#endif\n'
        'main __FILE__ __LINE__ VALUE\n#line 99 "renamed"\nend __FILE__ __LINE__\n'
    )
    for enabled in (0, 1):
        args = ["-E", "-P", "-isystem", "a", "-isystem", "b", "-nostdinc", "-std="+std,
                "-DCHECK="+str(enabled), "./"+source.name]
        reference = subprocess.run([oracle, *args], capture_output=True, text=True, timeout=8)
        if enabled:
            assert reference.returncode != 0 and "missing-for-negative.h" in reference.stderr
            with pytest.raises(RuntimeError, match="missing-for-negative.h"):
                owned(lang, args, monkeypatch)
            assert not capsys.readouterr().out
        else:
            assert reference.returncode == 0, reference.stderr
            assert owned(lang, args, monkeypatch) == 0
            assert tokens(capsys.readouterr().out) == tokens(reference.stdout)


@pytest.mark.parametrize("lang,ext,std", LANGUAGES)
def test_empty_error_and_default_header_boundaries(tmp_path, monkeypatch, capsys, lang, ext, std):
    monkeypatch.chdir(tmp_path)
    file = Path("select." + ext)
    file.write_text('#if FAIL\n#error required_bad_path\n#endif\n')
    flags = ["-P", "-E", file.name]
    assert owned(lang, flags, monkeypatch) == 0 and not capsys.readouterr().out.strip()
    with pytest.raises(RuntimeError, match="required_bad_path"):
        owned(lang, [*flags, "-DFAIL=1"], monkeypatch)
    assert capsys.readouterr().out == ""
    header = "cstddef" if lang == "cpp" else "stddef.h"
    file.write_text(f'#if !__has_include(<{header}>)\n#error builtin_header_missing\n#endif\n')
    assert owned(lang, flags, monkeypatch) == 0
    with pytest.raises(RuntimeError, match="builtin_header_missing"):
        owned(lang, ["-nostdinc", *flags], monkeypatch)
    if lang == "cpp":
        # PCC's C-only PyCAPI header must not become a C++ default.
        file.write_text('#if __has_include(<Python.h>)\n#error unexpected_c_search\n#endif\n')
        assert owned(lang, flags, monkeypatch) == 0


@pytest.mark.parametrize("lang,ext,std", LANGUAGES)
def test_shared_directory_keeps_its_system_classification(tmp_path, monkeypatch, capsys, lang, ext, std):
    monkeypatch.chdir(tmp_path)
    for name, value in (("system", 1), ("user", 2)):
        Path(name).mkdir()
        (Path(name)/"priority.h").write_text(f"#define PRIORITY {value}\n")
    Path("alias").symlink_to(Path("system").resolve(), target_is_directory=True)
    source = Path("probe." + ext)
    source.write_text("#include <priority.h>\nPRIORITY\n")
    args = ["-E", "-P", "-Ialias", "-Iuser", "-isystem", "system", "-nostdinc", source.name]
    oracle = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++") if lang == "cpp" else "gcc")
    assert oracle
    reference = subprocess.run([oracle, *args], capture_output=True, text=True, timeout=8)
    assert reference.returncode == 0 and reference.stdout.strip() == "2", reference.stderr
    assert owned(lang, args, monkeypatch) == 0 and capsys.readouterr().out.strip() == "2"


@pytest.mark.parametrize("lang,ext,std", LANGUAGES)
def test_line_directive_diagnoses_active_malformed_filename_only(tmp_path, monkeypatch, capsys, lang, ext, std):
    monkeypatch.chdir(tmp_path)
    source = Path("original." + ext)
    source.write_text('prefix\n#if BAD\n#line 9 42\n#endif\n'
                      '#define ORIGIN "logical"\n#line 33 ORIGIN\nend __FILE__ __LINE__\n')
    args = ["-E", "-P", source.name]
    assert owned(lang, [*args, "-DBAD=0"], monkeypatch) == 0
    assert tokens(capsys.readouterr().out) == tokens('prefix end "logical" 33')
    with pytest.raises(RuntimeError, match="line"):
        owned(lang, [*args, "-DBAD=1"], monkeypatch)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("extra", [
    ["-E"], ["-P"], ["-c", "-E", "-P"], ["-E", "-P", "-o", "sample.c"],
    ["-E", "-P", "-o", "unrelated"], ["-E", "-P", "b.c"],
    ["-E", "-P", "b.o"], ["-E", "-P", "-M"], ["-E", "-P", "-std=c++17"],
])
def test_unimplemented_modes_preserve_all_inputs_and_destinations(tmp_path, monkeypatch, capsys, extra):
    monkeypatch.chdir(tmp_path)
    for name in ("sample.c", "unrelated", "b.c", "b.o", "a.out"):
        Path(name).write_bytes(b"preserve me\n")
    with pytest.raises(ValueError):
        owned("c", [*extra, "sample.c"], monkeypatch)
    assert all(Path(name).read_bytes() == b"preserve me\n" for name in ("sample.c", "unrelated", "b.c", "b.o", "a.out"))
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("lang,ext,std", LANGUAGES)
def test_stock_meson_uncached_header_probe_calls_configured_tool(tmp_path, monkeypatch, lang, ext, std):
    # Real vendored Meson Environment, compiler, has_header, argv, file, process,
    # and diagnostics. This explicitly scoped probe does not perform project
    # setup, compiler sanity linking or discovery of another language.
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    monkeypatch.syspath_prepend(str(MESON))
    monkeypatch.delenv("PCC_RUNTIME_ARCHIVE", raising=False)
    from mesonbuild import coredata
    from mesonbuild.compilers.compilers import CompileCheckMode
    from mesonbuild.environment import Environment
    from mesonbuild.mesonlib import MachineChoice
    from pcc_meson_compilers import PCCCCompiler, PCCCPPCompiler
    from pcc.tools.meson_cc import release
    assert Path(coredata.__file__).resolve().is_relative_to(MESON) and coredata.version == "1.9.2"
    env = Environment(str(tmp_path), str(tmp_path/"out"), argparse.Namespace(
        native_file=[], cross_file=[], cmd_line_options={}, wrap_mode=None))
    machine = MachineChoice.HOST
    kind = PCCCCompiler if lang == "c" else PCCCPPCompiler
    env.add_lang_args(lang, kind, machine)
    command = [sys.executable, str(DRIVER), "--tool", lang]
    compiler = kind([], command, release(), machine, False, env.machines.host)
    default = "Python.h" if lang == "c" else "cstddef"
    assert compiler.has_header(default, "", env, disable_cache=True) == (True, False)
    assert compiler.has_header(default, "", env, extra_args=["-nostdinc"], disable_cache=True) == (False, False)
    header = tmp_path / "configured.h"
    header.write_text('#if CONTROL\n#error original_header_error\n#endif\n#define VALUE 89\n')
    args = ["-nostdinc", "-I"+str(tmp_path)]
    assert compiler.has_header(header.name, "", env, extra_args=args, disable_cache=True) == (True, False)
    assert compiler.has_header("absent-pcc-meson.h", "", env, extra_args=args, disable_cache=True) == (False, False)
    source = '#include "configured.h"\n#line 6 "actual"\noriginal __FILE__ __LINE__ VALUE\n'
    for control in (0, 1):
        with compiler.compile(source, extra_args=[*args, "-DCONTROL="+str(control)],
                              mode=CompileCheckMode.PREPROCESS, temp_dir=env.scratch_dir) as result:
            assert result.command[:len(command)] == command
            assert "-E" in result.command and "-P" in result.command and "-o" not in result.command
            if control:
                assert result.returncode != 0 and "original_header_error" in result.stderr
                assert result.stdout == ""
            else:
                assert result.returncode == 0, result.stderr
                assert tokens(result.stdout) == tokens('original "actual" 6 89')
