"""The compiler arena's raw zero span executes through the owned backend."""

import subprocess
import os
from pathlib import Path


PROGRAM = '''probe_backend = extern("pcc_gc_backend", (), c_int64)

def main():
    backend = probe_backend()
    arena = CompilerIntArena(1)
    assert arena.uses_native_storage
    arena.append(13)
    arena.append_zeros(8193)
    arena.append(-7)
    assert len(arena) == 8195
    assert arena.get(0) == 13
    assert arena.get(1) == 0
    assert arena.get(4096) == 0
    assert arena.get(8193) == 0
    assert arena.get(8194) == -7
    arena.close()
    print('arena-zero-span-ok', backend)

main()
'''


def test_compiler_arena_zero_span_runs_natively(
    tmp_path, python_program_compiler, pcc_runtime_archive,
):
    source = tmp_path / 'arena_zero_span.py'
    # Compiler internals join the bootstrap closure, not the ordinary app
    # provider allow-list. Compile the actual class source into this probe.
    arena_source = (Path(__file__).absolute().parents[2]
                    / 'pcc/backend/self_backend_value_arena.py')
    source.write_text(arena_source.read_text() + '\n' + PROGRAM, encoding='utf-8')
    binary = tmp_path / 'arena_zero_span'
    python_program_compiler(
        str(source), str(binary), backend='self', libpython_mode='off',
        runtime_archive=str(pcc_runtime_archive),
    )
    for backend in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PATH='/nonexistent',
                     PCC_HOST_PYTHON='/nonexistent/python', PCC_HOST_PCC='/nonexistent/pcc'),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == f'arena-zero-span-ok {backend}\n'
