"""Owned native execution of a Go scalar function on an arm64 Darwin host.

The package is compiled by PCC for arm64-apple-darwin, an observer `main` is
assembled by PCC's own assembler and the program is linked by PCC's own
Mach-O linker. The observer calls the function, stores its 64-bit result
register x0 and writes those eight bytes (little endian) to stdout. No
external compiler, assembler or linker runs. Linux x86-64 execution stays
with tests.go.owned_execution.

The reference helper builds the same unchanged source files with a selected
development Go toolchain (PCC_GO_REFERENCE, else `go` on PATH) and a separate
observer main that writes the same eight bytes. It is a labeled reference
experiment, never PCC ownership evidence.
"""
import os
import platform
import shutil
import subprocess
import sys

import pytest

DARWIN_TARGET = 'arm64-apple-darwin'
UNAVAILABLE = (None if sys.platform == 'darwin' and platform.machine() == 'arm64'
               else 'requires an arm64 Darwin host for owned Mach-O execution')
REFERENCE = os.environ.get('PCC_GO_REFERENCE') or shutil.which('go')
REFERENCE_UNAVAILABLE = None if REFERENCE else 'select a development Go toolchain with PCC_GO_REFERENCE or PATH'

OBSERVER = '''.section __TEXT,__text,regular,pure_instructions
.globl _main
.p2align 2
_main:
  stp x29, x30, [sp, #-32]!
  mov x29, sp
  bl _{symbol}
  str x0, [sp, #16]
  add x1, sp, #16
  mov x2, #8
  mov x0, #1
  bl _write
  mov x0, #0
  ldp x29, x30, [sp], #32
  ret
'''
OWNED_MAIN = ('observer_main.go', 'package main\n\nfunc main() {}\n')
REFERENCE_MAIN = '''package main

import "os"

func main() {{
	v := uint64({function}())
	var b [8]byte
	for i := range b {{
		b[i] = byte(v >> (8 * uint(i)))
	}}
	os.Stdout.Write(b[:])
}}
'''


def run_owned_go_function(units, function, tmp_path, monkeypatch):
    """Compile package main from `units`, run `function`, return its uint64."""
    from pcc.backend import macho_exec, macho_obj
    from pcc.backend.arm64_asm_driver import assemble_file
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.go import compile_go_sources, function_symbol
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned compile/link invoked a tool'))
        module = compile_go_sources((*units, OWNED_MAIN), target_triple=DARWIN_TARGET)
        result = module.globals.get(function_symbol(function))
        if result is None or getattr(result.function_type.return_type, 'width', 0) != 64 or result.args:
            raise ValueError('the Go observer requires a 64-bit integer result without arguments')
        sections, undefined = assemble_file(OBSERVER.format(symbol=function_symbol(function)))
        image = macho_exec.link_executable([emit_owned_object(str(module), DARWIN_TARGET),
                                            macho_obj.emit_object(sections, undefined=undefined)])
    binary = tmp_path / 'owned-go'
    binary.write_bytes(image)
    binary.chmod(0o700)
    run = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert (run.returncode, run.stderr, len(run.stdout)) == (0, b'', 8), run
    return int.from_bytes(run.stdout, 'little')


def run_reference_go_function(units, function, tmp_path):
    """The same unchanged files built by the reference toolchain."""
    directory = tmp_path / 'reference'
    directory.mkdir()
    for name, text in units:
        (directory / name).write_text(text)
    (directory / 'observer_main.go').write_text(REFERENCE_MAIN.format(function=function))
    env = dict(os.environ, GOTOOLCHAIN='local', GOPROXY='off', GOSUMDB='off', GOWORK='off',
               GO111MODULE='off', CGO_ENABLED='0', GOCACHE=str(tmp_path / 'cache'))
    names = [name for name, _ in units] + ['observer_main.go']
    subprocess.run([REFERENCE, 'build', '-o', str(directory / 'reference'), *names],
                   cwd=directory, env=env, check=True, capture_output=True, timeout=120)
    run = subprocess.run([str(directory / 'reference')], capture_output=True, timeout=10, check=True)
    assert len(run.stdout) == 8, run
    return int.from_bytes(run.stdout, 'little')


def reference_rejection(units, tmp_path):
    """Reference build of an invalid package: (returncode, stderr text)."""
    directory = tmp_path / 'reference'
    directory.mkdir()
    for name, text in units:
        (directory / name).write_text(text)
    env = dict(os.environ, GOTOOLCHAIN='local', GOPROXY='off', GOSUMDB='off', GOWORK='off',
               GO111MODULE='off', CGO_ENABLED='0', GOCACHE=str(tmp_path / 'cache'))
    run = subprocess.run([REFERENCE, 'build', '-o', str(directory / 'reference'), *[name for name, _ in units]],
                         cwd=directory, env=env, capture_output=True, timeout=120)
    return run.returncode, run.stderr.decode('utf-8', 'replace')
