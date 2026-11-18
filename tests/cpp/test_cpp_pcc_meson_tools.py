"""The host Meson adapter runs real PCC programs; it is not a package gate."""
from pathlib import Path
import os
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "scripts/pcc_meson_setup.py"
MESON = ROOT / "projects/numpy-2.4.4/vendored-meson/meson"


@pytest.mark.parametrize("lang,argv", [
    ("c", []), ("c", ["-shared", "x.c"]), ("c", ["-fPIC", "x.c"]),
    ("cpp", ["-std=gnu++17", "x.cpp"]), ("c", ["-std=c++17", "x.c"]),
    ("c", ["x.cc"]), ("cpp", ["x.c"]), ("c", ["-I"]), ("c", ["-o"]),
    ("c", ["-c", "a.c", "b.c"]), ("c", ["-c", "lib.a"]),
    ("c", ["lib.a", "a.o"]), ("cpp", ["a.o", "b.cpp"]),
    ("c", ["-o", "x", "-o", "y", "a.c"]), ("c", ["-c", "-c", "a.c"]),
])
def test_unimplemented_modes_never_degrade_to_another_tool_or_input_order(lang, argv):
    from pcc.tools.meson_cc import parse
    with pytest.raises(ValueError):
        parse(lang, argv)


def test_includes_defines_and_language_order_are_not_synthesized():
    from pcc.tools.meson_cc import parse
    args = ["-D", "X=2", "-Ione", "-U", "X", "-isystem", "two", "-include", "chosen.h",
            "-nostdinc", "-std=c++17", "-O2", "-c", "actual.cpp", "-o", "result.o"]
    flags, inputs, output, obj, level = parse("cpp", args)
    assert flags == args[:11] and inputs == [Path("actual.cpp")]
    assert (output, obj, level) == (Path("result.o"), True, 2)


@pytest.mark.parametrize("selector", ["PCC_SELF_OBJ", "PCC_SELF_LINK", "PCC_IR_TO_OBJ_EMITTER",
                                       "PCC_RUNTIME_BUILD", "PCC_RUNTIME_CC", "PCC_RUNTIME_HIGH"])
def test_conflicting_selection_refuses_before_subprocess_or_file_mutation(selector, tmp_path, monkeypatch):
    from pcc.tools.meson_cc import run
    def external(*args, **kwargs):
        pytest.fail("external subprocess invoked")
    monkeypatch.setattr(subprocess, "Popen", external)
    monkeypatch.setenv(selector, "host")
    output = tmp_path / "old.o"; output.write_bytes(b"preserve me")
    with pytest.raises(ValueError, match=selector):
        run("cpp", ["-c", "missing.cpp", "-o", str(output)])
    assert output.read_bytes() == b"preserve me"


@pytest.mark.parametrize("alias", ["direct", "hardlink", "symlink", "runtime"])
def test_output_never_replaces_an_input_or_selected_archive(alias, tmp_path, monkeypatch):
    from pcc.tools.meson_cc import _product
    src = tmp_path / "source.c"; src.write_bytes(b"int main(void){return 0;}")
    if alias == "runtime":
        dst = tmp_path / "libpy_runtime_pcc_py.a"; dst.write_bytes(b"archive")
        monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(dst))
    elif alias == "direct":
        dst = src
    else:
        dst = tmp_path / "destination"
        if alias == "hardlink": os.link(src, dst)
        else: dst.symlink_to(src)
    before = dst.read_bytes()
    with pytest.raises(ValueError, match="aliases|symbolic"):
        with _product(dst, [src]):
            pytest.fail("admitted an output alias")
    assert dst.read_bytes() == before


def test_registered_host_is_not_a_native_or_cross_platform_claim(monkeypatch):
    from pcc.frontends.python import pipeline_targets
    from pcc.tools.meson_cc import _target
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: "aarch64-unknown-linux-gnu")
    with pytest.raises(ValueError, match="native Linux x86-64"):
        _target()


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_RUNTIME_ARCHIVE", platforms=("linux",), machines=("x86_64",))
@pytest.mark.xdist_group(name="pcc-meson-owned-target")
def test_a_real_c_object_links_to_owned_cpp_archive_and_runs(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import ElfError, parse_static_executable, read_archive_payloads
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from pcc.tools.meson_cc import run
    if host_target_triple() != "x86_64-unknown-linux-gnu":
        pytest.skip("PCC Meson C/C++ provider is currently native Linux x86-64 only")
    from tests.owned_runtime_c_fixture import admitted_runtime
    archive = admitted_runtime()
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))
    monkeypatch.chdir(tmp_path)
    actual = tmp_path / "actual"; actual.mkdir()
    (actual / "calc.cc").write_text('#include "value.h"\nextern "C" int add(int a,int b){return a+b+OFFSET;}')
    (actual / "value.h").write_text('#define OFFSET -1\n')
    (tmp_path / "value.h").write_text('#define OFFSET 0\n')
    (tmp_path / "calc.cc").symlink_to(actual / "calc.cc")
    (tmp_path / "main.c").write_text('extern int add(int,int); int main(void){return add(5,37);}')
    (tmp_path / "missing.c").write_text('extern int missing(void); int main(void){return missing();}')
    def external(*args, **kwargs): pytest.fail("external target tool invoked")
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", external)
        assert run("cpp", ["-std=c++17", "-c", "calc.cc", "-o", "calc.o"]) == 0
        assert run("c", ["-std=c11", "-c", "main.c", "-o", "main.o"]) == 0
        assert run("ar", ["--create", "libcalc.a", "calc.o"]) == 0
        assert read_archive_payloads(Path("libcalc.a").read_bytes()) == [("calc.o", Path("calc.o").read_bytes())]
        archive_bytes = Path("libcalc.a").read_bytes()
        for corrupt in (b"not an object", Path("calc.o").read_bytes()[:18] + b"\xb7\x00" + Path("calc.o").read_bytes()[20:]):
            Path("foreign.o").write_bytes(corrupt)
            with pytest.raises(ValueError, match="not a Linux x86-64 ELF object"):
                run("ar", ["--create", "libcalc.a", "foreign.o"])
            assert Path("libcalc.a").read_bytes() == archive_bytes
        with pytest.raises(ValueError, match="duplicate"):
            run("ar", ["--create", "libcalc.a", "calc.o", "calc.o"])
        assert Path("libcalc.a").read_bytes() == archive_bytes
        assert run("c", ["main.o", "libcalc.a", "-o", "product"]) == 0
        with pytest.raises(ElfError, match="undefined.*missing"):
            run("c", ["missing.c", "-o", "not-an-executable"])
    assert not Path("not-an-executable").exists()
    parse_static_executable(Path("product").read_bytes())
    assert subprocess.run([str(tmp_path / "product")], timeout=5).returncode == 42


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_RUNTIME_ARCHIVE", platforms=("linux",), machines=("x86_64",))
@pytest.mark.xdist_group(name="pcc-meson-owned-target")
def test_stock_meson_generates_header_from_real_target_and_reentry_binds_runtime(tmp_path):
    import shutil
    from tests.owned_runtime_c_fixture import admitted_runtime
    assert (MESON / "mesonbuild/mesonmain.py").is_file(), "tracked upstream Meson fixture is missing"
    # Use a byte-identical private archive view so the mutation control cannot
    # damage the selected original runtime or omit its admission sidecars.
    real = admitted_runtime(); private = tmp_path / "runtime" / real.name
    private.parent.mkdir()
    for suffix in ("", ".provenance.json", ".capi_syms"):
        shutil.copy2(str(real) + suffix, str(private) + suffix)
    src = tmp_path / "project"; src.mkdir(); build = tmp_path / "build"
    (src / "meson.build").write_text('''project('pcc-owned-probes','c','cpp',default_options:['c_std=c11','cpp_std=c++17','buildtype=plain','warning_level=0'])
cc=meson.get_compiler('c')
cpp=meson.get_compiler('cpp')
assert(cc.get_id() == 'pcc' and cpp.get_id() == 'pcc', 'wrong compiler identity')
assert(cc.compiles('int value(void){return 41;}') and not cc.compiles('#error invalid C'), 'C probes do not discriminate')
assert(cpp.compiles('struct S {int x;}; int f(){S s; s.x=2; return s.x;}') and not cpp.compiles('#error invalid C++'), 'C++ probes do not discriminate')
r=cc.run('int main(void){return 42;}')
assert(r.compiled() and r.returncode()==42, 'incorrect native result')
assert(not cc.links('extern int not_provided(void); int main(void){return not_provided();}'), 'missing symbol accepted')
d=configuration_data()
d.set('ACTUAL_TARGET_RESULT',r.returncode())
configure_file(output:'config.h',configuration:d)
''', encoding='utf-8')
    env = dict(os.environ, PCC_RUNTIME_ARCHIVE=str(private))
    setup = subprocess.run([sys.executable, str(DRIVER), '--meson-source', str(MESON), '--configuration-only', str(src), str(build)],
                           cwd=tmp_path, env=env, text=True, capture_output=True, timeout=65)
    assert setup.returncode == 0, (setup.stdout, setup.stderr)
    assert '#define ACTUAL_TARGET_RESULT 42' in (build / 'config.h').read_text()
    log = (build / 'meson-logs/meson-log.txt').read_text()
    assert 'sanitycheckc.exe` -> 0' in log and 'sanitycheckcpp.exe` -> 0' in log
    launch = build / 'pcc-meson-toolchain/launcher.py'
    program = tmp_path / 'program'; unit = tmp_path / 'app.c'
    unit.write_text('#include "config.h"\nint main(void){return ACTUAL_TARGET_RESULT;}\n')
    # A different invoking shell cannot substitute an unverified archive.
    env['PCC_RUNTIME_ARCHIVE'] = str(tmp_path / 'missing')
    env.pop('PYTHONPATH', None)
    cmd = [sys.executable,str(launch),'--tool','c','-I',str(build),str(unit),'-o',str(program)]
    subprocess.run(cmd, check=True, cwd=tmp_path, env=env, capture_output=True, timeout=20)
    assert subprocess.run([str(program)], timeout=5).returncode == 42
    old = program.read_bytes()
    with private.open('r+b') as archive:
        archive.seek(-1, 2); item = archive.read(1); archive.seek(-1, 2); archive.write(bytes((item[0] ^ 1,)))
    rejected = subprocess.run(cmd, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=8)
    assert rejected.returncode != 0 and 'runtime bytes changed' in rejected.stderr
    assert program.read_bytes() == old
