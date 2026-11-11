"""Two real carriers exercise the emitted GC4 enqueue's flags commit.

This Linux fixture controls phase/color and redirects exactly one link-time
call: enqueue's existing pcc_current_thread_id call waits for the other
carrier, then returns the real ID. The production enqueue, root publisher,
pin operations and archive bytes are unchanged. This is deterministic native
scheduling instrumentation, not a stress, lifecycle or throughput claim.
The synthetic mark phase is never collected; lifecycle tests are separate.
"""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
from unittest.mock import patch

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "gc4_store_buffer_atomic_flags.c"
_ENQUEUE = "pcc_gc_backend4_store_buffer_enqueue"
_CHECKPOINT = "gc4_enqueue_checkpoint"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.pcc_gate(probe=lambda: sys.platform.startswith("linux")
                        and platform.machine().lower() in ("x86_64", "amd64")),
]


def compile_scheduled_enqueue(source, binary, archive):
    """Only redirect the existing thread-ID call inside the enqueue symbol."""
    from pcc.backend import elf_x86_64 as elf
    from pcc.backend.owned_elf_inputs import ElfInputStore

    stage = ElfInputStore.stage
    scheduled = []

    def schedule(store, *args, **kwargs):
        obj = stage(store, *args, **kwargs)
        owners = [s for s in obj.symbols if s.name == _ENQUEUE
                  and s.section_index != elf.SHN_UNDEF]
        if not owners:
            return obj
        assert len(owners) == 1 and obj.machine == elf.EM_X86_64
        # The real linker stages file-backed sections. Materialize only this
        # admitted owner so the test relocation cannot be reread from disk.
        # Input identity verification and the archive itself remain intact.
        obj = elf.ElfObject(tuple(elf.ElfSection(
            s.name, s.type, s.flags, s.align, s.data, s.mem_size,
            tuple(s.relocations),
        ) for s in obj.sections), obj.symbols, obj.machine)
        owner = owners[0]
        assert owner.binding == elf.STB_GLOBAL and owner.type == elf.STT_FUNC
        index = owner.section_index - 1
        section = obj.sections[index]
        selected = [r for r in section.relocations
                    if owner.value <= r.offset < owner.value + owner.size
                    and obj.symbols[r.symbol_index].name == "pcc_current_thread_id"]
        assert len(selected) == 1, "expected exactly one existing enqueue scheduling point"
        chosen = selected[0]
        assert all(s.name != _CHECKPOINT for s in obj.symbols)
        symbols = obj.symbols + (elf.ElfSymbol(
            _CHECKPOINT, elf.SHN_UNDEF, 0, 0, elf.STB_GLOBAL, elf.STT_FUNC,
        ),)
        relocations = tuple(replace(r, symbol_index=len(obj.symbols))
                            if r == chosen else r for r in section.relocations)
        sections = list(obj.sections)
        sections[index] = replace(section, relocations=relocations)
        # Every instruction and every other relocation stays byte-identical.
        assert sections[index].data == section.data
        assert sum(a != b for a, b in zip(section.relocations, relocations)) == 1
        scheduled.append((owner.value, owner.size, chosen.offset))
        return replace(obj, sections=tuple(sections), symbols=symbols)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with patch.object(ElfInputStore, "stage", schedule):
        compile_owned_c_with_runtime(
            source, binary, runtime_archive=archive,
            cpp_args=("-I", str(ROOT / "pcc/runtime/src")),
        )
    assert scheduled, "linker did not consume the instrumented production owner"
    assert len(set(scheduled)) == 1
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest
    return binary


@pytest.fixture(scope="module")
def scheduled_enqueue_binary(tmp_path_factory, pcc_runtime_archive):
    binary = tmp_path_factory.mktemp("gc4_enqueue_flags") / "enqueue_flags"
    return compile_scheduled_enqueue(FIXTURE, binary, Path(pcc_runtime_archive))


@pytest.mark.parametrize("pin_change", ["set", "clear"])
def test_gc4_native_enqueue_preserves_concurrent_root_shade_and_pin(
    scheduled_enqueue_binary, pin_change,
):
    result = subprocess.run(
        [str(scheduled_enqueue_binary), pin_change, "fixed"],
        capture_output=True, text=True, timeout=20,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="4"),
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "GC4_ENQUEUE_NATIVE_PRESERVED\n", "",
    ), result
