"""Owned native execution of a Rust function returning `&'static str`.

The function is compiled by PCC, an observer entry point is assembled by PCC's
own assembler, and the program is linked by PCC's own linker for the host:
arm64 Mach-O on Apple silicon, x86-64 ELF on Linux. The observer calls the
function, receives the (pointer, length) pair in the first two integer return
registers (x0/x1, rax/rdx) and writes those bytes to stdout. No external
compiler, assembler or linker runs. run_owned_u64_function is the same route
for a function returning u64 (x0/rax, written as eight little-endian bytes).
"""
import platform
import subprocess
import sys

import pytest

HOST_TARGET = ('arm64-apple-darwin' if sys.platform == 'darwin' and platform.machine() == 'arm64'
               else 'x86_64-unknown-linux-gnu' if sys.platform.startswith('linux')
               and platform.machine() in {'x86_64', 'AMD64'} else None)
UNAVAILABLE = None if HOST_TARGET else 'requires an arm64 Darwin or x86-64 Linux host for owned execution'

DARWIN_OBSERVER = '''.section __TEXT,__text,regular,pure_instructions
.globl _main
.p2align 2
_main:
  stp x29, x30, [sp, #-16]!
  mov x29, sp
  bl _{symbol}
  mov x2, x1
  mov x1, x0
  mov x0, #1
  bl _write
  mov x0, #0
  ldp x29, x30, [sp], #16
  ret
'''
LINUX_OBSERVER = '''.intel_syntax noprefix
.text
.globl _start
_start:
 call {symbol}
 mov rsi, rax
 mov eax, 1
 mov edi, 1
 syscall
 mov eax, 60
 xor edi, edi
 syscall
'''


def run_owned_str_function(source, function, tmp_path, monkeypatch, **options):
    """Compile `source` for the host, run `function`, return its bytes."""
    from pcc.frontends.rust import compile_rust
    options.setdefault('experimental_rustc_checked', True)
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned compile/link invoked a tool'))
        result = compile_rust(source, target_triple=HOST_TARGET, **options)
        symbol = result.symbols[function]
        if HOST_TARGET == 'arm64-apple-darwin':
            from pcc.backend import macho_exec, macho_obj
            from pcc.backend.arm64_asm_driver import assemble_file
            sections, undefined = assemble_file(DARWIN_OBSERVER.format(symbol=symbol))
            image = macho_exec.link_executable([result.emit_object(),
                                                macho_obj.emit_object(sections, undefined=undefined)])
        else:
            from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
            from pcc.backend.x86_64_asm_driver import assemble_file
            image = link_static_executable([parse_relocatable(result.emit_object()),
                                            assemble_file(LINUX_OBSERVER.format(symbol=symbol))])
    binary = tmp_path / 'owned-str'
    binary.write_bytes(image)
    binary.chmod(0o700)
    run = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert (run.returncode, run.stderr) == (0, b''), run
    return run.stdout

DARWIN_U64_OBSERVER = '''.section __TEXT,__text,regular,pure_instructions
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
LINUX_U64_OBSERVER = '''.intel_syntax noprefix
.text
.globl _start
_start:
 call {symbol}
 push rax
 mov rsi, rsp
 mov edx, 8
 mov eax, 1
 mov edi, 1
 syscall
 mov eax, 60
 xor edi, edi
 syscall
'''


def run_owned_u64_function(source, function, tmp_path, monkeypatch, *, target_triple=None, **options):
    """Compile `source` for the host (or the given spelling of the host
    target, e.g. the one a rustc check names), run `function`, return its u64."""
    from pcc.frontends.rust import compile_rust
    target = target_triple or HOST_TARGET
    if 'rustc_check' not in options:
        options.setdefault('experimental_rustc_checked', True)
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned compile/link invoked a tool'))
        result = compile_rust(source, target_triple=target, **options)
        symbol = result.symbols[function]
        if 'apple' in target:
            from pcc.backend import macho_exec, macho_obj
            from pcc.backend.arm64_asm_driver import assemble_file
            sections, undefined = assemble_file(DARWIN_U64_OBSERVER.format(symbol=symbol))
            image = macho_exec.link_executable([result.emit_object(),
                                                macho_obj.emit_object(sections, undefined=undefined)])
        else:
            from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
            from pcc.backend.x86_64_asm_driver import assemble_file
            image = link_static_executable([parse_relocatable(result.emit_object()),
                                            assemble_file(LINUX_U64_OBSERVER.format(symbol=symbol))])
    binary = tmp_path / 'owned-u64'
    binary.write_bytes(image)
    binary.chmod(0o700)
    run = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert (run.returncode, run.stderr, len(run.stdout)) == (0, b'', 8), run
    return int.from_bytes(run.stdout, 'little')


def run_rustc_str_function(source, function, tmp_path, *, edition=2021, environment=None, cfg=(), panic=None,
                           output='{call}.as_bytes()'):
    """The same function built by a development rustc, printed byte for byte
    (`output='&{call}.to_le_bytes()'` for an integer result)."""
    import os
    import shutil
    rustc = shutil.which('rustc')
    library, main = tmp_path / 'reference_lib.rs', tmp_path / 'reference_main.rs'
    library.write_text(source)
    call = output.format(call=f'rust_reference::{function}()')
    main.write_text('use std::io::Write;\nfn main(){std::io::stdout().write_all('
                    f'{call}).unwrap();}}\n')
    keep = {key: os.environ[key] for key in ('PATH', 'HOME', 'RUSTUP_HOME', 'CARGO_HOME', 'RUSTUP_TOOLCHAIN')
            if key in os.environ}
    env = {**keep, **(environment or {})}
    strategy = ['-C', f'panic={panic}'] if panic else []
    common = [f'--edition={edition}', '-C', 'overflow-checks=off', *strategy,
              *[part for spec in cfg for part in ('--cfg', spec)]]
    subprocess.run([rustc, '--crate-name', 'rust_reference', '--crate-type=rlib', '-A', 'warnings', *common,
                    str(library), '-o', str(tmp_path / 'librust_reference.rlib')],
                   check=True, capture_output=True, timeout=120, env=env)
    subprocess.run([rustc, f'--edition={edition}', *strategy,
                    '--extern', 'rust_reference=' + str(tmp_path / 'librust_reference.rlib'),
                    str(main), '-o', str(tmp_path / 'reference')],
                   check=True, capture_output=True, timeout=120, env=env)
    return subprocess.run([str(tmp_path / 'reference')], check=True, capture_output=True, timeout=10).stdout
