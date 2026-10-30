"""A public Python CLI can call independently compiled C through owned linking."""

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest


@pytest.mark.parametrize('entry', ('module', 'console', pytest.param('pcc1', marks=pytest.mark.integration)))
def test_public_python_cli_links_independent_c_object(
    tmp_path, pcc_runtime_archive, request, entry,
):
    from pcc import build

    source = tmp_path / 'external source.c'
    source.write_text('int external_add(int a, int b) { return a + b; }\n')
    with patch('subprocess.Popen', side_effect=AssertionError('external C build process')):
        artifact = build(source, kind='object', backend='self',
                         out_dir=tmp_path / 'foreign object', use_compile_cache=False)
    script = tmp_path / 'caller.py'
    script.write_text('''from pcc.extern import extern, c_int
external_add = extern('external_add', (c_int, c_int), c_int)
def main():
    print(external_add(19, 23))
main()
''')
    output = tmp_path / 'caller'
    if entry == 'pcc1':
        command = [str(request.getfixturevalue('native_pcc1_compiler'))]
    elif entry == 'console':
        command = [str(Path(sys.executable).with_name('pcc'))]
    else:
        command = [sys.executable, '-m', 'pcc']
    env = dict(os.environ, PCC_RUNTIME_ARCHIVE=str(pcc_runtime_archive),
               PCC_SELF_LINK='pcc', CC='/nonexistent/pcc-test-cc',
               PCC_NO_AUTO_PCC1='1')
    if entry == 'pcc1':
        env.update(PCC_HOST_PYTHON='/nonexistent/python', PCC_HOST_PCC='/nonexistent/pcc')
    compiled = subprocess.run(
        command + ['--backend', 'self', '--python-libpython=off',
                   '--link-arg', artifact.output_path, str(script), '-o', str(output)],
        capture_output=True, text=True, timeout=300, env=env,
    )
    assert compiled.returncode == 0, compiled.stderr
    native_env = dict(env, PATH='/nonexistent', PCC_HOST_PYTHON='/nonexistent/python',
                      PCC_HOST_PCC='/nonexistent/pcc')
    ran = subprocess.run([str(output)], capture_output=True, text=True,
                         timeout=30, env=native_env)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == '42\n'
    if entry == 'module':
        env['PCC_PY_RUN_CACHE_DIR'] = str(tmp_path / 'run-cache')
        run_command = command + ['--backend', 'self', '--python-libpython=off',
                                 '--link-arg', artifact.output_path, str(script)]
        first = subprocess.run(run_command, capture_output=True, text=True, timeout=300, env=env)
        assert first.returncode == 0, first.stderr
        assert first.stdout == '42\n'
        previous = Path(artifact.output_path).stat()
        source.write_text('int external_add(int a, int b) { return a - b; }\n')
        with patch('subprocess.Popen', side_effect=AssertionError('external C build process')):
            build(source, kind='object', backend='self', out_dir=tmp_path / 'foreign object',
                  use_compile_cache=False)
        os.utime(artifact.output_path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        second = subprocess.run(run_command, capture_output=True, text=True, timeout=300, env=env)
        assert second.returncode == 0, second.stderr
        assert second.stdout == '-4\n'


def test_python_run_cache_binds_native_object_content(tmp_path, monkeypatch):
    from pcc.driver.cli_core import _python_run_cache_key

    script = tmp_path / 'caller.py'
    script.write_text('print(42)\n')
    obj = tmp_path / 'external.o'
    obj.write_bytes(b'first object')
    before = obj.stat()
    monkeypatch.setenv('PCC_PACKAGE_SITE', str(tmp_path))
    options = dict(python_libpython='off', python_library=False, ir_scaffold='on',
                   backend='self', gpu_backend=None, link_args=[str(obj)])
    first = _python_run_cache_key(str(script), **options)
    obj.write_bytes(b'other object')
    os.utime(obj, ns=(before.st_atime_ns, before.st_mtime_ns))
    second = _python_run_cache_key(str(script), **options)
    assert first != second
    assert second != _python_run_cache_key(str(script), target_triple='x86_64-unknown-linux-gnu', **options)
    monkeypatch.setenv('PCC_SELF_LINK', 'cc')
    assert second != _python_run_cache_key(str(script), **options)


@pytest.mark.parametrize('mode,objects,link_args', (
    ('pcc', ['foreign.o'], ['-lm']),
    ('cc', [], ['foreign.o', '-lm']),
))
def test_cli_keeps_reference_link_arguments_explicit(tmp_path, monkeypatch, mode, objects, link_args):
    from pcc.driver.cli_launcher import main
    from pcc.frontends.python import pipeline

    calls = []
    monkeypatch.setenv('PCC_SELF_LINK', mode)
    monkeypatch.setattr(pipeline, 'compile_python', lambda *args, **kwargs: calls.append(kwargs))
    source = tmp_path / 'caller.py'
    source.write_text('print(42)\n')
    assert main(['--backend', 'self', '--link-arg', 'foreign.o', '--link-arg=-lm',
                 str(source), '-o', str(tmp_path / 'output')]) == 0
    assert calls[0]['object_inputs'] == objects
    assert calls[0]['link_args'] == link_args
