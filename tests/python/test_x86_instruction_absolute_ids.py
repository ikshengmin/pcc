"""Host-only indexed-ID contracts and exact x86 emission-byte parity.

No native compiler, runtime archive, executable build or subprocess is used.
"""

from types import SimpleNamespace

import pytest

from pcc.backend import self_backend_emit as emit
from pcc.backend import self_backend_x86_64_linux as x86
from pcc.backend.indexed_phase_timing import ModulePhaseTiming
from pcc.backend.self_backend_ir import (
    CompactParsedInstrArena,
    PARSED_INSTRUCTION_KIND_FREEZE,
    _EMPTY_SEQUENCE,
)
from pcc.backend.self_backend_kernel import (
    IndexedFunctionKernel,
    get_indexed_function_kernel,
)
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.target_objects import (
    emit_indexed_assembly,
    emit_x86_64_streamed_module,
)


IR = """
target triple = "{triple}"
declare i64 @helper(i64)
define i64 @probe(i64 %x, ptr %p, i1 %flag) {{
entry:
  %slot = alloca i64
  store volatile i64 %x, ptr %slot
  %loaded = load volatile i64, ptr %slot
  %sum = add nsw i64 %loaded, 1
  %called = call i64 @helper(i64 %sum)
  %frozen = freeze i64 %called
  %cmp = icmp sgt i64 %frozen, 4
  br i1 %cmp, label %left, label %right
left:
  %mixed = xor i64 %frozen, 7
  br label %join
right:
  %cast = zext i1 %flag to i64
  br label %join
join:
  %result = phi i64 [ %mixed, %left ], [ %cast, %right ]
  ret i64 %result
}}
"""


def _module(triple="x86_64-unknown-linux-gnu", *, direct=False):
    module = parse_self_backend_module(IR.format(triple=triple))
    for func in module.functions:
        kernel = get_indexed_function_kernel(func)
        if direct:
            # The production direct-indexed input has no legacy block graph.
            func.blocks = []
            func.block_map = {}
    return module


def _coordinates(kernel):
    for block_id in range(len(kernel.block_names)):
        block = kernel.block_fact(block_id)
        for local in range(block.second):
            yield block_id, local, block.first + local


class BlockLocalKernel:
    """A custom-kernel client of the unchanged block-local protocol."""

    def __init__(self, kernel):
        self.kernel = kernel
        self.reads = []

    def __getattr__(self, name):
        return getattr(self.kernel, name)

    def instruction_kind_id(self, block_id, local):
        self.reads.append(("kind", block_id, local))
        return self.kernel.instruction_kind_id(block_id, local)

    def instruction_record_id(self, block_id, local):
        self.reads.append(("record", block_id, local))
        return self.kernel.instruction_record_id(block_id, local)

    def instruction_data(self, block_id, local):
        self.reads.append(("data", block_id, local))
        return self.kernel.instruction_data(block_id, local)

    def instruction_is_volatile(self, block_id, local):
        self.reads.append(("volatile", block_id, local))
        return self.kernel.instruction_is_volatile(block_id, local)

    def instruction_arithmetic_flags(self, block_id, local):
        self.reads.append(("flags", block_id, local))
        return self.kernel.instruction_arithmetic_flags(block_id, local)


def test_absolute_getters_keep_values_flags_volatility_and_cold_identity():
    func = _module().functions[0]
    kernel = get_indexed_function_kernel(func)
    volatile = flagged = cold = 0
    for block_id, local, instruction_id in _coordinates(kernel):
        assert kernel.instruction_kind_id_by_id(instruction_id) == kernel.instruction_kind_id(block_id, local)
        assert kernel.instruction_record_id_by_id(instruction_id) == kernel.instruction_record_id(block_id, local)
        assert kernel.instruction_arithmetic_flags_by_id(instruction_id) is kernel.instruction_arithmetic_flags(block_id, local)
        assert kernel.instruction_is_volatile_by_id(instruction_id) is kernel.instruction_is_volatile(block_id, local)
        volatile += kernel.instruction_is_volatile_by_id(instruction_id)
        flagged += bool(kernel.instruction_arithmetic_flags_by_id(instruction_id))
        if kernel.instruction_kind_id_by_id(instruction_id) == PARSED_INSTRUCTION_KIND_FREEZE:
            assert kernel.instruction_record_id_by_id(instruction_id) < 0
            assert kernel.instruction_data_by_id(instruction_id, block_id, local) is kernel.instruction_data(block_id, local)
            cold += 1
    assert (volatile, flagged, cold) == (2, 1, 1)


def _capture_loop(func, kernel, blocks=None):
    captured = []

    def capture(_func, _block, owner, block_id, local, absolute, kind, data, volatile):
        decoded = owner.instruction_data_by_id(absolute, block_id, local) if isinstance(data, int) else data
        captured.append((block_id, local, absolute, kind, data, volatile, decoded))
        return []

    lines = emit.emit_function_blocks(
        func,
        block_label=lambda name, block: name + "." + block,
        emit_instruction=lambda *_: pytest.fail("legacy instruction projection"),
        emit_terminator=lambda _func, _block, term: [term.kind],
        blocks=blocks,
        indexed_kernel=kernel,
        emit_indexed_instruction=capture,
    )
    return lines, captured


@pytest.mark.parametrize("reordered", [False, True], ids=["source-order", "block-layout"])
def test_exact_loop_reuses_ids_and_custom_kernel_keeps_block_local_reads(monkeypatch, reordered):
    func = _module().functions[0]
    kernel = get_indexed_function_kernel(func)
    custom = BlockLocalKernel(kernel)
    blocks = [
        kernel.diagnostic_block(block_id, include_instructions=False)
        for block_id in reversed(range(len(kernel.block_names)))
    ] if reordered else None
    expected_lines, expected = _capture_loop(func, custom, blocks)
    assert {item[0] for item in custom.reads} == {"kind", "record", "data", "volatile"}
    if reordered:
        assert len(blocks) > 1
        assert len({item[0] for item in expected}) > 1
        assert expected[0][0] > expected[-1][0]

    def forbidden(*_):
        pytest.fail("exact kernel reread a block-local instruction coordinate")

    for name in ("instruction_kind_id", "instruction_record_id", "instruction_data", "instruction_is_volatile"):
        monkeypatch.setattr(IndexedFunctionKernel, name, forbidden)
    actual_lines, actual = _capture_loop(func, kernel, blocks)
    assert actual_lines == expected_lines
    assert actual == expected
    for got, wanted in zip(actual, expected):
        if not isinstance(got[4], int):
            assert got[4] is wanted[4]


@pytest.mark.parametrize("packed", [False, True], ids=["cold", "packed"])
@pytest.mark.parametrize("failure_at", ["kind", "payload", "absolute-id", "volatile", None])
def test_custom_kernel_retains_argument_and_exception_order(packed, failure_at):
    events = []

    def event(name):
        events.append(name)
        if name == failure_at:
            raise ValueError("custom " + name + " failure")

    class Start:
        def __add__(self, local):
            event("absolute-id")
            return 37 + local

    class Kernel:
        def block_id(self, _name):
            return 0

        def inline_error_edge_span(self, _block_id):
            return SimpleNamespace(first=0, second=0)

        def block_fact(self, _block_id):
            return SimpleNamespace(first=Start(), second=1, third=0, fourth=-1)

        def instruction_kind_id(self, _block_id, _local):
            event("kind")
            return 0 if packed else PARSED_INSTRUCTION_KIND_FREEZE

        def instruction_record_id(self, _block_id, _local):
            event("payload")
            return 77

        def instruction_data(self, _block_id, _local):
            event("payload")
            return ("cold",)

        def instruction_is_volatile(self, _block_id, _local):
            event("volatile")
            return True

    def selected(*args):
        event("emit")
        assert args[5] == 37
        assert args[-1] is True
        return []

    func = SimpleNamespace(name="f", blocks=[SimpleNamespace(
        name="entry", instructions=[], terminator=object(),
    )])

    def run():
        return emit.emit_function_blocks(
            func, block_label=lambda *_: "entry",
            emit_instruction=lambda *_: [], emit_terminator=lambda *_: [],
            indexed_kernel=Kernel(), emit_indexed_instruction=selected,
        )

    order = ["kind", "payload", "absolute-id", "volatile", "emit"]
    if failure_at is not None:
        with pytest.raises(ValueError, match="custom " + failure_at + " failure"):
            run()
        assert events == order[:order.index(failure_at) + 1]
    else:
        run()
        assert events == order


@pytest.mark.parametrize("released_arenas", [False, True])
def test_negative_payload_uses_authoritative_cold_decoder(released_arenas):
    func = _module().functions[0]
    kernel = get_indexed_function_kernel(func)
    block_id, local, absolute = next(
        coords for coords in _coordinates(kernel)
        if kernel.instruction_kind_id_by_id(coords[2]) == PARSED_INSTRUCTION_KIND_FREEZE
    )
    sentinel = (object(), "cold payload")
    kernel.cold_instruction_data = (sentinel,)
    kernel.instruction_metadata.set(absolute * 4 + 1, -1)
    if released_arenas:
        kernel.instruction_arenas = ()
    else:
        kernel.instruction_arenas = [
            CompactParsedInstrArena() for _ in kernel.block_names
        ]
        kernel.instruction_arenas[block_id]._data = _EMPTY_SEQUENCE
    assert kernel.instruction_data_by_id(absolute, block_id, local) is sentinel
    assert kernel.instruction_data(block_id, local) is sentinel
    _, captured = _capture_loop(func, kernel)
    assert next(item for item in captured if item[2] == absolute)[4] is sentinel


@pytest.mark.parametrize("adapter,custom", [
    ("_emit_indexed_instruction_parts", False),
    ("_emit_indexed_instruction_parts", True),
    ("_emit_exact_indexed_instruction_parts", False),
])
def test_adapter_preserves_missing_flag_error_before_dispatch(monkeypatch, adapter, custom):
    func = _module().functions[0]
    kernel = get_indexed_function_kernel(func)
    block_id, local, absolute = next(
        coords for coords in _coordinates(kernel)
        if kernel.instruction_arithmetic_flags(*coords[:2])
    )
    data = kernel.instruction_data(block_id, local)
    kind = kernel.instruction_kind_id(block_id, local)
    del kernel.instruction_arithmetic_flag_values[absolute]
    owner = BlockLocalKernel(kernel) if custom else kernel
    monkeypatch.setattr(x86, "emit_instruction_dispatch_parts", lambda *_args, **_kwargs: pytest.fail("invalid flags reached dispatch"))
    with pytest.raises(KeyError) as failure:
        getattr(x86, adapter)(
            func, SimpleNamespace(name="entry"), owner,
            block_id, local, absolute, kind, data, False,
        )
    assert failure.value.args == (absolute,)
    if custom:
        assert owner.reads == [("flags", block_id, local)]


@pytest.mark.parametrize("triple", ["x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc"])
@pytest.mark.parametrize("streamed", [False, True], ids=["assembly", "object-bytes"])
def test_linux_windows_emission_is_byte_identical_to_block_local_route(monkeypatch, triple, streamed):
    def render():
        module = _module(triple, direct=True)
        timer = ModulePhaseTiming("absolute-id-parity")
        if streamed:
            return emit_x86_64_streamed_module(module, phase_timing=timer).encode_object(timer)
        return emit_indexed_assembly(module, phase_timing=timer).encode("utf-8")

    actual = render()
    old_loop = x86.emit_function_blocks
    custom_owners = []

    def block_local_loop(func, **kwargs):
        owner = BlockLocalKernel(kwargs["indexed_kernel"])
        custom_owners.append(owner)
        kwargs["indexed_kernel"] = owner
        # This oracle deliberately replaces the already-validated exact owner.
        # Use its compatibility adapter as well as its block-local getters.
        kwargs["emit_indexed_instruction"] = x86._emit_indexed_instruction_parts
        return old_loop(func, **kwargs)

    monkeypatch.setattr(x86, "emit_function_blocks", block_local_loop)
    expected = render()
    assert actual == expected
    assert actual
    assert custom_owners
    assert {item[0] for owner in custom_owners for item in owner.reads} == {
        "kind", "record", "data", "volatile", "flags",
    }


@pytest.mark.parametrize("triple", ["x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc"])
@pytest.mark.parametrize("streamed", [False, True], ids=["assembly", "object-bytes"])
def test_exact_function_selects_adapter_without_instruction_type_dispatch(monkeypatch, triple, streamed):
    def compatibility(*_args, **_kwargs):
        pytest.fail("exact function entered per-instruction compatibility dispatch")

    monkeypatch.setattr(x86, "_emit_indexed_instruction_parts", compatibility)
    module = _module(triple, direct=True)
    timer = ModulePhaseTiming("exact-adapter-selection")
    if streamed:
        actual = emit_x86_64_streamed_module(module, phase_timing=timer).encode_object(timer)
    else:
        actual = emit_indexed_assembly(module, phase_timing=timer).encode("utf-8")
    assert actual
    assert all(not func.blocks and not func.block_map for func in module.functions)


@pytest.mark.parametrize("packed", [False, True], ids=["decoded", "packed"])
@pytest.mark.parametrize("failure_at", ["decode", "flags", "intern", None])
def test_exact_adapter_preserves_decode_flag_and_dispatch_order(monkeypatch, packed, failure_at):
    func = _module().functions[0]
    kernel = get_indexed_function_kernel(func)
    block_id, local, absolute = next(_coordinates(kernel))
    events = []
    decoded = ("payload",)

    def event(name):
        events.append(name)
        if name == failure_at:
            raise ValueError(name + " failure")

    def decode(_owner, instruction_id, owner_block, owner_local):
        event("decode")
        assert (instruction_id, owner_block, owner_local) == (absolute, block_id, local)
        return decoded

    def flags(_owner, instruction_id):
        event("flags")
        assert instruction_id == absolute
        return ()

    def intern(data):
        event("intern")
        assert data is decoded
        return data

    def dispatch(_func, _block, _kind, data, **_kwargs):
        event("dispatch")
        assert data is decoded
        return ["selected"]

    monkeypatch.setattr(IndexedFunctionKernel, "instruction_data_by_id", decode)
    monkeypatch.setattr(IndexedFunctionKernel, "instruction_arithmetic_flags_by_id", flags)
    monkeypatch.setattr(x86, "_interned_operands", intern)
    monkeypatch.setattr(x86, "emit_instruction_dispatch_parts", dispatch)
    order = (["decode"] if packed else []) + ["flags", "intern", "dispatch"]

    def run():
        return x86._emit_exact_indexed_instruction_parts(
            func, SimpleNamespace(name="entry"), kernel, block_id, local,
            absolute, 0, 77 if packed else decoded, False,
        )

    if failure_at in order:
        with pytest.raises(ValueError, match=failure_at + " failure"):
            run()
        assert events == order[:order.index(failure_at) + 1]
    else:
        assert run() == ["selected"]
        assert events == order
