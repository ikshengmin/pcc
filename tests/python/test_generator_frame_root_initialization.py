"""Real resume IR initializes roots before publication; no native GC claim."""

import re

import pytest

from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.frontends.python.pipeline import compile_python
from tests.owned_ir_validation import verify_ir_text
from tests.python.root_slot_contract import RootSlotContract


PROGRAMS = {
    "ordinary": """
def probe(value, callback):
    saved = value
    yield saved
    result = callback(saved)
    yield result
    return saved
""",
    "delegated": """
def child(value):
    yield value
    return value
def probe(value, callback):
    saved = value
    yield from child(saved)
    result = callback(saved)
    yield result
    return saved
""",
    "direct_may_park": """
import pcc.virtual_thread as vt
def child(value):
    vt.yield_now()
    return value
def probe(value, callback):
    saved = value
    result = child(saved)
    return callback(result)
""",
    "virtual_call": """
import pcc.virtual_thread as vt
def child(value):
    vt.yield_now()
    return value
def probe(value, callback):
    saved = value
    result = vt.call(child, saved)
    return vt.call(callback, result)
""",
    "async": """
async def child(value):
    return value
async def probe(value, callback):
    saved = value
    result = await child(saved)
    return callback(result)
""",
}
TARGETS = ("x86_64-unknown-linux-gnu", "arm64-apple-darwin")


def _emit(tmp_path, monkeypatch, shape, first_entry, target):
    monkeypatch.setenv("PCC_GENERATOR_FIRST_ENTRY_INIT", first_entry)
    monkeypatch.setenv("PCC_IMPLICIT_THREAD_SAFEPOINTS", "1")
    source = tmp_path / "frame_root_init.py"
    output = tmp_path / "frame_root_init.ll"
    source.write_text(PROGRAMS[shape])
    compile_python(
        str(source), str(output), emit_llvm_only=True, python_library=True,
        ir_scaffold_mode="on", libpython_mode="off", backend="self",
        target_triple=target,
    )
    return output.read_text()


def _check(text):
    module = verify_ir_text(text)
    resumes = [function for function in module.functions
               if function.name.endswith("__gen_resume")]
    assert resumes, "fixture must compile real generator/coroutine resumes"
    records = []
    for function in resumes:
        blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
        roots = RootSlotContract(blocks, module.globals_)
        entry = next(block for block in blocks if block.name == "entry")
        entries = [(index, ins) for index, ins in enumerate(entry.instructions)
                   if roots.called(ins, "pcc_gc_frame_enter")]
        assert entries, "resume must publish its root frames"
        first_registration = entries[0][0]
        owning = set()
        borrowed = []
        for index, ins in entries:
            frame_map, address = roots.args(ins)
            descriptor = roots.globals[roots.symbol(frame_map).lstrip("@")]
            count = int(descriptor.initializer)
            base, offset = roots.slot(address)
            if count < 0:
                borrowed.append((index, base, offset, -count))
                continue
            for cell in range(count):
                identity = (base, offset + cell * roots.POINTER_BYTES)
                roots.require_owning(identity)
                owning.add(identity)

        # Incoming gen/frame are borrowed, not empty owned cells. Prove their
        # original ABI values remain in their distinct registered cells.
        assert len(borrowed) == 1 and borrowed[0][3] == 2
        registration, base, offset, _ = borrowed[0]
        borrowed_cells = [(base, offset), (base, offset + roots.POINTER_BYTES)]
        for parameter, identity in zip(function.args, borrowed_cells, strict=True):
            stores = [(block, index, ins) for block, index, ins in roots.rows
                      if ins.kind == "store" and roots.slot(ins.data[3]) == identity]
            assert len(stores) == 1, "borrowed ABI owner was overwritten"
            block, index, ins = stores[0]
            assert block.name == "entry" and index < registration
            assert ins.data[0].is_ptr and ins.data[1] == parameter.name, (
                "borrowed ABI owner must retain its incoming value"
            )

        # Frame takes identify the physical legacy local cells independently
        # of source-variable spelling, generated-name prefixes or SSA indices.
        loads = {ins.data[0]: ins.data[3]
                 for _, _, ins in roots.rows if ins.kind == "load"}
        legacy = set()
        for block, index, ins in roots.rows:
            if not roots.called(ins, "py_gen_frame_take"):
                continue
            assert roots.slot(loads.get(roots.args(ins)[0], "not-a-frame-load")) == borrowed_cells[1], (
                "frame restore must reload the authoritative borrowed frame"
            )
            following = block.instructions[index + 1]
            assert following.kind == "store" and following.data[1] == ins.data[0], (
                "restored NEW owner must be published without substitution"
            )
            identity = roots.slot(following.data[3])
            assert identity in owning, "frame restore needs a registered owning cell"
            if roots.allocas[identity[0]][0].is_ptr:
                legacy.add(identity)
        assert legacy, "fixture must exercise legacy frame-owned locals"
        for identity in legacy:
            initializers = [(block, index, ins) for block, index, ins in roots.rows
                            if ins.kind == "store" and ins.data[0].is_ptr
                            and ins.data[1] == "null"
                            and roots.slot(ins.data[3]) == identity]
            assert len(initializers) == 1, "a restored owner must not be zeroed again"
            block, index, _ = initializers[0]
            assert block.name == "entry" and index < first_registration, (
                "legacy frame cells must be NULL before the first publication"
            )

        # Publication can itself park. Initialization above dominates every
        # later explicit thread/GC safepoint, not merely the eventual restore.
        assert any(roots.called(ins, "pcc_thread_safepoint_gate")
                   or roots.called(ins, "pcc_gc_safepoint") for _, _, ins in roots.rows)
        roots.assert_frame_exits()
        records.append((function, roots, legacy, borrowed_cells))
    return records


@pytest.mark.parametrize("shape", tuple(PROGRAMS))
@pytest.mark.parametrize("first_entry", ("0", "1"))
@pytest.mark.parametrize("target", TARGETS)
def test_frame_roots_are_empty_before_publication_and_preserve_restores(
    tmp_path, monkeypatch, shape, first_entry, target,
):
    _check(_emit(tmp_path, monkeypatch, shape, first_entry, target))


@pytest.mark.parametrize("mutation", (
    "missing-null", "late-null", "borrowed-cleared", "restore-dropped", "owner-cleared",
))
def test_root_initialization_contract_rejects_unsafe_changes(tmp_path, monkeypatch, mutation):
    text = _emit(tmp_path, monkeypatch, "ordinary", "1", TARGETS[0])
    function, roots, legacy, borrowed = _check(text)[0]
    body = re.search(r"^define[^\n]*@" + re.escape(function.name) + r"\([^\n]*\).*?^}",
                     text, re.M | re.S).group(0)
    original = body
    cell = sorted(legacy)[0][0]
    empty = "  store ptr null, ptr %" + cell + "\n"
    assert body.count(empty) == 1
    if mutation in ("missing-null", "late-null"):
        body = body.replace(empty, "", 1)
        if mutation == "late-null":
            enter = re.search(r"^  call void[^\n]*@pcc_gc_frame_enter(?:_mapped)?\([^\n]*, ptr %"
                              + re.escape(cell) + r"\)\n", body, re.M).group(0)
            body = body.replace(enter, enter + empty, 1)
    elif mutation == "borrowed-cleared":
        parameter = function.args[0].name
        store = re.search(r"^  store ptr %" + re.escape(parameter) + r", ptr %[^\n]+\n", body, re.M).group(0)
        body = body.replace(store, store.replace("%" + parameter, "null", 1), 1)
    else:
        restore = next(ins for _, _, ins in roots.rows
                       if roots.called(ins, "py_gen_frame_take"))
        store = re.search(r"^  store ptr %" + re.escape(restore.data[0])
                          + r", ptr (%[^\n]+)\n", body, re.M)
        assert store is not None
        replacement = "" if mutation == "restore-dropped" else (
            store.group(0) + "  store ptr null, ptr " + store.group(1) + "\n"
        )
        body = body.replace(store.group(0), replacement, 1)
    assert body != original
    with pytest.raises(AssertionError):
        _check(text.replace(original, body, 1))
