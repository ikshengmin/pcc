"""New sizeof observers on the current public APIs, not recovered gate evidence.

Only PCC's owned compile/emission/link APIs build the owned executable. G++ is
an explicit, separate same-original reference. Persist both observations before
asserting their semantic result, including an owned failure before reference.
"""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import traceback

from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
    run_executable, write_host_executable,
)

TARGET = 'x86_64-unknown-linux-gnu'


def capture_row_execution(binary):
    actual = run_executable(binary)
    binary.with_suffix('.stdout.bin').write_bytes(actual.stdout)
    binary.with_suffix('.stderr.bin').write_bytes(actual.stderr)
    binary.with_suffix('.execution.json').write_text(json.dumps({
        'returncode': actual.returncode,
        'stdout_hex': actual.stdout.hex(), 'stderr_hex': actual.stderr.hex(),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'binary': str(binary),
    }, indent=2))
    return actual


def execute_row_pair(tmp_path, monkeypatch, units, value=42):
    originals = tmp_path / 'originals'
    originals.mkdir()
    for filename, source in units:
        (originals / filename).write_text(source)
    first = second = owned_error = actual = None
    try:
        with monkeypatch.context() as guard:
            def forbidden(*args, **kwargs):
                raise AssertionError('owned sizeof compilation invoked an external build tool')
            guard.setattr(subprocess, 'Popen', forbidden)
            first = compile_host_cpp(units)
            second = compile_host_cpp(units)
    except Exception as error:
        owned_error = error
        (tmp_path / 'owned-build-error.txt').write_text(traceback.format_exc())
    try:
        if first is not None:
            for index, (ir, obj) in enumerate(zip(first.ir, first.objects)):
                (tmp_path / ('owned-' + str(index) + '.ll')).write_text(ir)
                (tmp_path / ('owned-' + str(index) + '.o')).write_bytes(obj)
            binary = tmp_path / 'owned'
            write_host_executable(first, binary)
            actual = capture_row_execution(binary)
    except Exception as error:
        if owned_error is None:
            owned_error = error
        (tmp_path / 'owned-materialization-or-run-error.txt').write_text(traceback.format_exc())
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference is required'
    original_run = subprocess.run
    def observe_reference_build(*args, **kwargs):
        result = original_run(*args, **kwargs)
        (tmp_path / 'reference-build.stdout.bin').write_bytes(result.stdout)
        (tmp_path / 'reference-build.stderr.bin').write_bytes(result.stderr)
        (tmp_path / 'reference-build.json').write_text(json.dumps({'returncode': result.returncode}))
        return result
    try:
        with monkeypatch.context() as guard:
            guard.setattr(subprocess, 'run', observe_reference_build)
            oracle = compile_reference_cpp(units, tmp_path / 'reference', compiler=compiler)
        reference = capture_row_execution(oracle)
    except Exception:
        (tmp_path / 'reference-build-or-run-error.txt').write_text(traceback.format_exc())
        raise
    if owned_error is not None:
        raise owned_error
    assert first == second
    expected = Execution(value & 255, struct.pack('<i', value), b'')
    assert_exact(actual, expected)
    assert_exact(reference, expected)


def compile_public(source, opt):
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    return CEvaluator(backend='self', target_triple=TARGET).compile_translation_units(
        [TranslationUnit('public-sizeof.cpp', None, source)],
        use_compile_cache=False, use_system_cpp=False, frontend_opt_level=opt,
    )


def compile_public_observed(source, opt, monkeypatch):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
    original_lower = CCodeGenerator._lower_ssa_function
    original_generate = CCodeGenerator.generate_code
    observations = {'ssa_attempts': [], 'pass_log': [], 'pass_report': []}
    def observe_lower(self, name, result_type):
        try:
            result = original_lower(self, name, result_type)
        except Exception as error:
            observations['ssa_attempts'].append({'function': name, 'result': 'exception', 'error': repr(error)})
            raise
        observations['ssa_attempts'].append({'function': name, 'result': bool(result)})
        return result
    def observe_generate(self, tree):
        result = original_generate(self, tree)
        observations['pass_log'].extend(
            {'pass': entry.pass_name, 'action': entry.action, 'target': entry.target, 'detail': entry.detail}
            for entry in self._pass_ctx.log
        )
        observations['pass_report'].append(self._pass_ctx.pass_report())
        return result
    with monkeypatch.context() as guard:
        guard.setattr(CCodeGenerator, '_lower_ssa_function', observe_lower)
        guard.setattr(CCodeGenerator, 'generate_code', observe_generate)
        def forbidden(*args, **kwargs):
            raise AssertionError('public owned sizeof pipeline invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        records = compile_public(source, opt)
    return records, observations


def execute_public_row_pair(source_text, opt, tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    source = tmp_path / 'public.cpp'
    source.write_text(source_text)
    first = second = owned_error = actual = None
    try:
        with monkeypatch.context() as guard:
            def forbidden(*args, **kwargs):
                raise AssertionError('owned public sizeof build invoked an external tool')
            guard.setattr(subprocess, 'Popen', forbidden)
            records, observed = compile_public_observed(source_text, opt, monkeypatch)
            (tmp_path / 'pipeline-observation.json').write_text(json.dumps(observed, indent=2))
            (tmp_path / 'public.ll').write_text(records[0][1])
            first = emit_owned_object(records[0][1], TARGET)
            (tmp_path / 'owned.o').write_bytes(first)
            second = emit_owned_object(compile_public(source_text, opt)[0][1], TARGET)
            startup = assemble_file('.intel_syntax noprefix\n.text\n.globl _start\n_start:\n'
                                    ' call main\n mov edi,eax\n mov rax,60\n syscall\n')
            executable = link_static_executable([startup, parse_relocatable(first)], entry='_start')
        binary = tmp_path / 'owned'
        binary.write_bytes(executable)
        binary.chmod(0o700)
        actual = capture_row_execution(binary)
    except Exception as error:
        owned_error = error
        (tmp_path / 'owned-build-or-run-error.txt').write_text(traceback.format_exc())
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference is required'
    reference = tmp_path / 'reference'
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-O' + str(opt),
                             str(source), '-o', str(reference)], capture_output=True, timeout=20)
    (tmp_path / 'reference-build.stdout.bin').write_bytes(result.stdout)
    (tmp_path / 'reference-build.stderr.bin').write_bytes(result.stderr)
    (tmp_path / 'reference-build.json').write_text(json.dumps({'returncode': result.returncode}))
    assert result.returncode == 0, result.stderr
    oracle = capture_row_execution(reference)
    if owned_error is not None:
        raise owned_error
    assert first == second
    expected = Execution(42, b'', b'')
    assert_exact(actual, expected)
    assert_exact(oracle, expected)
