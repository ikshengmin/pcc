"""Root-store branch outlining must preserve real owner replacement effects."""
import os
import subprocess
import sys

from pcc.frontends.python.pipeline import compile_python


PROGRAM = '''import gc
import weakref

events = []
saved = []
class Item:
    def __init__(self, value):
        self.value = value
    def __del__(self):
        events.append(self.value)
        if self.value == 3:
            saved.append(self)

def run():
    first = Item(1)
    second = Item(2)
    keep = [first]
    first = second
    second = None
    gc.collect()
    assert events == []
    keep.clear()
    gc.collect()
    assert events == [1]
    first = 17
    gc.collect()
    assert events == [1, 2]
    first = Item(3)
    reference = weakref.ref(first)
    first = None
    gc.collect()
    assert saved[0].value == 3 and events == [1, 2, 3]
    # Refcount replacement remains correct after resurrection, too.
    first = saved[0]
    saved.clear()
    gc.collect()
    assert first.value == 3 and events == [1, 2, 3]
    first = 2 ** 150
    gc.collect()
    assert first == 1427247692705959881058285969449495136382746624
    assert events == [1, 2, 3]
    print("ROOT_REPLACEMENT_OK")
run()
'''


def test_root_replacement_finalizers_and_resurrection_all_gc(tmp_path, pcc_runtime_archive):
    source = tmp_path / "root_replacement.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=15)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, "ROOT_REPLACEMENT_OK\n", "")
    output = tmp_path / "root_replacement"
    compile_python(str(source), str(output), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        for probe in ("0", "2"):
            result = subprocess.run([str(output)], capture_output=True, text=True, timeout=20,
                                    env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc),
                                             PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe))
            assert (result.returncode, result.stdout, result.stderr) == (0, reference.stdout, ""), (gc, probe)


FIELD_PROGRAM = '''import gc
import os
import weakref

events = []
saved = []
class Item:
    def __init__(self, value):
        self.value = value
    def __del__(self):
        events.append(self.value)
        if self.value == 3:
            saved.append(self)

class Holder:
    def __init__(self):
        self.slot = None

def run():
    holder = Holder()
    holder.slot = Item(1)
    keep = [holder.slot]
    holder.slot = Item(2)
    gc.collect()
    assert events == []
    keep.clear()
    gc.collect()
    assert events == [1]
    holder.slot = holder.slot
    assert holder.slot.value == 2 and events == [1]
    holder.slot = 17
    gc.collect()
    assert events == [1, 2]
    holder.slot = Item(3)
    reference = weakref.ref(holder.slot)
    holder.slot = None
    gc.collect()
    assert saved[0].value == 3 and events == [1, 2, 3]
    holder.slot = saved[0]
    saved.clear()
    gc.collect()
    assert holder.slot.value == 3 and events == [1, 2, 3]
    holder.slot = (1.5, "heap", [events])
    gc.collect()
    assert holder.slot[0] == 1.5 and holder.slot[2][0] is events
    assert events == [1, 2, 3]
    for index in range(1000):
        holder.slot = Item(100 + index) if index % 2 else str(index)
    holder.slot = None
    gc.collect()
    # No owner is released twice on any collector. Reference counting
    # (CPython, pcc GC0) finalizes each displaced item at once, in order;
    # a tracing collector may keep a few behind conservative roots.
    assert len(set(events)) == len(events)
    assert set(events) <= {1, 2, 3} | set(range(101, 1100, 2))
    if os.environ.get("PCC_GC_BACKEND", "0") == "0":
        assert len(events) == 503 and events[-1] == 1099
    print("FIELD_REPLACEMENT_OK")
run()
'''


def test_field_replacement_finalizers_and_resurrection_all_gc(tmp_path, pcc_runtime_archive):
    source = tmp_path / "field_replacement.py"
    source.write_text(FIELD_PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=15)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, "FIELD_REPLACEMENT_OK\n", "")
    output = tmp_path / "field_replacement"
    compile_python(str(source), str(output), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        for probe in ("0", "2"):
            result = subprocess.run([str(output)], capture_output=True, text=True, timeout=20,
                                    env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc),
                                             PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe))
            assert (result.returncode, result.stdout, result.stderr) == (0, reference.stdout, ""), (gc, probe)
