"""Prove the two-root witness in the actual indexed emission input.

This is a second representation of the textual witness, not a serializer or
an emitter. Only fresh codec decodes are prepared: neither a live compiler
module nor a caller's decoded graph is changed. Inline error edges are read
at their instruction positions, never flattened into ordinary reachability.
"""

from dataclasses import dataclass
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from pcc.backend.self_backend_ir import (
    PARSED_INSTRUCTION_KIND_ALLOCA as ALLOCA,
    PARSED_INSTRUCTION_KIND_BR as BR,
    PARSED_INSTRUCTION_KIND_BR_COND as BR_COND,
    PARSED_INSTRUCTION_KIND_CALL as CALL,
    PARSED_INSTRUCTION_KIND_CAST as CAST,
    PARSED_INSTRUCTION_KIND_ICMP as ICMP,
    PARSED_INSTRUCTION_KIND_LOAD as LOAD,
    PARSED_INSTRUCTION_KIND_STORE as STORE,
    PARSED_INSTRUCTION_KIND_STORE_ATOMIC as STORE_ATOMIC,
)
from pcc.backend.self_backend_kernel import get_indexed_function_kernel


_HELPER = "py_cleanup_two_roots_preserving_exception"
_ENTER = "pcc_gc_frame_enter_lifo"
_LEAVE = "pcc_gc_frame_leave_lifo"
_LOCAL_SUFFIXES = ("_local", "_local_mapped")
# The receipt loader also enforces its inventory and aggregate limits. Do not
# let a direct helper invocation inherit the codec's much larger file limit.
_MAX_MODULE_BYTES = 2 * 1024 * 1024
_MAX_DUMP_FILES = 128


@dataclass(frozen=True)
class _Instruction:
    block: int
    position: int
    kind: int
    dest: int
    record: int


@dataclass(frozen=True)
class _Call:
    instruction: _Instruction
    symbol: str
    result_type: int
    args: tuple
    indirect: bool
    vararg: bool
    fixed_args: int


class _Graph:
    """Small read-only index over the authoritative scalar arenas."""

    def __init__(self, module, function):
        self.module = module
        self.function = function
        self.kernel = get_indexed_function_kernel(function)
        self.instructions = []
        self.definitions = {}
        self.calls = []
        self.aliases = {}
        kernel = self.kernel
        for block in range(len(kernel.block_names)):
            rows = []
            for position in range(kernel.instruction_count(block)):
                row = _Instruction(
                    block, position, kernel.instruction_kind_id(block, position),
                    kernel.defined_value_id(block, position),
                    kernel.instruction_record_id(block, position),
                )
                rows.append(row)
                if row.dest >= 0:
                    assert row.dest not in self.definitions, "duplicate definition"
                    self.definitions[row.dest] = row
                if row.kind == CALL:
                    assert row.record >= 0, "unsupported cold call"
                    header = kernel.call_header(row.record)
                    span = kernel.call_span(row.record)
                    args = []
                    for offset in range(span.first):
                        arg = kernel.call_arg(header.fourth + offset)
                        ref = arg.second if arg.second >= 0 else -arg.third - 1
                        args.append((arg.first, ref))
                    self.calls.append(_Call(
                        row, kernel.call_texts[header.second], header.first,
                        tuple(args), bool(header.third & 1), bool(header.third & 2),
                        span.second,
                    ))
                elif row.kind == CAST:
                    raw = self.fixed(row)
                    if (kernel.call_texts[raw.first] == "bitcast"
                            and self.is_type(raw.second, "ptr")
                            and self.is_type(raw.fourth, "ptr")):
                        self.aliases[row.dest] = raw.third
            self.instructions.append(rows)
        # Reject cycles even if a later alias would otherwise hide one.
        for value in self.aliases:
            self.original(value)

    def fixed(self, row):
        assert row.record >= 0, "unsupported cold operand record"
        return self.kernel.instruction_record(row.record)

    def is_type(self, type_id, kind, width=None):
        value = self.kernel.type_desc(type_id)
        return value.kind == kind and (width is None or value.width == width)

    def original(self, ref):
        seen = set()
        while ref in self.aliases:
            assert ref not in seen, "cyclic pointer alias"
            seen.add(ref)
            ref = self.aliases[ref]
        return ref

    def literal(self, ref):
        assert ref < 0, "expected a literal operand"
        return self.kernel.call_texts[-ref - 1]

    def local(self, ref):
        ref = self.original(ref)
        assert ref >= 0, "expected a physical local root"
        return ref

    def global_(self, ref):
        name = self.literal(self.original(ref))
        assert name.startswith("@"), "expected a global address"
        matches = [item for item in self.module.globals_ if item.name == name[1:]]
        assert len(matches) == 1, "missing or ambiguous local global"
        return matches[0]

    def signature(self, call, count, result="void"):
        assert not call.indirect and not call.vararg, "non-direct helper ABI"
        assert len(call.args) == call.fixed_args == count, "helper arity"
        assert self.is_type(call.result_type, result), "helper return ABI"
        assert all(self.is_type(ty, "ptr") for ty, _ in call.args), "helper pointer ABI"

    def edges(self, block):
        span = self.kernel.inline_error_edge_span(block)
        return range(span.first, span.first + span.second)

    def no_edges(self, block, start=0, stop=None):
        if stop is None:
            stop = self.kernel.instruction_count(block)
        assert not any(start <= self.kernel.inline_error_edge_trigger(edge) < stop
                       for edge in self.edges(block)), "conditional cleanup diversion"

    def alloca(self, value, kind, width=None):
        row = self.definitions.get(value)
        assert row is not None and row.kind == ALLOCA, "root is not an alloca"
        allocated = self.kernel.alloca_type_id(value)
        assert allocated >= 0 and self.is_type(allocated, kind, width), "alloca ABI"
        return row

    def comparison(self, value, block, start, stop):
        row = self.definitions.get(value)
        assert row is not None and row.kind == ICMP, "error condition is not icmp"
        assert row.block == block and start <= row.position < stop, "stale error condition"
        raw = self.fixed(row)
        assert self.is_type(raw.second, "int", 64), "error condition is not i64"
        assert self.literal(raw.fourth) == "0", "error condition is not against zero"
        return row, self.kernel.call_texts[raw.first], raw.third


def _cleanup_site(graph):
    candidates = []
    for call in graph.calls:
        if call.symbol != _HELPER:
            continue
        graph.signature(call, 2)
        roots = tuple(graph.local(ref) for _, ref in call.args)
        names = tuple(graph.kernel.value_name(root) for root in roots)
        if ".args.operand." not in names[0] or ".args.item.operand." not in names[1]:
            continue
        assert roots[0] != roots[1], "aliased operand roots"
        suffixes = []
        for root in roots:
            graph.alloca(root, "ptr")
            entries = []
            for entry in graph.calls:
                if entry.symbol not in {_ENTER + item for item in _LOCAL_SUFFIXES}:
                    continue
                graph.signature(entry, 2)
                if graph.original(entry.args[1][1]) == root:
                    entries.append(entry)
            assert len(entries) == 1, "root must have exactly one frame enter"
            entry = entries[0]
            suffix = entry.symbol[len(_ENTER):]
            assert suffix in _LOCAL_SUFFIXES, "wrong local frame enter ABI"
            mapping = graph.global_(entry.args[0][1])
            assert (mapping.is_internal and mapping.is_constant and not mapping.tls_model
                    and mapping.type.kind == "int" and mapping.type.width == 32
                    and mapping.ir_prefix == "internal" and not mapping.trailing_attributes
                    and mapping.alignment == 0 and mapping.initializer.strip() == "1"), "not a one-slot frame map"
            suffixes.append(suffix)
        assert suffixes[0] == suffixes[1], "mixed frame ABIs"
        block = call.instruction.block
        # A landing edge first stores its payload into the advertised slot.
        # Cleanup begins at the helper, not at a validated landing load, so
        # accepting a landing here could overwrite either physical root.
        assert graph.kernel.inline_error_landing_slot(block) < 0, (
            "cleanup site cannot be a shared landing"
        )
        block_calls = [item for item in graph.calls if item.instruction.block == block]
        leaves = [item for item in block_calls if item.symbol.startswith(_LEAVE)]
        for leave in leaves:
            graph.signature(leave, 1)
        assert tuple(graph.original(item.args[0][1]) for item in leaves) == roots[::-1], (
            "roots are not retired exactly once in reverse order"
        )
        assert all(item.symbol == _LEAVE + suffixes[0] for item in leaves), "leave ABI"
        assert all(call.instruction.position < item.instruction.position for item in leaves), (
            "cleanup occurs after retirement"
        )
        assert all(item.symbol not in ("py_tls_exc_swap_slot", "pcc_gc_store_root")
                   for item in block_calls), "inline cleanup bypass"
        graph.no_edges(block, stop=leaves[-1].instruction.position + 1)
        candidates.append((call, leaves))
    assert len(candidates) == 1, "expected exactly one two-root cleanup site"
    return candidates[0]


def _pending_error_target(graph, fail):
    """Follow only the fail call's next checks, including inline fallthrough."""
    kernel = graph.kernel
    block = fail.instruction.block
    # An inline edge executes immediately AFTER its trigger. Do not skip an
    # edge attached to the fail call itself when starting at the next row.
    assert all(kernel.inline_error_edge_trigger(edge) != fail.instruction.position
               for edge in graph.edges(block)), "inline edge at fail call bypasses error check"
    start = fail.instruction.position + 1
    visited = set()
    while True:
        state = (block, start)
        assert state not in visited, "error-check loop"
        visited.add(state)
        remaining = [edge for edge in graph.edges(block)
                     if kernel.inline_error_edge_trigger(edge) >= start]
        if remaining:
            edge = remaining[0]
            trigger = kernel.inline_error_edge_trigger(edge)
            condition = kernel.inline_error_edge_condition(edge)
            stop = trigger + 1
            target = kernel.inline_error_edge_target(edge)
        else:
            edge = None
            term = kernel.terminator_header(block)
            assert term.first == BR_COND, "missing pending-error branch"
            condition, target = term.third, term.fourth
            stop = kernel.instruction_count(block)
        comparison, predicate, lhs = graph.comparison(condition, block, start, stop)
        if edge is not None:
            assert comparison.position == trigger, "inline trigger is not its condition"
        flags = [call for call in graph.calls
                 if call.instruction.block == block
                 and start <= call.instruction.position < stop
                 and call.symbol == "py_err_occurred"]
        if flags:
            assert len(flags) == 1, "ambiguous pending-error check"
            flag = flags[0]
            assert (not flag.indirect and not flag.vararg and not flag.args
                    and flag.fixed_args == 0
                    and graph.is_type(flag.result_type, "int", 64)), "error query ABI"
            assert flag.instruction.position < comparison.position, "query after condition"
            assert predicate == "ne" and lhs == flag.instruction.dest, "wrong error condition"
            return target, edge
        assert predicate == "slt" and lhs >= 0, "unsupported preliminary error check"
        if edge is not None:
            start = trigger + 1
        else:
            block = kernel.terminator_span(block).first
            start = 0


def _landing(graph, block, payload, source_line):
    """Bind a shared traceback landing to this edge's actual payload slot."""
    kernel = graph.kernel
    slot = kernel.inline_error_landing_slot(block)
    assert slot >= 0 and 0 <= payload < 2**31, "invalid shared landing payload"
    graph.alloca(slot, "int", 32)
    rows = graph.instructions[block]
    assert rows and rows[0].kind == LOAD, "landing must first load its payload"
    load = rows[0]
    raw = graph.fixed(load)
    assert (graph.is_type(raw.first, "int", 32) and graph.is_type(raw.second, "ptr")
            and graph.original(raw.third) == slot), "wrong landing payload load"
    for row in rows:
        if row.kind in (STORE, STORE_ATOMIC):
            assert graph.original(graph.fixed(row).fourth) != slot, "landing overwrites payload"
    calls = [call for call in graph.calls if call.instruction.block == block
             and call.symbol == "py_exc_append_frame_indexed"]
    assert len(calls) == 1, "unsupported shared landing consumer"
    call = calls[0]
    assert (not call.indirect and not call.vararg and call.fixed_args == 6
            and len(call.args) == 6 and graph.is_type(call.result_type, "void")
            and all(graph.is_type(ty, "ptr") for ty, _ in call.args[:5])
            and graph.is_type(call.args[5][0], "int", 32)
            and call.args[5][1] == load.dest), "landing does not consume its payload"
    lines = graph.global_(call.args[3][1])
    sources = graph.global_(call.args[4][1])
    assert (lines.is_internal and lines.is_constant and not lines.tls_model
            and lines.type.kind == "array" and lines.type.elem.kind == "int"
            and lines.type.elem.width == 32), "invalid traceback line table"
    assert (sources.is_internal and sources.is_constant and not sources.tls_model
            and sources.type.kind == "array" and sources.type.elem.kind == "ptr"
            and sources.type.count == lines.type.count), "invalid traceback source table"
    source_initializer = sources.initializer.strip()
    assert source_initializer.startswith("[") and source_initializer.endswith("]"), (
        "unsupported traceback source initializer"
    )
    source_entries = source_initializer[1:-1].split(",")
    assert len(source_entries) == sources.type.count, "traceback source cardinality"
    for entry in source_entries:
        match = re.fullmatch(r"\s*ptr\s+(null|@([\w.$-]+))\s*", entry)
        assert match is not None, "traceback source entry is not a typed pointer"
        name = match[2]
        if name is not None:
            referenced = [item for item in graph.module.globals_ if item.name == name]
            assert len(referenced) == 1, "missing or ambiguous traceback source global"
            source = referenced[0]
            assert (source.is_internal and source.is_constant and not source.tls_model
                    and source.type.kind == "array" and source.type.elem.kind == "int"
                    and source.type.elem.width == 8), "invalid traceback source global type"
            from pcc.backend.self_backend_parse import decode_llvm_c_string

            data = decode_llvm_c_string(source.initializer.strip())
            assert len(data) == source.type.count and data.endswith(b"\0"), (
                "invalid traceback source global bytes"
            )
    initializer = lines.initializer.strip()
    assert initializer.startswith("[") and initializer.endswith("]"), "unsupported line table"
    entries = initializer[1:-1].split(",")
    assert len(entries) == lines.type.count and payload < len(entries), "landing payload out of range"
    values = []
    for entry in entries:
        match = re.fullmatch(r"\s*i32\s+([0-9]+)\s*", entry)
        assert match is not None, "unsupported traceback line"
        values.append(int(match[1]))
    assert values[payload] == source_line, "landing payload belongs to another error site"
    assert values.count(source_line) == 1, "ambiguous shared landing source line"
    graph.no_edges(block)
    term = kernel.terminator_header(block)
    assert term.first == BR, "conditional shared landing continuation"
    return term.fourth


def _assert_probe(graph):
    forbidden = {"py_list_new", "py_tuple_from_list"}
    assert not any(call.symbol in forbidden for call in graph.calls), "aggregate argument path"
    fail_name = graph.function.name[:-5] + "fail"
    fails = [call for call in graph.calls if call.symbol == fail_name and not call.indirect]
    assert len(fails) == 1, "expected exactly one corresponding fail call"
    cleanup, leaves = _cleanup_site(graph)
    target, edge = _pending_error_target(graph, fails[0])
    kernel = graph.kernel
    if edge is not None:
        payload = kernel.inline_error_edge_payload(edge)
        if kernel.inline_error_landing_slot(target) >= 0:
            assert payload >= 0, "missing shared landing payload"
        else:
            assert payload == -1, "payload on a non-landing edge"
    visited = {fails[0].instruction.block}
    pending_landing = edge is not None
    while target != cleanup.instruction.block:
        assert target not in visited, "cleanup path loop"
        visited.add(target)
        if kernel.inline_error_landing_slot(target) >= 0:
            assert pending_landing, "shared landing without an edge payload"
            target = _landing(graph, target, kernel.inline_error_edge_payload(edge),
                              kernel.inline_error_edge_source_line(edge))
        else:
            graph.no_edges(target)
            term = kernel.terminator_header(target)
            assert term.first == BR, "conditional cleanup diversion"
            target = term.fourth
        pending_landing = False
    # A cleanup block on the direct route may itself store the traceback index
    # before its unconditional jump to the shared landing. Bind that store too.
    term = kernel.terminator_header(target)
    if edge is not None and term.first == BR:
        landing = term.fourth
        slot = kernel.inline_error_landing_slot(landing)
        if slot >= 0:
            stores = [row for row in graph.instructions[target]
                      if row.kind in (STORE, STORE_ATOMIC)
                      and graph.original(graph.fixed(row).fourth) == slot]
            assert len(stores) == 1 and stores[0].kind == STORE, "missing landing payload store"
            store = stores[0]
            raw = graph.fixed(store)
            assert graph.is_type(raw.first, "int", 32), "landing store ABI"
            assert store.position > leaves[-1].instruction.position, "early landing payload store"
            literal = graph.literal(raw.second)
            assert re.fullmatch(r"[0-9]+", literal), "nonliteral landing payload"
            _landing(graph, landing, int(literal), kernel.inline_error_edge_source_line(edge))


def assert_indexed_compiler_two_root_error_edge(raw_modules):
    """Require the same mandatory witness using independently decoded inputs."""
    from pcc.backend.self_backend_indexed_codec import decode_indexed_module_file
    from pcc.backend.self_backend_precise_stackmaps import build_stack_map_plans
    from pcc.backend.self_backend_prepare import prepare_parsed_module_for_target
    from pcc.backend.self_backend_x86_64_linux import _aggregate_returned_indirect

    probes = []
    assert type(raw_modules) in (list, tuple) and raw_modules, "missing indexed modules"
    # Four files per typed module plus the invocation and final link receipts.
    assert 4 * len(raw_modules) + 2 <= _MAX_DUMP_FILES, "indexed module inventory"
    assert all(type(raw) is bytes and 0 < len(raw) <= _MAX_MODULE_BYTES
               for raw in raw_modules), "indexed module size"
    assert sum(map(len, raw_modules)) <= _MAX_MODULE_BYTES, "indexed module aggregate size"
    with TemporaryDirectory(prefix="pcc-two-root-proof-") as temporary:
        for index, raw in enumerate(raw_modules):
            path = Path(temporary) / (str(index) + ".pidx")
            path.write_bytes(raw)
            module = decode_indexed_module_file(str(path), max_bytes=_MAX_MODULE_BYTES)
            # Exactly the text witness's verifier/stackmap policy, operating
            # on a fresh diagnostic copy rather than the emitter's live graph.
            prepared = prepare_parsed_module_for_target(
                module, aggregate_returned_indirect=_aggregate_returned_indirect,
            )
            plans = build_stack_map_plans(
                prepared.functions, prepared.globals_, target="x86_64-linux",
            )
            assert len(plans) == len(prepared.functions), "missing function stackmap plan"
            # Preparation publishes stack/type state and stackmap auxiliary
            # call state. Read predicates from another decode of identical
            # bytes rather than depending on those mutations being harmless.
            module = decode_indexed_module_file(str(path), max_bytes=_MAX_MODULE_BYTES)
            for function in module.functions:
                if function.name.endswith("_probe"):
                    graph = _Graph(module, function)
                    fail_name = function.name[:-5] + "fail"
                    if any(call.symbol == fail_name and not call.indirect for call in graph.calls):
                        probes.append(graph)
        assert len(probes) == 1, "expected exactly one corresponding probe"
        _assert_probe(probes[0])
