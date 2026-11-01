"""Program code drops its mapped frame entries only under a published table.

The stack walk is the only root source of a mapped frame once main publishes
the stack-map table, which only the owned Darwin link does. Program code
compiled for it carries ``__pcc_owned_gc_walked_frames_v1`` and the AArch64
self backend emits nothing for its mapped entries. Library and runtime
modules can be linked without a table (a C host, ``PCC_SELF_LINK=cc``), so
they keep the entries and the runtime registers those frames while no table
is published.
"""

import re

from pcc.backend.self_backend_aarch64_gc_calls import GC_WALKED_FRAMES_MARKER
from pcc.frontends.python.pipeline import compile_python


_SOURCE = """
class Box:
    def __init__(self, value):
        self.value = value


def make(n):
    items = []
    i = 0
    while i < n:
        items.append(Box(i))
        i += 1
    return items


print(len(make(3)))
"""

_DARWIN = "arm64-apple-darwin"


def _ir(tmp_path, monkeypatch, *, link="pcc", library=False, target=_DARWIN):
    monkeypatch.setenv("PCC_SELF_LINK", link)
    monkeypatch.delenv("PCC_PRECISE_STACKMAP_TABLES", raising=False)
    source = tmp_path / "program.py"
    source.write_text(_SOURCE, encoding="utf-8")
    output = tmp_path / "program.ll"
    compile_python(str(source), str(output), emit_llvm_only=True,
                   python_library=library, target_triple=target)
    return output.read_text(encoding="utf-8")


def _marked(text):
    return "@" + GC_WALKED_FRAMES_MARKER + " = internal constant i8 1" in text


def _mapped_calls(text):
    return re.findall(r"call void \([^)]*\) @pcc_gc_frame_(?:enter|leave)\w*_mapped\(", text)


def _publishes_table(text):
    return re.search(r"call void \(ptr\) @pcc_gc_register_precise_stackmap_table\(", text)


def test_owned_darwin_program_code_is_marked(tmp_path, monkeypatch):
    text = _ir(tmp_path, monkeypatch)
    assert _mapped_calls(text)
    assert _marked(text)
    # main publishes the table the marker relies on.
    assert _publishes_table(text)


def test_cc_reference_link_keeps_mapped_entries_unmarked(tmp_path, monkeypatch):
    text = _ir(tmp_path, monkeypatch, link="cc")
    assert _mapped_calls(text)
    assert not _marked(text)
    assert not _publishes_table(text)


def test_library_modules_keep_mapped_entries_unmarked(tmp_path, monkeypatch):
    text = _ir(tmp_path, monkeypatch, library=True)
    assert _mapped_calls(text)
    assert not _marked(text)


def test_linux_program_code_registers_its_frames(tmp_path, monkeypatch):
    # ELF links publish no table, so frames use the plain entries.
    text = _ir(tmp_path, monkeypatch, target="aarch64-unknown-linux-gnu")
    assert not _mapped_calls(text)
    assert not _marked(text)
