"""Owned C++ assertions: real source evaluation, diagnostics and abort ABI."""
from pathlib import Path
import platform
import signal
import subprocess
import sys

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp

TARGET = 'x86_64-unknown-linux-gnu'
ACTIVE = '''#include <assert.h>
int main(){int x=40;assert(++x==41);return x+1;}
'''
DISABLED = '''#include <assert.h>
int first(){int x=39;assert(++x==40);return x;}
#define NDEBUG
#include <cassert>
int disabled(){int x=41;assert(++x&&unbound_name);return x;}
#undef NDEBUG
#include <assert.h>
int enabled(){int x=40;assert(++x==41);return x;}
int main(){return first()+disabled()+enabled()-80;}
'''
FAILED = '''#include <cassert>
#include <cstdlib>
#line 18 "assert-input.cpp"
template<typename T> T check(T v){int write=1;int abort=2;assert(v!=write-1&&abort==2);return v;}
int main(){return check(0);}
'''
FAILURE_MESSAGE = b"assert-input.cpp:18: check: Assertion `v!=write-1&&abort==2' failed.\n"


def compile_object(source, filename, target):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    units = CEvaluator(backend='self', target_triple=target).compile_translation_units(
        [TranslationUnit(filename, None, source)], use_system_cpp=False,
        frontend_opt_level=0, use_compile_cache=False,
    )
    ir = units[0][1]
    return ir, emit_owned_object(ir, target)


def test_reinclusion_rebinds_assert_from_current_ndebug_without_host_headers(monkeypatch):
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external compiler invoked'))
        text = preprocess_cpp(DISABLED, target_triple=TARGET, source_name='toggle.cpp')
        assert text.count('__pcc_cpp_assert_fail') == 2
        assert 'unbound_name' not in text
        assert 'int disabled(){int x=41;((void)0);return x;}' in text
        assert '"++x==40", "toggle.cpp", 2' in text
        assert '"++x==41", "toggle.cpp", 8' in text
        ir, _ = compile_object(DISABLED, 'toggle.cpp', TARGET)
    assert ir.count('call void () @abort()') == 2
    disabled = ir.split('@_Z8disabledv()', 1)[1].split('}', 1)[0]
    assert '@abort(' not in disabled and '@write(' not in disabled


@pytest.mark.parametrize('target', [TARGET, 'aarch64-unknown-linux-gnu', 'arm64-apple-darwin'])
def test_active_assert_emits_real_runtime_abi_and_void_result(target, monkeypatch):
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external compiler invoked'))
        first = compile_object(ACTIVE, 'active.cpp', target)
        assert first == compile_object(ACTIVE, 'active.cpp', target)
    ir, image = first
    assert '@abort()' in ir and '@write(i32' in ir
    assert 'br i1' in ir and len(image) > 0


def test_active_assert_does_not_erase_semantic_checks_or_admit_arbitrary_noexcept():
    def parse(body):
        pp = preprocess_cpp('#include <cassert>\n#include <cstdlib>\n' + body,
                            target_triple=TARGET)
        return CppParser(target_triple=TARGET).parse(pp)
    with pytest.raises(CppDiagnostic, match='not found|undeclared|unknown'):
        parse('int main(){assert(unbound_name);return 42;}')
    with pytest.raises(CppDiagnostic) as raised:
        parse('int main(){std::abort();return 42;}')
    assert raised.value.code == 'cpp.unsupported.exceptions'
    with pytest.raises(CppDiagnostic) as raised:
        parse('int main(){return assert(true);}')
    assert raised.value.code == 'cpp.return-void-expression'


def run_owned_assert_cases(directory, archive, monkeypatch, target):
    from pcc.backend.owned_elf_link import link_inputs
    for name, source, expected in (
        ('active', ACTIVE, (42, b'', b'')),
        ('toggle', DISABLED, (42, b'', b'')),
        ('failed', FAILED, (-signal.SIGABRT, b'', FAILURE_MESSAGE)),
    ):
        obj = directory / (name + '.o')
        exe = directory / name
        with monkeypatch.context() as guard:
            guard.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external compiler/linker invoked'))
            _ir, image = compile_object(source, name + '.cpp', target)
            obj.write_bytes(image)
            link_inputs(target=target, output=str(exe), objects=[str(obj)], archives=[str(archive)])
        def disable_core():
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        process = subprocess.run([str(exe)], capture_output=True, timeout=10, preexec_fn=disable_core)
        assert (process.returncode, process.stdout, process.stderr) == expected


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if sys.platform == 'linux' and platform.machine().lower() in (
    'amd64', 'x86_64', 'aarch64', 'arm64') else 'requires owned Linux assertion runtime execution')
def test_assert_native_success_reinclusion_and_failure(tmp_path, pcc_runtime_archive, monkeypatch):
    # The suite's strict runtime fixture binds current source/target/codegen.
    from pcc.frontends.python.pipeline_targets import host_target_triple
    run_owned_assert_cases(tmp_path, Path(pcc_runtime_archive), monkeypatch, host_target_triple())
